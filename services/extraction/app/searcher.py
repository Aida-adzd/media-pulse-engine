import logging

import httpx

from app.db import SUPABASE_URL, supabase_headers
from app.embeddings import embed_query
from app.gemini import call_gemini, extract_text

logger = logging.getLogger("extraction.searcher")


def search(query: str, match_count: int = 5) -> dict:
    embedding = embed_query(query)

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_chunks",
        headers=supabase_headers(with_content_type=True),
        json={"query_embedding": embedding, "match_count": match_count},
        timeout=30,
    )
    resp.raise_for_status()
    matches = resp.json()

    if not matches:
        return {"query": query, "answer": "No relevant content found in your knowledge base.", "sources": []}

    seen: set[str] = set()
    source_ids = [m["source_id"] for m in matches if not (m["source_id"] in seen or seen.add(m["source_id"]))]

    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        params={"id": f"in.({','.join(source_ids)})", "select": "id,title,url"},
        timeout=10,
    )
    resp.raise_for_status()
    sources_by_id = {s["id"]: s for s in resp.json()}

    context_parts = []
    for m in matches:
        src = sources_by_id.get(m["source_id"], {})
        title = src.get("title") or "Unknown"
        context_parts.append(f"[{title}]\n{m['content']}")
    context = "\n\n---\n\n".join(context_parts)

    prompt = (
        f"Based on the following excerpts from a personal knowledge base, answer the query concisely.\n\n"
        f"Query: {query}\n\n"
        f"Excerpts:\n{context}\n\n"
        f"Answer in 2-4 sentences. If the excerpts don't contain enough information, say so clearly."
    )

    resp = call_gemini({"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
    answer = extract_text(resp).strip()

    sources = [
        {"title": sources_by_id.get(sid, {}).get("title") or "Unknown",
         "url": sources_by_id.get(sid, {}).get("url") or ""}
        for sid in source_ids
    ]

    return {"query": query, "answer": answer, "sources": sources}
