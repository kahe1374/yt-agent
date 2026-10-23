import json
import os
import re
import subprocess
import textwrap

OUT = "output"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1280, 720


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


def wrap(text, width=42):
    return "\n".join(textwrap.wrap(text, width=width))


def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        data = json.load(f)
    audio = f"{OUT}/narration.mp3"
    total = audio_duration(audio)

    sentences = split_sentences(data["narration"])
    lengths = [max(len(s), 1) for s in sentences]
    scale = total / sum(lengths)

    os.makedirs(f"{OUT}/txt", exist_ok=True)
    with open(f"{OUT}/txt/title.txt", "w", encoding="utf-8") as f:
        f.write(wrap(data["title"], 48))

    filters = [
        f"color=c=0x0b1220:s={W}x{H}:r=30[bg]",
        f"[1:a]showwaves=s={W}x160:mode=cline:colors=0x38bdf8:rate=30[wv]",
        "[bg][wv]overlay=0:{}[v0]".format(H - 190),
        (
            f"[v0]drawtext=fontfile={FONT}:textfile={OUT}/txt/title.txt:"
            "fontcolor=0x38bdf8:fontsize=38:x=(w-text_w)/2:y=50[v1]"
        ),
    ]

    t = 0.0
    last = "v1"
    for i, s in enumerate(sentences):
        dur = lengths[i] * scale
        path = f"{OUT}/txt/s{i}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(wrap(s))
        nxt = f"c{i}"
        filters.append(
            f"[{last}]drawtext=fontfile={FONT}:textfile={path}:fontcolor=white:"
            f"fontsize=40:line_spacing=12:x=(w-text_w)/2:y=(h-text_h)/2-30:"
            f"enable='between(t,{t:.2f},{t + dur:.2f})'[{nxt}]"
        )
        last = nxt
        t += dur

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0b1220:s={W}x{H}:r=30",
        "-i", audio,
        "-filter_complex", ";".join(filters[1:]).replace("[bg]", "[0:v]"),
        "-map", f"[{last}]", "-map", "1:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "160k",
        "-t", f"{total:.2f}",
        f"{OUT}/video.mp4",
    ]
    subprocess.run(cmd, check=True)
    print("done:", f"{OUT}/video.mp4", f"({total:.1f}s)")


if __name__ == "__main__":
    main()
