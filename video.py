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

PALETTES = {
    "sunset":   dict(bg0=(240, 98, 58),  bg1=(252, 140, 96),  fg=(255, 255, 255), sub=(255, 236, 224), accent=(27, 22, 20),    pill=(27, 22, 20),    pill_fg=(255, 255, 255), card=(255, 255, 255)),
    "ocean":    dict(bg0=(14, 70, 160),  bg1=(40, 120, 215),  fg=(255, 255, 255), sub=(210, 230, 255), accent=(255, 205, 60),  pill=(255, 205, 60),  pill_fg=(27, 22, 20),    card=(255, 255, 255)),
    "violet":   dict(bg0=(84, 38, 176),  bg1=(130, 80, 225),  fg=(255, 255, 255), sub=(228, 214, 255), accent=(250, 204, 21),  pill=(250, 204, 21),  pill_fg=(27, 22, 20),    card=(255, 255, 255)),
    "mint":     dict(bg0=(12, 140, 112), bg1=(46, 186, 150),  fg=(255, 255, 255), sub=(214, 255, 240), accent=(20, 30, 40),    pill=(20, 30, 40),    pill_fg=(255, 255, 255), card=(255, 255, 255)),
    "rose":     dict(bg0=(206, 40, 104), bg1=(244, 92, 148),  fg=(255, 255, 255), sub=(255, 226, 238), accent=(255, 230, 120), pill=(255, 230, 120), pill_fg=(27, 22, 20),    card=(255, 255, 255)),
    "cream":    dict(bg0=(253, 246, 238), bg1=(251, 222, 202), fg=(27, 22, 20),   sub=(90, 80, 74),    accent=(234, 88, 12),   pill=(234, 88, 12),   pill_fg=(255, 255, 255), card=(255, 255, 255)),
    "sky":      dict(bg0=(232, 244, 255), bg1=(190, 222, 252), fg=(15, 30, 60),   sub=(60, 80, 120),   accent=(37, 99, 235),   pill=(37, 99, 235),   pill_fg=(255, 255, 255), card=(255, 255, 255)),
    "night":    dict(bg0=(12, 7, 22),    bg1=(44, 16, 46),    fg=(255, 255, 255), sub=(200, 190, 215), accent=(251, 146, 60),  pill=(251, 146, 60),  pill_fg=(20, 12, 8),     card=(255, 255, 255)),
    "midnight": dict(bg0=(6, 12, 30),    bg1=(16, 38, 84),    fg=(255, 255, 255), sub=(190, 210, 240), accent=(34, 211, 238),  pill=(34, 211, 238),  pill_fg=(10, 20, 30),    card=(255, 255, 255)),
}
STYLES = ["rays", "blobs", "stripes", "rings", "dots", "waves", "glow"]


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


def make_chunks(words, fnt, max_w=860, max_words=4):
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


# ------------------------------------------------------------------ backgrounds

_bg_cache = {}


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


def _base(pal_name, vertical=True):
    key = ("base", pal_name)
    if key not in _bg_cache:
        P = PALETTES[pal_name]
        _bg_cache[key] = _gradient(P["bg0"], P["bg1"])
    return _bg_cache[key].copy()


