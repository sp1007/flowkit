"""Local image processing for the Node Editor's non-AI nodes (filter / text / upscale /
blend). Pure Pillow — no Flow call. The graph executor runs these, then re-uploads the
result to Flow so a processed image still has a media_id and the chain (→ edit/video/
output) keeps working.

Every function takes/returns a PIL.Image (RGB) and never raises on out-of-range inputs —
values are clamped so a bad slider can't crash a run.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def apply_filter(img, d: dict):
    """Color/tone adjustments + geometric transforms, all optional (identity by default).
    Order: grayscale/sepia → enhance (brightness/contrast/saturation/sharpness) → blur →
    rotate → flip. `d` keys: brightness, contrast, saturation, sharpness (1.0 = no change,
    0–2), blur (0–20 px), grayscale/sepia (bool), rotate (0/90/180/270), flip_h, flip_v."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    img = img.convert("RGB")
    if d.get("auto"):                       # one-click auto contrast/levels
        img = ImageOps.autocontrast(img, cutoff=1)
    if d.get("grayscale"):
        img = ImageOps.grayscale(img).convert("RGB")
    if d.get("sepia"):
        g = ImageOps.grayscale(img)
        img = ImageOps.colorize(g, black=(20, 12, 0), white=(255, 230, 190)).convert("RGB")

    for key, enh in (("brightness", ImageEnhance.Brightness),
                     ("contrast", ImageEnhance.Contrast),
                     ("saturation", ImageEnhance.Color),
                     ("sharpness", ImageEnhance.Sharpness)):
        f = _clamp(d.get(key, 1.0), 0.0, 2.0, 1.0)
        if abs(f - 1.0) > 1e-3:
            img = enh(img).enhance(f)

    blur = _clamp(d.get("blur", 0), 0.0, 20.0, 0.0)
    if blur > 0.1:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))

    rot = int(_clamp(d.get("rotate", 0), 0, 359, 0))
    if rot in (90, 180, 270):
        img = img.rotate(-rot, expand=True)           # clockwise, PIL rotates CCW
    elif rot:
        img = img.rotate(-rot, expand=True, fillcolor=(0, 0, 0))

    if d.get("flip_h"):
        img = ImageOps.mirror(img)
    if d.get("flip_v"):
        img = ImageOps.flip(img)
    return img


def overlay_text(img, d: dict, font_path: str | None):
    """Burn a text caption onto the image. `d`: text, anchor (top/center/bottom + optional
    -left/-right), font_scale (0.02–0.2 of width), color (#hex), stroke (bool)."""
    from PIL import Image, ImageDraw, ImageFont

    img = img.convert("RGB")
    text = (d.get("text") or "").strip()
    if not text:
        return img
    W, H = img.size
    scale = _clamp(d.get("font_scale", 0.06), 0.02, 0.30, 0.06)
    size = max(12, int(W * scale))
    try:
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    except Exception:  # noqa: BLE001 — font load must never break a run
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(img)

    # wrap to ~90% width
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) > W * 0.9 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)

    line_h = size + max(4, size // 5)
    block_h = line_h * len(lines)
    anchor = (d.get("anchor") or "bottom").lower()
    pad = int(H * 0.04) + max(8, size // 4)
    if "top" in anchor:
        y = pad
    elif "center" in anchor or "middle" in anchor:
        y = (H - block_h) // 2
    else:
        y = H - block_h - pad

    color = d.get("color") or "#ffffff"
    stroke_w = max(2, size // 12) if d.get("stroke", True) else 0
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        if "left" in anchor:
            x = pad
        elif "right" in anchor:
            x = W - tw - pad
        else:
            x = (W - tw) // 2
        draw.text((x, y), ln, font=font, fill=color,
                  stroke_width=stroke_w, stroke_fill="#000000")
        y += line_h
    return img


def upscale(img, d: dict):
    """Resize by `scale` (1.5–4) with LANCZOS, optional unsharp mask. Capped at 4096px so a
    huge upscale can't blow up memory."""
    from PIL import Image, ImageFilter

    img = img.convert("RGB")
    scale = _clamp(d.get("scale", 2), 1.0, 4.0, 2.0)
    W, H = img.size
    nw, nh = int(W * scale), int(H * scale)
    cap = 4096
    if max(nw, nh) > cap:
        r = cap / max(nw, nh)
        nw, nh = int(nw * r), int(nh * r)
    img = img.resize((max(1, nw), max(1, nh)), Image.LANCZOS)
    if d.get("sharpen", True):
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
    return img


_CROP_RATIOS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4}


def crop(img, d: dict):
    """Center-crop to a target aspect ratio. d.aspect: 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / free
    ('free' or unknown → keep ratio). d.zoom (1–3) crops in further (punch-in) first."""
    img = img.convert("RGB")
    W, H = img.size
    zoom = _clamp(d.get("zoom", 1), 1.0, 3.0, 1.0)
    if zoom > 1.001:
        cw, ch = int(W / zoom), int(H / zoom)
        l, t = (W - cw) // 2, (H - ch) // 2
        img = img.crop((l, t, l + cw, t + ch))
        W, H = img.size
    target = _CROP_RATIOS.get(d.get("aspect") or "free")
    if not target:
        return img
    cur = W / H
    if cur > target:                       # too wide → trim width
        nw = max(1, int(round(H * target)))
        l = (W - nw) // 2
        img = img.crop((l, 0, l + nw, H))
    elif cur < target:                     # too tall → trim height
        nh = max(1, int(round(W / target)))
        t = (H - nh) // 2
        img = img.crop((0, t, W, t + nh))
    return img


def border(img, d: dict):
    """Add a solid border/frame around the image. d.width = fraction of the short side
    (0–0.25), d.color = #hex. width 0 → unchanged."""
    from PIL import ImageOps

    img = img.convert("RGB")
    W, H = img.size
    frac = _clamp(d.get("width", 0.04), 0.0, 0.25, 0.04)
    px = int(round(min(W, H) * frac))
    if px <= 0:
        return img
    return ImageOps.expand(img, border=px, fill=d.get("color") or "#000000")


def vignette(img, d: dict):
    """Darken the edges with a soft radial falloff. d.strength 0–1 (default 0.5)."""
    from PIL import Image, ImageDraw, ImageFilter

    img = img.convert("RGB")
    strength = _clamp(d.get("strength", 0.5), 0.0, 1.0, 0.5)
    if strength <= 0.01:
        return img
    W, H = img.size
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse(
        [-W * 0.12, -H * 0.12, W * 1.12, H * 1.12], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(8, min(W, H) * 0.15)))
    faded = Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), mask)
    return Image.blend(img, faded, strength)


def blend(img_a, img_b, d: dict):
    """Combine two images. mode 'alpha' = cross-fade (alpha 0–1, b over a); mode 'side' =
    place side by side (horizontal) on matched height."""
    from PIL import Image

    a = img_a.convert("RGB")
    b = img_b.convert("RGB")
    mode = (d.get("mode") or "alpha").lower()
    if mode == "side":
        h = min(a.height, b.height)
        a = a.resize((int(a.width * h / a.height), h), Image.LANCZOS)
        b = b.resize((int(b.width * h / b.height), h), Image.LANCZOS)
        out = Image.new("RGB", (a.width + b.width, h), (0, 0, 0))
        out.paste(a, (0, 0))
        out.paste(b, (a.width, 0))
        return out
    # alpha cross-fade — match b to a's size
    b = b.resize(a.size, Image.LANCZOS)
    alpha = _clamp(d.get("alpha", 0.5), 0.0, 1.0, 0.5)
    return Image.blend(a, b, alpha)
