import array
import json
import math
import os
import re
import subprocess
import textwrap
import wave

import numpy as np

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = "output"
CHANNEL = os.environ.get("CHANNEL_NAME", "TREND PULSE DAILY")
W, H = 1080, 1920
FPS = 30
END_CARD = 2.5
TR = 0.30  # transition seconds

ORANGE = (249, 115, 22)
RED = (239, 68, 68)
INK = (27, 22, 20)

# ------------------------------------------------------------------ utils

def _font_path():
    for p in (
        "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
        "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
        "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(p):
            return p
    raise RuntimeError("no font found")


FONT_PATH = _font_path()
_fonts = {}


def font(size):
    s = max(8, int(round(size)))
    if s not in _fonts:
        _fonts[s] = ImageFont.truetype(FONT_PATH, s)
    return _fonts[s]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def e_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def e_back(x, k=1.9):
    x = clamp(x)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def audio_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(out.decode().strip())


def speech_span(audio, total):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    step = 160
    rms = [math.sqrt(sum(s * s for s in a[i:i + step]) / step) for i in range(0, len(a) - step, step)]
    if not rms:
        return 0.0, total
    ref = sorted(rms)[int(len(rms) * 0.9)] or 1.0
    idx = [i for i, v in enumerate(rms) if v > ref * 0.06]
    if not idx:
        return 0.0, total
    return max(0.0, idx[0] * 0.02 - 0.05), min(total, (idx[-1] + 1) * 0.02 + 0.1)


def build_words(tokens, t_start, t_end):
    weights = []
    for w in tokens:
        base = len(w) + 1
        if w[-1] in ".!?":
            base += 7
        elif w[-1] in ",;:":
            base += 3
        weights.append(base)
    tot = sum(weights)
    out, t = [], t_start
    for w, wt in zip(tokens, weights):
        d = (t_end - t_start) * wt / tot
        out.append((w, t, t + d))
        t += d
    return out


def make_chunks(words, fnt, max_w=740, max_words=3):
    chunks, cur = [], []
    for item in words:
        cur.append(item)
        text = " ".join(x[0] for x in cur)
        if len(cur) >= max_words or item[0][-1] in ".!?,;:" or fnt.getlength(text) > max_w:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    merged = []
    for c in chunks:
        if merged and len(c) <= 1 and fnt.getlength(" ".join(x[0] for x in merged[-1] + c)) <= max_w:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged


def wrap_px(text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if fnt.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(text, max_w, max_lines, sizes):
    for s in sizes:
        f = font(s)
        lines = wrap_px(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return s, lines
    s = sizes[-1]
    return s, wrap_px(text, font(s), max_w)[:max_lines]


THEMES = {
    "cream": dict(bg0=(253, 248, 242), bg1=(250, 229, 210), fg=INK, sub=(112, 100, 92), accent=ORANGE,
                  accent2=(124, 58, 237), card=(255, 255, 255), cardfg=INK, dark=False,
                  pill=ORANGE, pill_fg=(255, 255, 255)),
    "dark": dict(bg0=(14, 9, 28), bg1=(42, 16, 52), fg=(255, 255, 255), sub=(190, 180, 212),
                 accent=(251, 146, 60), accent2=(139, 92, 246), card=(38, 28, 58), cardfg=(255, 255, 255),
                 dark=True, pill=(251, 146, 60), pill_fg=(20, 12, 8)),
    "sun": dict(bg0=(238, 96, 56), bg1=(252, 150, 100), fg=(255, 255, 255), sub=(255, 236, 224),
                accent=INK, accent2=(255, 214, 160), card=(255, 255, 255), cardfg=INK, dark=False,
                pill=INK, pill_fg=(255, 255, 255)),
}
HUE = {"deg": 0.0}
LAYOUT = {"cap": 0, "trans": 0, "hook": 0, "cards": 0, "card": 0, "news": 0, "stat": 0, "quote": 0, "align": 0, "kick": 0, "brand": 0}


def hue_rotate(im, deg):
    if abs(deg) < 1:
        return im
    c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = (.213 + c * .787 - s_ * .213, .715 - c * .715 - s_ * .715, .072 - c * .072 + s_ * .928, 0,
         .213 - c * .213 + s_ * .143, .715 + c * .285 + s_ * .140, .072 - c * .072 - s_ * .283, 0,
         .213 - c * .213 - s_ * .787, .715 - c * .715 + s_ * .715, .072 + c * .928 + s_ * .072, 0)
    return im.convert("RGB", m)


DECORS = ["blobs", "grid", "rings", "floaters", "beams", "dots", "stripes", "waves", "halftone", "checker"]


# ------------------------------------------------------------------ backgrounds

_cache = {}


def _gradient(c_top, c_bot):
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=mix(c_top, c_bot, y / H))
    return im


def _blob_sprite(r):
    sp = Image.new("L", (r * 2, r * 2), 0)
    ImageDraw.Draw(sp).ellipse([r // 4, r // 4, r * 2 - r // 4, r * 2 - r // 4], fill=255)
    return sp.filter(ImageFilter.GaussianBlur(r // 4))


def base_bg(theme):
    key = ("base", theme)
    if key not in _cache:
        T = THEMES[theme]
        _cache[key] = _gradient(T["bg0"], T["bg1"])
    return _cache[key].copy()


def background(theme, decor, t, seed=0):
    T = THEMES[theme]
    im = base_bg(theme)
    d = ImageDraw.Draw(im)
    mid = mix(T["bg0"], T["bg1"], 0.5)
    c1 = mix(mid, (255, 255, 255) if theme != "cream" else T["accent"], 0.10 if theme != "cream" else 0.09)
    c2 = mix(mid, T["accent2"], 0.14)
    if decor == "blobs":
        key = ("blob", 380)
        if key not in _cache:
            _cache[key] = _blob_sprite(380)
        sp = _cache[key]
        cols = [c2, mix(mid, T["accent"], 0.22 if theme != "sun" else 0.0) if theme != "sun" else (255, 190, 150), c1]
        for i in range(3):
            bx = W / 2 + 420 * math.sin(t * (0.22 + 0.06 * i) + i * 2.1 + seed)
            by = H / 2 + 650 * math.sin(t * (0.17 + 0.05 * i) + i * 2.9 + seed * 0.7)
            im.paste(Image.new("RGB", sp.size, cols[i]), (int(bx - 380), int(by - 380)),
                     sp.point(lambda v_: int(v_ * 0.65)))
    elif decor == "grid":
        step = 108
        ox, oy = (t * 14) % step, (t * 9) % step
        for x in range(-step, W + step, step):
            d.line([(x + ox, 0), (x + ox, H)], fill=c1, width=2)
        for y in range(-step, H + step, step):
            d.line([(0, y + oy), (W, y + oy)], fill=c1, width=2)
    elif decor == "rings":
        cx, cy = (W * 0.9, H * 0.18) if seed % 2 else (W * 0.1, H * 0.82)
        for k in range(14):
            r = (k * 190 + t * 55) % 2300
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c1 if k % 2 else c2, width=4)
    elif decor == "floaters":
        rnd = np.random.RandomState(seed + 3)
        for i in range(9):
            sz = int(rnd.randint(90, 230))
            bx = rnd.randint(0, W)
            by = rnd.randint(0, H)
            sp_ = 0.18 + 0.12 * (i % 3)
            x = (bx + 80 * math.sin(t * sp_ + i)) % W
            y = (by - t * 18 * (1 + i % 3)) % (H + 300) - 150
            col = c1 if i % 2 else c2
            if i % 3 == 0:
                d.ellipse([x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2], outline=col, width=5)
            elif i % 3 == 1:
                d.rounded_rectangle([x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2], radius=sz // 4, fill=col)
            else:
                d.rounded_rectangle([x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2], radius=sz // 4,
                                    outline=col, width=5)
    elif decor == "beams":
        for k in range(4):
            x0 = (k * 380 + t * 28 * (1 + k % 2)) % (W + 900) - 500
            d.polygon([(x0, 0), (x0 + 170, 0), (x0 + 170 - 1100, H), (x0 - 1100, H)], fill=c1 if k % 2 else c2)
    elif decor == "stripes":
        step = 130
        oy = (t * 22) % (step * 2)
        for k in range(-2, H // step + 3):
            y0 = k * step + oy
            if k % 2 == 0:
                d.rectangle([0, y0, W, y0 + step * 0.55], fill=c1)
    elif decor == "waves":
        for k in range(7):
            pts = [(x, H * (0.1 + 0.14 * k) + 60 * math.sin(x * 0.006 + t * 0.9 + k * 1.3)) for x in range(0, W + 30, 30)]
            d.line(pts, fill=c1 if k % 2 else c2, width=10)
    elif decor == "halftone":
        step = 70
        cx, cy = (0, 0) if seed % 2 else (W, H)
        for gy in range(0, H + step, step):
            for gx in range(0, W + step, step):
                dist = math.hypot(gx - cx, gy - cy) / math.hypot(W, H)
                r = max(0, 26 * (1 - dist * 1.25) + 4 * math.sin(t * 1.2 + dist * 6))
                if r > 1:
                    d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=c1)
    elif decor == "checker":
        step = 180
        ox = (t * 20) % (step * 2)
        for gy in range(-1, H // step + 2):
            for gx in range(-2, W // step + 2):
                if (gx + gy) % 2 == 0:
                    x0, y0 = gx * step + ox, gy * step
                    d.rectangle([x0, y0, x0 + step, y0 + step], fill=c1)
    else:  # dots
        step = 74
        for gy in range(0, H + step, step):
            for gx in range(0, W + step, step):
                wv = math.sin(t * 1.4 + gx * 0.01 + gy * 0.008 + seed)
                r = 4 + 3.5 * wv
                d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=c1)
    return im


def add_grain(im):
    """Final grade: crisp contrast + richer colour, soft vignette, almost no grain."""
    from PIL import ImageEnhance
    if "vig" not in _cache:
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        _cache["vig"] = (1.0 - 0.10 * np.clip(d - 0.55, 0, 1) ** 1.5)[..., None].astype(np.float32)
    im = ImageEnhance.Contrast(im).enhance(1.10)
    im = ImageEnhance.Color(im).enhance(1.18)
    a = np.asarray(im, dtype=np.float32) * _cache["vig"]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


# ------------------------------------------------------------------ icons

def _unit_icon(name, d, k, col, lw):
    def P(*xy):
        return [v * k for v in xy]

    lwk = int(lw * k)
    if name == "phone":
        d.rounded_rectangle(P(30, 8, 70, 92), radius=int(11 * k), outline=col, width=lwk)
        d.line(P(44, 19, 56, 19), fill=col, width=lwk)
        d.ellipse(P(46, 78, 54, 86), fill=col)
    elif name == "chip":
        d.rectangle(P(28, 28, 72, 72), outline=col, width=lwk)
        d.rectangle(P(41, 41, 59, 59), fill=col)
        for i in (38, 50, 62):
            d.line(P(i, 14, i, 28), fill=col, width=lwk)
            d.line(P(i, 72, i, 86), fill=col, width=lwk)
            d.line(P(14, i, 28, i), fill=col, width=lwk)
            d.line(P(72, i, 86, i), fill=col, width=lwk)
    elif name == "shield":
        d.polygon(P(50, 7, 85, 20, 85, 52, 50, 93, 15, 52, 15, 20), outline=col, width=lwk)
        d.line(P(34, 49, 46, 62, 68, 36), fill=col, width=lwk, joint="curve")
    elif name == "bolt":
        d.polygon(P(58, 5, 22, 56, 46, 56, 40, 95, 78, 40, 54, 40), fill=col)
    elif name == "globe":
        d.ellipse(P(12, 12, 88, 88), outline=col, width=lwk)
        d.ellipse(P(34, 12, 66, 88), outline=col, width=lwk)
        d.line(P(12, 50, 88, 50), fill=col, width=lwk)
    elif name == "server":
        for y in (14, 40, 66):
            d.rounded_rectangle(P(16, y, 84, y + 22), radius=int(6 * k), outline=col, width=lwk)
            d.ellipse(P(26, y + 7, 34, y + 15), fill=col)
    elif name == "camera":
        d.rounded_rectangle(P(10, 28, 90, 84), radius=int(10 * k), outline=col, width=lwk)
        d.ellipse(P(35, 40, 65, 72), outline=col, width=lwk)
        d.polygon(P(34, 28, 40, 16, 60, 16, 66, 28), fill=col)
    elif name == "spark":
        d.polygon(P(46, 6, 57, 38, 90, 49, 57, 60, 46, 94, 35, 60, 2, 49, 35, 38), fill=col)
        d.polygon(P(82, 6, 86, 18, 98, 22, 86, 26, 82, 38, 78, 26, 66, 22, 78, 18), fill=col)
    elif name == "lock":
        d.rounded_rectangle(P(22, 44, 78, 92), radius=int(9 * k), fill=col)
        d.arc(P(31, 8, 69, 64), 180, 360, fill=col, width=lwk)
        d.line(P(31, 36, 31, 46), fill=col, width=lwk)
        d.line(P(69, 36, 69, 46), fill=col, width=lwk)
    elif name == "warning":
        d.polygon(P(50, 10, 93, 87, 7, 87), outline=col, width=lwk)
        d.line(P(50, 38, 50, 62), fill=col, width=lwk)
        d.ellipse(P(45, 69, 55, 79), fill=col)
    elif name == "battery":
        d.rounded_rectangle(P(8, 30, 84, 70), radius=int(8 * k), outline=col, width=lwk)
        d.rectangle(P(86, 43, 94, 57), fill=col)
        d.rectangle(P(17, 39, 35, 61), fill=col)
        d.rectangle(P(42, 39, 60, 61), fill=col)
    elif name == "wifi":
        for r in (16, 32, 48):
            d.arc(P(50 - r, 78 - r, 50 + r, 78 + r), 225, 315, fill=col, width=lwk)
        d.ellipse(P(44, 72, 56, 84), fill=col)
    else:
        _unit_icon("bolt", d, k, col, lw)


_ICON_KEYS = [
    ("shield", ["security", "flaw", "vulnerab", "hack", "breach", "malware", "exploit", "attack", "cyber",
                "uefi", "firmware", "patch", "ransom", "leak"]),
    ("chip", ["chip", "cpu", "processor", "nvidia", "intel", "amd", "qualcomm", "mediatek", "snapdragon",
              "silicon", "gpu", "tsmc", "semiconductor"]),
    ("phone", ["iphone", "phone", "samsung", "galaxy", "pixel", "android", "foldable", "smartphone", "vivo",
               "xiaomi", "oneplus", "huawei", "apple", "ios"]),
    ("spark", [" ai ", "openai", "chatgpt", "gemini", "claude", "model", "llm", "robot", "copilot", "agent",
               "anthropic", "artificial"]),
    ("server", ["server", "cloud", "data center", "datacenter", "aws", "azure", "outage"]),
    ("camera", ["camera", "photo", "lens", "video"]),
    ("battery", ["battery", "charging", "charger", "power"]),
    ("wifi", ["wifi", "5g", "network", "internet", "router", "starlink", "satellite"]),
    ("lock", ["privacy", "lock", "password", "encrypt", "passkey"]),
    ("warning", ["warning", "recall", "crack", "issue", "problem", "bug", "fail"]),
    ("globe", ["web", "browser", "google", "chrome", "site", "search"]),
]


def icon_for(text):
    t = " " + (text or "").lower() + " "
    best, score = "bolt", 0
    for name, keys in _ICON_KEYS:
        s = sum(t.count(k) for k in keys)
        if s > score:
            best, score = name, s
    return best


def glyph(name, size, color):
    key = ("glyph", name, size, color)
    if key not in _cache:
        S = 4
        im = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        _unit_icon(name, d, size * S / 100.0, color + (255,), 7)
        _cache[key] = im.resize((size, size), Image.LANCZOS)
    return _cache[key]


def gradient_tile(w, h, c0, c1, radius):
    key = ("tile", w, h, c0, c1, radius)
    if key not in _cache:
        g = Image.new("RGB", (w, h))
        gd = ImageDraw.Draw(g)
        for i in range(w + h):
            gd.line([(i, 0), (i - h, h)], fill=mix(c0, c1, i / (w + h)), width=2)
        m = Image.new("L", (w * 2, h * 2), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w * 2 - 1, h * 2 - 1], radius=radius * 2, fill=255)
        _cache[key] = (g, m.resize((w, h), Image.LANCZOS))
    return _cache[key]


def icon_tile(im, name, cx, cy, size, c0, c1, scale=1.0, shadow=True):
    s = int(size * scale)
    if s < 8:
        return
    g, m = gradient_tile(s, s, c0, c1, s // 4)
    x, y = int(cx - s / 2), int(cy - s / 2)
    if shadow:
        key = ("tshadow", s)
        if key not in _cache:
            sp = Image.new("L", (s + 80, s + 80), 0)
            ImageDraw.Draw(sp).rounded_rectangle([40, 40, 40 + s, 40 + s], radius=s // 4, fill=110)
            _cache[key] = sp.filter(ImageFilter.GaussianBlur(14))
        sp = _cache[key]
        im.paste(Image.new("RGB", sp.size, (30, 14, 10)), (x - 40, y - 40 + 14), sp)
    im.paste(g, (x, y), m)
    gl = glyph(name, int(s * 0.58), (255, 255, 255))
    im.paste(gl, (int(cx - gl.size[0] / 2), int(cy - gl.size[1] / 2)), gl)


# ------------------------------------------------------------------ drawing helpers

def card(im, x, y, w, h, T, radius=44):
    x, y, w, h = int(x), int(y), int(w), int(h)
    st = LAYOUT["card"]
    if st == 3:
        radius = int(min(radius * 2, h / 2))
    elif st == 2:
        radius = int(radius * 0.45)
    if st != 2:
        key = ("shadow", w, h, radius)
        if key not in _cache:
            sp = Image.new("L", (w + 140, h + 140), 0)
            ImageDraw.Draw(sp).rounded_rectangle([70, 70, 70 + w, 70 + h], radius=radius, fill=150 if st != 1 else 90)
            _cache[key] = sp.filter(ImageFilter.GaussianBlur(26))
        sp = _cache[key]
        im.paste(Image.new("RGB", sp.size, (20, 8, 20) if T["dark"] else (60, 30, 10)),
                 (int(x - 70), int(y - 70 + 22)), sp)
    d = ImageDraw.Draw(im)
    if st == 1:
        region = im.crop((x, y, x + w, y + h))
        blended = Image.blend(region, Image.new("RGB", (w, h), T["card"]), 0.68)
        m = Image.new("L", (w * 2, h * 2), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w * 2 - 1, h * 2 - 1], radius=radius * 2, fill=255)
        im.paste(blended, (x, y), m.resize((w, h), Image.LANCZOS))
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=(255, 255, 255), width=3)
        return
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=T["card"])
    if st == 2:
        c0 = T["accent"] if T["accent"] != INK else ORANGE
        d.rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=c0, width=7)
        return
    edge = mix(T["card"], (255, 255, 255), 0.18) if T["dark"] else mix(T["card"], (0, 0, 0), 0.07)
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=edge, width=2)


def line_with_accent(d, im, line, accent, cx, cy, size, fg, pill, pill_fg):
    f = font(size)
    words = line.split()
    space = f.getlength(" ")
    total = sum(f.getlength(w) for w in words) + space * (len(words) - 1)
    x = cx - total / 2
    if LAYOUT["align"] == 1 and abs(cx - W / 2) < 1:
        x = 90 + size * 0.22
    for w in words:
        wl = f.getlength(w)
        if accent_match(w, accent):
            pad = size * 0.22
            d.rounded_rectangle([x - pad, cy - size * 0.62, x + wl + pad, cy + size * 0.62],
                                radius=int(size * 0.3), fill=pill)
            d.text((x, cy), w, font=f, fill=pill_fg, anchor="lm")
        else:
            d.text((x, cy), w, font=f, fill=fg, anchor="lm")
        x += wl + space


def kicker(im, d, text, T, lt, y=300):
    if not text:
        return
    p = e_out(lt / 0.4)
    f = font(34)
    tw = f.getlength(text)
    w, h = tw + 96, 76
    x = (W - w) / 2 if LAYOUT["align"] == 0 else 80
    yy = y - (1 - p) * 40
    k = LAYOUT["kick"]
    c0 = T["accent"] if T["accent"] != INK else ORANGE
    if k == 0:
        d.rounded_rectangle([x, yy, x + w, yy + h], radius=38, fill=(24, 18, 30))
        d.ellipse([x + 28, yy + 29, x + 46, yy + 47], fill=RED)
        d.text((x + 64, yy + h / 2), text, font=f, fill=(255, 255, 255), anchor="lm")
    elif k == 1:
        d.rounded_rectangle([x, yy, x + w - 30, yy + h], radius=14, fill=c0)
        d.text((x + (w - 30) / 2, yy + h / 2), text, font=f, fill=(255, 255, 255), anchor="mm")
    else:
        d.text((x + 8, yy + h / 2), text, font=f, fill=T["fg"], anchor="lm")
        d.rounded_rectangle([x + 8, yy + h - 4, x + 8 + tw, yy + h + 4], radius=4, fill=c0)


def accent_match(word, accent):
    return bool(accent) and re.sub(r"\W", "", word).lower() == re.sub(r"\W", "", accent).lower()


# ------------------------------------------------------------------ scenes

DEFAULT_KICKER = {"hook": "BREAKING TECH", "stat": "BY THE NUMBERS", "cards": "KEY POINTS",
                  "statement": "WHY IT MATTERS", "cta": ""}


def news_card_layer(T, title, icon):
    st = LAYOUT["news"]
    key = ("newscard", title, icon, T["accent"], st)
    if key in _cache:
        return _cache[key]
    LW, LH, cw, ch = 940, 520, 800, 400
    x0, y0 = 70, 50
    layer = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    sh = Image.new("L", (LW, LH), 0)
    ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 22, x0 + cw, y0 + ch + 22], radius=48, fill=130)
    sh = sh.filter(ImageFilter.GaussianBlur(24))
    layer.paste(Image.new("RGBA", (LW, LH), (30, 14, 10, 255)), (0, 0), sh)
    d = ImageDraw.Draw(layer)
    bgc = (255, 255, 255, 255) if st != 1 else (26, 20, 38, 255)
    fgc = INK if st != 1 else (255, 255, 255)
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], radius=48 if st != 2 else 30, fill=bgc)
    # header
    d.ellipse([x0 + 30, y0 + 28, x0 + 82, y0 + 80], fill=ORANGE)
    g = glyph("bolt", 30, (255, 255, 255))
    layer.paste(g, (x0 + 41, y0 + 39), g)
    d.text((x0 + 98, y0 + 54), "Trend Pulse Daily", font=font(30), fill=fgc, anchor="lm")
    d.text((x0 + cw - 32, y0 + 54), "now", font=font(26), fill=(150, 140, 134), anchor="rm")
    # image area
    ix0, iy0, ix1, iy1 = x0 + 30, y0 + 102, x0 + cw - 30, y0 + 252
    if st == 2:
        ix1, iy1 = x0 + 290, y0 + ch - 30
    gt, gm = gradient_tile(ix1 - ix0, iy1 - iy0, T["accent"] if T["accent"] != INK else ORANGE,
                           T["accent2"], 30)
    layer.paste(gt.convert("RGBA"), (ix0, iy0), gm)
    gl = glyph(icon, 130, (255, 255, 255))
    layer.paste(gl, ((ix0 + ix1) // 2 - 65, (iy0 + iy1) // 2 - 65), gl)
    # headline
    if st == 2:
        s, lines = fit_text(title, cw - 370, 4, [38, 34, 30, 27])
        for i, ln in enumerate(lines):
            d.text((x0 + 320, y0 + 200 + (i - (len(lines) - 1) / 2) * s * 1.3), ln, font=font(s), fill=fgc, anchor="lm")
    else:
        s, lines = fit_text(title, cw - 70, 2, [40, 36, 32])
        for i, ln in enumerate(lines):
            d.text((x0 + 34, y0 + 282 + i * s * 1.25), ln, font=font(s), fill=fgc, anchor="lm")
    _cache[key] = layer
    return layer


def scene_hook(im, d, sc, T, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["hook"]), T, lt, 250)
    size, lines = fit_text(sc.get("headline", ""), 900, 3, [150, 136, 122, 110, 98])
    lh = size * 1.18
    hy = 600 if LAYOUT["hook"] == 0 else 1000
    top = hy - (len(lines) * lh) / 2 + lh / 2
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.08 * i - 0.1) / 0.5)
        if p <= 0.01:
            continue
        s = size * (0.45 + 0.55 * p)
        line_with_accent(d, im, ln, sc.get("accent"), W / 2, top + i * lh + (1 - e_out(p)) * 50,
                         s, T["fg"], T["pill"], T["pill_fg"])
    icon = icon_for(sc.get("headline", "") + " " + sc.get("_context", ""))
    layer = news_card_layer(T, sc.get("_title") or sc.get("headline", ""), icon)
    pcard = e_back((lt - 0.45) / 0.65, 1.3)
    if pcard > 0.02:
        ang = -3.5 + 1.2 * math.sin(lt * 1.6)
        rot = layer.rotate(ang, resample=Image.BICUBIC, expand=True)
        scl = clamp(pcard, 0, 1.06)
        if abs(scl - 1) > 0.01:
            rot = rot.resize((int(rot.size[0] * scl), int(rot.size[1] * scl)), Image.BILINEAR)
        cy = (1130 if LAYOUT["hook"] == 0 else 560) + (1 - clamp(pcard, 0, 1)) * 260 + 8 * math.sin(lt * 2.1)
        im.paste(rot, (int(W / 2 - rot.size[0] / 2), int(cy - rot.size[1] / 2)), rot)


