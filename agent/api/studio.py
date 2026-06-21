"""Flow Studio API — stateful orchestration over the Flow proxy (video-app.md).

Phase 0: project CRUD (DB + Flow), Flow project import with thumbnails, options,
settings, health. Heavier pipeline endpoints land in later phases.
"""
import asyncio
import json
import logging
import math
import os
import random
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from agent.config import (
    IMAGE_MODELS, VIDEO_MODELS, UPSCALE_MODELS, OMNI_FLASH_MODELS,
)
from agent.services.flow_client import get_flow_client
from agent.studio import db, media_store, brain, assembler, davinci_xml, vntext, graph as graph_mod
from agent.studio.jobs import get_job_manager

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
    script_lang: str = "Vietnamese"       # ngôn ngữ kịch bản / lời thoại / lời đọc
    image_text_lang: str = "Vietnamese"   # ngôn ngữ chữ viết/vẽ trong ảnh
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
    script_lang: Optional[str] = None
    image_text_lang: Optional[str] = None
    bgm_volume: Optional[float] = None
    bgm_duck: Optional[bool] = None
    tts_speed: Optional[float] = None
    seed: Optional[int] = None
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


@router.get("/fonts")
async def list_fonts():
    """Các font có trên máy để chọn cho caption (vẽ chữ lên video)."""
    fonts = await asyncio.to_thread(assembler.list_fonts)
    return {"fonts": fonts, "current": (await db.kv_get_all()).get("caption_font") or ""}


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


def _flow_existing_media_ids(raw: dict) -> set[str]:
    """Every media id that still lives in a Flow project — raw media[] names, each
    workflow's primaryMediaId, and uploaded references. Used to detect deletions: a
    local media id absent from this set was removed on Flow. Includes videos (unlike
    `_flow_media_items`, which is image-only) so video sync works too."""
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    ids: set[str] = set()
    for m in (_deep_find(data, "media") or []):
        if isinstance(m, dict) and m.get("name"):
            ids.add(m["name"])
    for w in (_deep_find(data, "workflows") or []):
        if isinstance(w, dict):
            mid = (w.get("metadata") or {}).get("primaryMediaId")
            if mid:
                ids.add(mid)
    for e in (_deep_find(data, "externalReferenceMedia") or []):
        if isinstance(e, dict) and e.get("mediaId"):
            ids.add(e["mediaId"])
    return ids


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
    # Global defaults (Settings §2.7A): used for fields the create form doesn't ask about,
    # so a new project inherits the studio-wide preferences. Order: per-project (form) →
    # global (kv) → hard default.
    kv = await db.kv_get_all()
    row = {
        "id": pid, "title": body.title, "flow_project_id": flow_id,
        "style": (body.style or kv.get("style") or "Realistic"),
        "aspect_ratio": (body.aspect_ratio or kv.get("aspect_ratio")
                         or "VIDEO_ASPECT_RATIO_LANDSCAPE"),
        "paygate_tier": await _current_tier(),   # từ /api/flow/credits, không do user chọn
        "storytelling": 1 if body.storytelling else 0,
        "script_lang": (body.script_lang or "Vietnamese").strip() or "Vietnamese",
        "image_text_lang": (body.image_text_lang or "Vietnamese").strip() or "Vietnamese",
        "thumb_media_key": thumb,
        "status": "draft", "created_at": ts, "updated_at": ts,
    }
    if kv.get("image_model"):
        row["image_model"] = kv["image_model"]
    if kv.get("video_model"):
        row["video_model"] = kv["video_model"]
    for key, cast in (("shot_duration", int), ("voice_id", int), ("tts_speed", float)):
        if kv.get(key) not in (None, ""):
            try:
                row[key] = cast(kv[key])
            except (TypeError, ValueError):
                pass
    await db.insert("project", row)
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
    if "bgm_duck" in data:
        data["bgm_duck"] = 1 if data["bgm_duck"] else 0
    if "seed" in data and (data["seed"] is None or data["seed"] <= 0):
        data["seed"] = None   # ≤0 / trống = bỏ khoá seed (ngẫu nhiên)
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
        p["style"], p["shot_duration"] or 8, p.get("script_lang") or "Vietnamese"))
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
        p["script_raw"] or "", body.instruction, p["style"],
        p.get("script_lang") or "Vietnamese"))
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


async def _gen_candidates(gen_call, project: dict, n: int) -> list[dict]:
    """Generate N candidate images WITHOUT committing them to any record (§13#2 — pick the
    best of several). Each is downloaded to local so the UI can preview it; the chosen one is
    committed later via apply-media. Calls are spaced out and serialized by the single-flight
    lock. Returns [{media_id, primary_media_id, workflow_id, web}]."""
    out: list[dict] = []
    for i in range(n):
        res = await gen_call()
        if res.get("error"):
            logger.warning("candidate %d/%d lỗi: %s", i + 1, n, res["error"])
        else:
            info = _extract_image_result(res.get("data", res))
            mid = info.get("media_id")
            if mid:
                web = await media_store.ensure_local(mid, project["id"])
                if web:
                    out.append({"media_id": mid,
                                "primary_media_id": info.get("primary_media_id") or mid,
                                "workflow_id": info.get("workflow_id"), "web": web})
        if i < n - 1:
            await asyncio.sleep(random.uniform(2, 5))
    if not out:
        raise HTTPException(502, "Không tạo được ảnh ứng viên nào (có thể bị chặn nội dung)")
    return out


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


async def _record_media_history(project_id: str, owner_kind: str, owner_id: str,
                                slot: str, media_id, primary_id, path) -> None:
    """Append a media-history row (§13#8) so an overwritten image/video can be restored.
    Skips a no-op repeat of the current latest entry for this owner+slot."""
    if not (media_id and path):
        return
    last = await db.query_one(
        "SELECT media_id FROM media_history WHERE owner_id=? AND slot=? "
        "ORDER BY created_at DESC LIMIT 1", (owner_id, slot))
    if last and last.get("media_id") == media_id:
        return
    await db.insert("media_history", {
        "id": db.new_id(), "project_id": project_id, "owner_kind": owner_kind,
        "owner_id": owner_id, "slot": slot, "media_id": media_id,
        "primary_media_id": primary_id, "path": path, "created_at": db.now()})


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
    await _record_media_history(project["id"], "entity", entity["id"], "image",
                               info.get("media_id"), info.get("primary_media_id"), web)
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
    row = await _generate_image_verified(
        gen_call=lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, image_model=model, seed=project.get("seed")),
        store_call=lambda info: _store_media_on_entity(
            entity, project, info, f"{entity['type']}_{entity['name']}"),
        label_for_err=f"asset {entity['name']}")
    # A location's reference image is ONE 2x2 grid of four angles. Overlay the position
    # labels on the quadrants for management (display only; the underlying media stays clean).
    if entity["type"] == "location" and row.get("media_id"):
        try:
            await _label_location_grid(row, project)
            row = await _entity_or_404(entity["id"])
        except Exception as ex:  # noqa: BLE001
            logger.warning("location grid labelling failed for %s: %s", entity["name"], ex)
    return row


