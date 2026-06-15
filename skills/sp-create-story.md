# sp-create-story — Story Chapter to Reading Video

Converts a story chapter file into a full reading video. Every word is preserved as narration — no skipping, no summarizing. TTS audio is generated per **scene** (one location), not per shot. Shots are 8-second video clips that share the scene's audio.

## Hard rules

- **Full text only** — every word from the chapter must appear in `narrator_text`. Never paraphrase or skip.
- **One location per scene** — each story scene = one primary location. No two consecutive scenes may share the same location.
- **TTS per scene** — one Colab TTS call per story scene (full scene text). Shots within a scene share that audio.
- **Shots = ceil(tts_duration / 8)** — number of 8-second video clips per scene.
- **Scene video duration = TTS duration** — the total video of all shots in a scene equals the TTS audio length.
- **Plan first** — complete the full scene-shot tree and confirm with user BEFORE making any project/scene API calls.
- **Reference images** — all characters and objects use horizontal multi-pose white-background format.
- **All prompts in English** — `prompt`, `video_prompt`, `image_prompt` must be in English regardless of story language.

---

## Step 0: Gather inputs

Ask the user for:

1. **Story file path** — absolute path to the chapter `.txt` or `.md` file
2. **Visual style** — run `GET http://127.0.0.1:8100/api/materials` to show available styles
3. **Colab TTS URL** — the ngrok or localtunnel URL (e.g. `https://abc123.ngrok-free.app`)
4. **Voice** — call `GET <TTS_URL>/api/voices/list`, display choices, ask user to pick `voice_id`
5. **Orientation** — VERTICAL (9:16 Shorts) or HORIZONTAL (16:9 YouTube)
6. **TTS speed** — default `1.0`, suggest `0.95` for story reading clarity

Verify Colab TTS is online:
```bash
curl -s <TTS_URL>/api/health
# Must return {"status": "healthy", "model_loaded": true}
```

---

## Step 1: Read and analyze the chapter

Read the full file completely — do not truncate.

### 1a. Extract entities

**Characters** — every named person/creature:
- Name, gender, age/generation, physical appearance (height, build, face, hair, clothing, distinctive features)
- Role in the story

**Locations** — all named or implied places where scenes happen (list every distinct location)

**Key props/objects** — magical items, gifts, weapons, significant named objects

### 1b. Segment by location (PRIMARY RULE)

**Each scene = one primary location. Two consecutive scenes MUST have different locations.**

Segmentation priority:
1. Location change → always starts a new scene
2. If same location continues for a long stretch: identify a sub-location (e.g. "throne room" → "castle gate" → "river bank outside castle") to force visual variety
3. Time jump within same location → new scene with a distinct sub-location or time-of-day variant
4. Never cut mid-sentence

Target segment length: 60–150 words. Respect story flow over target length.

**Location alternation check**: After segmenting, scan consecutive pairs — if any two adjacent scenes share the same location label, re-split or rename one sub-location before proceeding.

Print segment plan and **wait for confirmation**:
```
Scene | Location           | Words | Text preview
  1   | Palace throne room |   72  | "Vua Hùng có người con gái..."
  2   | Mountain top       |   88  | "Một hôm, có hai chàng trai..."
  3   | Palace gate        |   64  | "Hai người đến tâu với vua..."
  ...
  N   | River bank         |   51  | "Từ đó, hằng năm nước dâng lên..."
Total: N scenes, XXXX words
No consecutive duplicate locations: ✓
```

---

## Step 2: TTS-first — get duration per SCENE (not per shot)

Call Colab TTS once for the **full scene text**. This gives the real audio duration → number of shots.

```python
import requests, base64, struct, math, json

TTS_URL  = "<user-provided>"
VOICE_ID = <selected>
SPEED    = <selected>

def tts_duration(text: str) -> float:
    resp = requests.post(f"{TTS_URL}/api/tts", json={
        "text": text, "voice_id": VOICE_ID, "speed": SPEED,
    }, timeout=120)
    resp.raise_for_status()
    wav = base64.b64decode(resp.json()["audio"])
    sample_rate = struct.unpack_from('<I', wav, 24)[0]
    channels    = struct.unpack_from('<H', wav, 22)[0]
    bits        = struct.unpack_from('<H', wav, 34)[0]
    data_size   = struct.unpack_from('<I', wav, 40)[0]
    return data_size / (sample_rate * channels * (bits // 8))

scenes_plan = [
    {"idx": 0, "location": "Palace throne room", "text": "Vua Hùng có người con gái..."},
    # ... all scenes
]

for scene in scenes_plan:
    scene["tts_duration"] = tts_duration(scene["text"])
    scene["shots"]        = math.ceil(scene["tts_duration"] / 8)
```

