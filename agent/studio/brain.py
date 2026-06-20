"""The "brain" — wraps the AI-agent CLI (claude / agy) for Studio tasks.

Builds a prompt that demands strict JSON, runs it through /api/agent/run's underlying
handler, then extracts + parses the JSON (tolerant of code fences / surrounding prose).
Retries once on parse failure. See video-app.md §6.
"""
import json
import logging
import re

from fastapi import HTTPException

from agent.api.ai_agent import RunRequest, run_agent
from agent.studio import db

logger = logging.getLogger(__name__)


async def _agent_name() -> str:
    settings = await db.kv_get_all()
    return settings.get("agent") or "claude"


def _extract_json(text: str):
    """Pull the first JSON object/array out of arbitrary model output."""
    if not text:
        raise ValueError("empty agent output")
    # strip ``` fences
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # fast path
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # balance-scan from the first { or [
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON found in agent output")
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON in agent output")


async def run_json(prompt: str, *, timeout: float = 300.0, retries: int = 1):
    """Run the agent and return parsed JSON. Raises HTTPException(502) on failure."""
    agent = await _agent_name()
    last_err = None
    for attempt in range(retries + 1):
        nudge = "" if attempt == 0 else "\n\nReturn ONLY valid JSON, no prose, no markdown."
        res = await run_agent(RunRequest(agent=agent, prompt=prompt + nudge, timeout=timeout))
        if not res.get("ok"):
            last_err = res.get("stderr") or f"exit {res.get('exit_code')}"
            continue
        try:
            return _extract_json(res.get("stdout", ""))
        except ValueError as e:
            last_err = str(e)
            logger.warning("brain JSON parse failed (try %d): %s", attempt, e)
    raise HTTPException(502, f"AI-agent không trả JSON hợp lệ: {last_err}")


# ─── Scene parsing (Fountain-ish screenplay → scenes) ───────

_SLUG_RE = re.compile(r"^\s*(INT\.|EXT\.|INT/EXT\.|EXT/INT\.|I/E\.)", re.IGNORECASE)


def parse_scenes(script: str) -> list[dict]:
    """Split a screenplay into scenes on slug lines (INT./EXT. ...).

    Returns [{idx, heading, slug, location_name, body}]. Location = the part of the
    slug between the INT./EXT. prefix and a trailing " - TIME".
    """
    lines = (script or "").splitlines()
    scenes: list[dict] = []
    cur = None
    for ln in lines:
        if _SLUG_RE.match(ln):
            if cur:
                scenes.append(cur)
            heading = ln.strip()
            # location: drop prefix + trailing " - DAY/NIGHT/..."
            loc = _SLUG_RE.sub("", heading).strip(" .-")
            loc = re.split(r"\s+-\s+", loc)[0].strip()
            cur = {"idx": len(scenes), "heading": heading, "slug": heading,
                   "location_name": loc, "body": ""}
        elif cur is not None:
            cur["body"] += ln + "\n"
    if cur:
        scenes.append(cur)
    return scenes


# ─── Prompt composition (style-first + header/footer + culture) ──

def compose_prompt(project: dict, body: str, *, include_culture: bool = True) -> str:
    """Assemble the final image/video prompt for a project.

    Order: [prompt_header] → style (always first of the visual terms) + culture_hint →
    body → [prompt_footer]. `style` leads so the model anchors on it; the culture hint
    (e.g. "Vietnamese folk tale, traditional Vietnamese architecture") keeps imagery
    faithful to the story's origin instead of defaulting to the style's home culture.
    """
    style = (project.get("style") or "").strip()
    header = (project.get("prompt_header") or "").strip()
    footer = (project.get("prompt_footer") or "").strip()
    culture = (project.get("culture_hint") or "").strip() if include_culture else ""
    lead = ", ".join(p for p in (style, culture) if p)
    parts = [header, lead, (body or "").strip(), footer, _image_text_clause(project)]
    return ". ".join(p for p in parts if p)


def _image_text_clause(project: dict) -> str:
    """Instruction for the language of any text rendered INSIDE the image (signs,
    captions, labels). Domain-specific foreign terms (brand/product/English jargon)
    stay untranslated so they read naturally."""
    lang = (project.get("image_text_lang") or "Vietnamese").strip()
    if not lang:
        return ""
    return (f"Any visible text, signs, captions or labels in the image must be written "
            f"in {lang} (keep domain-specific foreign terms, e.g. English brand or "
            f"technical words, in their original language)")


