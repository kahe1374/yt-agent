import array
import json
import math
import os
import re
import subprocess
import textwrap

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

THEMES = {
    "light": dict(fg=INK, sub=(90, 80, 74), accent=ORANGE, card=(255, 255, 255)),
    "sun": dict(fg=(255, 255, 255), sub=(255, 236, 224), accent=INK, card=(255, 255, 255)),
    "dark": dict(fg=(255, 255, 255), sub=(200, 190, 215), accent=(251, 146, 60), card=(36, 28, 52)),
}


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


def _glow(color, r):
    sp = Image.new("L", (r * 2, r * 2), 0)
    ImageDraw.Draw(sp).ellipse([r // 3, r // 3, r * 2 - r // 3, r * 2 - r // 3], fill=255)
    return sp.filter(ImageFilter.GaussianBlur(r // 3)), color


def background(theme, t):
    if theme == "light":
        if "light" not in _bg_cache:
            _bg_cache["light"] = _gradient((253, 246, 238), (251, 226, 208))
        im = _bg_cache["light"].copy()
        d = ImageDraw.Draw(im)
        for i in range(7):
            x = (i * 190 + 60 * math.sin(t * 0.6 + i)) % W
            y = (i * 310 + 40 * math.cos(t * 0.5 + i * 2)) % H
            r = 5 + (i % 3) * 3
            d.ellipse([x - r, y - r, x + r, y + r], fill=(240, 190, 160))
        return im
    if theme == "sun":
        im = Image.new("RGB", (W, H), (240, 98, 58))
        d = ImageDraw.Draw(im)
        cx, cy = W / 2, 820
        n = 22
        for i in range(0, n, 2):
            a0 = (i / n) * 2 * math.pi + t * 0.12
            a1 = ((i + 1) / n) * 2 * math.pi + t * 0.12
            R = 2600
            d.polygon([(cx, cy), (cx + R * math.cos(a0), cy + R * math.sin(a0)),
                       (cx + R * math.cos(a1), cy + R * math.sin(a1))], fill=(250, 128, 84))
        return im
    if "dark" not in _bg_cache:
        _bg_cache["dark"] = _gradient((12, 7, 22), (38, 14, 40))
        _bg_cache["dark_glow"] = _glow((220, 80, 40), 520)
    im = _bg_cache["dark"].copy()
    sp, col = _bg_cache["dark_glow"]
    gx = int(lerp(300, 780, (math.sin(t * 0.5) + 1) / 2))
    gy = int(lerp(1100, 1500, (math.cos(t * 0.4) + 1) / 2))
    im.paste(Image.new("RGB", sp.size, col), (gx - 520, gy - 520), sp.point(lambda v: int(v * 0.45)))
    d = ImageDraw.Draw(im)
    for i in range(14):
        x = (i * 83 + 7) % W
        y = (i * 197 + 40 * math.sin(t * 0.7 + i)) % H
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(150, 110, 150))
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


def kicker(im, d, text, th, lt, y=300):
    if not text:
        return
    p = e_out(lt / 0.4)
    f = font(36)
    tw = f.getlength(text)
    w, h = tw + 90, 80
    x = (W - w) / 2
    yy = y - (1 - p) * 40
    d.rounded_rectangle([x, yy, x + w, yy + h], radius=40, fill=(RED if th != "dark" else (60, 40, 70)))
    d.ellipse([x + 26, yy + 31, x + 44, yy + 49], fill=(255, 255, 255))
    d.text((x + 62, yy + h / 2), text, font=f, fill=(255, 255, 255), anchor="lm")


# ------------------------------------------------------------------ scenes

DEFAULT_KICKER = {"hook": "BREAKING TECH", "stat": "BY THE NUMBERS", "cards": "KEY POINTS",
                  "statement": "WHY IT MATTERS", "cta": ""}
SCENE_THEME = {"hook": "sun", "stat": "dark", "cards": "light", "statement": "dark", "cta": "dark"}


def scene_hook(im, d, sc, th, lt, dur):
    T = THEMES[th]
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["hook"]), th, lt, 330)
    size, lines = fit_text(sc.get("headline", ""), 900, 4, [168, 150, 132, 116, 100])
    lh = size * 1.18
    top = 960 - (len(lines) * lh) / 2 + 40
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.08 * i - 0.1) / 0.5)
        if p <= 0.01:
            continue
        s = size * (0.45 + 0.55 * p)
        line_with_accent(d, im, ln, sc.get("accent"), W / 2, top + i * lh + (1 - e_out(p)) * 50,
                         s, T["fg"], T["accent"], (255, 255, 255))


