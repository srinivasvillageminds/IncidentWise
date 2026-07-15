"""Extract structured incident facts from the indexed corpus.

One LLM call per tier-1/2 document (case studies, investigations) pulls
date, location, incident type, substances, casualties and root cause into
incidents.json - the structured backbone that lets analytics questions
("what were the incidents in 2018?") be answered by COUNTING, not by
sampling six chunks and hoping.

Honesty rules baked in:
- The model may only extract what the text states; unknowns stay null.
- Dates found in filenames (PNGRB publication dates) are marked
  date_source="filename_publication" - a publication date is not an
  incident date and the analytics layer says so.
- Every record carries a confidence field, and incidents.json is plain,
  human-editable JSON: the domain expert (you) is the final validator.
  Spot-check 10 records before trusting any number in public.

Run after ingest:   python facts.py          (incremental, cached by doc)
                    python facts.py --refresh  (re-extract everything)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict

import httpx

from config import (APP_DIR, OLLAMA_MODEL, OLLAMA_URL, OPENAI_API_KEY,
                    OPENAI_CHAT_MODEL)
from rag import get_collection

INCIDENTS_PATH = APP_DIR / "incidents.json"
FACTS_CACHE_PATH = APP_DIR / "facts_cache.json"
MAX_TEXT = 2600
# Publication dates in filenames: bare year (..._2021_...) OR compact
# YYYYMMDD (..._20250630_CSR_CS1.pdf - the common PNGRB pattern; the earlier
# pattern required a non-digit after the year and silently missed all of them).
_FILENAME_YMD = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
_FILENAME_YEAR = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROMPT = """You extract structured facts from an Indian process-safety incident document.

Document text (may be partial):
{text}

