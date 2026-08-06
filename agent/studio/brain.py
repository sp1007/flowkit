"""The "brain" — wraps the AI-agent CLI (claude / agy) for Studio tasks.

Builds a prompt that demands strict JSON, runs it through /api/agent/run's underlying
handler, then extracts + parses the JSON (tolerant of code fences / surrounding prose).
Retries once on parse failure. See video-app.md §6.
"""
import asyncio
import json
import logging
import os
import re

from fastapi import HTTPException

from agent.api.ai_agent import RunRequest, run_agent
from agent.studio import db, vntext

logger = logging.getLogger(__name__)

# Per-call agent timeout for brain JSON prompts. Must match the CLI ceiling in config
# (AGENT_CLI_TIMEOUT) — a slow agent/model (e.g. antigravity + gemini-flash) can take
# several minutes per scene-plan/beat-split call, so 300s was too tight and tripped 504s.
_AGENT_TIMEOUT = float(os.environ.get("AGENT_CLI_TIMEOUT", "600"))


async def _agent_cfg() -> tuple[str, str | None]:
    """(agent key, model). Model comes from the `agent_model` setting (or env AGENT_MODEL);
    None → let the CLI use its own default. Passing a fast model (e.g. gemini-flash) here
    speeds up every brain call — script/scene/shot generation."""
    settings = await db.kv_get_all()
    agent = settings.get("agent") or "claude"
    model = (settings.get("agent_model") or os.environ.get("AGENT_MODEL") or "").strip() or None
    return agent, model


async def _agent_name() -> str:
    agent, _ = await _agent_cfg()
    return agent


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


async def run_json(prompt: str, *, timeout: float = _AGENT_TIMEOUT, retries: int = 2):
    """Run the agent and return parsed JSON. Raises HTTPException(502) on failure."""
    agent, model = await _agent_cfg()
    last_err = None
    for attempt in range(retries + 1):
        nudge = "" if attempt == 0 else "\n\nReturn ONLY valid JSON, no prose, no markdown."
        res = await run_agent(RunRequest(agent=agent, prompt=prompt + nudge, timeout=timeout,
                                         model=model))
        if not res.get("ok"):
            last_err = res.get("stderr") or f"exit {res.get('exit_code')}"
            continue
        try:
            return _extract_json(res.get("stdout", ""))
        except ValueError as e:
            last_err = str(e)
            logger.warning("brain JSON parse failed (try %d): %s", attempt, e)
    raise HTTPException(502, f"AI-agent không trả JSON hợp lệ: {last_err}")


async def run_json_valid(prompt: str, validate, *, label: str = "AI",
                         attempts: int = 3, timeout: float = _AGENT_TIMEOUT):
    """run_json that ALSO retries when the reply is valid JSON but fails `validate` (wrong
    shape/semantics — which run_json's parse-only retry can't catch). `validate(data)` returns
    True to accept. Raises HTTPException(502) after all attempts fail, so callers stop silently
    degrading to a worse result and instead surface (or retry) a real failure."""
    last = None
    for attempt in range(attempts):
        try:
            data = await run_json(prompt, timeout=timeout)
            if validate(data):
                return data
            last = "reply failed validation (wrong shape/size)"
            logger.warning("%s try %d: %s", label, attempt + 1, last)
        except HTTPException as e:
            last = e.detail
            logger.warning("%s try %d: %s", label, attempt + 1, last)
        except Exception as e:  # noqa: BLE001 — keep retrying through transient agent errors
            last = str(e)
            logger.warning("%s try %d: %s", label, attempt + 1, last)
        await asyncio.sleep(min(1.0 + attempt, 4.0))
    raise HTTPException(502, f"{label}: AI không trả kết quả hợp lệ sau {attempts} lần thử ({last})")


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

# Guard for SHOT FRAME generation. Some entity references are DESIGN SHEETS (character
# turnarounds + expression rows, prop multi-angle sheets). Without this, the model copies
# that sheet layout into the frame. This forces a single coherent photograph. Used only on
# the frame path, never when generating the reference art itself.
_SINGLE_FRAME = (
    "Render ONE single unified cinematic frame from a SINGLE camera angle — one continuous "
    "photographic moment, not a composite. The attached reference images (character turnaround "
    "& expression sheets, prop multi-angle sheets, a location establishing shot) are there ONLY "
    "to keep identity, costume, architecture, materials, colour and lighting consistent. Do NOT "
    "reproduce any reference-sheet layout: no grid, no 2x2, no multi-panel or split screen, no "
    "collage, no turnaround row, no side-by-side angles, no plain white reference backdrop. "
    "Each named character must match its OWN reference image in IDENTITY ONLY — face, hair, "
    "skin, build, age and costume — never swap, blend or mix up faces, hair or costumes between "
    "characters, and do NOT add any extra people who are not named in this shot. The reference "
    "does NOT dictate POSE: ignore its A-pose/stance, its expression, its gaze direction, its "
    "body orientation, its framing, and — when a reference happens to show more than one person "
    "— the way those people are arranged relative to each other. Pose, angle and spacing must be "
    "invented FRESH for THIS shot's action and camera setup, and must differ from other shots; "
    "characters interact with the scene and each other as the action demands. Never paste a "
    "character in as a rigid cut-out standing the way the reference sheet shows. The location "
    "reference is a "
    "2x2 grid of FOUR angles of the place for identity only — PICK the ONE angle that suits "
    "this shot and render it as a single full-frame scene; do NOT reproduce the grid, the four "
    "panels, the split layout or any position labels from it, and compose THIS shot at its own "
    "specified shot size and camera angle. "
    # Chỗ chết người: mô tả frame do LLM viết từ CHỮ (description của entity), còn ảnh location
    # là do model vẽ ra — hai bên lệch nhau là chuyện thường ("mái ngói rêu phong" trong chữ,
    # nhưng ảnh lại ra dãy hàng khô mái bằng). Không nói rõ bên nào thắng thì model theo CHỮ và
    # dựng hẳn một con phố khác, kéo theo trang phục lẫn nét vẽ trôi luôn — hai frame liền nhau
    # thành hai nơi khác hẳn. Ảnh phải thắng, đúng như ref_image_prompt đã làm với ảnh mẫu.
    "WHERE THE TEXT ABOVE AND THE REFERENCE IMAGES DISAGREE ABOUT WHAT SOMETHING LOOKS LIKE, "
    "THE REFERENCE WINS. This is THE place and THESE are the people from the references, not a "
    "similar-sounding one: keep the location's real architecture, roof and wall materials, "
    "shopfronts, signage, street furniture, era and colour exactly as the reference shows them, "
    "and keep each character's costume exactly as their sheet shows it. Wording in the text "
    "(e.g. 'ancient', 'century-old', 'mossy tiled roofs', 'modern') only says what to POINT THE "
    "CAMERA AT and how to light it — never a licence to rebuild the place in another style, "
    "another town or another period. If the text calls for a detail the reference does not show, "
    "render the nearest equivalent that already exists in the reference instead of inventing new "
    "surroundings. Render NO text, labels, captions, annotations, "
    "callouts or watermarks, and do not reproduce any text/labels that appear in the references"
)


# Nhãn cỡ cảnh tiếng Việt cho dòng caption dưới mỗi panel — cùng bộ từ người dùng đang gõ tay.
PANEL_SHOT_SIZES = ["toàn cảnh", "cận trung", "cận cảnh", "toàn thân", "cận sau",
                    "trung cảnh", "đại toàn cảnh", "qua vai"]

