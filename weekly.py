"""Weekly long-form video: ONE technology explained for beginners (landscape 1920x1080, ~4-6 min).

Run by .github/workflows/weekly-video.yml. Needs OPENROUTER_API_KEY and internet (Wikipedia is the fact source).
Topic = the technology most related to this week's daily stories (archive/*.json), never one used recently
(archive/weekly_topics.json). If no source text can be fetched or the AI output fails validation, nothing is
published that week (made=false) instead of publishing unchecked facts.
"""
import asyncio
import datetime
import functools
import glob
import json
import os
import re
import subprocess
import time
import urllib.parse

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import main as daily  # reuse models, voice, helpers

OUT = os.environ.get("OUT_DIR", "output_weekly")
ARCHIVE = "archive"
TOPICS_FILE = f"{ARCHIVE}/weekly_topics.json"
W, H, FPS = 1920, 1080, 30
PAD = 0.6  # seconds of silence after every spoken segment
CHANNEL = os.environ.get("CHANNEL_NAME", "TREND PULSE DAILY")
WIKI_UA = {"User-Agent": "TrendPulseDailyBot/1.0 (https://github.com/kahe1374/yt-agent; explainer source)"}

PALETTES = [((10, 22, 48), (0, 150, 255)), ((26, 12, 44), (255, 94, 98)), ((8, 36, 36), (30, 214, 160)),
            ((38, 20, 10), (255, 176, 32)), ((16, 16, 44), (140, 110, 255)), ((10, 30, 54), (34, 211, 238))]

# id, video topic, Wikipedia page, regex of words that make it relevant to the week's news
TOPICS = [
    ("llm", "Large Language Models", "Large language model", r"chatgpt|openai|gemini|claude|llm|language model|chatbot|copilot"),
    ("imagegen", "AI Image Generation", "Text-to-image model", r"image generat|midjourney|dall-e|sora|video generat|diffusion"),
    ("ml", "Machine Learning", "Machine learning", r"\bai\b|artificial intelligence|machine learning|neural|model"),
    ("foldable", "Foldable Phones", "Foldable smartphone", r"foldable|galaxy z|fold|flip"),
    ("wireless_charging", "Wireless Charging", "Wireless power transfer", r"wireless charg|qi2|magsafe|charging pad"),
    ("nfc", "NFC and Tap to Pay", "Near-field communication", r"\bnfc\b|tap to pay|contactless|apple pay|google wallet"),
    ("chips", "How Computer Chips Are Made", "Semiconductor device fabrication", r"chip|tsmc|foundry|semiconductor|fab\b|wafer"),
    ("gpu", "GPUs", "Graphics processing unit", r"gpu|nvidia|geforce|rtx|graphics card|radeon"),
    ("5g", "5G", "5G", r"\b5g\b|\b6g\b|spectrum|carrier|cellular|verizon|t-mobile"),
    ("starlink", "Satellite Internet", "Starlink", r"starlink|satellite internet|spacex|leo satellite"),
    ("wifi", "Wi-Fi", "Wi-Fi", r"wi-fi|wifi|router|mesh network"),
    ("bluetooth", "Bluetooth", "Bluetooth", r"bluetooth|airpods|earbuds|wireless headphone"),
    ("usbc", "USB-C", "USB-C", r"usb-c|usb c|thunderbolt|usb4|charging port|lightning port"),
    ("liion", "Lithium-Ion Batteries", "Lithium-ion battery", r"battery|lithium|ev range|power bank"),
    ("solidstate", "Solid-State Batteries", "Solid-state battery", r"solid-state|solid state battery"),
    ("selfdriving", "Self-Driving Cars", "Self-driving car", r"self-driving|autonomous|waymo|robotaxi|\bfsd\b|autopilot"),
    ("mfa", "Two-Factor Authentication", "Multi-factor authentication", r"2fa|passkey|password|authentication|account takeover|login"),
    ("e2ee", "End-to-End Encryption", "End-to-end encryption", r"encrypt|whatsapp|signal app|privacy|e2e"),
    ("blockchain", "Blockchain", "Blockchain", r"blockchain|crypto|bitcoin|ethereum"),
    ("quantum", "Quantum Computing", "Quantum computing", r"quantum|qubit"),
    ("ar", "Augmented Reality", "Augmented reality", r"augmented reality|smart glasses|ar glasses|vision pro|hololens"),
    ("vr", "Virtual Reality", "Virtual reality", r"\bvr\b|virtual reality|quest 3|headset"),
    ("cloud", "Cloud Computing", "Cloud computing", r"cloud|aws|azure|data center|datacenter"),
    ("oled", "OLED Screens", "OLED", r"oled|amoled|display|screen panel|refresh rate"),
    ("face", "Facial Recognition", "Facial recognition system", r"face id|facial recognition|face unlock"),
    ("opensource", "Open-Source Software", "Open-source software", r"open source|open-source|linux|github"),
]

