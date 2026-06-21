"""Export a DaVinci Resolve-compatible timeline (FCP7 XML / xmeml).

References the local shot videos with cumulative in/out points so the user can do
the final edit in Resolve. Frames are computed at a fixed fps from clip durations.
A second video track carries timed keyword captions (FCP7 Text generators) aligned to
when the narration reaches each phrase.
"""
import asyncio
import json
import os
import shutil
from pathlib import Path
from urllib.request import pathname2url
from xml.sax.saxutils import escape

from PIL import Image

from agent.config import BASE_DIR
from agent.studio import assembler, db, media_store

FPS = 24
STUDIO_MEDIA_DIR = Path(os.environ.get("STUDIO_OUT_DIR", BASE_DIR / "studio_media"))


def _alpha(i: int) -> str:
    """0,1,2… → a,b,…,z,aa,ab… — a LETTERS-ONLY id. Media is staged under these names so
    Resolve can't read a digit run in the filename as an image-sequence frame number (UUID
    names like ...eea0e7a3310e.png get collapsed to a phantom '[3310-3621]' sequence)."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


async def _resolve_local(web_path, media_id, ext: str, project_id: str):
    """Local file for a shot media; if the cache file is missing, re-download from Flow by
    media_id — a generated shot whose local copy was pruned/never cached still exports."""
    if not web_path:
        return None
    p = assembler._local(web_path)
    if p.exists() and p.stat().st_size > 0:
        return p
    if media_id:
        web = await media_store.ensure_local(media_id, project_id, ext)
        if web:
            p = assembler._local(web)
            if p.exists() and p.stat().st_size > 0:
                return p
    return None


def _stage(src: Path, name: str, dv_dir: Path) -> Path:
    """Hardlink (or copy across volumes) `src` into dv_dir/<name><ext>; return the staged path.
    Lets the timeline reference sequence-safe filenames in one self-contained folder."""
    dst = dv_dir / f"{name}{src.suffix.lower()}"
    try:
        if dst.exists():
            dst.unlink()
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def _stage_image_jpg(src: Path, name: str, dv_dir: Path) -> Path:
    """Re-encode a still to JPG (flatten alpha) into dv_dir. Resolve reliably imports JPG
    stills but chokes on some PNGs ('media offline'), so storyboard frames are exported as
    JPG. Falls back to a plain hardlink if PIL can't read the source."""
    dst = dv_dir / f"{name}.jpg"
    try:
        with Image.open(src) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=92)
        return dst
    except OSError:
        return _stage(src, name, dv_dir)


def _file_url(p: Path) -> str:
    # Canonical Resolve form: file://localhost/<path>. On Windows pathname2url yields
    # '///D:/...'; the extra slashes (file://localhost///D:/...) trip Resolve's relink, so
    # collapse to a single slash → file://localhost/D:/... (and /home/... on posix).
    return "file://localhost/" + pathname2url(str(p.resolve())).lstrip("/")