def scene_statement(im, d, sc, T, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["statement"]), T, lt, 250)
    size, lines = fit_text(sc.get("headline", ""), 780, 5, [104, 94, 84, 74, 64])
    lh = size * 1.26
    ch = len(lines) * lh + 250
    cy0 = 800 - ch / 2 + 60
    p = e_back((lt - 0.05) / 0.55, 1.2)
    if p <= 0.02:
        return
    off = (1 - clamp(p, 0, 1)) * 160
    card(im, 70, cy0 + off, 940, ch, T, radius=56)
    d2 = ImageDraw.Draw(im)
    qc = T["accent"] if T["accent"] != INK else ORANGE
    xb = 130
    if LAYOUT["quote"] == 0:
        d2.text((120, cy0 + off + 20), "“", font=font(260), fill=T["accent"], anchor="la")
    elif LAYOUT["quote"] == 1:
        d2.rounded_rectangle([105, cy0 + off + 70, 121, cy0 + off + ch - 70], radius=8, fill=qc)
        xb = 160
    else:
        d2.ellipse([100, cy0 + off - 10, 220, cy0 + off + 110], fill=qc)
        d2.text((160, cy0 + off + 70), "“", font=font(150), fill=(255, 255, 255), anchor="mm")
    for i, ln in enumerate(lines):
        pp = e_out((lt - 0.2 - 0.09 * i) / 0.45)
        if pp <= 0:
            continue
        f = font(size)
        space = f.getlength(" ")
        x = xb - (1 - pp) * 80
        y = cy0 + off + 230 + i * lh
        for w in ln.split():
            wl = f.getlength(w)
            if accent_match(w, sc.get("accent")):
                d2.rounded_rectangle([x - 14, y - size * 0.6, x + wl + 14, y + size * 0.6],
                                     radius=int(size * 0.28), fill=T["pill"])
                d2.text((x, y), w, font=f, fill=T["pill_fg"], anchor="lm")
            else:
                d2.text((x, y), w, font=f, fill=T["cardfg"], anchor="lm")
            x += wl + space
    icon = icon_for(sc.get("headline", "") + " " + sc.get("voice", ""))
    tp = e_back((lt - 0.5) / 0.5)
    if tp > 0:
        icon_tile(im, icon, 880, cy0 + off + ch - 20, 150, T["accent"] if T["accent"] != INK else ORANGE,
                  T["accent2"], scale=clamp(tp, 0, 1.05))


