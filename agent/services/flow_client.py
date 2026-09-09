"""
Flow Client — communicates with Google Flow API via Chrome extension WebSocket bridge.

Agent runs a WS server. Extension connects as client. Agent sends API requests,
extension executes them in browser context (residential IP, cookies, reCAPTCHA).
"""
import asyncio
import json
import os
import logging
import re
import time
import uuid
from typing import Optional
from urllib.parse import quote

from agent.config import (
    GOOGLE_FLOW_API, GOOGLE_API_KEY, ENDPOINTS,
    VIDEO_MODELS, UPSCALE_MODELS, IMAGE_MODELS, VIDEO_POLL_TIMEOUT,
    OMNI_FLASH_MODELS, OMNI_FLASH_T2V_MODELS, OMNI_FLASH_VALID_ASPECTS,
    UPSAMPLE_IMAGE_RESOLUTIONS, UPSAMPLE_IMAGE_DEFAULT, UPSAMPLE_IMAGE_TIMEOUT,
    UPSAMPLE_VIDEO_RESOLUTIONS, UPSAMPLE_VIDEO_DEFAULT,
    VEO_LITE_MODELS, VEO_LITE_TIERS, VEO_LITE_DEFAULT_S, VEO_LITE_FRAME_MODELS,
)
from agent.services.headers import random_headers

logger = logging.getLogger(__name__)

# Nhật ký recon batchexecute — mỗi dòng một JSON, ghi tiếp chứ không đè.
BOQ_LOG_PATH = os.environ.get("FLOWKIT_BOQ_LOG", "boq_calls.jsonl")


