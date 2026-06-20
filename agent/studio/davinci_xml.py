"""Export a DaVinci Resolve-compatible timeline (FCP7 XML / xmeml).

References the local shot videos with cumulative in/out points so the user can do
the final edit in Resolve. Frames are computed at a fixed fps from clip durations.
A second video track carries timed keyword captions (FCP7 Text generators) aligned to
when the narration reaches each phrase.
"""
import json
import os
from pathlib import Path
from urllib.request import pathname2url
from xml.sax.saxutils import escape

from agent.config import BASE_DIR
from agent.studio import assembler, db, media_store

FPS = 24
STUDIO_MEDIA_DIR = Path(os.environ.get("STUDIO_OUT_DIR", BASE_DIR / "studio_media"))


def _file_url(p: Path) -> str:
    return "file://localhost" + pathname2url(str(p.resolve()))


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


async def build(project_id: str) -> dict:
    project = await db.query_one("SELECT * FROM project WHERE id=?", (project_id,))
    if not project:
        raise RuntimeError("project not found")
    shots = await db.query_all(
        "SELECT sh.* FROM shot sh JOIN scene sc ON sh.scene_id=sc.id "
        "WHERE sc.project_id=? AND sh.video_path IS NOT NULL ORDER BY sc.idx, sh.idx",
        (project_id,))
    if not shots:
        raise RuntimeError("Chưa có shot nào có video để export")

    w, h = assembler._res(project["aspect_ratio"])
    items, titles, start_f, total, tnum = [], [], 0, 0, 0
    for i, sh in enumerate(shots):
        path = assembler._local(sh["video_path"])
        if not path.exists():
            continue
        dur_s = await assembler.probe_duration(path)
        dur_f = max(1, round(dur_s * FPS))
        items.append(_clipitem(i, sh.get("title") or f"Shot {i+1}", path, start_f, dur_f, w, h))
        # timed keyword captions → title track (offset within this clip)
        try:
            caps = json.loads(sh.get("captions") or "[]")
        except (json.JSONDecodeError, TypeError):
            caps = []
        base = float(sh.get("start_time") or 0)   # caption times are scene-local
        for c in caps:
            off = max(0.0, float(c.get("start", 0)) - base)
            cs = start_f + round(off * FPS)
            cd = max(1, round((float(c.get("end", 0)) - float(c.get("start", 0))) * FPS))
            cd = min(cd, max(1, start_f + dur_f - cs))  # clamp inside the clip
            if c.get("text") and cd > 0 and cs < start_f + dur_f:
                titles.append(_title_item(tnum, c["text"], cs, cd))
                tnum += 1
        start_f += dur_f
        total += dur_f

    title_track = f"\n        <track>\n{chr(10).join(titles)}\n        </track>" if titles else ""
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
      </video>
    </media>
  </sequence>
</xmeml>
"""
    out_dir = STUDIO_MEDIA_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "timeline.xml"
    out.write_text(xml, encoding="utf-8")
    await db.execute("DELETE FROM asset WHERE project_id=? AND kind='davinci_xml'", (project_id,))
    await db.insert("asset", {
        "id": db.new_id(), "project_id": project_id, "kind": "davinci_xml",
        "path": str(out), "meta_json": None, "created_at": db.now()})
    return {"path": str(out), "web_path": f"/studio-media/{project_id}/timeline.xml",
            "clips": len(items)}
