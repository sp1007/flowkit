"""TTS proxy endpoints — relay tới server OmniVoice chạy trên Google Colab.

Server OmniVoice (FastAPI trên Colab) expose: /api/health, /api/tts,
/api/voices/list, /api/voices/add, /api/voices/remove. URL public của Colab
(ngrok/localtunnel) đổi mỗi phiên, nên base URL cấu hình được lúc chạy qua
PUT /api/tts/config (hoặc env OMNIVOICE_BASE_URL).
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.config import OMNIVOICE_BASE_URL, OMNIVOICE_TTS_TIMEOUT
from agent.studio import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tts", tags=["tts"])

# Base URL của Colab đổi mỗi phiên — lưu vào kv (db) để giữ qua restart, fallback env.
_TTS_KEY = "tts_base_url"
_state = {"base_url": OMNIVOICE_BASE_URL.rstrip("/")}
_loaded = False


async def _ensure_loaded() -> None:
    """Nạp base_url đã lưu từ db (1 lần). Giá trị người dùng đặt qua UI thắng env."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        saved = await db.kv_get(_TTS_KEY)
        if saved:
            _state["base_url"] = str(saved).rstrip("/")
    except Exception as e:  # noqa: BLE001 — db chưa sẵn sàng thì giữ giá trị env
        logger.warning("load tts base_url failed: %s", e)


# ─── Models ──────────────────────────────────────────────────

class ConfigRequest(BaseModel):
    base_url: str


class TTSRequest(BaseModel):
    text: str
    voice_id: int = 0
    voice: Optional[str] = None      # base64 WAV/MP3 cho dynamic cloning
    speed: float = 1.0
    instruct: Optional[str] = None


class AddVoiceRequest(BaseModel):
    voice: str                       # base64 WAV/MP3
    title: str
    desciption: Optional[str] = None  # giữ đúng tên field của OmniVoice


class RemoveVoiceRequest(BaseModel):
    voice_id: int


# ─── Proxy helper ────────────────────────────────────────────

async def _proxy(method: str, path: str, *, json: dict | None = None,
                 timeout: float = 30.0) -> dict:
    """Gọi server OmniVoice và trả JSON. Lỗi mạng → 503; lỗi HTTP → passthrough."""
    await _ensure_loaded()
    base = _state["base_url"]
    if not base:
        raise HTTPException(503, "OMNIVOICE_BASE_URL chưa được cấu hình (PUT /api/tts/config)")
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=json)
    except httpx.RequestError as e:
        logger.warning("OmniVoice unreachable at %s: %s", url, e)
        raise HTTPException(503, f"Không kết nối được OmniVoice tại {base}: {e}")
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    try:
        return resp.json()
    except Exception:
        raise HTTPException(502, "OmniVoice trả về phản hồi không phải JSON")


# ─── Config ──────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    """Xem base URL OmniVoice hiện tại."""
    await _ensure_loaded()
    return {"base_url": _state["base_url"]}


@router.put("/config")
async def set_config(body: ConfigRequest):
    """Đặt base URL OmniVoice (dán URL ngrok/localtunnel từ Colab vào đây). Lưu bền vào db."""
    _state["base_url"] = body.base_url.rstrip("/")
    global _loaded
    _loaded = True
    await db.kv_set(_TTS_KEY, _state["base_url"])
    logger.info("OmniVoice base_url set to %s", _state["base_url"])
    return {"base_url": _state["base_url"]}


# ─── Proxied OmniVoice endpoints ─────────────────────────────

@router.get("/health")
async def health():
    """Kiểm tra server OmniVoice + trạng thái nạp model."""
    return await _proxy("GET", "/api/health", timeout=10.0)


@router.post("/synthesize")
async def synthesize(body: TTSRequest):
    """Tổng hợp giọng từ text. Trả {audio: base64 WAV, status, msg}."""
    return await _proxy("POST", "/api/tts", json=body.model_dump(),
                        timeout=OMNIVOICE_TTS_TIMEOUT)


@router.get("/voices")
async def list_voices():
    """Liệt kê các giọng custom đã đăng ký."""
    return await _proxy("GET", "/api/voices/list", timeout=30.0)


@router.post("/voices")
async def add_voice(body: AddVoiceRequest):
    """Đăng ký một giọng clone mới (base64 WAV/MP3)."""
    return await _proxy("POST", "/api/voices/add", json=body.model_dump(),
                        timeout=120.0)


@router.post("/voices/remove")
async def remove_voice(body: RemoveVoiceRequest):
    """Xóa một giọng custom theo voice_id."""
    return await _proxy("POST", "/api/voices/remove", json=body.model_dump(),
                        timeout=30.0)
