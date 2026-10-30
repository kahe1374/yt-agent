import asyncio
import datetime
import json
import os
import re
import time

import edge_tts
import feedparser
import requests

NEWS_URL = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"
MODELS = [m.strip() for m in (os.environ.get("MODELS") or "google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free,openrouter/free"
).split(",") if m.strip()]
API_KEY = os.environ["OPENROUTER_API_KEY"]
VOICE = os.environ.get("VOICE", "en-US-AriaNeural")
RATE = os.environ.get("RATE", "+0%")
OUT = "output"
SCENE_TYPES = {"hook", "stat", "cards", "statement", "cta"}

SYSTEM_PROMPT = """You write short vertical videos for a faceless English YouTube channel about technology news.
You get a numbered list of today's tech headlines (with short snippets). One story is marked FOCUS. Make the video about the FOCUS story only. Other headlines may be used only as background if they are clearly about the same story.

STRICT RULES
- Use ONLY facts that appear in the provided headlines and snippets. Never add analysis, predictions, causes, numbers, dates, prices or quotes that are not in the text.
- Attribute claims to the source when you can ("according to The Verge", "Ars Technica reports").
- If the text is thin, keep the video shorter and stay vague rather than inventing details.
- Plain ASCII English only. No emojis, no markdown, no hashtags.

OUTPUT: one valid JSON object, nothing else, no code fences:
{
  "title": "catchy YouTube title, max 70 characters",
  "description": "2-3 short lines for the YouTube description, mention the source",
  "keywords": ["3 to 5 short topic words"],
  "tags": ["5 to 8 tags"],
  "scenes": [
    {"type":"hook","headline":"3-6 word punchy headline","accent":"one word taken from the headline","voice":"the spoken sentence(s)"},
    {"type":"stat","value":"a number from the text, e.g. $2,000 or 12 million","label":"max 8 words saying what the number is","voice":"spoken sentence(s)"},
    {"type":"cards","heading":"max 5 words","items":["fact 1 (max 9 words)","fact 2","fact 3"],"voice":"spoken sentence(s)"},
    {"type":"statement","headline":"a complete grammatical sentence of 5-9 words, readable on its own (never a fragment like \"Weigh Upgrade Needs\")","accent":"one word from the headline","voice":"spoken sentence(s)"},
    {"type":"cta","headline":"Follow for daily tech news","voice":"one short outro sentence, max 12 words"}
  ]
}

SCENE PLAN
- 6 to 8 scenes. First scene is type hook, last scene is type cta.
- Use a stat scene ONLY if the source text contains a real number or amount. Otherwise do not use stat.
- Use at most one cards scene, with exactly 3 items, all facts from the text.
- Fill the rest with statement scenes.
- The "voice" of all scenes together is the full narration: 130 to 180 words, conversational, strong hook in the first sentence, simple words, short sentences.
- The on-screen text (headline/label/items) must match what the voice says in that scene."""


def get_news(limit=8):
    feed = feedparser.parse(NEWS_URL)
    items = []
    for e in feed.entries[:limit]:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        summary = re.sub(r"\s+", " ", summary).strip()
        items.append((e.title, e.get("source", {}).get("title", ""), summary[:220]))
    if not items:
        raise RuntimeError("no news from RSS")
    return items


def news_prompt(items):
    n = min(6, len(items))
    focus = datetime.date.today().toordinal() % n
    lines = []
    for i, (title, src, summ) in enumerate(items):
        tag = "  <== FOCUS" if i == focus else ""
        lines.append(f"{i + 1}. {title} ({src}) {summ}{tag}")
    return "\n".join(lines), items[focus][0]


def normalize(text):
    rep = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
           "…": "...", " ": " ", "•": "-"}
    for a, b in rep.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def is_ascii(s):
    return all(ord(c) < 128 for c in s)


def digits_in(s):
    return re.sub(r"\D", "", s)