def parse_value(v):
    m = re.search(r"\d[\d,]*\.?\d*", v)
    if not m:
        return None
    return v[:m.start()], m.group(0), v[m.end():]


def scene_stat(im, d, sc, T, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["stat"]), T, lt, 250)
    value = str(sc.get("value", "")).strip()
    pv = parse_value(value)
    p = e_out((lt - 0.1) / 1.2)
    pct = 0.78
    if pv:
        pre, raw, suf = pv
        target = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        cur = target * p
        num = f"{cur:,.{decimals}f}" if "," in raw else f"{cur:.{decimals}f}"
        shown = pre + num + suf
        if "%" in suf and target <= 100:
            pct = target / 100.0
    else:
        shown = value
    st = LAYOUT["stat"]
    c0 = T["accent"] if T["accent"] != INK else ORANGE
    cx, cy = W / 2, 760
    if st == 0:
        R, wd = 300, 40
        cx, cy = W / 2, 760
        S = 2
        box = (R + wd) * 2 + 20
        ring = Image.new("RGBA", (box * S, box * S), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        bb = [10 * S, 10 * S, (box - 10) * S, (box - 10) * S]
        track = mix(T["bg0"], T["fg"], 0.16)
        rd.ellipse(bb, outline=track + (255,), width=wd * S)
        sweep = 360 * pct * p
        if sweep > 1:
            rd.arc(bb, -90, -90 + sweep, fill=T["accent"] + (255,), width=wd * S)
            a = math.radians(-90 + sweep)
            rr = (box / 2 - 10 - wd / 2) * S
            ex, ey = box * S / 2 + rr * math.cos(a), box * S / 2 + rr * math.sin(a)
            rd.ellipse([ex - wd * S / 2, ey - wd * S / 2, ex + wd * S / 2, ey + wd * S / 2],
                       fill=T["accent"] + (255,))
        ring = ring.resize((box, box), Image.LANCZOS)
        im.paste(ring, (int(cx - box / 2), int(cy - box / 2)), ring)
    elif st == 1:
        cy = 700
        bw, bh = 800, 56
        bx = W / 2 - bw / 2
        by = 930
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 2, fill=mix(T["bg0"], T["fg"], 0.16))
        fw = max(bh, bw * pct * p)
        d.rounded_rectangle([bx, by, bx + fw, by + bh], radius=bh // 2, fill=c0)
        d.ellipse([bx + fw - bh + 8, by + 8, bx + fw - 8, by + bh - 8], fill=(255, 255, 255))
    else:
        rr = 310 * (0.9 + 0.1 * e_out((lt - 0.05) / 0.6))
        for k, (gr, ga) in enumerate(((rr + 70, 0.10), (rr + 36, 0.18))):
            d.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=mix(T["bg0"], c0, ga))
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=c0)
    size = 230
    while font(size).getlength(value) > 470 and size > 70:
        size -= 8
    pop = e_back((lt - 0.05) / 0.5)
    d.text((cx, cy - 4), shown, font=font(size * clamp(0.6 + 0.4 * pop, 0.6, 1.06)),
           fill=(255, 255, 255) if st == 2 else T["fg"], anchor="mm")
    ls, llines = fit_text(sc.get("label", ""), 900, 3, [76, 68, 60, 52])
    for i, ln in enumerate(llines):
        a = e_out((lt - 0.5 - 0.1 * i) / 0.4)
        if a > 0:
            d.text((W / 2, 1180 + i * ls * 1.25 + (1 - a) * 30), ln, font=font(ls),
                   fill=mix(T["bg0"], T["fg"], a), anchor="mm")


