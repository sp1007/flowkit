"""Flow Studio API — stateful orchestration over the Flow proxy (video-app.md).

Phase 0: project CRUD (DB + Flow), Flow project import with thumbnails, options,
settings, health. Heavier pipeline endpoints land in later phases.
"""
import asyncio
import json
import logging
import random
import shutil
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.config import (
    IMAGE_MODELS, VIDEO_MODELS, UPSCALE_MODELS, OMNI_FLASH_MODELS,
)
from agent.services.flow_client import get_flow_client
from agent.studio import db, media_store, brain, assembler, davinci_xml, graph as graph_mod

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio", tags=["studio"])

# Google đôi khi chặn ảnh theo policy (không trả media) hoặc trả filtered → thử lại.
IMAGE_GEN_RETRIES = 3
# Video tốn thời gian (15–30s/lần) nên thử lại ít hơn.
VIDEO_GEN_RETRIES = 2


# ─── Models ──────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    title: str
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    style: str = "Realistic"
    storytelling: bool = False
    import_flow_project_id: Optional[str] = None   # gắn vào project Flow có sẵn
    import_thumb_media_key: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    style: Optional[str] = None
    aspect_ratio: Optional[str] = None
    paygate_tier: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    voice_id: Optional[int] = None
    agent: Optional[str] = None
    idea: Optional[str] = None
    target_duration: Optional[int] = None
    shot_duration: Optional[int] = None
    storytelling: Optional[bool] = None
    prompt_header: Optional[str] = None
    prompt_footer: Optional[str] = None
    culture_hint: Optional[str] = None


class GenerateScriptRequest(BaseModel):
    idea: str
    target_duration: Optional[int] = None   # giây


class SaveScriptRequest(BaseModel):
    script: str


class ScriptChatRequest(BaseModel):
    instruction: str


class AddEntityRequest(BaseModel):
    type: str = "character"        # character | location | prop
    name: str
    description: str = ""
    ref_prompt: str = ""


class UpdateEntityRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    ref_prompt: Optional[str] = None
    type: Optional[str] = None


class SetMediaRequest(BaseModel):
    media_id: str


class ImportEntityRequest(BaseModel):
    source_entity_id: str


class LinkEntityRequest(BaseModel):
    source_entity_id: str


class ImportMediaRequest(BaseModel):
    media_id: str
    name: str = "Flow asset"
    type: str = "character"
    description: str = ""


# ─── Helpers ─────────────────────────────────────────────────

def _deep_find(obj, key: str):
    """First value for `key` anywhere in a nested dict/list (tRPC envelopes)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _deep_find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_find(v, key)
            if found is not None:
                return found
    return None


def _flow_projects(raw: dict) -> list[dict]:
    """Pull the projects array out of the tRPC envelope."""
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    projects = _deep_find(data, "projects")
    out = []
    for p in projects or []:
        info = p.get("projectInfo", {})
        out.append({
            "flow_project_id": p.get("projectId"),
            "title": info.get("projectTitle"),
            "thumb_media_key": info.get("thumbnailMediaKey"),
            "creation_time": p.get("creationTime"),
        })
    return out


def _require_extension():
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension chưa kết nối (mở Google Flow trong Chrome)")
    return client


_tier_cache = {"value": None, "ts": 0.0}


async def _current_tier() -> str:
    """User's paygate tier from /api/flow/credits (không do người dùng chọn). Cache 60s."""
    import time as _t
    if _tier_cache["value"] and _t.monotonic() - _tier_cache["ts"] < 60:
        return _tier_cache["value"]
    client = get_flow_client()
    if client.connected:
        try:
            res = await client.get_credits()
            data = res.get("data", res)
            tier = data.get("userPaygateTier") if isinstance(data, dict) else None
            if tier:
                _tier_cache.update(value=tier, ts=_t.monotonic())
                return tier
        except Exception:
            pass
    return _tier_cache["value"] or "PAYGATE_TIER_ONE"


# ─── Health / options / settings ────────────────────────────

@router.get("/health")
async def health():
    client = get_flow_client()
    omni = await _safe_omni_health()
    return {
        "status": "ok",
        "extension_connected": client.connected,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "tts": omni,
    }


async def _safe_omni_health() -> bool:
    try:
        from agent.api.tts import _state
        return bool(_state.get("base_url"))
    except Exception:
        return False


@router.get("/options")
async def options():
    """Lựa chọn cho Settings: models, styles, aspect, tiers, voices, agents."""
    voices, agents = [], []
    try:
        from agent.api.tts import _proxy
        voices = await _proxy("GET", "/api/voices/list", timeout=10.0)
    except Exception:
        voices = []
    try:
        from agent.api.ai_agent import list_agents
        agents = (await list_agents())["agents"]
    except Exception:
        agents = []
    return {
        "image_models": list(IMAGE_MODELS.keys()),
        "video_models": {"veo_tiers": list(VIDEO_MODELS.keys()),
                          "omni_flash_durations": list(OMNI_FLASH_MODELS.keys())},
        "upscale_models": list(UPSCALE_MODELS.keys()),
        "aspect_ratios": ["VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"],
        "paygate_tiers": ["PAYGATE_TIER_ONE", "PAYGATE_TIER_TWO"],
        "style_presets": ["Realistic", "Cinematic", "Anime", "3D Pixar", "Watercolor", "Noir"],
        "voices": voices,
        "agents": agents,
    }


@router.get("/settings")
async def get_settings():
    return await db.kv_get_all()


@router.put("/settings")
async def put_settings(body: dict):
    for k, v in body.items():
        await db.kv_set(k, v)
    return await db.kv_get_all()


@router.get("/credits")
async def credits():
    client = _require_extension()
    result = await client.get_credits()
    return result.get("data", result)


# ─── Flow projects (live, for import) ───────────────────────

@router.get("/flow-projects")
async def flow_projects():
    """Project trên Google Flow (có thumbnail) để import."""
    client = _require_extension()
    raw = await client.get_projects()
    return {"projects": _flow_projects(raw)}


