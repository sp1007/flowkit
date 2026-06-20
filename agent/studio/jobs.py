"""Background job manager for Flow Studio batch generation (video-app.md §9).

In-process asyncio jobs that outlive the HTTP request that started them, so closing
the browser tab no longer aborts a batch. Each job loops over a list of items,
calling an async worker per item with throttle between items. Progress is mirrored
to the `job` table and broadcast to WebSocket subscribers on /api/studio/ws.

The manager holds no Flow state of its own — workers are closures from studio.py
that call the existing per-item generate helpers.
"""
import asyncio
import json
import logging
import random
import time
from typing import Awaitable, Callable, Optional

from agent.studio import db

logger = logging.getLogger(__name__)

# Finished jobs linger this long so a tab that reconnects can still see the result.
_REAP_DELAY = 180.0


class Job:
    def __init__(self, jid: str, project_id: str, type_: str, total: int, label: str = ""):
        self.id = jid
        self.project_id = project_id
        self.type = type_
        self.total = total
        self.label = label
        self.done = 0
        self.errors: list[dict] = []
        self.status = "running"   # running | done | error | cancelled
        self.message = ""
        self.current = ""         # human label of the item in progress
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.cancel = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

    @property
    def progress(self) -> float:
        if not self.total:
            return 1.0
        return (self.done + len(self.errors)) / self.total

    def to_dict(self) -> dict:
        return {
            "id": self.id, "project_id": self.project_id, "type": self.type,
            "label": self.label, "total": self.total, "done": self.done,
            "errors": self.errors, "status": self.status, "message": self.message,
            "current": self.current, "progress": round(self.progress, 4),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._subs: set = set()

    # ── WebSocket subscribers ───────────────────────────────
    def subscribe(self, ws) -> None:
        self._subs.add(ws)

    def unsubscribe(self, ws) -> None:
        self._subs.discard(ws)

    async def _broadcast(self, job: Job) -> None:
        job.updated_at = time.time()
        payload = {"type": "job", "job": job.to_dict()}
        dead = []
        for ws in list(self._subs):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._subs.discard(ws)

    # ── Query / control ─────────────────────────────────────
    def active(self, project_id: Optional[str] = None) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()
                if project_id is None or j.project_id == project_id]

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        j = self._jobs.get(job_id)
        if j and j.status == "running":
            j.cancel.set()
            return True
        return False

    # ── Persistence (best-effort mirror to the job table) ───
    async def _persist(self, job: Job) -> None:
        try:
            row = {
                "project_id": job.project_id, "type": job.type, "status": job.status,
                "progress": job.progress, "message": job.message,
                "error": json.dumps(job.errors) if job.errors else None,
                "updated_at": time.time(),
            }
            existing = await db.query_one("SELECT id FROM job WHERE id=?", (job.id,))
            if existing:
                await db.update("job", job.id, row)
            else:
                await db.insert("job", {"id": job.id, "created_at": job.created_at, **row})
        except Exception:
            logger.exception("job persist failed")

    # ── Run a batch ─────────────────────────────────────────
    def start(
        self,
        *,
        project_id: str,
        type_: str,
        items: list,
        worker: Callable[[object], Awaitable[None]],
        label: str = "",
        throttle: tuple[float, float] = (2.0, 6.0),
        item_label: Optional[Callable[[object], str]] = None,
        finalize: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> Job:
        job = Job(db.new_id(), project_id, type_, len(items), label)
        self._jobs[job.id] = job
        job.task = asyncio.create_task(
            self._run(job, items, worker, throttle, item_label, finalize))
        return job

    async def _run(self, job, items, worker, throttle, item_label, finalize=None) -> None:
        await self._broadcast(job)
        await self._persist(job)
        for i, item in enumerate(items):
            if job.cancel.is_set():
                job.status = "cancelled"
                break
            job.current = item_label(item) if item_label else ""
            await self._broadcast(job)
            try:
                await worker(item)
                job.done += 1
            except Exception as ex:
                logger.exception("job %s item %d failed", job.id, i)
                job.errors.append(
                    {"item": (item_label(item) if item_label else str(i)), "error": str(ex)[:200]})
            await self._broadcast(job)
            await self._persist(job)
            if i < len(items) - 1 and not job.cancel.is_set():
                # Interruptible throttle: wakes early if the job is cancelled.
                try:
                    await asyncio.wait_for(job.cancel.wait(), timeout=random.uniform(*throttle))
                except asyncio.TimeoutError:
                    pass
        if finalize is not None and job.status != "cancelled":
            job.current = "Hoàn tất…"
            await self._broadcast(job)
            try:
                await finalize()
            except Exception as ex:
                logger.exception("job %s finalize failed", job.id)
                job.errors.append({"item": "finalize", "error": str(ex)[:200]})
        if job.status != "cancelled":
            job.status = "error" if job.errors and not job.done else "done"
        job.current = ""
        job.message = f"{job.done}/{job.total} xong" + (
            f", {len(job.errors)} lỗi" if job.errors else "")
        await self._broadcast(job)
        await self._persist(job)
        asyncio.create_task(self._reap(job.id))

    async def _reap(self, job_id: str, delay: float = _REAP_DELAY) -> None:
        await asyncio.sleep(delay)
        self._jobs.pop(job_id, None)


_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
