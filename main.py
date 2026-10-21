import asyncio
import json
import os
import re
import time

import edge_tts
import feedparser
import requests

NEWS_URL = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
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
2. Write a narration of about 150 to 200 words (roughly 60 to 90 seconds).
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


def call_llm(system, user, json_mode=False):
    attempts = 4 if GEMINI_KEY else 6
    for attempt in range(1, attempts + 1):
        raw = ""
        r = None
        try:
            if GEMINI_KEY:
                body = {
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": 0.7},
                }
                if json_mode:
                    body["generationConfig"]["responseMimeType"] = "application/json"
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    headers={"x-goog-api-key": GEMINI_KEY},
                    json=body,
                    timeout=180,
                )
                raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                print("model:", GEMINI_MODEL)
            else:
                model = MODELS[(attempt - 1) % len(MODELS)]
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                    json={
                        "model": model,
                        "max_tokens": 1500,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
