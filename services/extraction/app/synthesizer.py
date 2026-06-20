import logging

import httpx

from app.db import SUPABASE_URL, supabase_headers
from app.gemini import call_gemini, extract_text

logger = logging.getLogger("extraction.synthesizer")

MIN_SOURCES = 2


def synthesize_digest(period_type: str, since: str, until: str) -> dict:
    """Synthesize a digest from all completed sources in the given time window."""
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        # httpx list-of-tuples allows duplicate param names for PostgREST range filters
        params=[
            ("status", "eq.completed"),
            ("processed_at", f"gte.{since}"),
            ("processed_at", f"lte.{until}"),
            ("select", "id,title,summary,content_type,url"),
            ("order", "processed_at.asc"),
        ],
        timeout=15,
    )
    r.raise_for_status()
    sources = [s for s in r.json() if s.get("summary")]

    if len(sources) < MIN_SOURCES:
        logger.info("Only %d source(s) in window; skipping digest", len(sources))
        return {"period_type": period_type, "sources_included": len(sources),
                "digest_id": None, "skipped": True}

    items_text = "\n\n".join(
        f"[{i+1}] {s['content_type'].upper()}: {s['title']}\n{s['summary']}"
        for i, s in enumerate(sources)
    )
    prompt = (
        f"You are synthesizing a {period_type} knowledge digest for a personal learning pipeline.\n\n"
        f"Here are {len(sources)} items processed during this period:\n\n"
        f"{items_text}\n\n"
        f"Write a concise markdown digest with these sections:\n"
        f"## Key Themes\n2–3 recurring ideas across the items.\n\n"
        f"## Top Insights\n3–5 most important concrete takeaways.\n\n"
        f"## Action Items\nConcrete next steps worth acting on (skip if none).\n\n"
        f"## Open Questions\nInteresting threads worth exploring further.\n\n"
        f"Be specific and reference actual titles. Write in a clear, personal knowledge management style."
    )

    resp = call_gemini({"contents": [{"parts": [{"text": prompt}]}]}, timeout=120)
    digest_content = extract_text(resp).strip()

    if not digest_content:
        logger.warning("Gemini returned empty digest content")
        return {"period_type": period_type, "sources_included": len(sources),
                "digest_id": None, "skipped": True}

    r = httpx.post(
        f"{SUPABASE_URL}/rest/v1/digests",
        headers={**supabase_headers(with_content_type=True), "Prefer": "return=representation"},
        json={
            "period_type": period_type,
            "period_start": since,
            "period_end": until,
            "content": digest_content,
            "source_ids": [s["id"] for s in sources],
        },
        timeout=15,
    )
    r.raise_for_status()
    digest_id = r.json()[0]["id"] if r.json() else None

    logger.info("Digest written: %s (%d sources, id=%s)", period_type, len(sources), digest_id)
    return {
        "period_type": period_type,
        "sources_included": len(sources),
        "digest_id": digest_id,
        "skipped": False,
    }