# Guard cho TRANG STORYBOARD (tab Storyboard) — nghịch đảo của `_SINGLE_FRAME`.
#
# `_SINGLE_FRAME` cấm lưới, cấm nhiều panel, cấm MỌI chữ trong ảnh. Ở đây cả ba thứ đó đều là
# thứ ta MUỐN: một trang gồm nhiều panel, mỗi panel có badge số và một dòng caption. Nối nhầm
# hai khối vào cùng một prompt là tự mâu thuẫn — model nhận "vẽ trang 6 panel" rồi ngay sau đó
# nhận "không được có grid, không được có chữ" và kết quả là hên xui.
#
# Những gì GIỮ LẠI từ `_SINGLE_FRAME` vì vẫn đúng: danh tính nhân vật bám sheet (chỉ danh tính,
# không bám dáng), không thêm người lạ, không chép layout của sheet tham chiếu vào trong panel,
# và câu "ảnh thắng chữ" (xem lịch sử: mô tả do LLM viết từ entity.description hay lệch với ảnh
# location model đã vẽ, không phân xử thì model dựng lại cả con phố).
_SHEET_PAGE = (
    "LAYOUT — render ONE storyboard PAGE: EXACTLY {n} cinematic panels in a clean {cols}x{rows} "
    "grid — {rows} rows of {cols} equally sized panels each, read left to right, top to bottom, "
    "panel 1 first. Draw all {n} of the panels listed below and number them 1 to {n} with none "
    "skipped, dropped, merged or added: a page with a panel missing, with a row of a different "
    "width, or with a gap in the numbering is wrong even if every panel on it is beautiful. "
    "Leave a {margin}px margin around the page and a {gap}px gap between panels horizontally. "
    "Directly under EACH panel put ONE short line of small-type caption text naming that "
    "panel's shot size (e.g. {sizes}); the next row of panels starts about {caption_gap}px below "
    "that caption line. A panel's caption is EXACTLY the words quoted for it below and nothing "
    "else — never append or print the camera direction (shot size, lens, movement), never print "
    "square brackets, and never print any English on the page. "
    "Number every panel with a small round badge in its TOP-LEFT corner: black digits on a "
    "white/cream circle. "
    "Every panel is a full cinematic frame in its own right, edge to edge inside its cell — no "
    "inner borders, no drop shadows, no rounded corners, no letterboxing, no picture-in-picture. "
    "The badges and the one-line captions are the ONLY text on the page: no titles, no headers, "
    "no arrows, no annotations, no watermarks, and never reproduce text or labels that appear in "
    "the reference images. "
    "CONSISTENT ACROSS EVERY PANEL — the panels are consecutive moments of one scene, drawn in "
    "the same pass: the location and its architecture, materials, signage and street furniture; "
    "the time of day, weather, light direction and colour temperature; every character's face, "
    "hair, costume and accessories; and the drawing style, line weight and colour palette. Only "
    "the camera and the action change between panels — each panel uses its OWN stated shot size, "
    "lens and movement, and neighbouring panels must differ in framing and in the characters' "
    "pose and gaze, so the page reads as a scene playing out rather than one picture repeated. "
    "Each named character must match its OWN reference image in IDENTITY ONLY — face, hair, "
    "skin, build, age and costume — never swap or blend faces, hair or costumes between "
    "characters, and add no extra people who are not named. The reference does NOT dictate POSE: "
    "ignore its A-pose, expression, gaze and framing; invent pose and framing fresh for each "
    "panel. Never reproduce a reference SHEET layout inside a panel — no turnaround row, no "
    "expression row, and if the location reference is itself a grid of angles, that grid must not "
    "appear anywhere on the page and its cells are ONE place seen several ways, never several "
    "places; re-frame that place freshly for each panel instead of copying any one cell. "
    "WHERE THE TEXT ABOVE AND THE REFERENCE IMAGES DISAGREE ABOUT WHAT SOMETHING LOOKS LIKE, "
    "THE REFERENCE WINS. This is THE place and THESE are the people from the references, not a "
    "similar-sounding one: keep the location's real architecture, materials, shopfronts, "
    "signage, era and colour, and each character's costume, exactly as the references show them. "
    "Wording such as 'ancient', 'century-old' or 'modern' only says what to point the camera at, "
    "never a licence to rebuild the place in another style, town or period. If the text calls "
    "for a detail the reference does not show, render the nearest equivalent that already exists "
    "in the reference instead of inventing new surroundings"
)


# Chỉ 4 hoặc 6 panel. Không phải hạn chế tuỳ tiện: trên khung 16:9, 2x2 và 3x2 là hai lưới cho
# ô đủ rộng để còn ra hình; 3x3 thì mỗi ô bé tới mức cận cảnh mặt người thành nhòe, mà cả trang
# vẫn chỉ là MỘT lượt sinh với ngần ấy chi tiết.
SHEET_PANEL_CHOICES = (4, 6)
SHEET_PANELS_DEFAULT = 6


def sheet_grid(panels: int | None) -> tuple[int, int]:
    """Số panel → (cols, rows). 4 → 2x2, 6 → 3x2; giá trị lạ rơi về mặc định."""
    n = int(panels or 0)
    if n not in SHEET_PANEL_CHOICES:
        n = SHEET_PANELS_DEFAULT
    return (2, 2) if n == 4 else (3, 2)


def sheet_page_guard(cols: int, rows: int, *, margin: int = 5, gap: int = 5,
                     caption_gap: int = 2) -> str:
    """`_SHEET_PAGE` điền theo kích thước lưới của dự án (⚙ Cấu hình dự án)."""
    return _SHEET_PAGE.format(
        n=cols * rows, cols=cols, rows=rows, margin=margin, gap=gap,
        caption_gap=caption_gap, sizes="/".join(PANEL_SHOT_SIZES[:5]))


_BRACE_TOKEN_RE = re.compile(r"\{([^{}]+)\}")


def _strip_braces(s: str) -> str:
    """`{Tên}` → `Tên`. Tên vẫn nằm trong câu để model đọc, chỉ mất tư cách token bind."""
    return _BRACE_TOKEN_RE.sub(r"\1", s or "")


def location_setting_clause(handle: str) -> str:
    """Câu mở đầu prompt TRANG storyboard: ảnh location là lưới, và frame nào mới là con phố.

    Ảnh location của một entity là contact sheet 2x2 bốn góc máy. Illustrators không cần giải
    thích gì — mỗi frame là một lượt sinh riêng và `_SINGLE_FRAME` cấm vẽ lưới, model tự chọn
    một ô. Trang storyboard thì im lặng là hỏng: prompt bảo "ảnh này LÀ con phố" trong khi đưa
    một tờ bốn ảnh khác nhau, và chính cái lưới ấy còn đánh nhau với lệnh dựng lưới 3x2. Đã đo:
    tham chiếu là phố rộng có cây và sạp hoa quả, trang vẽ ra là ngõ hẹp treo đèn lồng.

    Gọi FRAME 1 theo badge số mà `_SHEET["location"]` bắt model vẽ sẵn vào lưới — chỉ đích danh
    một con số chắc hơn tả vị trí, và không phải cắt ô ra rồi upload thêm một ảnh rác.

    Dùng chung cho cả tab Storyboard lẫn node editor của trang (`graph.py`, kind="sheet"): hai
    đường cùng vẽ một thứ thì phải nhận cùng một lời dặn."""
    return (f"SETTING — every panel takes place at the SAME place, {{{handle}}}. Read its "
            "reference correctly: it is NOT a single photograph, it is a 2x2 contact sheet of "
            "four camera angles of that ONE place, each cell numbered by a round badge — "
            + "; ".join(LOCATION_GRID_CELLS_EN) + ". Four views of one place, not "
            "four places. FRAME 1 is the definitive view; read frames 2, 3 and 4 only to learn "
            "details frame 1 does not show. Frame 1 is what the place IS: the width and shape of "
            "the road or room, the stalls and shopfronts and what they sell, the trees, awnings, "
            "signage, parked vehicles, furniture and the colour of the light. Build EVERY panel "
            "from it — a panel may look along it, across it, or close in on part of it, but it "
            "must be recognisably this place and not a similar-sounding one. Never copy that "
            "contact sheet's own 2x2 layout and never draw its badges; the page you draw uses "
            "the grid specified below. Any wording further down is about what HAPPENS there, "
            "never about what the place looks like.")


def sheet_page_prompt(panels: list[dict], refs: list[dict] | None = None) -> str:
    """Phần THÂN của prompt trang storyboard.

    Bố cục: một khối SETTING/CAST gọi tên MỖI reference đúng MỘT lần, rồi tới các dòng
    `Panel N [cỡ, ống kính, máy]: hành động` đã BỎ HẾT ngoặc nhọn.

    Vì sao phải gom token lên đầu thay vì để rải rác như bản gõ tay: một trang phải bind mỗi
    ảnh đúng một lần (bind mọi lần → 30 reference part → Flow trả 400, đã đo). Mà khi chỉ được
    bind một lần thì VỊ TRÍ của lần ấy quyết định tất cả — để nó rơi vào giữa câu panel 1 thì
    ảnh chỉ ràng buộc mỗi panel đó, còn 5 panel sau trôi theo chữ. Đặt ở đầu, trong câu nói rõ
    "ảnh này định nghĩa chỗ này cho MỌI panel", thì một lần bind phủ được cả trang.

    Bỏ ngoặc trong mô tả panel là cố ý: mỗi ngoặc còn sót lại sẽ ăn mất lượt bind của ảnh đó
    khỏi khối đầu, hoặc (nếu không dedupe) đẻ thêm reference part cho tới khi vượt trần.

    Khối SETTING nói thẳng ảnh location là LƯỚI 2x2 và chỉ đích danh ô trên-trái. Không nói thì
    model nhận một tờ contact sheet bốn góc máy trong khi prompt bảo "ảnh này LÀ con phố" — đã
    đo: tham chiếu là phố rộng có cây và sạp hoa quả, trang vẽ ra là ngõ hẹp treo đèn lồng.

    `caption` được ĐỌC THÀNH LỜI ("caption under this panel reads: …") chứ không để model tự
    nghĩ ra chữ. Thông số máy trong ngoặc vuông là tiếng Anh, nên nếu không đưa caption ra thì
    model chép luôn thông số ấy xuống làm caption và dòng chữ trong ảnh ra tiếng Anh."""
    head_lines: list[str] = []
    loc = next((r for r in (refs or []) if r.get("type") == "location"), None)
    if loc:
        head_lines.append(location_setting_clause(loc["handle"]))
    others = [r for r in (refs or []) if r.get("type") != "location"]
    if others:
        head_lines.append(
            "SUBJECTS — these appear across the panels and each must match its own reference "
            "image exactly: "
            + ", ".join("{" + r["handle"] + "}" for r in others)
            + ". Keep their identity, costume, colours and details as the references show them; "
            "only pose, angle and framing change from panel to panel.")

    out = list(head_lines)
    if loc:
        # Nhắc lại NGAY TRƯỚC danh sách panel. Khối SETTING đứng ở đầu một prompt ~9700 ký tự,
        # và tới lúc model đọc tới panel thì nó đã trôi: đo được trang vẽ ra một ngõ hẹp generic
        # trong khi frame 1 là phố rộng có cây. Nhắc bằng TÊN THƯỜNG, không ngoặc — lượt bind đã
        # tiêu ở khối SETTING rồi, thêm ngoặc ở đây chỉ ăn mất nó khỏi chỗ có ích hơn.
        out.append(
            f"Every panel below is set on that same street. Its background is {loc['handle']} "
            "exactly as frame 1 of the reference shows it — the same road width, the same trees, "
            "awnings and stalls, the same shopfronts and goods. A panel's wording says what "
            "HAPPENS in it; it never redescribes the place, and nothing in it licenses a "
            "different street.")
    for i, p in enumerate(panels):
        spec = ", ".join(str(p.get(k) or "").strip()
                         for k in ("shot_size", "lens", "movement") if (p.get(k) or "").strip())
        body = _strip_braces(str(p.get("description") or p.get("title") or "").strip().rstrip("."))
        cap = _strip_braces(str(p.get("caption") or "").strip().rstrip("."))
        # Thông số máy KHÔNG đứng cạnh số panel nữa. `Panel 1 [Wide, 24mm, tracking back]:` —
        # và cả biến thể trong ngoặc đơn — trông y như một nhãn viết sẵn, model chép nguyên cụm
        # xuống làm caption: trang ra "toàn cảnh [Wide, 24mm, tracking back]". Tách thành câu
        # riêng, đặt SAU hành động và cách xa câu caption, thì nó đọc như lời dặn.
        line = f"Panel {i + 1}: {body}." if body else f"Panel {i + 1}."
        if spec:
            line += f" Shoot it {spec.lower()}."
        if cap:
            line += (f' Its printed caption is the single phrase "{cap}" and nothing else — '
                     "never the camera wording.")
        out.append(line)
    return "\n".join(out)


