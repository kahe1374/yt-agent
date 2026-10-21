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
        "MODELS", "google/gemma-4-31b-it:free,qwen/qwen3.8-27b:free"
    ).split(",")
    if m.strip()
]
VOICE = os.environ.get("VOICE", "fa-IR-DilaraNeural")
RATE = os.environ.get("RATE", "-8%")
OUT = "output"

SYSTEM_PROMPT = """تو نویسنده‌ی حرفه‌ای اسکریپت یه کانال یوتیوب فارسی درباره‌ی تکنولوژی هستی. کانال بدون چهره است و متن تو را یک گوینده‌ی زن با صدا می‌خواند.

ورودی: لیست خبرهای امروز تکنولوژی (به انگلیسی).

کار تو:
1. مهم‌ترین و جذاب‌ترین خبر را انتخاب کن.
2. روایت را به فارسی معیار، روان و صمیمی بنویس، حدود ۱۵۰ تا ۲۰۰ کلمه.
3. شروع با یک جمله‌ی قلاب‌کننده، وسط توضیح ساده، آخر یک جمع‌بندی کوتاه.
4. فقط چیزی بنویس که در خبر هست. عدد و ادعای ساختگی نیاور. اگر اطلاعات خبر کم است، کوتاه‌تر بنویس و پرگویی نکن.

قواعد نوشتن برای گوش:
- جمله‌ها کوتاه باشند (حداکثر ۱۵ کلمه). بعد از هر جمله نقطه بگذار.
- املا و دستور زبان فارسی را درست رعایت کن. ترجمه‌ی تحت‌اللفظی و عجیب ننویس. معادل درست فارسی را بنویس (مثلاً foldable یعنی تاشو).
- فقط از حروف فارسی استفاده کن. هیچ حرف انگلیسی، روسی یا زبان دیگری در روایت نباشد.
- اسم شرکت‌ها و محصولات را به فارسی بنویس (مثلاً اپل، گوگل، مایکروسافت، آیفون).
- عددها را با حروف بنویس (مثلاً «سی درصد»، «دو میلیارد دلار»).
- از ایموجی، پرانتز، نقل‌قول، علامت‌های خاص و فهرست استفاده نکن.
- محاوره‌ی افراطی و کلمات عامیانه‌ی نادرست نیاور.

خروجی فقط یک JSON معتبر باشد، بدون هیچ متن اضافه و بدون ```:
{
  "title": "عنوان جذاب فارسی، حداکثر ۷۰ کاراکتر",
  "description": "توضیحات یوتیوب، ۲ تا ۳ خط، با نام منبع خبر",
  "narration": "متن کامل روایت برای تبدیل به صدا",
  "keywords": ["۳ تا ۵ کلمه‌ی کلیدی انگلیسی برای جستجوی تصویر"],
  "tags": ["۵ تا ۸ تگ فارسی و انگلیسی"]
}"""

EDITOR_PROMPT = """تو ویراستار فارسی هستی. متن زیر برای خوانده شدن با صدای گوینده نوشته شده.
- غلط‌های املایی و دستوری را درست کن.
- جمله‌های نامفهوم یا ترجمه‌ی تحت‌اللفظی را روان و طبیعی کن.
- جمله‌ها کوتاه بمانند. معنی و اطلاعات را تغییر نده و چیزی اضافه نکن.
- فقط از حروف فارسی استفاده کن. عددها را با حروف بنویس.
- هیچ ایموجی، پرانتز یا علامت خاصی نگذار.
فقط متن اصلاح‌شده را برگردان، بدون هیچ توضیح اضافه و بدون عذرخواهی."""


def get_news(limit=10):
    feed = feedparser.parse(NEWS_URL)
    items = []
    for e in feed.entries[:limit]:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        items.append(f"- {e.title} ({e.get('source', {}).get('title', '')}) {summary[:200]}")
    if not items:
        raise RuntimeError("خبری از RSS نیومد")
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
                print("مدل:", GEMINI_MODEL)
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
                        ],
                    },
                    timeout=180,
                )
                body = r.json()
                raw = body["choices"][0]["message"]["content"] or ""
                print("مدل:", body.get("model", model))
        except Exception as e:
            print(f"تلاش {attempt} خطا:", e, (r.text[:200] if r is not None else ""))
        if raw.strip():
            return raw
        time.sleep(15)
    raise RuntimeError("مدل جواب نداد")


def narration_ok(text):
    if len(text) < 200:
        return False
    return not re.search(r"[^\u0600-\u06FF\u200c\s0-9.,!?:;\-«»()\"'…]", text)


def write_script(news):
    for attempt in range(1, 5):
        raw = call_llm(SYSTEM_PROMPT, news, json_mode=True)
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if narration_ok(data.get("narration", "")):
                    return data
                print(f"تلاش {attempt}: متن روایت قابل قبول نبود (کوتاه یا دارای حروف غیرفارسی).")
            except json.JSONDecodeError:
                print(f"تلاش {attempt}: JSON نامعتبر.")
        else:
            print(f"تلاش {attempt}: JSON پیدا نشد:", raw[:200])
        time.sleep(5)
    raise RuntimeError("مدل متن قابل قبول نداد")


def proofread(text):
    try:
        out = call_llm(EDITOR_PROMPT, text).strip()
    except Exception as e:
        print("ویراستاری انجام نشد:", e)
        return text
    if 0.7 * len(text) <= len(out) <= 1.3 * len(text) and narration_ok(out):
        return out
    print("خروجی ویراستار رد شد، متن اصلی استفاده می‌شود.")
    return text


def clean_for_tts(text):
    text = re.sub(r"[*#_`>\[\]()\"«»]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def make_audio(text, path):
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    news = get_news()
    data = write_script(news)
    data["narration"] = clean_for_tts(proofread(data["narration"]))
    with open(f"{OUT}/script.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    asyncio.run(make_audio(data["narration"], f"{OUT}/narration.mp3"))
    print("عنوان:", data["title"])
    print("تمام شد: output/script.json و output/narration.mp3")


if __name__ == "__main__":
    main()
