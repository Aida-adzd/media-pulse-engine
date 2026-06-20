import logging

import yt_dlp

from app.schemas import ExtractResponse

logger = logging.getLogger("extraction.instagram")

_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
}


def extract_instagram(url: str) -> ExtractResponse:
    try:
        with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("yt-dlp failed for %s: %s", url, exc)
        return ExtractResponse(
            title=None,
            author=None,
            raw_content=f"Instagram content at {url} — extraction failed (may require login)",
            metadata={"url": url, "extraction_error": str(exc)},
        )

    uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id")
    description = (info.get("description") or "").strip()
    title = info.get("title") or (f"Instagram post by @{uploader}" if uploader else "Instagram post")

    metadata = {}
    for key in ("uploader", "uploader_id", "upload_date", "view_count", "like_count", "duration", "thumbnail"):
        val = info.get(key)
        if val is not None:
            metadata[key] = val

    raw_content = description or f"[No caption] Instagram content by @{uploader or 'unknown'}"

    return ExtractResponse(
        title=title,
        author=uploader,
        raw_content=raw_content,
        metadata=metadata,
    )
