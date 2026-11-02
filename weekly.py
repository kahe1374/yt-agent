"""Weekly long-form recap (landscape 1920x1080, ~4-8 min) built from the daily scripts saved in archive/.

Run by .github/workflows/weekly-video.yml. Needs OPENROUTER_API_KEY. Writes output_weekly/{video.mp4,script.json}.
If fewer than MIN_STORIES daily scripts exist it does nothing (and tells the workflow to skip the upload).
"""
import asyncio
import datetime
import glob
import json
import os
import re
import subprocess
import time

import requests
from PIL import Image, ImageDraw, ImageFont

import main as daily  # reuse models, voice, helpers

OUT = os.environ.get("OUT_DIR", "output_weekly")
ARCHIVE = "archive"
MIN_STORIES = 3
MAX_STORIES = 7
W, H, FPS = 1920, 1080, 30
PAD = 0.6  # seconds of silence after every spoken segment
CHANNEL = os.environ.get("CHANNEL_NAME", "TREND PULSE DAILY")

PALETTES = [((10, 22, 48), (0, 150, 255)), ((26, 12, 44), (255, 94, 98)), ((8, 36, 36), (30, 214, 160)),
            ((38, 20, 10), (255, 176, 32)), ((16, 16, 44), (140, 110, 255)), ((10, 30, 54), (34, 211, 238))]

SYSTEM_PROMPT = """You write the script for a weekly recap video (5 to 7 minutes) for a faceless English YouTube channel about technology news.
You get the stories the channel already covered this week, each with its title, source line and the narration that was used.

STRICT RULES
- Use ONLY facts that appear in the provided stories. Never add numbers, dates, prices, quotes, names or causes that are not in the text.
- Keep source attribution ("according to ...") when the story text has it.
- Pick the most important stories (at most 7; use all if there are fewer) and order them by importance, biggest first.
- Plain ASCII English only. No emojis, no markdown, no hashtags.
- Conversational, short sentences, simple words. Do not repeat the story text word for word: rewrite and connect it.
- "watch" is ONE hedged sentence (max 22 words) about what to keep an eye on next. Use words like "could" or "may". It must not contain new facts or numbers.

OUTPUT: one valid JSON object, nothing else, no code fences:
{
  "title": "YouTube title, max 80 characters, starts with 'Weekly Tech Recap:' then the 2 biggest stories",
  "description": "2 short lines summarising the week",
  "tags": ["6 to 10 tags"],
  "intro": "25 to 40 words: welcome to the weekly tech recap from Trend Pulse Daily, say what is coming",
  "stories": [
    {"headline": "max 8 words", "source": "outlet name from the story text or empty",
     "points": ["3 short facts, max 10 words each"],
     "narration": "70 to 100 words, facts only from the story",
     "watch": "one hedged sentence"}
  ],
  "outro": "20 to 30 words: thank the viewer, ask them to subscribe for daily tech news"
}"""


# ------------------------------------------------------------------ data

def load_archive():
    today = datetime.date.today()
    rows = []
    for p in sorted(glob.glob(f"{ARCHIVE}/*.json")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})\.json$", p)
        if not m:
            continue
        d = datetime.date.fromisoformat(m.group(1))
        if 0 <= (today - d).days <= 7:
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:  # noqa: BLE001
                print("skip bad archive file", p, e)
                continue
            if data.get("narration") and data.get("title"):
                rows.append((d, data))
    return rows


def story_block(i, d, data):
    return (f"STORY {i} ({d.isoformat()})\nTITLE: {data['title']}\nDESCRIPTION: {data.get('description', '')}\n"
            f"NARRATION: {data['narration']}\n")


def numbers(s):
    return {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d,\.]*", s)} - {""}


def validate(data, source):
    stories = data.get("stories")
    if not isinstance(stories, list) or not (MIN_STORIES <= len(stories) <= MAX_STORIES):
        raise ValueError("bad stories count")
    allowed = numbers(source)
    out = []
    texts = [data.get("intro", ""), data.get("outro", ""), data.get("title", "")]
    for s in stories:
        n = daily.normalize(str(s.get("narration", "")))
        pts = [daily.normalize(str(x)) for x in (s.get("points") or [])][:3]
        if len(n) < 250 or len(pts) < 2 or not s.get("headline"):
            raise ValueError("story too thin")
        texts += [n, " ".join(pts), s.get("watch", "") or ""]
        out.append({"headline": daily.normalize(str(s["headline"]))[:80],
                    "source": daily.normalize(str(s.get("source", "")))[:40],
                    "points": pts, "narration": n,
                    "watch": daily.normalize(str(s.get("watch", "")))[:200]})
    intro = daily.normalize(str(data.get("intro", "")))
    outro = daily.normalize(str(data.get("outro", "")))
    if len(intro) < 80 or len(outro) < 60:
        raise ValueError("intro/outro too short")
    full = " ".join(texts)
    if not daily.is_ascii(full):
        raise ValueError("non-ascii text")
    bad = numbers(full) - allowed
    if bad:
        raise ValueError(f"numbers not in source: {sorted(bad)[:5]}")
    return {"title": daily.normalize(str(data["title"]))[:100], "description": daily.normalize(str(data.get("description", ""))),
            "tags": [daily.normalize(str(t)) for t in data.get("tags", [])][:10],
            "intro": intro, "stories": out, "outro": outro}