Print plan and **wait for confirmation**:
```
Scene | Location           | Words | TTS Duration | Shots | Text preview
  1   | Palace throne room |   72  |      19.4s   |   3   | "Vua Hùng có người con gái..."
  2   | Mountain top       |   88  |      23.8s   |   3   | "Một hôm, có hai chàng trai..."
  ...
Total: N scenes → M shots (M × 8s video, total TTS audio ≈ XXXs)
```

---

## Step 3: Plan full scene-shot tree (confirm BEFORE any API calls)

Expand every scene into its shots. Assign:
- Sub-text for each shot (equal sentence split, for visual prompt & subtitle display — NOT for TTS)
- Chain type per shot
- Characters visible

**Chain type rules:**
- Shot 1 of Scene 1 → `ROOT`
- Shots 2…N within any scene → `CONTINUATION` (same location, continuous action)
- Shot 1 of Scene 2, 3, … → `ROOT` (location always changes between scenes)

Print the tree:
```
Scene 1 — Palace throne room (19.4s TTS → 3 shots)
  Shot 1.1 [ROOT]         "Vua Hùng có người con gái tên là Mị Nương..."
  Shot 1.2 [CONTINUATION] "nàng đẹp như hoa, tính nết hiền dịu..."
  Shot 1.3 [CONTINUATION] "Vua Hùng yêu thương nàng hết mực..."

Scene 2 — Mountain top (23.8s TTS → 3 shots)
  Shot 2.1 [ROOT]         "Một hôm, có hai chàng trai đến cầu hôn..."
  ...

Total: N scenes, M shots (DB scenes)
```

**Wait for user confirmation** before proceeding.

---

## Step 4: Create the project

```bash
curl -s -X POST http://127.0.0.1:8100/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<story title — chapter N>",
    "description": "Video đọc truyện: <title>",
    "story": "<2-sentence plot summary>",
    "material": "<user-provided style>",
    "characters": [ ... ]
  }'
```

Save `project_id`.

### Character entity description (multi-pose horizontal ref)

```
Reference sheet showing <Name> in 3 poses side by side on pure white background:
(1) full-body front view, (2) three-quarter profile facing left,
(3) dynamic pose from the story (e.g. fighting stance, greeting, running).
No background scenery — white only. Horizontal layout.
<Physical details: age, gender, build, face shape, hair, clothing, distinctive features.>
```

### Object / prop entity description (3-view horizontal ref)

```
Object reference sheet: <Name>. Three views side by side on pure white background:
(1) front/main view, (2) side profile, (3) perspective detail.
No environment, no shadow, clean product-style. Horizontal layout.
<Physical description from story: material, color, size, shape, notable features.>
```

### Location entity description

```
<Location name>: <visual description from story>. <Atmosphere, time of day, architectural or natural features.> Detailed concept art.
```

---

## Step 5: Create the video record

```bash
curl -s -X POST http://127.0.0.1:8100/api/videos \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<PID>", "title": "<chapter title>", "display_order": 0}'
```

Save `video_id`.

---

## Step 6: Batch-create all shots (DB scenes) and save manifest

Create every shot from the plan in Step 3. After each `POST /api/scenes`, save the returned `id`.

`narrator_text` per shot = sub-portion of scene text (equal sentence split) — used for subtitle display and visual prompt reference, **NOT for TTS**.

```bash
# For each shot:
curl -s -X POST http://127.0.0.1:8100/api/scenes \
  -H "Content-Type: application/json" \
  -d '{
    "video_id":        "<VID>",
    "display_order":   <global_shot_index>,
    "narrator_text":   "<sub-portion of scene text for this 8s window>",
    "prompt":          "<English image prompt describing this 8s moment>",
    "video_prompt":    "<English video prompt 100-150 words>",
    "character_names": ["<entities visible in this clip>"],
    "chain_type":      "ROOT|CONTINUATION",
    "parent_scene_id": "<previous shot id, only for CONTINUATION>"
  }'
```

