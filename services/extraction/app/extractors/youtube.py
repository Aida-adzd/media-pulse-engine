import json
import logging
import os
import re

import httpx
import yt_dlp
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.schemas import ExtractResponse

logger = logging.getLogger("extraction.youtube")

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})"
)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _video_id(url: str) -> str:
    match = YOUTUBE_ID_RE.search(url)
    if not match:
        raise ValueError(f"Could not extract YouTube video ID from {url}")
    return match.group(1)


def _fetch_oembed(url: str) -> dict:
    resp = httpx.get(
        "https://www.youtube.com/oembed",
        params={"url": url, "format": "json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_transcript_api(video_id: str, cookies_file: str = "") -> str:
    kwargs = {"cookies": cookies_file} if cookies_file and os.path.exists(cookies_file) else {}
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"], **kwargs)
    except (TranscriptsDisabled, NoTranscriptFound):
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
        available = [t.language_code for t in transcript_list]
        transcript = transcript_list.find_transcript(available).fetch()
    return " ".join(segment["text"] for segment in transcript)


def _pick_sub_url(subs: dict) -> tuple:
    """Return (url, ext) for the best available subtitle track."""
    for lang in ["en", "en-US", "en-orig"]:
        if lang in subs:
            for fmt in subs[lang]:
                if fmt.get("ext") in ("json3", "vtt"):
                    return fmt["url"], fmt["ext"]
    for lang_subs in subs.values():
        for fmt in lang_subs:
            if fmt.get("ext") in ("json3", "vtt"):
                return fmt["url"], fmt["ext"]
    return None, None


def _parse_json3(data: dict) -> str:
    parts = []
    for event in data.get("events", []):
        for seg in event.get("segs", []):
            text = seg.get("utf8", "").strip()
            if text and text != "\n":
                parts.append(text)
    return " ".join(parts)


def _parse_vtt(content: str) -> str:
    lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return " ".join(lines)


def _fetch_transcript_yt_dlp(url: str) -> str:
    """Fallback: use yt-dlp to get subtitle URLs, then download via yt-dlp's
    own session so cookies and headers are applied correctly."""
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "")

    ydl_opts = {"quiet": True, "no_warnings": True}
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

        sub_url, ext = _pick_sub_url(info.get("subtitles", {}))
        if not sub_url:
            sub_url, ext = _pick_sub_url(info.get("automatic_captions", {}))
        if not sub_url:
            raise RuntimeError("No subtitles available for this video")

        # Use yt-dlp's own urllib session — cookies and UA are already set.
        response = ydl.urlopen(sub_url)
        content = response.read().decode("utf-8")

    if ext == "json3":
        return _parse_json3(json.loads(content))
    return _parse_vtt(content)


def extract_youtube(url: str) -> ExtractResponse:
    video_id = _video_id(url)

    title = None
    author = None
    metadata: dict = {"video_id": video_id}

    try:
        oembed = _fetch_oembed(url)
        title = oembed.get("title")
        author = oembed.get("author_name")
        metadata["thumbnail"] = oembed.get("thumbnail_url")
    except Exception:
        logger.warning("oEmbed lookup failed for %s", url, exc_info=True)

    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "")
    try:
        transcript_text = _fetch_transcript_api(video_id, cookies_file=cookies_file)
        metadata["transcript_source"] = "youtube_transcript_api"
    except Exception as primary_exc:
        logger.warning(
            "youtube-transcript-api failed for %s (%s); falling back to yt-dlp",
            url,
            primary_exc,
        )
        transcript_text = _fetch_transcript_yt_dlp(url)
        metadata["transcript_source"] = "yt-dlp"

    metadata["word_count"] = len(transcript_text.split())

    raw_content = f"Title: {title}\nChannel: {author}\n\nTranscript:\n{transcript_text}"

    return ExtractResponse(
        title=title,
        author=author,
        raw_content=raw_content,
        metadata=metadata,
    )
