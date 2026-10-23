import json
import os
import re
import subprocess
import textwrap

OUT = "output"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
CHANNEL = os.environ.get("CHANNEL_NAME", "TREND PULSE DAILY")
W, H = 1280, 720
END_CARD = 3.0  # seconds added at the end


def audio_duration(path):
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ]
    )
    return float(out.decode().strip())


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def centered_lines(label, lines, font_size, line_h, center_y, color, enable=None, box=False):
    """One drawtext per line so every line is truly centered."""
    filters = []
    n = len(lines)
    top = center_y - (n * line_h) / 2
    for i, line in enumerate(lines):
        p = f"{OUT}/txt/{label}_{i}.txt"
        write_text(p, line)
        f = (
            f"drawtext=fontfile={FONT}:textfile={p}:fontcolor={color}:"
            f"fontsize={font_size}:x=(w-text_w)/2:y={top + i * line_h:.0f}"
        )
        if box:
            f += ":box=1:boxcolor=0x020617@0.55:boxborderw=14"
        if enable:
            f += f":enable='{enable}'"
        filters.append(f)
    return filters


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

    chain = []  # list of filter strings applied in order to the video stream

    # Channel tag (top-left) and headline (top)
    write_text(f"{OUT}/txt/channel.txt", CHANNEL)
    chain.append(
        f"drawtext=fontfile={FONT}:textfile={OUT}/txt/channel.txt:"
        "fontcolor=0x94a3b8:fontsize=22:x=40:y=28"
    )
    title_lines = textwrap.wrap(data["title"], width=46)[:2]
    chain += centered_lines("title", title_lines, 38, 48, 118, "0x38bdf8")

    # Captions, one sentence at a time
    t = 0.0
    for i, s in enumerate(sentences):
        dur = lengths[i] * scale
        lines = textwrap.wrap(s, width=40)
        chain += centered_lines(
            f"s{i}", lines, 42, 62, 360, "white",
            enable=f"between(t,{t:.2f},{t + dur:.2f})", box=True,
        )
        t += dur

    # End card
    chain += centered_lines(
        "end1", ["Follow for daily tech news"], 52, 70, 330, "white",
        enable=f"gte(t,{total:.2f})",
    )
    chain += centered_lines(
        "end2", [CHANNEL], 30, 40, 400, "0x38bdf8",
        enable=f"gte(t,{total:.2f})",
    )

    filter_complex = ";".join(
        [
            f"gradients=s={W}x{H}:r=30:c0=0x0b1220:c1=0x1e3a8a:c2=0x0f172a:"
            f"x0=0:y0=0:x1={W}:y1={H}:speed=0.015:duration={full:.2f}[bg]",
            f"[0:a]apad=pad_dur={END_CARD},asplit=2[a_out][a_vis]",
            f"[a_vis]volume=2.5,showwaves=s={W}x120:mode=cline:scale=sqrt:"
            "colors=0x38bdf8:rate=30[wv]",
            f"[bg][wv]overlay=0:{H - 170}[v0]",
            f"color=c=0x38bdf8:s={W}x8:r=30:d={full:.2f}[bar]",
            f"[v0][bar]overlay=x='-W+W*t/{full:.2f}':y={H - 8}[v1]",
            "[v1]" + ",".join(chain) + "[vout]",
        ]
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", audio,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[a_out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "160k",
        "-t", f"{full:.2f}",
        f"{OUT}/video.mp4",
    ]
    subprocess.run(cmd, check=True)
    print("done:", f"{OUT}/video.mp4", f"({full:.1f}s)")


if __name__ == "__main__":
    main()