SYSTEM_PROMPT = """You write the script for a 4 to 6 minute YouTube explainer video for beginners on a faceless English channel about technology.
You get one TOPIC, a SOURCE text (from Wikipedia), and optionally a few NEWS HEADLINES the channel covered this week.

STRICT RULES
- Every factual claim, number, date, name and definition must come from the SOURCE text. Never add facts, figures or dates from your own memory.
- You MAY use simple everyday analogies to help understanding ("think of it like..."), but never put numbers or facts inside an analogy.
- If the SOURCE does not cover something, leave it out. Do not guess.
- Beginner level: short sentences, simple words, define every technical term the first time you use it.
- Plain ASCII English only. No emojis, no markdown, no hashtags.
- Do not mention Wikipedia or the SOURCE in the narration.
- NEWS HEADLINES may be mentioned in the intro only (at most one, using its meaning exactly as given). Never invent news.

STRUCTURE
- Sections go in a logical order for a beginner: what it is, why it matters, how it works (can be 2 sections), where you see it / what it is used for, limits or challenges (only if in the SOURCE).
- 5 or 6 sections. Each narration 80 to 120 words. Each section has exactly 3 points (short facts, max 10 words each) that match what the narration says.

VISUAL: 8 to 16 words describing ONE physical object or real-world scene that fits the narration, for example 'a close-up of a shiny battery cell on a workbench'. No text, letters, numbers, logos or brand names, no people's faces, and do not use the topic name.

OUTPUT: one valid JSON object, nothing else, no code fences:
{
  "title": "YouTube title, max 80 characters, like 'How Solid-State Batteries Work (Explained Simply)'",
  "description": "2 short lines saying what the viewer will learn",
  "tags": ["6 to 10 tags"],
  "intro_visual": "VISUAL idea for the intro background",
  "intro": "35 to 50 words: a hook question or surprising fact from the SOURCE, then say what the video explains",
  "sections": [
    {"heading": "max 7 words", "points": ["fact 1", "fact 2", "fact 3"], "narration": "80 to 120 words", "visual": "VISUAL idea"}
  ],
  "outro": "20 to 30 words: quick recap, ask the viewer to subscribe for a new tech story every day"
}"""


# ------------------------------------------------------------------ topic + source

