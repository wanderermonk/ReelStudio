"""Subtitles: one transparent PNG per line (works for Hindi and English), overlaid with ffmpeg, plus an .srt file."""
import os
import re

from PIL import Image, ImageDraw, ImageFont, features

FONTS_DEV = ["/System/Library/Fonts/Kohinoor.ttc", "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
             "/System/Library/Fonts/Supplemental/Kohinoor.ttc",
             "/System/Library/Fonts/Supplemental/DevanagariMT.ttc", "/Library/Fonts/Arial Unicode.ttf",
             "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
             "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
             "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
             "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
             "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"]
FONTS_LAT = ["/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _has_glyphs(font, sample="कखग"):
    """True when the font really draws Devanagari (not empty boxes)."""
    try:
        def shot(ch):
            img = Image.new("L", (80, 80))
            ImageDraw.Draw(img).text((10, 10), ch, font=font, fill=255)
            return img.tobytes()
        missing = shot("\U0010FFFD")
        return all(shot(ch) != missing and any(shot(ch)) for ch in sample)
    except Exception:
        return False


def _font(text, size):
    dev = bool(DEVANAGARI.search(text))
    kw = {"layout_engine": ImageFont.Layout.RAQM} if features.check("raqm") else {}
    for f in (FONTS_DEV if dev else FONTS_LAT):
        if not os.path.exists(f):
            continue
        for idx in (range(8) if f.endswith(".ttc") else [0]):  # .ttc files hold several faces
            try:
                font = ImageFont.truetype(f, size, index=idx, **kw)
            except Exception:
                break
            if not dev:
                return font
            name = " ".join(font.getname()).lower()
            if ("devanagari" in name or not f.endswith(".ttc")) and _has_glyphs(font):
                return font
    return ImageFont.load_default()


def can_render(text):
    """Complex scripts (Hindi) need the RAQM text shaper; Latin text always works."""
    if not DEVANAGARI.search(text or ""):
        return True
    return features.check("raqm") and _font(text, 20) is not None and _has_glyphs(_font(text, 20))


def _wrap(draw, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def render(text, out_png, width=1080, size=54):
    font = _font(text, size)
    tmp = Image.new("RGBA", (width, 10))
    d = ImageDraw.Draw(tmp)
    lines = _wrap(d, text, font, width - 160)
    lh = int(size * 1.45)
    h = lh * len(lines) + 36
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    wmax = max(d.textlength(l, font=font) for l in lines)
    x0 = (width - wmax) / 2 - 28
    d.rounded_rectangle([x0, 0, width - x0, h], radius=28, fill=(15, 10, 20, 165))
    for i, l in enumerate(lines):
        tw = d.textlength(l, font=font)
        d.text(((width - tw) / 2, 18 + i * lh), l, font=font, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 200))
    img.save(out_png)
    return out_png, h


def srt(cues, out):
    def ts(t):
        h, r = divmod(t, 3600)
        m, s = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"
    with open(out, "w", encoding="utf-8") as f:
        for i, (a, b, text) in enumerate(cues, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{text}\n\n")
    return out