def cast_clause(names: list[str], *, panels: int = 0) -> str:
    """Chốt SỐ NGƯỜI có trong khung, dựng từ các nhân vật mà prompt thật sự gọi tên.

    Sheet nhân vật có nhiều view (bust + turnaround + dãy biểu cảm), nên ở cỡ cận model hay
    vẽ thành hai bản sao của cùng một người đứng cạnh nhau. Câu "không thêm người lạ" trong
    _SINGLE_FRAME không chặn được vì bản sao KHÔNG phải người lạ — phải nói thẳng tổng số.

    `panels > 0` (trang storyboard): đếm theo MỖI PANEL. Nói "cả trang chỉ có 1 người" thì model
    vẽ nhân vật vào một panel rồi bỏ trống các panel còn lại."""
    names = [n for n in dict.fromkeys(n for n in names if n)]
    if not names:
        return ""
    who = ", ".join(names)
    n = len(names)
    where = f"EACH of the {panels} panels" if panels else "this frame"
    return (f"CAST — exactly {n} person{'' if n == 1 else 's'} appear{'s' if n == 1 else ''} in "
            f"{where}: {who}. Each appears ONCE and only once{' per panel' if panels else ''}. "
            "Never draw a second copy, twin, mirrored duplicate or reflection-as-a-person of the "
            "same character, and never split one character's reference views into several people "
            "standing together. Add no background crowd or bystanders unless the text above "
            "explicitly asks for them")


def scene_anchor_clause(handle: str) -> str:
    """Neo frame này vào một frame ĐÃ VẼ của cùng scene.

    Lưới location 2x2 là bốn ô nhỏ và model tự chọn một ô, nên mỗi frame vẫn là một lượt sinh
    độc lập — không frame nào thấy pixel của frame khác, và chỉ cần chữ nghiêng đi một chút là
    ra chỗ khác hẳn. Một frame THẬT của đúng scene này là neo mạnh hơn nhiều: nó đã chốt sẵn
    con phố nào, ánh sáng nào, áo quần nào.

    Handle phải là `shot.media_name` và phải được gọi bằng token `{…}` trong prompt — cú pháp
    DUY NHẤT Flow bind thành reference part (xem CLAUDE.md)."""
    if not handle:
        return ""
    return (
        f"ANCHOR — {{{handle}}} is an already-rendered still of THIS VERY scene, a moment from "
        "the same continuous take. IT, not the wording above, defines what this place and these "
        "people look like: reuse its exact architecture, shopfronts, signage, street furniture, "
        "materials and surfaces, its colour palette, time of day, weather and light direction, "
        "and every character's exact costume, hair and accessories as seen there. This frame "
        "shows the SAME place and the SAME moment-chain from THIS shot's own camera position — "
        "so do NOT copy its framing, camera angle, shot size or poses, and never paste it in as "
        "an inset, panel, split screen or picture-in-picture")


def compose_prompt(project: dict, body: str, *, include_culture: bool = True,
                   single_frame: bool = False, cast: list[str] | None = None,
                   anchor: str | None = None,
                   sheet_page: tuple[int, int] | None = None) -> str:
    """Assemble the final image/video prompt for a project.

    Order: [prompt_header] → style (always first of the visual terms) + culture_hint →
    body → [single-frame guard] → [cast] → [prompt_footer]. `style` leads so the model anchors
    on it; the culture hint (e.g. "Vietnamese folk tale, traditional Vietnamese architecture")
    keeps imagery faithful to the story's origin instead of defaulting to the style's home
    culture.

    `single_frame=True` (shot frames only) appends a guard so the model renders one coherent
    photograph instead of copying the entity reference SHEETS (incl. the 2x2 location grid).
    `cast`: tên các nhân vật frame này thật sự có — xem `cast_clause`.
    `anchor`: media_name của một frame đã vẽ trong cùng scene — xem `scene_anchor_clause`.
    Khối ANCHOR đứng NGAY SAU body và TRƯỚC single-frame guard: guard nói "chữ lệch ảnh thì ảnh
    thắng", nên neo phải có mặt trước đó để câu ấy trỏ được vào nó.

    `sheet_page=(cols, rows)` (tab Storyboard): dùng guard TRANG `_SHEET_PAGE` THAY CHO
    `_SINGLE_FRAME`. Hai khối phủ định nhau — một bên bắt vẽ lưới nhiều panel kèm badge số và
    caption, một bên cấm lưới và cấm mọi chữ — nên không bao giờ được đi cùng một prompt. Ghép
    cả hai chính là lỗi thấy trong bản gõ tay: "6 panels 3x2" ở đầu, "no grid, no multi-panel,
    render NO text" ở cuối.
    """
    style = (project.get("style") or "").strip()
    header = (project.get("prompt_header") or "").strip()
    footer = (project.get("prompt_footer") or "").strip()
    culture = (project.get("culture_hint") or "").strip() if include_culture else ""
    lead = ", ".join(p for p in (style, culture) if p)
    if sheet_page:
        guard = sheet_page_guard(*sheet_page)
        n_panels = sheet_page[0] * sheet_page[1]
    else:
        guard = _SINGLE_FRAME if single_frame else ""
        n_panels = 0
    parts = [header, lead, (body or "").strip(), scene_anchor_clause(anchor or ""), guard,
             cast_clause(cast or [], panels=n_panels), footer, _image_text_clause(project)]
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
        "A 'character' is ONE SINGLE individual — never a group. If the screenplay refers to "
        "several people with one collective term (a couple, the pair, the parents, the twins, "
        "the children, a family, a crowd, a gang, a team), do NOT make one entity for it: emit "
        "a SEPARATE character entity for each individual you can distinguish, each with its own "
        "singular `name` and its own appearance. Unnamed background crowds are not entities at "
        "all — leave them out. `name` must be singular and refer to one being; never a plural "
        "or collective noun.\n"
        "`name` MUST be the SHORT, single consistent token the screenplay uses to refer to the "
        "entity (e.g. a first name like 'Hùng', not 'Hùng (Phạm Trọng Hùng)'). Do NOT put a "
        "full name, alias or anything in parentheses in `name` — that goes in `description`. "
        "Keep `name` unique; if two characters share a short name, pick distinct short tokens.\n"
        "For each, write a concise visual `description` (you may note the full name/alias here) "
        "and a `ref_prompt` (a vivid image prompt to generate its reference art).\n\n"
        f"SCREENPLAY:\n{script}\n\n"
        "Return ONLY JSON array: "
        "[{\"type\":\"character|location|prop\",\"name\":\"...\",\"description\":\"...\","
        "\"ref_prompt\":\"...\"}]"
    )


