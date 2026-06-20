import json
import logging

import httpx

from app.db import SUPABASE_URL, supabase_headers
from app.embeddings import embed_query
from app.gemini import call_gemini, extract_text

logger = logging.getLogger("extraction.linker")

SIMILARITY_THRESHOLD = 0.75
MAX_CANDIDATES = 5


def _parse_gemini_json(data: dict) -> list:
    text = extract_text(data)
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(stripped)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        logger.warning("Linker: Gemini returned non-JSON: %.200s", text)
        return []


def link_source(source_id: str) -> dict:
    """Find and store semantic connections for a completed source."""
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        params={"id": f"eq.{source_id}", "select": "id,title,summary,content_type"},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows or not rows[0].get("summary"):
        logger.info("Source %s has no summary; skipping linking", source_id)
        return {"source_id": source_id, "connections_written": 0}
    source = rows[0]

    query_vec = embed_query(source["summary"])
    embedding_str = "[" + ",".join(str(v) for v in query_vec) + "]"

    r = httpx.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_chunks",
        headers=supabase_headers(with_content_type=True),
        json={"query_embedding": embedding_str, "match_count": 30},
        timeout=15,
    )
    r.raise_for_status()

    source_scores: dict[str, float] = {}
    for m in r.json():
        sid = m["source_id"]
        if sid == source_id:
            continue
        sim = float(m.get("similarity", 0))
        if sim < SIMILARITY_THRESHOLD:
            continue
        if sim > source_scores.get(sid, 0):
            source_scores[sid] = sim

    candidates = sorted(source_scores.items(), key=lambda x: x[1], reverse=True)[:MAX_CANDIDATES]
    if not candidates:
        logger.info("No candidates above threshold for source %s", source_id)
        return {"source_id": source_id, "connections_written": 0}

    cand_ids = ",".join(sid for sid, _ in candidates)
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        params={"id": f"in.({cand_ids})", "select": "id,title,summary"},
        timeout=10,
    )
    r.raise_for_status()
    cand_map = {s["id"]: s for s in r.json() if s.get("summary")}
    if not cand_map:
        return {"source_id": source_id, "connections_written": 0}

    cand_text = "\n\n".join(
        f"Candidate {i+1} (source_id: {sid}):\nTitle: {cand_map[sid]['title']}\n"
        f"Summary: {cand_map[sid]['summary']}"
        for i, (sid, _) in enumerate(candidates)
        if sid in cand_map
    )
    prompt = (
        f"Source A:\nTitle: {source['title']}\nSummary: {source['summary']}\n\n"
        f"For each candidate, classify its relationship TO Source A.\n"
        f"Choose exactly one of: related, contradicts, builds_on, duplicate, none.\n"
        f"Use 'none' if there is no meaningful intellectual connection.\n\n"
        f"{cand_text}\n\n"
        f"Respond with ONLY a valid JSON array of objects, each with keys:\n"
        f"  source_id (string), relationship_type (string)."
    )
    resp = call_gemini(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=60,
    )
    classifications = _parse_gemini_json(resp)

    score_map = dict(candidates)
    written = 0
    for c in classifications:
        cid = c.get("source_id", "")
        rel = c.get("relationship_type", "none")
        if rel == "none" or cid not in cand_map:
            continue
        # Normalize ordering (smaller UUID first) to satisfy UNIQUE(source_id_a, source_id_b)
        a, b = (source_id, cid) if source_id < cid else (cid, source_id)
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/connections",
            headers={**supabase_headers(with_content_type=True), "Prefer": "return=minimal"},
            json={
                "source_id_a": a,
                "source_id_b": b,
                "relationship_type": rel,
                "similarity_score": score_map.get(cid),
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            written += 1
        elif r.status_code == 409:
            logger.debug("Connection %s <-> %s already exists", a, b)
        else:
            r.raise_for_status()

    logger.info("Linked source %s: %d connection(s) written", source_id, written)
    return {"source_id": source_id, "connections_written": written}
