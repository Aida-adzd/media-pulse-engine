#!/usr/bin/env python3
"""
One-off script: backfill tags for completed sources that have none.

Reads from .env, calls Gemini (flash-lite) on each source's summary,
upserts into `tags` and `source_tags`. Never touches chunks or embeddings.

Usage:
    python scripts/backfill_tags.py [--dry-run]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load .env from the project root (one level up from scripts/)
load_dotenv(Path(__file__).parent.parent / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

SUPABASE_HEADERS = {
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "apikey": SUPABASE_KEY,
    "Accept": "application/json",
}


def fetch_sources_without_tags() -> list[dict]:
    """Return completed sources that have no rows in source_tags."""
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=SUPABASE_HEADERS,
        params={"status": "eq.completed", "select": "id,title,summary,content_type"},
        timeout=15,
    )
    resp.raise_for_status()
    all_sources = resp.json()

    # Fetch source_ids that already have tags
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/source_tags",
        headers=SUPABASE_HEADERS,
        params={"select": "source_id"},
        timeout=15,
    )
    resp.raise_for_status()
    tagged_ids = {row["source_id"] for row in resp.json()}

    untagged = [s for s in all_sources if s["id"] not in tagged_ids and s.get("summary")]
    return untagged


def extract_tags_from_gemini(title: str, summary: str, content_type: str) -> list[str]:
    """Ask Gemini for 3-7 short topic tags given title + summary only."""
    prompt = (
        "Given the title and summary below, respond with ONLY a valid JSON array "
        "of 3 to 7 short lowercase topic keywords (e.g. [\"machine learning\", \"productivity\"]). "
        "No explanation, no markdown, just the JSON array.\n\n"
        f"Content type: {content_type}\n"
        f"Title: {title}\n"
        f"Summary: {summary}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    for attempt in range(3):
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=30,
        )
        if resp.status_code in (429, 503) and attempt < 2:
            wait = 30 * (attempt + 1)
            print(f"    Gemini {resp.status_code} — retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    candidates = resp.json().get("candidates", [])
    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "") if candidates else ""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Try parsing as-is first
    try:
        tags = json.loads(stripped)
        if isinstance(tags, list):
            return [t.lower().strip() for t in tags if isinstance(t, str) and t.strip()]
    except json.JSONDecodeError:
        pass

    # Fallback: collapse newlines and try again (handles Gemini splitting array across lines)
    try:
        collapsed = " ".join(stripped.splitlines())
        tags = json.loads(collapsed)
        if isinstance(tags, list):
            return [t.lower().strip() for t in tags if isinstance(t, str) and t.strip()]
    except json.JSONDecodeError:
        pass

    # Last resort: extract all quoted strings from the response
    import re
    found = re.findall(r'"([^"]{2,40})"', stripped)
    if found:
        print(f"    Warning: used regex fallback on malformed JSON.")
        return [t.lower().strip() for t in found if t.strip()]

    print(f"    Warning: could not parse Gemini response: {stripped[:100]}")
    return []


def write_tags(source_id: str, tag_names: list[str]) -> int:
    """Upsert tags by name, then link to source. Returns number of tags linked."""
    if not tag_names:
        return 0

    # Upsert tag names (ignore if already exists)
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/tags?on_conflict=name",
        headers={**SUPABASE_HEADERS, "Content-Type": "application/json",
                 "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json=[{"name": n} for n in tag_names],
        timeout=10,
    )
    resp.raise_for_status()

    # Fetch IDs for all tag names (new + pre-existing)
    names_filter = "(" + ",".join(f'"{n}"' for n in tag_names) + ")"
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/tags",
        headers=SUPABASE_HEADERS,
        params={"name": f"in.{names_filter}", "select": "id"},
        timeout=10,
    )
    resp.raise_for_status()
    tag_ids = [t["id"] for t in resp.json()]

    if not tag_ids:
        return 0

    # Link source to tags
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/source_tags",
        headers={**SUPABASE_HEADERS, "Content-Type": "application/json",
                 "Prefer": "return=minimal"},
        json=[{"source_id": source_id, "tag_id": tid} for tid in tag_ids],
        timeout=10,
    )
    resp.raise_for_status()
    return len(tag_ids)


def main():
    parser = argparse.ArgumentParser(description="Backfill tags for untagged completed sources.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without writing anything.")
    args = parser.parse_args()

    print(f"Using model: {GEMINI_MODEL}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}\n")

    sources = fetch_sources_without_tags()
    if not sources:
        print("All completed sources already have tags. Nothing to do.")
        return

    print(f"Found {len(sources)} source(s) without tags:\n")
    for s in sources:
        print(f"  - {s['title'][:70]} ({s['id'][:8]}...)")

    print()
    total_tagged = 0

    for i, source in enumerate(sources, 1):
        title = source.get("title") or "Untitled"
        summary = source.get("summary", "")
        print(f"[{i}/{len(sources)}] {title[:60]}")

        tags = extract_tags_from_gemini(title, summary, source.get("content_type", "article"))
        if not tags:
            print("    No tags returned — skipping.")
            continue

        print(f"    Tags: {tags}")

        if not args.dry_run:
            written = write_tags(source["id"], tags)
            print(f"    Written: {written} tag(s) linked.")
            total_tagged += written
        else:
            print("    [dry-run] Would write these tags.")

        # Pause between Gemini calls to stay within rate limits
        if i < len(sources):
            time.sleep(4)

    print(f"\nDone. {total_tagged} tag links written across {len(sources)} source(s).")


if __name__ == "__main__":
    main()
