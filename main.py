import asyncio
import json
import os
import re
import time

import edge_tts
import feedparser
import requests

NEWS_URL = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"
API_KEY = os.environ["OPENROUTER_API_KEY"]
MODELS = [
    m.strip()
    for m in os.environ.get(
        "MODELS", "google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free"
    ).split(",")
    if m.strip()
]
VOICE = os.environ.get("VOICE", "en-US-AriaNeural")
RATE = os.environ.get("RATE", "+0%")
OUT = "output"

SYSTEM_PROMPT = """You are a professional script writer for a faceless English YouTube channel about technology news. Your script is read aloud by a female narrator.

Input: a list of today's tech news headlines.

Your job:
1. Pick the single most interesting and important story.
2. Write a narration of about 280 to 350 words (roughly 100 to 140 seconds).
3. Start with a strong hook in the first sentence. Then explain the story simply. End with one short takeaway about why it matters.
4. Use only facts that are in the headlines. Never invent numbers, quotes, dates, or claims. If the information is thin, write a shorter script and do not pad it.

Writing rules for the ear:
- Short, clear sentences, conversational but correct English.
- No emojis, no parentheses, no bullet points, no hashtags, no special symbols, no stage directions.
- Write numbers and names the way a person would say them.

Output only one valid JSON object, with no extra text and no code fences:
{
  "title": "Catchy but honest title, max 70 characters",
  "description": "2 to 3 lines for the YouTube description, including the news source name",
  "narration": "The full narration text to be converted to speech",
  "keywords": ["3 to 5 English keywords for finding background footage"],
  "tags": ["5 to 8 English tags"]
}"""


def get_news(limit=10):
    feed = feedparser.parse(NEWS_URL)
    items = []
    for e in feed.entries[:limit]:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        items.append(f"- {e.title} ({e.get('source', {}).get('title', '')}) {summary[:200]}")
    if not items:
        raise RuntimeError("No news came from the RSS feed")
    return "\n".join(items)


def call_llm(system, user):
    for attempt in range(1, 7):
        model = MODELS[(attempt - 1) % len(MODELS)]
        raw = ""
        r = None
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": model,
                    "max_tokens": 1500,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=180,
            )
            body = r.json()
            raw = body["choices"][0]["message"]["content"] or ""
            print("model:", body.get("model", model))
        except Exception as e:
            print(f"attempt {attempt} error:", e, (r.text[:200] if r is not None else ""))
        if raw.strip():
            return raw
        time.sleep(15)
    raise RuntimeError("The model did not respond")


def narration_ok(text):
    if len(text) < 400:
        return False
    return not re.search(r"[^\x00-\x7F\u2018\u2019\u201C\u201D\u2013\u2014\u2026]", text)


def write_script(news):
    for attempt in range(1, 5):
        raw = call_llm(SYSTEM_PROMPT, news)
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if narration_ok(data.get("narration", "")):
                    return data
                print(f"attempt {attempt}: narration rejected.")
            except json.JSONDecodeError:
                print(f"attempt {attempt}: invalid JSON.")
        else:
            print(f"attempt {attempt}: no JSON found:", raw[:200])
        time.sleep(5)
    raise RuntimeError("The model did not produce an acceptable script")


def clean_for_tts(text):
    text = re.sub(r"[*#_`>\[\]()\"]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def make_audio(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    news = get_news()
    data = write_script(news)
    data["narration"] = clean_for_tts(data["narration"])
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    asyncio.run(make_audio(data["narration"], f"{OUT}/narration.mp3"))
    print("title:", data["title"])
    print("done: output/script.json and output/narration.mp3")


if __name__ == "__main__":
    main()
