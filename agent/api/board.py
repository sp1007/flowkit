"""Tab Storyboard: MỘT trang 4/6 panel = MỘT lượt sinh ảnh = MỘT clip video.

Vì sao là trang chứ không phải từng ảnh rời: sinh từng ảnh thì mỗi lượt là một canh bạc riêng,
nên hai frame liền nhau của cùng scene vẫn ra hai con phố khác hẳn. Vẽ tất cả trong CÙNG một
lượt thì bối cảnh, ánh sáng, trang phục và nét vẽ không thể lệch — chúng là cùng một bức tranh.

Vì sao KHÔNG cắt trang ra: cắt thì dính viền/đường kẻ model tự vẽ, và mỗi ô phải upload ngược
lên Flow mới có media_id. Ở đây cả trang đi thẳng sang tab Shots làm reference DUY NHẤT, còn
badge số tròn vẽ sẵn trong ảnh là thứ chỉ cho model biết panel nào là panel nào — nó thay cho
token `{handle}` mà clip nhiều-ảnh bên tab Illustrators phải dùng.

Illustrators (`shot`) không bị đụng tới: vẫn sinh từng ảnh rời và chỉ để minh hoạ.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.api import studio as S
from agent.config import OMNI_FLASH_MODELS
from agent.studio import brain, db, graph as graph_mod, media_store
from agent.studio.jobs import get_job_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio", tags=["board"])


def _panels_of_project(project: dict) -> tuple[int, int, int]:
    """(n_panels, cols, rows) theo ⚙ Cấu hình dự án."""
    n = int(project.get("sheet_panels") or brain.SHEET_PANELS_DEFAULT)
    if n not in brain.SHEET_PANEL_CHOICES:
        n = brain.SHEET_PANELS_DEFAULT
    cols, rows = brain.sheet_grid(n)
    return n, cols, rows


# ─── Đọc ────────────────────────────────────────────────────

async def _sheet_or_404(sid: str) -> dict:
    row = await db.query_one("SELECT * FROM board_sheet WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "Không tìm thấy trang storyboard")
    return row


async def _panel_or_404(pid: str) -> dict:
    row = await db.query_one("SELECT * FROM board_panel WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Không tìm thấy panel")
    return row


async def _panels_of(sheet_id: str) -> list[dict]:
    return await db.query_all(
        "SELECT * FROM board_panel WHERE sheet_id=? ORDER BY idx", (sheet_id,))


async def _with_panels(sheets: list[dict]) -> list[dict]:
    """Gắn `panels` vào từng sheet bằng MỘT truy vấn, không phải N — tab này gọi lại danh sách
    sau mỗi lượt sinh và một dự án dài có hàng chục trang."""
    if not sheets:
        return []
    ids = [s["id"] for s in sheets]
    qs = ",".join("?" * len(ids))
    rows = await db.query_all(
        f"SELECT * FROM board_panel WHERE sheet_id IN ({qs}) ORDER BY sheet_id, idx", tuple(ids))
    by_sheet: dict[str, list[dict]] = {}
    for r in rows:
        by_sheet.setdefault(r["sheet_id"], []).append(r)
    return [{**s, "panels_list": by_sheet.get(s["id"], [])} for s in sheets]


@router.get("/projects/{pid}/sheets")
async def list_project_sheets(pid: str):
    await S._project_or_404(pid)
    sheets = await db.query_all(
        "SELECT bs.*, sc.idx AS scene_idx, sc.heading AS scene_heading "
        "FROM board_sheet bs JOIN scene sc ON bs.scene_id=sc.id "
        "WHERE bs.project_id=? ORDER BY sc.idx, bs.idx", (pid,))
    return {"sheets": await _with_panels(sheets)}


@router.get("/scenes/{sid}/sheets")
async def list_scene_sheets(sid: str):
    await S._scene_or_404(sid)
    sheets = await db.query_all(
        "SELECT * FROM board_sheet WHERE scene_id=? ORDER BY idx", (sid,))
    return {"sheets": await _with_panels(sheets)}


# ─── Tạo / sửa ──────────────────────────────────────────────

class SheetPatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    motion_prompt: str | None = None


class PanelPatch(BaseModel):
    caption: str | None = None
    shot_size: str | None = None
    lens: str | None = None
    movement: str | None = None
    description: str | None = None
    ref_entity_ids: list[str] | None = None


async def _next_sheet_idx(scene_id: str) -> int:
    row = await db.query_one(
        "SELECT MAX(idx) AS m FROM board_sheet WHERE scene_id=?", (scene_id,))
    return (row["m"] + 1) if row and row["m"] is not None else 0


async def _create_sheet(scene: dict, project: dict, idx: int, title: str,
                        panels: list[dict]) -> dict:
    """Một trang + ĐÚNG `sheet_panels` hàng panel (kể cả khi AI trả thiếu).

    Số panel là cố định theo lưới: thiếu hàng thì trang vẽ ra có ô trống mà app không biết, thừa
    thì panel cuối không bao giờ được vẽ."""
    n, cols, rows = _panels_of_project(project)
    sid = db.new_id()
    ts = db.now()
    await db.insert("board_sheet", {
        "id": sid, "project_id": scene["project_id"], "scene_id": scene["id"], "idx": idx,
        "title": title or f"Trang {idx + 1}", "prompt": "",
        "panels": n, "cols": cols, "rows": rows,
        "status": "pending", "created_at": ts, "updated_at": ts})
    for i in range(n):
        p = panels[i] if i < len(panels) else {}
        await db.insert("board_panel", {
            "id": db.new_id(), "sheet_id": sid, "project_id": scene["project_id"],
            "scene_id": scene["id"], "idx": i,
            "caption": p.get("caption") or "",
            "shot_size": p.get("shot_size") or "",
            "lens": p.get("lens") or "",
            "movement": p.get("movement") or "",
            "description": p.get("description") or "",
            "continuity": p.get("continuity") or "",
            "ref_entity_ids": json.dumps(p.get("ref_entity_ids") or []),
            "created_at": ts, "updated_at": ts})
    return await _sheet_or_404(sid)


@router.post("/scenes/{sid}/sheets")
async def add_sheet(sid: str):
    """Thêm một trang trống vào scene."""
    scene = await S._scene_or_404(sid)
    project = await S._project_or_404(scene["project_id"])
    return await _create_sheet(scene, project, await _next_sheet_idx(sid), "", [])


@router.patch("/sheets/{sid}")
async def patch_sheet(sid: str, body: SheetPatch):
    await _sheet_or_404(sid)
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if upd:
        await db.update("board_sheet", sid, {**upd, "updated_at": db.now()})
    return await _sheet_or_404(sid)


@router.patch("/panels/{pid}")
async def patch_panel(pid: str, body: PanelPatch):
    await _panel_or_404(pid)
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "ref_entity_ids" in upd:
        upd["ref_entity_ids"] = json.dumps(upd["ref_entity_ids"])
    if upd:
        await db.update("board_panel", pid, {**upd, "updated_at": db.now()})
    return await _panel_or_404(pid)


@router.delete("/sheets/{sid}")
async def delete_sheet(sid: str):
    sheet = await _sheet_or_404(sid)
    await db.execute("DELETE FROM board_panel WHERE sheet_id=?", (sid,))
    await db.execute("DELETE FROM board_sheet WHERE id=?", (sid,))
    rest = await db.query_all(
        "SELECT id FROM board_sheet WHERE scene_id=? ORDER BY idx", (sheet["scene_id"],))
    for i, r in enumerate(rest):          # idx phải liền mạch: nhãn trang lấy theo nó
        await db.update("board_sheet", r["id"], {"idx": i})
    return {"ok": True}


@router.post("/scenes/{sid}/sheets/autofill")
async def autofill_scene_sheets(sid: str, n_sheets: int | None = None, replace: bool = True):
    """✨ AI chia scene thành các trang, mỗi trang đúng `sheet_panels` panel."""
    scene = await S._scene_or_404(sid)
    project = await S._project_or_404(scene["project_id"])
    n, _, _ = _panels_of_project(project)
    entities = await db.query_all(
        "SELECT * FROM entity WHERE project_id=?", (scene["project_id"],))
    loc = next((e["name"] for e in entities
                if e["id"] == scene.get("location_entity_id")), None)
    out = await brain.run_json(brain.sheet_autofill_prompt(
        scene.get("heading") or "", scene.get("action") or scene.get("dialog") or "",
        entities, project.get("style") or "", panels=n, n_sheets=n_sheets, location=loc))
    pages = out if isinstance(out, list) else (out or {}).get("pages") or (out or {}).get("sheets")
    if not pages:
        raise HTTPException(502, "AI không chia được scene thành trang storyboard")

    # PHẢI dùng _index_by_name: `_resolve_shot_refs` tra bằng TÊN ĐÃ CHUẨN HOÁ và mong giá trị
    # là cả HÀNG entity (nó đọc e["type"], e["id"]). Từng viết {name: id} ở đây nên mọi tra cứu
    # trả None — không entity nào ngoài location của scene được đính, và trang vẽ ra bịa lại
    # nhân vật lẫn đạo cụ dù prompt gọi đúng tên chúng.
    by_name = S._index_by_name(entities)
    if replace:
        for o in await db.query_all("SELECT id FROM board_sheet WHERE scene_id=?", (sid,)):
            await db.execute("DELETE FROM board_panel WHERE sheet_id=?", (o["id"],))
            await db.execute("DELETE FROM board_sheet WHERE id=?", (o["id"],))
    base = 0 if replace else await _next_sheet_idx(sid)
    made = []
    for k, page in enumerate(pages):
        items = (page or {}).get("panels") or []
        for p in items:
            # Tên entity → id bằng đúng bộ resolve của tab cũ (bắt được cả tên trong {ngoặc}).
            p["ref_entity_ids"] = S._resolve_shot_refs(
                p.get("description") or "", p.get("ref_entity_names"), by_name,
                scene.get("location_entity_id"))
        made.append(await _create_sheet(
            scene, project, base + k, (page or {}).get("title") or "", items))
    return {"sheets": await _with_panels(made)}


# ─── Sinh ảnh trang ─────────────────────────────────────────

async def _sheet_refs_and_cast(sheet: dict, scene: dict,
                               panels: list[dict]) -> tuple[list[dict], list[str], str]:
    """(references, cast, body) cho một trang.

    references gộp entity của MỌI panel — trang là một bức tranh nên nhân vật/đạo cụ xuất hiện ở
    bất kỳ panel nào cũng phải có mặt trong cùng một request."""
    ids: list[str] = []
    for p in panels:
        try:
            ids += json.loads(p.get("ref_entity_ids") or "[]")
        except (json.JSONDecodeError, TypeError):
            pass
    shim = {"ref_entity_ids": json.dumps(list(dict.fromkeys(ids)))}
    refs = await S._build_frame_references(shim, scene)
    # `type` để sheet_page_prompt tách câu SETTING (location) khỏi câu SUBJECTS (nhân vật/đạo
    # cụ) — hai loại cần hai lời dặn khác hẳn nhau.
    if refs:
        rows = await db.query_all(
            "SELECT name, type FROM entity WHERE project_id=?", (scene["project_id"],))
        kind_of = {r["name"]: r["type"] for r in rows}
        refs = [{**r, "type": kind_of.get(r["handle"], "prop")} for r in refs]
    body = sheet.get("prompt") or brain.sheet_page_prompt(panels, refs)
    cast = await S._frame_cast(scene, body)
    return refs, cast, body


async def _generate_sheet(sheet: dict, batch_id: str | None = None) -> dict:
    scene = await S._scene_or_404(sheet["scene_id"])
    project = await S._project_or_404(scene["project_id"])
    client = S._require_extension()
    panels = await _panels_of(sheet["id"])
    refs, cast, body = await _sheet_refs_and_cast(sheet, scene, panels)
    cols = int(sheet.get("cols") or 3)
    rows = int(sheet.get("rows") or 2)
    prompt = brain.compose_prompt(project, body, cast=cast, sheet_page=(cols, rows))
    aspect = S._to_image_aspect(project["aspect_ratio"])
    model = await S._resolve_image_model(project)
    tier = await S._current_tier()
    label = f"sc{scene['idx'] + 1:03d}-page{sheet['idx'] + 1:02d}"

    async def _store(info: dict) -> dict:
        web = await media_store.save_media(
            info.get("media_id"), project["id"], "png", info.get("url"))
        await db.update("board_sheet", sheet["id"], {
            "media_id": info.get("media_id"),
            "primary_media_id": info.get("primary_media_id"),
            "workflow_id": info.get("workflow_id"),
            # KHÔNG ghi `prompt` ở đây. Cột đó là chỗ NGƯỜI DÙNG ghi đè thân prompt; lưu bản tự
            # sinh vào đấy thì mọi lượt vẽ sau tái dùng thân cũ và trang không bao giờ nhận được
            # thay đổi ở panel hay ở cách dựng prompt. Muốn xem thân đang gửi thì có
            # /sheets/{id}/prompt-preview, nó tính lại từ panel.
            "path": web, "status": "done", "updated_at": db.now()})
        if info.get("workflow_id") and project.get("flow_project_id"):
            try:
                await client.change_display_name(
                    info["workflow_id"], project["flow_project_id"], label[:60])
            except Exception:  # noqa: BLE001
                pass
        await S._record_media_history(project["id"], "sheet", sheet["id"], "image",
                                      info.get("media_id"), info.get("primary_media_id"), web)
        # `image_path` là thứ _generate_image_verified kiểm để biết ảnh đã về máy hay chưa.
        return {**await _sheet_or_404(sheet["id"]), "image_path": web}

    await db.update("board_sheet", sheet["id"], {"status": "running", "updated_at": db.now()})
    try:
        await S._generate_image_verified(
            gen_call=lambda: client.generate_images(
                prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
                user_paygate_tier=tier, references=refs or None, image_model=model,
                seed=project.get("seed"), batch_id=batch_id, serialize=batch_id is None,
                # BẮT BUỘC ở đây: một trang gọi tên cùng một entity ở MỌI panel, mà mỗi lần gọi
                # lại sinh thêm một reference part trỏ cùng mediaId trong khi imageInputs chỉ có
                # một mục → Flow trả 400 INVALID_ARGUMENT. Đã đo: 6 part/1 ảnh hỏng, bind một
                # lần thì chạy ở đúng độ dài prompt ấy.
                dedupe_refs=True),
            store_call=_store, label_for_err=f"trang {label}")
    except HTTPException:
        await db.update("board_sheet", sheet["id"], {"status": "error", "updated_at": db.now()})
        raise
    return {**await _sheet_or_404(sheet["id"]), "panels_list": panels}


@router.post("/sheets/{sid}/generate")
async def generate_sheet(sid: str):
    return await _generate_sheet(await _sheet_or_404(sid))


@router.get("/sheets/{sid}/prompt-preview")
async def preview_sheet_prompt(sid: str):
    """Prompt Y HỆT lúc gửi đi — để đối chiếu với bản gõ tay trên Flow mà không tốn credit."""
    sheet = await _sheet_or_404(sid)
    scene = await S._scene_or_404(sheet["scene_id"])
    project = await S._project_or_404(scene["project_id"])
    panels = await _panels_of(sheet["id"])
    refs, cast, body = await _sheet_refs_and_cast(sheet, scene, panels)
    prompt = brain.compose_prompt(project, body, cast=cast,
                                  sheet_page=(int(sheet.get("cols") or 3),
                                              int(sheet.get("rows") or 2)))
    return {"prompt": prompt, "references": [r["handle"] for r in refs], "cast": cast}


@router.post("/projects/{pid}/sheets/generate-all")
async def generate_all_sheets(pid: str, force: bool = False):
    """✦ Sinh mọi trang chưa có ảnh. Không cần chia pha như tab Illustrators: một trang đã tự
    nhất quán trong chính nó nên không trang nào phải chờ trang nào."""
    await S._project_or_404(pid)
    sheets = await db.query_all(
        "SELECT bs.* FROM board_sheet bs JOIN scene sc ON bs.scene_id=sc.id "
        "WHERE bs.project_id=? ORDER BY sc.idx, bs.idx", (pid,))
    todo = [s for s in sheets if force or not s.get("path")]

    async def _worker(s, batch_id):
        await _generate_sheet(s, batch_id=batch_id)

    job = get_job_manager().start(
        project_id=pid, type_="storyboard", items=todo, worker=_worker,
        label=f"Sinh trang storyboard ({len(todo)})",
        throttle=S.IMAGE_BATCH_COOLDOWN, batch_size=S.IMAGE_BATCH_SIZE,
        stagger=S.IMAGE_BATCH_STAGGER,
        item_label=lambda s: s.get("title") or s["id"])
    return {"job_id": job.id, "total": len(todo)}


# ─── Node Editor của trang ──────────────────────────────────

class SaveGraphRequest(BaseModel):
    graph: dict


@router.get("/sheets/{sid}/graph")
async def get_sheet_graph(sid: str):
    row = await _sheet_or_404(sid)
    return {"graph": json.loads(row["graph_json"]) if row.get("graph_json") else None}


@router.put("/sheets/{sid}/graph")
async def put_sheet_graph(sid: str, body: SaveGraphRequest):
    await _sheet_or_404(sid)
    await db.update("board_sheet", sid, {"graph_json": json.dumps(body.graph),
                                         "updated_at": db.now()})
    return {"ok": True}


@router.post("/sheets/{sid}/graph/run")
async def run_sheet_graph(sid: str, body: SaveGraphRequest, only_node: str | None = None,
                          propagate: bool = False):
    sheet = await _sheet_or_404(sid)
    scene = await S._scene_or_404(sheet["scene_id"])
    project = await S._project_or_404(scene["project_id"])
    await db.update("board_sheet", sid, {"graph_json": json.dumps(body.graph)})
    try:
        out = await graph_mod.run_graph(body.graph, sheet, project, "sheet",
                                        only_node=only_node, propagate=propagate)
    except graph_mod.GraphError as e:
        raise HTTPException(400, str(e)) from e
    return {**out, "sheet": {**await _sheet_or_404(sid),
                             "panels_list": await _panels_of(sid)}}


@router.post("/sheets/{sid}/apply-media")
async def apply_sheet_media(sid: str, body: dict):
    """Gán một media (kết quả quick-gen trong node editor) làm ảnh của trang."""
    sheet = await _sheet_or_404(sid)
    media_id = (body or {}).get("media_id")
    if not media_id:
        raise HTTPException(400, "Thiếu media_id")
    web = await media_store.ensure_local(media_id, sheet["project_id"],
                                         (body or {}).get("ext") or "png")
    await db.update("board_sheet", sid, {
        "media_id": media_id, "primary_media_id": media_id, "path": web,
        "status": "done", "updated_at": db.now()})
    await S._record_media_history(sheet["project_id"], "sheet", sid, "image",
                                  media_id, media_id, web)
    return {"ok": True, "path": web, "sheet": await _sheet_or_404(sid)}


# ─── Video: một trang = một clip ────────────────────────────

async def _sheet_timeline(sheet: dict, panels: list[dict], scene: dict,
                          project: dict, clip_s: int) -> str:
    out = await brain.run_json(brain.sheet_timeline_prompt(
        panels, clip_s, scene.get("heading") or "", project.get("style") or ""))
    motion = (out or {}).get("motion_prompt") if isinstance(out, dict) else None
    if not motion:
        raise HTTPException(502, "AI không viết được prompt timeline cho trang")
    await db.update("board_sheet", sheet["id"], {"motion_prompt": motion,
                                                 "updated_at": db.now()})
    return motion


@router.post("/sheets/{sid}/prompt")
async def gen_sheet_prompt(sid: str):
    """✨ Viết prompt timeline đi xuyên các panel của trang (gọi 'panel 1..N', KHÔNG dùng
    `{token}` — cả trang chỉ là MỘT reference nên không có gì để bind)."""
    sheet = await _sheet_or_404(sid)
    scene = await S._scene_or_404(sheet["scene_id"])
    project = await S._project_or_404(scene["project_id"])
    if not sheet.get("media_id"):
        raise HTTPException(400, "Trang chưa có ảnh — sinh trang trước")
    _, clip_max = S._video_engine(project)
    await _sheet_timeline(sheet, await _panels_of(sid), scene, project, clip_max)
    return await _sheet_or_404(sid)


async def _generate_sheet_video(sheet: dict) -> dict:
    """Render MỘT clip từ trang: cả trang là reference DUY NHẤT, badge số trong ảnh chỉ panel."""
    scene = await S._scene_or_404(sheet["scene_id"])
    project = await S._project_or_404(scene["project_id"])
    client = S._require_extension()
    panels = await _panels_of(sheet["id"])
    if not sheet.get("media_id"):
        raise HTTPException(400, "Trang chưa có ảnh — sinh ở tab Storyboard trước")
    engine, clip_max = S._video_engine(project)
    if engine != "omni":
        raise HTTPException(
            400, "Trang storyboard chỉ render được bằng Omni Flash (Veo i2v coi ảnh start là "
                 "khung hình đầu tiên, nên nó sẽ dựng chính cái lưới thành video). Đổi model "
                 "video ở ⚙ Cấu hình dự án.")

    motion = sheet.get("motion_prompt") or ""
    if not motion.strip():
        motion = await _sheet_timeline(sheet, panels, scene, project, clip_max)

    # Reference DUY NHẤT là cả trang. Không thêm entity ref: prompt timeline cố ý không dùng
    # `{token}` nào (xem brain.sheet_timeline_prompt), nên ảnh entity gửi kèm sẽ không được gọi
    # tên — vô dụng, mà lại chiếm chỗ và dễ khiến model chép sheet nhân vật vào khung.
    refs = [{"handle": f"storyboard-page-{sheet['idx'] + 1}", "media_id": sheet["media_id"]}]
    duration = clip_max
    await db.update("board_sheet", sheet["id"], {"status": "running", "updated_at": db.now()})
    name = f"sc{scene['idx'] + 1:03d}-page{sheet['idx'] + 1:02d}-clip"
    tier = await S._current_tier()
    try:
        info = await S._render_clip(
            client, project, sheet["id"],
            lambda: client.generate_video_omni(
                prompt=motion, project_id=project["flow_project_id"],
                reference_media_ids=[sheet["media_id"]],
                duration_s=duration, aspect_ratio=project["aspect_ratio"],
                user_paygate_tier=tier, references=refs),
            name, table="board_sheet")
    except HTTPException:
        await db.update("board_sheet", sheet["id"], {"status": "error", "updated_at": db.now()})
        raise
    await db.update("board_sheet", sheet["id"], {
        "video_media_id": info["media_id"], "video_primary_id": info.get("primary_media_id"),
        "video_workflow_id": info.get("workflow_id"), "video_path": info["web"],
        "video_model": OMNI_FLASH_MODELS.get(str(duration)), "duration": duration,
        "status": "done", "updated_at": db.now()})
    await S._record_media_history(project["id"], "sheet", sheet["id"], "video",
                                  info.get("media_id"), info.get("primary_media_id"), info["web"])
    return await _sheet_or_404(sheet["id"])


@router.post("/sheets/{sid}/video")
async def generate_sheet_video(sid: str):
    return await _generate_sheet_video(await _sheet_or_404(sid))


@router.post("/projects/{pid}/sheets/video/generate-all")
async def generate_all_sheet_videos(pid: str, force: bool = False):
    await S._project_or_404(pid)
    sheets = await db.query_all(
        "SELECT bs.* FROM board_sheet bs JOIN scene sc ON bs.scene_id=sc.id "
        "WHERE bs.project_id=? AND bs.media_id IS NOT NULL ORDER BY sc.idx, bs.idx", (pid,))
    todo = [s for s in sheets if force or not s.get("video_path")]

    async def _worker(s):
        await _generate_sheet_video(s)

    job = get_job_manager().start(
        project_id=pid, type_="videos", items=todo, worker=_worker,
        label=f"Sinh video ({len(todo)} trang)", throttle=(15, 30),
        item_label=lambda s: s.get("title") or s["id"])
    return {"job_id": job.id, "total": len(todo)}