class FlowClient:
    """Sends commands to Chrome extension via WebSocket."""

    def __init__(self):
        self._extension_ws = None  # Set by WS server when extension connects
        self._pending: dict[str, asyncio.Future] = {}
        self._flow_key: Optional[str] = None
        # Tài khoản Google đang đăng nhập Flow trong Chrome ({email, name, picture, sub}).
        # Extension đẩy lên khi kết nối / đổi account; agent không tự suy ra được.
        self._identity: Optional[dict] = None
        # Single-flight queue (video-app.md §9.1): the extension is ONE shared WS channel,
        # so every mutating Flow command (generate / edit / upscale / upload / rename /
        # get-url) is serialized through this lock — only one is in flight at a time. This
        # stops a batch and a manual op (⚡ quick-gen, Node Editor) from interleaving requests
        # and corrupting rate-limit/captcha state. Read-only polls (check-status, credits) opt
        # out (serialize=False) so they don't block submits — they run on their own cadence.
        self._flow_lock = asyncio.Lock()
        # Recon batchexecute: rpcid mà giao diện mới vừa gọi (xem handle_message).
        self.boq_calls: list[dict] = []
        # WS stats
        self._ws_connect_count = 0
        self._ws_disconnect_count = 0
        self._ws_connected_at: Optional[float] = None
        self._ws_last_disconnect_at: Optional[float] = None

    def set_extension(self, ws):
        """Called when extension connects via WS."""
        self._extension_ws = ws
        self._ws_connect_count += 1
        self._ws_connected_at = time.time()
        logger.info("Extension connected #%d (waiting for extension_ready/token_captured to sync)", self._ws_connect_count)

    def clear_extension(self):
        """Called when extension disconnects."""
        self._extension_ws = None
        # Identity KHÔNG bị xoá: extension rớt không có nghĩa người dùng đăng xuất, và xoá đi
        # sẽ làm mọi dự án "mất chủ" trong lúc mất kết nối. Extension gửi lại khi nối lại.
        self._ws_disconnect_count += 1
        self._ws_last_disconnect_at = time.time()
        # Cancel all pending futures (copy to avoid RuntimeError on concurrent modification)
        pending_copy = list(self._pending.items())
        count = len(pending_copy)
        for req_id, future in pending_copy:
            if not future.done():
                future.set_exception(ConnectionError("Extension disconnected"))
        self._pending.clear()
        logger.warning("Extension disconnected, cleared %d pending requests", count)

    def set_flow_key(self, key: str):
        self._flow_key = key

    @property
    def connected(self) -> bool:
        return self._extension_ws is not None

    @property
    def identity(self) -> Optional[dict]:
        """Tài khoản Flow đang đăng nhập, hoặc None khi chưa xác định được."""
        return self._identity

    async def fetch_identity(self, refresh: bool = True) -> Optional[dict]:
        """Hỏi extension tài khoản đang đăng nhập (refresh=False → lấy bản extension đang giữ).

        Chỉ dùng khi cần chắc chắn mới nhất (ví dụ ngay trước khi tạo dự án). Luồng thường
        đã có extension tự đẩy `identity` lúc kết nối và mỗi 45 phút."""
        if not self._extension_ws:
            return self._identity
        res = await self._send("get_identity", {"refresh": refresh}, timeout=20, serialize=False)
        data = res.get("result") if isinstance(res, dict) else None
        if isinstance(data, dict) and (data.get("email") or data.get("sub")):
            self._set_identity(data)
        return self._identity

    def _set_identity(self, data: dict) -> None:
        prev = (self._identity or {}).get("email")
        self._identity = data
        if data.get("email") != prev:
            logger.info("Tài khoản Flow: %s", data.get("email") or data.get("sub"))

    @property
    def ws_stats(self) -> dict:
        uptime = None
        if self._ws_connected_at and self.connected:
            uptime = int(time.time() - self._ws_connected_at)
        return {
            "connected": self.connected,
            "connects": self._ws_connect_count,
            "disconnects": self._ws_disconnect_count,
            "uptime_s": uptime,
        }

    async def handle_message(self, data: dict):
        """Handle incoming message from extension."""
        if data.get("type") == "token_captured":
            self._flow_key = data.get("flowKey")
            logger.info("Flow key captured from extension")
            return

        if data.get("type") == "identity":
            ident = data.get("identity") or {}
            if ident.get("email") or ident.get("sub"):
                self._set_identity(ident)
            return

        if data.get("type") == "boq_call":
            # Recon: giao diện mới gọi rpcid nào. Giữ trong bộ nhớ + ghi ra file để đọc lại
            # sau khi agent tắt — đây là nguồn DUY NHẤT cho biết rpcid nào làm việc gì.
            entry = data.get("entry") or {}
            self.boq_calls.append(entry)
            del self.boq_calls[:-500]
            try:
                with open(BOQ_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
            return

        if data.get("type") == "extension_ready":
            logger.info("Extension ready, flowKey=%s", "yes" if data.get("flowKeyPresent") else "no")
            return

        if data.get("type") == "pong":
            return

        if data.get("type") == "ping":
            # Respond to keepalive
            if self._extension_ws:
                await self._extension_ws.send(json.dumps({"type": "pong"}))
            return

        # Response to a pending request
        req_id = data.get("id")
        if req_id and req_id in self._pending:
            if not self._pending[req_id].done():
                self._pending[req_id].set_result(data)
            return


    async def _send(self, method: str, params: dict, timeout: float = 300,
                    *, serialize: bool = True) -> dict:
        """Send request to extension and wait for response.

        Always returns a dict. On error, returns {"error": "<reason>"} — callers
        must check result.get("error") or use _is_ws_error() before reading data.
        Never raises; exceptions are caught and returned as error dicts.

        `serialize=True` (default) routes the call through the single-flight lock so it
        does not overlap another Flow command. Read-only polls pass `serialize=False`.
        """
        if not self._extension_ws:
            return {"error": "Extension not connected"}
        if serialize:
            async with self._flow_lock:
                return await self._send_raw(method, params, timeout)
        return await self._send_raw(method, params, timeout)

    async def _send_raw(self, method: str, params: dict, timeout: float) -> dict:
        """Actual send + await of one extension request (no serialization)."""
        if not self._extension_ws:
            return {"error": "Extension not connected"}

        req_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._extension_ws.send(json.dumps({
                "id": req_id,
                "method": method,
                "params": params,
            }))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return {"error": f"Timeout ({timeout}s) waiting for {method}"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._pending.pop(req_id, None)



    # ─── High-level API Methods ──────────────────────────────








    async def boq_request(self, rpcid: str, args=None, source_path: str | None = None,
                          captcha_action: str = "IMAGE_GENERATION", timeout: float = 120) -> dict:
        """Gọi một RPC của giao diện mới qua batchexecute (cookie phiên, không cần token).

        Đường dự phòng cho ngày labs.google tắt — xem docs/new-flow-stack.md. Cần một tab
        flow.google.com/project/* đang mở vì `at`/`f.sid`/`bl` nằm trong WIZ_global_data.
        """
        return await self._send("boq_request", {
            "rpcid": rpcid,
            "args": args,
            "source_path": source_path,
            "captcha_action": captcha_action,
        }, timeout=timeout)

    async def probe_tabs(self) -> dict:
        """Tab Flow nào đang mở, tab nào có grecaptcha."""
        return await self._send("probe_tabs", {}, timeout=60, serialize=False)

    async def boq_log(self, limit: int = 100) -> dict:
        """Danh sách rpcid mà giao diện thật vừa gọi (recon)."""
        return await self._send("boq_log", {"limit": limit}, timeout=30, serialize=False)






















_REF_TOKEN_RE = re.compile(r"\{([^{}]+)\}")
_ALIAS_RE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")








# Singleton
_client: Optional[FlowClient] = None


class _BoqRouted:
    """Bọc FlowClient: mọi thao tác media đi đường batchexecute.

    `FlowClient` nay chỉ còn ống dẫn WebSocket và `boq_request`; toàn bộ thân các method
    sinh media đã bị xoá (~900 dòng) chứ không chỉ bị vòng qua. Đường cũ gọi
    `aisandbox-pa` bằng `Authorization: Bearer ya29.*`, mà token ấy do app Next.js ở
    labs.google phát và giao diện mới không phát nữa — giữ lại chỉ là một đường chết mà
    ai đó sẽ tưởng còn dùng được.

    Mọi thứ khác (`connected`, `set_extension`, `handle_message`, `boq_request`…) rơi
    thẳng xuống client thật qua `__getattr__`, nên tầng WebSocket và các endpoint /v2
    không biết có lớp này.
    """

    def __init__(self, real: "FlowClient"):
        self._real = real
        self._compat = None

    def __getattr__(self, name):
        from .boq_compat import BoqCompat
        if self._compat is None:
            self._compat = BoqCompat(self._real)
        # Có ở lớp tương thích thì dùng nó; còn lại là ống dẫn, rơi xuống client thật.
        if hasattr(self._compat, name):
            return getattr(self._compat, name)
        return getattr(self._real, name)


def get_flow_client():
    global _client
    if _client is None:
        _client = _BoqRouted(FlowClient())
    return _client
