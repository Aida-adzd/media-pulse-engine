import json
import logging

import httpx
import trafilatura

from app.schemas import ExtractResponse

logger = logging.getLogger("extraction.article")

_FALLBACK_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaPulseBot/1.0)"}


def extract_article(url: str) -> ExtractResponse:
    downloaded = trafilatura.fetch_url(url)

    if not downloaded:
        # Some sites block trafilatura's default fetcher; retry with a browser-like UA.
        resp = httpx.get(url, headers=_FALLBACK_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        downloaded = resp.text

    extracted = trafilatura.extract(
        downloaded,
        output_format="json",
        with_metadata=True,
        include_comments=False,
    )

    if not extracted:
        raise RuntimeError(f"trafilatura could not extract content from {url}")

    data = json.loads(extracted)
    raw_content = data.get("text", "")

    metadata = {
        "word_count": len(raw_content.split()),
        "sitename": data.get("sitename"),
        "date": data.get("date"),
    }

    return ExtractResponse(
        title=data.get("title"),
        author=data.get("author"),
        raw_content=raw_content,
        metadata=metadata,
    )