def scene_statement(im, d, sc, th, lt, dur):
    T = THEMES[th]
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["statement"]), th, lt, 330)
    q = e_back((lt - 0.05) / 0.5)
    if q > 0:
        d.text((90, 520 - (1 - e_out(q)) * 60), "“", font=font(420 * clamp(q, 0.3, 1.1)),
               fill=T["accent"], anchor="la")
    size, lines = fit_text(sc.get("headline", ""), 860, 6, [112, 100, 90, 80, 70])
    lh = size * 1.25
    top = 860
    for i, ln in enumerate(lines):
        p = e_out((lt - 0.12 - 0.09 * i) / 0.45)
        if p <= 0:
            continue
        f = font(size)
        words = ln.split()
        space = f.getlength(" ")
        x = 110 - (1 - p) * 90
        y = top + i * lh
        for w in words:
            wl = f.getlength(w)
            is_acc = sc.get("accent") and re.sub(r"\W", "", w).lower() == re.sub(r"\W", "", sc["accent"]).lower()
            if is_acc:
                d.rounded_rectangle([x - 14, y - size * 0.62, x + wl + 14, y + size * 0.62],
                                    radius=int(size * 0.28), fill=T["accent"])
                d.text((x, y), w, font=f, fill=(20, 12, 8) if th == "dark" else (255, 255, 255), anchor="lm")
            else:
                d.text((x, y), w, font=f, fill=T["fg"], anchor="lm")
            x += wl + space
    bar = e_out((lt - 0.4) / 0.6)
    d.rounded_rectangle([110, top + len(lines) * lh + 30, 110 + 420 * bar, top + len(lines) * lh + 46],
                        radius=8, fill=T["accent"])


def parse_value(v):
    m = re.search(r"\d[\d,]*\.?\d*", v)
    if not m:
        return None
    raw = m.group(0)
    return v[:m.start()], raw, v[m.end():]


def scene_stat(im, d, sc, th, lt, dur):
    T = THEMES[th]
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["stat"]), th, lt, 300)
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
        full_text = value
    else:
        shown = value
        full_text = value
    size = 360
    while font(size).getlength(full_text) > 940 and size > 120:
        size -= 12
    pop = e_back((lt - 0.05) / 0.5)
    d.text((W / 2, 760), shown, font=font(size * clamp(0.6 + 0.4 * pop, 0.6, 1.08)),
           fill=T["accent"], anchor="mm")
    lab = sc.get("label", "")
    ls, llines = fit_text(lab, 900, 3, [78, 70, 62, 54])
    for i, ln in enumerate(llines):
        a = e_out((lt - 0.5 - 0.1 * i) / 0.4)
        if a > 0:
            d.text((W / 2, 1000 + i * ls * 1.25 + (1 - a) * 30), ln, font=font(ls),
                   fill=mix((20, 12, 30), T["fg"], a), anchor="mm")
    # rising line chart
    prog = e_out((lt - 0.6) / 1.4)
    pts = []
    n = 40
    for i in range(n + 1):
        x = 130 + (W - 260) * i / n
        v = (i / n) ** 2.2 + 0.05 * math.sin(i * 0.9)
        pts.append((x, 1490 - 280 * v))
    k = int(len(pts) * prog)
    if k >= 2:
        d.line(pts[:k], fill=T["accent"], width=14, joint="curve")
        ex, ey = pts[k - 1]
        r = 18 + 5 * math.sin(lt * 8)
        d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=(255, 255, 255))


def scene_cards(im, d, sc, th, lt, dur):
    T = THEMES[th]
    kicker(im, d, sc.get("kicker", DEFAULT_KICKER["cards"]), th, lt, 230)
    heading = sc.get("heading", "")
    hs, hl = fit_text(heading, 900, 2, [88, 78, 70])
    for i, ln in enumerate(hl):
        a = e_out((lt - 0.05 * i) / 0.45)
        d.text((W / 2, 440 + i * hs * 1.2 + (1 - a) * 40), ln, font=font(hs), fill=T["fg"], anchor="mm")
    items = [str(x) for x in sc.get("items", [])][:3]
    ch, gap = 250, 40
    y0 = 640 if len(hl) == 1 else 690
    for i, it in enumerate(items):
        p = e_back((lt - 0.25 - 0.22 * i) / 0.55, 1.4)
        if p <= 0.02:
            continue
        y = y0 + i * (ch + gap) + (1 - clamp(p, 0, 1)) * 220
        card(im, 80, y, 920, ch, T["card"])
        d2 = ImageDraw.Draw(im)
        d2.ellipse([130, y + ch / 2 - 55, 240, y + ch / 2 + 55], fill=T["accent"])
        d2.text((185, y + ch / 2), str(i + 1), font=font(64), fill=(255, 255, 255), anchor="mm")
        s, lines = fit_text(it, 640, 3, [54, 48, 42, 38])
        lh = s * 1.22
        for j, ln in enumerate(lines):
            d2.text((280, y + ch / 2 - (len(lines) - 1) * lh / 2 + j * lh), ln, font=font(s),
                    fill=INK, anchor="lm")