# ─── Prompt templates ───────────────────────────────────────

def script_from_idea_prompt(idea: str, target_duration: int | None,
                            storytelling: bool, style: str, shot_duration: int = 8,
                            language: str = "Vietnamese") -> str:
    budget = ""
    if target_duration:
        shots = max(1, round(target_duration / max(1, shot_duration)))
        words = round(target_duration * 2.5)
        budget = (f"\nTARGET DURATION: {target_duration}s "
                  f"(≈ {shots} shots, ≈ {words} words of narration). "
                  f"Compress or expand the content to fit this length.")
    else:
        budget = "\nNo target duration — keep the full content, natural length."
    mode = ("This is STORYTELLING mode: write a continuous voiceover-driven story; "
            "each scene = one contiguous segment of the content, tied to one location."
            if storytelling else
            "Standard screenplay with dialogue and action.")
    return (
        "You are a professional screenwriter. Write a screenplay in FOUNTAIN format "
        "(scene headings like 'INT. PLACE - DAY', action lines, CHARACTER cues, dialogue).\n"
        f"WRITE THE SCREENPLAY IN {language.upper()}: all action lines, dialogue and "
        f"narration must be in {language}. Keep the FOUNTAIN structural keywords in English "
        "(INT./EXT., DAY/NIGHT, the dual-dialogue caret), but the place name in the scene "
        f"heading should be in {language}. Keep proper nouns and domain-specific foreign "
        "terms (brand/technical/English jargon) in their original language.\n"
        f"Visual style of the film: {style}.\n{mode}{budget}\n\n"
        f"IDEA / CONTENT:\n{idea}\n\n"
        "Also DETECT the cultural origin of this content (which country/era/folk tradition "
        "it belongs to) and return a short ENGLISH `culture_hint` — a comma-separated list of "
        "concrete visual cues that make generated imagery faithful to that origin "
        "(e.g. for a Vietnamese folk tale: 'Vietnamese folk tale, traditional Vietnamese "
        "architecture (nhà tranh, đình làng), áo dài / áo tứ thân clothing, Vietnamese rural "
        "landscape, conical hats'). If the content is culturally neutral, return an empty string.\n\n"
        "Return ONLY JSON: {\"script\": \"<fountain screenplay>\", "
        "\"estimated_duration\": <seconds>, \"culture_hint\": \"<english visual cues or empty>\"}"
    )


def entity_extract_prompt(script: str) -> str:
    return (
        "Extract every distinct ENTITY from this screenplay for an asset library.\n"
        "Three types: 'character' (people/animals), 'location' (places), 'prop' (key objects).\n"
        "For each, write a concise visual `description` and a `ref_prompt` (a vivid image "
        "prompt to generate its reference art).\n\n"
        f"SCREENPLAY:\n{script}\n\n"
        "Return ONLY JSON array: "
        "[{\"type\":\"character|location|prop\",\"name\":\"...\",\"description\":\"...\","
        "\"ref_prompt\":\"...\"}]"
    )


# Per-type reference-image prompt rules (video-app.md §2.2) — clean refs.
# Returns the BODY only; the caller wraps it with style/culture/header/footer via
# compose_prompt() so style always leads the prompt.
_SHEET = {
    "character": ("full character reference sheet on a plain solid white background, "
                  "laid out as a single sheet: ONE large detailed upper-body (bust) "
                  "portrait on the left, a row of turnaround views (front, 3/4, side, back) "
                  "in a neutral A-pose, and a separate row of facial EXPRESSION studies "
                  "(neutral, happy, sad, angry, surprised). Same consistent character in "
                  "every view. No scene, no extra props, no ground shadow, studio reference"),
    "prop": ("object design sheet, multiple angles (front, 3/4, side, top), single isolated "
             "object on plain solid white background, no background scene, no shadow, "
             "studio product reference"),
    "location": ("establishing location reference sheet, a 2x2 grid showing FOUR different "
                 "camera angles of the SAME place (wide establishing, reverse angle, high "
                 "angle, eye-level detail), consistent architecture and lighting across all "
                 "four, cinematic, no people"),
}