async def _label_location_grid(entity: dict, project: dict) -> None:
    """Overlay the four position labels (Toàn cảnh / Góc ngược / Trên cao / Cận cảnh) on the
    location's 2x2 grid quadrants → a labeled DISPLAY copy set as image_path. The original
    grid (media_id) stays unlabeled and is what shots reference."""
    web = entity.get("image_path")
    if not web:
        return
    src = media_store.MEDIA_DIR / web.replace("/media/", "", 1)
    if not src.exists():
        return
    out_rel = f"{project['id']}/loc_{entity['id']}_labeled.png"
    out_abs = media_store.MEDIA_DIR / out_rel
    ok = await asyncio.to_thread(
        assembler.label_quadrants, src, out_abs, brain.LOCATION_GRID_LABELS,
        assembler._caption_font())
    if ok:
        await db.update("entity", entity["id"],
                        {"image_path": f"/media/{out_rel}", "updated_at": db.now()})


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
async def extract_entities(pid: str, replace: bool = False):
    """Trích entity từ kịch bản. `replace=true` → XOÁ toàn bộ entity hiện tại (kèm ảnh)
    rồi trích lại từ đầu; mặc định chỉ thêm entity mới (bỏ qua tên đã có)."""
    p = await _project_or_404(pid)
    if not p.get("script_raw"):
        raise HTTPException(400, "Chưa có kịch bản để trích entity")
    items = await brain.run_json(brain.entity_extract_prompt(p["script_raw"]))
    if not isinstance(items, list):
        raise HTTPException(502, "AI không trả về danh sách entity")
    if replace:
        for r in await db.query_all(
                "SELECT id, image_path FROM entity WHERE project_id=?", (pid,)):
            await db.delete("entity", r["id"])
            if r.get("image_path"):
                f = media_store.MEDIA_DIR / r["image_path"].replace("/media/", "", 1)
                f.unlink(missing_ok=True)
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
    """✦ Auto gen ảnh cho asset CHƯA có ảnh → job nền (§9). Trả job_id ngay."""
    project = await _project_or_404(pid)
    rows = await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,))
    todo = [e for e in rows if force or not e.get("image_path")]

    async def _worker(e):
        await _generate_entity_image(e, project)

    job = get_job_manager().start(
        project_id=pid, type_="assets", items=todo, worker=_worker,
        label=f"Sinh ảnh asset ({len(todo)})", throttle=(2, 6),
        item_label=lambda e: e.get("name") or e["id"])
    return {"job_id": job.id, "total": len(todo)}


# ─── Storyboard (shots = frames) ────────────────────────────

class AutofillRequest(BaseModel):
    n_frames: Optional[int] = None


class BuildBeatsRequest(BaseModel):
    language: Optional[str] = None   # None → dùng script_lang của dự án
    # measure=True → TTS each scene now for the real audio length (needs OmniVoice up);
    # False → estimate from word count (no quota), real length fitted later.
    measure: bool = True


# ≈2.5 spoken words/second (video-app.md §5.2) → estimate a narration's length without
# burning TTS quota. Real durations replace this when narration is generated / at assemble.
def _estimate_narration_secs(text: str) -> float:
    words = len((text or "").split())
    return max(1.0, round(words / 2.5, 2))


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
    await _record_media_history(project["id"], "shot", shot["id"], kind,
                               info.get("media_id"), info.get("primary_media_id"), web)
    if kind == "image":
        await _maybe_set_cover(project["id"], project.get("flow_project_id"), info.get("media_id"))
    return await _shot_or_404(shot["id"])


async def _generate_frame_image(shot: dict) -> dict:
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    refs = await _build_frame_references(shot, scene)
    prompt = brain.compose_prompt(
        project, shot.get("description") or shot.get("title") or "", single_frame=True)
    aspect = _to_image_aspect(project["aspect_ratio"])
    model = await _resolve_image_model(project)
    tier = await _current_tier()
    return await _generate_image_verified(
        gen_call=lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, references=refs or None, image_model=model,
            seed=project.get("seed")),
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


# ─── Shot reference resolution (location + entities actually in the prompt) ──