def background(pal_name, style, t, seed=0):
    P = PALETTES[pal_name]
    light = pal_name in ("cream", "sky")
    im = _base(pal_name)
    d = ImageDraw.Draw(im)
    hi = mix(P["bg1"], (255, 255, 255), 0.18 if not light else 0.0)
    lo = mix(P["bg0"], (0, 0, 0), 0.12 if not light else 0.0)
    tint = mix(P["bg1"], P["accent"], 0.25)

    if style == "rays":
        cx, cy = W / 2, 760 + (seed % 5) * 40
        n = 22 + (seed % 3) * 4
        for i in range(0, n, 2):
            a0 = (i / n) * 2 * math.pi + t * 0.12
            a1 = ((i + 1) / n) * 2 * math.pi + t * 0.12
            R = 2600
            d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)),
                       (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=hi if not light else tint)
    elif style == "blobs":
        key = ("blob", 330)
        if key not in _bg_cache:
            _bg_cache[key] = _blob_sprite(330)
        sp = _bg_cache[key]
        for i in range(4):
            bx = W / 2 + 420 * math.sin(t * (0.25 + 0.07 * i) + i * 1.9 + seed)
            by = H / 2 + 700 * math.sin(t * (0.19 + 0.05 * i) + i * 2.7 + seed * 0.7)
            col = tint if i % 2 == 0 else hi
            im.paste(Image.new("RGB", sp.size, col), (int(bx - 330), int(by - 330)),
                     sp.point(lambda v_: int(v_ * 0.55)))
    elif style == "stripes":
        off = (t * 60) % 240
        for k in range(-12, 22):
            x0 = k * 240 + off
            d.polygon([(x0, 0), (x0 + 110, 0), (x0 + 110 - 700, H), (x0 - 700, H)], fill=hi if k % 2 == 0 else lo)
    elif style == "rings":
        cx, cy = W / 2, 820
        for k in range(14, 0, -1):
            r = ((k * 150 + t * 90) % 2100)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hi if not light else tint, width=26)
    elif style == "dots":
        step = 90
        for gy in range(0, H + step, step):
            for gx in range(0, W + step, step):
                ph = math.sin(t * 1.6 + gx * 0.012 + gy * 0.009 + seed)
                r = 7 + 6 * ph
                d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=hi if not light else tint)
    elif style == "waves":
        for band in range(4):
            col = mix(P["bg0"], P["bg1"], 0.25 + band * 0.22) if not light else mix(P["bg0"], tint, 0.25 + band * 0.2)
            base_y = 1050 + band * 190
            pts = [(0, H)]
            for x in range(0, W + 40, 40):
                pts.append((x, base_y + 70 * math.sin(x * 0.006 + t * (0.8 + band * 0.3) + band * 1.3 + seed)))
            pts.append((W, H))
            d.polygon(pts, fill=col)
    else:  # glow
        key = ("blob", 520)
        if key not in _bg_cache:
            _bg_cache[key] = _blob_sprite(520)
        sp = _bg_cache[key]
        gx = int(lerp(300, 780, (math.sin(t * 0.5 + seed) + 1) / 2))
        gy = int(lerp(900, 1400, (math.cos(t * 0.4 + seed) + 1) / 2))
        im.paste(Image.new("RGB", sp.size, tint if light else mix(P["accent"], P["bg1"], 0.4)),
                 (gx - 520, gy - 520), sp.point(lambda v_: int(v_ * 0.5)))
        d = ImageDraw.Draw(im)
        for i in range(14):
            x = (i * 83 + 7) % W
            y = (i * 197 + 40 * math.sin(t * 0.7 + i)) % H
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=hi)
    return im


# ------------------------------------------------------------------ drawing helpers

_shadow_cache = {}


def card(im, x, y, w, h, fill, radius=44, shadow=True):
    if shadow:
        key = (w, h, radius)
        if key not in _shadow_cache:
            sp = Image.new("L", (w + 120, h + 120), 0)
            ImageDraw.Draw(sp).rounded_rectangle([60, 60, 60 + w, 60 + h], radius=radius, fill=120)
            _shadow_cache[key] = sp.filter(ImageFilter.GaussianBlur(22))
        sp = _shadow_cache[key]
        im.paste(Image.new("RGB", sp.size, (40, 20, 10)), (int(x - 60), int(y - 60 + 18)), sp)
    ImageDraw.Draw(im).rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill)


def centered(d, text, cx, cy, size, fill):
    d.text((cx, cy), text, font=font(size), fill=fill, anchor="mm")