def scene_cards(im, d, sc, T, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["cards"]), T, lt, 220)
    hs, hl = fit_text(sc.get("heading", ""), 900, 2, [88, 78, 70])
    for i, ln in enumerate(hl):
        a = e_out((lt - 0.05 * i) / 0.45)
        d.text((W / 2, 420 + i * hs * 1.2 + (1 - a) * 40), ln, font=font(hs), fill=T["fg"], anchor="mm")
    ub = e_out((lt - 0.2) / 0.5)
    ybar = 420 + len(hl) * hs * 1.2 - 10
    d.rounded_rectangle([W / 2 - 90 * ub, ybar, W / 2 + 90 * ub, ybar + 12], radius=6, fill=T["accent"])
    items = [str(x) for x in sc.get("items", [])][:3]
    ch, gap = 215, 30
    y0 = 560 if len(hl) == 1 else 620
    for i, it in enumerate(items):
        p = e_back((lt - 0.25 - 0.22 * i) / 0.55, 1.4)
        if p <= 0.02:
            continue
        y = y0 + i * (ch + gap) + (1 - clamp(p, 0, 1)) * 220
        zz = LAYOUT["cards"] == 1
        cx0 = (60 if i % 2 == 0 else 200) if zz else 80
        cw = 820 if zz else 920
        card(im, cx0, y, cw, ch, T, radius=46)
        c0 = T["accent"] if T["accent"] != INK else ORANGE
        icon_tile(im, icon_for(it), cx0 + 110, y + ch / 2, 120, c0, T["accent2"], scale=clamp(p, 0.3, 1.04), shadow=False)
        s, lines = fit_text(it, cw - 320, 3, [48, 44, 40, 36])
        lh = s * 1.22
        d2 = ImageDraw.Draw(im)
        for j, ln in enumerate(lines):
            d2.text((cx0 + 220, y + ch / 2 - (len(lines) - 1) * lh / 2 + j * lh), ln, font=font(s),
                    fill=T["cardfg"], anchor="lm")


