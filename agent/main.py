"""Flow Kit — FastAPI + WebSocket server entry point (Flow proxy only)."""
import asyncio
import json
import logging
import secrets as _secrets
from contextlib import asynccontextmanager

import os
from pathlib import Path

import websockets
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.config import API_HOST, API_PORT, WS_HOST, WS_PORT
from agent.api.flow import router as flow_router
from agent.api.tts import router as tts_router
from agent.api.ai_agent import router as agent_router
from agent.api.studio import router as studio_router
from agent.services.flow_client import get_flow_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_CALLBACK_SECRET = _secrets.token_urlsafe(32)


# ─── WebSocket Server for Extension ─────────────────────────

async def ws_handler(websocket):
    """Handle a Chrome extension WebSocket connection."""
    client = get_flow_client()
    client.set_extension(websocket)
    logger.info("Extension connected from %s", websocket.remote_address)

    # Send callback secret so extension can authenticate HTTP callbacks
    await websocket.send(json.dumps({"type": "callback_secret", "secret": _CALLBACK_SECRET}))

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
                await client.handle_message(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from extension")
            except Exception as e:
                logger.exception("Error handling extension message: %s", e)
    except websockets.ConnectionClosed:
        pass
    finally:
        client.clear_extension()
        logger.info("Extension disconnected")


async def run_ws_server():
    """Run WebSocket server for extension connections."""
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        logger.info("WebSocket server listening on ws://%s:%d", WS_HOST, WS_PORT)
        await asyncio.Future()  # run forever


# ─── FastAPI App ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Flow Kit starting on %s:%d", API_HOST, API_PORT)
    ws_task = asyncio.create_task(run_ws_server())
    logger.info("WS server started")

    yield

    ws_task.cancel()
    logger.info("Flow Kit stopped")


app = FastAPI(title="Flow Kit", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(flow_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(studio_router, prefix="/api")


@app.post("/api/ext/callback")
async def ext_callback(request: Request):
    """HTTP callback for extension to deliver API responses.

    Replaces ws.send() for response delivery — immune to WS disconnect.
    Extension POSTs {id, status, data, error} here instead of sending via WS.
    """
    data = await request.json()
    client = get_flow_client()
    req_id = data.get("id")
    logger.info("ext/callback: id=%s pending=%d match=%s",
                str(req_id)[:8] if req_id else "none",
                len(client._pending),
                "yes" if req_id and req_id in client._pending else "no")
    if req_id and req_id in client._pending:
        future = client._pending[req_id]
        try:
            future.set_result(data)
        except asyncio.InvalidStateError:
            pass
        return {"ok": True}
    return {"ok": False, "reason": "no matching pending request"}


@app.get("/health")
async def health():
    client = get_flow_client()
    return {
        "status": "ok",
        "version": "0.2.0",
        "extension_connected": client.connected,
        "ws": client.ws_stats,
    }


# ─── Static: local media cache + built SPA (mount last) ─────
_REPO_ROOT = Path(__file__).parent.parent
_MEDIA_DIR = Path(os.environ.get("STUDIO_MEDIA_DIR", _REPO_ROOT / "media"))
_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_MEDIA_DIR)), name="media")

_STUDIO_OUT = Path(os.environ.get("STUDIO_OUT_DIR", _REPO_ROOT / "studio_media"))
_STUDIO_OUT.mkdir(parents=True, exist_ok=True)
app.mount("/studio-media", StaticFiles(directory=str(_STUDIO_OUT)), name="studio-media")

_SPA_DIST = _REPO_ROOT / "webapp" / "dist"
if _SPA_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_SPA_DIST), html=True), name="spa")
else:
    logger.info("SPA dist not built yet (%s) — run `npm run build` in webapp/", _SPA_DIST)


if __name__ == "__main__":
    import os
    import uvicorn
    reload_enabled = os.environ.get("GLA_RELOAD", "0") == "1"
    uvicorn.run(
        "agent.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=reload_enabled,
    )