def line_with_accent(d, im, line, accent, cx, cy, size, fg, pill, pill_fg, pop=1.0):
    """Draw one centered line; the accent word gets a rounded pill behind it."""
    f = font(size)
    words = line.split()
    space = f.getlength(" ")
    total = sum(f.getlength(w) for w in words) + space * (len(words) - 1)
    x = cx - total / 2
    for w in words:
        wl = f.getlength(w)
        is_acc = accent and re.sub(r"\W", "", w).lower() == re.sub(r"\W", "", accent).lower()
        if is_acc:
            pad = size * 0.22
            d.rounded_rectangle([x - pad, cy - size * 0.62, x + wl + pad, cy + size * 0.62],
                                radius=int(size * 0.3), fill=pill)
            d.text((x, cy), w, font=f, fill=pill_fg, anchor="lm")
        else:
            d.text((x, cy), w, font=f, fill=fg, anchor="lm")
        x += wl + space


def kicker(im, d, text, P, lt, y=300):
    if not text:
        return
    p = e_out(lt / 0.4)
    f = font(36)
    tw = f.getlength(text)
    w, h = tw + 90, 80
    x = (W - w) / 2
    yy = y - (1 - p) * 40
    d.rounded_rectangle([x, yy, x + w, yy + h], radius=40, fill=(24, 18, 30))
    d.ellipse([x + 26, yy + 31, x + 44, yy + 49], fill=RED)
    d.text((x + 62, yy + h / 2), text, font=f, fill=(255, 255, 255), anchor="lm")


# ------------------------------------------------------------------ scenes

DEFAULT_KICKER = {"hook": "BREAKING TECH", "stat": "BY THE NUMBERS", "cards": "KEY POINTS",
                  "statement": "WHY IT MATTERS", "cta": ""}


def accent_match(word, accent):
    return bool(accent) and re.sub(r"\W", "", word).lower() == re.sub(r"\W", "", accent).lower()


def scene_hook(im, d, sc, P, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["hook"]), P, lt, 300)
    size, lines = fit_text(sc.get("headline", ""), 900, 4, [168, 150, 132, 116, 100])
    lh = size * 1.18
    top = 820 - (len(lines) * lh) / 2 + 40
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.08 * i - 0.1) / 0.5)
        if p <= 0.01:
            continue
        s = size * (0.45 + 0.55 * p)
        line_with_accent(d, im, ln, sc.get("accent"), W / 2, top + i * lh + (1 - e_out(p)) * 50,
                         s, P["fg"], P["pill"], P["pill_fg"])


def scene_statement(im, d, sc, P, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["statement"]), P, lt, 300)
    q = e_back((lt - 0.05) / 0.5)
    if q > 0:
        d.text((90, 470 - (1 - e_out(q)) * 60), "\u201c", font=font(380 * clamp(q, 0.3, 1.1)),
               fill=P["accent"], anchor="la")
    size, lines = fit_text(sc.get("headline", ""), 860, 5, [108, 96, 86, 76, 66])
    lh = size * 1.25
    top = 760
    for i, ln in enumerate(lines):
        p = e_out((lt - 0.12 - 0.09 * i) / 0.45)
        if p <= 0:
            continue
        f = font(size)
        space = f.getlength(" ")
        x = 110 - (1 - p) * 90
        y = top + i * lh
        for w in ln.split():
            wl = f.getlength(w)
            if accent_match(w, sc.get("accent")):
                d.rounded_rectangle([x - 14, y - size * 0.62, x + wl + 14, y + size * 0.62],
                                    radius=int(size * 0.28), fill=P["pill"])
                d.text((x, y), w, font=f, fill=P["pill_fg"], anchor="lm")
            else:
                d.text((x, y), w, font=f, fill=P["fg"], anchor="lm")
            x += wl + space
    bar = e_out((lt - 0.4) / 0.6)
    yb = top + len(lines) * lh + 20
    d.rounded_rectangle([110, yb, 110 + 420 * bar, yb + 16], radius=8, fill=P["accent"])


def parse_value(v):
    m = re.search(r"\d[\d,]*\.?\d*", v)
    if not m:
        return None
    return v[:m.start()], m.group(0), v[m.end():]