After all shots are created, save the scene-to-shots mapping:

```python
import json

manifest = {
    "0": {"text": "Full text of scene 1...", "shots": ["uuid1", "uuid2", "uuid3"], "location": "Palace throne room"},
    "1": {"text": "Full text of scene 2...", "shots": ["uuid4", "uuid5"], "location": "Mountain top"},
    # ...
}

with open(f"{OUTDIR}/tts/manifest.json", "w") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"Manifest saved: {len(manifest)} scenes, {sum(len(v['shots']) for v in manifest.values())} shots total")
```

### Image prompt rules

```
[Character] [action matching narrator_text]. [Location]. [Camera/composition]. Illustration style.
```

- Describe exactly what the story text says is happening at this 8-second window
- Full faces visible when characters are present
- Never describe character appearance (refs handle that)
- English only

### Video prompt rules

- 100–150 words, natural prose like briefing a film director
- Camera movement as a separate sentence
- `Audio: soft ambient music, natural environment sounds.`
- `Negative: subtitles, watermark, text overlay.`

---

## Step 7: Switch active project

```bash
curl -s -X PUT http://127.0.0.1:8100/api/active-project \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<PID>"}'

curl -s http://127.0.0.1:8100/api/active-project
```

---

## Step 8: Generate reference images

Run `/fk-gen-refs`

Review:
- Characters: must show 3 poses on white background, horizontal layout
- Objects: must show 3 views on white background
- If a ref is single-pose or has a background, PATCH the entity description and regenerate

---

## Step 9: Generate TTS WAV per STORY SCENE via Colab API

**One WAV per story scene (full scene text) — NOT per shot.**
**Do NOT use `/fk-gen-narrator`.**

```python
import requests, base64, struct, json, os

TTS_URL  = "<user-provided>"
VOICE_ID = <selected>
SPEED    = <selected>
OUTDIR   = "<project output dir>"

os.makedirs(f"{OUTDIR}/tts", exist_ok=True)

with open(f"{OUTDIR}/tts/manifest.json") as f:
    manifest = json.load(f)

def wav_duration(wav_bytes: bytes) -> float:
    sample_rate = struct.unpack_from('<I', wav_bytes, 24)[0]
    channels    = struct.unpack_from('<H', wav_bytes, 22)[0]
    bits        = struct.unpack_from('<H', wav_bytes, 34)[0]
    data_size   = struct.unpack_from('<I', wav_bytes, 40)[0]
    return data_size / (sample_rate * channels * (bits // 8))

for scene_idx, info in sorted(manifest.items(), key=lambda x: int(x[0])):
    text = info["text"]
    first_shot_id = info["shots"][0] if info["shots"] else scene_idx

    resp = requests.post(f"{TTS_URL}/api/tts", json={
        "text": text, "voice_id": VOICE_ID, "speed": SPEED,
    }, timeout=180)
    resp.raise_for_status()

    wav_bytes = base64.b64decode(resp.json()["audio"])
    duration  = wav_duration(wav_bytes)
    out_path  = f"{OUTDIR}/tts/story_{int(scene_idx):03d}_{first_shot_id}.wav"

    with open(out_path, "wb") as f:
        f.write(wav_bytes)

    manifest[scene_idx]["wav"]      = out_path
    manifest[scene_idx]["duration"] = duration
    print(f"[{int(scene_idx):03d}] {len(text)} chars → {duration:.1f}s → {out_path}")

with open(f"{OUTDIR}/tts/manifest.json", "w") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(f"\nDone: {len(manifest)} scene WAVs")
```

---

## Step 10: Generate images and videos

```
/fk-gen-images     — generate scene illustrations (one per shot)
/fk-gen-videos     — generate 8-second video clips
```

---

## Step 11: Concat with scene-level TTS audio

Custom concat script — groups shots by story scene, attaches scene WAV:

```python
import subprocess, json, os, requests

OUTDIR   = "<project output dir>"
VIDEO_ID = "<video_id>"

with open(f"{OUTDIR}/tts/manifest.json") as f:
    manifest = json.load(f)

shots_resp = requests.get(f"http://127.0.0.1:8100/api/scenes?video_id={VIDEO_ID}").json()
shot_by_id = {s["id"]: s for s in shots_resp}

os.makedirs(f"{OUTDIR}/tmp", exist_ok=True)

scene_clips = []
for scene_idx, info in sorted(manifest.items(), key=lambda x: int(x[0])):
    wav_path  = info.get("wav")
    duration  = info.get("duration", 0)
    shot_ids  = info["shots"]

    if not wav_path or not os.path.exists(wav_path):
        print(f"[{scene_idx}] SKIP — no WAV")
        continue

    # Collect video paths for shots in order
    shot_videos = []
    for sid in shot_ids:
        shot = shot_by_id.get(sid, {})
        vpath = shot.get("video_local_path") or shot.get("output_url")
        if vpath and os.path.exists(vpath):
            shot_videos.append(vpath)
        else:
            print(f"  [shot {sid[:8]}] WARNING — no video file")

    if not shot_videos:
        print(f"[{scene_idx}] SKIP — no shot videos")
        continue

    # Concat shot videos
    concat_txt = f"{OUTDIR}/tmp/scene_{scene_idx}_list.txt"
    with open(concat_txt, "w") as f:
        for v in shot_videos:
            f.write(f"file '{v}'\n")

    scene_video = f"{OUTDIR}/tmp/scene_{scene_idx}_video.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_txt, "-c", "copy", scene_video], check=True)

    # Mix audio + trim to TTS duration
    scene_final = f"{OUTDIR}/tmp/scene_{scene_idx}_final.mp4"
    subprocess.run(["ffmpeg", "-y",
                    "-i", scene_video, "-i", wav_path,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac",
                    "-t", str(duration),
                    scene_final], check=True)

    scene_clips.append(scene_final)
    print(f"[{scene_idx}] {len(shot_videos)} shots + {duration:.1f}s audio → {scene_final}")

# Final concat of all story scenes
final_list = f"{OUTDIR}/tmp/final_list.txt"
with open(final_list, "w") as f:
    for clip in scene_clips:
        f.write(f"file '{clip}'\n")

final_output = f"{OUTDIR}/final_video.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", final_list, "-c", "copy", final_output], check=True)

print(f"\nFinal video: {final_output}")
print(f"Scenes: {len(scene_clips)}")
```

Optional next steps:
```
/fk-gen-music          — add soft background music
/fk-brand-logo         — apply channel watermark
/fk-youtube-seo        — generate title/description/tags
/fk-youtube-upload     — upload to YouTube
/fk-timeline-resolve   — export DaVinci Resolve XML timeline from scene images
```

---

## Output summary (print after Step 6)

```
Story Reading Project Created
══════════════════════════════════════════════════════════
Project:      <title> (<PID>)
Video:        <VID>
Chapter file: <path>
Style:        <material>
Orientation:  VERTICAL | HORIZONTAL
TTS Voice:    ID <N> — <voice title>, speed <X>x

Story stats:  <XXXX> words, <N> scenes

Entities:
  Characters: A  (horizontal 3-pose white-bg refs)
  Locations:  B
  Props:      C

Scenes → Shots:
  Scene  1 [Location A, Xs TTS → N shots]: shots 0–N-1   "Vua Hùng có người con gái..."
  Scene  2 [Location B, Xs TTS → N shots]: shots N–M-1   "Một hôm, có hai chàng trai..."
  ...

Total shots (DB scenes): M
No consecutive duplicate locations: ✓
Full text preserved: ✓ (all words in narrator_text fields)
TTS audio: 1 WAV per story scene (NOT per shot)

Next steps:
  1. /fk-gen-refs                       generate reference images
  2. /fk-gen-images                     generate scene illustrations
  3. /fk-gen-videos                     generate 8s clips
  4. Run TTS script (Step 9)            save WAV files to ${OUTDIR}/tts/
  5. Run concat script (Step 11)        final video with scene-level audio
```

---

## Pre-pipeline checklist

Before running generation, verify:
- [ ] narrator_text total word count matches original chapter word count
- [ ] No consecutive scenes share the same location
- [ ] Each shot's prompt/video_prompt describes what is happening at that 8s moment
- [ ] Character descriptions include 3-pose horizontal white-bg instructions
- [ ] Object descriptions include 3-view horizontal white-bg instructions
- [ ] All Scene 1-shots of each story scene have chain_type=ROOT
- [ ] All shots 2…N within a story scene have chain_type=CONTINUATION
- [ ] manifest.json saved with scene → shot_ids mapping
- [ ] Active project switched to new project_id