def _srt_ts(sec: float) -> str:
    h = int(sec // 3600); m = int((sec % 3600) // 60)
    s = int(sec % 60); ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clipitem(idx: int, name: str, path: Path, start_f: int, dur_f: int, w: int, h: int) -> str:
    end_f = start_f + dur_f
    return f"""        <clipitem id="clip{idx}">
          <name>{escape(name)}</name>
          <duration>{dur_f}</duration>
          <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
          <start>{start_f}</start>
          <end>{end_f}</end>
          <in>0</in>
          <out>{dur_f}</out>
          <file id="file{idx}">
            <name>{escape(path.name)}</name>
            <pathurl>{_file_url(path)}</pathurl>
            <rate><timebase>{FPS}</timebase></rate>
            <duration>{dur_f}</duration>
            <media><video><samplecharacteristics>
              <width>{w}</width><height>{h}</height>
            </samplecharacteristics></video></media>
          </file>
        </clipitem>"""


def _title_item(idx: int, text: str, start_f: int, dur_f: int) -> str:
    """FCP7 'Text' generator clip (Resolve imports these onto a title track)."""
    end_f = start_f + dur_f
    return f"""        <clipitem id="title{idx}">
          <name>{escape(text[:40])}</name>
          <enabled>TRUE</enabled>
          <duration>{dur_f}</duration>
          <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
          <start>{start_f}</start>
          <end>{end_f}</end>
          <in>0</in>
          <out>{dur_f}</out>
          <effect>
            <name>Text</name>
            <effectid>Text</effectid>
            <effectcategory>Text</effectcategory>
            <effecttype>generator</effecttype>
            <mediatype>video</mediatype>
            <parameter>
              <parameterid>str</parameterid>
              <name>Text</name>
              <value>{escape(text)}</value>
            </parameter>
          </effect>
        </clipitem>"""


def _audio_item(idx: int, name: str, path: Path, start_f: int, dur_f: int) -> str:
    end_f = start_f + dur_f
    return f"""        <clipitem id="aclip{idx}">
          <name>{escape(name)}</name>
          <duration>{dur_f}</duration>
          <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
          <start>{start_f}</start>
          <end>{end_f}</end>
          <in>0</in>
          <out>{dur_f}</out>
          <file id="afile{idx}">
            <name>{escape(path.name)}</name>
            <pathurl>{_file_url(path)}</pathurl>
            <rate><timebase>{FPS}</timebase></rate>
            <duration>{dur_f}</duration>
            <media><audio><channelcount>2</channelcount></audio></media>
          </file>
          <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
        </clipitem>"""


DEFAULT_IMG_S = 4.0


async def build(project_id: str) -> dict:
    """Resolve timeline from each shot's VIDEO, or its IMAGE as a still when no video exists
    yet (storytelling: review storyboard images, then edit in Resolve without rendering Flow
    videos). Per scene: video shots use their probed length; image-only scenes scale their
    stills to fill the scene's continuous narration — same timing as 'Tạo video từ ảnh'."""
    project = await db.query_one("SELECT * FROM project WHERE id=?", (project_id,))
    if not project:
        raise RuntimeError("project not found")
    scenes = await db.query_all(
        "SELECT * FROM scene WHERE project_id=? ORDER BY idx", (project_id,))

    w, h = assembler._res(project["aspect_ratio"])
    # Stage media under sequence-safe (letters-only) names in one folder next to the XML, so
    # Resolve never mis-reads a UUID's digits as an image-sequence frame range.
    dv_dir = STUDIO_MEDIA_DIR / project_id / "dv_media"
    shutil.rmtree(dv_dir, ignore_errors=True)
    dv_dir.mkdir(parents=True, exist_ok=True)

    items, titles, srt, start_f, total, tnum = [], [], [], 0, 0, 0
    audio_segs = []   # (scene narration WAV, timeline start frame)
    skipped = []      # shots with media in the DB but no usable file (even after re-download)
    i = 0
    for sc in scenes:
        rows = await db.query_all(
            "SELECT * FROM shot WHERE scene_id=? AND "
            "(video_path IS NOT NULL OR image_path IS NOT NULL) ORDER BY idx", (sc["id"],))
        # Resolve each shot to a usable media file: prefer video, else the still image.
        usable = []   # (shot, path, is_image)
        for sh in rows:
            vp = await _resolve_local(sh.get("video_path"), sh.get("video_media_id"), "mp4", project_id)
            if vp:
                usable.append((sh, vp, False))
                continue
            ip = await _resolve_local(sh.get("image_path"), sh.get("image_media_id"), "png", project_id)
            if ip:
                usable.append((sh, ip, True))
            else:
                skipped.append(sh.get("title") or sh["id"])
        if not usable:
            continue

        # Per-shot durations (seconds). Video → its real length; image-only scene → scale the
        # stills to fill the scene narration (mirrors assembler._scene_clip).
        scene_dur = float(sc.get("narration_duration") or 0)
        if sc.get("narration_path") and scene_dur <= 0:
            np_ = assembler._local(sc["narration_path"])
            if np_.exists():
                scene_dur = await assembler.probe_duration(np_)
        base = []
        for (sh, path, is_img) in usable:
            base.append(max(0.5, float(sh.get("duration") or DEFAULT_IMG_S)) if is_img
                        else await assembler.probe_duration(path))
        if scene_dur > 0 and all(is_img for (_, _, is_img) in usable):
            s = sum(base) or 1.0
            durs = [d * scene_dur / s for d in base]
        else:
            durs = base

        if sc.get("narration_path"):                 # anchor scene narration at its start
            audio_segs.append((sc["narration_path"], start_f))

        for (sh, path, is_img), dur_s in zip(usable, durs):
            dur_f = max(1, round(dur_s * FPS))
            name = f"clip{_alpha(i)}"
            staged = await asyncio.to_thread(_stage_image_jpg, path, name, dv_dir) if is_img \
                else _stage(path, name, dv_dir)
            items.append(_clipitem(i, sh.get("title") or f"Shot {i+1}", staged, start_f, dur_f, w, h))
            # timed keyword captions → FCP7 title track (Studio) + a sibling SRT (works on Free)
            try:
                caps = json.loads(sh.get("captions") or "[]")
            except (json.JSONDecodeError, TypeError):
                caps = []
            base_t = float(sh.get("start_time") or 0)   # caption times are scene-local
            clip_start_s = start_f / FPS
            for c in caps:
                off = max(0.0, float(c.get("start", 0)) - base_t)
                cs = start_f + round(off * FPS)
                cd = max(1, round((float(c.get("end", 0)) - float(c.get("start", 0))) * FPS))
                cd = min(cd, max(1, start_f + dur_f - cs))  # clamp inside the clip
                if c.get("text") and cd > 0 and cs < start_f + dur_f:
                    titles.append(_title_item(tnum, c["text"], cs, cd))
                    gstart = clip_start_s + off
                    gend = min((start_f + dur_f) / FPS, gstart + (cd / FPS))
                    srt.append((gstart, gend, c["text"]))
                    tnum += 1
            start_f += dur_f
            total += dur_f
            i += 1

    if not items:
        raise RuntimeError("Chưa có shot nào có ảnh hoặc video để export")

    # narration audio track: each scene's continuous WAV at its timeline start
    audio_items = []
    for ai, (narr_web, sf) in enumerate(audio_segs):
        ap = assembler._local(narr_web)
        if not ap.exists():
            continue
        adur_f = max(1, round(await assembler.probe_duration(ap) * FPS))
        staged_ap = _stage(ap, f"narr{_alpha(ai)}", dv_dir)
        audio_items.append(_audio_item(ai, "narration", staged_ap, sf, adur_f))

    title_track = f"\n        <track>\n{chr(10).join(titles)}\n        </track>" if titles else ""
    audio_media = (f"""
      <audio>
        <track>
{chr(10).join(audio_items)}
        </track>
      </audio>""" if audio_items else "")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="seq1">
    <name>{escape(project["title"] or "Flow Studio")}</name>
    <duration>{total}</duration>
    <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
        <format><samplecharacteristics>
          <width>{w}</width><height>{h}</height>
          <rate><timebase>{FPS}</timebase></rate>
        </samplecharacteristics></format>
        <track>
{chr(10).join(items)}
        </track>{title_track}
      </video>{audio_media}
    </media>
  </sequence>
</xmeml>
"""
    out_dir = STUDIO_MEDIA_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "timeline.xml"
    out.write_text(xml, encoding="utf-8")

    # Sibling SRT of the keyword captions — Resolve (incl. Free) imports subtitles reliably,
    # whereas FCP7 title generators may be dropped on XML import.
    srt_web = None
    if srt:
        lines = []
        for n, (a, b, txt) in enumerate(srt, 1):
            lines.append(f"{n}\n{_srt_ts(a)} --> {_srt_ts(b)}\n{txt}\n")
        (out_dir / "captions.srt").write_text("\n".join(lines), encoding="utf-8")
        srt_web = f"/studio-media/{project_id}/captions.srt"

    await db.execute("DELETE FROM asset WHERE project_id=? AND kind='davinci_xml'", (project_id,))
    await db.insert("asset", {
        "id": db.new_id(), "project_id": project_id, "kind": "davinci_xml",
        "path": str(out), "meta_json": None, "created_at": db.now()})
    return {"path": str(out), "web_path": f"/studio-media/{project_id}/timeline.xml",
            "clips": len(items), "captions_srt": srt_web, "captions": len(srt),
            "audio_tracks": len(audio_items), "missing": len(skipped),
            "missing_titles": skipped[:20]}