def ref_image_prompt(entity_type: str, name: str, description: str) -> str:
    """Build the (style-less) body of an entity's reference-art prompt."""
    base = (description or name).strip()
    rule = _SHEET.get(entity_type)
    if rule:
        return f"{name}: {base}. {rule}"
    return f"{name}: {base}. clean reference image"


# Cinematography spec injected into every shot-creating prompt so each frame's
# `visual_prompt` is a real camera setup, not a vague description. The model must
# make a deliberate choice on every axis below (and vary them across shots so the
# scene doesn't read as one flat angle repeated).
_CINE = (
    "CINEMATOGRAPHY — the `visual_prompt` MUST explicitly specify ALL of these, and vary "
    "them shot-to-shot so the scene has visual rhythm:\n"
    "  • Shot size / framing: extreme wide, wide/establishing, full, medium, medium close-up, "
    "close-up, or extreme close-up.\n"
    "  • Camera angle & height: eye-level, low angle, high angle, overhead/top-down, dutch "
    "tilt, over-the-shoulder, or POV.\n"
    "  • Lens / focal length & depth of field: e.g. 24mm wide, 35mm, 50mm, 85mm portrait, "
    "135mm telephoto — plus shallow depth of field (soft bokeh background) or deep focus.\n"
    "  • Lighting: scheme and direction (key/fill/back, soft vs hard, Rembrandt, rim/back-"
    "light, silhouette), source (natural daylight, golden hour, moonlight, practical lamps, "
    "firelight), color temperature (warm/cool) and overall contrast.\n"
    "  • Composition & object layout: where each character and prop sits in frame "
    "(foreground / midground / background), rule of thirds, leading lines, symmetry/balance, "
    "headroom and negative space.\n"
    "  • Mood / color palette and atmosphere: time of day, weather, haze/fog/dust, "
    "volumetric light, particles — whatever sells the scene's emotion."
)

# Dynamic spec injected into every motion-generating prompt. The shot's START FRAME is an
# image-to-video reference that ALREADY locks the static look (shot size, angle, focal
# length, lighting, composition). So the `motion_prompt` must NOT redefine that look — it
# only describes what MOVES over the clip. Re-stating the static framing risks the model
# morphing away from the frame.
_MOTION = (
    "MOTION (image-to-video) — the start frame already fixes the shot size, camera angle, "
    "focal length, lighting and composition. The `motion_prompt` describes ONLY what changes "
    "over time inside that locked frame; do NOT restate or alter the framing/angle/lens:\n"
    "  • Camera movement: type (push-in/dolly, pull-out, pan L/R, tilt up/down, truck, crane "
    "up/down, orbit/arc, handheld, or a static lock-off) + direction + speed (slow & steady "
    "vs brisk & decisive). If the shot is meant to be still, say 'locked-off, no camera move'.\n"
    "  • Focus pull: any rack focus / focus shift from one subject to another during the clip.\n"
    "  • Light & atmosphere over time: light shifting, flicker (fire, neon), drifting "
    "smoke/fog/dust, falling particles, moving shadows.\n"
    "  • Subject motion & pacing: the concrete action and its timing within the clip "
    "(when it starts, how it builds), referencing the SAME entities.\n"
    "  • Continuity: stay within the established frame — the look at the first frame must "
    "match the reference image; only the motion evolves."
)


