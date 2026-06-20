"""SQLite store for Flow Studio (stdlib sqlite3, accessed via asyncio.to_thread).

One module-level connection (check_same_thread=False) guarded by a lock. The full
schema from video-app.md §4 is created up-front so later phases need no migration.
"""
import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from agent.config import BASE_DIR

DB_PATH = Path(os.environ.get("STUDIO_DB", BASE_DIR / "agent" / "studio.db"))

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  id TEXT PRIMARY KEY, title TEXT, flow_project_id TEXT,
  style TEXT DEFAULT 'Realistic',
  aspect_ratio TEXT DEFAULT 'VIDEO_ASPECT_RATIO_LANDSCAPE',
  paygate_tier TEXT DEFAULT 'PAYGATE_TIER_ONE',
  image_model TEXT, video_model TEXT,
  voice_id INTEGER, agent TEXT,
  idea TEXT, target_duration INTEGER, shot_duration INTEGER DEFAULT 8,
  storytelling INTEGER DEFAULT 0,
  voiceover_raw TEXT, script_raw TEXT,
  prompt_header TEXT, prompt_footer TEXT, culture_hint TEXT,
  thumb_media_key TEXT,
  status TEXT DEFAULT 'draft',
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS entity (
  id TEXT PRIMARY KEY, project_id TEXT, type TEXT,
  name TEXT, description TEXT, ref_prompt TEXT,
  media_id TEXT, primary_media_id TEXT, workflow_id TEXT,
  image_path TEXT, image_url TEXT, graph_json TEXT,
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS scene (
  id TEXT PRIMARY KEY, project_id TEXT, idx INTEGER,
  heading TEXT, slug TEXT, action TEXT, dialog TEXT,
  location_entity_id TEXT, source_segment TEXT,
  source_start INTEGER, source_end INTEGER,
  created_at REAL
);

CREATE TABLE IF NOT EXISTS shot (
  id TEXT PRIMARY KEY, scene_id TEXT, idx INTEGER, title TEXT,
  beat_id TEXT, part_idx INTEGER DEFAULT 0, is_chained INTEGER DEFAULT 0,
  description TEXT, ref_entity_ids TEXT,
  image_media_id TEXT, image_primary_id TEXT, image_workflow_id TEXT, image_path TEXT,
  visual_prompt TEXT, motion_prompt TEXT, beat_action TEXT,
  video_model TEXT, duration INTEGER DEFAULT 8,
  video_media_id TEXT, video_primary_id TEXT, video_workflow_id TEXT, video_path TEXT,
  upscale_path TEXT, upscale_url TEXT, operation_json TEXT, graph_json TEXT,
  narrator_text TEXT, narration_path TEXT, narration_duration REAL, start_time REAL,
  status TEXT DEFAULT 'pending', created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS job (
  id TEXT PRIMARY KEY, project_id TEXT, type TEXT, target_id TEXT,
  status TEXT, progress REAL, message TEXT, error TEXT,
  created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS asset (
  id TEXT PRIMARY KEY, project_id TEXT, kind TEXT,
  path TEXT, meta_json TEXT, created_at REAL
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_entity_project ON entity(project_id);
CREATE INDEX IF NOT EXISTS idx_scene_project ON scene(project_id);
CREATE INDEX IF NOT EXISTS idx_shot_scene ON shot(scene_id);
"""

# Columns added after the initial schema shipped — ALTER on existing DBs (idempotent).
_MIGRATIONS = [
    ("project", "prompt_header", "TEXT"),
    ("project", "prompt_footer", "TEXT"),
    ("project", "culture_hint", "TEXT"),
    # A shot has two independent node graphs: graph_json = the storyboard IMAGE graph,
    # video_graph_json = the shots-tab VIDEO graph. They must not share storage.
    ("shot", "video_graph_json", "TEXT"),
    # Storytelling (§2.6, audio-first): ONE continuous TTS per scene (kept whole so the
    # narration keeps its emotional flow); beats are timing windows over it.
    ("scene", "narration_text", "TEXT"),
    ("scene", "narration_path", "TEXT"),
    ("scene", "narration_duration", "REAL"),
    # Timed keyword captions burned on the video / exported to DaVinci (JSON list of
    # {text, start, end} in scene-local seconds).
    ("shot", "captions", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, decl in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
        _migrate(_conn)
    return _conn


def now() -> float:
    return time.time()


def new_id() -> str:
    return str(uuid.uuid4())


# ─── Sync primitives (run inside to_thread) ─────────────────

def _query_all(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = _get_conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def _query_one(sql: str, params: tuple = ()) -> dict | None:
    with _lock:
        cur = _get_conn().execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def _execute(sql: str, params: tuple = ()) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(sql, params)
        conn.commit()


# ─── Async wrappers ─────────────────────────────────────────

async def query_all(sql: str, params: tuple = ()) -> list[dict]:
    return await asyncio.to_thread(_query_all, sql, params)


async def query_one(sql: str, params: tuple = ()) -> dict | None:
    return await asyncio.to_thread(_query_one, sql, params)


async def execute(sql: str, params: tuple = ()) -> None:
    await asyncio.to_thread(_execute, sql, params)


# ─── Generic helpers ────────────────────────────────────────

async def insert(table: str, data: dict) -> None:
    cols = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    await execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values()))


async def update(table: str, id_: str, data: dict) -> None:
    if not data:
        return
    sets = ", ".join(f"{k}=?" for k in data)
    await execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), id_))


async def delete(table: str, id_: str) -> None:
    await execute(f"DELETE FROM {table} WHERE id=?", (id_,))


# ─── kv settings ────────────────────────────────────────────

async def kv_get_all() -> dict:
    rows = await query_all("SELECT key, value FROM kv")
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            out[r["key"]] = r["value"]
    return out


async def kv_get(key: str, default=None):
    row = await query_one("SELECT value FROM kv WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


async def kv_set(key: str, value) -> None:
    await execute(
        "INSERT INTO kv(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
