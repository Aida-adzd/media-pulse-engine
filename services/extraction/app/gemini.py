import logging
import os
import time

import httpx

logger = logging.getLogger("extraction.gemini")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def call_gemini(payload: dict, timeout: int = 120) -> dict:
    """POST to Gemini with 3-attempt retry on 429/503. Returns the raw API response."""
    for attempt in range(3):
        resp = httpx.post(_API_URL, params={"key": GEMINI_API_KEY}, json=payload, timeout=timeout)
        if resp.status_code in (429, 503) and attempt < 2:
            wait = 30 * (attempt + 1)
            logger.warning("Gemini %s on attempt %d; retrying in %ds", resp.status_code, attempt + 1, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    return {}


def extract_text(response: dict) -> str:
    """Pull the text string out of a Gemini generateContent response."""
    candidates = response.get("candidates", [])
    if not candidates:
        return ""
    return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