# Per-type reference-image prompt rules (video-app.md §2.2) — clean refs.
# Returns the BODY only; the caller wraps it with style/culture/header/footer via
# compose_prompt() so style always leads the prompt.
_SHEET = {
    # EXACTLY ONE individual per sheet. A description that mentions a partner/group ("một nửa
    # của cặp đôi", "walks with her husband") otherwise makes the model draw BOTH people on the
    # sheet — and then every shot referencing it reproduces that same pair in the same stance,
    # like carrying one statue from frame to frame.
    "character": ("full character reference sheet on a plain solid white background, "
                  "laid out as a single sheet: ONE large detailed upper-body (bust) "
                  "portrait on the left, a row of turnaround views (front, 3/4, side, back) "
                  "in a neutral A-pose, and a separate row of facial EXPRESSION studies "
                  "(neutral, happy, sad, angry, surprised). EXACTLY ONE individual appears on "
                  "this sheet — the same single character in every view. Never draw two or more "
                  "people, never a couple, pair, family or group, even if the description above "
                  "mentions other people (they are separate entities with their own sheets); "
                  "include no companion, partner, child or bystander. No scene, no extra props, "
                  "no ground shadow, studio reference. Do NOT draw any text, titles, captions, "
                  "view labels or watermarks on the sheet — clean art only"),
    "prop": ("object design sheet, multiple angles (front, 3/4, side, top), single isolated "
             "object on plain solid white background, no background scene, no shadow, "
             "studio product reference. Do NOT draw any text, titles, captions, view labels or "
             "watermarks on the sheet — clean art only"),
    # ONE image = a 2x2 grid of four angles of the same place, in a FIXED quadrant order so
    # we can overlay correct position labels afterwards (Toàn cảnh / Nhìn ngang / Trên cao /
    # Cận cảnh). Shots use the single_frame guard to pick one angle instead of copying the grid.
    # Mỗi ô mang một badge số y như trang storyboard: prompt nào cần chỉ đích danh một góc thì
    # gọi "the cell badged 1" — chắc hơn hẳn tả bằng vị trí, và không phải cắt/upload thêm ảnh
    # nào. Ngoài badge ra vẫn cấm mọi chữ.
    "location": ("ONE image laid out as a tidy 2x2 grid of FOUR camera setups of the SAME "
                 "place, in this EXACT reading order. "
                 "Cell 1 is the master view: eye level, wide lens, camera at one end looking "
                 "through the whole space. "
                 "Cell 2 is taken from the near edge looking straight across at the frontage "
                 "opposite, so the street or room runs left-to-right across the frame instead of "
                 "away from the camera; no long receding perspective and no vanishing point in "
                 "the distance, the opposite side fills the frame flat on. "
                 "Cell 3 is a bird's-eye view from well above roof or ceiling height with the "
                 "camera tilted steeply down, the rooftops, canopies and the layout of the "
                 "ground being the subject. "
                 "Cell 4 stands about two metres from one single element — one shopfront, stall, "
                 "doorway, window or corner — which fills the frame; no receding perspective, no "
                 "vanishing point, no view along the whole space. "
                 "When a reference image of this place is supplied it shows ONE viewpoint, most "
                 "often the one cell 1 needs — so cell 1 reproduces it (same road or room width, "
                 "same trees, awnings, canopies and stall goods, same architecture and signage) "
                 "and cells 2, 3 and 4 are the SAME place seen from elsewhere, carrying those "
                 "very features across. For an angle the reference does not show, extend what it "
                 "does show; never fall back on a generic version of this kind of place (a "
                 "generic old-quarter alley, a generic market row) — that is the one failure "
                 "that makes the sheet useless. "
                 "These are four genuinely different vantage points, not one view repeated with "
                 "small changes — a viewer must tell them apart at a glance, and no two cells may "
                 "share the same camera height AND the same vanishing point. Consistent "
                 "architecture, materials, colour and lighting across all four. NO PEOPLE and no "
                 "animals anywhere in any cell (ignore any people mentioned above) — but empty of "
                 "people is NOT emptied of the place: it stays fully dressed and open for "
                 "business, stalls stocked and spilling over, goods hung out, shopfronts lit, "
                 "produce and merchandise on display exactly as the reference shows them. Never "
                 "roll the shutters down, never clear the stalls, never strip the colour out of "
                 "the place — the wares ARE the location. "
                 "Photoreal, cinematic, deep detail. "
                 "Number the cells 1, 2, 3, 4 in that reading order with EXACTLY ONE badge per "
                 "cell — a small white/cream circle holding a black digit, placed in that cell's "
                 "TOP-LEFT corner. FOUR badges on the whole image and no more: no second badge in "
                 "another corner of a cell, no number used twice, and no badge on a cell that "
                 "already has one. Those four digits are the ONLY text you draw: never write out "
                 "the wording of this brief, never label a cell with the type of shot it is, and "
                 "add no captions, titles or watermarks"),
}

# Position labels overlaid on the location grid quadrants (TL, TR, BL, BR), matching the
# order fixed in the _SHEET["location"] prompt above.
LOCATION_GRID_LABELS = ["Toàn cảnh", "Nhìn ngang", "Trên cao", "Cận cảnh"]

# Cùng bốn ô ấy, bằng tiếng Anh, cho prompt phải NÓI RÕ model đọc ô nào. Ảnh location là một
# lưới 2x2 — thứ hợp lý khi mỗi frame là một lượt sinh riêng (`_SINGLE_FRAME` cấm vẽ lưới, model
# tự chọn một ô), nhưng với MỘT TRANG storyboard thì im lặng là hỏng: prompt bảo "ảnh này LÀ con
# phố" trong khi đưa cho model một tờ contact sheet bốn góc máy, và chính cái lưới ấy còn đánh
# nhau với lệnh dựng lưới 3x2 của trang.
LOCATION_GRID_CELLS_EN = ("frame 1, the top-left cell, a wide establishing view along the place",
                          "frame 2, top-right, a flat side-on view across it",
                          "frame 3, bottom-left, a high overhead angle",
                          "frame 4, bottom-right, one frontage close up")


def ref_image_prompt(entity_type: str, name: str, description: str,
                     ref_handles: list[str] | None = None) -> str:
    """Build the (style-less) body of an entity's reference-art prompt.

    The entity NAME is a LIBRARY LABEL, not art direction, so it is no longer prefixed onto
    the prompt: the model read it as part of the scene description and painted whatever the
    label happened to mention — a location named "DÂY PHƠI VÀ CON PHỐ LÚC RẠNG SÁNG" came back
    with a clothesline hung across the street even when the description said nothing of the
    sort. The name is only used as the body when there is no description at all.
    Trailing dots are trimmed so the rule doesn't get glued on after ".." either.

    `ref_handles`: ảnh mẫu người dùng đính vào entity. Tên chúng được gọi bằng token `{…}` —
    cú pháp DUY NHẤT Flow bind thành reference part; nói suông "the attached photo" thì ảnh đi
    kèm request nhưng model không bám vào (xem CLAUDE.md).
    """
    base = ((description or "").strip() or (name or "").strip()).rstrip(" .")
    rule = _SHEET.get(entity_type) or "clean reference image"
    out = f"{base}. {rule}" if base else rule
    handles = [h for h in (ref_handles or []) if h]
    if handles:
        toks = ", ".join("{" + h + "}" for h in handles)
        out += (f". Reproduce the SUBJECT SHOWN IN {toks} — this is the same place/person/"
                "object, not merely an inspiration. Keep its architecture, materials, layout, "
                "proportions, colours and distinguishing details exactly as they appear there; "
                "the text above only says how to FRAME and light it. Where the text and the "
                "reference disagree about what the subject looks like, the reference wins.")
    return out


# Cinematography spec injected into every shot-creating prompt so each frame's
# `visual_prompt` is a real camera setup, not a vague description. The model must
# make a deliberate choice on every axis below (and vary them across shots so the
# scene doesn't read as one flat angle repeated).
_CINE = (
    "CINEMATOGRAPHY — BOTH the `description` (which generates the still image) and the "
    "`visual_prompt` MUST explicitly specify ALL of these, and ADJACENT frames MUST DIFFER "
    "(never repeat the same shot size AND angle in two consecutive frames) so the scene has "
    "visual rhythm and the cuts don't look like the same shot repeated:\n"
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
    "  • Pose & body language of EVERY character present: stance or posture, what the hands "
    "are doing, head turn and gaze direction, facial expression, and — with two or more people "
    "— how they are placed and turned relative to each other (facing, side by side, one behind, "
    "one reaching toward the other). State this explicitly and CHANGE it between frames; "
    "characters must act out the beat, never stand in the same neutral stance shot after shot "
    "like a statue moved around the set.\n"
    "  • Mood / color palette and atmosphere: time of day, weather, haze/fog/dust, "
    "volumetric light, particles — whatever sells the scene's emotion."
)