def storyboard_autofill_prompt(scene_heading: str, scene_body: str,
                               entities: list[dict], style: str,
                               n_frames: int | None = None) -> str:
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    locations = [e["name"] for e in entities if e.get("type") == "location"]
    loc_line = (
        "The location entities available are: "
        + ", ".join("{" + n + "}" for n in locations)
        + ". Pick the single location this scene happens at."
    ) if locations else (
        "No location entity exists yet — invent a consistent place name and wrap it in "
        "curly braces, reusing the SAME name for every frame of this scene."
    )
    count = f"about {n_frames} frames" if n_frames else "as many frames as the action needs (2–6)"
    return (
        "Break this scene into storyboard FRAMES (still shots). Every frame in this scene "
        "happens at ONE shared location.\n"
        f"{loc_line}\n\n"
        "For each frame return:\n"
        "- `title`: short label.\n"
        "- `description`: a vivid image-generator prompt that MUST begin by naming the "
        "location and the camera angle, e.g. \"At {Khu rừng}, camera angle from the left, "
        "{Mai} opens the wooden door...\". Always state the place first, then the angle, "
        "then the action.\n"
        "- `visual_prompt`: the full camera setup + what is on screen for an image-to-video "
        "model — keep the SAME entity references.\n"
        "- `motion_prompt`: the camera move + the concrete action that happens during the "
        "clip, referencing the SAME entities.\n"
        "- `ref_entity_names`: every entity used in the frame (names WITHOUT braces), and it "
        "MUST include the scene's location.\n"
        f"\n{_CINE}\n\n{_MOTION}\n\n"
        "IMPORTANT: whenever a known entity (character/location/prop) appears in ANY prompt, "
        "wrap its name in curly braces exactly as listed (e.g. {Mai}) so it binds to its "
        "reference image.\n"
        f"Visual style: {style}. Produce {count}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\n"
        f"SCENE: {scene_heading}\n{scene_body}\n\n"
        "Return ONLY JSON array: [{\"title\":\"...\",\"description\":\"At {Location}, "
        "<angle>, ... {Entity} ...\",\"visual_prompt\":\"...\",\"motion_prompt\":\"...\","
        "\"ref_entity_names\":[\"Location\",\"Entity\"]}]"
    )


_SENT_RE = re.compile(r"[^.!?…\n]+[.!?…]+[\"'’”\)]*|\S[^.!?…\n]*(?:\n|$)", re.S)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.findall(text or "") if s.strip()]


def partition_text(text: str, n: int) -> list[str]:
    """Split `text` into up to `n` contiguous, VERBATIM parts on sentence boundaries,
    balanced by length. Storytelling reads the user's ORIGINAL input — so every word is
    kept, in order: concatenating the parts back gives the whole source (only inter-
    sentence whitespace is normalized to single spaces). Never rewrites or drops content."""
    text = (text or "").strip()
    if not text:
        return []
    sents = _sentences(text)
    if not sents:
        return [text]
    n = max(1, min(n, len(sents)))
    if n == 1:
        return [" ".join(sents)]
    total = sum(len(s) for s in sents) or 1
    target = total / n
    parts: list[str] = []
    cur: list[str] = []
    acc = 0
    for i, s in enumerate(sents):
        cur.append(s)
        acc += len(s)
        opened = len(parts)
        sents_left = len(sents) - i - 1
        slots_left = n - opened - 1               # parts still to open after this one
        # must close now if we have to reserve ≥1 sentence for every remaining slot
        must_close = sents_left <= slots_left
        if opened < n - 1 and (acc >= target * (opened + 1) or must_close):
            parts.append(" ".join(cur))
            cur = []
    if cur:
        parts.append(" ".join(cur))
    return parts


def scene_segment_prompt(voiceover: str, entities: list[dict], style: str) -> str:
    """Split an ALREADY-WRITTEN scene voiceover into visual BEATS. Each beat's `text` is a
    verbatim CONTIGUOUS slice of the voiceover (in order, concatenating back to the whole),
    so each beat's share of the audio time can be derived from its word count. Also pick the
    key phrases to flash on screen when the narration reaches them."""
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    locations = [e["name"] for e in entities if e.get("type") == "location"]
    loc_line = (
        "Location entities available: " + ", ".join("{" + n + "}" for n in locations)
        + ". Every beat is at the ONE location of this scene."
    ) if locations else (
        "No location entity yet — invent ONE consistent place name in curly braces and "
        "reuse it for every beat."
    )
    return (
        "Split this scene VOICEOVER into visual BEATS (one beat = one on-screen moment). "
        "Do NOT rewrite the narration — each beat's `text` MUST be a verbatim, contiguous "
        "slice of the voiceover, and the slices in order MUST concatenate back to the whole "
        "voiceover (no gaps, no overlaps).\n"
        f"{loc_line}\n\n"
        "For each beat return:\n"
        "- `text`: the verbatim voiceover slice for this beat.\n"
        "- `beat_action`: the concrete action happening on screen.\n"
        "- `description`: image prompt beginning with the location then the shot size/angle, "
        "e.g. \"At {Làng}, low-angle wide shot, {Tấm} scrubs the porch...\".\n"
        "- `visual_prompt`: the full camera setup + what is on screen (same entity refs).\n"
        "- `motion_prompt`: camera move + action during the clip (same entity refs).\n"
        "- `ref_entity_names`: entity names WITHOUT braces, MUST include the location.\n"
        "- `key_phrases`: 1–3 SHORT punchy phrases taken VERBATIM from this beat's `text` "
        "(the words worth flashing on screen as captions); [] if none.\n\n"
        f"{_CINE}\n\n{_MOTION}\n\n"
        f"Wrap known entity names in curly braces. Visual style: {style}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\nVOICEOVER:\n{voiceover}\n\n"
        "Return ONLY JSON array: [{\"text\":\"...\",\"beat_action\":\"...\","
        "\"description\":\"At {Loc}, <angle>, ...\",\"visual_prompt\":\"...\","
        "\"motion_prompt\":\"...\",\"ref_entity_names\":[\"Loc\"],\"key_phrases\":[\"...\"]}]"
    )