def scene_cta(im, d, sc, T, lt, dur):
    size, lines = fit_text(sc.get("headline", "Follow for daily tech news"), 900, 3, [124, 110, 96])
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.1 * i) / 0.5)
        d.text((W / 2, 560 + i * size * 1.2), ln, font=font(size * clamp(0.5 + 0.5 * p, 0.5, 1.1)),
               fill=T["fg"], anchor="mm")
    p = e_back((lt - 0.5) / 0.5)
    pulse = 1 + 0.04 * math.sin(lt * 7)
    bw, bh = 700 * pulse * clamp(p, 0, 1.05), 170 * pulse * clamp(p, 0, 1.05)
    cy = 560 + len(lines) * size * 1.2 + 120
    if p > 0:
        d.rounded_rectangle([W / 2 - bw / 2, cy - bh / 2, W / 2 + bw / 2, cy + bh / 2], radius=60, fill=RED)
        d.text((W / 2, cy), "SUBSCRIBE", font=font(72 * clamp(p, 0.3, 1)), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, cy + 180), CHANNEL, font=font(52), fill=T["accent"] if T["accent"] != INK else T["fg"],
           anchor="mm")


SCENES = {"hook": scene_hook, "statement": scene_statement, "stat": scene_stat,
          "cards": scene_cards, "cta": scene_cta}