# Continuity spec for storyboard frames. Without it every frame was written as a standalone
# illustration of the same scene text: the model re-invented where the subject stood, which way
# it faced, how far the light had moved — so two adjacent "shots" could not be cut together, and
# the video step had nothing to travel BETWEEN. A storyboard is a single continuous take broken
# into key poses, so the frames must be written as one moving chain, not N independent pictures.
_CONTINUITY = (
    "CONTINUITY — the frames of this scene are consecutive moments of ONE unbroken take, in "
    "order. Frame N+1 must be physically reachable from frame N in a second or two of real "
    "time. Write them as a chain, never as separate illustrations of the same scene:\n"
    "  • LOCKED for the whole scene (identical in every frame, restate them so nothing drifts): "
    "time of day, weather, light direction and colour temperature, every character's costume / "
    "hair / accessories, the props they carry, and the architecture and dressing of the place.\n"
    "  • ONE spatial path: decide where the subject starts and where it ends up, then have each "
    "frame advance ALONG that path. A subject may not teleport, change which way it faces, or "
    "swap which hand holds a prop between two frames without the movement being described.\n"
    "  • The camera also travels a path. Adjacent frames must still DIFFER in shot size and "
    "angle (see CINEMATOGRAPHY), but the change has to read as a move or a cut a real operator "
    "would make from the previous position, motivated by what the scene is doing — not a random "
    "new viewpoint each frame. Choose the coverage this particular moment calls for rather than "
    "cycling through stock moves.\n"
    "  • Carry the surroundings across: the background elements, lights, vehicles, bystanders "
    "and weather visible in one frame stay present and consistent in the neighbouring frames, "
    "seen from the new angle.\n"
    "  • `continuity` (per frame) says in ONE sentence how this frame follows the previous one "
    "— what the subject moved, where the camera went, what changed. For the FIRST frame write "
    "what the scene opens on instead.\n"
    "  • The `description` of every frame after the first must OPEN by anchoring to that "
    "carried-over state (e.g. \"continuing from the same rainy street, now three steps closer "
    "to the gate, ...\") before it gives this frame's framing and action."
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

# Omni Flash reads TIMESTAMP CUES in the prompt: "[00:04] ..." means "from 4s until the next
# cue (or the end), do this". Veo has no such notion, so this block is only ever appended for
# Omni. Without it a 10s Omni clip gets one flat instruction and renders as a single monotonous
# camera move for the whole duration — paying 10 seconds' worth of credits for one beat.
_OMNI_TIMELINE_HEAD = (
    "TIMED BEATS (Omni Flash only) — this clip is {clip_s} seconds long and the model reads "
    "timestamp cues, so write the `motion_prompt` as a SEQUENCE of cues instead of one flat "
    "sentence:\n"
    "  • Format: `[mm:ss] <what happens from this moment until the next cue>`. Always open at "
    "`[00:00]`, then add cues across the clip; the last cue runs to the end.\n"
    "  • Use as MANY cues as the action genuinely warrants — {n_beats} or more for {clip_s}s. "
    "Denser is fine and often better, as long as every cue marks a REAL, distinct change that "
    "can physically happen in the time it is given. Never pad with cues that just restate the "
    "previous beat.\n"
    "  • Make consecutive beats DIFFERENT in kind, not just 'more of the same': e.g. a camera "
    "move, then a subject action, then a light/atmosphere change, then a focus shift or a beat "
    "of stillness. A single push-in held for the whole clip is exactly what to avoid.\n"
    "  • The beats must form ONE continuous take — no cuts, no teleporting; each follows on "
    "physically from the previous.\n"
    "  • Example shape (do not copy the content): `[00:00] locked-off on the puddle, rain "
    "dimpling the surface. [00:03] a slow push-in begins as ripples spread outward. [00:06] a "
    "cyclo rolls through frame behind, its lamp smearing across the water. [00:08] the ripples "
    "settle and the reflection resolves.`"
)


def motion_spec(engine: str = "veo", clip_s: int = 8) -> str:
    """Khối hướng dẫn viết `motion_prompt`, có thêm phần mốc thời gian khi engine là Omni.

    `n_beats` chỉ là SÀN gợi ý (≈1 mốc / 2s), không phải trần — Omni nhận bao nhiêu mốc cũng
    được miễn là hợp logic, nên prompt khuyến khích dày hơn nếu hành động xứng đáng."""
    if engine != "omni":
        return _MOTION
    return _MOTION + "\n\n" + _OMNI_TIMELINE_HEAD.format(
        clip_s=clip_s, n_beats=max(3, round(clip_s / 2)))


def storyboard_autofill_prompt(scene_heading: str, scene_body: str,
                               entities: list[dict], style: str,
                               n_frames: int | None = None,
                               location: str | None = None,
                               engine: str = "veo", clip_s: int = 8) -> str:
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    locations = [e["name"] for e in entities if e.get("type") == "location"]
    if location:
        loc_line = (
            f"This scene takes place at ONE fixed location: {{{location}}}. EVERY frame is at "
            f"this SAME place — begin each `description` with {{{location}}}, use ONLY "
            f"{{{location}}} and NO other location anywhere, and put {{{location}}} (and no "
            "other place) in ref_entity_names. Do NOT invent or switch to any other location."
        )
    elif locations:
        loc_line = (
            "The location entities available are: "
            + ", ".join("{" + n + "}" for n in locations)
            + ". Pick the single location this scene happens at and use ONLY it in every frame."
        )
    else:
        loc_line = (
            "No location entity exists yet — invent a consistent place name and wrap it in "
            "curly braces, reusing the SAME name for every frame of this scene."
        )
    count = f"about {n_frames} frames" if n_frames else "as many frames as the action needs (2–6)"
    return (
        "Break this scene into storyboard FRAMES (still shots) — consecutive key moments of "
        "ONE continuous take, in order. Every frame in this scene happens at ONE shared "
        "location.\n"
        f"{loc_line}\n\n"
        "For each frame return:\n"
        "- `title`: short label.\n"
        "- `continuity`: ONE sentence on how this frame follows the previous one (what the "
        "subject moved, where the camera travelled, what changed); for frame 1, what the scene "
        "opens on.\n"
        "- `description`: a vivid image-generator prompt that MUST begin by naming the "
        "location, then — for every frame after the first — the state carried over from the "
        "previous frame, then a SPECIFIC shot size + camera angle/height for THIS frame, then "
        "the action — e.g. \"At {Khu rừng}, same overcast noon light, {Mai} now at the "
        "threshold she was walking toward, low-angle medium close-up, she opens the wooden "
        "door...\". The shot size AND angle MUST DIFFER from the previous frame's (alternate "
        "wide / medium / close and change the angle/height) while still reading as the next "
        "position of the SAME camera move, so consecutive frames cut together with rhythm "
        "instead of looking like the same shot repeated.\n"
        "- `visual_prompt`: the full camera setup + what is on screen for an image-to-video "
        "model — keep the SAME entity references.\n"
        "- `motion_prompt`: the camera move + the concrete action that happens during the "
        "clip, referencing the SAME entities.\n"
        "- `ref_entity_names`: every entity used in the frame (names WITHOUT braces), and it "
        "MUST include the scene's location.\n"
        f"\n{_CINE}\n\n{_CONTINUITY}\n\n{motion_spec(engine, clip_s)}\n\n"
        "IMPORTANT: whenever a known entity (character/location/prop) appears in ANY prompt, "
        "wrap its name in curly braces exactly as listed (e.g. {Mai}) so it binds to its "
        "reference image.\n"
        f"Visual style: {style}. Produce {count}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\n"
        f"SCENE: {scene_heading}\n{scene_body}\n\n"
        "Return ONLY JSON array: [{\"title\":\"...\",\"continuity\":\"...\",\"description\":"
        "\"At {Location}, <carried-over state>, <angle>, ... {Entity} ...\",\"visual_prompt\":"
        "\"...\",\"motion_prompt\":\"...\",\"ref_entity_names\":[\"Location\",\"Entity\"]}]"
    )


def clip_timeline_prompt(frames: list[dict], clip_s: int, scene_heading: str = "",
                         style: str = "") -> str:
    """Write ONE video prompt that travels through several storyboard frames.

    The Shots tab groups consecutive storyboard frames into a single clip: every frame image is
    attached as an Omni Flash reference whose HANDLE is that frame's `media_name`
    (`sc001-s01-…`), and the prompt is a timestamped take that passes through them.

    The frames must be named as `{sc001-s01-…}` tokens — curly braces are the ONLY syntax Flow
    binds (`flow_client._build_structured_parts` turns each `{handle}` into a reference part).
    A frame written any other way is plain text: the image would still ride along as an
    imageInput, but nothing tells the model WHICH moment belongs to WHICH reference.

    What this prompt deliberately does NOT do is dictate the camera work. Handing the model a
    menu of moves ("tracks back", "pushes in") got every clip written to the same template, and
    turned the frames into a checklist to tick off rather than moments to play. The frames say
    where the take must pass through; the model chooses how it gets there — including not
    moving at all."""
    n = len(frames)
    names = [(f.get("media_name") or f"frame-{i+1}") for i, f in enumerate(frames)]
    listing = "\n".join(
        f"{{{names[i]}}} — {(f.get('title') or '').strip()}"
        + (f" · {(f.get('continuity') or '').strip()}" if f.get("continuity") else "")
        + f"\n    {(f.get('description') or '').strip()}"
        for i, f in enumerate(frames))
    token_list = ", ".join("{" + x + "}" for x in names)
    timeline = _OMNI_TIMELINE_HEAD.format(clip_s=clip_s, n_beats=max(3, round(clip_s / 2)))
    return (
        f"You are the cinematographer for ONE {clip_s}-second continuous take. {n} storyboard "
        "frames of this take are attached as reference images, in order — each is what the shot "
        "LOOKS LIKE at one moment of it. Write the motion prompt for the whole take.\n\n"
        f"{timeline}\n\n"
        "USING THE FRAMES:\n"
        f"  • Refer to a frame by its EXACT token, in this order: {token_list}. Copy each token "
        "character for character, keep the curly braces, and do not renumber, translate or "
        "shorten them — the braces are what binds the image to that moment. Every token must "
        "appear at least once and the take must reach the LAST one before it ends.\n"
        "  • A token is the LOOK of that moment, not an editing instruction. Write the moment "
        "itself and let the token sit inside it. Never write stage directions about the frames "
        "— no 'cut to', 'transition to', 'move to the next frame', 'tighten to the framing of', "
        "no numbering or announcing them.\n"
        "  • YOU decide the motion. Read what actually happens between these frames — the "
        "action, the emotion, the space — and choose the camera behaviour that shot deserves, "
        "including holding still when stillness is what it deserves. Do not reach for a stock "
        "move, do not apply the same move to every clip, and do not invent motion merely to "
        "fill the seconds. The frames tell you where the take has to pass through; how it gets "
        "there is your call.\n"
        "  • The frames already fix costume, light, weather and place. Do NOT restate the style "
        "or re-invent the setting — only what MOVES.\n"
        + (f"\nVisual style (context only, do not restate): {style}.\n" if style else "")
        + (f"\nSCENE: {scene_heading}\n" if scene_heading else "")
        + f"\nFRAMES:\n{listing}\n\n"
        "Return ONLY JSON: {\"motion_prompt\":\"[00:00] ... "
        + "{" + names[0] + "} ... " + "{" + names[-1] + "} ...\"}"
    )


def sheet_autofill_prompt(scene_heading: str, scene_body: str, entities: list[dict],
                          style: str, panels: int = 6, n_sheets: int | None = None,
                          location: str | None = None) -> str:
    """Chia scene thành các TRANG storyboard, mỗi trang đúng `panels` panel (tab Storyboard).

    Khác `storyboard_autofill_prompt` ở hai chỗ. Một, số panel KHÔNG tự do: lưới đã cố định nên
    thiếu panel là trang có ô trống, thừa là mất khoảnh khắc. Hai, các panel của MỘT trang được
    vẽ trong cùng một lượt và sau đó thành MỘT clip, nên chúng phải là một cú máy liên tục;
    ranh giới giữa hai TRANG mới là chỗ được đổi chỗ đứng, đổi nhịp.

    `caption` là dòng chữ NHỎ in dưới panel trong chính bức ảnh — phải là cỡ cảnh tiếng Việt,
    một dòng, vì đó là thứ người xem storyboard đọc để biết panel này là cú gì."""
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    locations = [e["name"] for e in entities if e.get("type") == "location"]
    if location:
        loc_line = (
            f"This scene takes place at ONE fixed location: {{{location}}}. EVERY panel of every "
            f"page is at this SAME place — name {{{location}}} in the panel descriptions, use "
            f"ONLY it, and put it in ref_entity_names. Do NOT invent or switch to another place.")
    elif locations:
        loc_line = ("The location entities available are: "
                    + ", ".join("{" + n + "}" for n in locations)
                    + ". Pick the single location this scene happens at and use ONLY it.")
    else:
        loc_line = ("No location entity exists yet — invent a consistent place name, wrap it in "
                    "curly braces and reuse the SAME name everywhere in this scene.")
    count = (f"exactly {n_sheets} pages" if n_sheets
             else "as many pages as the action needs (1–3)")
    sizes = ", ".join(PANEL_SHOT_SIZES[:6])
    return (
        f"Break this scene into STORYBOARD PAGES. Each page is ONE drawing laid out as "
        f"{panels} numbered panels, so each page needs EXACTLY {panels} panels — never fewer, "
        "never more.\n"
        f"{loc_line}\n\n"
        f"The {panels} panels of a page are {panels} consecutive moments of ONE unbroken take. "
        "They are drawn in the SAME pass and later become ONE continuous video clip, so within a "
        "page the place, the light, the weather and the costumes cannot change at all — only the "
        "camera and the action move. A NEW page is where a bigger jump is allowed.\n\n"
        "For each page return:\n"
        "- `title`: short label for the whole page.\n"
        f"- `panels`: a list of exactly {panels} objects, in reading order (left to right, top to "
        "bottom), each with:\n"
        f"  · `caption`: the ONE-LINE Vietnamese shot-size label printed under the panel — one of "
        f"{sizes}, or a similarly short term. This is drawn INSIDE the image, keep it very short.\n"
        "  · `shot_size`: English shot size for the camera spec, e.g. Wide / Medium / Close-up / "
        "Full-body / Rear.\n"
        "  · `lens`: focal length, e.g. 24mm / 35mm / 50mm / 85mm.\n"
        "  · `movement`: the camera behaviour for THIS moment, e.g. tracking back / low angle / "
        "rear follow / shallow DOF. Leave \"\" if the moment is a static hold.\n"
        "  · `description`: ONE sentence of ACTION ONLY — what the subject does and how it is "
        "posed in this panel. The place is supplied to the image model as a reference PICTURE "
        "and is stated once for the whole page, so describing it again in a panel is not "
        "harmless repetition: your words compete with that picture and the model redraws the "
        "street from your words instead. So write NOTHING about the street, buildings, stalls, "
        "signage, weather, time of day or lighting.\n"
        "    BAD: \"Mở đầu trên {Phố X} dưới cơn mưa đêm, hàng đèn lồng đỏ rực rỡ phản chiếu "
        "xuống mặt đường ướt đẫm, cô gái bước đi.\"\n"
        "    GOOD: \"{An} bước chậm về phía máy quay, tay nâng nhẹ {Ô trong suốt}, đầu hơi "
        "nghiêng, ánh mắt dõi sang bên phải.\"\n"
        "    Name entities with {braces}.\n"
        "  · `ref_entity_names`: every entity in that panel (names WITHOUT braces), always "
        "including the location.\n"
        f"\n{_CINE}\n\n{_CONTINUITY}\n\n"
        "IMPORTANT: whenever a known entity (character/location/prop) appears in ANY prompt, "
        "wrap its name in curly braces exactly as listed (e.g. {Mai}) so it binds to its "
        "reference image.\n"
        f"Visual style: {style}. Produce {count}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\n"
        f"SCENE: {scene_heading}\n{scene_body}\n\n"
        "Return ONLY JSON array: [{\"title\":\"...\",\"panels\":[{\"caption\":\"toàn cảnh\","
        "\"shot_size\":\"Wide\",\"lens\":\"24mm\",\"movement\":\"tracking back\","
        "\"description\":\"...\",\"ref_entity_names\":[\"Location\"]}]}]"
    )


def sheet_timeline_prompt(panels: list[dict], clip_s: int, scene_heading: str = "",
                          style: str = "") -> str:
    """Prompt cho MỘT clip đi xuyên các panel của một trang storyboard.

    Khác `clip_timeline_prompt` ở chỗ căn bản: ở đó mỗi frame là một ẢNH RIÊNG và được gọi bằng
    token `{media_name}` để Flow bind thành reference part. Ở đây chỉ có ĐÚNG MỘT ảnh — cả trang
    — nên không có gì để bind; thứ chỉ ra panel nào là badge số tròn VẼ SẴN TRONG ẢNH. Vì vậy
    prompt phải gọi "panel 1..N" chứ không phải `{token}`, và tuyệt đối không được viết `{}`
    quanh chúng (Flow sẽ đi tìm một reference không tồn tại rồi bỏ token đó thành chữ trơ).

    Nguy hiểm lớn nhất của cách này: đưa một TRANG storyboard cho model video thì nó rất dễ làm
    đúng nghĩa đen — cho cái trang đó chuyển động, lưới và caption và tất cả. Nên câu đầu tiên
    phải nói rõ trang là BẢN VẼ KẾ HOẠCH, không phải khung hình cần dựng lại."""
    n = len(panels)
    listing = "\n".join(
        f"  Panel {i + 1}"
        + (f" [{p.get('shot_size') or ''}"
           + (f", {p['lens']}" if p.get("lens") else "")
           + (f", {p['movement']}" if p.get("movement") else "") + "]"
           if p.get("shot_size") else "")
        + f": {(p.get('description') or p.get('caption') or '').strip()}"
        for i, p in enumerate(panels))
    timeline = _OMNI_TIMELINE_HEAD.format(clip_s=clip_s, n_beats=max(n, round(clip_s / 2)))
    return (
        f"You are the cinematographer for ONE {clip_s}-second continuous take.\n\n"
        "THE REFERENCE IMAGE IS A STORYBOARD PAGE, NOT A SHOT. It shows this take drawn as "
        f"{n} numbered panels laid out in a grid, each with a small round number badge and a "
        "one-line caption. It is a PLAN of the take. The video must be the take itself: one "
        "full-frame continuous shot that passes through what panel 1, then panel 2, and so on "
        f"up to panel {n} depict. NEVER animate the page: no grid, no panel borders, no split "
        "screen, no badges, no caption text, no page margins anywhere in the video, and never "
        "show two panels at once.\n\n"
        f"{timeline}\n\n"
        "USING THE PANELS:\n"
        f"  • Work through the panels IN ORDER, 1 to {n}, and reach panel {n} before the take "
        "ends. Each panel is what the shot LOOKS LIKE at one moment — its framing, its pose, its "
        "moment of the action.\n"
        "  • Refer to a panel as `panel 3` in plain words if you must, but prefer writing the "
        "MOMENT itself. Never wrap a panel number in curly braces, never write 'cut to panel 4', "
        "'transition', or any stage direction about the storyboard — the viewer must not be able "
        "to tell the take was planned on a page.\n"
        # Không kê sẵn nước máy — cùng lý do với clip_timeline_prompt (xem CLAUDE.md): đưa menu
        # vào thì mọi clip ra một khuôn và các panel biến thành checklist để tick. Thông số máy
        # ghi trên panel là mô tả KHOẢNH KHẮC ĐÓ TRÔNG RA SAO, không phải lệnh điều khiển.
        "  • A panel's shot size and lens say what that MOMENT looks like. How the camera gets "
        "from one moment to the next is entirely YOUR call: read what actually happens between "
        "them — the action, the emotion, the space — and choose the movement that passage "
        "deserves, including holding still when stillness is what it deserves. Do not reach for "
        "a stock move, do not apply the same move to every clip, and do not invent motion merely "
        "to fill seconds.\n"
        "  • The one thing that is NOT negotiable is that it must make physical sense: the "
        "subject cannot teleport, turn around or change hands between two moments without the "
        "movement being shown, and the camera cannot jump to a position it had no way of "
        "reaching. Every transition has to be something a real operator and a real actor could "
        "actually have done in the time it is given.\n"
        # Chia đều thời lượng cho các panel là cách chắc chắn giết nhịp: một cái liếc mắt xong
        # trong 0.6s, một cú đi bộ qua cổng cần 3s. Cue phải bám hành động, không bám số học.
        f"  • The panels do NOT get equal time. Give each one exactly as long as its action "
        f"physically needs — a glance may take under a second, walking through a gateway may "
        f"take three — so the gaps between cues are uneven by design. Never space the cues "
        f"evenly just to divide {clip_s}s by {n}. A panel may also earn more than one cue if "
        "something distinct happens inside it.\n"
        "  • The page already fixes costume, light, weather and place. Do NOT restate the style "
        "or re-invent the setting — write only what MOVES.\n"
        + (f"\nVisual style (context only, do not restate): {style}.\n" if style else "")
        + (f"\nSCENE: {scene_heading}\n" if scene_heading else "")
        + f"\nPANELS:\n{listing}\n\n"
        "Return ONLY JSON: {\"motion_prompt\":\"[00:00] ... [00:0X] ...\"}"
    )


# A terminator (.!?…) ends a sentence ONLY when followed by whitespace or end-of-string
# (optionally after closing quotes/brackets). A '.' glued to the next char — a filename
# "ACC_REPORT...2047.zip", a decimal, a version, a glued abbreviation — is NOT a boundary,
# so the sentence is never cut mid-token. Newlines always break.
_SENT_RE = re.compile(r".*?(?:[.!?…]+[\"'’”\)\]]*(?=\s|$)|\n|$)", re.S)


def _sentences(text: str) -> list[str]:
    # Drop fragments with no readable word (a standalone "◆", a row of bullets) so decoration
    # never becomes its own contiguous part → its own beat → a wasted shot + 0.8s of noise.
    return [s.strip() for s in _SENT_RE.findall(text or "")
            if s.strip() and vntext.has_words(s)]


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


_CLAUSE_RE = re.compile(r"(?<=[,;:—–])\s+")     # split points inside an over-long sentence


def _split_long_sentence(sent: str, max_words: int) -> list[str]:
    """Split ONE over-long sentence into ≤max_words pieces at clause boundaries (, ; : — –),
    hard word-splitting any clause that is still too long. Verbatim (only whitespace
    normalized), so the pieces concatenate back to the sentence."""
    out: list[str] = []
    for cl in _CLAUSE_RE.split(sent):
        words = cl.split()
        if len(words) <= max_words:
            out.append(cl)
        else:                                   # a single clause too long → hard word-split
            for k in range(0, len(words), max_words):
                out.append(" ".join(words[k:k + max_words]))
    return out or [sent]


# Vietnamese narration rate of the TTS voice, words per second. Measured over 95 built scenes
# (words ÷ scene WAV duration, incl. its pauses): ~3.4 for the continuous-read v2 takes. The old
# 2.5 under-counted by ~35%, which inflated every duration estimate → too many beats, and made a
# "10s" chunk actually run ~7s. Override with FLOWKIT_WORDS_PER_SEC.
WORDS_PER_SEC = float(os.environ.get("FLOWKIT_WORDS_PER_SEC", "3.4"))


def chunk_by_duration(text: str, max_secs: float = 10.0, min_secs: float = 8.0,
                      wps: float = WORDS_PER_SEC) -> list[str]:
    """Split `text` into contiguous, VERBATIM chunks that each AIM for the [min_secs, max_secs]
    band of narration — one shot (and so one generated image) per chunk.

    Sentences are the base unit; a sentence longer than the budget is further split at CLAUSE
    boundaries (, ; : —) then by word count. Pieces are then PACKED to FILL the band: a chunk is
    only closed once it has reached `min_secs` worth of words, so we stop emitting the swarm of
    3–5s shots that made the image count explode. When a piece would overflow `max_secs` while
    the chunk is still under the minimum, we keep whichever choice lands nearer the band's middle.
    A tiny trailing chunk is folded back into the previous one rather than left as a stray shot.

    Concatenating the chunks back gives the whole text (whitespace normalized) — never rewrites
    or drops content."""
    text = (text or "").strip()
    if not text:
        return []
    max_words = max(3, round(max_secs * wps))
    min_words = max(2, min(round(min_secs * wps), max_words))
    target_words = (min_words + max_words) // 2
    pieces: list[str] = []
    for s in _sentences(text):
        if len(s.split()) <= max_words:
            pieces.append(s)
        else:
            pieces.extend(_split_long_sentence(s, max_words))
    out: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for p in pieces:
        w = len(p.split())
        if cur and cur_w + w > max_words:
            # Over the cap. Close only if the chunk already fills the band, or if closing lands
            # strictly nearer the target than overflowing would — otherwise keep packing (a
            # slightly long shot beats a stray 3s one, and ties favour packing).
            if cur_w >= min_words or abs(cur_w - target_words) < abs(cur_w + w - target_words):
                out.append(" ".join(cur))
                cur, cur_w = [], 0
        cur.append(p)
        cur_w += w
    if cur:
        # A trailing chunk under the minimum reads as a stray short shot. Fold it into the
        # previous one when that stays within a reasonable overshoot; else keep it standalone.
        prev_w = len(out[-1].split()) if out else 0
        if out and cur_w < min_words and prev_w + cur_w <= round(max_words * 1.25):
            out[-1] = f"{out[-1]} {' '.join(cur)}"
        else:
            out.append(" ".join(cur))
    return out or [text]


async def align_source_to_scenes(source: str, scenes: list[dict]) -> list[str]:
    """Assign the original SOURCE prose to scenes BY CONTENT (not by equal length). Each scene
    gets a contiguous, verbatim block of source sentences that matches its location heading /
    action, in order; together the slices cover the whole source with no gaps or overlaps.
    Returns one slice per scene (len == len(scenes)).

    Robust by construction: the AI only picks the sentence index where each scene ENDS, and we
    slice on those boundaries — so the text is never paraphrased and the union is always the
    complete source. Falls back to length-balanced partition_text if the AI reply is unusable."""
    sents = _sentences(source)
    n = len(scenes)
    total = len(sents)
    if n <= 0:
        return []
    if n == 1 or total <= 1:
        return [" ".join(sents)] + [""] * (n - 1)
    if total <= n:                                   # fewer sentences than scenes → one each
        return [sents[i] if i < total else "" for i in range(n)]

    numbered = "\n".join(f"[{i + 1}] {s}" for i, s in enumerate(sents))
    scene_lines = "\n".join(
        f"- Scene {i + 1}: {sc.get('heading') or ''} :: {((sc.get('action') or '')[:200])}"
        for i, sc in enumerate(scenes))
    prompt = (
        "You align an original SOURCE narration to a list of SCENES. The SOURCE below is split "
        "into NUMBERED sentences. Each scene covers a CONTIGUOUS block of sentences IN ORDER; "
        "together the scenes MUST cover EVERY sentence with no gaps or overlaps. Using each "
        "scene's location heading and action summary, keep every sentence with the scene whose "
        "LOCATION/EVENT it actually describes (a change of place starts a new scene's block).\n\n"
        f"Return ONLY a JSON array of {n} integers: the 1-based index of the LAST sentence of "
        f"each scene. Values MUST be strictly increasing and the final value MUST equal {total}."
        f"\n\nSCENES:\n{scene_lines}\n\nSOURCE SENTENCES:\n{numbered}"
    )
    def _ok(data):
        try:
            return len(data) == n and all(isinstance(int(x), int) for x in data)
        except Exception:  # noqa: BLE001
            return False

    try:
        raw = await run_json_valid(prompt, _ok, label="Căn nội dung→scene")
        ends = [int(x) for x in raw]
    except Exception as e:  # noqa: BLE001 — exhausted retries → safe length-based fallback
        logger.warning("source→scene align failed after retries (%s) — dùng chia đều", e)
        return partition_text(source, n)
    # sanitize: clamp into range, force strictly-increasing, ≥1 sentence per scene, last=total
    fixed: list[int] = []
    prev = 0
    for i, e in enumerate(ends):
        lo = prev + 1                                # ≥1 sentence after the previous scene
        hi = total - (n - 1 - i)                     # leave ≥1 sentence for each remaining scene
        e = max(lo, min(e, hi))
        fixed.append(e)
        prev = e
    fixed[-1] = total
    out, start = [], 0
    for e in fixed:
        out.append(" ".join(sents[start:e]))
        start = e
    return out


def scene_plan_prompt(voiceover: str, entities: list[dict], style: str,
                      location: str | None = None) -> str:
    """Read the WHOLE scene first and produce a short shot PLAN, so the shots that follow are
    coherent (a real scene) instead of a random jumble of solo shots. Identifies who is
    physically present + where (blocking) and a camera coverage strategy. Small JSON output."""
    roster = "\n".join(
        f"- {e['name']} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    return (
        "You are a film director. Read this ENTIRE scene voiceover and return a SHORT plan so "
        "the storyboard shots stay coherent — same place, same people, consistent spatial "
        "relationships — instead of disconnected solo shots.\n"
        f"Location: {location or 'one consistent place (name it)'}. Visual style: {style}.\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\nVOICEOVER:\n{voiceover}\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "present": ["names of entities PHYSICALLY in this scene"],\n'
        '  "blocking": "one sentence: where each person/object is and their spatial relation",\n'
        '  "coverage": "one sentence: how to shoot it — e.g. establishing wide, then '
        'over-the-shoulder between the two speakers, reaction close-ups, inserts of the screen"\n'
        "}"
    )


def scene_segment_prompt(voiceover: str, entities: list[dict], style: str,
                         location: str | None = None, target_beats: int | None = None,
                         plan: dict | None = None,
                         engine: str = "veo", clip_s: int = 8) -> str:
    """Split an ALREADY-WRITTEN scene voiceover into visual BEATS. Each beat's `text` is a
    verbatim CONTIGUOUS slice of the voiceover (in order, concatenating back to the whole),
    so each beat's share of the audio time can be derived from its word count. Also pick the
    key phrases to flash on screen when the narration reaches them."""
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    locations = [e["name"] for e in entities if e.get("type") == "location"]
    if location:
        loc_line = (
            f"This scene is at ONE fixed location: {{{location}}}. EVERY beat is at this SAME "
            f"place — begin each `description` with {{{location}}}, use ONLY {{{location}}} and "
            f"NO other location, and put {{{location}}} (and no other place) in ref_entity_names."
        )
    elif locations:
        loc_line = (
            "Location entities available: " + ", ".join("{" + n + "}" for n in locations)
            + ". Every beat is at the ONE location of this scene; use ONLY that one."
        )
    else:
        loc_line = (
            "No location entity yet — invent ONE consistent place name in curly braces and "
            "reuse it for every beat."
        )
    count_line = (
        f"Aim for ABOUT {target_beats} beats — each on-screen image should last 8–10 seconds of "
        "narration. That is fresh enough to keep the viewer engaged, while each beat costs one "
        "generated image, so do NOT over-split. Split at natural sentence/clause boundaries; a "
        "beat is usually 2–3 sentences. Avoid beats shorter than ~8 seconds: merge a short "
        "thought into its neighbour rather than emitting a tiny beat."
        if target_beats else
        "Each beat should cover one on-screen moment worth 8–10 seconds of narration (usually "
        "2–3 sentences). Each beat costs one generated image, so avoid tiny beats — merge a "
        "short thought into its neighbour instead of over-splitting."
    )
    plan_line = ""
    if plan and (plan.get("blocking") or plan.get("coverage")):
        present = ", ".join(plan.get("present") or [])
        plan_line = (
            "SCENE PLAN — OBEY IT so the beats form ONE coherent scene, not disconnected solo "
            "shots:\n"
            + (f"- People present the WHOLE scene: {present}. Keep them consistent; do NOT drop "
               "a present person or invent someone not listed.\n" if present else "")
            + (f"- Blocking / space: {plan['blocking']}\n" if plan.get("blocking") else "")
            + (f"- Camera coverage: {plan['coverage']}\n" if plan.get("coverage") else "")
            + "Establish the space early, then VARY framing across beats (wide → medium → "
            "over-the-shoulder → reaction/insert) while respecting who is where. In a dialogue, "
            "alternate over-the-shoulder + reaction shots of BOTH speakers — never a random "
            "string of one-person shots.\n\n"
        )
    return (
        "Split this scene VOICEOVER into visual BEATS (one beat = one on-screen moment). "
        "Do NOT rewrite the narration — each beat's `text` MUST be a verbatim, contiguous "
        "slice of the voiceover, and the slices in order MUST concatenate back to the whole "
        "voiceover (no gaps, no overlaps).\n"
        f"{count_line}\n"
        f"{plan_line}"
        f"{loc_line}\n\n"
        "For each beat return:\n"
        "- `text`: the verbatim voiceover slice for this beat.\n"
        "- `beat_action`: the concrete action happening on screen.\n"
        "- `continuity`: ONE sentence on how this beat follows the previous one (what moved, "
        "where the camera went, what changed); for beat 1, what the scene opens on.\n"
        "- `description`: image prompt beginning with the location, then — for every beat after "
        "the first — the state carried over from the previous beat, then a SPECIFIC shot size + "
        "camera angle/height (which MUST DIFFER from the previous beat's — alternate "
        "wide/medium/close and change the angle so beats don't look like one repeated shot), "
        "then the action, e.g. \"At {Làng}, same grey dawn light, {Tấm} still at the porch she "
        "knelt on, low-angle wide shot, she scrubs the boards...\".\n"
        "- `visual_prompt`: the full camera setup + what is on screen (same entity refs).\n"
        "- `motion_prompt`: camera move + action during the clip (same entity refs).\n"
        "- `ref_entity_names`: entity names WITHOUT braces, MUST include the location.\n"
        "- `key_phrases`: 1–3 SHORT punchy phrases taken VERBATIM from this beat's `text` "
        "(the words worth flashing on screen as captions); [] if none.\n\n"
        f"{_CINE}\n\n{_CONTINUITY}\n\n{motion_spec(engine, clip_s)}\n\n"
        f"Wrap known entity names in curly braces. Visual style: {style}.\n\n"
        f"AVAILABLE ENTITIES:\n{roster}\n\nVOICEOVER:\n{voiceover}\n\n"
        "Return ONLY JSON array: [{\"text\":\"...\",\"beat_action\":\"...\","
        "\"continuity\":\"...\",\"description\":\"At {Loc}, <carried-over state>, <angle>, ...\","
        "\"visual_prompt\":\"...\",\"motion_prompt\":\"...\",\"ref_entity_names\":[\"Loc\"],"
        "\"key_phrases\":[\"...\"]}]"
    )


def beat_parts_prompt(beat_action: str, motion_prompt: str, n_parts: int,
                      clip_s: int = 8, engine: str = "veo") -> str:
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
        f"{motion_spec(engine, clip_s)}\n\n"
        "Return ONLY JSON: {\"parts\":[{\"part_idx\":0,\"motion_prompt\":\"...\"}, ...]}"
    )


