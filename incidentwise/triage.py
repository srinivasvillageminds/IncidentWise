"""Corpus triage - tier every document BEFORE spending compute on it.

Tiers (lower = more valuable, processed first):
  1  core_knowledge   incident/accident case studies, investigation reports
  2  guidance         guidelines, standards, safety alerts, circulars
  3  reference        statistics, annual reports, forms, admin lists - indexed
                      but OCR-capped and demoted in retrieval
  4  scrap            organograms, directories, tenders/EOIs, recruitment,
                      awards, press releases - QUARANTINED (not indexed)

Design rules, stated bluntly:
- Cheap signals first (filename, category, first-page text); one small LLM
  call only when heuristics are unsure. Never per-page LLM calls.
- Scanned docs get a page-1-only OCR for triage (through the OCR cache, so
  the full pass later reuses it) - the doc earns its full OCR by what page 1 says.
- Quarantine is conservative and reversible: the LLM alone cannot condemn a
  document to scrap without a heuristic scrap signal; everything quarantined
  is listed in ingest_report.json; triage_overrides.json overrides any verdict.
  Losing one real incident report costs more than indexing ten org charts.
- Verdicts are cached by content hash in triage_cache.json.
"""
from __future__ import annotations

import json
import re

import fitz  # PyMuPDF
import httpx

from config import APP_DIR, OCR_DPI, OLLAMA_MODEL, OLLAMA_URL
from ocr import OCRError, ocr_page
from ocr_cache import OCRCache

TIER_CORE, TIER_GUIDANCE, TIER_REFERENCE, TIER_SCRAP = 1, 2, 3, 4
TIER_NAMES = {1: "core_knowledge", 2: "guidance", 3: "reference", 4: "scrap"}

TRIAGE_CACHE_PATH = APP_DIR / "triage_cache.json"
OVERRIDES_PATH = APP_DIR / "triage_overrides.json"   # {"filename substring": tier}

_SCRAP_FILENAME = re.compile(
    r"organogram|organi[sz]ation.?chart|directory|work.?allocation|"
    r"expression.?of.?interest|\beoi\b|tender|recruitment|vacancy|"
    r"award|citation.?booklet|press.?release|holiday|advertis|newsletter|"
    r"magazine|brochure|calendar", re.IGNORECASE)
_SCRAP_TEXT = re.compile(
    r"applications?\s+are\s+invited|tender|bid\s+document|expression\s+of\s+interest|"
    r"organi[sz]ation\s+chart|telephone\s+directory|list\s+of\s+officers|"
    r"work\s+allocation|press\s+release|award\s+ceremony", re.IGNORECASE)
_CORE_TEXT = re.compile(
    r"incident\s+(occurred|report|investigation)|accident|root\s+cause|"
    r"case\s+stud|lessons?\s+learn|near\s+miss|fatalit|explosion|fire\s+broke",
    re.IGNORECASE)
_GUIDANCE_TEXT = re.compile(
    r"guideline|standard|shall\s+be|procedure|safety\s+alert|circular|"
    r"code\s+of\s+practice", re.IGNORECASE)

_cache: dict | None = None


