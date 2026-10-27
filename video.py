import json
import os
import re
import subprocess
import textwrap
import time
import urllib.parse

import requests

from PIL import ImageFont

OUT = "output"
CHANNEL = os.environ.get("CHANNEL_NAME", "TREND PULSE DAILY")
W, H = 1280, 720
END_CARD = 3.5
FPS = 30

SKY = "0x38bdf8"
RED = "0xef4444"
NAVY = "0x0b1220"


def pick_font():
    for p in (
        "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
        "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(p):
            return p
    raise RuntimeError("no font found")


FONT = pick_font()


def audio_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path]
    )
    return float(out.decode().strip())


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------- optional AI scene images (falls back to pure motion graphics) ----------

def image_ok(path):
    try:
        if os.path.getsize(path) < 8000:
            return False
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", path],
            check=True, capture_output=True,
        )
        return True
    except Exception:
        return False


def fetch_image(prompt, seed, path):
    if os.environ.get("FAKE_IMAGES"):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"gradients=s=1280x720:c0=0x1d4ed8:c1=0x0f172a:c2=0x7c3aed:seed={seed}:duration=1",
             "-frames:v", "1", path], check=True, capture_output=True)
        return True
    if os.environ.get("NO_IMAGES"):
        return False
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width=1280&height=720&nologo=true&seed={seed}")
    for _ in range(2):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                with open(path, "wb") as f:
                    f.write(r.content)
                if image_ok(path):
                    return True
        except Exception as e:
            print("image fetch error:", e)
        time.sleep(3)
    return False


def get_scene_images(data, n):
    kws = ", ".join(data.get("keywords", [])[:4]) or "technology"
    base = (f"{kws}, cinematic dark futuristic technology photo, dramatic lighting, "
            "blue and purple tones, no text, no letters, no logos, no people")
    paths = []
    for i in range(n):
        p = f"{OUT}/img{i}.jpg"
        ok = fetch_image(base, i + 11, p)
        print("image", i, "ok" if ok else "FAILED")
        paths.append(p if ok else None)
    good = [p for p in paths if p]
    if len(good) < 2:
        return []
    out, last = [], good[0]
    for p in paths:
        last = p or last
        out.append(last)
    return out


# ---------- drawtext helpers ----------

def fade(t0, t1, fi=0.35, fo=0.3):
    return (f"alpha='if(lt(t,{t0:.2f}),0,if(lt(t,{t0 + fi:.2f}),(t-{t0:.2f})/{fi},"
            f"if(lt(t,{t1 - fo:.2f}),1,if(lt(t,{t1:.2f}),({t1:.2f}-t)/{fo},0))))'")


def rise(t0, y, dist=26, dur=0.4):
    return f"y='{y}+{dist}*(1-min(1,max(0,(t-{t0:.2f})/{dur})))'"


def dt(textfile, size, color, x, y_expr, alpha_expr="", extra=""):
    s = (f"drawtext=fontfile={FONT}:textfile={textfile}:fontcolor={color}:fontsize={size}:"
         f"x='{x}':{y_expr}:shadowcolor=black@0.55:shadowx=2:shadowy=2")
    if alpha_expr:
        s += ":" + alpha_expr
    if extra:
        s += ":" + extra
    return s


def box(x, y, w, h, color, enable=None):
    s = f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill"
    if enable:
        s += f":enable='{enable}'"
    return s


def speech_span(audio, total):
    """Return (start, end) seconds of real speech, ignoring leading/trailing silence."""
    import array
    import math
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    step = 160  # 20 ms
    rms = []
    for i in range(0, len(a) - step, step):
        seg = a[i:i + step]
        rms.append(math.sqrt(sum(s * s for s in seg) / step))
    if not rms:
        return 0.0, total
    ref = sorted(rms)[int(len(rms) * 0.9)] or 1.0
    thr = ref * 0.06
    idx = [i for i, v in enumerate(rms) if v > thr]
    if not idx:
        return 0.0, total
    return max(0.0, idx[0] * 0.02 - 0.05), min(total, (idx[-1] + 1) * 0.02 + 0.1)