def fallback_plan(rows):
    """No LLM: reuse the daily narrations (still fact-safe, just less polished)."""
    stories = []
    for d, data in rows[-MAX_STORIES:]:
        m = re.search(r"Source:\s*([^\n.]+)", data.get("description", ""))
        pts = []
        for sc in data.get("scenes", []):
            if sc.get("type") == "cards":
                pts = [str(x) for x in sc.get("items", [])][:3]
        if len(pts) < 2:
            pts = [sc["headline"] for sc in data.get("scenes", []) if sc.get("type") == "statement" and sc.get("headline")][:3]
        if len(pts) < 2:
            pts = [data["title"], "Covered by Trend Pulse Daily"]
        words = data["narration"].split()
        stories.append({"headline": data["title"][:80], "source": (m.group(1).strip() if m else "")[:40],
                        "points": pts, "narration": " ".join(words[:110]), "watch": ""})
    titles = [s["headline"] for s in stories[:2]]
    return {"title": ("Weekly Tech Recap: " + " | ".join(titles))[:100],
            "description": "The biggest tech stories of the week in one video.",
            "tags": ["tech news", "weekly tech recap", "technology", "ai", "gadgets"],
            "intro": "Welcome to the weekly tech recap from Trend Pulse Daily. Here are the biggest tech stories we covered this week.",
            "stories": stories,
            "outro": "That is the week in tech. Subscribe to Trend Pulse Daily for a new tech story every day, and see you next week."}


