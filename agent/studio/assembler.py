"""ffmpeg assembly for Flow Studio — normalize shot clips + narration → final.mp4.

Each clip is re-encoded to a uniform format (resolution/fps/audio) so the concat
demuxer can stitch them with -c copy. If a shot has a TTS narration WAV, it becomes
that clip's audio and -shortest trims the clip to the narration (the storytelling
"đọc tới đâu hình tới đó" alignment); otherwise a silent track is added.
"""
import asyncio
import json
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


async def _image_clip(img: Path, narration: Path | None, out: Path,
                      w: int, h: int, dur: float, ken_burns: bool) -> None:
    """Turn a still shot image into a clip of exactly `dur` seconds (+ narration audio).

    `-loop 1` holds the image; `-t dur` caps the length so it matches the narration
    beat, and the narration (if any) becomes the audio track. With `ken_burns` a slow
    zoom is applied so the slideshow isn't fully static.
    """
    fps = 24
    if ken_burns:
        frames = max(1, int(round(dur * fps)))
        # scale up first so the zoom never reveals padding, then zoompan, then fit canvas.
        vf = (
            f"scale={int(w*1.3)}:{int(h*1.3)}:force_original_aspect_ratio=increase,"
            f"crop={int(w*1.3)}:{int(h*1.3)},"
            f"zoompan=z='min(zoom+0.0007,1.18)':d={frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},"
            f"setsar=1,format=yuv420p"
        )
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p")

    args = ["ffmpeg", "-y", "-loop", "1", "-i", str(img)]
    if narration and narration.exists():
        args += ["-i", str(narration)]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    args += [
        "-t", f"{dur:.3f}",
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-ar", "44100", "-shortest", str(out),
    ]
    await _run(args)


def _caption_font() -> str | None:
    cand = os.environ.get("STUDIO_CAPTION_FONT") or "C:/Windows/Fonts/arial.ttf"
    return cand if Path(cand).exists() else None


def _drawtext_chain(captions: list[dict], font: str, out_dir: Path, tag: str,
                    h: int) -> str:
    """Build a chain of ffmpeg drawtext filters that flash each caption during its window.
    Text is read from a sidecar file (textfile=) to avoid escaping Vietnamese/punctuation."""
    parts = []
    fontposix = Path(font).as_posix()
    for k, c in enumerate(captions):
        txt = (c.get("text") or "").strip()
        if not txt or c.get("end", 0) <= c.get("start", 0):
            continue
        tf = out_dir / f"cap_{tag}_{k}.txt"
        tf.write_text(txt, encoding="utf-8")
        parts.append(
            f"drawtext=fontfile='{fontposix}':textfile='{tf.as_posix()}'"
            f":enable='between(t,{c['start']:.3f},{c['end']:.3f})'"
            f":fontsize={max(28, int(h*0.055))}:fontcolor=white:borderw=2:bordercolor=black"
            f":box=1:boxcolor=black@0.45:boxborderw=14"
            f":x=(w-text_w)/2:y=h-(text_h*2.2)"
        )
    return ",".join(parts)


async def _scene_clip(parts: list[dict], scene: dict, out: Path, w: int, h: int,
                      default_duration: float, ken_burns: bool, font: str | None) -> float:
    """Render ONE scene to `out`: its shot images shown back-to-back over the scene's single
    continuous narration (audio kept whole), with timed keyword captions burned on top."""
    narr = _local(scene["narration_path"]) if scene.get("narration_path") else None
    narr = narr if (narr and narr.exists()) else None
    scene_dur = float(scene.get("narration_duration") or 0)
    if narr and scene_dur <= 0:
        scene_dur = await probe_duration(narr)

    base = [max(0.5, float(p.get("duration") or default_duration)) for p in parts]
    if scene_dur > 0:                                  # scale image windows to cover audio
        s = sum(base) or 1.0
        durs = [d * scene_dur / s for d in base]
    else:
        durs = base

    # one silent sub-clip per shot image, then concat → scene video
    tmp = []
    for k, p in enumerate(parts):
        src = _local(p["image_path"])
        if not src.exists():
            continue
        sub = out.with_name(f"{out.stem}_s{k}.mp4")
        await _image_clip(src, None, sub, w, h, durs[k], ken_burns)
        tmp.append(sub)
    if not tmp:
        raise RuntimeError("scene không có ảnh hợp lệ")
    lst = out.with_name(f"{out.stem}_list.txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in tmp), encoding="utf-8")
    silent = out.with_name(f"{out.stem}_silent.mp4")
    await _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", str(silent)])

    # captions are stored scene-local (already on the scene timeline)
    caps: list[dict] = []
    for p in parts:
        try:
            caps.extend(json.loads(p.get("captions") or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
    chain = _drawtext_chain(caps, font, out.parent, out.stem, h) if font and caps else ""

    args = ["ffmpeg", "-y", "-i", str(silent)]
    if narr:
        args += ["-i", str(narr)]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if chain:
        args += ["-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]", "-map", "1:a"]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "44100", "-shortest", str(out)]
    await _run(args)
    return await probe_duration(out)


async def assemble_from_images(project_id: str, ken_burns: bool = True,
                               default_duration: float = 4.0) -> dict:
    """Build ONE long video from the shot IMAGES, grouped BY SCENE.

    Storytelling (§2.6): each scene has ONE continuous narration (kept whole for emotional
    flow); its shot images play back-to-back to cover that audio ("đọc tới đâu hình tới
    đó"), with timed keyword captions burned on. Scenes without narration fall back to each
    shot's `duration`. Scene clips concat into studio_media/<pid>/final.mp4 — no Flow video.
    """
    project = await db.query_one("SELECT * FROM project WHERE id=?", (project_id,))
    if not project:
        raise RuntimeError("project not found")
    scenes = await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (project_id,))
    out_dir = STUDIO_MEDIA_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = _res(project["aspect_ratio"])
    font = _caption_font()

    clip_paths, total = [], 0.0
    for si, sc in enumerate(scenes):
        parts = await db.query_all(
            "SELECT * FROM shot WHERE scene_id=? AND image_path IS NOT NULL ORDER BY idx",
            (sc["id"],))
        parts = [p for p in parts if _local(p["image_path"]).exists()]
        if not parts:
            continue
        out = out_dir / f"scene{si:03d}.mp4"
        total += await _scene_clip(parts, sc, out, w, h, default_duration, ken_burns, font)
        clip_paths.append(out)

    if not clip_paths:
        raise RuntimeError("Chưa có shot nào có ảnh để ghép")

    list_file = out_dir / "concat_images.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in clip_paths), encoding="utf-8")
    final = out_dir / "final.mp4"
    await _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(final)])

    duration = await probe_duration(final)
    await db.execute("DELETE FROM asset WHERE project_id=? AND kind='final_video'", (project_id,))
    await db.insert("asset", {
        "id": db.new_id(), "project_id": project_id, "kind": "final_video",
        "path": str(final), "meta_json": None, "created_at": db.now()})
    web = f"/studio-media/{project_id}/final.mp4"
    return {"final_path": str(final), "web_path": web, "clips": len(clip_paths),
            "duration": duration, "mode": "images"}


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