def _flow_media_items(raw: dict) -> list[dict]:
    """Pull named media out of a getProjectContents envelope.

    Real schema (data.result.data.json.result):
      - `workflows[]`: each generation, with metadata.displayName (the asset name we
        set) + metadata.primaryMediaId (the image/video to reference).
      - `media[]`: raw media items (name = media id, has `image`/`video`) — used to
        tell whether a workflow's primary media is an image or a video.
      - `externalReferenceMedia[]`: uploaded reference media (mediaId, mediaType,
        workflowDisplayName) — we keep the IMAGE ones (skip AUDIO voice presets).
    """
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    workflows = _deep_find(data, "workflows") or []
    media_list = _deep_find(data, "media") or []
    ext = _deep_find(data, "externalReferenceMedia") or []

    by_name: dict[str, dict] = {}
    for m in media_list:
        if isinstance(m, dict) and m.get("name"):
            by_name[m["name"]] = m

    def kind_of(mid: str) -> str:
        m = by_name.get(mid) or {}
        return "video" if "video" in m else "image"

    out: list[dict] = []
    seen: set[str] = set()

    for w in workflows:
        if not isinstance(w, dict):
            continue
        meta = w.get("metadata") or {}
        mid = meta.get("primaryMediaId")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"media_id": mid, "name": str(meta.get("displayName") or "")[:80],
                    "kind": kind_of(mid)})

    for e in ext:
        if not isinstance(e, dict) or str(e.get("mediaType") or "").upper() != "IMAGE":
            continue
        mid = e.get("mediaId")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"media_id": mid, "name": str(e.get("workflowDisplayName") or "")[:80],
                    "kind": "image"})

    return out


@router.get("/flow-projects/{flow_id}/media")
async def flow_project_media(flow_id: str, images_only: bool = True):
    """Media (ảnh) bên trong một project Flow — để tham chiếu/đồng bộ làm asset."""
    client = _require_extension()
    raw = await client.get_project(flow_id)
    items = _flow_media_items(raw)
    if images_only:
        items = [m for m in items if m["kind"] == "image"]
    return {"media": items}


@router.get("/library/all-media")
async def all_flow_media(images_only: bool = True):
    """Tất cả ảnh trong MỌI project Flow (gắn kèm tên project) — gallery 'All image'."""
    client = _require_extension()
    projects = _flow_projects(await client.get_projects())
    out = []
    for p in projects:
        fid = p.get("flow_project_id")
        if not fid:
            continue
        try:
            items = _flow_media_items(await client.get_project(fid))
        except Exception as e:
            logger.warning("all-media: project %s lỗi: %s", fid, e)
            continue
        for m in items:
            if images_only and m["kind"] != "image":
                continue
            out.append({**m, "project_title": p.get("title") or "", "flow_project_id": fid})
    return {"media": out, "projects": len(projects)}


# ─── Studio projects (DB) ───────────────────────────────────

@router.get("/projects")
async def list_projects():
    rows = await db.query_all("SELECT * FROM project ORDER BY updated_at DESC")
    return {"projects": rows}


@router.post("/projects")
async def create_project(body: CreateProjectRequest):
    client = _require_extension()

    flow_id = body.import_flow_project_id
    thumb = body.import_thumb_media_key
    if not flow_id:
        # Tạo project mới trên Flow
        result = await client.create_project(body.title)
        data = result.get("data", result)
        flow_id = _deep_find(data, "projectId")
        if not flow_id:
            raise HTTPException(502, "Không tạo được project trên Flow")

    pid = db.new_id()
    ts = db.now()
    await db.insert("project", {
        "id": pid, "title": body.title, "flow_project_id": flow_id,
        "style": body.style, "aspect_ratio": body.aspect_ratio,
        "paygate_tier": await _current_tier(),   # từ /api/flow/credits, không do user chọn
        "storytelling": 1 if body.storytelling else 0,
        "thumb_media_key": thumb,
        "status": "draft", "created_at": ts, "updated_at": ts,
    })
    return await db.query_one("SELECT * FROM project WHERE id=?", (pid,))


@router.get("/projects/{pid}")
async def get_project(pid: str):
    row = await db.query_one("SELECT * FROM project WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Project không tồn tại")
    return row


@router.patch("/projects/{pid}")
async def update_project(pid: str, body: UpdateProjectRequest):
    row = await db.query_one("SELECT * FROM project WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Project không tồn tại")
    data = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "storytelling" in data:
        data["storytelling"] = 1 if data["storytelling"] else 0
    data["updated_at"] = db.now()
    await db.update("project", pid, data)
    return await db.query_one("SELECT * FROM project WHERE id=?", (pid,))


@router.put("/projects/{pid}/cover")
async def set_project_cover(pid: str, body: SetMediaRequest):
    """Đặt ảnh đại diện project. Cập nhật thumb của studio (luôn) + thử set trên Flow (best-effort)."""
    p = await _project_or_404(pid)
    flow_ok = False
    try:
        res = await get_flow_client().change_project_cover(p["flow_project_id"], body.media_id)
        flow_ok = not (isinstance(res, dict) and res.get("error"))
    except Exception as e:
        logger.warning("set cover (flow) failed: %s", e)
    await db.update("project", pid, {"thumb_media_key": body.media_id, "updated_at": db.now()})
    return {"project": await db.query_one("SELECT * FROM project WHERE id=?", (pid,)),
            "flow_updated": flow_ok}


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    row = await db.query_one("SELECT * FROM project WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Project không tồn tại")
    await db.delete("project", pid)
    # dọn media local của project
    folder = media_store.MEDIA_DIR / pid
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


# ─── Script + scenes ────────────────────────────────────────

async def _project_or_404(pid: str) -> dict:
    row = await db.query_one("SELECT * FROM project WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "Project không tồn tại")
    return row


async def _save_scenes(pid: str, script: str) -> list[dict]:
    """Re-parse script → replace project's scenes in DB. Returns scene rows."""
    await db.execute("DELETE FROM scene WHERE project_id=?", (pid,))
    parsed = brain.parse_scenes(script)
    ts = db.now()
    for s in parsed:
        await db.insert("scene", {
            "id": db.new_id(), "project_id": pid, "idx": s["idx"],
            "heading": s["heading"], "slug": s["slug"],
            "action": s["body"].strip(), "dialog": None,
            "location_entity_id": None, "source_segment": None,
            "source_start": None, "source_end": None, "created_at": ts,
        })
    return await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))


@router.get("/projects/{pid}/scenes")
async def list_scenes(pid: str):
    await _project_or_404(pid)
    return {"scenes": await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))}


@router.post("/projects/{pid}/script/generate")
async def generate_script(pid: str, body: GenerateScriptRequest):
    p = await _project_or_404(pid)
    result = await brain.run_json(brain.script_from_idea_prompt(
        body.idea, body.target_duration, bool(p["storytelling"]),
        p["style"], p["shot_duration"] or 8))
    script = result.get("script", "")
    if not script:
        raise HTTPException(502, "AI không trả về script")
    fields = {"idea": body.idea, "target_duration": body.target_duration,
              "script_raw": script, "updated_at": db.now()}
    # culture_hint is auto-detected from the content; don't clobber a user override.
    ch = (result.get("culture_hint") or "").strip()
    if ch and not (p.get("culture_hint") or "").strip():
        fields["culture_hint"] = ch
    await db.update("project", pid, fields)
    scenes = await _save_scenes(pid, script)
    return {"script": script, "scenes": scenes,
            "estimated_duration": result.get("estimated_duration"),
            "culture_hint": fields.get("culture_hint") or p.get("culture_hint")}