def scene_stat(im, d, sc, P, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["stat"]), P, lt, 300)
    value = str(sc.get("value", "")).strip()
    pv = parse_value(value)
    p = e_out((lt - 0.1) / 1.1)
    if pv:
        pre, raw, suf = pv
        target = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        cur = target * p
        num = f"{cur:,.{decimals}f}" if "," in raw else f"{cur:.{decimals}f}"
        shown = pre + num + suf
    else:
        shown = value
    size = 360
    while font(size).getlength(value) > 940 and size > 120:
        size -= 12
    pop = e_back((lt - 0.05) / 0.5)
    d.text((W / 2, 640), shown, font=font(size * clamp(0.6 + 0.4 * pop, 0.6, 1.08)),
           fill=P["accent"], anchor="mm")
    ls, llines = fit_text(sc.get("label", ""), 900, 3, [78, 70, 62, 54])
    for i, ln in enumerate(llines):
        a = e_out((lt - 0.5 - 0.1 * i) / 0.4)
        if a > 0:
            d.text((W / 2, 860 + i * ls * 1.25 + (1 - a) * 30), ln, font=font(ls),
                   fill=mix(P["bg0"], P["fg"], a), anchor="mm")
    prog = e_out((lt - 0.6) / 1.4)
    pts, n = [], 40
    for i in range(n + 1):
        x = 130 + (W - 260) * i / n
        v_ = (i / n) ** 2.2 + 0.05 * math.sin(i * 0.9)
        pts.append((x, 1290 - 240 * v_))
    k = int(len(pts) * prog)
    if k >= 2:
        d.line(pts[:k], fill=P["accent"], width=14, joint="curve")
        ex, ey = pts[k - 1]
        r = 18 + 5 * math.sin(lt * 8)
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(255, 255, 255))


def scene_cards(im, d, sc, P, lt, dur):
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["cards"]), P, lt, 230)
    hs, hl = fit_text(sc.get("heading", ""), 900, 2, [88, 78, 70])
    for i, ln in enumerate(hl):
        a = e_out((lt - 0.05 * i) / 0.45)
        d.text((W / 2, 420 + i * hs * 1.2 + (1 - a) * 40), ln, font=font(hs), fill=P["fg"], anchor="mm")
    items = [str(x) for x in sc.get("items", [])][:3]
    ch, gap = 215, 30
    y0 = 530 if len(hl) == 1 else 590
    for i, it in enumerate(items):
        p = e_back((lt - 0.25 - 0.22 * i) / 0.55, 1.4)
        if p <= 0.02:
            continue
        y = y0 + i * (ch + gap) + (1 - clamp(p, 0, 1)) * 220
        card(im, 80, y, 880, ch, P["card"])
        d2 = ImageDraw.Draw(im)
        d2.ellipse([125, y + ch / 2 - 48, 221, y + ch / 2 + 48], fill=P["pill"])
        d2.text((173, y + ch / 2), str(i + 1), font=font(56), fill=P["pill_fg"], anchor="mm")
        s, lines = fit_text(it, 600, 3, [48, 44, 40, 36])
        lh = s * 1.22
        for j, ln in enumerate(lines):
            d2.text((265, y + ch / 2 - (len(lines) - 1) * lh / 2 + j * lh), ln, font=font(s),
                    fill=INK, anchor="lm")


def scene_cta(im, d, sc, P, lt, dur):
    size, lines = fit_text(sc.get("headline", "Follow for daily tech news"), 900, 3, [124, 110, 96])
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.1 * i) / 0.5)
        d.text((W / 2, 560 + i * size * 1.2), ln, font=font(size * clamp(0.5 + 0.5 * p, 0.5, 1.1)),
               fill=P["fg"], anchor="mm")
    p = e_back((lt - 0.5) / 0.5)
    pulse = 1 + 0.04 * math.sin(lt * 7)
    bw, bh = 700 * pulse * clamp(p, 0, 1.05), 170 * pulse * clamp(p, 0, 1.05)
    cy = 560 + len(lines) * size * 1.2 + 120
    if p > 0:
        d.rounded_rectangle([W / 2 - bw / 2, cy - bh / 2, W / 2 + bw / 2, cy + bh / 2], radius=60, fill=RED)
        d.text((W / 2, cy), "SUBSCRIBE", font=font(72 * clamp(p, 0.3, 1)), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, cy + 180), CHANNEL, font=font(52), fill=P["accent"] if P["accent"] != INK else P["fg"],
           anchor="mm")