def load_archive():
    today = datetime.date.today()
    rows = []
    for p in sorted(glob.glob(f"{ARCHIVE}/20*.json")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.json$", p)
        if not m:
            continue
        try:
            d = datetime.date.fromisoformat(m.group(1))
            if 0 <= (today - d).days <= 7:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("title"):
                    rows.append((d, data))
        except Exception as e:  # noqa: BLE001
            print("skip bad archive file", p, e)
    return rows


def used_topics():
    try:
        with open(TOPICS_FILE, encoding="utf-8") as f:
            return [str(x) for x in json.load(f)]
    except Exception:  # noqa: BLE001
        return []


def rank_topics(rows):
    """Topics ordered by relevance to this week's news; unused first. Ties/zero scores rotate by ISO week."""
    text = " ".join(f"{d['title']} {' '.join(d.get('keywords', []))} {d.get('description', '')} {d.get('narration', '')}"
                    for _, d in rows).lower()
    used = used_topics()
    week = datetime.date.today().isocalendar()[1]
    scored = []
    for i, (tid, name, page, pat) in enumerate(TOPICS):
        score = len(re.findall(pat, text))
        recent_penalty = 1 if tid in used[-8:] else 0
        scored.append((recent_penalty, -score, (i - week) % len(TOPICS), tid, name, page))
    scored.sort()
    return [(tid, name, page, -ns) for (_rp, ns, _r, tid, name, page) in scored]


def fetch_wiki(page):
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=40, params={
        "action": "query", "format": "json", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": page})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    pg = next(iter(pages.values()), {})
    text = pg.get("extract") or ""
    text = re.split(r"\n==+\s*(?:See also|References|External links|Further reading|Notes|Citations|Bibliography)\s*==+", text)[0]
    text = re.sub(r"\n=+\s*([^=\n]+?)\s*=+\n", r"\n\1.\n", text)
    text = re.sub(r"\[\d+\]|\[citation needed\]", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return pg.get("title", page), text[:14000]


def numbers(s):
    return {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d,\.]*", s)} - {""}


def validate(data, source):
    sections = data.get("sections")
    if not isinstance(sections, list) or not (4 <= len(sections) <= 7):
        raise ValueError("bad section count")
    allowed = numbers(source)
    out, texts = [], [data.get("intro", ""), data.get("outro", ""), data.get("title", ""), data.get("description", "")]
    for s in sections:
        n = daily.normalize(str(s.get("narration", "")))
        pts = [daily.normalize(str(x)) for x in (s.get("points") or [])][:3]
        if len(n) < 350 or len(pts) < 2 or not s.get("heading"):
            raise ValueError("section too thin")
        texts += [n, " ".join(pts)]
        out.append({"heading": daily.normalize(str(s["heading"]))[:70], "points": pts, "narration": n,
                    "visual": clean_visual(s.get("visual"))})
    intro = daily.normalize(str(data.get("intro", "")))
    outro = daily.normalize(str(data.get("outro", "")))
    if len(intro) < 120 or len(outro) < 80:
        raise ValueError("intro/outro too short")
    full = " ".join(texts)
    if not daily.is_ascii(full):
        raise ValueError("non-ascii text")
    bad = numbers(full) - allowed
    if bad:
        raise ValueError(f"numbers not in source: {sorted(bad)[:5]}")
    title = daily.normalize(str(data.get("title", "")))
    if not title:
        raise ValueError("no title")
    return {"title": title[:100], "description": daily.normalize(str(data.get("description", ""))),
            "tags": [daily.normalize(str(t)) for t in data.get("tags", [])][:10],
            "intro": intro, "intro_visual": clean_visual(data.get("intro_visual")),
            "sections": out, "outro": outro}


def write_plan(topic_name, source, news_titles):
    user = f"TOPIC: {topic_name}\n\nSOURCE:\n{source}\n\nNEWS HEADLINES THIS WEEK (optional, intro only):\n" + \
           ("\n".join("- " + t for t in news_titles) if news_titles else "(none)")
    models = daily.MODELS + [m for m in daily.EXTRA_MODELS if m not in daily.MODELS]
    allowed_src = source + " " + " ".join(news_titles)
    last = None
    for attempt in range(8):
        model = models[attempt % len(models)]
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                              headers={"Authorization": f"Bearer {daily.API_KEY}"},
                              json={"model": model, "temperature": 0.4,
                                    "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                 {"role": "user", "content": user}]}, timeout=180)
            if r.status_code != 200:
                raise RuntimeError(f"{model}: HTTP {r.status_code} {r.text[:120]}")
            raw = r.json()["choices"][0]["message"]["content"] or ""
            clean = re.sub(r"```json|```", "", raw).strip()
            s, e = clean.find("{"), clean.rfind("}")
            if s < 0 or e < 0:
                raise ValueError(f"{model}: no JSON")
            return validate(json.loads(clean[s:e + 1]), allowed_src)
        except Exception as ex:  # noqa: BLE001
            last = ex
            print(f"attempt {attempt + 1} failed: {ex}")
            time.sleep(4)
    raise RuntimeError(f"explainer script failed: {last}")


# ------------------------------------------------------------------ text helpers

@functools.lru_cache(maxsize=None)
def _font(size, bold=True):
    for p in ("/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf" if bold else "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def wrap(text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if font.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(text, sizes, maxw, maxlines, bold=True):
    for s in sizes:
        f = _font(s, bold)
        ls = wrap(text, f, maxw)
        if len(ls) <= maxlines:
            return f, ls, s
    f = _font(sizes[-1], bold)
    return f, wrap(text, f, maxw)[:maxlines], sizes[-1]


# ------------------------------------------------------------------ visuals (animated 1920x1080 renderer)

RW, RH = 1920, 1080
BIG = (2400, 1350)  # background canvas; a moving crop of it gives a slow push-in
WHITE = (255, 255, 255)
SECTION_FADE = 0.25  # seconds of dip-to-black at each segment edge


def clean_visual(v):
    """Background picture idea written by the LLM: plain ASCII, no digits, long enough to be a real scene."""
    v = daily.normalize(str(v or "")).strip().strip('"')
    v = re.sub(r"[^A-Za-z ,.'-]", "", v)[:170].strip()
    return v if len(v) >= 18 else ""


def col(c, a=1.0):
    return (c[0], c[1], c[2], int(255 * max(0.0, min(1.0, a))))


def ease(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3


def anim(t, start, dur=0.5):
    return ease((t - start) / dur)


def fetch_bg(visual, path, seed):
    """AI background (Pollinations, needs POLLINATIONS_API_KEY). Returns False when unavailable."""
    key = os.environ.get("POLLINATIONS_API_KEY", "")
    if not key or not visual:
        return False
    prompt = (f"{visual}, cinematic, photorealistic, dark moody lighting, shallow depth of field, "
              "wide 16:9 frame, no text, no letters, no words, no numbers, no logos, no watermark")
    url = "https://gen.pollinations.ai/image/" + urllib.parse.quote(prompt)
    for _ in range(3):
        try:
            r = requests.get(url, params={"width": 1920, "height": 1080, "nologo": "true", "seed": seed, "key": key},
                             headers={"Authorization": "Bearer " + key, "User-Agent": "Mozilla/5.0"}, timeout=150)
            if r.status_code == 200 and len(r.content) > 20000:
                with open(path, "wb") as f:
                    f.write(r.content)
                Image.open(path).convert("RGB").load()
                return True
            print("image fetch status", r.status_code)
        except Exception as e:  # noqa: BLE001
            print("image fetch failed:", type(e).__name__, str(e)[:80])
        time.sleep(4)
    return False


def procedural_bg(pal, seed):
    bg, ac = PALETTES[pal % len(PALETTES)]
    im = Image.new("RGB", BIG, bg)
    d = ImageDraw.Draw(im, "RGBA")
    for k in range(9):
        x0 = (k * 330 + seed * 97) % (BIG[0] + 800) - 500
        d.polygon([(x0, 0), (x0 + 220, 0), (x0 - 520, BIG[1]), (x0 - 740, BIG[1])], fill=ac + (16,))
    glow = Image.new("RGB", BIG, (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for k in range(4):
        cx, cy = (k * 613 + seed * 211) % BIG[0], (k * 397 + seed * 131) % BIG[1]
        gd.ellipse([cx - 420, cy - 420, cx + 420, cy + 420], fill=tuple(int(c * 0.45) for c in ac))
    return ImageChops.add(im, glow.filter(ImageFilter.GaussianBlur(170)))


def finish_bg(im):
    """Darken so text stays readable: strongest on the left (text side) and along the caption strip."""
    dark = Image.new("RGB", BIG, (4, 8, 20))
    mask = Image.new("L", BIG)
    md = ImageDraw.Draw(mask)
    for x in range(BIG[0]):
        md.line([(x, 0), (x, BIG[1])], fill=int(255 * (0.80 - 0.34 * x / BIG[0])))
    strip = Image.new("L", BIG)
    sd = ImageDraw.Draw(strip)
    for y in range(BIG[1]):
        sd.line([(0, y), (BIG[0], y)], fill=int(255 * 0.55 * max(0.0, (y / BIG[1] - 0.62) / 0.38) ** 1.5))
    im = Image.composite(dark, im, mask)
    return Image.composite(dark, im, strip)


def make_base(path, visual, pal, seed):
    img = Image.open(path).convert("RGB").resize(BIG, Image.LANCZOS) if visual and fetch_bg(visual, path, 100 + seed) \
        else procedural_bg(pal, seed)
    return finish_bg(img)


def bg_frame(base, p, seed):
    z = 1.0 - 0.10 * p
    cw, ch = BIG[0] * z, BIG[1] * z
    ox = (BIG[0] - cw) / 2 + (seed % 3 - 1) * 70 * p
    oy = (BIG[1] - ch) / 2
    return base.crop((int(ox), int(oy), int(ox + cw), int(oy + ch))).resize((RW, RH), Image.BILINEAR)


def draw_caption(d, words, t, A):
    if not words:
        return
    idx = None
    for i, (_w, a, _b) in enumerate(words):
        if a <= t:
            idx = i
        else:
            break
    if idx is None or t > words[-1][2] + 0.8:
        return
    c0 = (idx // 7) * 7
    chunk = words[c0:c0 + 7]
    f = _font(52, True)
    gap = f.getlength(" ")
    tw = sum(f.getlength(w) for w, _, _ in chunk) + gap * (len(chunk) - 1)
    x, y = (RW - tw) / 2, 905
    d.rounded_rectangle([x - 40, y - 42, x + tw + 40, y + 46], radius=24, fill=(0, 0, 0, 165))
    for k, (w, _a, _b) in enumerate(chunk):
        d.text((x, y), w, font=f, fill=col(A) if c0 + k == idx else col(WHITE, 0.94), anchor="lm")
        x += f.getlength(w) + gap


def draw_row(d, x, y, num, text, A, a, width, sizes=(44, 40, 36)):
    d.ellipse([x, y, x + 64, y + 64], fill=col(A, a))
    d.text((x + 32, y + 32), str(num), font=_font(40), fill=col((10, 14, 28), a), anchor="mm")
    f, ls, s = fit(text, list(sizes), width, 2, bold=False)
    for j, ln in enumerate(ls):
        d.text((x + 94, y + 32 + (j - (len(ls) - 1) / 2) * s * 1.18), ln, font=f, fill=col(WHITE, a), anchor="lm")


def draw_intro(d, seg, t, A):
    a = anim(t, 0.1, 0.8)
    xo = int((1 - a) * -60)
    d.text((110 + xo, 280), "A BEGINNER'S GUIDE", font=_font(42), fill=col(A, a), anchor="lm")
    f, ls, s = fit(seg["title"], [170, 150, 130, 112, 96], 1000, 3)
    for j, ln in enumerate(ls):
        d.text((110 + xo, 340 + j * s * 1.12), ln, font=f, fill=col(WHITE, a), anchor="la")
    yb = 340 + len(ls) * s * 1.12 + 30
    d.rectangle([110, yb, 110 + 180 * a, yb + 8], fill=col(A, a))
    d.text((110 + xo, yb + 60), "Explained simply, step by step", font=_font(46, False), fill=col(WHITE, a * 0.85),
           anchor="lm")
    d.text((1160, 250), "IN THIS VIDEO", font=_font(40), fill=col(A, anim(t, 1.0)), anchor="lm")
    for k, h in enumerate(seg["agenda"][:7]):
        ak = anim(t, 1.6 + k * 0.55, 0.5)
        if ak > 0:
            draw_row(d, 1160 + (1 - ak) * 70, 310 + k * 92, k + 1, h, A, ak, 620, (42, 38, 34))


def draw_section(d, seg, t, dur, A):
    a = anim(t, 0.0, 0.55)
    xo = int((1 - a) * -50)
    d.text((110 + xo, 290), f"PART {seg['i']} OF {seg['n']}", font=_font(40), fill=col(A, a), anchor="lm")
    f, ls, s = fit(seg["heading"], [118, 104, 92, 80, 70], 820, 3)
    for j, ln in enumerate(ls):
        d.text((110 + xo, 350 + j * s * 1.12), ln, font=f, fill=col(WHITE, a), anchor="la")
    yb = 350 + len(ls) * s * 1.12 + 34
    d.rectangle([110, yb, 110 + 170 * a, yb + 8], fill=col(A, a))
    pts = seg["points"]
    step = max(0.7, (dur * 0.85 - 1.2) / max(1, len(pts)))
    for k, p in enumerate(pts):
        ak = anim(t, 1.2 + k * step, 0.5)
        if ak <= 0:
            continue
        x, y, w, h = 1010 + (1 - ak) * 90, 230 + k * 200, 800, 168
        d.rounded_rectangle([x, y, x + w, y + h], radius=30, fill=col((6, 12, 26), 0.80 * ak), outline=col(A, ak), width=3)
        d.ellipse([x + 36, y + h / 2 - 34, x + 104, y + h / 2 + 34], fill=col(A, ak))
        d.text((x + 70, y + h / 2), str(k + 1), font=_font(44), fill=col((10, 14, 28), ak), anchor="mm")
        pf, pl, ps = fit(p, [46, 42, 38, 34], w - 200, 2, bold=False)
        for j, ln in enumerate(pl):
            d.text((x + 134, y + h / 2 + (j - (len(pl) - 1) / 2) * ps * 1.2), ln, font=pf, fill=col(WHITE, ak), anchor="lm")


def draw_outro(d, t, A):
    a = anim(t, 0.1, 0.6)
    d.text((RW / 2, 330), "Subscribe for a new", font=_font(110), fill=col(WHITE, a), anchor="mm")
    d.text((RW / 2, 460), "tech story every day", font=_font(110), fill=col(A, a), anchor="mm")
    p = anim(t, 1.0, 0.5)
    y = 580 + (1 - p) * 60
    d.rounded_rectangle([RW / 2 - 330, y, RW / 2 + 330, y + 150], radius=60, fill=col((255, 40, 40), p))
    d.text((RW / 2, y + 75), "SUBSCRIBE", font=_font(84), fill=col(WHITE, p), anchor="mm")


def draw_segment(ov, seg, t, dur, gt):
    d = ImageDraw.Draw(ov)
    A = PALETTES[seg["pal"] % len(PALETTES)][1]
    d.text((96, 64), CHANNEL, font=_font(30), fill=col(WHITE, 0.75), anchor="lm")
    d.text((RW - 96, 64), "TECH EXPLAINED", font=_font(30), fill=col(A), anchor="rm")
    d.rectangle([0, RH - 8, RW * gt, RH], fill=col(A))
    if seg["kind"] == "intro":
        draw_intro(d, seg, t, A)
    elif seg["kind"] == "section":
        draw_section(d, seg, t, dur, A)
    else:
        draw_outro(d, t, A)
    draw_caption(d, seg["words"], t, A)


def render_video(segs, out_path):
    total = sum(s["dur"] for s in segs)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{RW}x{RH}", "-r", str(FPS),
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", out_path],
        stdin=subprocess.PIPE)
    black = Image.new("RGB", (RW, RH))
    done = 0.0
    try:
        for seg in segs:
            dur = seg["dur"]
            for f in range(int(round(dur * FPS))):
                t = f / FPS
                im = bg_frame(seg["base"], t / dur, seg["seed"]).convert("RGBA")
                ov = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
                draw_segment(ov, seg, t, dur, (done + t) / total)
                im = Image.alpha_composite(im, ov).convert("RGB")
                a = min(t / SECTION_FADE, (dur - t) / SECTION_FADE, 1.0)
                if a < 1.0:
                    im = Image.blend(black, im, max(a, 0.0))
                proc.stdin.write(im.tobytes())
            done += dur
            print(f"rendered segment {seg['kind']} {dur:.1f}s", flush=True)
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError("ffmpeg video encode failed")


# ------------------------------------------------------------------ audio / video

def audio_duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(out.decode().strip())


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-400:]}")


def tts(text, path):
    for attempt in range(4):
        try:
            asyncio.run(daily.make_audio(text, path))
            if os.path.getsize(path) > 1000:
                return
        except Exception as e:  # noqa: BLE001
            print("tts failed:", e)
        time.sleep(3)
    raise RuntimeError("tts failed")


def fmt_ts(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build(plan, topic_name, wiki_title, tts_fn=tts):
    os.makedirs(OUT, exist_ok=True)
    import random
    import video  # reuse: speech timing, word timing and the music bed
    sections = plan["sections"]
    n = len(sections)

    specs = [("Intro", plan["intro"], plan.get("intro_visual", ""),
              {"kind": "intro", "title": topic_name, "agenda": [s["heading"] for s in sections]})]
    for i, sec in enumerate(sections, 1):
        specs.append((sec["heading"], sec["narration"], sec.get("visual", ""),
                      {"kind": "section", "i": i, "n": n, "heading": sec["heading"], "points": sec["points"]}))
    specs.append(("Outro", plan["outro"], "", {"kind": "outro"}))

    segs, voices, chapters, t = [], [], [], 0.0
    for si, (label, text, visual, info) in enumerate(specs):
        ap = f"{OUT}/seg{si}.mp3"
        tts_fn(text, ap)
        dur = audio_duration(ap) + PAD
        ts, te = video.speech_span(ap, dur)
        words = video.build_words(text.split(), ts, te)
        base = make_base(f"{OUT}/bg{si}.jpg", visual, si, si)
        segs.append({**info, "words": words, "base": base, "seed": si, "pal": si, "dur": dur})
        voices.append((ap, dur))
        chapters.append((t, label))
        t += dur
    total = t

    inputs, filt = [], []
    for k, (ap, dur) in enumerate(voices):
        inputs += ["-i", ap]
        filt.append(f"[{k}:a]aresample=44100,apad=whole_dur={dur:.3f}[a{k}]")
    filt.append("".join(f"[a{k}]" for k in range(len(voices))) + f"concat=n={len(voices)}:v=0:a=1[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[v]",
         f"{OUT}/voice.wav"])
    audio = f"{OUT}/voice.wav"
    try:
        video.build_audio_mix(f"{OUT}/voice.wav", total, [], random.randint(0, 9999), f"{OUT}/mix.wav")
        audio = f"{OUT}/mix.wav"
    except Exception as e:  # noqa: BLE001
        print("music mix skipped:", e)

    render_video(segs, f"{OUT}/silent.mp4")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{OUT}/silent.mp4", "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}",
         f"{OUT}/video.mp4"])

    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(wiki_title.replace(" ", "_"))
    desc = plan["description"] + "\n\nChapters:\n" + "\n".join(f"{fmt_ts(ts)} {lab[:60]}" for ts, lab in chapters)
    desc += f"\n\nSource: Wikipedia, \"{wiki_title}\" (CC BY-SA 4.0), {url}"
    desc += "\n\nA beginner explainer from Trend Pulse Daily."
    script = {"title": plan["title"], "description": desc, "tags": plan["tags"] + ["tech explained", "technology"],
              "plan": plan, "topic": topic_name}
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    print(f"done: {OUT}/video.mp4 ({total:.0f}s, {n} sections)")
    return total


def set_output(made):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as f:
            f.write(f"made={'true' if made else 'false'}\n")


def remember_topic(tid):
    os.makedirs(ARCHIVE, exist_ok=True)
    used = used_topics()
    used.append(tid)
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(used[-40:], f)


def main():
    rows = load_archive()
    news_titles = [d["title"] for _, d in rows][-6:]
    print("daily stories archived this week:", len(rows))
    for tid, name, page, score in rank_topics(rows)[:6]:
        try:
            wiki_title, text = fetch_wiki(page)
        except Exception as e:  # noqa: BLE001
            print(f"wikipedia fetch failed for {page}: {e}")
            continue
        if len(text) < 3000:
            print(f"source too short for {page} ({len(text)} chars), trying next topic")
            continue
        print(f"topic: {name} (news relevance {score}), source {wiki_title}, {len(text)} chars")
        try:
            plan = write_plan(name, text, news_titles if score > 0 else [])
        except Exception as e:  # noqa: BLE001
            print(e)
            continue
        build(plan, name, wiki_title)
        remember_topic(tid)
        set_output(True)
        return
    print("no explainer made this week")
    set_output(False)


if __name__ == "__main__":
    main()
