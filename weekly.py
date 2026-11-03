"""Weekly long-form video: ONE technology explained for beginners (landscape 1920x1080, ~4-6 min).

Run by .github/workflows/weekly-video.yml. Needs OPENROUTER_API_KEY and internet (Wikipedia is the fact source).
Topic = the technology most related to this week's daily stories (archive/*.json), never one used recently
(archive/weekly_topics.json). If no source text can be fetched or the AI output fails validation, nothing is
published that week (made=false) instead of publishing unchecked facts.
"""
import asyncio
import datetime
import glob
import json
import os
import re
import subprocess
import time
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFont

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

OUTPUT: one valid JSON object, nothing else, no code fences:
{
  "title": "YouTube title, max 80 characters, like 'How Solid-State Batteries Work (Explained Simply)'",
  "description": "2 short lines saying what the viewer will learn",
  "tags": ["6 to 10 tags"],
  "intro": "35 to 50 words: a hook question or surprising fact from the SOURCE, then say what the video explains",
  "sections": [
    {"heading": "max 7 words", "points": ["fact 1", "fact 2", "fact 3"], "narration": "80 to 120 words"}
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
        out.append({"heading": daily.normalize(str(s["heading"]))[:70], "points": pts, "narration": n})
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
            "intro": intro, "sections": out, "outro": outro}


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


# ------------------------------------------------------------------ slides

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


def base(pal, seed):
    bg, ac = PALETTES[pal % len(PALETTES)]
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im, "RGBA")
    for k in range(6):  # soft diagonal bands
        x0 = (k * 400 + seed * 90) % (W + 600) - 400
        d.polygon([(x0, 0), (x0 + 140, 0), (x0 + 140 - 700, H), (x0 - 700, H)], fill=ac + (14,))
    d.rectangle([0, 0, W, 10], fill=ac)
    d.text((80, H - 70), CHANNEL, font=_font(30), fill=(255, 255, 255, 150), anchor="lm")
    d.text((W - 80, H - 70), "TECH EXPLAINED", font=_font(30), fill=ac + (255,), anchor="rm")
    return im, ImageDraw.Draw(im, "RGBA"), ac


def slide_title(topic_name, pal=0):
    im, d, ac = base(pal, 1)
    d.text((W / 2, 250), "TECH EXPLAINED", font=_font(64), fill=ac, anchor="mm")
    f, ls, s = fit(topic_name.upper(), [170, 150, 130, 110, 96], W - 280, 3)
    lh = s * 1.15
    y0 = 560 - (len(ls) - 1) * lh / 2
    for j, ln in enumerate(ls):
        d.text((W / 2, y0 + j * lh), ln, font=f, fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 880), "Explained simply, step by step", font=_font(48, False), fill=(255, 255, 255, 215), anchor="mm")
    return im


def slide_agenda(headings, pal=0):
    im, d, ac = base(pal, 2)
    d.text((120, 130), "IN THIS VIDEO", font=_font(64), fill=ac, anchor="lm")
    y = 250
    for i, h in enumerate(headings[:7]):
        f, ls, s = fit(h, [54, 48, 42], W - 360, 2)
        d.rounded_rectangle([120, y - 10, 200, y + 70], radius=24, fill=ac)
        d.text((160, y + 30), str(i + 1), font=_font(48), fill=(10, 14, 28), anchor="mm")
        for j, ln in enumerate(ls):
            d.text((250, y + 30 + (j - (len(ls) - 1) / 2) * s * 1.2), ln, font=f, fill=(255, 255, 255), anchor="lm")
        y += 112
    return im


def slide_heading(i, n, sec, pal):
    im, d, ac = base(pal, i + 3)
    d.text((120, 150), f"PART {i} OF {n}", font=_font(44), fill=ac, anchor="lm")
    f, ls, s = fit(sec["heading"], [130, 112, 98, 84, 72], W - 240, 4)
    lh = s * 1.18
    y0 = H / 2 - (len(ls) - 1) * lh / 2 - 20
    for j, ln in enumerate(ls):
        d.text((120, y0 + j * lh), ln, font=f, fill=(255, 255, 255), anchor="lm")
    return im


