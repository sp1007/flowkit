# fk-timeline-resolve — Export DaVinci Resolve XML Timeline from Scene Images

Generates a FCP 7 XML file (importable by DaVinci Resolve) with all scene images placed on the video track in order. Optionally includes TTS audio tracks. Use this to edit the video manually in DaVinci Resolve after generating images.

## Input

1. `video_id` — the video to export (from `GET http://127.0.0.1:8100/api/videos`)
2. **Frame rate** — 24 or 30 fps (default: 24)
3. **Shot duration** — seconds per image clip (default: 8; or use TTS manifest if available)
4. **Include audio** — yes/no — attach TTS WAV files if `manifest.json` exists
5. **Output path** — where to save the XML (default: `<project_output_dir>/timeline_davinci.xml`)

---

## Step 1: Get project output directory and scenes

```bash
# Get video info
curl -s http://127.0.0.1:8100/api/videos/<video_id>

# Get all scenes ordered by display_order
curl -s "http://127.0.0.1:8100/api/scenes?video_id=<video_id>"

# Get project output dir
curl -s http://127.0.0.1:8100/api/projects/<project_id>/output-dir
```

---

## Step 2: Download scene images and generate XML

Run this Python script. It downloads images locally then generates the XML:

```python
import requests, json, struct, os, urllib.request
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────
VIDEO_ID   = "<video_id>"
PROJECT_ID = "<project_id>"
OUTDIR     = "<project output dir>"   # from /api/projects/<pid>/output-dir
FPS        = 24                        # 24 or 30
DEFAULT_SHOT_S = 8                     # seconds per shot when no TTS manifest

# Resolution: match your project orientation
WIDTH, HEIGHT = 1080, 1920  # VERTICAL 9:16; flip to 1920, 1080 for HORIZONTAL

# ── Load scenes ─────────────────────────────────────────────
scenes = requests.get(
    f"http://127.0.0.1:8100/api/scenes?video_id={VIDEO_ID}"
).json()
scenes.sort(key=lambda s: s["display_order"])

# ── Load TTS manifest if available ──────────────────────────
manifest_path = Path(OUTDIR) / "tts" / "manifest.json"
manifest = {}
shot_duration_override = {}  # shot_id → duration_s
if manifest_path.exists():
    with open(manifest_path) as f:
        manifest = json.load(f)
    # Build per-shot duration: each shot in a scene gets tts_duration/num_shots
    for scene_idx, info in manifest.items():
        dur  = info.get("duration", DEFAULT_SHOT_S * len(info.get("shots", [1])))
        shots = info.get("shots", [])
        if shots:
            per_shot = dur / len(shots)
            for sid in shots:
                shot_duration_override[sid] = per_shot

# ── Download images ─────────────────────────────────────────
img_dir = Path(OUTDIR) / "timeline_images"
img_dir.mkdir(exist_ok=True)

clips = []
for i, scene in enumerate(scenes):
    img_url = (
        scene.get("image_url") or
        scene.get("output_url") or
        scene.get("image_local_path")
    )
    if not img_url:
        print(f"[{i:03d}] scene {scene['id'][:8]} — no image, skipping")
        continue

    img_path = img_dir / f"scene_{i:03d}_{scene['id'][:8]}.jpg"
    if not img_path.exists():
        try:
            if img_url.startswith("http"):
                urllib.request.urlretrieve(img_url, img_path)
                print(f"[{i:03d}] downloaded → {img_path.name}")
            else:
                # Local path
                import shutil
                shutil.copy(img_url, img_path)
        except Exception as e:
            print(f"[{i:03d}] ERROR downloading: {e}")
            continue
    else:
        print(f"[{i:03d}] already exists: {img_path.name}")

    dur_s = shot_duration_override.get(scene["id"], DEFAULT_SHOT_S)
    clips.append({
        "idx": i,
        "scene_id": scene["id"],
        "img_path": str(img_path.resolve()),
        "narrator_text": scene.get("narrator_text", ""),
        "duration_s": dur_s,
        "duration_f": round(dur_s * FPS),
    })

# ── Build TTS audio track info ───────────────────────────────
audio_clips = []
if manifest:
    cur_frame = 0
    # Map shot_id → global start frame
    shot_start_frame = {}
    for clip in clips:
        shot_start_frame[clip["scene_id"]] = cur_frame
        cur_frame += clip["duration_f"]

    for scene_idx, info in sorted(manifest.items(), key=lambda x: int(x[0])):
        wav_path = info.get("wav", "")
        if not wav_path or not Path(wav_path).exists():
            continue
        shot_ids = info.get("shots", [])
        if not shot_ids:
            continue
        # Audio starts at first shot's frame
        first_sid = shot_ids[0]
        start_frame = shot_start_frame.get(first_sid, 0)
        dur_s = info.get("duration", DEFAULT_SHOT_S)
        audio_clips.append({
            "idx": int(scene_idx),
            "wav": wav_path,
            "start_f": start_frame,
            "dur_f": round(dur_s * FPS),
            "dur_s": dur_s,
        })

# ── Generate FCP 7 XML ───────────────────────────────────────
total_frames = sum(c["duration_f"] for c in clips)
project_name = f"Story Timeline {datetime.now().strftime('%Y-%m-%d')}"

def rate_block(indent=""):
    return f"{indent}<rate><ntsc>FALSE</ntsc><timebase>{FPS}</timebase></rate>"

def file_block(clip_id, clip):
    path_uri = Path(clip["img_path"]).as_uri()
    return f"""        <file id="file-{clip_id}">
          <name>{Path(clip["img_path"]).name}</name>
          <pathurl>{path_uri}</pathurl>
          {rate_block()}
          <duration>{clip["duration_f"]}</duration>
          <media>
            <video>
              <samplecharacteristics>
                <width>{WIDTH}</width><height>{HEIGHT}</height>
                <pixelaspectratio>square</pixelaspectratio>
                {rate_block()}
              </samplecharacteristics>
            </video>
          </media>
        </file>"""

def audio_file_block(afile_id, ac):
    path_uri = Path(ac["wav"]).as_uri()
    return f"""        <file id="afile-{afile_id}">
          <name>{Path(ac["wav"]).name}</name>
          <pathurl>{path_uri}</pathurl>
          {rate_block()}
          <duration>{ac["dur_f"]}</duration>
          <media>
            <audio>
              <samplecharacteristics>
                <depth>16</depth>
                <samplerate>24000</samplerate>
              </samplecharacteristics>
            </audio>
          </media>
        </file>"""

lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<!DOCTYPE xmeml PUBLIC "-//Apple//DTD XMEML 1//EN" "http://www.apple.com/DTD/xmeml1.dtd">',
    '<xmeml version="4">',
    f'  <sequence id="seq-1">',
    f'    <name>{project_name}</name>',
    f'    <duration>{total_frames}</duration>',
    f'    {rate_block("    ")}',
    '    <in>-1</in><out>-1</out>',
    '    <media>',
    '      <video>',
    '        <format>',
    '          <samplecharacteristics>',
    f'            <width>{WIDTH}</width><height>{HEIGHT}</height>',
    '            <pixelaspectratio>square</pixelaspectratio>',
    f'            {rate_block("            ")}',
    '          </samplecharacteristics>',
    '        </format>',
    '        <track>',
]

start_frame = 0
for clip in clips:
    end_frame = start_frame + clip["duration_f"]
    clip_id   = clip["idx"] + 1
    lines += [
        f'          <clipitem id="clip-{clip_id}">',
        f'            <name>scene_{clip["idx"]:03d}</name>',
        f'            <duration>{clip["duration_f"]}</duration>',
        f'            {rate_block("            ")}',
        f'            <start>{start_frame}</start>',
        f'            <end>{end_frame}</end>',
        f'            <in>0</in><out>{clip["duration_f"]}</out>',
        file_block(clip_id, clip),
        '          </clipitem>',
    ]
    start_frame = end_frame

lines += [
    '        </track>',
    '      </video>',
]

if audio_clips:
    lines.append('      <audio>')
    lines.append('        <track>')
    for ac in audio_clips:
        afile_id = ac["idx"] + 1
        lines += [
            f'          <clipitem id="aclip-{afile_id}">',
            f'            <name>tts_scene_{ac["idx"]:03d}</name>',
            f'            <duration>{ac["dur_f"]}</duration>',
            f'            {rate_block("            ")}',
            f'            <start>{ac["start_f"]}</start>',
            f'            <end>{ac["start_f"] + ac["dur_f"]}</end>',
            f'            <in>0</in><out>{ac["dur_f"]}</out>',
            audio_file_block(afile_id, ac),
            '          </clipitem>',
        ]
    lines += [
        '        </track>',
        '      </audio>',
    ]

lines += [
    '    </media>',
    '  </sequence>',
    '</xmeml>',
]

xml_path = Path(OUTDIR) / "timeline_davinci.xml"
xml_path.write_text('\n'.join(lines), encoding='utf-8')

print(f"\nDaVinci timeline saved: {xml_path}")
print(f"  Clips: {len(clips)}")
print(f"  FPS: {FPS}")
print(f"  Resolution: {WIDTH}×{HEIGHT}")
print(f"  Total duration: {total_frames/FPS:.1f}s ({total_frames} frames)")
if audio_clips:
    print(f"  Audio tracks: {len(audio_clips)} scene WAVs")
print(f"\nImport into DaVinci Resolve:")
print(f"  File → Import → Timeline... → select {xml_path.name}")
```

---

## Step 3: Import into DaVinci Resolve

1. Open DaVinci Resolve
2. **File → Import → Timeline…**
3. Select `timeline_davinci.xml`
4. In the import dialog:
   - Set frame rate to match (24 or 30)
   - Set resolution to match project (1080×1920 for vertical, 1920×1080 for horizontal)
5. The timeline opens with all scene images on V1 track, TTS audio on A1 (if included)

### If images show as "offline" (red)

DaVinci couldn't find the images at the XML path. Fix:
- Right-click any offline clip → **Relink Selected Clips** → navigate to `<OUTDIR>/timeline_images/`
- Or use **File → Relink Clips…** and point to the images folder

### Notes

- Still images in DaVinci default to their set duration — DaVinci won't loop them, just hold the last frame
- For smooth transitions between images, add **Cross Dissolve** transitions in the Edit page
- To adjust image duration: select all clips on V1, right-click → **Change Clip Duration**
- Audio sync: TTS WAV clips start at the frame matching the first shot of their story scene
