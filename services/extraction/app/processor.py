import json
import logging
from datetime import datetime, timezone

import httpx

from app.db import SUPABASE_URL, supabase_headers
from app.embeddings import embed_passages_batch
from app.gemini import call_gemini, extract_text

logger = logging.getLogger("extraction.processor")

CHUNK_SIZE = 800    # words
CHUNK_OVERLAP = 100  # words
MIN_CONTENT_CHARS = 200
_LOGIN_WALL_PHRASES = ["log in sign up", "login sign up", "please log in", "sign in to continue"]


def _fetch_source(source_id: str) -> dict:
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        params={"id": f"eq.{source_id}", "select": "id,url,content_type,title,raw_content"},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError(f"Source {source_id} not found")
    return rows[0]


def _is_low_quality(raw_content: str) -> str | None:
    """Return a reason string if content is too low quality to process, else None."""
    stripped = raw_content.strip()
    if len(stripped) < MIN_CONTENT_CHARS:
        return f"Content too short ({len(stripped)} chars) — likely a login wall or empty page"
    lower = stripped.lower()
    for phrase in _LOGIN_WALL_PHRASES:
        if phrase in lower:
            return f"Login wall detected: '{stripped[:80]}'"
    return None


def _mark_completed(source_id: str, summary: str) -> None:
    resp = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(with_content_type=True),
        params={"id": f"eq.{source_id}"},
        json={
            "status": "completed",
            "summary": summary,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
        timeout=10,
    )
    resp.raise_for_status()


def _mark_failed(source_id: str, reason: str) -> None:
    resp = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(with_content_type=True),
        params={"id": f"eq.{source_id}"},
        json={"status": "failed", "error_message": reason},
        timeout=10,
    )
    resp.raise_for_status()


def _call_gemini(raw_content: str, title: str, content_type: str) -> dict:
    instructions = (
        "Respond with ONLY a valid JSON object with these keys: "
        "summary (string, 2-3 sentences), key_points (array of strings), "
        "action_items (array of strings), entities (array of strings), "
        "questions (array of strings), "
        "tags (array of 3-7 short lowercase topic keywords, e.g. 'machine learning', 'productivity')."
    )
    prompt = (
        f"{instructions}\n\n"
        f"Content type: {content_type}\nTitle: {title}\n\n"
        f"Content:\n{raw_content[:50000]}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    resp = call_gemini(payload, timeout=120)
    candidates = resp.get("candidates", [])
    if not candidates:
        logger.warning("Gemini returned no candidates: %s", resp)
        return {}
    text = extract_text(resp)
    if not text:
        logger.warning("Gemini candidate had no text; finish_reason=%s", candidates[0].get("finishReason"))
        return {}
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("Gemini returned non-JSON text (len=%d): %.200s", len(text), text)
        return {}


def _chunk_text(raw_content: str) -> list[dict]:
    words = raw_content.split()
    chunks = []
    start = 0
    idx = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunks.append({
            "chunk_index": idx,
            "content": " ".join(words[start:end]),
            "token_count": end - start,
        })
        idx += 1
        if end >= len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _write_tags(source_id: str, tag_names: list[str]) -> int:
    clean = [n.lower().strip() for n in tag_names if n.strip()]
    if not clean:
        return 0

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/tags?on_conflict=name",
        headers={**supabase_headers(with_content_type=True), "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json=[{"name": n} for n in clean],
        timeout=10,
    )
    resp.raise_for_status()

    names_filter = "(" + ",".join(f'"{n}"' for n in clean) + ")"
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/tags",
        headers=supabase_headers(),
        params={"name": f"in.{names_filter}", "select": "id"},
        timeout=10,
    )
    resp.raise_for_status()
    tag_ids = [t["id"] for t in resp.json()]
    if not tag_ids:
        return 0

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/source_tags",
        headers={**supabase_headers(with_content_type=True), "Prefer": "return=minimal"},
        json=[{"source_id": source_id, "tag_id": tid} for tid in tag_ids],
        timeout=10,
    )
    resp.raise_for_status()
    return len(tag_ids)


def process_source(source_id: str) -> dict:
    """Full intelligence pipeline: Gemini → chunk → embed → write to Supabase."""
    source = _fetch_source(source_id)
    raw_content = source.get("raw_content") or ""
    title = source.get("title") or source.get("url") or "Untitled"
    content_type = source.get("content_type") or "article"

    if not raw_content.strip():
        logger.warning("Source %s has empty raw_content", source_id)
        _mark_failed(source_id, "Empty content — nothing to process")
        return {"source_id": source_id, "summary": "", "chunks_written": 0, "insights_written": 0, "tags_written": 0}

    quality_issue = _is_low_quality(raw_content)
    if quality_issue:
        logger.warning("Source %s failed quality check: %s", source_id, quality_issue)
        _mark_failed(source_id, quality_issue)
        return {"source_id": source_id, "summary": "", "chunks_written": 0, "insights_written": 0, "tags_written": 0}

    gemini = _call_gemini(raw_content, title, content_type)
    summary = gemini.get("summary", "")

    insights = []
    for insight_type, key in [
        ("key_point", "key_points"),
        ("action_item", "action_items"),
        ("entity", "entities"),
        ("question", "questions"),
    ]:
        for item in gemini.get(key, []):
            if isinstance(item, str) and item.strip():
                insights.append({
                    "source_id": source_id,
                    "insight_type": insight_type,
                    "content": item.strip(),
                })

    chunks = _chunk_text(raw_content)
    texts = [c["content"] for c in chunks]
    embeddings = embed_passages_batch(texts)
    chunk_rows = [
        {
            "source_id": source_id,
            "chunk_index": c["chunk_index"],
            "content": c["content"],
            "token_count": c["token_count"],
            "embedding": "[" + ",".join(str(v) for v in vec) + "]",
        }
        for c, vec in zip(chunks, embeddings)
    ]

    write_headers = {**supabase_headers(with_content_type=True), "Prefer": "return=minimal"}

    if insights:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/insights", headers=write_headers, json=insights, timeout=30
        )
        resp.raise_for_status()

    if chunk_rows:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/chunks", headers=write_headers, json=chunk_rows, timeout=60
        )
        resp.raise_for_status()

    tags_written = _write_tags(source_id, gemini.get("tags", []))

    _mark_completed(source_id, summary)

    return {
        "source_id": source_id,
        "summary": summary,
        "chunks_written": len(chunk_rows),
        "insights_written": len(insights),
        "tags_written": tags_written,
    }