def build_words(narration, t_start, t_end):
    toks = narration.split()
    weights = []
    for w in toks:
        base = len(w) + 1
        if w[-1] in ".!?":
            base += 7
        elif w[-1] in ",;:":
            base += 3
        weights.append(base)
    tot = sum(weights)
    out, t = [], t_start
    for w, wt in zip(toks, weights):
        dur = (t_end - t_start) * wt / tot
        out.append((w, t, t + dur))
        t += dur
    return out


def make_chunks(words, font, max_w=1120, max_words=5):
    chunks, cur = [], []
    for item in words:
        cur.append(item)
        text = " ".join(x[0] for x in cur)
        end_punct = item[0][-1] in ".!?,;:"
        if len(cur) >= max_words or end_punct or font.getlength(text) > max_w:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    # merge a lone short tail into the previous chunk when it fits
    merged = []
    for c in chunks:
        if merged and len(c) <= 1 and font.getlength(" ".join(x[0] for x in merged[-1] + c)) <= max_w:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged


def speech_span(audio, total):
    """Return (start, end) seconds of real speech, ignoring leading/trailing silence."""
    import array
    import math
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    step = 160  # 20 ms
    rms = []
    for i in range(0, len(a) - step, step):
        seg = a[i:i + step]
        rms.append(math.sqrt(sum(s * s for s in seg) / step))
    if not rms:
        return 0.0, total
    ref = sorted(rms)[int(len(rms) * 0.9)] or 1.0
    thr = ref * 0.06
    idx = [i for i, v in enumerate(rms) if v > thr]
    if not idx:
        return 0.0, total
    return max(0.0, idx[0] * 0.02 - 0.05), min(total, (idx[-1] + 1) * 0.02 + 0.1)


def build_words(narration, t_start, t_end):
    toks = narration.split()
    weights = []
    for w in toks:
        base = len(w) + 1
        if w[-1] in ".!?":
            base += 7
        elif w[-1] in ",;:":
            base += 3
        weights.append(base)
    tot = sum(weights)
    out, t = [], t_start
    for w, wt in zip(toks, weights):
        dur = (t_end - t_start) * wt / tot
        out.append((w, t, t + dur))
        t += dur
    return out


def make_chunks(words, font, max_w=1120, max_words=5):
    chunks, cur = [], []
    for item in words:
        cur.append(item)
        text = " ".join(x[0] for x in cur)
        end_punct = item[0][-1] in ".!?,;:"
        if len(cur) >= max_words or end_punct or font.getlength(text) > max_w:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    merged = []
    for c in chunks:
        if merged and len(c) <= 1 and font.getlength(" ".join(x[0] for x in merged[-1] + c)) <= max_w:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    return merged