def _load_json(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _cache_get(doc_id: str) -> dict | None:
    global _cache
    if _cache is None:
        _cache = _load_json(TRIAGE_CACHE_PATH)
    return _cache.get(doc_id)


def _cache_put(doc_id: str, verdict: dict) -> None:
    global _cache
    if _cache is None:
        _cache = _load_json(TRIAGE_CACHE_PATH)
    _cache[doc_id] = verdict
    TRIAGE_CACHE_PATH.write_text(json.dumps(_cache, indent=1, ensure_ascii=False),
                                 encoding="utf-8")


def triage_sample_text(pdf_path, doc_id: str, ocr_backend: str,
                       cache: OCRCache) -> str:
    """First-page text (plus a mid-page taste) as cheaply as possible.
    Scanned docs get a page-1-only OCR through the cache."""
    with fitz.open(pdf_path) as doc:
        first = doc[0].get_text("text") if len(doc) else ""
        middle = ""
        if len(doc) > 5:
            middle = doc[len(doc) // 2].get_text("text")[:400]
        if len(first.strip()) < 40 and ocr_backend != "off":
            cached = cache.get(doc_id, 1)
            if cached is not None:
                first = cached
            else:
                try:
                    png = doc[0].get_pixmap(dpi=OCR_DPI).tobytes("png")
                    first = ocr_page(png, ocr_backend)
                    cache.put(doc_id, 1, first, backend=ocr_backend,
                              source_file=str(getattr(pdf_path, "name", pdf_path)))
                except (OCRError, Exception):  # noqa: BLE001
                    first = ""
    return (first[:1200] + ("\n---\n" + middle if middle else "")).strip()


def _heuristic(filename: str, category: str, sample: str) -> tuple[int, str, str, bool]:
    """(tier, kind, reason, confident)."""
    if _SCRAP_FILENAME.search(filename):
        return TIER_SCRAP, "admin_scrap", f"filename pattern: {filename}", True
    scrap_sig = bool(_SCRAP_TEXT.search(sample))
    core_sig = len(_CORE_TEXT.findall(sample))
    guide_sig = len(_GUIDANCE_TEXT.findall(sample))
    if scrap_sig and core_sig == 0:
        return TIER_SCRAP, "admin_scrap", "scrap keywords on page 1, no incident signal", False
    if core_sig >= 2:
        return TIER_CORE, "incident_content", f"{core_sig} incident signals on page 1", True
    if guide_sig >= 2 and core_sig == 0:
        return TIER_GUIDANCE, "guidance", "guideline/standard language", False
    c = category.lower()
    if "case" in c or "compendium" in c or "analysis" in c or "investigation" in c:
        return TIER_CORE, "incident_content", "category default", False
    if "guideline" in c:
        return TIER_GUIDANCE, "guidance", "category default", False
    if "statistic" in c:
        return TIER_REFERENCE, "statistics", "category default", False
    return TIER_REFERENCE, "reference", "no strong signal", False


def _llm_triage(filename: str, category: str, sample: str) -> dict | None:
    if len(sample.strip()) < 40:
        return None
    try:
        prompt = (
            "Classify this document from an Indian process-safety corpus.\n"
            f"Filename: {filename}\nCrawl category: {category}\n"
            f"Page-1 text:\n{sample[:1000]}\n\n"
            "Tiers: 1=incident/accident case study or investigation report; "
            "2=safety guideline/standard/alert; 3=reference (statistics, annual "
            "report, forms, technical lists); 4=administrative scrap (org chart, "
            "directory, tender/EOI, recruitment, awards, press release).\n"
            "If unsure between 3 and 4, choose 3. Never give 4 to anything "
            "mentioning an incident, accident or investigation.\n"
            'Return JSON: {"tier": 1-4, "kind": "short label", "reason": "one line"}')
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                   "format": "json", "options": {"temperature": 0.0, "num_ctx": 4096}}
        with httpx.Client(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            r = client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            data = json.loads(r.json().get("response", "{}"))
        tier = int(data.get("tier", 0))
        if tier not in (1, 2, 3, 4):
            return None
        return {"tier": tier, "kind": str(data.get("kind", ""))[:40],
                "reason": str(data.get("reason", ""))[:160]}
    except Exception:  # noqa: BLE001
        return None


def triage_doc(doc_id: str, filename: str, category: str, sample: str,
               use_llm: bool = True) -> dict:
    """Verdict: {tier, kind, reason, by}. Overrides > cache > heuristic (+LLM)."""
    overrides = _load_json(OVERRIDES_PATH)
    for pattern, tier in overrides.items():
        if pattern.lower() in filename.lower():
            return {"tier": int(tier), "kind": "override",
                    "reason": f"triage_overrides.json: '{pattern}'", "by": "override"}

    cached = _cache_get(doc_id)
    if cached:
        return cached

    tier, kind, reason, confident = _heuristic(filename, category, sample)
    verdict = {"tier": tier, "kind": kind, "reason": reason, "by": "heuristic"}

    if not confident and use_llm:
        llm = _llm_triage(filename, category, sample)
        if llm:
            # The LLM alone cannot quarantine: tier 4 needs a heuristic scrap signal.
            if llm["tier"] == TIER_SCRAP and tier != TIER_SCRAP:
                llm["tier"] = TIER_REFERENCE
                llm["reason"] += " (clamped: no heuristic scrap signal)"
            verdict = {**llm, "by": "llm"}

    _cache_put(doc_id, verdict)
    return verdict