def render_scene(sc, lt, dur, t_abs):
    th = sc["_pal"]
    im = background(th, sc["_style"], t_abs, sc["_seed"])
    d = ImageDraw.Draw(im)
    SCENES[sc["type"]](im, d, sc, THEMES[th], lt, dur)
    return im


# ------------------------------------------------------------------ captions & chrome

CAP_CY = 1470  # safe zone: above the Shorts UI (channel name / buttons)


def draw_chrome(im, t, full, chunks, th):
    d = ImageDraw.Draw(im)
    T = THEMES[th]
    bx = 60
    if LAYOUT["brand"] == 1:
        bx = int(W - 70 - 52 - 18 - font(32).getlength(CHANNEL))
    d.rounded_rectangle([bx, 130, bx + 52, 182], radius=14, fill=ORANGE)
    g = glyph("bolt", 30, (255, 255, 255))
    im.paste(g, (bx + 11, 141), g)
    d.text((bx + 70, 156), CHANNEL, font=font(32), fill=T["sub"], anchor="lm")
    d.rectangle([0, 0, W * clamp(t / full), 10], fill=ORANGE)
    cur = None
    for c in chunks:
        if c["t0"] <= t < c["t1"]:
            cur = c
            break
    if not cur:
        return
    fnt = font(64)
    words = [w[0] for w in cur["words"]]
    space = fnt.getlength(" ")
    tw = sum(fnt.getlength(w) for w in words) + space * (len(words) - 1)
    pw, ph = tw + 90, 150
    p = e_back((t - cur["t0"]) / 0.22, 1.2)
    cx, cy = (W - 70) / 2, CAP_CY + (1 - clamp(p, 0, 1)) * 40
    cs = LAYOUT["cap"]
    if cs == 0:
        d.rounded_rectangle([cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], radius=44, fill=(28, 22, 32))
    elif cs == 1:
        d.rounded_rectangle([cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], radius=44, fill=(255, 255, 255))
    x = cx - tw / 2
    for (w, a, b) in cur["words"]:
        wl = fnt.getlength(w)
        if a <= t < b:
            d.rounded_rectangle([x - 16, cy - 56, x + wl + 16, cy + 56], radius=28, fill=ORANGE)
        if cs == 2:
            d.text((x, cy + 2), w, font=fnt, fill=(255, 255, 255), anchor="lm", stroke_width=9, stroke_fill=(20, 14, 26))
        else:
            d.text((x, cy + 2), w, font=fnt, fill=(255, 255, 255) if cs == 0 or a <= t < b else (28, 22, 32), anchor="lm")
        x += wl + space


# ------------------------------------------------------------------ sound design

SR = 44100


def _reverb(sig, seed, wet_len=1.1, decay=4.2):
    n = int(wet_len * SR)
    rs = np.random.RandomState(seed)
    ir = rs.randn(n) * np.exp(-np.arange(n) / SR * decay)
    ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))
    size = 1
    while size < len(sig) + n:
        size *= 2
    out = np.fft.irfft(np.fft.rfft(sig, size) * np.fft.rfft(ir, size), size)[: len(sig)]
    return out / (np.max(np.abs(out)) + 1e-9) * (np.max(np.abs(sig)) + 1e-9)


def synth_music(length, seed):
    """Stereo lo-fi/electronic bed: pad + pluck arp (reverb), bass, kick, hats, kick-pump."""
    import random
    rng = random.Random(seed)
    bpm = rng.choice([84, 88, 92, 96, 100, 104, 108, 116, 124])
    beat = 60.0 / bpm
    bar = beat * 4
    progs = [
        [[57, 60, 64, 67], [53, 57, 60, 64], [60, 64, 67, 71], [55, 59, 62, 67]],
        [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]],
        [[62, 65, 69, 72], [58, 62, 65, 70], [53, 57, 60, 65], [60, 64, 67, 72]],
        [[52, 55, 59, 62], [48, 52, 55, 59], [55, 59, 62, 66], [50, 54, 57, 62]],
    ]
    progs += [
        [[57, 60, 64, 69], [55, 59, 62, 67], [53, 57, 60, 65], [55, 59, 62, 67]],
        [[64, 67, 71, 74], [60, 64, 67, 71], [57, 60, 64, 67], [59, 62, 66, 69]],
        [[60, 63, 67, 70], [56, 60, 63, 67], [58, 62, 65, 70], [55, 58, 62, 65]],
        [[65, 69, 72, 76], [62, 65, 69, 72], [60, 64, 67, 72], [58, 62, 65, 69]],
    ]
    shift = rng.randint(-5, 6)
    prog = [[n + shift for n in ch] for ch in rng.choice(progs)]
    arp_style = rng.randint(0, 2)
    hat_step = rng.choice([1, 1, 2])
    pluck_mul = rng.choice([1.0, 1.0, 0.5])
    wave2 = rng.choice([0.15, 0.35, 0.6])
    N = int(length * SR) + SR
    pad = np.zeros((N, 2))
    pluck = np.zeros((N, 2))
    dry = np.zeros((N, 2))
    kicks = []

    def add(buf, sig, t, pan=0.5):
        i = int(t * SR)
        if i >= N:
            return
        j = min(N, i + len(sig))
        s = sig[: j - i]
        buf[i:j, 0] += s * (1 - pan) * 1.4
        buf[i:j, 1] += s * pan * 1.4

    def f(m):
        return 440.0 * 2 ** ((m - 69) / 12.0)

    nbars = int(length / bar) + 2
    for b in range(nbars):
        t0 = b * bar
        chord = prog[b % 4]
        n = int(bar * SR)
        tt = np.arange(n) / SR
        env = np.minimum(1, tt / 0.4) * np.clip((bar - tt) / 0.35, 0, 1)
        for ch_i in (0, 1):
            sig = np.zeros(n)
            for m in chord:
                fr = f(m) * (1.0045 if ch_i == 0 else 0.9955)
                sig += np.sin(2 * np.pi * fr * tt) + wave2 * np.sin(2 * np.pi * 2 * fr * tt) \
                    + 0.15 * np.sin(2 * np.pi * 3 * fr * tt)
            i = int(t0 * SR)
            if i < N:
                j = min(N, i + n)
                pad[i:j, ch_i] += (sig * env * 0.05)[: j - i]
        root = f(chord[0] - 24)
        for k, (pos, amp) in enumerate(((0, 0.26), (1.5, 0.14), (2.5, 0.2), (3.25, 0.12))):
            nn = int(beat * 1.3 * SR)
            tb = np.arange(nn) / SR
            bass = (np.sin(2 * np.pi * root * tb) + 0.2 * np.sin(2 * np.pi * 2 * root * tb)) \
                * np.exp(-tb * 3.2) * amp
            add(dry, bass, t0 + pos * beat, 0.5)
        for pos in (0, 1, 2, 3):
            tk = np.arange(int(0.3 * SR)) / SR
            phase = 2 * np.pi * np.cumsum(48 + 100 * np.exp(-tk * 30)) / SR
            add(dry, np.sin(phase) * np.exp(-tk * 9) * (0.34 if pos % 2 == 0 else 0.2), t0 + pos * beat, 0.5)
            kicks.append(t0 + pos * beat)
        noise = np.random.RandomState(b).randn(int(0.05 * SR))
        hat = np.diff(noise, prepend=0) * np.exp(-np.arange(len(noise)) / SR * 110) * 0.6
        for pos in range(1, 8, 2 if hat_step == 1 else 1):
            add(dry, hat * 0.05, t0 + pos * beat / 2, 0.55)
        arps = [[0, 1, 2, 3, 2, 1, 3, 2], [0, 2, 1, 3, 0, 2, 3, 1], [3, 2, 1, 0, 1, 2, 3, 2]]
        arp = [chord[j] for j in arps[arp_style]]
        for k in range(8):
            tp = np.arange(int(0.5 * SR)) / SR
            fr = f(arp[k] + (24 if pluck_mul == 1.0 else 12))
            pl = (np.sin(2 * np.pi * fr * tp) + 0.3 * np.sin(2 * np.pi * 2 * fr * tp)) * np.exp(-tp * 6.5)
            add(pluck, pl * 0.08, t0 + k * beat / 2, 0.3 if k % 2 else 0.7)
    pump = np.ones(N)
    for kt in kicks[::2]:
        i = int(kt * SR)
        seg = min(N - i, int(0.3 * SR))
        if seg > 0:
            pump[i:i + seg] *= 1 - 0.45 * np.exp(-np.arange(seg) / SR * 9)
    pad *= pump[:, None]
    bus = pad + pluck
    wet = np.stack([_reverb(bus[:, 0], 11), _reverb(bus[:, 1], 23)], axis=1)
    out = dry + pad + pluck + wet * 0.55
    out = out[: int(length * SR)]
    fade = np.ones(len(out))
    fi, fo = int(1.2 * SR), int(2.0 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-fo:] = np.linspace(1, 0, fo)
    out *= fade[:, None]
    return out / (np.max(np.abs(out)) + 1e-9)


