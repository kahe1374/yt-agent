import asyncio
import json
import os
import time

import edge_tts
import feedparser
import requests
import re

NEWS_URL = "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"
MODEL = os.environ.get("MODEL", "openrouter/free")
API_KEY = os.environ["OPENROUTER_API_KEY"]
VOICE = os.environ.get("VOICE", "fa-IR-DilaraNeural")
OUT = "output"

SYSTEM_PROMPT = """تو نویسنده‌ی اسکریپت یه کانال یوتیوب فارسی درباره‌ی تکنولوژی هستی. کانال بدون چهره است و ویدیوها با صدای گوینده ساخته می‌شن.

ورودی: لیست خبرهای امروز تکنولوژی.

کار تو:
1. مهم‌ترین و جذاب‌ترین خبر رو انتخاب کن.
2. یه اسکریپت روایت فارسی روان و محاوره‌ای بنویس، حدود ۱۵۰ تا ۲۰۰ کلمه (نزدیک ۶۰ تا ۹۰ ثانیه).
3. شروع با یه جمله‌ی قلاب‌کننده در ۳ ثانیه اول، وسط توضیح ساده، آخر یه جمع‌بندی کوتاه.
4. فقط چیزی بنویس که تو خبر هست. چیزی از خودت اضافه نکن و عدد یا ادعای ساختگی نیار.

خروجی فقط یک JSON معتبر باشه، بدون هیچ متن اضافه و بدون ```:
{
  "title": "عنوان جذاب فارسی، حداکثر ۷۰ کاراکتر",
  "description": "توضیحات یوتیوب، ۲ تا ۳ خط، با منبع خبر",
  "narration": "متن کامل روایت برای تبدیل به صدا",
  "keywords": ["۳ تا ۵ کلمه‌ی کلیدی انگلیسی برای جستجوی تصویر"],
  "tags": ["۵ تا ۸ تگ فارسی و انگلیسی"]
}"""


def get_news(limit=10):
    feed = feedparser.parse(NEWS_URL)
    items = []
    for e in feed.entries[:limit]:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        items.append(f"- {e.title} ({e.get('source', {}).get('title', '')}) {summary[:200]}")
    if not items:
        raise RuntimeError("خبری از RSS نیومد")
    return "\n".join(items)


def write_script(news):
    for attempt in range(1, 4):
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": news},
                ],
            },
            timeout=180,
        )
        raw = ""
        try:
            body = r.json()
            raw = body["choices"][0]["message"]["content"] or ""
            print("مدل:", body.get("model"))
        except Exception:
            print("پاسخ نامعتبر:", r.status_code, r.text[:300])
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        print(f"تلاش {attempt} ناموفق. خروجی مدل:", raw[:300])
        time.sleep(5)
    raise RuntimeError("مدل JSON درست نداد")


async def make_audio(text, path):
    await edge_tts.Communicate(text, VOICE).save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    news = get_news()
    data = write_script(news)
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    asyncio.run(make_audio(data["narration"], f"{OUT}/narration.mp3"))
    print("عنوان:", data["title"])
    print("تمام شد: output/script.json و output/narration.mp3")


if __name__ == "__main__":
    main()