def write_plan(rows):
    block = "\n".join(story_block(i + 1, d, x) for i, (d, x) in enumerate(rows))
    source = " ".join(f"{x['title']} {x.get('description', '')} {x['narration']}" for _, x in rows)
    models = daily.MODELS + [m for m in daily.EXTRA_MODELS if m not in daily.MODELS]
    last = None
    for attempt in range(8):
        model = models[attempt % len(models)]
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                              headers={"Authorization": f"Bearer {daily.API_KEY}"},
                              json={"model": model, "temperature": 0.5,
                                    "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                 {"role": "user", "content": block}]}, timeout=150)
            if r.status_code != 200:
                raise RuntimeError(f"{model}: HTTP {r.status_code} {r.text[:120]}")
            raw = r.json()["choices"][0]["message"]["content"] or ""
            clean = re.sub(r"```json|```", "", raw).strip()
            s, e = clean.find("{"), clean.rfind("}")
            if s < 0 or e < 0:
                raise ValueError(f"{model}: no JSON")
            return validate(json.loads(clean[s:e + 1]), source)
        except Exception as ex:  # noqa: BLE001
            last = ex
            print(f"attempt {attempt + 1} failed: {ex}")
            time.sleep(4)
    print("all models failed, using fallback plan:", last)
    return fallback_plan(rows)


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
    d.text((W - 80, H - 70), "WEEKLY TECH RECAP", font=_font(30), fill=ac + (255,), anchor="rm")
    return im, ImageDraw.Draw(im, "RGBA"), ac


def slide_title(date_range, pal=0):
    im, d, ac = base(pal, 1)
    d.text((W / 2, 400), "WEEKLY", font=_font(190), fill=(255, 255, 255), anchor="mm")
    d.text((W / 2, 590), "TECH RECAP", font=_font(190), fill=ac, anchor="mm")
    d.text((W / 2, 780), date_range, font=_font(52, False), fill=(255, 255, 255, 220), anchor="mm")
    return im


def slide_agenda(headlines, pal=0):
    im, d, ac = base(pal, 2)
    d.text((120, 130), "THIS WEEK", font=_font(64), fill=ac, anchor="lm")
    y = 250
    for i, h in enumerate(headlines):
        f, ls, s = fit(h, [54, 48, 42], W - 360, 2)
        d.rounded_rectangle([120, y - 10, 200, y + 70], radius=24, fill=ac)
        d.text((160, y + 30), str(i + 1), font=_font(48), fill=(10, 14, 28), anchor="mm")
        for j, ln in enumerate(ls):
            d.text((250, y + 30 + (j - (len(ls) - 1) / 2) * s * 1.2), ln, font=f, fill=(255, 255, 255), anchor="lm")
        y += 112
    return im


def slide_headline(i, n, st, pal):
    im, d, ac = base(pal, i + 3)
    d.text((120, 150), f"STORY {i} OF {n}", font=_font(44), fill=ac, anchor="lm")
    f, ls, s = fit(st["headline"], [120, 104, 92, 80, 70], W - 240, 4)
    lh = s * 1.18
    y0 = H / 2 - (len(ls) - 1) * lh / 2 - 20
    for j, ln in enumerate(ls):
        d.text((120, y0 + j * lh), ln, font=f, fill=(255, 255, 255), anchor="lm")
    if st["source"]:
        d.text((120, H - 170), "Source: " + st["source"], font=_font(40, False), fill=(255, 255, 255, 210), anchor="lm")
    return im


def slide_points(i, n, st, pal):
    im, d, ac = base(pal, i + 3)
    f, ls, s = fit(st["headline"], [60, 54, 48], W - 240, 2)
    for j, ln in enumerate(ls):
        d.text((120, 120 + j * s * 1.2), ln, font=f, fill=ac, anchor="lm")
    y = 120 + len(ls) * s * 1.2 + 70
    avail = H - 170 - y
    rh = min(230, avail / max(1, len(st["points"])))
    for k, p in enumerate(st["points"]):
        d.rounded_rectangle([120, y + k * rh, W - 120, y + k * rh + rh - 28], radius=36, fill=(255, 255, 255, 28),
                            outline=ac + (200,), width=3)
        d.text((190, y + k * rh + (rh - 28) / 2), str(k + 1), font=_font(84), fill=ac, anchor="mm")
        pf, pl, ps = fit(p, [52, 46, 40, 36], W - 520, 2, bold=False)
        for j, ln in enumerate(pl):
            d.text((280, y + k * rh + (rh - 28) / 2 + (j - (len(pl) - 1) / 2) * ps * 1.2), ln, font=pf,
                   fill=(255, 255, 255), anchor="lm")
    return im


def slide_watch(i, n, st, pal):
    im, d, ac = base(pal, i + 3)
    d.text((W / 2, 300), "WHAT TO WATCH", font=_font(90), fill=ac, anchor="mm")
    f, ls, s = fit(st["watch"], [64, 56, 48, 42], W - 340, 5, bold=False)
    lh = s * 1.3
    y0 = 560 - (len(ls) - 1) * lh / 2
    for j, ln in enumerate(ls):
        d.text((W / 2, y0 + j * lh), ln, font=f, fill=(255, 255, 255), anchor="mm")
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


def build(plan, rows, tts_fn=tts):
    os.makedirs(OUT, exist_ok=True)
    first, last = rows[0][0], rows[-1][0]
    date_range = f"{first.strftime('%b %d')} - {last.strftime('%b %d, %Y')}"
    stories = plan["stories"]
    n = len(stories)

    # segments: (label, text, [(slide image, weight)])
    segs = [("Intro", plan["intro"], [(slide_title(date_range), 0.4),
                                        (slide_agenda([s["headline"] for s in stories][:7]), 0.6)])]
    for i, st in enumerate(stories, 1):
        pal = i
        sl = [(slide_headline(i, n, st, pal), 0.25), (slide_points(i, n, st, pal), 0.5 if st["watch"] else 0.75)]
        if st["watch"]:
            sl.append((slide_watch(i, n, st, pal), 0.25))
        text = st["narration"] + (" " + st["watch"] if st["watch"] else "")
        segs.append((st["headline"], text, sl))
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

    # narration track with padding, then music bed (reuse the daily music synth when available)
    inputs, filt = [], []
    for k, (ap, dur) in enumerate(voices):
        inputs += ["-i", ap]
        filt.append(f"[{k}:a]aresample=44100,apad=whole_dur={dur:.3f}[a{k}]")
    filt.append("".join(f"[a{k}]" for k in range(len(voices))) + f"concat=n={len(voices)}:v=0:a=1[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[v]",
         f"{OUT}/voice.wav"])
    audio = f"{OUT}/voice.wav"
    try:
        import video  # daily renderer: synth_music + ducked mix
        import random
        video.build_audio_mix(f"{OUT}/voice.wav", total, [], random.randint(0, 9999), f"{OUT}/mix.wav")
        audio = f"{OUT}/mix.wav"
    except Exception as e:  # noqa: BLE001
        print("music mix skipped:", e)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{OUT}/silent.mp4", "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", f"{OUT}/video.mp4"])

    ch_lines = ["Chapters:"] + [f"{fmt_ts(ts)} {lab[:60]}" for ts, lab in chapters]
    desc = plan["description"] + "\n\n" + "\n".join(ch_lines)
    srcs = sorted({s["source"] for s in stories if s["source"]})
    if srcs:
        desc += "\n\nSources: " + ", ".join(srcs)
    desc += "\n\nA recap of this week's short news summaries from Trend Pulse Daily."
    script = {"title": plan["title"], "description": desc, "tags": plan["tags"] + ["tech news"], "plan": plan}
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    print(f"done: {OUT}/video.mp4 ({total:.0f}s, {n} stories)")
    return total


def set_output(made):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as f:
            f.write(f"made={'true' if made else 'false'}\n")


def main():
    rows = load_archive()
    print("archived stories this week:", len(rows))
    if len(rows) < MIN_STORIES:
        print("not enough stories yet; skipping weekly video")
        set_output(False)
        return
    rows = rows[-MAX_STORIES:] if len(rows) > MAX_STORIES else rows
    plan = write_plan(rows)
    build(plan, rows)
    set_output(True)


if __name__ == "__main__":
    main()
