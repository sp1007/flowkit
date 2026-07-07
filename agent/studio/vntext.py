"""Vietnamese text normalization for TTS.

Before sending narration to OmniVoice we expand numbers/dates/times/currency to spoken
Vietnamese and strip symbols the model would otherwise read literally (e.g. '-' → "gạch
ngang", '_' → "gạch dưới"). Then `split_segments` cuts the result into short, sentence-
aligned chunks for the TTS engine (no mid-sentence cuts, so the read stays natural).
"""
import re
import unicodedata

_ONES = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALES = ["", " nghìn", " triệu", " tỷ"]


def _read_triple(num: int, full: bool) -> str:
    """Read 0..999. `full` = this group is not the most-significant one, so it must be
    spoken in full (e.g. trailing group of 1_005 → "không trăm lẻ năm")."""
    hund, rem = divmod(num, 100)
    tens, ones = divmod(rem, 10)
    out = []
    if hund > 0 or full:
        out.append(_ONES[hund] + " trăm")
    if tens == 0:
        if ones > 0:
            out.append(("lẻ " if (hund > 0 or full) else "") + _ONES[ones])
    elif tens == 1:
        out.append("mười")
        if ones == 5:
            out.append("lăm")
        elif ones > 0:
            out.append(_ONES[ones])
    else:
        out.append(_ONES[tens] + " mươi")
        if ones == 1:
            out.append("mốt")
        elif ones == 4:
            out.append("tư")
        elif ones == 5:
            out.append("lăm")
        elif ones > 0:
            out.append(_ONES[ones])
    return " ".join(out).strip()


def int_to_words(n: int) -> str:
    if n == 0:
        return "không"
    neg, n = n < 0, abs(n)
    groups = []
    while n > 0:
        n, r = divmod(n, 1000)
        groups.append(r)
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        if groups[i] == 0:
            continue
        parts.append(_read_triple(groups[i], full=i < len(groups) - 1) + _SCALES[i])
    res = " ".join(p for p in parts if p).strip()
    return ("âm " + res) if neg else res


def _digits_words(s: str) -> str:
    return " ".join(_ONES[int(c)] for c in s if c.isdigit())


def _number_words(token: str) -> str:
    """A numeric token → words. '.' = thousands separator, ',' = decimal point (VN style);
    a lone '.' before <3 digits is treated as a decimal point too (e.g. 3.14)."""
    token = token.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", token):           # 1.000.000 → thousands
        return int_to_words(int(token.replace(".", "")))
    if "," in token:                                         # 3,14 → decimal
        ip, dp = token.split(",", 1)
        ip = ip.replace(".", "")
        dp = dp.rstrip("0") or "0"                            # 0,50 → "không phẩy năm"
        return f"{int_to_words(int(ip or 0))} phẩy {_digits_words(dp)}".strip()
    if re.fullmatch(r"\d+\.\d+", token):                     # 3.14 / 00.00 → decimal
        ip, dp = token.split(".", 1)
        dp = dp.rstrip("0") or "0"                            # 00.00 → "không phẩy không"
        return f"{int_to_words(int(ip))} phẩy {_digits_words(dp)}".strip()
    if token.isdigit():
        return int_to_words(int(token))
    return token


# ─── Special-cased patterns (run before generic number expansion) ───

def _time_sub(m: re.Match) -> str:
    h, mm = int(m.group("h")), int(m.group("m"))
    period = (m.group("p") or "").strip()
    if h > 23 or mm > 59:
        return m.group(0)
    if h == 0 and mm == 0:
        words = "không giờ"
    else:
        words = f"{int_to_words(h)} giờ"
        if mm > 0:
            words += f" {int_to_words(mm)} phút"
    if period:
        words += " " + period
    elif 0 < h < 12:                 # no explicit am/pm → default morning (per spec)
        words += " sáng"
    return words


