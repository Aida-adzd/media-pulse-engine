import io
import logging

import httpx
import pdfplumber

from app.schemas import ExtractResponse

logger = logging.getLogger("extraction.pdf")

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MediaPulseBot/1.0)"}


def extract_pdf(url: str) -> ExtractResponse:
    resp = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    pages_text: list[str] = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        title = (pdf.metadata or {}).get("Title") or None
        author = (pdf.metadata or {}).get("Author") or None
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text.strip())

    raw_content = "\n\n".join(pages_text)
    if not raw_content.strip():
        raise RuntimeError(f"pdfplumber could not extract text from {url}")

    return ExtractResponse(
        title=title,
        author=author,
        raw_content=raw_content,
        metadata={"page_count": len(pages_text), "source_url": url},
    )