@router.put("/projects/{pid}/script")
async def save_script(pid: str, body: SaveScriptRequest):
    await _project_or_404(pid)
    await db.update("project", pid, {"script_raw": body.script, "updated_at": db.now()})
    scenes = await _save_scenes(pid, body.script)
    return {"script": body.script, "scenes": scenes}


@router.post("/projects/{pid}/script/chat")
async def script_chat(pid: str, body: ScriptChatRequest):
    p = await _project_or_404(pid)
    result = await brain.run_json(brain.edit_script_prompt(
        p["script_raw"] or "", body.instruction, p["style"]))
    script = result.get("script", "")
    if not script:
        raise HTTPException(502, "AI không trả về script")
    await db.update("project", pid, {"script_raw": script, "updated_at": db.now()})
    scenes = await _save_scenes(pid, script)
    return {"script": script, "scenes": scenes}


# ─── Assets (entities) ──────────────────────────────────────

def _to_image_aspect(video_aspect: str) -> str:
    return (video_aspect or "").replace("VIDEO_ASPECT_RATIO_", "IMAGE_ASPECT_RATIO_") \
        or "IMAGE_ASPECT_RATIO_LANDSCAPE"


async def _resolve_image_model(project: dict) -> Optional[str]:
    name = project.get("image_model") or (await db.kv_get_all()).get("image_model")
    if not name:
        return None  # flow_client default (NANO_BANANA_PRO)
    return IMAGE_MODELS.get(name, name)  # name → key, or already a key


def _extract_image_result(payload: dict) -> dict:
    media = (payload.get("media") or [{}])[0]
    gen = media.get("image", {}).get("generatedImage", {})
    wf = (payload.get("workflows") or [{}])[0]
    return {
        "media_id": gen.get("mediaId") or media.get("name"),
        "workflow_id": wf.get("name"),
        "primary_media_id": wf.get("metadata", {}).get("primaryMediaId"),
    }


def _image_block_reason(payload: dict) -> Optional[str]:
    """Detect a content-policy / RAI filter in an image response (no media produced)."""
    for key in ("raiFilteredReason", "filteredReason", "raiFilterReason", "blockReason"):
        v = _deep_find(payload, key)
        if v:
            return str(v)
    return None


async def _generate_image_verified(gen_call, store_call, label_for_err: str) -> dict:
    """Run an image generation, VERIFY a media was actually produced + downloaded, and
    retry on Google content-policy blocks / transient failures (video-app spec).

    `gen_call()` → raw Flow response; `store_call(info)` → persisted row (with image_path).
    Raises HTTPException(502) only after all retries fail.
    """
    last = ""
    for attempt in range(IMAGE_GEN_RETRIES):
        res = await gen_call()
        if res.get("error"):
            last = str(res["error"])
        else:
            payload = res.get("data", res)
            info = _extract_image_result(payload)
            if info.get("media_id"):
                row = await store_call(info)
                if row.get("image_path"):       # ảnh tạo + tải về OK
                    return row
                last = "ảnh chưa tải được"
            else:
                last = _image_block_reason(payload) or "Flow không trả media (có thể bị chặn)"
        logger.warning("%s: tạo ảnh hỏng (lần %d/%d): %s",
                       label_for_err, attempt + 1, IMAGE_GEN_RETRIES, last)
        if attempt < IMAGE_GEN_RETRIES - 1:
            await asyncio.sleep(random.uniform(2, 5))
    raise HTTPException(502, f"Tạo ảnh thất bại sau {IMAGE_GEN_RETRIES} lần ({label_for_err}): {last}")


async def _entity_or_404(eid: str) -> dict:
    row = await db.query_one("SELECT * FROM entity WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "Entity không tồn tại")
    return row


async def _maybe_set_cover(project_id: str, flow_project_id: str, media_id: str):
    """Set the Flow project cover (thumbnail) from the first generated image."""
    if not (media_id and flow_project_id):
        return
    row = await db.query_one("SELECT thumb_media_key FROM project WHERE id=?", (project_id,))
    if row and row.get("thumb_media_key"):
        return
    try:
        await get_flow_client().change_project_cover(flow_project_id, media_id)
    except Exception as e:
        logger.warning("set project cover failed: %s", e)
    await db.update("project", project_id, {"thumb_media_key": media_id})


async def _store_media_on_entity(entity: dict, project: dict, info: dict, label: str):
    """Rename on Flow + download local + persist media fields onto the entity."""
    client = get_flow_client()
    if info.get("workflow_id") and project.get("flow_project_id"):
        try:
            await client.change_display_name(
                info["workflow_id"], project["flow_project_id"], label[:60])
        except Exception:
            pass
    web = None
    if info.get("media_id"):
        web = await media_store.ensure_local(info["media_id"], project["id"])
    await db.update("entity", entity["id"], {
        "media_id": info.get("media_id"),
        "primary_media_id": info.get("primary_media_id"),
        "workflow_id": info.get("workflow_id"),
        "image_path": web, "updated_at": db.now(),
    })
    await _maybe_set_cover(project["id"], project.get("flow_project_id"), info.get("media_id"))
    return await _entity_or_404(entity["id"])


async def _generate_entity_image(entity: dict, project: dict) -> dict:
    client = _require_extension()
    body = brain.ref_image_prompt(
        entity["type"], entity["name"],
        entity.get("description") or entity.get("ref_prompt") or "")
    prompt = brain.compose_prompt(project, body)
    aspect = ("IMAGE_ASPECT_RATIO_LANDSCAPE" if entity["type"] in ("character", "prop", "location")
              else _to_image_aspect(project["aspect_ratio"]))
    model = await _resolve_image_model(project)
    tier = await _current_tier()
    return await _generate_image_verified(
        gen_call=lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, image_model=model),
        store_call=lambda info: _store_media_on_entity(
            entity, project, info, f"{entity['type']}_{entity['name']}"),
        label_for_err=f"asset {entity['name']}")


@router.get("/projects/{pid}/entities")
async def list_entities(pid: str):
    await _project_or_404(pid)
    return {"entities": await db.query_all(
        "SELECT * FROM entity WHERE project_id=? ORDER BY type, created_at", (pid,))}


