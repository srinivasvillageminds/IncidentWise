"""OCR fallback for scanned pages - two interchangeable backends.

  ollama  local VLM (default qwen3-vl:8b). Data never leaves the machine.
          Slow on CPU (expect minutes/page for an 8B model) - fine for a
          few dozen pages, painful for hundreds. Lighter alternatives via
          OLLAMA_VLM_MODEL: granite3.2-vision:2b (IBM, document-specialized,
          much faster), or community document-OCR models on ollama.com.
  openai  OpenAI vision API (default gpt-4o-mini). Fast, excellent on
          handwriting and Indic scripts, costs per page. Acceptable for
          this PUBLIC government corpus; do NOT use it later for permits,
          near-miss records, or P&IDs - that data must stay on-prem.

Both are page-level fallbacks: only pages with no extractable text get OCR'd.
"""
from __future__ import annotations

import base64
import re
import time

import httpx

from config import (
    OLLAMA_URL,
    OLLAMA_VLM_MODEL,
    OPENAI_API_KEY,
    OPENAI_VLM_MODEL,
)

PROMPT = (
    "Transcribe ALL text from this scanned document page, exactly as written. "
    "Preserve reading order. For tables, output one row per line with cells "
    "separated by ' | '. Include form field labels AND their filled or "
    "handwritten values. If text is in Hindi or Gujarati, transcribe it in "
    "that script. Output plain text only - no commentary, no markdown fences. "
    "If the page contains no text at all, return a completely empty response - "
    "do NOT describe the page or say there is no text."
)

# Canned non-content responses VLMs emit for blank/photo pages. If a SHORT
# output matches, it is junk, not page text - without this filter such
# sentences pass the length gate, get indexed as content, and get cached.
_JUNK = re.compile(
    r"no (readable|extractable|visible|discernible)?\s*text|blank page|"
    r"page (is|appears)\s*(blank|empty)|contains no|nothing to transcribe|"
    r"unable to|i cannot|i'm sorry|does not contain", re.IGNORECASE)


def clean_ocr_text(text: str) -> str:
    """Strip fences; collapse canned 'no text here' responses to empty."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith(("text", "plaintext", "markdown")):
            t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.strip()
    if len(t) < 120 and _JUNK.search(t):
        return ""
    return t


class OCRError(RuntimeError):
    pass


def ocr_page_ollama(png: bytes, read_timeout: float = 600.0) -> str:
    b64 = base64.b64encode(png).decode()
    payload = {"model": OLLAMA_VLM_MODEL, "prompt": PROMPT, "images": [b64],
               "stream": False, "options": {"temperature": 0}}
    with httpx.Client(timeout=httpx.Timeout(10.0, read=read_timeout)) as client:
        r = client.post(f"{OLLAMA_URL}/api/generate", json=payload)
        if r.status_code == 404:
            raise OCRError(f"VLM '{OLLAMA_VLM_MODEL}' not pulled. "
                           f"Run:  ollama pull {OLLAMA_VLM_MODEL}")
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise OCRError(f"Ollama VLM error: {data['error']}")
        return (data.get("response") or "").strip()


def ocr_page_openai(png: bytes, read_timeout: float = 120.0) -> str:
    if not OPENAI_API_KEY:
        raise OCRError("OPENAI_API_KEY is not set.")
    b64 = base64.b64encode(png).decode()
    payload = {
        "model": OPENAI_VLM_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
        ]}],
        "max_tokens": 4000,
        "temperature": 0,
    }
    delays = [20.0, 40.0, 80.0]  # backoff for genuine rate limits
    with httpx.Client(timeout=httpx.Timeout(15.0, read=read_timeout)) as client:
        for attempt in range(len(delays) + 1):
            r = client.post("https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                            json=payload)
            if r.status_code in (401, 403):
                raise OCRError("OpenAI authentication failed - check OPENAI_API_KEY.")
            if r.status_code == 429:
                body = r.text[:400]
                if "insufficient_quota" in body or "billing" in body.lower():
                    raise OCRError(
                        "OpenAI insufficient_quota: this key's account has no "
                        "credits/payment method. Fix billing at platform.openai.com "
                        "- retrying will not help. Detail: " + body[:200])
                if attempt < len(delays):
                    wait = min(float(r.headers.get("retry-after") or delays[attempt]), 120.0)
                    time.sleep(wait)
                    continue
                raise OCRError("OpenAI rate limit persisted after "
                               f"{len(delays)} retries. Detail: {body[:200]}")
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
    raise OCRError("OpenAI OCR: unreachable state")  # defensive


def ocr_page(png: bytes, backend: str) -> str:
    if backend == "ollama":
        return clean_ocr_text(ocr_page_ollama(png))
    if backend == "openai":
        return clean_ocr_text(ocr_page_openai(png))
    raise OCRError(f"Unknown OCR backend: {backend!r} (use 'ollama' or 'openai').")
