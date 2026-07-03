#!/usr/bin/env python3
"""
Fetch a YouTube transcript using the Supadata API.

Setup:
1. Copy .env.example to .env
2. Paste your Supadata API key after SUPADATA_API_KEY=
3. Paste YouTube video URLs after YOUTUBE_URL_1=, YOUTUBE_URL_2=, etc.
4. Run: python3 fetch_supadata_transcript.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://api.supadata.ai/v1"
ENV_FILE = Path(".env")
OUTPUT_DIR = Path("transcripts")


def load_env_file(path: Path) -> None:
    if not path.exists():
        print("Missing .env file.")
        print("Create one by copying .env.example, then paste your API key and YouTube URL.")
        sys.exit(1)

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("paste_"):
        print(f"Missing value for {name} in .env")
        sys.exit(1)
    return value


def get_youtube_urls() -> list[str]:
    urls = []

    legacy_url = os.environ.get("YOUTUBE_URL", "").strip()
    if legacy_url and not legacy_url.startswith("paste_"):
        urls.append(legacy_url)

    for index in range(1, 51):
        url = os.environ.get(f"YOUTUBE_URL_{index}", "").strip()
        if url and not url.startswith("paste_"):
            urls.append(url)

    if not urls:
        print("Missing YouTube URL in .env")
        print("Paste one or more video links after YOUTUBE_URL_1=, YOUTUBE_URL_2=, etc.")
        sys.exit(1)

    return urls


def validate_youtube_url(url: str) -> None:
    looks_like_youtube_url = (
        url.startswith("https://www.youtube.com/watch?v=")
        or url.startswith("https://youtube.com/watch?v=")
        or url.startswith("https://youtu.be/")
        or url.startswith("http://www.youtube.com/watch?v=")
        or url.startswith("http://youtube.com/watch?v=")
        or url.startswith("http://youtu.be/")
    )

    if not looks_like_youtube_url:
        print("The YOUTUBE_URL value does not look like a YouTube video link.")
        print("Use a link like: https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        print("The value you pasted should not look like: sd_28f55c518849d64dc8380676a2f0229d")
        sys.exit(1)


def request_json(url: str, api_key: str) -> tuple[int, dict]:
    request = Request(
        url,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=90) as response:
            status = response.status
            raw_body = response.read().decode("utf-8")
            return status, json.loads(raw_body)
    except HTTPError as error:
        raw_body = error.read().decode("utf-8", errors="replace")
        print(f"Supadata returned HTTP {error.code}.")
        print(raw_body)
        sys.exit(1)
    except URLError as error:
        print("Network error while calling Supadata.")
        print(error)
        sys.exit(1)
    except json.JSONDecodeError:
        print("Supadata returned a response, but it was not valid JSON.")
        sys.exit(1)


def fetch_transcript(api_key: str, youtube_url: str, lang: str, mode: str) -> dict:
    query = urlencode(
        {
            "url": youtube_url,
            "lang": lang,
            "text": "true",
            "mode": mode,
        }
    )
    status, data = request_json(f"{API_BASE}/transcript?{query}", api_key)

    if status == 202 and "jobId" in data:
        return poll_job(api_key, data["jobId"])

    return data


def poll_job(api_key: str, job_id: str) -> dict:
    print(f"Supadata is processing the transcript. Job ID: {job_id}")

    for attempt in range(1, 121):
        time.sleep(1)
        _, data = request_json(f"{API_BASE}/transcript/{job_id}", api_key)
        status = data.get("status")

        if status == "completed":
            return data.get("result", data)

        if status == "failed":
            print("Transcript job failed.")
            print(json.dumps(data, indent=2))
            sys.exit(1)

        print(f"Waiting... status={status or 'unknown'} attempt={attempt}/120")

    print("Timed out while waiting for Supadata to finish.")
    sys.exit(1)


def extract_text(result: dict) -> str:
    content = result.get("content")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)

    return ""


def safe_filename_from_url(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    video_id = match.group(1) if match else "youtube-video"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{video_id}-{timestamp}"


def main() -> None:
    load_env_file(ENV_FILE)

    api_key = get_required_env("SUPADATA_API_KEY")
    youtube_urls = get_youtube_urls()
    lang = os.environ.get("TRANSCRIPT_LANG", "en").strip() or "en"
    mode = os.environ.get("TRANSCRIPT_MODE", "native").strip() or "native"

    for youtube_url in youtube_urls:
        validate_youtube_url(youtube_url)

    for video_number, youtube_url in enumerate(youtube_urls, start=1):
        print(f"Calling Supadata for video {video_number}/{len(youtube_urls)}...")
        result = fetch_transcript(api_key, youtube_url, lang, mode)
        transcript_text = extract_text(result)

        if not transcript_text:
            print(f"No transcript text was returned for: {youtube_url}")
            print("Full API response:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            continue

        OUTPUT_DIR.mkdir(exist_ok=True)
        filename_base = safe_filename_from_url(youtube_url)
        txt_path = OUTPUT_DIR / f"{filename_base}.txt"
        json_path = OUTPUT_DIR / f"{filename_base}.json"

        txt_path.write_text(transcript_text, encoding="utf-8")
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        print("Done.")
        print(f"Transcript text saved to: {txt_path}")
        print(f"Full JSON response saved to: {json_path}")


if __name__ == "__main__":
    main()