@router.get("/library/entities")
async def library_entities(exclude_project: Optional[str] = None):
    """Mọi asset (đã có ảnh) trên TẤT CẢ dự án — để dùng chung asset giữa các project.

    Một dự án có thể đóng vai 'thư viện' chứa nhân vật/bối cảnh/đạo cụ; dự án khác chỉ
    việc import lại entity có sẵn (không phải gen lại).
    """
    rows = await db.query_all(
        "SELECT e.*, p.title AS project_title FROM entity e "
        "JOIN project p ON e.project_id = p.id "
        "WHERE e.media_id IS NOT NULL "
        + ("AND e.project_id != ? " if exclude_project else "")
        + "ORDER BY p.title, e.type, e.name",
        (exclude_project,) if exclude_project else ())
    return {"entities": rows}


@router.post("/projects/{pid}/entities/import")
async def import_entity(pid: str, body: ImportEntityRequest):
    """Sao chép một entity từ dự án khác vào dự án này, GIỮ ảnh sẵn có (không gen lại)."""
    await _project_or_404(pid)
    src = await db.query_one("SELECT * FROM entity WHERE id=?", (body.source_entity_id,))
    if not src:
        raise HTTPException(404, "Entity nguồn không tồn tại")
    # tải ảnh về thư mục project hiện tại (an toàn nếu dự án nguồn bị xoá); fallback path cũ
    web = None
    if src.get("media_id"):
        try:
            web = await media_store.ensure_local(src["media_id"], pid)
        except Exception:
            web = None
    web = web or src.get("image_path")
    eid = db.new_id()
    ts = db.now()
    await db.insert("entity", {
        "id": eid, "project_id": pid, "type": src.get("type", "character"),
        "name": src.get("name", ""), "description": src.get("description", ""),
        "ref_prompt": src.get("ref_prompt", ""),
        "media_id": src.get("media_id"), "primary_media_id": src.get("primary_media_id"),
        "workflow_id": src.get("workflow_id"), "image_path": web,
        "created_at": ts, "updated_at": ts})
    return await _entity_or_404(eid)


@router.post("/projects/{pid}/entities/import-media")
async def import_flow_media(pid: str, body: ImportMediaRequest):
    """Tạo entity mới từ một media_id Flow bất kỳ (đồng bộ asset từ project trên Flow)."""
    await _project_or_404(pid)
    web = await media_store.ensure_local(body.media_id, pid)
    if not web:
        raise HTTPException(404, "media_id không hợp lệ hoặc không tồn tại trên Flow")
    eid = db.new_id()
    ts = db.now()
    await db.insert("entity", {
        "id": eid, "project_id": pid, "type": body.type or "character",
        "name": (body.name or "Flow asset")[:80], "description": body.description,
        "ref_prompt": "", "media_id": body.media_id, "primary_media_id": body.media_id,
        "image_path": web, "created_at": ts, "updated_at": ts})
    return await _entity_or_404(eid)


@router.post("/projects/{pid}/entities/extract")
async def extract_entities(pid: str):
    p = await _project_or_404(pid)
    if not p.get("script_raw"):
        raise HTTPException(400, "Chưa có kịch bản để trích entity")
    items = await brain.run_json(brain.entity_extract_prompt(p["script_raw"]))
    if not isinstance(items, list):
        raise HTTPException(502, "AI không trả về danh sách entity")
    # tránh trùng tên (đã có)
    existing = {r["name"].lower() for r in await db.query_all(
        "SELECT name FROM entity WHERE project_id=?", (pid,))}
    ts = db.now()
    added = 0
    for it in items:
        name = (it.get("name") or "").strip()
        if not name or name.lower() in existing:
            continue
        await db.insert("entity", {
            "id": db.new_id(), "project_id": pid,
            "type": it.get("type", "character"), "name": name,
            "description": it.get("description", ""),
            "ref_prompt": it.get("ref_prompt", ""),
            "created_at": ts, "updated_at": ts})
        added += 1
    return {"added": added, "entities": await db.query_all(
        "SELECT * FROM entity WHERE project_id=? ORDER BY type, created_at", (pid,))}


@router.post("/projects/{pid}/entities")
async def add_entity(pid: str, body: AddEntityRequest):
    await _project_or_404(pid)
    ts = db.now()
    eid = db.new_id()
    await db.insert("entity", {
        "id": eid, "project_id": pid, "type": body.type, "name": body.name,
        "description": body.description, "ref_prompt": body.ref_prompt,
        "created_at": ts, "updated_at": ts})
    return await _entity_or_404(eid)


@router.patch("/entities/{eid}")
async def update_entity(eid: str, body: UpdateEntityRequest):
    await _entity_or_404(eid)
    data = body.model_dump(exclude_none=True)
    data["updated_at"] = db.now()
    await db.update("entity", eid, data)
    return await _entity_or_404(eid)


@router.delete("/entities/{eid}")
async def delete_entity(eid: str):
    row = await _entity_or_404(eid)
    await db.delete("entity", eid)
    if row.get("image_path"):
        f = media_store.MEDIA_DIR / row["image_path"].replace("/media/", "", 1)
        if f.exists():
            f.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/entities/{eid}/link")
async def link_entity_media(eid: str, body: LinkEntityRequest):
    """Trỏ ảnh/media_id của một asset (dự án bất kỳ) vào entity NÀY, giữ nguyên tên.

    Dùng khi entity hiện tại (vd 'anh A', prompt đều dùng {anh A}) thực ra là cùng
    nhân vật với 'Nguyễn Văn A' ở dự án khác — chỉ mượn ảnh + media_id, không đổi tên,
    nên các prompt cũ vẫn bind đúng.
    """
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    src = await db.query_one("SELECT * FROM entity WHERE id=?", (body.source_entity_id,))
    if not src or not src.get("media_id"):
        raise HTTPException(404, "Asset nguồn không hợp lệ (chưa có ảnh)")
    web = None
    try:
        web = await media_store.ensure_local(src["media_id"], project["id"])
    except Exception:
        web = None
    web = web or src.get("image_path")
    await db.update("entity", eid, {
        "media_id": src["media_id"],
        "primary_media_id": src.get("primary_media_id") or src["media_id"],
        "workflow_id": src.get("workflow_id"),
        "image_path": web, "updated_at": db.now()})
    return await _entity_or_404(eid)


@router.post("/entities/{eid}/generate")
async def generate_entity(eid: str):
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    return await _generate_entity_image(entity, project)


@router.put("/entities/{eid}/image")
async def set_entity_image(eid: str, body: SetMediaRequest):
    """Gán ảnh chính từ media_id có sẵn (xác thực tồn tại trên Flow → tải local)."""
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    web = await media_store.ensure_local(body.media_id, project["id"])
    if not web:
        raise HTTPException(404, "media_id không hợp lệ hoặc không tồn tại trên Flow")
    await db.update("entity", eid, {
        "media_id": body.media_id, "primary_media_id": body.media_id,
        "image_path": web, "updated_at": db.now()})
    return await _entity_or_404(eid)