_BRACE_RE = re.compile(r"\{([^{}]+)\}")
_HEADING_PREFIX_RE = re.compile(r"^\s*(INT\.?/?EXT\.?|INT\.|EXT\.|NỘI\.|NGOẠI\.|I/E\.)\s*", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _brace_names(text: str) -> set[str]:
    """Entity names the AI actually wrapped in {curly braces} (the binding tokens)."""
    return {m.strip() for m in _BRACE_RE.findall(text or "") if m.strip()}


def _location_from_heading(heading: str) -> str:
    """'INT. BẾP CỦA THIÊN ÂN - NGÀY' → 'BẾP CỦA THIÊN ÂN'."""
    h = _HEADING_PREFIX_RE.sub("", (heading or "").strip())
    return re.split(r"\s+[-–—]\s+", h)[0].strip()


def _match_location_entity(heading: str, locations: list[dict]) -> Optional[dict]:
    """The location entity named in the scene heading (exact, else containment)."""
    target = _norm(_location_from_heading(heading))
    if not target or not locations:
        return None
    for e in locations:
        if _norm(e["name"]) == target:
            return e
    for e in locations:
        n = _norm(e["name"])
        if n and (n in target or target in n):
            return e
    return None


def _first_location_id(frames: list[dict], by_name: dict) -> Optional[str]:
    """Fallback when the heading matches no location entity: the first location the AI named."""
    for f in frames:
        text = " ".join(filter(None, [f.get("description"), f.get("visual_prompt"), f.get("motion_prompt")]))
        for n in (set(f.get("ref_entity_names") or []) | _brace_names(text)):
            e = by_name.get(_norm(n))
            if e and e["type"] == "location":
                return e["id"]
    return None


def _resolve_shot_refs(text: str, ref_names, by_name: dict, scene_loc_id: Optional[str]) -> list[str]:
    """A shot references EXACTLY one location (the scene's) plus every NON-location entity
    actually named in the prompt ({braces}) or ref_entity_names. Any other location is dropped
    so a shot never mixes places, and an entity mentioned in the prompt is always referenced."""
    names = set(ref_names or []) | _brace_names(text)
    other_ids: list[str] = []
    for n in names:
        e = by_name.get(_norm(n))
        if e and e["type"] != "location" and e["id"] not in other_ids:
            other_ids.append(e["id"])
    return ([scene_loc_id] if scene_loc_id else []) + other_ids


@router.post("/scenes/{sid}/storyboard/autofill")
async def autofill_storyboard(sid: str, body: AutofillRequest):
    scene = await _scene_or_404(sid)
    project = await _project_or_404(scene["project_id"])
    erows = await db.query_all(
        "SELECT id, name, type, description FROM entity WHERE project_id=?", (scene["project_id"],))
    by_name = {_norm(r["name"]): r for r in erows}
    # The scene's location is fixed by its heading — every shot uses ONLY this place.
    scene_loc = _match_location_entity(scene["heading"], [r for r in erows if r["type"] == "location"])
    scene_loc_id = scene_loc["id"] if scene_loc else None
    frames = await brain.run_json(brain.storyboard_autofill_prompt(
        scene["heading"], scene.get("action") or "", erows, project["style"], body.n_frames,
        location=(scene_loc["name"] if scene_loc else None)))
    if not isinstance(frames, list):
        raise HTTPException(502, "AI không trả về danh sách frame")
    if not scene_loc_id:                      # heading matched no entity → use the AI's pick
        scene_loc_id = _first_location_id(frames, by_name)
    await db.execute("DELETE FROM shot WHERE scene_id=?", (sid,))
    ts = db.now()
    for i, f in enumerate(frames):
        text = " ".join(filter(None, [f.get("description"), f.get("visual_prompt"), f.get("motion_prompt")]))
        ref_ids = _resolve_shot_refs(text, f.get("ref_entity_names"), by_name, scene_loc_id)
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
    if scene_loc_id:
        await db.update("scene", sid, {"location_entity_id": scene_loc_id})
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


def _strip_word(w: str) -> str:
    return w.lower().strip('.,!?;:"\'’“”…()-')


def _find_subseq(hay: list[str], needle: list[str], start: int) -> int:
    """Index in `hay` where `needle` first occurs at/after `start` (-1 if none)."""
    if not needle:
        return -1
    for i in range(start, len(hay) - len(needle) + 1):
        if all(hay[i + j] == needle[j] for j in range(len(needle))):
            return i
    return -1


def _caption_windows(beat_text: str, key_phrases: list[str],
                     b_start: float, b_dur: float) -> list[dict]:
    """Time each key phrase to roughly when the narration reaches it, by its word position
    within the beat (≈ proportional, since the beat is read at a steady pace)."""
    words = (beat_text or "").split()
    n = len(words) or 1
    low = [_strip_word(w) for w in words]
    caps, search_from = [], 0
    for ph in key_phrases or []:
        pw = [_strip_word(w) for w in (ph or "").split()]
        pw = [w for w in pw if w]
        if not pw:
            continue
        idx = _find_subseq(low, pw, search_from)
        if idx < 0:
            idx = search_from
        start = b_start + (idx / n) * b_dur
        dur = max(1.2, (len(pw) / n) * b_dur)
        caps.append({"text": ph.strip(), "start": round(start, 3),
                     "end": round(min(b_start + b_dur, start + dur), 3)})
        search_from = min(n - 1, idx + len(pw))
    # keep windows from overlapping (one caption on screen at a time)
    for i in range(len(caps) - 1):
        if caps[i]["end"] > caps[i + 1]["start"]:
            caps[i]["end"] = round(caps[i + 1]["start"], 3)
    return [c for c in caps if c["end"] > c["start"]]


def _concat_wav_bytes(chunks: list[bytes], dest) -> None:
    """Join same-format WAV byte blobs (the per-segment TTS outputs) into one WAV file."""
    import io
    import wave
    params, frames = None, []
    for b in chunks:
        with wave.open(io.BytesIO(b), "rb") as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    with wave.open(str(dest), "wb") as out:
        out.setparams(params)
        for f in frames:
            out.writeframes(f)


async def _tts_one(text: str, voice_id: int, speed: float = 1.0) -> bytes:
    """ONE TTS call for the whole text → WAV bytes. A single continuous read keeps the
    narration's emotion (no seams from stitching many short clips)."""
    import base64
    from agent.api.tts import _proxy
    res = await _proxy("POST", "/api/tts",
                       json={"text": text, "voice_id": voice_id, "speed": speed},
                       timeout=600.0)
    b64 = res.get("audio") if isinstance(res, dict) else None
    if not b64:
        raise HTTPException(502, "OmniVoice không trả audio")
    return base64.b64decode(b64)


async def _tts_segments(text: str, voice_id: int, speed: float = 1.0) -> list[bytes]:
    """Fallback only: split VN text into short sentence-aligned segments and TTS each → WAV
    bytes (re-joined by the caller). Used when a single-shot read fails (e.g. text too long
    for the engine); per-scene narration prefers `_tts_one` to stay emotionally continuous."""
    import base64
    from agent.api.tts import _proxy
    out = []
    for seg in (vntext.split_segments(text) or [text]):
        res = await _proxy("POST", "/api/tts",
                           json={"text": seg, "voice_id": voice_id, "speed": speed},
                           timeout=600.0)
        b64 = res.get("audio") if isinstance(res, dict) else None
        if not b64:
            raise HTTPException(502, "OmniVoice không trả audio")
        out.append(base64.b64decode(b64))
    return out


def _wav_bytes_duration(b: bytes) -> float:
    """Duration (s) of a WAV byte buffer, read straight from its header (no ffprobe)."""
    import io
    import wave
    try:
        with wave.open(io.BytesIO(b), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


async def _tts_beats(texts: list[str], voice_id: int, pid: str,
                     scene_id: str, speed: float = 1.0) -> tuple[str, list[float]]:
    """TTS each beat's text as its OWN continuous read, then concat them into the scene WAV.
    Returns (web_path, [per-beat real durations]). One read per beat keeps the emotion within
    each visual unit AND gives the EXACT time each beat occupies, so the image changes land on
    the narration — the cuts fall on beat (image-change) boundaries where a micro-pause is
    natural. Raises if OmniVoice is unreachable (caller falls back to a word-count estimate)."""
    chunks, durs = [], []
    for txt in texts:
        norm = vntext.normalize(txt) or txt
        if not norm.strip():
            continue
        audio = await _tts_one(norm, voice_id, speed)
        chunks.append(audio)
        durs.append(round(_wav_bytes_duration(audio), 3))
    if not chunks:
        raise HTTPException(502, "Không tạo được audio cho beat nào")
    rel = f"{pid}/narr_scene_{scene_id}.wav"
    dest = media_store.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_concat_wav_bytes, chunks, dest)
    return f"/media/{rel}", durs


@router.post("/scenes/{sid}/beats")
async def build_scene_beats(sid: str, body: BuildBeatsRequest):
    """Storytelling (§2.6, audio-first): the scene reads a VERBATIM contiguous chunk of the
    user's original input. We segment it into visual beats, then TTS each beat as its own
    continuous read and measure its REAL duration, so image changes land exactly on the
    narration (the cuts fall on beat = image-change boundaries). Key phrases get timed caption
    windows. If TTS is off/unreachable, beat durations fall back to a word-count estimate."""
    scene = await _scene_or_404(sid)
    project = await _project_or_404(scene["project_id"])
    erows = await db.query_all(
        "SELECT id, name, type, description FROM entity WHERE project_id=?", (scene["project_id"],))
    by_name = {_norm(r["name"]): r for r in erows}
    # The scene's location is fixed by its heading — every beat/shot uses ONLY this place.
    scene_loc = _match_location_entity(scene["heading"], [r for r in erows if r["type"] == "location"])
    scene_loc_id = scene_loc["id"] if scene_loc else None

    # 1) the scene's narration = its VERBATIM slice of the user's ORIGINAL input
    #    (project.idea), read in full — NOT an AI rewrite of the screenplay. Storytelling
    #    must speak the source text the user gave, complete and unaltered. We partition the
    #    original across the project's scenes (in order) so the union covers the whole text.
    source = (project.get("idea") or "").strip()
    if source:
        order = [r["id"] for r in await db.query_all(
            "SELECT id FROM scene WHERE project_id=? ORDER BY idx", (scene["project_id"],))]
        pos = order.index(sid) if sid in order else 0
        parts = brain.partition_text(source, len(order) or 1)
        voiceover = (parts[pos] if pos < len(parts) else "").strip()
        if not voiceover:
            raise HTTPException(
                400, "Scene này không còn nội dung gốc để đọc — số scene đang nhiều hơn "
                "số câu trong nội dung nguồn. Giảm bớt scene hoặc bổ sung nội dung gốc.")
    else:
        # no original input stored → fall back to the scene's own script text, verbatim
        voiceover = (scene.get("action") or "").strip()
        if not voiceover:
            raise HTTPException(400, "Chưa có nội dung gốc (idea) để đọc cho scene này.")

    # 2) segment the verbatim narration into visual beats (AI = visual structure + key
    #    phrases). The SPOKEN text per beat is re-derived verbatim from the narration so the
    #    audio always covers the whole scene in order — no AI drift, no dropped sentences.
    beats = await brain.run_json(brain.scene_segment_prompt(
        voiceover, erows, project["style"],
        location=(scene_loc["name"] if scene_loc else None)))
    if not isinstance(beats, list) or not beats:
        beats = [{"description": scene["heading"], "ref_entity_names": [], "key_phrases": []}]
    if not scene_loc_id:                      # heading matched no entity → use the AI's pick
        scene_loc_id = _first_location_id(beats, by_name)
    say = brain.partition_text(voiceover, len(beats))   # verbatim contiguous slices, complete
    if len(say) < len(beats):                            # fewer sentences than beats → trim
        beats = beats[:len(say)]
    for i, b in enumerate(beats):
        b["_say"] = (say[i] if i < len(say) else (b.get("text") or "")).strip()

    # 3) TTS one continuous read PER BEAT → exact per-beat durations + the scene WAV (concat).
    voice_id = project.get("voice_id") or 0
    speed = float(project.get("tts_speed") or 1.0)
    narr_web, durs = None, None
    if body.measure and any(b.get("_say") for b in beats):
        try:
            narr_web, durs = await _tts_beats(
                [b["_say"] for b in beats], voice_id, project["id"], sid, speed)
        except HTTPException as e:
            logger.warning("beat TTS unavailable (%s) — dùng ước lượng theo số từ", e.detail)
        except Exception as e:  # noqa: BLE001
            logger.warning("beat TTS failed: %s — dùng ước lượng theo số từ", e)
    if durs is None or len(durs) != len(beats):          # TTS off/failed → word-count estimate
        wc = [max(1, len((b.get("_say") or "").split())) for b in beats]
        total_wc = sum(wc) or 1
        scene_est = _estimate_narration_secs(voiceover)
        durs = [max(0.8, round(scene_est * w / total_wc, 3)) for w in wc]
    scene_dur = round(sum(durs), 3)

    await db.execute("DELETE FROM shot WHERE scene_id=?", (sid,))
    await db.update("scene", sid, {
        "narration_text": voiceover, "narration_path": narr_web,
        "narration_duration": scene_dur, "location_entity_id": scene_loc_id})
    ts = db.now()
    t = 0.0
    for i, b in enumerate(beats):
        b_dur = durs[i]
        caps = _caption_windows(b.get("_say") or "", b.get("key_phrases") or [], t, b_dur)
        text = " ".join(filter(None, [b.get("description"), b.get("visual_prompt"), b.get("motion_prompt")]))
        ref_ids = _resolve_shot_refs(text, b.get("ref_entity_names"), by_name, scene_loc_id)
        await db.insert("shot", {
            "id": db.new_id(), "scene_id": sid, "idx": i,
            "beat_id": db.new_id(), "part_idx": 0, "is_chained": 0,
            "title": (b.get("_say") or "")[:40] or f"Beat {i+1}",
            "description": b.get("description", ""),
            "visual_prompt": b.get("visual_prompt") or None,
            "motion_prompt": b.get("motion_prompt") or None,
            "beat_action": b.get("beat_action") or None,
            # narrator_text = this beat's VERBATIM spoken slice; its audio is the beat's
            # segment of the scene WAV (measured duration above).
            "narrator_text": b.get("_say") or None,
            "narration_duration": b_dur,          # this beat's real share of the timeline
            "start_time": round(t, 3),            # scene-local offset
            "captions": json.dumps(caps, ensure_ascii=False),
            "ref_entity_ids": json.dumps(ref_ids),
            "duration": max(1, int(round(b_dur))),
            "status": "pending", "created_at": ts, "updated_at": ts})
        t += b_dur

    return {"shots": await db.query_all(
        "SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,)),
        "scene_duration": scene_dur, "narration_path": narr_web,
        "measured": narr_web is not None}


async def _revary_scene(sid: str) -> int:
    """Rewrite EXISTING shots' camera (description/visual/motion) for varied angles AND fix the
    location/refs — without touching narration text, timing or audio. The fast way to repair a
    scene (monotonous framing, wrong location, missing/extra entity refs) without re-TTS."""
    scene = await _scene_or_404(sid)
    project = await _project_or_404(scene["project_id"])
    shots = await db.query_all("SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))
    if not shots:
        return 0
    erows = await db.query_all(
        "SELECT id, name, type, description FROM entity WHERE project_id=?", (scene["project_id"],))
    by_name = {_norm(r["name"]): r for r in erows}
    scene_loc = _match_location_entity(scene["heading"], [r for r in erows if r["type"] == "location"])
    scene_loc_id = scene_loc["id"] if scene_loc else None
    # Retry the AI step until we get a usable list (covers agent errors, bad JSON AND a
    # valid-but-wrong-shape reply, which run_json's own retry doesn't catch).
    out = None
    for attempt in range(3):
        try:
            cand = await brain.run_json(brain.revary_shots_prompt(
                shots, erows, project["style"],
                location=(scene_loc["name"] if scene_loc else None)))
            if isinstance(cand, list) and cand:
                out = cand
                break
            logger.warning("revary scene %s attempt %d: AI trả về sai định dạng", sid, attempt)
        except Exception as ex:  # noqa: BLE001
            logger.warning("revary scene %s attempt %d failed: %s", sid, attempt, ex)
        await asyncio.sleep(1.0 + attempt)
    if not out:
        raise HTTPException(502, "AI không trả về danh sách góc máy (đã thử lại nhiều lần)")
    if not scene_loc_id:                       # heading matched no entity → keep AI's pick
        scene_loc_id = _first_location_id(out, by_name)
    mapped: dict[int, dict] = {}
    for pos, o in enumerate(out):
        if not isinstance(o, dict):
            continue
        k = o.get("idx")
        idx = int(k) if isinstance(k, (int, float)) or (isinstance(k, str) and k.isdigit()) else pos
        mapped[idx] = o
    n = 0
    for i, s in enumerate(shots):
        o = mapped.get(i)
        if not o:
            continue
        upd = {f: o[f] for f in ("description", "visual_prompt", "motion_prompt") if o.get(f)}
        if upd:
            # Re-resolve refs from the new prompt's {braces}: one location (the scene's) +
            # every non-location entity actually named.
            text = " ".join(filter(None, [o.get("description"), o.get("visual_prompt"), o.get("motion_prompt")]))
            upd["ref_entity_ids"] = json.dumps(_resolve_shot_refs(text, None, by_name, scene_loc_id))
            upd["updated_at"] = db.now()
            await db.update("shot", s["id"], upd)
            n += 1
    if scene_loc_id:
        await db.update("scene", sid, {"location_entity_id": scene_loc_id})
    return n


@router.post("/scenes/{sid}/revary-job")
async def revary_scene_job(sid: str):
    """Đa dạng góc máy cho 1 scene (giữ lời đọc/thời lượng) → job nền (§9)."""
    scene = await _scene_or_404(sid)

    async def _worker(_):
        await _revary_scene(sid)

    job = get_job_manager().start(
        project_id=scene["project_id"], type_="revary", items=[scene], worker=_worker,
        label=f"Góc máy: {scene.get('heading') or 'scene'}", throttle=(0, 0),
        item_label=lambda s: s.get("heading") or sid)
    return {"job_id": job.id, "total": 1}


@router.post("/projects/{pid}/revary")
async def revary_project(pid: str):
    """Đa dạng góc máy cho MỌI scene (chỉ viết lại mô tả/visual/motion, KHÔNG đụng audio/TTS)
    → job nền (§9). Nhanh hơn nhiều so với dựng lại shots; sau đó chỉ cần Auto gen lại ảnh."""
    await _project_or_404(pid)
    scenes = await db.query_all("SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))
    if not scenes:
        raise HTTPException(400, "Chưa có scene — tạo kịch bản (Script) trước.")

    async def _worker(sc):
        await _revary_scene(sc["id"])

    job = get_job_manager().start(
        project_id=pid, type_="revary", items=scenes, worker=_worker,
        label=f"Đa dạng góc máy ({len(scenes)} scene)", throttle=(0.3, 1.0),
        item_label=lambda sc: sc.get("heading") or sc["id"])
    return {"job_id": job.id, "total": len(scenes)}


@router.post("/scenes/{sid}/beats-job")
async def build_scene_beats_job(sid: str, body: BuildBeatsRequest):
    """Per-scene 'Dựng theo lời đọc' as a background job (§9): TTS is slow, so kick it off
    and report state in the banner instead of blocking the request (which made the UI look
    hung). One scene = one job step; shots are deleted + rebuilt when it completes."""
    scene = await _scene_or_404(sid)

    async def _worker(_):
        await build_scene_beats(sid, body)

    job = get_job_manager().start(
        project_id=scene["project_id"], type_="beats", items=[scene], worker=_worker,
        label=f"Lời đọc: {scene.get('heading') or 'scene'}", throttle=(0, 0),
        item_label=lambda s: s.get("heading") or sid)
    return {"job_id": job.id, "total": 1}


@router.post("/projects/{pid}/voiceover")
async def build_project_beats(pid: str, body: BuildBeatsRequest):
    """Storytelling (§2.6): per-scene whole-read TTS + beat mapping for EVERY scene → job
    nền (§9). Mỗi scene là 1 bước (xoá shot cũ + TTS + dựng beat) nên tiến độ hiện theo
    từng scene; sau cùng stitch project.voiceover_raw từ các narration. Trả job_id ngay —
    quá trình TTS rất chậm nên KHÔNG block request, tránh tưởng treo."""
    await _project_or_404(pid)
    scenes = await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))
    if not scenes:
        raise HTTPException(400, "Chưa có scene — tạo kịch bản (Script) trước.")

    async def _worker(sc):
        await build_scene_beats(sc["id"], body)

    async def _finalize():
        rows = await db.query_all(
            "SELECT narration_text FROM scene WHERE project_id=? ORDER BY idx", (pid,))
        vo = [s["narration_text"] for s in rows if s.get("narration_text")]
        await db.update("project", pid, {
            "voiceover_raw": "\n\n".join(vo), "storytelling": 1, "updated_at": db.now()})

    job = get_job_manager().start(
        project_id=pid, type_="beats", items=scenes, worker=_worker,
        label=f"Dựng lời đọc + beats ({len(scenes)} scene)",
        throttle=(0.5, 1.5),  # TTS itself is the slow part; keep inter-scene gap small
        item_label=lambda sc: sc.get("heading") or sc["id"],
        finalize=_finalize)
    return {"job_id": job.id, "total": len(scenes)}


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


class ReorderRequest(BaseModel):
    order: list[str]   # ids in the desired order


@router.post("/scenes/{sid}/shots/reorder")
async def reorder_shots(sid: str, body: ReorderRequest):
    """Đặt lại thứ tự shot trong scene theo danh sách id (idx = vị trí)."""
    await _scene_or_404(sid)
    ts = db.now()
    for i, shot_id in enumerate(body.order):
        await db.execute("UPDATE shot SET idx=?, updated_at=? WHERE id=? AND scene_id=?",
                         (i, ts, shot_id, sid))
    return {"shots": await db.query_all(
        "SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))}


@router.post("/projects/{pid}/scenes/reorder")
async def reorder_scenes(pid: str, body: ReorderRequest):
    """Đặt lại thứ tự scene trong dự án theo danh sách id (idx = vị trí)."""
    await _project_or_404(pid)
    for i, scene_id in enumerate(body.order):
        await db.execute("UPDATE scene SET idx=? WHERE id=? AND project_id=?",
                         (i, scene_id, pid))
    return {"scenes": await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))}


# ─── Project backup: export / import as .zip (§13#5) ──────────

@router.get("/projects/{pid}/export-zip")
async def export_project_zip(pid: str):
    """Đóng gói dự án (rows DB + media local) thành .zip để sao lưu / chuyển máy."""
    project = await _project_or_404(pid)
    scenes = await db.query_all("SELECT * FROM scene WHERE project_id=? ORDER BY idx", (pid,))
    shots: list[dict] = []
    for sc in scenes:
        shots += await db.query_all(
            "SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sc["id"],))
    entities = await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,))
    manifest = {"version": 1, "project": project, "scenes": scenes,
                "shots": shots, "entities": entities}

    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        media_dir = media_store.MEDIA_DIR / pid
        if media_dir.exists():
            for f in media_dir.iterdir():
                if f.is_file():
                    zf.write(f, f"media/{f.name}")
        bgm = (project.get("bgm_path") or "").strip()
        if bgm and Path(bgm).exists():
            zf.write(bgm, f"bgm/{Path(bgm).name}")

    safe = "".join(c if (c.isalnum() or c in "-_.") else "_"
                   for c in (project.get("title") or "project"))[:50] or "project"
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


@router.post("/projects/import-zip")
async def import_project_zip(file: UploadFile = File(...)):
    """Nhập dự án từ .zip đã export: tạo project MỚI (id mới), khôi phục media local +
    rows DB. Giữ flow_project_id cũ (có thể cần re-link Flow), media hiển thị từ file local."""
    import io
    import zipfile
    data = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(zf.read("manifest.json"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"File .zip không hợp lệ: {e}")

    old_proj = manifest.get("project") or {}
    old_pid = old_proj.get("id")
    if not old_pid:
        raise HTTPException(400, "manifest thiếu thông tin project")
    new_pid = db.new_id()
    ts = db.now()

    # khôi phục media → ./media/<new_pid>/
    dest = media_store.MEDIA_DIR / new_pid
    dest.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        if name.startswith("media/") and not name.endswith("/"):
            (dest / Path(name).name).write_bytes(zf.read(name))
    new_bgm = None
    for name in zf.namelist():
        if name.startswith("bgm/") and not name.endswith("/"):
            bdir = assembler.STUDIO_MEDIA_DIR / new_pid
            bdir.mkdir(parents=True, exist_ok=True)
            bp = bdir / Path(name).name
            bp.write_bytes(zf.read(name))
            new_bgm = str(bp)

    def remap(v):
        if isinstance(v, str) and f"/media/{old_pid}/" in v:
            return v.replace(f"/media/{old_pid}/", f"/media/{new_pid}/")
        return v

    # entities first (ref_entity_ids + location_entity_id reference these ids)
    ent_map: dict[str, str] = {}
    for e in manifest.get("entities", []):
        nid = db.new_id()
        ent_map[e["id"]] = nid
        row = {k: remap(v) for k, v in e.items()}
        row.update({"id": nid, "project_id": new_pid, "created_at": ts, "updated_at": ts})
        await db.insert("entity", row)

    proj = {k: remap(v) for k, v in old_proj.items()}
    proj.update({"id": new_pid, "bgm_path": new_bgm,
                 "title": (old_proj.get("title") or "Imported") + " (nhập)",
                 "created_at": ts, "updated_at": ts})
    await db.insert("project", proj)

    sc_map: dict[str, str] = {}
    for sc in manifest.get("scenes", []):
        nid = db.new_id()
        sc_map[sc["id"]] = nid
        row = {k: remap(v) for k, v in sc.items()}
        row.update({"id": nid, "project_id": new_pid})
        if row.get("location_entity_id"):
            row["location_entity_id"] = ent_map.get(row["location_entity_id"])
        await db.insert("scene", row)

    for sh in manifest.get("shots", []):
        nsid = sc_map.get(sh.get("scene_id"))
        if not nsid:
            continue
        row = {k: remap(v) for k, v in sh.items()}
        row.update({"id": db.new_id(), "scene_id": nsid})
        try:
            ids = json.loads(row.get("ref_entity_ids") or "[]")
            row["ref_entity_ids"] = json.dumps([ent_map.get(i, i) for i in ids])
        except (json.JSONDecodeError, TypeError):
            pass
        await db.insert("shot", row)

    return await db.query_one("SELECT * FROM project WHERE id=?", (new_pid,))


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
    scene = await _scene_or_404(sid)
    shots = await db.query_all("SELECT * FROM shot WHERE scene_id=? ORDER BY idx", (sid,))
    return _start_image_job(scene["project_id"], shots, force, "storyboard")


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
    return _start_image_job(pid, shots, force, "storyboard")


def _start_image_job(pid: str, shots: list[dict], force: bool, type_: str) -> dict:
    """Enqueue a background job that generates storyboard frame images (§9)."""
    todo = [s for s in shots if force or not s.get("image_path")]

    async def _worker(s):
        await _generate_frame_image(s)

    job = get_job_manager().start(
        project_id=pid, type_=type_, items=todo, worker=_worker,
        label=f"Sinh ảnh storyboard ({len(todo)})", throttle=(2, 6),
        item_label=lambda s: s.get("title") or s["id"])
    return {"job_id": job.id, "total": len(todo)}


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


CLIP_MAX_S = 8  # one Veo i2v clip ≈ 8s; longer beats are rendered as chained sub-clips


async def _render_i2v_clip(client, project: dict, shot_id: str,
                           start_media_id: str, prompt: str, name: str) -> dict:
    """Submit one i2v clip, poll, download to media/<pid>/<media_id>.mp4. Retries on
    block/transient. Returns {media_id, primary_media_id, workflow_id, web, local}."""
    tier = await _current_tier()
    last = ""
    for attempt in range(VIDEO_GEN_RETRIES):
        res = await client.generate_video(
            start_image_media_id=start_media_id, prompt=prompt,
            project_id=project["flow_project_id"], scene_id=shot_id,
            aspect_ratio=project["aspect_ratio"], user_paygate_tier=tier)
        if res.get("error"):
            last = str(res["error"])
        else:
            info = _extract_video_submit(res.get("data", res))
            if not info.get("media_id"):
                last = _image_block_reason(res.get("data", res)) or "Flow không trả media"
            else:
                op = {"operation": {"name": info["media_id"]}, "sceneId": shot_id}
                url = await _poll_video(client, op)
                if not url:
                    last = "video chưa xong trong thời gian chờ"
                else:
                    if info.get("workflow_id"):
                        try:
                            await client.change_display_name(
                                info["workflow_id"], project["flow_project_id"], name)
                        except Exception:
                            pass
                    web = await media_store.save_from_url(
                        info["media_id"], project["id"], "mp4", url)
                    if web:
                        return {**info, "web": web, "local": assembler._local(web)}
                    last = "tải video về lỗi"
        logger.warning("clip %s hỏng (lần %d/%d): %s",
                       shot_id[:6], attempt + 1, VIDEO_GEN_RETRIES, last)
        if attempt < VIDEO_GEN_RETRIES - 1:
            await asyncio.sleep(random.uniform(5, 10))
    raise HTTPException(502, f"Tạo clip thất bại sau {VIDEO_GEN_RETRIES} lần: {last}")


async def _chained_video(shot: dict, scene: dict, project: dict, client, n: int) -> dict:
    """Storytelling beat > one clip: render `n` chained i2v sub-clips (each starts on the
    previous clip's last frame, motion flows on) and concat them into the shot's video."""
    motion = shot.get("motion_prompt") or shot.get("visual_prompt") or shot.get("description") or ""
    motions = [motion]
    try:
        pp = await brain.run_json(brain.beat_parts_prompt(
            shot.get("beat_action") or motion, motion, n, CLIP_MAX_S))
        parts = pp.get("parts") if isinstance(pp, dict) else None
        if parts:
            motions = [p.get("motion_prompt") or motion
                       for p in sorted(parts, key=lambda x: x.get("part_idx", 0))]
    except Exception as ex:  # noqa: BLE001
        logger.warning("beat_parts failed: %s", ex)
    while len(motions) < n:
        motions.append(motion)

    out_dir = assembler.STUDIO_MEDIA_DIR / project["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    start_media = shot["image_media_id"]
    clips, first = [], None
    for k in range(n):
        name = f"s{scene['idx']+1:02d}_{shot['idx']+1:02d}_p{k+1}_vid"
        info = await _render_i2v_clip(client, project, shot["id"], start_media, motions[k], name)
        first = first or info
        clips.append(info["local"])
        if k < n - 1:  # chain: last frame of this clip → uploaded start image for the next
            frame = out_dir / f"chain_{shot['id']}_{k}.jpg"
            if await assembler.extract_last_frame(info["local"], frame):
                import base64
                up = await client.upload_image(
                    base64.b64encode(frame.read_bytes()).decode(), "image/jpeg",
                    project["flow_project_id"], frame.name)
                if up.get("_mediaId"):
                    start_media = up["_mediaId"]
                else:
                    logger.warning("upload_image cho chain thất bại — dùng lại frame gốc")

    final = out_dir / f"shot_{shot['id']}_chain.mp4"
    await assembler.concat_videos(clips, final)
    web = f"/studio-media/{project['id']}/{final.name}"
    await db.update("shot", shot["id"], {
        "video_media_id": first["media_id"], "video_primary_id": first.get("primary_media_id"),
        "video_workflow_id": first.get("workflow_id"), "video_path": web,
        "status": "done", "updated_at": db.now()})
    return await _shot_or_404(shot["id"])


async def _generate_shot_video(shot: dict) -> dict:
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    if not shot.get("image_media_id"):
        raise HTTPException(400, "Shot chưa có ảnh frame — tạo ảnh ở Storyboard trước")
    await db.update("shot", shot["id"], {"status": "running", "updated_at": db.now()})

    # Storytelling beat longer than one clip → chained sub-clips covering the beat.
    dur = float(shot.get("duration") or 0)
    n = max(1, math.ceil(dur / CLIP_MAX_S)) if dur > CLIP_MAX_S else 1
    try:
        if n > 1:
            return await _chained_video(shot, scene, project, client, n)
        motion = shot.get("motion_prompt") or shot.get("visual_prompt") or shot.get("description") or ""
        info = await _render_i2v_clip(
            client, project, shot["id"], shot["image_media_id"], motion,
            f"s{scene['idx']+1:02d}_{shot['idx']+1:02d}_vid")
        await db.update("shot", shot["id"], {
            "video_media_id": info["media_id"], "video_primary_id": info.get("primary_media_id"),
            "video_workflow_id": info.get("workflow_id"), "video_path": info["web"],
            "status": "done", "updated_at": db.now()})
        return await _shot_or_404(shot["id"])
    except HTTPException:
        await db.update("shot", shot["id"], {"status": "error", "updated_at": db.now()})
        raise


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
    """✦ Auto gen video cho shot CÓ ảnh, CHƯA có video → job nền (§9). Throttle 15–30s."""
    await _project_or_404(pid)
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? ORDER BY sc.idx, sh.idx", (pid,))
    todo = [s for s in shots if s.get("image_media_id") and (force or not s.get("video_path"))]

    async def _worker(s):
        await _generate_shot_video(s)

    job = get_job_manager().start(
        project_id=pid, type_="videos", items=todo, worker=_worker,
        label=f"Sinh video ({len(todo)})", throttle=(15, 30),
        item_label=lambda s: s.get("title") or s["id"])
    return {"job_id": job.id, "total": len(todo)}


# ─── Node Editor graphs ─────────────────────────────────────

class SaveGraphRequest(BaseModel):
    graph: dict
    only_node: str | None = None  # run just this node + upstream (per-node "tạo nhanh")


# A shot owns two separate graphs: the storyboard IMAGE graph (graph_json) and the
# shots-tab VIDEO graph (video_graph_json). `goal` selects which column to read/write.
def _shot_graph_col(goal: str | None) -> str:
    return "video_graph_json" if goal == "video" else "graph_json"


@router.get("/shots/{sid}/graph")
async def get_shot_graph(sid: str, goal: str | None = None):
    row = await _shot_or_404(sid)
    col = _shot_graph_col(goal)
    return {"graph": json.loads(row[col]) if row.get(col) else None}


@router.put("/shots/{sid}/graph")
async def put_shot_graph(sid: str, body: SaveGraphRequest, goal: str | None = None):
    await _shot_or_404(sid)
    col = _shot_graph_col(goal)
    await db.update("shot", sid, {col: json.dumps(body.graph), "updated_at": db.now()})
    return {"ok": True}


@router.post("/shots/{sid}/graph/run")
async def run_shot_graph(sid: str, body: SaveGraphRequest, goal: str | None = None):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    await db.update("shot", sid, {_shot_graph_col(goal): json.dumps(body.graph)})
    project = {**project, "paygate_tier": await _current_tier()}
    try:
        out = await graph_mod.run_graph(body.graph, shot, project, "shot",
                                        only_node=body.only_node)
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
        out = await graph_mod.run_graph(body.graph, entity, project, "entity",
                                        only_node=body.only_node)
    except graph_mod.GraphError as e:
        raise HTTPException(400, str(e))
    return {**out, "entity": await _entity_or_404(eid)}


class ApplyMediaRequest(BaseModel):
    media_id: str
    ext: str = "png"


async def _flow_workflow_name_for_media(flow_project_id: str, media_id: str) -> Optional[str]:
    """Workflow (theo primaryMediaId) chứa media_id → trả `name` để đổi tên hiển thị. Dùng
    cho media gán bằng node/candidate (apply-media) vốn không giữ workflow_id như auto-gen."""
    if not (flow_project_id and media_id):
        return None
    try:
        raw = await get_flow_client().get_project(flow_project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("rename: đọc project Flow lỗi: %s", e)
        return None
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    for w in (_deep_find(data, "workflows") or []):
        if isinstance(w, dict) and (w.get("metadata") or {}).get("primaryMediaId") == media_id:
            return w.get("name")
    return None


async def _rename_flow_media(project: dict, media_id: str, label: str) -> None:
    """Đổi tên hiển thị của media trên Flow (để dễ tìm khi chọn ảnh tham chiếu) — giống
    auto-gen. Bỏ qua nếu project chưa gắn Flow / không tìm thấy workflow tương ứng."""
    fpid = project.get("flow_project_id")
    if not (fpid and media_id and label):
        return
    name = await _flow_workflow_name_for_media(fpid, media_id)
    if not name:
        return
    try:
        await get_flow_client().change_display_name(name, fpid, label[:60])
    except Exception as e:  # noqa: BLE001
        logger.warning("đổi tên media trên Flow lỗi: %s", e)


# Commit a media (e.g. the result of a per-node "tạo nhanh") to a shot/entity so the
# storyboard / asset reflects it without a full graph run.
@router.post("/shots/{sid}/apply-media")
async def apply_shot_media(sid: str, body: ApplyMediaRequest):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    web = await media_store.ensure_local(body.media_id, project["id"], body.ext)
    col = "video" if body.ext == "mp4" else "image"
    await db.update("shot", sid, {
        f"{col}_media_id": body.media_id, f"{col}_primary_id": body.media_id,
        f"{col}_path": web, "updated_at": db.now()})
    await _record_media_history(project["id"], "shot", sid, col, body.media_id, body.media_id, web)
    # Đổi tên trên Flow giống auto-gen (s01_03_img / _vid) để dễ tìm khi tham chiếu.
    slot = "vid" if col == "video" else "img"
    await _rename_flow_media(project, body.media_id,
                             f"s{scene['idx']+1:02d}_{shot['idx']+1:02d}_{slot}")
    return {"ok": True, "path": web, "shot": await _shot_or_404(sid)}


@router.post("/entities/{eid}/apply-media")
async def apply_entity_media(eid: str, body: ApplyMediaRequest):
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    web = await media_store.ensure_local(body.media_id, project["id"], body.ext)
    await db.update("entity", eid, {
        "media_id": body.media_id, "primary_media_id": body.media_id,
        "image_path": web, "updated_at": db.now()})
    # A location's media is a 2x2 grid → overlay the position labels for display (same as
    # quick-gen), so node "tạo nhanh" and candidate-pick get labels too.
    if entity.get("type") == "location" and web:
        try:
            await _label_location_grid(await _entity_or_404(eid), project)
        except Exception as ex:  # noqa: BLE001
            logger.warning("location grid labelling (apply-media) failed for %s: %s", eid, ex)
    await _record_media_history(project["id"], "entity", eid, "image", body.media_id, body.media_id, web)
    # Đổi tên trên Flow giống auto-gen (type_tên) để dễ tìm khi tham chiếu.
    await _rename_flow_media(project, body.media_id, f"{entity['type']}_{entity['name']}")
    return {"ok": True, "path": web, "entity": await _entity_or_404(eid)}


@router.get("/shots/{sid}/history")
async def shot_history(sid: str, slot: str = "image"):
    """Lịch sử các phiên bản ảnh/video đã gán cho shot (mới nhất trước)."""
    await _shot_or_404(sid)
    return {"history": await db.query_all(
        "SELECT * FROM media_history WHERE owner_id=? AND slot=? ORDER BY created_at DESC",
        (sid, slot))}


@router.get("/entities/{eid}/history")
async def entity_history(eid: str):
    """Lịch sử các phiên bản ảnh đã gán cho entity (mới nhất trước)."""
    await _entity_or_404(eid)
    return {"history": await db.query_all(
        "SELECT * FROM media_history WHERE owner_id=? AND slot='image' ORDER BY created_at DESC",
        (eid,))}


@router.post("/shots/{sid}/history/{hid}/restore")
async def restore_shot_history(sid: str, hid: str):
    """Khôi phục một phiên bản cũ làm media hiện tại của shot."""
    await _shot_or_404(sid)
    h = await db.query_one("SELECT * FROM media_history WHERE id=?", (hid,))
    if not h or h.get("owner_id") != sid:
        raise HTTPException(404, "Không tìm thấy phiên bản")
    kind = h["slot"]
    await db.update("shot", sid, {
        f"{kind}_media_id": h["media_id"], f"{kind}_primary_id": h["primary_media_id"],
        f"{kind}_path": h["path"], "updated_at": db.now()})
    return await _shot_or_404(sid)


@router.post("/entities/{eid}/history/{hid}/restore")
async def restore_entity_history(eid: str, hid: str):
    """Khôi phục một phiên bản ảnh cũ làm ảnh hiện tại của entity."""
    await _entity_or_404(eid)
    h = await db.query_one("SELECT * FROM media_history WHERE id=?", (hid,))
    if not h or h.get("owner_id") != eid:
        raise HTTPException(404, "Không tìm thấy phiên bản")
    await db.update("entity", eid, {
        "media_id": h["media_id"], "primary_media_id": h["primary_media_id"],
        "image_path": h["path"], "updated_at": db.now()})
    return await _entity_or_404(eid)


class CandidatesRequest(BaseModel):
    n: int = 3   # số ảnh ứng viên (2–4)


@router.post("/entities/{eid}/candidates")
async def entity_candidates(eid: str, body: CandidatesRequest):
    """Sinh N ảnh ứng viên cho entity (không commit) → chọn bản đẹp rồi apply-media (§13#2)."""
    entity = await _entity_or_404(eid)
    project = await _project_or_404(entity["project_id"])
    client = _require_extension()
    body_text = brain.ref_image_prompt(
        entity["type"], entity["name"],
        entity.get("description") or entity.get("ref_prompt") or "")
    prompt = brain.compose_prompt(project, body_text)
    aspect = ("IMAGE_ASPECT_RATIO_LANDSCAPE" if entity["type"] in ("character", "prop", "location")
              else _to_image_aspect(project["aspect_ratio"]))
    model = await _resolve_image_model(project)
    tier = await _current_tier()
    cands = await _gen_candidates(
        lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, image_model=model),
        project, max(2, min(4, body.n)))
    return {"candidates": cands}


@router.post("/shots/{sid}/candidates")
async def shot_candidates(sid: str, body: CandidatesRequest):
    """Sinh N ảnh frame ứng viên cho shot (không commit) → chọn rồi apply-media (§13#2)."""
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    client = _require_extension()
    refs = await _build_frame_references(shot, scene)
    prompt = brain.compose_prompt(
        project, shot.get("description") or shot.get("title") or "", single_frame=True)
    aspect = _to_image_aspect(project["aspect_ratio"])
    model = await _resolve_image_model(project)
    tier = await _current_tier()
    cands = await _gen_candidates(
        lambda: client.generate_images(
            prompt=prompt, project_id=project["flow_project_id"], aspect_ratio=aspect,
            user_paygate_tier=tier, references=refs or None, image_model=model),
        project, max(2, min(4, body.n)))
    return {"candidates": cands}


# ─── Assemble / narration / export ──────────────────────────

class NarrationRequest(BaseModel):
    language: Optional[str] = None   # None → dùng script_lang của dự án
    text: Optional[str] = None     # nếu None → AI tự sinh


async def _tts_wav(text: str, voice_id: int, project_id: str, shot_id: str,
                   speed: float = 1.0) -> Optional[str]:
    """Normalize VN text → synthesize via OmniVoice (segmented + re-joined), save WAV."""
    chunks = await _tts_segments(vntext.normalize(text) or text, voice_id, speed)
    rel = f"{project_id}/narr_{shot_id}.wav"
    dest = media_store.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_concat_wav_bytes, chunks, dest)
    return f"/media/{rel}"


@router.post("/shots/{sid}/narration")
async def shot_narration(sid: str, body: NarrationRequest):
    shot = await _shot_or_404(sid)
    scene = await _scene_or_404(shot["scene_id"])
    project = await _project_or_404(scene["project_id"])
    text = body.text
    if not text:
        out = await brain.run_json(brain.narrator_prompt(
            shot.get("description") or shot.get("title") or "",
            body.language or project.get("script_lang") or "Vietnamese"))
        text = out.get("narrator_text", "")
    if not text:
        raise HTTPException(502, "Không sinh được narrator text")
    voice_id = project.get("voice_id") or 0
    web = await _tts_wav(text, voice_id, project["id"], sid,
                         float(project.get("tts_speed") or 1.0))
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
async def assemble_project_images(pid: str, ken_burns: bool = True,
                                  font: Optional[str] = None):
    """Ghép 1 video dài từ ẢNH các shot (theo scene), narration cả scene + caption từ khoá.
    `font` (hoặc setting `caption_font`) chọn font vẽ chữ; bỏ trống → tự dò theo OS."""
    await _project_or_404(pid)
    caption_font = font or (await db.kv_get_all()).get("caption_font") or None
    try:
        return await assembler.assemble_from_images(
            pid, ken_burns=ken_burns, caption_font=caption_font)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


_BGM_EXT = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav",
            "audio/x-wav": ".wav", "audio/aac": ".aac", "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a", "audio/ogg": ".ogg", "audio/flac": ".flac"}


@router.post("/projects/{pid}/bgm")
async def upload_bgm(pid: str, file: UploadFile = File(...),
                     volume: Optional[float] = Form(None)):
    """Tải nhạc nền cho dự án. Khi ghép video, nhạc sẽ tự được trộn dưới giọng đọc với
    `volume` (mặc định 0.18). Bỏ trống nhạc → không chèn gì."""
    await _project_or_404(pid)
    ext = _BGM_EXT.get((file.content_type or "").lower())
    if not ext:
        ext = os.path.splitext(file.filename or "")[1].lower() or ".mp3"
    out_dir = assembler.STUDIO_MEDIA_DIR / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    # one bgm per project — remove any previous file with a different extension
    for old in out_dir.glob("bgm.*"):
        old.unlink(missing_ok=True)
    dest = out_dir / f"bgm{ext}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    fields = {"bgm_path": str(dest), "updated_at": db.now()}
    if volume is not None:
        fields["bgm_volume"] = min(max(float(volume), 0.0), 1.0)
    await db.update("project", pid, fields)
    return await db.query_one("SELECT * FROM project WHERE id=?", (pid,))


@router.delete("/projects/{pid}/bgm")
async def clear_bgm(pid: str):
    """Gỡ nhạc nền khỏi dự án (video ghép sau sẽ không còn nhạc)."""
    p = await _project_or_404(pid)
    old = (p.get("bgm_path") or "").strip()
    if old:
        try:
            os.remove(old)
        except OSError:
            pass
    await db.update("project", pid, {"bgm_path": None, "updated_at": db.now()})
    return await db.query_one("SELECT * FROM project WHERE id=?", (pid,))


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
    meta = await brain.run_json(brain.seo_prompt(
        p["title"], p.get("script_raw") or "", p.get("script_lang") or "Vietnamese"))
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
async def thumb(media_key: str, pid: Optional[str] = None):
    """Trả thumbnail cho ảnh đại diện project/media.

    Ưu tiên file local đã tải (cache theo project) — chỉ gọi Flow khi máy chưa có ảnh.
    `pid` (project_id) giúp tìm đúng thư mục local trước."""
    path = await media_store.ensure_thumb(media_key, pid)
    if not path:
        raise HTTPException(404, "Không lấy được thumbnail (id sai hoặc chưa sẵn sàng)")
    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path, media_type=media_type)


