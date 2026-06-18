"""ffmpeg assembly for Flow Studio — normalize shot clips + narration → final.mp4.

Each clip is re-encoded to a uniform format (resolution/fps/audio) so the concat
demuxer can stitch them with -c copy. If a shot has a TTS narration WAV, it becomes
that clip's audio and -shortest trims the clip to the narration (the storytelling
"đọc tới đâu hình tới đó" alignment); otherwise a silent track is added.
"""
import asyncio
import logging
import os
from pathlib import Path

from agent.config import BASE_DIR
from agent.studio import db, media_store

logger = logging.getLogger(__name__)

STUDIO_MEDIA_DIR = Path(os.environ.get("STUDIO_OUT_DIR", BASE_DIR / "studio_media"))


def _local(web_path: str) -> Path:
    """/media/<pid>/<f> → absolute local path."""
    return media_store.MEDIA_DIR / web_path.replace("/media/", "", 1)


def _res(aspect: str) -> tuple[int, int]:
    return (720, 1280) if "PORTRAIT" in (aspect or "") else (1280, 720)


async def _run(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', 'replace')[-500:]}")


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def _normalize(src: Path, narration: Path | None, out: Path, w: int, h: int) -> None:
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24")
    args = ["ffmpeg", "-y", "-i", str(src)]
    if narration and narration.exists():
        args += ["-i", str(narration)]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    args += [
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-shortest", str(out),
    ]
    await _run(args)


async def assemble(project_id: str) -> dict:
    """Concat all shot videos (in scene/shot order) → studio_media/<pid>/final.mp4."""
    project = await db.query_one("SELECT * FROM project WHERE id=?", (project_id,))
    if not project:
        raise RuntimeError("project not found")
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? AND sh.video_path IS NOT NULL ORDER BY sc.idx, sh.idx",
        (project_id,))
    if not shots:
        raise RuntimeError("Chưa có shot nào có video để ghép")

    out_dir = STUDIO_MEDIA_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = _res(project["aspect_ratio"])

    norm_paths = []
    for i, sh in enumerate(shots):
        src = _local(sh["video_path"])
        if not src.exists():
            continue
        narr = _local(sh["narration_path"]) if sh.get("narration_path") else None
        out = out_dir / f"norm{i:03d}.mp4"
        await _normalize(src, narr, out, w, h)
        norm_paths.append(out)

    if not norm_paths:
        raise RuntimeError("Không có clip hợp lệ để ghép")

    list_file = out_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in norm_paths), encoding="utf-8")
    final = out_dir / "final.mp4"
    await _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(final)])

    duration = await probe_duration(final)
    # record asset
    await db.execute("DELETE FROM asset WHERE project_id=? AND kind='final_video'", (project_id,))
    await db.insert("asset", {
        "id": db.new_id(), "project_id": project_id, "kind": "final_video",
        "path": str(final), "meta_json": None, "created_at": db.now()})
    web = f"/studio-media/{project_id}/final.mp4"
    return {"final_path": str(final), "web_path": web, "clips": len(norm_paths),
            "duration": duration}