def shift_pitch(sig, ratio):
    n = int(len(sig) / ratio)
    return np.interp(np.linspace(0, len(sig) - 1, n), np.arange(len(sig)), sig)


def sfx_whoosh(dur=0.6):
    n = int(dur * SR)
    x = np.random.RandomState(7).randn(n)
    y = np.zeros(n)
    p1 = p2 = 0.0
    for i in range(n):
        a = 0.004 + 0.07 * math.sin(math.pi * i / n) ** 2
        p1 += a * (x[i] - p1)
        p2 += a * (p1 - p2)
        y[i] = p2
    y = y * np.sin(np.pi * np.arange(n) / n) ** 2
    return y / (np.max(np.abs(y)) + 1e-9) * 0.5


def sfx_pop():
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * (420 + 900 * t) * t) * np.exp(-t * 38) * 0.55


def sfx_thump():
    n = int(0.7 * SR)
    t = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(38 + 80 * np.exp(-t * 14)) / SR
    return np.sin(ph) * np.exp(-t * 5.5) * 0.9


def sfx_rise():
    n = int(1.0 * SR)
    t = np.arange(n) / SR
    ph = 2 * np.pi * np.cumsum(300 + 700 * (t / 1.0) ** 2) / SR
    return np.sin(ph) * (t / 1.0) ** 1.5 * 0.35


def sfx_ding(freqs=(1318.5,), gap=0.0):
    n = int(1.0 * SR)
    out = np.zeros(n)
    for k, fr in enumerate(freqs):
        s = int(k * gap * SR)
        t = np.arange(n - s) / SR
        out[s:] += (np.sin(2 * np.pi * fr * t) + 0.25 * np.sin(2 * np.pi * 2 * fr * t)) * np.exp(-t * 4.5) * 0.4
    return out


def sfx_tick():
    n = int(0.04 * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 1900 * t) * np.exp(-t * 130) * 0.5