@router.post("/media/ensure/{media_id}")
async def ensure_media(media_id: str, project_id: str, ext: str = "png"):
    """Đảm bảo file local tồn tại; trả web path."""
    web = await media_store.ensure_local(media_id, project_id, ext)
    if not web:
        raise HTTPException(404, "Không tải được media")
    return {"path": web}


def _media_abs(path: Optional[str]):
    """Web media path (/media/...) → file trên đĩa, hoặc None nếu rỗng/ngoài kho."""
    if not path or "/media/" not in path:
        return None
    return media_store.MEDIA_DIR / path.replace("/media/", "", 1)


@router.post("/projects/{pid}/sync-media")
async def sync_project_media(pid: str):
    """Đồng bộ ảnh/video với Flow (Flow là nguồn chuẩn).

    Media nào đã bị xoá trên Flow thì chắc chắn không còn → gỡ tham chiếu ở mục tương
    ứng (entity / shot ảnh / shot video / các view phụ / lịch sử media) và xoá file cache
    local. Một media coi là CÒN nếu media_id HOẶC primary_media_id của nó còn trên Flow
    (đối chiếu rộng để tránh xoá nhầm)."""
    project = await db.query_one("SELECT * FROM project WHERE id=?", (pid,))
    if not project:
        raise HTTPException(404, "Không tìm thấy project")
    flow_id = project.get("flow_project_id")
    if not flow_id:
        raise HTTPException(400, "Project chưa gắn với project trên Flow")

    client = _require_extension()
    raw = await client.get_project(flow_id)
    existing = _flow_existing_media_ids(raw)
    if not existing:
        # Không đọc được media nào → có thể lỗi tạm thời. Không xoá gì (tránh phá dữ liệu).
        raise HTTPException(502, "Không đọc được media từ Flow — thử lại, chưa xoá gì.")

    def present(*ids) -> bool:
        return any(i in existing for i in ids if i)

    def rm_file(path: Optional[str]) -> None:
        f = _media_abs(path)
        if f and f.is_file():
            try:
                f.unlink()
            except OSError:
                pass

    removed: dict = {"entities": [], "shot_images": [], "shot_videos": [],
                     "extra_views": 0, "history": 0}

    # Entities (ảnh chính + các view phụ trong extra_media)
    for e in await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,)):
        upd: dict = {}
        if (e.get("media_id") or e.get("primary_media_id")) and \
                not present(e.get("media_id"), e.get("primary_media_id")):
            rm_file(e.get("image_path"))
            upd.update(media_id=None, primary_media_id=None, image_path=None, image_url=None)
            removed["entities"].append(e.get("name") or e["id"])
        if e.get("extra_media"):
            try:
                views = json.loads(e["extra_media"]) or []
            except (json.JSONDecodeError, TypeError):
                views = []
            kept = [v for v in views if present(v.get("media_id"), v.get("primary_media_id"))]
            for v in views:
                if v not in kept:
                    rm_file(v.get("path"))
                    removed["extra_views"] += 1
            if len(kept) != len(views):
                upd["extra_media"] = json.dumps(kept) if kept else None
        if upd:
            upd["updated_at"] = db.now()
            await db.update("entity", e["id"], upd)

    # Shots (ảnh + video) trong mọi scene của project
    scenes = await db.query_all("SELECT id FROM scene WHERE project_id=?", (pid,))
    for sc in scenes:
        for sh in await db.query_all("SELECT * FROM shot WHERE scene_id=?", (sc["id"],)):
            upd = {}
            if (sh.get("image_media_id") or sh.get("image_primary_id")) and \
                    not present(sh.get("image_media_id"), sh.get("image_primary_id")):
                rm_file(sh.get("image_path"))
                upd.update(image_media_id=None, image_primary_id=None,
                           image_workflow_id=None, image_path=None)
                removed["shot_images"].append(sh.get("title") or sh["id"])
            if (sh.get("video_media_id") or sh.get("video_primary_id")) and \
                    not present(sh.get("video_media_id"), sh.get("video_primary_id")):
                rm_file(sh.get("video_path"))
                upd.update(video_media_id=None, video_primary_id=None,
                           video_workflow_id=None, video_path=None)
                removed["shot_videos"].append(sh.get("title") or sh["id"])
            if upd:
                upd["updated_at"] = db.now()
                await db.update("shot", sh["id"], upd)

    # Lịch sử media — bỏ các bản đã biến mất khỏi Flow
    for h in await db.query_all("SELECT * FROM media_history WHERE project_id=?", (pid,)):
        if not present(h.get("media_id"), h.get("primary_media_id")):
            rm_file(h.get("path"))
            await db.delete("media_history", h["id"])
            removed["history"] += 1

    total = (len(removed["entities"]) + len(removed["shot_images"]) +
             len(removed["shot_videos"]) + removed["extra_views"] + removed["history"])
    return {"flow_media": len(existing), "removed": removed, "total_removed": total}


# ─── Jobs: realtime batch progress (§9) ─────────────────────

@router.get("/jobs")
async def list_jobs(project_id: Optional[str] = None):
    """Các job đang/ vừa chạy (trong bộ nhớ). Dùng để dựng lại trạng thái khi mở tab."""
    return {"jobs": get_job_manager().active(project_id)}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Dừng một batch đang chạy (sau item hiện tại)."""
    ok = get_job_manager().cancel(job_id)
    if not ok:
        raise HTTPException(404, "Job không tồn tại hoặc đã kết thúc")
    return {"ok": True}


@router.websocket("/ws")
async def jobs_ws(ws: WebSocket):
    """Kênh realtime: server đẩy {type:'job', job:{…}} mỗi khi job thay đổi.

    Khi vừa kết nối, gửi snapshot toàn bộ job hiện có để client dựng lại banner.
    """
    await ws.accept()
    mgr = get_job_manager()
    mgr.subscribe(ws)
    try:
        await ws.send_json({"type": "snapshot", "jobs": mgr.active()})
        while True:
            # Không cần dữ liệu từ client; giữ kết nối mở (và phát hiện ngắt).
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        mgr.unsubscribe(ws)