@router.post("/projects/{pid}/assets/generate-all")
async def generate_all_assets(pid: str, force: bool = False):
    """✦ Auto gen: sinh ảnh cho asset CHƯA có ảnh (idempotent). Tuần tự + throttle."""
    project = await _project_or_404(pid)
    rows = await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,))
    todo = [e for e in rows if force or not e.get("image_path")]
    done, errors = 0, []
    for i, e in enumerate(todo):
        try:
            await _generate_entity_image(e, project)
            done += 1
        except Exception as ex:
            errors.append({"entity": e["name"], "error": str(ex)[:200]})
        if i < len(todo) - 1:
            await asyncio.sleep(random.uniform(2, 6))  # rate-limit
    return {"requested": len(todo), "done": done, "errors": errors}


# ─── Storyboard (shots = frames) ────────────────────────────

class AutofillRequest(BaseModel):
    n_frames: Optional[int] = None


class UpdateShotRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    ref_entity_ids: Optional[list[str]] = None
    visual_prompt: Optional[str] = None
    motion_prompt: Optional[str] = None
    duration: Optional[int] = None
    video_model: Optional[str] = None


async def _scene_or_404(sid: str) -> dict:
    row = await db.query_one("SELECT * FROM scene WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "Scene không tồn tại")
    return row


async def _shot_or_404(sid: str) -> dict:
    row = await db.query_one("SELECT * FROM shot WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "Shot không tồn tại")
    return row


async def _scene_project(scene: dict) -> dict:
    return await _project_or_404(scene["project_id"])


async def _next_shot_idx(scene_id: str) -> int:
    row = await db.query_one(
        "SELECT MAX(idx) AS m FROM shot WHERE scene_id=?", (scene_id,))
    return (row["m"] + 1) if row and row["m"] is not None else 0


async def _build_frame_references(shot: dict, scene: dict) -> list[dict]:
    """Resolve shot ref entities (+ scene location) → references list (≤10, location first)."""
    try:
        ids = json.loads(shot.get("ref_entity_ids") or "[]")
    except (json.JSONDecodeError, TypeError):
        ids = []
    if scene.get("location_entity_id"):
        ids = [scene["location_entity_id"]] + [i for i in ids if i != scene["location_entity_id"]]
    refs = []
    seen = set()
    rows = await db.query_all(
        "SELECT * FROM entity WHERE project_id=?", (scene["project_id"],))
    by_id = {r["id"]: r for r in rows}
    # location-type first
    ordered = sorted(ids, key=lambda i: 0 if by_id.get(i, {}).get("type") == "location" else 1)
    for i in ordered:
        e = by_id.get(i)
        if e and e.get("media_id") and e["media_id"] not in seen:
            refs.append({"handle": e["name"], "media_id": e["media_id"]})
            seen.add(e["media_id"])
        if len(refs) >= 10:
            break
    return refs


async def _store_media_on_shot(shot: dict, project: dict, info: dict,
                               kind: str, label: str):
    """Rename on Flow + download + persist image_*/video_* on the shot."""
    client = get_flow_client()
    if info.get("workflow_id") and project.get("flow_project_id"):
        try:
            await client.change_display_name(
                info["workflow_id"], project["flow_project_id"], label[:60])
        except Exception:
            pass
    ext = "png" if kind == "image" else "mp4"
    web = await media_store.ensure_local(info["media_id"], project["id"], ext) \
        if info.get("media_id") else None
    fields = {
        f"{kind}_media_id": info.get("media_id"),
        f"{kind}_primary_id": info.get("primary_media_id"),
        f"{kind}_workflow_id": info.get("workflow_id"),
        f"{kind}_path": web, "updated_at": db.now(),
    }
    await db.update("shot", shot["id"], fields)
    if kind == "image":
        await _maybe_set_cover(project["id"], project.get("flow_project_id"), info.get("media_id"))
    return await _shot_or_404(shot["id"])


async def _generate_frame_image(shot: dict) -> dict:
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    refs = await _build_frame_references(shot, scene)
    prompt = brain.compose_prompt(
        project, shot.get("description") or shot.get("title") or "")
    aspect = _to_image_aspect(project["aspect_ratio"])
    model = await _resolve_image_model(project)
    tier = await _current_tier()
    return await _generate_image_verified(
        gen_call=lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, references=refs or None, image_model=model),
        store_call=lambda info: _store_media_on_shot(
            shot, project, info, "image", f"s{scene['idx']+1:02d}_{shot['idx']+1:02d}_img"),
        label_for_err=f"frame {shot.get('title') or shot['id'][:6]}")


@router.get("/scenes/{sid}/shots")
async def list_scene_shots(sid: str):
    await _scene_or_404(sid)
    return {"shots": await db.query_all(
        "SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))}


@router.get("/projects/{pid}/shots")
async def list_project_shots(pid: str):
    await _project_or_404(pid)
    return {"shots": await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? ORDER BY sc.idx, sh.idx", (pid,))}


@router.post("/scenes/{sid}/storyboard/autofill")
async def autofill_storyboard(sid: str, body: AutofillRequest):
    scene = await _scene_or_404(sid)
    project = await _project_or_404(scene["project_id"])
    entities = await db.query_all(
        "SELECT name, type, description FROM entity WHERE project_id=?", (scene["project_id"],))
    frames = await brain.run_json(brain.storyboard_autofill_prompt(
        scene["heading"], scene.get("action") or "", entities, project["style"], body.n_frames))
    if not isinstance(frames, list):
        raise HTTPException(502, "AI không trả về danh sách frame")
    erows = await db.query_all(
        "SELECT id, name, type FROM entity WHERE project_id=?", (scene["project_id"],))
    name_to_id = {r["name"].lower(): r["id"] for r in erows}
    # scene location = first location-type entity referenced by any frame
    used_names = {n.lower() for f in frames for n in f.get("ref_entity_names", [])}
    loc_id = next((r["id"] for r in erows
                   if r["type"] == "location" and r["name"].lower() in used_names), None)
    await db.execute("DELETE FROM shot WHERE scene_id=?", (sid,))
    ts = db.now()
    for i, f in enumerate(frames):
        ref_names = list(f.get("ref_entity_names", []))
        ref_ids = [name_to_id[n.lower()] for n in ref_names if n.lower() in name_to_id]
        # ensure the scene location is always referenced by every frame
        if loc_id and loc_id not in ref_ids:
            ref_ids = [loc_id] + ref_ids
        await db.insert("shot", {
            "id": db.new_id(), "scene_id": sid, "idx": i,
            "title": f.get("title", f"Shot {i+1}"),
            "description": f.get("description", ""),
            # visual/motion prompts come from the same autofill pass so the shot image
            # and its video action stay consistent (same entity references).
            "visual_prompt": f.get("visual_prompt") or None,
            "motion_prompt": f.get("motion_prompt") or None,
            "ref_entity_ids": json.dumps(ref_ids),
            "duration": project["shot_duration"] or 8,
            "status": "pending", "created_at": ts, "updated_at": ts})
    if loc_id:
        await db.update("scene", sid, {"location_entity_id": loc_id})
    return {"shots": await db.query_all(
        "SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))}


@router.post("/projects/{pid}/storyboard/autofill-all")
async def autofill_all_storyboard(pid: str, body: AutofillRequest, force: bool = False):
    """✨ Autofill every scene in the project (skip scenes that already have shots unless force)."""
    await _project_or_404(pid)
    scenes = await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))
    done, errors = 0, []
    for sc in scenes:
        if not force:
            existing = await db.query_one(
                "SELECT COUNT(*) AS n FROM shot WHERE scene_id=?", (sc["id"],))
            if existing and existing["n"]:
                continue
        try:
            await autofill_storyboard(sc["id"], body)
            done += 1
        except Exception as ex:
            errors.append({"scene": sc["id"], "error": str(ex)[:200]})
    return {"requested": len(scenes), "done": done, "errors": errors}


@router.post("/scenes/{sid}/shots")
async def add_shot(sid: str):
    await _scene_or_404(sid)
    ts = db.now()
    sidx = await _next_shot_idx(sid)
    shot_id = db.new_id()
    await db.insert("shot", {
        "id": shot_id, "scene_id": sid, "idx": sidx, "title": f"Shot {sidx+1}",
        "description": "", "ref_entity_ids": "[]", "duration": 8,
        "status": "pending", "created_at": ts, "updated_at": ts})
    return await _shot_or_404(shot_id)


@router.post("/shots/{sid}/insert")
async def insert_shot(sid: str):
    cur = await _shot_or_404(sid)
    ts = db.now()
    # đẩy idx các shot sau lên 1
    await db.execute("UPDATE shot SET idx = idx + 1 WHERE scene_id=? AND idx > ?",
                     (cur["scene_id"], cur["idx"]))
    shot_id = db.new_id()
    await db.insert("shot", {
        "id": shot_id, "scene_id": cur["scene_id"], "idx": cur["idx"] + 1,
        "title": "Shot", "description": "", "ref_entity_ids": "[]", "duration": 8,
        "status": "pending", "created_at": ts, "updated_at": ts})
    return await _shot_or_404(shot_id)


@router.patch("/shots/{sid}")
async def update_shot(sid: str, body: UpdateShotRequest):
    await _shot_or_404(sid)
    data = body.model_dump(exclude_none=True)
    if "ref_entity_ids" in data:
        data["ref_entity_ids"] = json.dumps(data["ref_entity_ids"])
    data["updated_at"] = db.now()
    await db.update("shot", sid, data)
    return await _shot_or_404(sid)


@router.delete("/shots/{sid}")
async def delete_shot(sid: str):
    row = await _shot_or_404(sid)
    await db.delete("shot", sid)
    for p in (row.get("image_path"), row.get("video_path")):
        if p:
            f = media_store.MEDIA_DIR / p.replace("/media/", "", 1)
            if f.exists():
                f.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/shots/{sid}/image")
async def generate_shot_image(sid: str):
    shot = await _shot_or_404(sid)
    return await _generate_frame_image(shot)


@router.put("/shots/{sid}/image-from-media")
async def set_shot_image(sid: str, body: SetMediaRequest):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    web = await media_store.ensure_local(body.media_id, scene["project_id"])
    if not web:
        raise HTTPException(404, "media_id không hợp lệ hoặc không tồn tại trên Flow")
    await db.update("shot", sid, {
        "image_media_id": body.media_id, "image_primary_id": body.media_id,
        "image_path": web, "updated_at": db.now()})
    return await _shot_or_404(sid)


@router.post("/scenes/{sid}/storyboard/generate-all")
async def generate_scene_images(sid: str, force: bool = False):
    await _scene_or_404(sid)
    shots = await db.query_all("SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))
    return await _batch_generate_images(shots, force)


def _slug(s: str) -> str:
    """Filename-safe slug (keeps Vietnamese diacritics, spaces → '-')."""
    import re as _re
    s = (s or "").strip().lower()
    s = _re.sub(r"\s+", "-", s)
    s = _re.sub(r'[\\/:*?"<>|\r\n\t]+', "", s)
    s = _re.sub(r"-{2,}", "-", s).strip("-")
    return s[:60] or "shot"


@router.get("/projects/{pid}/storyboard/export")
async def export_storyboard_images(pid: str):
    """Đóng gói toàn bộ ảnh storyboard thành .zip, đặt tên scXXX-sXXX-mô-tả.png."""
    project = await _project_or_404(pid)
    shots = await db.query_all(
        "SELECT sh.*, sc.idx AS scene_idx FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? AND sh.image_path IS NOT NULL ORDER BY sc.idx, sh.idx", (pid,))
    if not shots:
        raise HTTPException(400, "Chưa có ảnh storyboard nào để export")

    out_dir = assembler.STUDIO_MEDIA_DIR / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "storyboard_images.zip"

    def _build():
        import zipfile
        used: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for sh in shots:
                src = media_store.MEDIA_DIR / sh["image_path"].replace("/media/", "", 1)
                if not src.exists():
                    continue
                desc = _slug(sh.get("description") or sh.get("title") or "")
                name = f"sc{sh['scene_idx']+1:03d}-s{sh['idx']+1:03d}-{desc}.png"
                # tránh trùng tên
                base, i = name, 2
                while name in used:
                    name = base[:-4] + f"-{i}.png"
                    i += 1
                used.add(name)
                zf.write(src, name)
        return len(used)

    n = await asyncio.to_thread(_build)
    if not n:
        raise HTTPException(400, "Không có file ảnh local hợp lệ để export")
    fname = f"{_slug(project['title'])}-storyboard.zip"
    return FileResponse(zip_path, media_type="application/zip", filename=fname)


@router.post("/projects/{pid}/storyboard/generate-all")
async def generate_project_images(pid: str, force: bool = False):
    await _project_or_404(pid)
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? ORDER BY sc.idx, sh.idx", (pid,))
    return await _batch_generate_images(shots, force)


async def _batch_generate_images(shots: list[dict], force: bool) -> dict:
    todo = [s for s in shots if force or not s.get("image_path")]
    done, errors = 0, []
    for i, s in enumerate(todo):
        try:
            await _generate_frame_image(s)
            done += 1
        except Exception as ex:
            errors.append({"shot": s["id"], "error": str(ex)[:200]})
        if i < len(todo) - 1:
            await asyncio.sleep(random.uniform(2, 6))
    return {"requested": len(todo), "done": done, "errors": errors}


# ─── Shots (video) ──────────────────────────────────────────

def _extract_video_submit(payload: dict) -> dict:
    media = (payload.get("media") or [{}])[0]
    wf = (payload.get("workflows") or [{}])[0]
    return {
        "media_id": media.get("name"),
        "workflow_id": wf.get("name"),
        "primary_media_id": wf.get("metadata", {}).get("primaryMediaId"),
    }


async def _poll_video(client, op: dict, timeout: float = 240, interval: float = 8):
    """Poll check-status until the video URL appears; return URL or None."""
    import time as _t
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        await asyncio.sleep(interval)
        st = await client.check_video_status([op])
        data = st.get("data", st)
        ops = data.get("operations") or []
        if ops:
            video = ops[0].get("operation", {}).get("metadata", {}).get("video", {})
            if video.get("fifeUrl"):
                return video["fifeUrl"]
    return None


async def _generate_shot_video(shot: dict) -> dict:
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    if not shot.get("image_media_id"):
        raise HTTPException(400, "Shot chưa có ảnh frame — tạo ảnh ở Storyboard trước")
    motion = shot.get("motion_prompt") or shot.get("visual_prompt") or shot.get("description") or ""
    tier = await _current_tier()
    await db.update("shot", shot["id"], {"status": "running", "updated_at": db.now()})

    last = ""
    for attempt in range(VIDEO_GEN_RETRIES):
        res = await client.generate_video(
            start_image_media_id=shot["image_media_id"], prompt=motion,
            project_id=project["flow_project_id"], scene_id=shot["id"],
            aspect_ratio=project["aspect_ratio"], user_paygate_tier=tier)
        if res.get("error"):
            last = str(res["error"])
        else:
            info = _extract_video_submit(res.get("data", res))
            if not info.get("media_id"):
                last = _image_block_reason(res.get("data", res)) or "Flow không trả media"
            else:
                op = {"operation": {"name": info["media_id"]}, "sceneId": shot["id"]}
                await db.update("shot", shot["id"], {"operation_json": json.dumps(op)})
                url = await _poll_video(client, op)
                if not url:
                    last = "video chưa xong trong thời gian chờ"
                else:
                    if info.get("workflow_id"):
                        try:
                            await client.change_display_name(
                                info["workflow_id"], project["flow_project_id"],
                                f"s{scene['idx']+1:02d}_{shot['idx']+1:02d}_vid")
                        except Exception:
                            pass
                    web = await media_store.save_from_url(
                        info["media_id"], project["id"], "mp4", url)
                    if web:
                        await db.update("shot", shot["id"], {
                            "video_media_id": info["media_id"],
                            "video_primary_id": info["primary_media_id"],
                            "video_workflow_id": info["workflow_id"], "video_path": web,
                            "status": "done", "updated_at": db.now()})
                        return await _shot_or_404(shot["id"])
                    last = "tải video về lỗi"
        logger.warning("video shot %s hỏng (lần %d/%d): %s",
                       shot["id"][:6], attempt + 1, VIDEO_GEN_RETRIES, last)
        if attempt < VIDEO_GEN_RETRIES - 1:
            await asyncio.sleep(random.uniform(5, 10))

    await db.update("shot", shot["id"], {"status": "error", "updated_at": db.now()})
    raise HTTPException(502, f"Tạo video thất bại sau {VIDEO_GEN_RETRIES} lần: {last}")


@router.post("/shots/{sid}/prompts")
async def gen_shot_prompts(sid: str):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    out = await brain.run_json(brain.shot_prompts_prompt(
        shot.get("description") or shot.get("title") or "", project["style"]))
    await db.update("shot", sid, {
        "visual_prompt": out.get("visual_prompt"),
        "motion_prompt": out.get("motion_prompt"), "updated_at": db.now()})
    return await _shot_or_404(sid)


@router.post("/shots/{sid}/video")
async def generate_shot_video(sid: str):
    shot = await _shot_or_404(sid)
    return await _generate_shot_video(shot)


@router.post("/shots/{sid}/upscale")
async def upscale_shot(sid: str, resolution: str = "VIDEO_RESOLUTION_4K"):
    shot = await _shot_or_404(sid)
    if not shot.get("video_media_id"):
        raise HTTPException(400, "Shot chưa có video để upscale")
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    res = await client.upscale_video(
        media_id=shot["video_media_id"], scene_id=shot["id"],
        aspect_ratio=project["aspect_ratio"], resolution=resolution)
    if res.get("error"):
        raise HTTPException(502, str(res["error"]))
    info = _extract_video_submit(res.get("data", res))
    op = {"operation": {"name": info["media_id"]}, "sceneId": shot["id"]}
    url = await _poll_video(client, op, timeout=300)
    if not url:
        raise HTTPException(504, "Upscale chưa xong — thử lại sau")
    web = await media_store.save_from_url(info["media_id"], project["id"], "mp4", url)
    await db.update("shot", sid, {"upscale_path": web, "upscale_url": url, "updated_at": db.now()})
    return await _shot_or_404(sid)


@router.post("/projects/{pid}/shots/generate-all")
async def generate_all_videos(pid: str, force: bool = False):
    """✦ Auto gen video cho shot CÓ ảnh, CHƯA có video. Tuần tự + throttle 15–30s."""
    await _project_or_404(pid)
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? ORDER BY sc.idx, sh.idx", (pid,))
    todo = [s for s in shots if s.get("image_media_id") and (force or not s.get("video_path"))]
    done, errors = 0, []
    for i, s in enumerate(todo):
        try:
            await _generate_shot_video(s)
            done += 1
        except Exception as ex:
            errors.append({"shot": s["id"], "error": str(ex)[:200]})
        if i < len(todo) - 1:
            await asyncio.sleep(random.uniform(15, 30))
    return {"requested": len(todo), "done": done, "errors": errors}


# ─── Node Editor graphs ─────────────────────────────────────

class SaveGraphRequest(BaseModel):
    graph: dict


@router.get("/shots/{sid}/graph")
async def get_shot_graph(sid: str):
    row = await _shot_or_404(sid)
    return {"graph": json.loads(row["graph_json"]) if row.get("graph_json") else None}


@router.put("/shots/{sid}/graph")
async def put_shot_graph(sid: str, body: SaveGraphRequest):
    await _shot_or_404(sid)
    await db.update("shot", sid, {"graph_json": json.dumps(body.graph), "updated_at": db.now()})
    return {"ok": True}


@router.post("/shots/{sid}/graph/run")
async def run_shot_graph(sid: str, body: SaveGraphRequest):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    await db.update("shot", sid, {"graph_json": json.dumps(body.graph)})
    project = {**project, "paygate_tier": await _current_tier()}
    try:
        out = await graph_mod.run_graph(body.graph, shot, project, "shot")
    except graph_mod.GraphError as e:
        raise HTTPException(400, str(e))
    return {**out, "shot": await _shot_or_404(sid)}


@router.get("/entities/{eid}/graph")
async def get_entity_graph(eid: str):
    row = await _entity_or_404(eid)
    return {"graph": json.loads(row["graph_json"]) if row.get("graph_json") else None}


@router.put("/entities/{eid}/graph")
async def put_entity_graph(eid: str, body: SaveGraphRequest):
    await _entity_or_404(eid)
    await db.update("entity", eid, {"graph_json": json.dumps(body.graph), "updated_at": db.now()})
    return {"ok": True}


@router.post("/entities/{eid}/graph/run")
async def run_entity_graph(eid: str, body: SaveGraphRequest):
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    await db.update("entity", eid, {"graph_json": json.dumps(body.graph)})
    project = {**project, "paygate_tier": await _current_tier()}
    try:
        out = await graph_mod.run_graph(body.graph, entity, project, "entity")
    except graph_mod.GraphError as e:
        raise HTTPException(400, str(e))
    return {**out, "entity": await _entity_or_404(eid)}


# ─── Assemble / narration / export ──────────────────────────

class NarrationRequest(BaseModel):
    language: str = "Vietnamese"
    text: Optional[str] = None     # nếu None → AI tự sinh


async def _tts_wav(text: str, voice_id: int, project_id: str, shot_id: str) -> Optional[str]:
    """Synthesize via OmniVoice, save WAV under media/<pid>/, return web path."""
    import base64
    from agent.api.tts import _proxy
    res = await _proxy("POST", "/api/tts",
                       json={"text": text, "voice_id": voice_id}, timeout=300.0)
    b64 = res.get("audio") if isinstance(res, dict) else None
    if not b64:
        raise HTTPException(502, "OmniVoice không trả audio")
    rel = f"{project_id}/narr_{shot_id}.wav"
    dest = media_store.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(b64))
    return f"/media/{rel}"


@router.post("/shots/{sid}/narration")
async def shot_narration(sid: str, body: NarrationRequest):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    text = body.text
    if not text:
        out = await brain.run_json(brain.narrator_prompt(
            shot.get("description") or shot.get("title") or "", body.language))
        text = out.get("narrator_text", "")
    if not text:
        raise HTTPException(502, "Không sinh được narrator text")
    voice_id = project.get("voice_id") or 0
    web = await _tts_wav(text, voice_id, project["id"], sid)
    dur = await assembler.probe_duration(assembler._local(web)) if web else 0.0
    await db.update("shot", sid, {
        "narrator_text": text, "narration_path": web,
        "narration_duration": dur, "updated_at": db.now()})
    return await _shot_or_404(sid)


@router.post("/projects/{pid}/assemble")
async def assemble_project(pid: str):
    await _project_or_404(pid)
    try:
        return await assembler.assemble(pid)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/projects/{pid}/assemble-images")
async def assemble_project_images(pid: str, ken_burns: bool = True):
    """Ghép 1 video dài từ ẢNH các shot, mỗi ảnh dài đúng bằng narration của shot."""
    await _project_or_404(pid)
    try:
        return await assembler.assemble_from_images(pid, ken_burns=ken_burns)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/projects/{pid}/export/davinci-xml")
async def export_davinci(pid: str):
    await _project_or_404(pid)
    try:
        return await davinci_xml.build(pid)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/projects/{pid}/export")
async def export_project(pid: str):
    """Sinh metadata SEO (AI) + SRT từ narration + thumbnail (AI → Flow image)."""
    p = await _project_or_404(pid)
    meta = await brain.run_json(brain.seo_prompt(p["title"], p.get("script_raw") or ""))
    # SRT từ narration các shot (theo thứ tự)
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? ORDER BY sc.idx, sh.idx", (pid,))
    srt, t = [], 0.0
    for i, sh in enumerate(shots):
        if not sh.get("narrator_text"):
            continue
        dur = sh.get("narration_duration") or sh.get("duration") or 4
        srt.append(f"{i+1}\n{_ts(t)} --> {_ts(t+dur)}\n{sh['narrator_text']}\n")
        t += dur
    srt_text = "\n".join(srt)
    out_dir = assembler.STUDIO_MEDIA_DIR / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "subtitles.srt").write_text(srt_text, encoding="utf-8")
    # thumbnail
    thumb_web = None
    try:
        client = get_flow_client()
        if client.connected and meta.get("thumbnail_prompt"):
            res = await client.generate_images(
                prompt=brain.compose_prompt(p, meta["thumbnail_prompt"]),
                project_id=p["flow_project_id"],
                aspect_ratio="IMAGE_ASPECT_RATIO_LANDSCAPE",
                user_paygate_tier=await _current_tier(),
                image_model=await _resolve_image_model(p))
            info = _extract_image_result(res.get("data", res))
            if info.get("media_id"):
                thumb_web = await media_store.ensure_local(info["media_id"], pid)
    except Exception as e:
        logger.warning("thumbnail gen failed: %s", e)
    await db.update("project", pid, {"updated_at": db.now()})
    return {"metadata": meta, "srt": srt_text, "thumbnail": thumb_web}


def _ts(sec: float) -> str:
    h = int(sec // 3600); m = int((sec % 3600) // 60)
    s = int(sec % 60); ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── Thumbnail / media resolve ──────────────────────────────

@router.get("/thumb/{media_key}")
async def thumb(media_key: str):
    """Trả thumbnail (tải về cache local 1 lần) cho ảnh đại diện project/media."""
    path = await media_store.ensure_thumb(media_key)
    if not path:
        raise HTTPException(404, "Không lấy được thumbnail (id sai hoặc chưa sẵn sàng)")
    return FileResponse(path, media_type="image/png")


@router.post("/media/ensure/{media_id}")
async def ensure_media(media_id: str, project_id: str, ext: str = "png"):
    """Đảm bảo file local tồn tại; trả web path."""
    web = await media_store.ensure_local(media_id, project_id, ext)
    if not web:
        raise HTTPException(404, "Không tải được media")
    return {"path": web}