def _date_sub(m: re.Match) -> str:
    d, mo = int(m.group("d")), int(m.group("mo"))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return m.group(0)
    day = ("mùng " if d <= 10 else "") + int_to_words(d)
    out = f"{day} tháng {int_to_words(mo)}"   # no "ngày" prefix (source often already has it)
    y = m.groupdict().get("y")                # the dd/mm (no-year) pattern has no 'y' group
    if y:
        out += f" năm {int_to_words(int(y))}"
    return out


_CURRENCY = {"đ": "đồng", "vnd": "đồng", "vnđ": "đồng", "$": "đô la", "usd": "đô la",
             "€": "ơ rô", "eur": "ơ rô", "¥": "yên", "£": "bảng"}

_ABBREV = [
    (r"\bTP\.?", "thành phố"), (r"\bTP\.HCM\b", "thành phố Hồ Chí Minh"),
    (r"\bQ\.(?=\s*\d)", "quận "), (r"\bP\.(?=\s*\d)", "phường "),
    (r"\bĐ/?C\b", "địa chỉ"), (r"\bSĐT\b", "số điện thoại"),
    (r"\bTS\b", "tiến sĩ"), (r"\bThS\b", "thạc sĩ"), (r"\bGS\b", "giáo sư"),
    (r"\bBS\b", "bác sĩ"), (r"\bv\.v\.?", "vân vân"), (r"\bvd\b", "ví dụ"),
    (r"\bkg\b", "ki lô gam"), (r"\bkm\b", "ki lô mét"), (r"\bcm\b", "xăng ti mét"),
    (r"\bm2\b", "mét vuông"), (r"\bUBND\b", "ủy ban nhân dân"),
]

_SYMBOLS = {"%": " phần trăm", "&": " và ", "+": " cộng ", "=": " bằng ",
            "@": " a còng ", "#": " ", "*": " ", "/": " trên ", "~": " ",
            "^": " ", "|": " ", "<": " ", ">": " ", "\\": " "}

# Decorative / markdown glyphs the TTS model would read literally or stumble on
# (stars, bullets, arrows, box-drawing, backticks, blockquote/heading marks…). Stripped
# to a space BEFORE everything else so "✦ ✦ ✦ # Chương 2" → "Chương 2", not spoken noise.
_DECOR = re.compile(
    r"[`✦✧✶✷✸✹✺✩✫✬✭✮✯★☆✪✦❂❉❋❅❄❆•◦‣⁃·∙▪▫■□◾◽◆◇♦●○◌►◄▶◀▸◂♥♠♣♤♧♡"
    r"※❖➤➢➣❯❮«»‹›¦§¶™►▼▲▽△▷◁☼☀�]"
)
_BULLET_LINE = re.compile(r"(?m)^[ \t]*[-*+•·]+[ \t]+")   # markdown list bullets at line start
_HRULE_LINE = re.compile(r"(?m)^[ \t]*([-*_=~])\1{2,}[ \t]*$")  # --- *** ___ === rules
# Em/en/figure dashes used as a pause or divider → comma (a spoken pause), not the glyph.
_DASHES = re.compile(r"\s*[—–―]+\s*")
# Catch-all: any NON-ASCII symbol/other char the explicit set missed (emoji, dingbats,
# arrows, private-use, format/unassigned) → space, so the TTS never reads a stray glyph.
_SYMBOL_CATS = {"So", "Sk", "Sm", "Co", "Cn", "Cf"}


def _strip_unicode_symbols(t: str) -> str:
    return "".join(
        " " if (ord(c) > 0x7F and unicodedata.category(c) in _SYMBOL_CATS) else c
        for c in t
    )