def slide_points(i, n, sec, pal):
    im, d, ac = base(pal, i + 3)
    f, ls, s = fit(sec["heading"], [60, 54, 48], W - 240, 2)
    for j, ln in enumerate(ls):
        d.text((120, 120 + j * s * 1.2), ln, font=f, fill=ac, anchor="lm")
    y = 120 + len(ls) * s * 1.2 + 70
    avail = H - 170 - y
    rh = min(230, avail / max(1, len(sec["points"])))
    for k, p in enumerate(sec["points"]):
        d.rounded_rectangle([120, y + k * rh, W - 120, y + k * rh + rh - 28], radius=36, fill=(255, 255, 255, 28),
                            outline=ac + (200,), width=3)
        d.text((190, y + k * rh + (rh - 28) / 2), str(k + 1), font=_font(84), fill=ac, anchor="mm")
        pf, pl, ps = fit(p, [52, 46, 40, 36], W - 520, 2, bold=False)
        for j, ln in enumerate(pl):
            d.text((280, y + k * rh + (rh - 28) / 2 + (j - (len(pl) - 1) / 2) * ps * 1.2), ln, font=pf,
                   fill=(255, 255, 255), anchor="lm")
    return im


def slide_outro(pal=0):
    im, d, ac = base(pal, 5)
    d.text((W / 2, 400), "Subscribe for a new", font=_font(110), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 540), "tech story every day", font=_font(110), fill=ac, anchor="mm")
    d.rounded_rectangle([W / 2 - 330, 700, W / 2 + 330, 860], radius=60, fill=(255, 40, 40))
    d.text((W / 2, 780), "SUBSCRIBE", font=_font(84), fill=(255, 255, 255), anchor="mm")
    return im


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
    sections = plan["sections"]
    n = len(sections)

    # segments: (chapter label, spoken text, [(slide image, weight)])
    segs = [("Intro", plan["intro"], [(slide_title(topic_name), 0.45),
                                        (slide_agenda([s["heading"] for s in sections]), 0.55)])]
    for i, sec in enumerate(sections, 1):
        segs.append((sec["heading"], sec["narration"],
                     [(slide_heading(i, n, sec, i), 0.3), (slide_points(i, n, sec, i), 0.7)]))
    segs.append(("Outro", plan["outro"], [(slide_outro(), 1.0)]))

    clips, voices, chapters, t = [], [], [], 0.0
    for si, (label, text, slides) in enumerate(segs):
        ap = f"{OUT}/seg{si}.mp3"
        tts_fn(text, ap)
        dur = audio_duration(ap) + PAD
        chapters.append((t, label))
        wsum = sum(w for _, w in slides)
        for k, (img, w) in enumerate(slides):
            ds = dur * w / wsum
            png = f"{OUT}/s{si}_{k}.png"
            img.save(png)
            clip = f"{OUT}/c{si}_{k}.mp4"
            fade = min(0.35, ds / 4)
            run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{ds:.3f}", "-i", png,
                 "-vf", f"fade=t=in:st=0:d={fade:.2f},fade=t=out:st={ds - fade:.2f}:d={fade:.2f},format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), clip])
            clips.append(clip)
        voices.append((ap, dur))
        t += dur
    total = t

    with open(f"{OUT}/clips.txt", "w") as f:
        f.writelines(f"file '{os.path.basename(c)}'\n" for c in clips)
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{OUT}/clips.txt", "-c", "copy",
         f"{OUT}/silent.mp4"])

    inputs, filt = [], []
    for k, (ap, dur) in enumerate(voices):
        inputs += ["-i", ap]
        filt.append(f"[{k}:a]aresample=44100,apad=whole_dur={dur:.3f}[a{k}]")
    filt.append("".join(f"[a{k}]" for k in range(len(voices))) + f"concat=n={len(voices)}:v=0:a=1[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[v]",
         f"{OUT}/voice.wav"])
    audio = f"{OUT}/voice.wav"
    try:  # music bed with voice ducking: reuse the daily renderer's synth
        import random
        import video
        video.build_audio_mix(f"{OUT}/voice.wav", total, [], random.randint(0, 9999), f"{OUT}/mix.wav")
        audio = f"{OUT}/mix.wav"
    except Exception as e:  # noqa: BLE001
        print("music mix skipped:", e)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{OUT}/silent.mp4", "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", f"{OUT}/video.mp4"])

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