def scene_cta(im, d, sc, th, lt, dur):
    T = THEMES[th]
    size, lines = fit_text(sc.get("headline", "Follow for daily tech news"), 900, 3, [124, 110, 96])
    for i, ln in enumerate(lines):
        p = e_back((lt - 0.1 * i) / 0.5)
        d.text((W / 2, 640 + i * size * 1.2), ln, font=font(size * clamp(0.5 + 0.5 * p, 0.5, 1.1)),
               fill=T["fg"], anchor="mm")
    p = e_back((lt - 0.5) / 0.5)
    pulse = 1 + 0.04 * math.sin(lt * 7)
    bw, bh = 700 * pulse * clamp(p, 0, 1.05), 170 * pulse * clamp(p, 0, 1.05)
    cy = 640 + len(lines) * size * 1.2 + 140
    if p > 0:
        d.rounded_rectangle([W / 2 - bw / 2, cy - bh / 2, W / 2 + bw / 2, cy + bh / 2], radius=60, fill=RED)
        d.text((W / 2, cy), "SUBSCRIBE", font=font(72 * clamp(p, 0.3, 1)), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, cy + 190), CHANNEL, font=font(52), fill=T["accent"], anchor="mm")


SCENES = {"hook": scene_hook, "statement": scene_statement, "stat": scene_stat,
          "cards": scene_cards, "cta": scene_cta}


def render_scene(sc, lt, dur, t_abs):
    th = sc["_theme"]
    im = background(th, t_abs)
    d = ImageDraw.Draw(im)
    SCENES[sc["type"]](im, d, sc, th, lt, dur)
    return im


# ------------------------------------------------------------------ captions & chrome

def draw_chrome(im, t, full, chunks, th):
    d = ImageDraw.Draw(im)
    T = THEMES[th]
    # brand tag
    d.ellipse([60, 92, 84, 116], fill=RED)
    d.text((100, 104), CHANNEL, font=font(34), fill=T["sub"], anchor="lm")
    # progress
    d.rectangle([0, 0, W * clamp(t / full), 10], fill=ORANGE)
    # captions
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
    cx, cy = W / 2, 1655 + (1 - clamp(p, 0, 1)) * 40
    d.rounded_rectangle([cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], radius=44, fill=(28, 22, 32))
    x = cx - tw / 2
    for (w, a, b) in cur["words"]:
        wl = fnt.getlength(w)
        if a <= t < b:
            d.rounded_rectangle([x - 16, cy - 56, x + wl + 16, cy + 56], radius=28, fill=ORANGE)
        d.text((x, cy + 2), w, font=fnt, fill=(255, 255, 255), anchor="lm")
        x += wl + space


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
    scenes = data.get("scenes")
    ok = (isinstance(scenes, list) and len(scenes) >= 3
          and all(isinstance(s, dict) and s.get("voice") and s.get("type") in SCENES for s in scenes))
    if not ok:
        scenes = fallback_scenes(data)
    alt = 0
    for i, s in enumerate(scenes):
        th = SCENE_THEME[s["type"]]
        if s["type"] == "statement":
            th = "light" if alt % 2 else "dark"
            alt += 1
        s["_theme"] = th
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
        th = s["_theme"]
        if i + 1 < len(scenes) and s["_t1"] - t < TR:
            p = e_out(1 - (s["_t1"] - t) / TR)
            n = scenes[i + 1]
            nxt = render_scene(n, 0.0, n["_t1"] - n["_t0"], t)
            canvas = Image.new("RGB", (W, H))
            canvas.paste(cur, (int(-W * 0.35 * p), 0))
            canvas.paste(nxt, (int(W * (1 - p)), 0))
            cur = canvas
            th = n["_theme"] if p > 0.5 else th
        draw_chrome(cur, t, full, chunks, th)
        return cur

    n_frames = int(full * FPS) + 1
    os.makedirs(OUT, exist_ok=True)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", audio,
         "-filter_complex", f"[1:a]apad=pad_dur={END_CARD + 1}[a]",
         "-map", "0:v", "-map", "[a]",
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