def beat_parts_prompt(beat_action: str, motion_prompt: str, n_parts: int,
                      clip_s: int = 8) -> str:
    """A beat's video is longer than one clip (~clip_s s) → split into `n_parts` continuous
    sub-clips. Each sub-clip starts from the previous one's last frame (chained), so the
    motion must flow on. Returns a continuation motion prompt for each part."""
    return (
        f"This action lasts longer than one {clip_s}-second video clip, so it is rendered as "
        f"{n_parts} consecutive sub-clips that play back-to-back as ONE continuous shot. Each "
        "sub-clip begins on the LAST frame of the previous one, so the motion must continue "
        "smoothly without resetting or repeating.\n\n"
        f"FULL ACTION: {beat_action}\nFULL MOTION: {motion_prompt}\n\n"
        f"Write {n_parts} motion prompts, one per sub-clip in order, each describing only the "
        f"portion of the action in that ~{clip_s}s window (continuous, no repetition).\n\n"
        f"{_MOTION}\n\n"
        "Return ONLY JSON: {\"parts\":[{\"part_idx\":0,\"motion_prompt\":\"...\"}, ...]}"
    )


def shot_prompts_prompt(description: str, style: str) -> str:
    return (
        "For this storyboard frame, write two prompts for an image-to-video model:\n"
        "- `visual_prompt`: the full camera setup + what is on screen.\n"
        "- `motion_prompt`: the camera move + the action that happens during the clip "
        "(concrete, e.g. 'the fox steps onto the ice, camera slowly pushes in').\n"
        f"\n{_CINE}\n\n{_MOTION}\n\n"
        f"Visual style: {style}.\n\n"
        f"FRAME: {description}\n\n"
        "Return ONLY JSON: {\"visual_prompt\":\"...\",\"motion_prompt\":\"...\"}"
    )


def narrator_prompt(description: str, language: str = "Vietnamese") -> str:
    return (
        f"Write ONE short {language} narrator line (voiceover) for this shot — natural, "
        "spoken, 1–2 sentences, no stage directions.\n\n"
        f"SHOT: {description}\n\n"
        "Return ONLY JSON: {\"narrator_text\":\"...\"}"
    )


def seo_prompt(title: str, script: str, language: str = "Vietnamese") -> str:
    return (
        f"Create YouTube metadata in {language} for this video, plus a thumbnail image "
        "prompt (English).\n\n"
        f"WORKING TITLE: {title}\nSCRIPT:\n{script[:2000]}\n\n"
        "Return ONLY JSON: {\"title\":\"...\",\"description\":\"...\",\"tags\":[\"...\"],"
        "\"thumbnail_prompt\":\"...\"}"
    )


def edit_script_prompt(script: str, instruction: str, style: str,
                       language: str = "Vietnamese") -> str:
    return (
        "You are editing a FOUNTAIN screenplay. Apply the user's instruction and return "
        "the FULL updated screenplay (keep fountain format, scene headings 'INT./EXT.').\n"
        f"Keep the screenplay written in {language} (action lines, dialogue, narration), "
        "unless the instruction explicitly asks for another language.\n"
        f"Film style: {style}.\n\n"
        f"CURRENT SCREENPLAY:\n{script}\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        "Return ONLY JSON: {\"script\": \"<updated fountain screenplay>\"}"
    )
