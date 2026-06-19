"""Local media cache for Flow Studio.

Resolves a Flow media id → signed URL (via the extension) and downloads the bytes
to ./media so the web app can display from local files instead of hitting Flow on
every render. Includes the URL-fetch throttle from video-app.md §5.2 (rest after a
burst of lookups) since /media/{id} is itself rate-limited.
"""
import asyncio
import logging
import os
import random
from pathlib import Path

import httpx

from agent.config import BASE_DIR
from agent.services.flow_client import get_flow_client

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.environ.get("STUDIO_MEDIA_DIR", BASE_DIR / "media"))
THUMB_DIR = MEDIA_DIR / "_thumbs"

# URL-fetch throttle: Flow's /media/{id} is rate-limited, so resolving signed URLs is
# (a) capped at a few concurrent calls — a gallery firing dozens of <img> at once must
# not translate into dozens of simultaneous Flow hits — and (b) rested after each burst.
_URL_BURST = 6
_URL_CONCURRENCY = 3
_url_lock = asyncio.Lock()
_url_sem = asyncio.Semaphore(_URL_CONCURRENCY)
_url_count = 0


async def _throttle_url_fetch() -> None:
    global _url_count
    async with _url_lock:
        _url_count += 1
        if _url_count % _URL_BURST == 0:
            await asyncio.sleep(random.uniform(2, 6))


async def resolve_url(media_id: str) -> str | None:
    """media_id → fresh signed URL (None if invalid/not ready). Concurrency-limited +
    throttled to avoid tripping Flow's media rate limit on bursty galleries."""
    client = get_flow_client()
    if not client.connected:
        return None
    async with _url_sem:
        await _throttle_url_fetch()
        result = await client.get_direct_media(media_id)
    data = result.get("data", result) if isinstance(result, dict) else {}
    if isinstance(data, dict) and data.get("redirected"):
        return data.get("url")
    return None


async def _download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            resp = await c.get(url)
        if resp.status_code >= 400:
            logger.warning("media download %s → %s", url[:60], resp.status_code)
            return False
        dest.write_bytes(resp.content)
        return True
    except httpx.RequestError as e:
        logger.warning("media download failed: %s", e)
        return False


async def ensure_local(media_id: str, project_id: str, ext: str = "png") -> str | None:
    """Ensure ./media/<project_id>/<media_id>.<ext> exists; return web path or None."""
    rel = Path(project_id) / f"{media_id}.{ext}"
    dest = MEDIA_DIR / rel
    if dest.exists() and dest.stat().st_size > 0:
        return f"/media/{rel.as_posix()}"
    url = await resolve_url(media_id)
    if not url:
        return None
    if await _download(url, dest):
        return f"/media/{rel.as_posix()}"
    return None


async def save_from_url(media_id: str, project_id: str, ext: str, url: str) -> str | None:
    """Download a known URL (e.g. video fifeUrl from poll) to the local cache."""
    rel = Path(project_id) / f"{media_id}.{ext}"
    dest = MEDIA_DIR / rel
    if dest.exists() and dest.stat().st_size > 0:
        return f"/media/{rel.as_posix()}"
    if await _download(url, dest):
        return f"/media/{rel.as_posix()}"
    return None


async def ensure_thumb(media_key: str) -> Path | None:
    """Ensure a cached thumbnail for a Flow media key; return local file path."""
    dest = THUMB_DIR / f"{media_key}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = await resolve_url(media_key)
    if not url:
        return None
    if await _download(url, dest):
        return dest
    return None