def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        data = json.load(f)
    audio = f"{OUT}/narration.mp3"
    total = audio_duration(audio)
    full = total + END_CARD

    sentences = split_sentences(data["narration"])
    lengths = [max(len(s), 1) for s in sentences]
    scale = total / sum(lengths)
    os.makedirs(f"{OUT}/txt", exist_ok=True)

    chain = []

    # ---- top bar ----
    chain.append(box(0, 0, W, 72, "black@0.45"))
    chain.append(box(40, 20, 150, 34, f"{RED}@1"))
    chain.append(box(52, 32, 10, 10, "white@1", enable="lt(mod(t,1.2),0.7)"))
    write_text(f"{OUT}/txt/live.txt", "TECH NEWS")
    chain.append(dt(f"{OUT}/txt/live.txt", 18, "white", 72, "y=27"))
    write_text(f"{OUT}/txt/channel.txt", CHANNEL)
    chain.append(dt(f"{OUT}/txt/channel.txt", 20, "0xcbd5e1", "w-text_w-40", "y=26"))

    # ---- headline (animated entrance) ----
    title_lines = textwrap.wrap(data["title"], width=38)[:2]
    for i, line in enumerate(title_lines):
        p = f"{OUT}/txt/title_{i}.txt"
        write_text(p, line)
        chain.append(dt(p, 42, "white", 78, rise(0.1 + i * 0.12, 100 + i * 54),
                        alpha_expr=f"alpha='min(1,max(0,(t-{0.1 + i * 0.12:.2f})/0.5))'"))
    chain.append(box(52, 104, 8, 54 * len(title_lines) - 8, f"{SKY}@1"))

    # ---- word-by-word captions (karaoke style) ----
    CAP_Y, CAP_H, CAP_SIZE = 322, 130, 62
    chain.append(box(0, CAP_Y, W, CAP_H, "black@0.38"))
    chain.append(box(0, CAP_Y, W, 3, f"{SKY}@0.7"))
    chain.append(box(0, CAP_Y + CAP_H - 3, W, 3, f"{SKY}@0.7"))

    t_s, t_e = speech_span(audio, total)
    words = build_words(data["narration"], t_s, t_e)
    cap_font = ImageFont.truetype(FONT, CAP_SIZE)
    chunks = make_chunks(words, cap_font)
    text_y = CAP_Y + (CAP_H - CAP_SIZE) // 2 - 6
    wi = 0
    for ci, chunk in enumerate(chunks):
        c0 = chunk[0][1] if ci > 0 else 0.0
        c1 = chunks[ci + 1][0][1] if ci + 1 < len(chunks) else total
        full_text = " ".join(x[0] for x in chunk)
        cw = cap_font.getlength(full_text)
        x0 = (W - cw) / 2
        for k, (w, a, b) in enumerate(chunk):
            prefix = " ".join(x[0] for x in chunk[:k]) + (" " if k else "")
            wx = x0 + cap_font.getlength(prefix)
            p = f"{OUT}/txt/w{wi}.txt"
            write_text(p, w)
            wi += 1
            nxt = chunk[k + 1][1] if k + 1 < len(chunk) else c1
            chain.append(dt(p, CAP_SIZE, "white", f"{wx:.1f}",
                            rise(c0, text_y, 16, 0.16),
                            alpha_expr=fade(c0, c1, 0.12, 0.08)))
            chain.append(
                f"drawtext=fontfile={FONT}:textfile={p}:fontcolor=white:fontsize={CAP_SIZE}:"
                f"x={wx:.1f}:y={text_y}:box=1:boxcolor=0xf97316@1:boxborderw=10:"
                f"enable='between(t,{a:.2f},{nxt:.2f})'"
            )

    # ---- keyword pills ----
    x = 40
    kws = [str(k).strip().upper() for k in data.get("keywords", []) if str(k).strip()][:4]
    for k, kw in enumerate(kws):
        p = f"{OUT}/txt/kw{k}.txt"
        label = "# " + kw
        write_text(p, label)
        chain.append(
            f"drawtext=fontfile={FONT}:textfile={p}:fontcolor=white:fontsize=20:x={x}:y=490:"
            f"box=1:boxcolor=0x1e3a8a@0.9:boxborderw=9:"
            f"alpha='min(1,max(0,(t-{1.0 + k * 0.35:.2f})/0.4))'"
        )
        x += int(len(label) * 13.2) + 46

    # ---- news ticker ----
    chain.append(box(0, 650, W, 62, f"{NAVY}@0.92"))
    ticker = f"{data['title']}   •   {CHANNEL}   •   " * 4
    write_text(f"{OUT}/txt/ticker.txt", ticker)
    chain.append(dt(f"{OUT}/txt/ticker.txt", 26, "white", f"w-mod(t*95,text_w)", "y=668"))
    chain.append(box(0, 650, 190, 62, f"{RED}@1"))
    write_text(f"{OUT}/txt/latest.txt", "LATEST")
    chain.append(dt(f"{OUT}/txt/latest.txt", 26, "white", 28, "y=668"))

    # ---- end card ----
    en = f"gte(t,{total:.2f})"
    chain.append(box(0, 0, W, H, "0x020617@0.82", enable=en))
    write_text(f"{OUT}/txt/end1.txt", "Follow for daily tech news")
    chain.append(dt(f"{OUT}/txt/end1.txt", 58, "white", "(w-text_w)/2", rise(total, 250, 30, 0.5),
                    alpha_expr=f"alpha='min(1,max(0,(t-{total:.2f})/0.5))'",
                    extra=f"enable='{en}'"))
    chain.append(box(490, 365, 300, 78, f"{RED}@1", enable=f"gte(t,{total + 0.5:.2f})*lt(mod(t,1),0.75)"))
    chain.append(box(490, 365, 300, 78, f"0xdc2626@1", enable=f"gte(t,{total + 0.5:.2f})*gte(mod(t,1),0.75)"))
    write_text(f"{OUT}/txt/end2.txt", "SUBSCRIBE")
    chain.append(dt(f"{OUT}/txt/end2.txt", 36, "white", "(w-text_w)/2", "y=383",
                    extra=f"enable='gte(t,{total + 0.5:.2f})'"))
    chain.append(dt(f"{OUT}/txt/channel.txt", 28, SKY, "(w-text_w)/2", "y=530",
                    extra=f"enable='{en}'"))

    # ---- background ----
    n_scenes = max(4, min(8, round(total / 9)))
    images = get_scene_images(data, n_scenes)

    inputs = ["-i", audio]
    parts = []
    parts.append(
        f"gradients=s={W}x{H}:r={FPS}:c0=0x0a1226:c1=0x1e3a8a:c2=0x4c1d95:c3=0x0f172a:"
        f"x0=0:y0=0:x1={W}:y1={H}:speed=0.02:duration={full:.2f}[grad]"
    )
    if images:
        n = len(images)
        seg = full / n
        FADE = 0.7
        for k, p in enumerate(images):
            inputs += ["-i", p]
            frames = int((seg + FADE) * FPS) + 2
            z = "min(zoom+0.0007,1.2)" if k % 2 == 0 else "if(eq(on,0),1.2,max(zoom-0.0007,1.0))"
            parts.append(
                f"[{k + 1}:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
                f"setsar=1,format=yuv420p[c{k}]"
            )
        prev = "c0"
        for k in range(1, n):
            parts.append(f"[{prev}][c{k}]xfade=transition=fade:duration={FADE}:offset={seg * k:.2f}[x{k}]")
            prev = f"x{k}"
        parts.append(
            f"[{prev}]trim=duration={full:.2f},setpts=PTS-STARTPTS,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.5:t=fill[photo]"
        )
        parts.append("[grad][photo]blend=all_mode=normal:all_opacity=0.85[base]")
    else:
        parts.append("[grad]null[base]")

    parts += [
        f"color=c=black:s={W + 64}x{H + 64}:r={FPS}:d={full:.2f},"
        f"drawgrid=w=64:h=64:t=1:c={SKY}@1,"
        f"crop={W}:{H}:x='mod(t*16,64)':y='mod(t*9,64)',format=yuv420p[grid]",
        "[base]format=yuv420p[b2]",
        "[b2][grid]blend=all_mode=screen:all_opacity=0.10,vignette=PI/5:mode=backward[bg]",
        f"[0:a]apad=pad_dur={END_CARD},asplit=2[a_out][a_vis]",
        f"[a_vis]volume=2.5,showwaves=s={W}x70:mode=cline:scale=sqrt:colors=0x7dd3fc:rate={FPS}[wv]",
        "[bg][wv]overlay=0:572[v0]",
        f"color=c={SKY}:s={W}x8:r={FPS}:d={full:.2f}[bar]",
        f"[v0][bar]overlay=x='-W+W*t/{full:.2f}':y={H - 8}[v1]",
        "[v1]" + ",".join(chain) + ",format=yuv420p[vout]",
    ]
    filter_complex = ";".join(parts)

    cmd = (["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[a_out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
        "-c:a", "aac", "-b:a", "160k",
        "-t", f"{full:.2f}",
        f"{OUT}/video.mp4",
    ])
    subprocess.run(cmd, check=True)
    print("done:", f"{OUT}/video.mp4", f"({full:.1f}s)")


if __name__ == "__main__":
    main()
