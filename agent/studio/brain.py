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


# ─── Prompt templates ───────────────────────────────────────

def script_from_idea_prompt(idea: str, target_duration: int | None,
                            storytelling: bool, style: str, shot_duration: int = 8) -> str:
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
        f"Visual style of the film: {style}.\n{mode}{budget}\n\n"
        f"IDEA / CONTENT:\n{idea}\n\n"
        "Return ONLY JSON: {\"script\": \"<fountain screenplay>\", "
        "\"estimated_duration\": <seconds>}"
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
_SHEET = {
    "character": ("character design sheet, multiple turnaround views (front, 3/4, side, "
                  "back), neutral A-pose, neutral expression, plain solid white background, "
                  "no scene, no props, no ground shadow, studio reference"),
    "prop": ("object design sheet, multiple angles (front, 3/4, side, top), single isolated "
             "object on plain solid white background, no background scene, no shadow, "
             "studio product reference"),
}


def ref_image_prompt(entity_type: str, name: str, description: str, style: str) -> str:
    """Build the generate-image prompt for an entity's reference art."""
    base = (description or name).strip()
    rule = _SHEET.get(entity_type)
    if rule:  # character / prop → design sheet, white bg
        return f"{name}: {base}. {rule}. Art style: {style}."
    # location → establishing shot, keep background
    return f"Establishing shot of {name}: {base}. {style}, cinematic, no people."


def storyboard_autofill_prompt(scene_heading: str, scene_body: str,
                               entities: list[dict], style: str,
                               n_frames: int | None = None) -> str:
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    count = f"about {n_frames} frames" if n_frames else "as many frames as the action needs (2–6)"
    return (
        "Break this scene into storyboard FRAMES (still shots). For each frame write a "
        "`title` and a vivid `description` for an image generator.\n"
        "IMPORTANT: when a known entity appears, wrap its name in curly braces exactly as "
        "listed (e.g. {Mai}) so it can be bound to its reference image. List the entities "
        "used per frame in `ref_entity_names` (names without braces).\n"
        f"Visual style: {style}. Produce {count}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\n"
        f"SCENE: {scene_heading}\n{scene_body}\n\n"
        "Return ONLY JSON array: [{\"title\":\"...\",\"description\":\"... {Entity} ...\","
        "\"ref_entity_names\":[\"Entity\"]}]"
    )


def shot_prompts_prompt(description: str, style: str) -> str:
    return (
        "For this storyboard frame, write two prompts for an image-to-video model:\n"
        "- `visual_prompt`: what is on screen (subject, composition, lighting).\n"
        "- `motion_prompt`: the camera move + the action that happens during the clip "
        "(concrete, e.g. 'the fox steps onto the ice, camera slowly pushes in').\n"
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


def edit_script_prompt(script: str, instruction: str, style: str) -> str:
    return (
        "You are editing a FOUNTAIN screenplay. Apply the user's instruction and return "
        "the FULL updated screenplay (keep fountain format, scene headings 'INT./EXT.').\n"
        f"Film style: {style}.\n\n"
        f"CURRENT SCREENPLAY:\n{script}\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        "Return ONLY JSON: {\"script\": \"<updated fountain screenplay>\"}"
    )