SCENES = {"hook": scene_hook, "statement": scene_statement, "stat": scene_stat,
          "cards": scene_cards, "cta": scene_cta}


def render_scene(sc, lt, dur, t_abs):
    pal = sc["_pal"]
    im = background(pal, sc["_style"], t_abs, sc["_seed"])
    d = ImageDraw.Draw(im)
    SCENES[sc["type"]](im, d, sc, PALETTES[pal], lt, dur)
    return im


# ------------------------------------------------------------------ captions & chrome

CAP_CY = 1470  # safe zone: above the Shorts UI (channel name / buttons)


def draw_chrome(im, t, full, chunks, pal):
    d = ImageDraw.Draw(im)
    P = PALETTES[pal]
    d.ellipse([60, 152, 84, 176], fill=RED)
    d.text((100, 164), CHANNEL, font=font(34), fill=P["sub"], anchor="lm")
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
    d.rounded_rectangle([cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], radius=44, fill=(28, 22, 32))
    x = cx - tw / 2
    for (w, a, b) in cur["words"]:
        wl = fnt.getlength(w)
        if a <= t < b:
            d.rounded_rectangle([x - 16, cy - 56, x + wl + 16, cy + 56], radius=28, fill=ORANGE)
        d.text((x, cy + 2), w, font=fnt, fill=(255, 255, 255), anchor="lm")
        x += wl + space


# ------------------------------------------------------------------ sound design

SR = 44100


def _env_exp(n, rate):
    return np.exp(-np.arange(n) / SR * rate)


def synth_music(length, seed):
    import random
    rng = random.Random(seed)
    bpm = rng.choice([88, 92, 96, 100])
    beat = 60.0 / bpm
    bar = beat * 4
    progs = [
        [[57, 60, 64, 67], [53, 57, 60, 64], [60, 64, 67, 71], [55, 59, 62, 67]],   # Am7 Fmaj7 Cmaj7 G
        [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]],   # C G Am F
        [[62, 65, 69, 72], [58, 62, 65, 70], [53, 57, 60, 65], [60, 64, 67, 72]],   # Dm7 Bb F C
    ]
    prog = rng.choice(progs)
    out = np.zeros(int(length * SR) + SR)

    def add(sig, t):
        i = int(t * SR)
        if i >= len(out):
            return
        j = min(len(out), i + len(sig))
        out[i:j] += sig[: j - i]

    def f(m):
        return 440.0 * 2 ** ((m - 69) / 12.0)

    nbars = int(length / bar) + 2
    for b in range(nbars):
        t0 = b * bar
        chord = prog[b % 4]
        n = int(bar * SR)
        tt = np.arange(n) / SR
        env = np.minimum(1, tt / 0.35) * np.minimum(1, (bar - tt) / 0.3 + 0.0).clip(0, 1)
        pad = np.zeros(n)
        for m in chord:
            fr = f(m)
            pad += np.sin(2 * np.pi * fr * tt) + 0.5 * np.sin(2 * np.pi * fr * 1.004 * tt) \
                + 0.25 * np.sin(2 * np.pi * 2 * fr * tt)
        add(pad * env * 0.045, t0)
        root = f(chord[0] - 24)
        for k, pos in enumerate((0, 2, 2.75)):
            nn = int(beat * 1.6 * SR)
            tb = np.arange(nn) / SR
            bass = np.sin(2 * np.pi * root * tb) * np.exp(-tb * 2.6) * (0.26 if k < 2 else 0.16)
            add(bass, t0 + pos * beat)
        for pos in (0, 2):
            nn = int(0.32 * SR)
            tk = np.arange(nn) / SR
            phase = 2 * np.pi * np.cumsum(46 + 95 * np.exp(-tk * 32)) / SR
            add(np.sin(phase) * np.exp(-tk * 9) * 0.36, t0 + pos * beat)
        noise = np.random.RandomState(b).randn(int(0.05 * SR))
        hat = np.diff(noise, prepend=0) * np.exp(-np.arange(len(noise)) / SR * 110) * 0.6
        for pos in range(1, 8, 2):
            add(hat * 0.05, t0 + pos * beat / 2)
        arp = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[3] + 12, chord[2] + 12, chord[1] + 12, chord[3] + 12, chord[2] + 12]
        for k in range(8):
            nn = int(0.45 * SR)
            tp = np.arange(nn) / SR
            fr = f(arp[k] + 12)
            pl = (np.sin(2 * np.pi * fr * tp) + 0.3 * np.sin(2 * np.pi * 2 * fr * tp)) * np.exp(-tp * 7)
            add(pl * 0.075, t0 + k * beat / 2)
    out = out[: int(length * SR)]
    fade = np.ones(len(out))
    fi, fo = int(1.2 * SR), int(2.0 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-fo:] = np.linspace(1, 0, fo)
    out *= fade
    return out / (np.max(np.abs(out)) + 1e-9)