def decode_voice(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def smooth(x, sec):
    n = max(1, int(sec * SR))
    c = np.cumsum(np.insert(x, 0, 0.0))
    y = (c[n:] - c[:-n]) / n
    return np.concatenate([y, np.full(len(x) - len(y), y[-1] if len(y) else 0.0)])


def build_audio_mix(voice_path, full, events, seed, out_path):
    voice = decode_voice(voice_path)
    N = int((full + 0.5) * SR)
    v = np.zeros(N)
    v[: min(N, len(voice))] = voice[:N]
    v = v / (np.max(np.abs(v)) or 1.0) * 0.85
    mix_ = np.stack([v, v], axis=1)

    if os.environ.get("MUSIC", "1") != "0":
        music = synth_music(N / SR, seed)
        if len(music) < N:
            music = np.pad(music, ((0, N - len(music)), (0, 0)))
        music = music[:N]
        mono_env = smooth(np.abs(v), 0.08)
        ref = np.percentile(mono_env, 95) or 1.0
        duck = 1.0 - 0.7 * smooth(np.clip(mono_env / ref, 0, 1), 0.3)
        mix_ += music[:N] * (duck * 0.32)[:, None]

    import random as _r
    sr_ = _r.Random(seed + 5)
    pr = lambda: sr_.choice([0.8, 0.9, 1.0, 1.12, 1.25, 1.4])
    lib = {"whoosh": (shift_pitch(sfx_whoosh(sr_.choice([0.45, 0.6, 0.8])), pr()), 0.7),
           "pop": (shift_pitch(sfx_pop(), pr()), 0.7), "thump": (shift_pitch(sfx_thump(), sr_.choice([0.85, 1.0, 1.2])), 0.7),
           "rise": (shift_pitch(sfx_rise(), sr_.choice([0.85, 1.0, 1.2])), 0.7),
           "ding": (shift_pitch(sfx_ding(), pr()), 0.7),
           "chime": (shift_pitch(sfx_ding((1046.5, 1318.5, 1568.0), 0.09), pr()), 0.7),
           "tick": (shift_pitch(sfx_tick(), sr_.choice([0.7, 1.0, 1.4])), 0.22)}
    for kind, t in events:
        s, g = lib[kind]
        i = int(max(0.0, t) * SR)
        j = min(N, i + len(s))
        if i < N:
            mix_[i:j, :] += (s[: j - i] * g)[:, None]

    pk = np.max(np.abs(mix_))
    if pk > 0.9:
        mix_ *= 0.9 / pk
    pcm = (np.clip(mix_, -1, 1) * 32767).astype("<i2")
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ script -> scenes

def fallback_scenes(data):
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", data["narration"].strip()) if s.strip()]
    scenes = []
    for i, s in enumerate(sents):
        short = " ".join(s.split()[:9]) + ("..." if len(s.split()) > 9 else "")
        if i == 0:
            scenes.append({"type": "hook", "headline": data.get("title", short)[:60], "voice": s})
        elif i == len(sents) - 1:
            scenes.append({"type": "cta", "headline": "Follow for daily tech news", "voice": s})
        else:
            m = re.search(r"[$€£]?\d[\d,\.]*\s?(%|percent|million|billion|GB|TB|inch|days|hours)?", s)
            if m and len(m.group(0).strip()) >= 2 and i % 2 == 0:
                scenes.append({"type": "stat", "value": m.group(0).strip(), "label": short, "voice": s})
            else:
                scenes.append({"type": "statement", "headline": short, "voice": s})
    return scenes


def prepare_scenes(data):
    import datetime
    import random
    scenes = data.get("scenes")
    ok = (isinstance(scenes, list) and len(scenes) >= 3
          and all(isinstance(s, dict) and s.get("voice") and s.get("type") in SCENES for s in scenes))
    if not ok:
        scenes = fallback_scenes(data)
    seed = int(datetime.datetime.now().timestamp()) % 1000003
    rng = random.Random(seed)
    names = list(THEMES)
    rng.shuffle(names)
    prefer = {"hook": names[0], "stat": names[1], "cards": names[2], "cta": names[1]}
    HUE["deg"] = rng.choice([0, 0, 28, 62, 110, 150, 185, 215, 250, 285, 320, 345])
    print("hue shift:", HUE["deg"])
    LAYOUT.update(cap=rng.randint(0, 2), trans=rng.randint(0, 3), hook=0, cards=0,
                  card=rng.randint(0, 3), news=rng.randint(0, 2), stat=rng.randint(0, 2), quote=rng.randint(0, 2),
                  align=rng.randint(0, 1), kick=rng.randint(0, 2), brand=rng.randint(0, 1))
    global FONT_PATH
    cands = [p for p in ("/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf", "/usr/share/fonts/opentype/inter/Inter-Black.otf",
                         "/usr/share/fonts/opentype/inter/InterDisplay-ExtraBold.otf") if os.path.exists(p)]
    if cands:
        FONT_PATH = rng.choice(cands)
        _fonts.clear()
    print("layout:", LAYOUT, os.path.basename(FONT_PATH))
    ctx = " ".join(s.get("voice", "") for s in scenes) + " " + " ".join(data.get("keywords", []))
    prev_th, prev_decor, st_alt = None, None, 0
    for i, s in enumerate(scenes):
        th = prefer.get(s["type"])
        if th is None:
            th = names[2] if st_alt % 2 else names[1]
            st_alt += 1
        if th == prev_th:
            th = rng.choice([x for x in THEMES if x != prev_th])
        decor = rng.choice([x for x in DECORS if x != prev_decor])
        s["_pal"], s["_style"], s["_seed"] = th, decor, rng.randint(0, 9)
        s["_title"] = data.get("title", "")
        s["_context"] = ctx
        prev_th, prev_decor = th, decor
    return scenes


def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        data = json.load(f)
    audio = f"{OUT}/narration.mp3"
    total = audio_duration(audio)
    full = total + END_CARD

    scenes = prepare_scenes(data)
    tokens_per_scene = [s["voice"].split() for s in scenes]
    tokens = [t for ts in tokens_per_scene for t in ts]

    t_s, t_e = speech_span(audio, total)
    words = build_words(tokens, t_s, t_e)

    # scene boundaries from word timing
    bounds, k = [], 0
    for i, ts in enumerate(tokens_per_scene):
        start = 0.0 if i == 0 else words[k][1]
        k += len(ts)
        bounds.append(start)
    bounds.append(full)
    for i, s in enumerate(scenes):
        s["_t0"], s["_t1"] = bounds[i], bounds[i + 1]
    scenes[-1]["_t1"] = full

    # caption chunks (global)
    cap_font = font(64)
    raw_chunks = make_chunks(words, cap_font)
    chunks = []
    for ci, ch in enumerate(raw_chunks):
        t0 = ch[0][1] if ci > 0 else 0.0
        t1 = raw_chunks[ci + 1][0][1] if ci + 1 < len(raw_chunks) else total
        chunks.append({"words": ch, "t0": t0, "t1": t1, "next": {}})

    def scene_index(t):
        for i, s in enumerate(scenes):
            if t < s["_t1"]:
                return i
        return len(scenes) - 1

    def frame(t):
        i = scene_index(t)
        s = scenes[i]
        lt = t - s["_t0"]
        dur = s["_t1"] - s["_t0"]
        cur = render_scene(s, lt, dur, t)
        th = s["_pal"]
        if i + 1 < len(scenes) and s["_t1"] - t < TR:
            p = e_out(1 - (s["_t1"] - t) / TR)
            n = scenes[i + 1]
            nxt = render_scene(n, 0.0, n["_t1"] - n["_t0"], t)
            tr = LAYOUT["trans"]
            if tr == 0:
                canvas = Image.new("RGB", (W, H))
                canvas.paste(cur, (int(-W * 0.35 * p), 0))
                canvas.paste(nxt, (int(W * (1 - p)), 0))
            elif tr == 3:
                canvas = Image.new("RGB", (W, H))
                canvas.paste(cur, (int(W * 0.35 * p), 0))
                canvas.paste(nxt, (int(-W * (1 - p)), 0))
            elif tr == 1:
                canvas = Image.new("RGB", (W, H))
                canvas.paste(cur, (0, int(-H * 0.3 * p)))
                canvas.paste(nxt, (0, int(H * (1 - p))))
            else:
                sc_ = 1.0 + 0.18 * (1 - p)
                z = nxt.resize((int(W * sc_), int(H * sc_)), Image.BILINEAR)
                z = z.crop(((z.size[0] - W) // 2, (z.size[1] - H) // 2, (z.size[0] - W) // 2 + W, (z.size[1] - H) // 2 + H))
                canvas = Image.blend(cur, z, p)
            cur = canvas
            th = n["_pal"] if p > 0.5 else th
        cur = add_grain(cur)
        draw_chrome(cur, t, full, chunks, th)
        return hue_rotate(cur, HUE["deg"])

    events = []
    for i, s in enumerate(scenes):
        t0 = s["_t0"]
        if i == 0:
            events.append(("thump", 0.05))
        else:
            events.append(("whoosh", t0 - TR - 0.05))
        if s["type"] == "cards":
            for k in range(min(3, len(s.get("items", [])))):
                events.append(("pop", t0 + 0.25 + 0.22 * k + 0.08))
        elif s["type"] == "stat":
            events.append(("rise", t0 + 0.1))
            events.append(("ding", t0 + 1.15))
        elif s["type"] == "cta":
            events.append(("chime", t0 + 0.55))
    for c in chunks:
        events.append(("tick", c["t0"]))
    import datetime
    seed = int(datetime.datetime.now().timestamp() * 7) % 9973
    print("mixing audio...")
    build_audio_mix(audio, full, events, seed, f"{OUT}/mix.wav")

    n_frames = int(full * FPS) + 1
    os.makedirs(OUT, exist_ok=True)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", f"{OUT}/mix.wav",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-t", f"{full:.2f}", f"{OUT}/video.mp4"],
        stdin=subprocess.PIPE)
    for fi in range(n_frames):
        enc.stdin.write(frame(fi / FPS).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    print("done:", f"{OUT}/video.mp4", f"({full:.1f}s, {len(scenes)} scenes)")


if __name__ == "__main__":
    main()