def strip_decoration(text: str) -> str:
    """Remove decorative / markdown glyphs (bullets, stars, box-drawing, em-dashes, any
    non-ASCII symbol) for DISPLAY / caption / stored-narration use — WITHOUT the full TTS
    normalization (numbers, dates etc. are left as written). Mirrors what `normalize` strips so
    a burned caption or shot title never shows a '◆' the narration won't actually read."""
    if not text:
        return ""
    t = _HRULE_LINE.sub(" ", text)
    t = _BULLET_LINE.sub("", t)
    t = _DECOR.sub(" ", t)
    t = _strip_unicode_symbols(t)
    t = _DASHES.sub(", ", t)
    t = re.sub(r"([,;:])\1+", r"\1", t)
    t = re.sub(r"\s+([,.;:!?…])", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    t = re.sub(r"^[\s,;:.\-]+", "", t)
    return t


def has_words(text: str) -> bool:
    """True if `text` has at least one readable word character once decoration is removed — so
    a pure-decoration fragment (a standalone '◆', a row of bullets) is recognised as NOT
    narration and can be dropped instead of becoming its own beat / shot / 0.8s of noise."""
    return bool(re.search(r"\w", strip_decoration(text), re.UNICODE))


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)      # a run of letters (no digits/underscore)


def _fix_allcaps(text: str) -> str:
    """Lowercase ALL-CAPS prose that the TTS mangles (garbled, sometimes silent): a caps PHRASE
    (2+ caps words in a row — a heading like 'PHÒNG KHÁCH CĂN HỘ CỦA HÙNG') or a lone caps word
    with a Vietnamese diacritic ('HÙNG', 'KHÔNG'). A LONE ASCII caps token (an acronym: VDK,
    ADMIN, TP) is kept, and code identifiers (ADMIN_OVERRIDE, DN31) are left untouched because
    their pieces are separated by '_'/digits, not a space, so they never form a caps phrase."""
    words = list(_WORD_RE.finditer(text))
    to_lower: set[int] = set()
    i = 0
    while i < len(words):
        if not (len(words[i].group()) >= 2 and words[i].group().isupper()):
            i += 1
            continue
        run = [i]                                    # extend across words joined ONLY by spaces
        while i + 1 < len(words):
            gap = text[words[i].end():words[i + 1].start()]
            nxt = words[i + 1].group()
            if gap.strip() == "" and len(nxt) >= 2 and nxt.isupper():
                run.append(i + 1)
                i += 1
            else:
                break
        has_dia = any(any(ord(c) > 127 for c in words[r].group()) for r in run)
        if len(run) >= 2 or has_dia:                 # a phrase, or a Vietnamese caps word
            to_lower.update(run)
        i += 1
    if not to_lower:
        return text
    out, last = [], 0
    for idx, w in enumerate(words):
        out.append(text[last:w.start()])
        out.append(w.group().lower() if idx in to_lower else w.group())
        last = w.end()
    out.append(text[last:])
    return "".join(out)