def sfx_whoosh(dur=0.6):
    n = int(dur * SR)
    x = np.random.RandomState(7).randn(n)
    y = np.zeros(n)
    p1 = p2 = 0.0
    for i in range(n):
        a = 0.004 + 0.07 * math.sin(math.pi * i / n) ** 2   # soft sweep, low cutoff
        p1 += a * (x[i] - p1)
        p2 += a * (p1 - p2)
        y[i] = p2
    env = np.sin(np.pi * np.arange(n) / n) ** 2
    y = y * env
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
    pk = np.max(np.abs(v)) or 1.0
    v = v / pk * 0.85
    mix_ = v.copy()

    if os.environ.get("MUSIC", "1") != "0":
        music = synth_music(N / SR, seed)
        env = smooth(np.abs(v), 0.08)
        ref = np.percentile(env, 95) or 1.0
        duck = 1.0 - 0.68 * smooth(np.clip(env / ref, 0, 1), 0.3)
        mix_ += music[:N] * duck * 0.30

    lib = {"whoosh": sfx_whoosh(), "pop": sfx_pop(), "thump": sfx_thump(), "rise": sfx_rise(),
           "ding": sfx_ding(), "chime": sfx_ding((1046.5, 1318.5, 1568.0), 0.09)}
    for kind, t in events:
        s = lib[kind]
        i = int(max(0.0, t) * SR)
        j = min(N, i + len(s))
        if i < N:
            mix_[i:j] += s[: j - i] * 0.7

    pk = np.max(np.abs(mix_))
    if pk > 0.9:
        mix_ *= 0.9 / pk
    pcm = (np.clip(mix_, -1, 1) * 32767).astype("<i2")
    st = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(st.tobytes())


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
    seed = datetime.date.today().toordinal() * 31 + sum(map(ord, data.get("title", "x")))
    rng = random.Random(seed)
    pal_names = list(PALETTES)
    prev_pal, prev_style = None, None
    for i, s in enumerate(scenes):
        pal = rng.choice([p for p in pal_names if p != prev_pal])
        style = rng.choice([x for x in STYLES if x != prev_style])
        s["_pal"], s["_style"], s["_seed"] = pal, style, rng.randint(0, 9)
        prev_pal, prev_style = pal, style
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
            canvas = Image.new("RGB", (W, H))
            canvas.paste(cur, (int(-W * 0.35 * p), 0))
            canvas.paste(nxt, (int(W * (1 - p)), 0))
            cur = canvas
            th = n["_pal"] if p > 0.5 else th
        draw_chrome(cur, t, full, chunks, th)
        return cur

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
    seed = sum(map(ord, data.get("title", "x"))) % 997
    print("mixing audio...")
    build_audio_mix(audio, full, events, seed, f"{OUT}/mix.wav")

    n_frames = int(full * FPS) + 1
    os.makedirs(OUT, exist_ok=True)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", f"{OUT}/mix.wav",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
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