def validate(data, source_text):
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not (5 <= len(scenes) <= 9):
        raise ValueError("bad scenes count")
    clean = []
    for s in scenes:
        if not isinstance(s, dict) or s.get("type") not in SCENE_TYPES:
            raise ValueError("bad scene type")
        s = {k: (normalize(v) if isinstance(v, str) else v) for k, v in s.items()}
        if isinstance(s.get("items"), list):
            s["items"] = [normalize(str(x)) for x in s["items"]][:3]
        if not s.get("voice"):
            raise ValueError("scene without voice")
        for key in ("voice", "headline", "heading", "label", "value", "accent"):
            if key in s and isinstance(s[key], str) and not is_ascii(s[key]):
                raise ValueError("non-ascii text")
        # anti-hallucination: a stat number must exist in the source text
        if s["type"] == "stat":
            d = digits_in(s.get("value", ""))
            if not d or d not in digits_in(source_text):
                s = {"type": "statement", "headline": s.get("label", "")[:60] or s["voice"][:60],
                     "voice": s["voice"]}
        if s["type"] == "cards" and len(s.get("items", [])) < 2:
            s = {"type": "statement", "headline": s.get("heading", "")[:60] or s["voice"][:60],
                 "voice": s["voice"]}
        if s.get("accent") and s.get("headline"):
            wl = [re.sub(r"\W", "", w).lower() for w in s["headline"].split()]
            if re.sub(r"\W", "", s["accent"]).lower() not in wl:
                s["accent"] = None
        clean.append(s)
    if clean[0]["type"] != "hook":
        clean[0]["type"] = "hook"
        clean[0].setdefault("headline", normalize(data.get("title", ""))[:50])
    if clean[-1]["type"] != "cta":
        clean.append({"type": "cta", "headline": "Follow for daily tech news",
                      "voice": "Follow for more daily tech news."})
    narration = " ".join(s["voice"] for s in clean)
    if len(narration) < 400 or len(narration) > 1600:
        raise ValueError(f"narration length {len(narration)}")
    title = normalize(str(data.get("title", "")))
    if not title or not is_ascii(title):
        raise ValueError("bad title")
    return {
        "title": title[:100],
        "description": normalize(str(data.get("description", ""))),
        "keywords": [normalize(str(k)) for k in data.get("keywords", [])][:5],
        "tags": [normalize(str(t)) for t in data.get("tags", [])][:10],
        "narration": narration,
        "scenes": clean,
    }


STRUCTURES = [
    "hook, statement, statement, cards, cta",
    "hook, cards, statement, statement, statement, cta",
    "hook, statement, stat (only if a real number exists), statement, cards, cta",
    "hook, statement, cards, stat (only if a real number exists), statement, cta",
    "hook, statement, statement, statement, cta",
    "hook, stat (only if a real number exists), cards, statement, statement, cta",
]


def write_script(news_text, source_text):
    import random
    news_text = news_text + "\n\nSCENE ORDER FOR THIS VIDEO (follow it, skip stat if no real number): " + random.choice(STRUCTURES)
    last = None
    for attempt in range(6):
        model = MODELS[attempt % len(MODELS)]
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": model, "temperature": 0.5,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": news_text}]},
                timeout=150,
            )
            if r.status_code != 200:
                raise RuntimeError(f"{model}: HTTP {r.status_code} {r.text[:120]}")
            raw = r.json()["choices"][0]["message"]["content"] or ""
            clean = re.sub(r"```json|```", "", raw).strip()
            s, e = clean.find("{"), clean.rfind("}")
            data = json.loads(clean[s:e + 1])
            return validate(data, source_text)
        except Exception as ex:  # noqa: BLE001
            last = ex
            print(f"attempt {attempt + 1} failed ({model}): {ex}")
            time.sleep(15)
    raise RuntimeError(f"script generation failed: {last}")


async def make_audio(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    items = get_news()
    news_text, focus_title = news_prompt(items)
    print("focus:", focus_title)
    source_text = " ".join(f"{t} {s} {m}" for t, s, m in items)
    data = write_script(news_text, source_text)
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    asyncio.run(make_audio(data["narration"], f"{OUT}/narration.mp3"))
    print("title:", data["title"])
    print("scenes:", [s["type"] for s in data["scenes"]])


if __name__ == "__main__":
    main()
