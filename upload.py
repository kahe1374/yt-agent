import json
import os

import requests

OUT = "output"
CLIENT_ID = os.environ["YT_CLIENT_ID"]
CLIENT_SECRET = os.environ["YT_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["YT_REFRESH_TOKEN"]
PRIVACY = os.environ.get("PRIVACY", "private")
CATEGORY_ID = "28"  # Science & Technology


def get_access_token():
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {r.status_code} {r.text[:300]}")
    return r.json()["access_token"]


def clean_tags(tags):
    out, total = [], 0
    for t in tags:
        t = str(t).replace("<", "").replace(">", "").strip()
        if not t:
            continue
        if total + len(t) + 1 > 450:
            break
        out.append(t)
        total += len(t) + 1
    return out


def main():
    with open(f"{OUT}/script.json", encoding="utf-8") as f:
        data = json.load(f)
    video_path = f"{OUT}/video.mp4"
    size = os.path.getsize(video_path)

    token = get_access_token()

    description = data.get("description", "").strip()
    description += "\n\nNarrated with an AI-generated voice."
    body = {
        "snippet": {
            "title": data["title"][:100],
            "description": description[:4900],
            "tags": clean_tags(data.get("tags", [])),
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        json=body,
        timeout=60,
    )
    if init.status_code != 200:
        raise RuntimeError(f"Upload init failed: {init.status_code} {init.text[:500]}")
    upload_url = init.headers["Location"]

    with open(video_path, "rb") as f:
        r = requests.put(
            upload_url,
            headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
            data=f,
            timeout=600,
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed: {r.status_code} {r.text[:500]}")
    video_id = r.json()["id"]
    print("uploaded:", video_id)
    print("studio: https://studio.youtube.com/video/" + video_id + "/edit")


if __name__ == "__main__":
    main()