Extract ONLY what the text explicitly states. Use null for anything absent. NEVER guess or infer dates, numbers, or names. Return JSON:
{{"is_incident_report": true/false,
 "incident_date": "YYYY-MM-DD or YYYY-MM or YYYY or null",
 "location": "city/site or null", "state": "Indian state or null",
 "facility_type": "refinery/pipeline/CGD/terminal/rig/plant type or null",
 "incident_type": "fire|explosion|gas leak|toxic release|injury|fatality|near miss|other or null",
 "substances": ["substances involved"], "equipment": "equipment involved or null",
 "fatalities": number or null, "injuries": number or null,
 "root_cause": "root cause in max 8 words or null",
 "summary": "what happened, max 25 words",
 "confidence": "high|medium|low"}}"""


def _load(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _llm_extract(text: str, backend: str = "ollama") -> dict | None:
    try:
        if backend == "openai":
            payload = {"model": OPENAI_CHAT_MODEL, "temperature": 0,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "user",
                                     "content": PROMPT.format(text=text[:MAX_TEXT])}]}
            with httpx.Client(timeout=httpx.Timeout(15.0, read=120.0)) as client:
                r = client.post("https://api.openai.com/v1/chat/completions",
                                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                                json=payload)
                if r.status_code == 429:          # small text calls; one patient retry
                    time.sleep(25)
                    r = client.post("https://api.openai.com/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                                    json=payload)
                r.raise_for_status()
                return json.loads(r.json()["choices"][0]["message"]["content"])
        payload = {"model": OLLAMA_MODEL, "prompt": PROMPT.format(text=text[:MAX_TEXT]),
                   "stream": False, "format": "json",
                   "options": {"temperature": 0.0, "num_ctx": 4096}}
        with httpx.Client(timeout=httpx.Timeout(10.0, read=240.0)) as client:
            r = client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            return json.loads(r.json().get("response", "{}"))
    except Exception as exc:  # noqa: BLE001
        print(f"    extract failed: {exc}")
        return None


def _year_of(fact: dict, filename: str) -> tuple[int | None, str]:
    """Incident date from the text wins. Otherwise fall back to the year in the
    filename, which is a PUBLICATION date - flagged as such so analytics can
    disclose it rather than pass it off as the incident date."""
    date = str(fact.get("incident_date") or "")
    m = re.match(r"(19|20)\d{2}", date)
    if m:
        return int(m.group(0)), "document_text"
    m = _FILENAME_YMD.search(filename)
    if m:
        return int(m.group(1)), "filename_publication"
    m = _FILENAME_YEAR.search(filename)
    if m:
        return int(m.group(0)), "filename_publication"
    return None, "unknown"


def collect_docs() -> list[dict]:
    """Group indexed chunks back into documents (tier 1-2 content only)."""
    col = get_collection()
    data = col.get(include=["documents", "metadatas"])
    docs: dict[str, dict] = {}
    for text, meta in zip(data["documents"], data["metadatas"]):
        if int(meta.get("tier", 2)) > 2 or meta.get("doc_type") == "form":
            continue
        d = docs.setdefault(meta["doc"], {
            "doc_id": meta["doc"], "title": meta.get("title", ""),
            "category": meta.get("category", ""), "doc_type": meta.get("doc_type", ""),
            "file": meta.get("file", ""), "pages_total": meta.get("pages_total", 0),
            "doc_url": meta.get("doc_url", ""), "source_url": meta.get("source_url", ""),
            "pages": {}})
        page = int(meta.get("page", 0))
        if page <= 3:
            d["pages"].setdefault(page, []).append(text)
    out = []
    for d in docs.values():
        joined = "\n".join("\n".join(v) for _, v in sorted(d["pages"].items()))
        d["text"] = joined[:MAX_TEXT]
        del d["pages"]
        if len(d["text"]) > 150:
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract incident facts -> incidents.json")
    ap.add_argument("--refresh", action="store_true", help="Ignore the facts cache")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--backend", choices=["ollama", "openai"], default="ollama",
                    help="Extraction model: local Ollama or OpenAI "
                         "(~110 small text calls, a few cents, public docs only)")
    args = ap.parse_args()

    if args.backend == "openai" and not OPENAI_API_KEY:
        print("ERROR: --backend openai requires OPENAI_API_KEY (in .env or env).")
        return 1

    docs = collect_docs()
    if args.limit:
        docs = docs[: args.limit]
    cache = {} if args.refresh else _load(FACTS_CACHE_PATH)
    model_name = OPENAI_CHAT_MODEL if args.backend == "openai" else OLLAMA_MODEL
    print(f"{len(docs)} content documents | {len(cache)} cached extractions "
          f"| backend: {args.backend} ({model_name})")

    records, new = [], 0
    t0 = time.time()
    for i, d in enumerate(docs, 1):
        fact = cache.get(d["doc_id"])
        if fact is None:
            fact = _llm_extract(d["text"], backend=args.backend)
            if fact is None:
                continue
            fact["_backend"] = args.backend  # provenance of the extraction
            cache[d["doc_id"]] = fact
            FACTS_CACHE_PATH.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                                        encoding="utf-8")
            new += 1
            print(f"[{i}/{len(docs)}] {d['title'][:55]} -> "
                  f"{fact.get('incident_type')}, {fact.get('incident_date')}")
        if not fact.get("is_incident_report"):
            continue
        year, date_source = _year_of(fact, d["file"])
        records.append({
            **{k: d[k] for k in ("doc_id", "title", "category", "doc_type", "file",
                                 "pages_total", "doc_url", "source_url")},
            "incident_date": fact.get("incident_date"),
            "year": year, "date_source": date_source,
            "location": fact.get("location"), "state": fact.get("state"),
            "facility_type": fact.get("facility_type"),
            "incident_type": (fact.get("incident_type") or "other"),
            "substances": fact.get("substances") or [],
            "equipment": fact.get("equipment"),
            "fatalities": fact.get("fatalities"), "injuries": fact.get("injuries"),
            "root_cause": fact.get("root_cause"),
            "summary": fact.get("summary") or "",
            "confidence": fact.get("confidence") or "low",
        })

    by_year = defaultdict(int)
    for r in records:
        by_year[r["year"] or 0] += 1
    INCIDENTS_PATH.write_text(json.dumps(records, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    print("-" * 60)
    print(f"Done in {time.time() - t0:.0f}s | {len(records)} incident records "
          f"({new} newly extracted) -> {INCIDENTS_PATH}")
    print(f"By year: {dict(sorted(by_year.items()))}")
    print("NOW SPOT-CHECK ~10 records in incidents.json before quoting any number "
          "publicly - you are the validator, not the model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