def normalize(text: str) -> str:
    if not text:
        return ""
    t = text

    # strip decorative/markdown noise first (rules whole-line, then bullets, then glyphs,
    # then any leftover non-ASCII symbol, then dashes → comma pause)
    t = _HRULE_LINE.sub(" ", t)
    t = _BULLET_LINE.sub("", t)
    t = _DECOR.sub(" ", t)
    t = _strip_unicode_symbols(t)
    t = _DASHES.sub(", ", t)

    # lowercase ALL-CAPS prose (headings / emphasised Vietnamese) the TTS otherwise garbles —
    # BEFORE abbreviations/acronym rules so real acronyms (TP, VDK) and code ids are preserved.
    t = _fix_allcaps(t)

    # abbreviations (longest first so TP.HCM beats TP.)
    for pat, rep in sorted(_ABBREV, key=lambda x: -len(x[0])):
        t = re.sub(pat, rep, t)

    # times: HH:MM or HHhMM only (the dot form like 00.00 is a DECIMAL, not a time), with
    # an optional trailing period word.
    period = r"(?P<p>sáng|trưa|chiều|tối|đêm)?"
    t = re.sub(rf"\b(?P<h>\d{{1,2}}):(?P<m>\d{{2}})\b\s*{period}", _time_sub, t)
    t = re.sub(rf"\b(?P<h>\d{{1,2}})h(?P<m>\d{{2}})\b\s*{period}", _time_sub, t)

    # dates: dd/mm/yyyy or dd/mm
    t = re.sub(r"\b(?P<d>\d{1,2})/(?P<mo>\d{1,2})/(?P<y>\d{2,4})\b", _date_sub, t)
    t = re.sub(r"\b(?P<d>\d{1,2})/(?P<mo>\d{1,2})\b(?!\s*\d)", _date_sub, t)

    # currency: number + unit, or leading $/€/£
    units = "|".join(re.escape(u) for u in _CURRENCY if u.isalpha() or u in "đ")
    # the unit must not be glued to a following letter (so '10 độ' isn't read as '... đồng')
    t = re.sub(rf"(\d[\d.,]*)\s*({units}|\$|€|£|¥)(?![\wđ])",
               lambda m: f"{_number_words(m.group(1))} {_CURRENCY[m.group(2).lower()]}", t,
               flags=re.IGNORECASE)
    t = re.sub(r"([$€£¥])\s*(\d[\d.,]*)",
               lambda m: f"{_number_words(m.group(2))} {_CURRENCY[m.group(1)]}", t)

    # percent
    t = re.sub(r"(\d[\d.,]*)\s*%", lambda m: f"{_number_words(m.group(1))} phần trăm", t)

    # number ranges: 5-10 → "từ năm đến mười" (both sides numeric; absorb an existing "từ")
    t = re.sub(r"(?<![\w.])(?:từ\s+)?(\d[\d.,]*)\s*-\s*(\d[\d.,]*)(?![\w.])",
               lambda m: f"từ {_number_words(m.group(1))} đến {_number_words(m.group(2))}", t)

    # Uppercase code identifiers (ADMIN_OVERRIDE, FILE_ACCESS, ADMIN_003) — SPEAK the '_' as
    # "gạch dưới": between two shouty ALL-CAPS tokens the TTS otherwise slurs or drops the join
    # (loses sound). Must run BEFORE the generic underscore→space rule below.
    t = re.sub(r"(?<=[A-Z0-9])_(?=[A-Z0-9])", " gạch dưới ", t)

    # alnum codes: drop the remaining hyphen/underscore so it isn't read as "gạch ngang/dưới"
    # (DN-31 → "DN 31", file_name → "file name")
    t = re.sub(r"(?<=\w)[-_](?=\w)", " ", t)

    # remaining standalone numbers → words
    t = re.sub(r"\d[\d.,]*\d|\d", lambda m: _number_words(m.group(0)), t)

    # leftover symbols
    for sym, rep in _SYMBOLS.items():
        t = t.replace(sym, rep)
    t = t.replace("-", " ").replace("_", " ")

    # tidy whitespace + spaces before punctuation
    t = re.sub(r"\s+([,.;:!?…])", r"\1", t)
    t = re.sub(r"([,;:])\1+", r"\1", t)            # collapse repeated commas (from dashes)
    t = re.sub(r"[ \t]+", " ", t).strip()
    t = re.sub(r"^[\s,;:.\-]+", "", t)             # drop leading separators (e.g. a lead comma)
    return t


# A terminator (.!?…) ends a sentence ONLY when followed by whitespace or end-of-string
# (optionally after closing quotes/brackets). A '.' glued to the next char — a filename
# "...2047.zip", a decimal, a version, a glued abbreviation — is NOT a boundary, so the
# sentence is never cut mid-token (e.g. "...2047." + "zip ..."). Newlines always break.
_SENT_RE = re.compile(r".*?(?:[.!?…]+[\"'’”\)\]]*(?=\s|$)|\n|$)", re.S)


def sentences(text: str) -> list[str]:
    """Split text into individual sentences (keeping the end punctuation). Used to TTS one
    sentence at a time so the engine pauses at every '.'/'!'/'?'/'…' instead of running them
    together. Unlike `split_segments`, this does NOT regroup short sentences."""
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_RE.findall(text) if s.strip()]


def split_segments(text: str, max_chars: int = 280) -> list[str]:
    """Split normalized text into short, sentence-aligned segments for the TTS engine."""
    text = (text or "").strip()
    if not text:
        return []
    # break after sentence-ending punctuation, keep the punctuation
    sentences = [s.strip() for s in _SENT_RE.findall(text) if s.strip()]
    segs, cur = [], ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > max_chars:
            segs.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        segs.append(cur)
    return segs