def revary_shots_prompt(shots: list[dict], entities: list[dict], style: str,
                        location: str | None = None,
                        engine: str = "veo", clip_s: int = 8) -> str:
    """Rewrite the CAMERA work of EXISTING shots without changing the story, order, count or
    per-shot action — only pick fresh, distinct angles so consecutive shots differ. Fast path
    to fix monotonous framing (and the location) without re-segmenting or re-running TTS."""
    roster = "\n".join(
        f"- {{{e['name']}}} ({e['type']}): {e.get('description') or ''}" for e in entities
    ) or "(none)"
    listing = "\n".join(
        f"{i}. {((s.get('beat_action') or s.get('narrator_text') or s.get('description') or '') or '').strip()[:300]}"
        for i, s in enumerate(shots))
    loc_line = (
        f"This scene is at ONE fixed location: {{{location}}}. EVERY shot's `description` MUST "
        f"begin with {{{location}}} and use ONLY this place — no other location anywhere.\n"
        if location else ""
    )
    return (
        f"An existing storyboard scene has {len(shots)} shots, in order, listed below by their "
        "action. Keep the story, the ORDER, the NUMBER of shots and each shot's action EXACTLY "
        "as is — change ONLY the camera so consecutive shots no longer share the same framing.\n"
        f"{loc_line}\n"
        "For EACH shot (same index, same order) return a NEW `description` (image prompt: begin "
        "with the location, then a SPECIFIC shot size + camera angle/height that DIFFERS from the "
        "previous shot, then the SAME action), plus a matching `visual_prompt` and `motion_prompt`. "
        "Wrap EVERY character/location/prop name in curly braces exactly as listed so it binds to "
        "its reference image (a character that acts in the shot MUST be wrapped and present).\n"
        f"\n{_CINE}\n\n{motion_spec(engine, clip_s)}\n\n"
        f"Visual style: {style}.\n\nAVAILABLE ENTITIES:\n{roster}\n\nSHOTS (in order):\n{listing}\n\n"
        "Return ONLY a JSON array with EXACTLY one object per shot, in order: "
        "[{\"idx\":0,\"description\":\"At {Loc}, <distinct shot size+angle>, <same action> {Entity}...\","
        "\"visual_prompt\":\"...\",\"motion_prompt\":\"...\"}]"
    )


def shot_prompts_prompt(description: str, style: str,
                        engine: str = "veo", clip_s: int = 8) -> str:
    return (
        "For this storyboard frame, write two prompts for an image-to-video model:\n"
        "- `visual_prompt`: the full camera setup + what is on screen.\n"
        "- `motion_prompt`: the camera move + the action that happens during the clip "
        "(concrete, e.g. 'the fox steps onto the ice, camera slowly pushes in').\n"
        f"\n{_CINE}\n\n{motion_spec(engine, clip_s)}\n\n"
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
