"""Ingest crawled safety PDFs into a local ChromaDB index.

Pipeline: walk data/raw_pdfs -> extract text per page (PyMuPDF) -> chunk
-> join provenance from the crawler manifest -> upsert into persistent
ChromaDB using its built-in local embedding function (MiniLM via ONNX;
downloads a small model ~80 MB on first run, then fully offline).

Idempotent: a document whose sha256 is already indexed is skipped, so you
can re-run this after every crawl. Use --rebuild to start fresh.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import chromadb
import fitz  # PyMuPDF
import httpx

from embeddings import embed_texts
from ocr import OCRError, ocr_page
from ocr_cache import OCRCache
from pdf_forensics import diagnose_doc
from triage import TIER_NAMES, TIER_SCRAP, triage_doc, triage_sample_text
from config import (
    OCR_BACKEND,
    OCR_CACHE_DIR,
    OCR_DPI,
    OCR_MAX_PAGES,
    OLLAMA_EMBED_MODEL,
    OLLAMA_MODEL,
    OLLAMA_URL,
    OPENAI_API_KEY,
    CHROMA_DIR,
    COLLECTION_NAME,
    INGEST_REPORT,
    MANIFEST_JSONL,
    RAW_PDF_DIR,
    pretty_category,
)

CHUNK_CHARS = 1100
OVERLAP_CHARS = 150
MIN_PAGE_CHARS = 40
BATCH_SIZE = 96

# Windows consoles often default to cp1252; PDFs contain arbitrary unicode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Manifest join
# --------------------------------------------------------------------------- #

def load_manifest_index(path: Path) -> dict[str, dict]:
    """Map saved_filename -> manifest row (successful downloads only)."""
    index: dict[str, dict] = {}
    if not path.exists():
        log(f"WARNING: manifest not found at {path}; provenance will be limited.")
        return index
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("saved_filename") and not row.get("error"):
                index.setdefault(row["saved_filename"], row)
    return index


# --------------------------------------------------------------------------- #
# PDF helpers
# --------------------------------------------------------------------------- #

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract_pages(path: Path, doc_id: str, ocr_backend: str = "off",
                  ocr_budget: int = 0, ocr_state: dict | None = None,
                  cache: "OCRCache | None" = None, reocr: bool = False
                  ) -> tuple[list[str], set[int]]:
    """Text per page; image-only pages come from the OCR cache when
    available, the OCR backend otherwise.

    Cache reads cost nothing, don't consume the OCR budget, and work even
    with --ocr off - so a --rebuild never repeats an OCR pass. Cached-empty
    pages are honored too (a hopeless scan is not retried every run);
    --reocr bypasses reads to regenerate, e.g. after switching VLM.
    Returns (pages, ocr_pages) with 1-based page numbers whose text came
    from OCR (fresh or cached). ocr_state is the run-wide circuit breaker.
    """
    ocr_state = ocr_state if ocr_state is not None else {}
    ocr_done: set[int] = set()
    budget = ocr_budget
    budget_logged = False
    with fitz.open(path) as doc:
        pages = [page.get_text("text") for page in doc]
        for i, text in enumerate(pages):
            if len(text.strip()) >= MIN_PAGE_CHARS:
                continue
            pno = i + 1

            if cache is not None and not reocr:
                cached = cache.get(doc_id, pno)
                if cached is not None:
                    if len(cached.strip()) >= MIN_PAGE_CHARS:
                        pages[i] = cached
                        ocr_done.add(pno)
                        ocr_state["cache_hits"] = ocr_state.get("cache_hits", 0) + 1
                    continue  # cached (even as empty) - never recompute

            if ocr_backend == "off" or ocr_state.get("disabled"):
                continue
            if budget <= 0:
                if not budget_logged:
                    log(f"    OCR budget ({ocr_budget} pages/doc) exhausted; "
                        f"further scanned pages skipped")
                    budget_logged = True
                continue

            try:
                png = doc[i].get_pixmap(dpi=OCR_DPI).tobytes("png")
                ocr_text = ocr_page(png, ocr_backend)
                budget -= 1
                ocr_state["failures"] = 0
                if cache is not None:
                    cache.put(doc_id, pno, ocr_text, backend=ocr_backend,
                              source_file=path.name)
                if len(ocr_text.strip()) >= MIN_PAGE_CHARS:
                    pages[i] = ocr_text
                    ocr_done.add(pno)
                    ocr_state["pages_done"] = ocr_state.get("pages_done", 0) + 1
                    log(f"    OCR {path.name[:34]} p.{pno}/{len(pages)}: "
                        f"{len(ocr_text)} chars via {ocr_backend}")
                else:
                    log(f"    OCR {path.name[:34]} p.{pno}/{len(pages)}: "
                        f"no usable text (cached as empty)")
            except (OCRError, Exception) as exc:  # noqa: BLE001
                ocr_state["failures"] = ocr_state.get("failures", 0) + 1
                log(f"    OCR {path.name[:34]} p.{pno} failed: {exc}")
                if ocr_state["failures"] >= 3:
                    ocr_state["disabled"] = True
                    log("    OCR disabled for the rest of this run "
                        "(3 consecutive failures).")
    return pages, ocr_done


_TITLE_BAD = re.compile(
    r"provided for information|disclaimer|for internal use|confidential|"
    r"page \d+|www\.|https?:|@|^date\b|^ref[.:# ]|^no[.:# ]|^tel[.:# ]|^fax\b",
    re.IGNORECASE,
)
_TITLE_GOOD = re.compile(
    r"case stud|incident|accident|investigat|report|guideline|standard|"
    r"format|safety|explosion|fire|leak|audit", re.IGNORECASE,
)


def guess_title(pages: list[str], fallback: str) -> tuple[str, bool]:
    """Best-scoring heading-like line from page 1. Returns (title, confident).

    The old 'first plausible line' heuristic promoted disclaimers and form
    boilerplate to document titles, which poisoned both the sources panel
    and the LLM reranker. This scores candidates instead.
    """
    best, best_score = None, -99.0
    if pages:
        lines = [" ".join(l.split()) for l in pages[0].splitlines()]
        lines = [l for l in lines if l][:20]
        for idx, line in enumerate(lines):
            if len(line) < 8 or sum(c.isalpha() for c in line) < 5:
                continue
            words = line.split()
            score = 0.0
            if _TITLE_BAD.search(line):
                score -= 4
            if _TITLE_GOOD.search(line):
                score += 1.5
            caps = sum(1 for w in words if w[:1].isupper())
            if line.isupper() or caps / max(len(words), 1) > 0.6:
                score += 2       # heading-like casing
            if idx < 5:
                score += 1       # near top of page
            if len(line) > 100:
                score -= 1.5     # probably a sentence, not a title
            if line.rstrip().lower().endswith(
                    (" in", " of", " to", " the", " and", " for", " is", " be", " a")):
                score -= 2       # truncated prose
            if line.endswith((".", ";", ",")):
                score -= 0.5
            if score > best_score:
                best, best_score = line, score
    if best and best_score >= 1.5:
        return best[:120], True
    if best and best_score > -1:
        return best[:120], False
    return fallback, False


def llm_title(first_page: str) -> str | None:
    """One small LLM call to name a document whose heading heuristic failed.

    Guarded: with too little page-1 text the model INVENTS a title (seen in
    the wild: an NDMA case-studies compendium named "Company Policy and
    Procedure Manual"). Below the threshold we return None and the caller
    falls back to the filename - a boring true title beats a confident lie.
    """
    if len(first_page.strip()) < 200:
        return None
    try:
        payload = {"model": OLLAMA_MODEL, "stream": False, "format": "json",
                   "options": {"temperature": 0.0, "num_ctx": 2048},
                   "prompt": ("First page of a document:\n" + first_page[:900] +
                              '\n\nGive it a short descriptive title (max 12 words) '
                              'using ONLY words and subject matter present in this '
                              'text. Do not invent a title. If the text is too '
                              'fragmentary to title honestly, return an empty title. '
                              'Return JSON: {"title": "..."}')}
        with httpx.Client(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            r = client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            title = str(json.loads(r.json().get("response", "{}")).get("title", "")).strip()
            return title[:120] or None
    except Exception:  # noqa: BLE001
        return None


_FORM_TITLE = re.compile(r"\b(format|form|pro\s*forma|proforma|performa|annexure)\b", re.IGNORECASE)
_DISCLAIMER_PAGE = re.compile(r"provided for information purpose", re.IGNORECASE)


def classify_doc_type(category: str, title: str, first_page: str) -> str:
    """case_study | investigation | guideline | form | statistics | other.

    Forms and templates are real documents but must not outrank incident
    content for 'what happened / why' questions - retrieval demotes them.
    """
    c = category.lower()
    # A filled investigation report titled "INCIDENT REPORTING FORMAT" is a
    # report, not a blank form - never tag investigation-category docs as forms.
    if ("analysis" not in c and "investigation" not in c) and (
            _FORM_TITLE.search(title) or re.search(
            r"reporting form|format for|quarterly incident report",
            (title + " " + first_page[:300]), re.IGNORECASE)):
        return "form"
    if "case" in c or "compendium" in c:
        return "case_study"
    if "analysis" in c or "investigation" in c or "incident-analysis" in c:
        return "investigation"
    if "guideline" in c:
        return "guideline"
    if "statistic" in c:
        return "statistics"
    return "other"


def is_boilerplate_page(text: str) -> bool:
    """Skip pages that ARE a disclaimer - not pages that merely CARRY one.

    Bug history: matching the disclaimer anywhere in the header silently
    dropped 11 real OISD case studies whose every page has a disclaimer
    header line. A page is boilerplate only if the disclaimer is present
    AND there is nearly nothing else on the page.
    """
    return bool(_DISCLAIMER_PAGE.search(text[:400])) and len(text.strip()) < 600


def chunk_text(text: str) -> list[str]:
    """Sliding window over one page, breaking on whitespace."""
    text = " ".join(text.split())
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            space = text.rfind(" ", start + CHUNK_CHARS - 200, end)
            if space > start:
                end = space
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return [c for c in chunks if c]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Index safety PDFs into ChromaDB")
    ap.add_argument("--rebuild", action="store_true", help="Delete and recreate the collection")
    ap.add_argument("--limit", type=int, default=0, help="Only process first N PDFs (smoke test)")
    ap.add_argument("--ocr", choices=["off", "ollama", "openai"], default=OCR_BACKEND,
                    help="OCR fallback for scanned/image-only pages "
                         "(default from OCR_BACKEND env; 'ollama' = local VLM, "
                         "'openai' = OpenAI vision API)")
    ap.add_argument("--ocr-max-pages", type=int, default=OCR_MAX_PAGES,
                    help="Max pages OCR'd per document")
    ap.add_argument("--reocr", action="store_true",
                    help="Ignore the OCR cache and regenerate (e.g. after "
                         "switching to a better VLM)")
    ap.add_argument("--triage-only", action="store_true",
                    help="Run pass 1 only: tier every new document and report, "
                         "no OCR/chunk/embed. Cheap preview before committing compute.")
    ap.add_argument("--include-scrap", action="store_true",
                    help="Index tier-4 scrap documents anyway (tagged + demoted) "
                         "instead of quarantining them")
    ap.add_argument("--no-llm-triage", action="store_true",
                    help="Heuristic-only triage (no LLM refinement)")
    ap.add_argument("--no-llm-titles", action="store_true",
                    help="Skip the LLM fallback for documents whose title "
                         "heuristic is low-confidence (faster, worse titles)")
    args = ap.parse_args()

    if args.ocr == "openai" and not OPENAI_API_KEY:
        log("ERROR: --ocr openai requires the OPENAI_API_KEY environment variable.")
        return 1

    if not RAW_PDF_DIR.exists():
        log(f"ERROR: corpus directory not found: {RAW_PDF_DIR}")
        return 1

    # Fail fast if the embedding backend isn't ready (Ollama + embed model).
    try:
        embed_texts(["healthcheck"], kind="document")
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: embedding backend not ready: {exc}")
        log(f"Fix: make sure Ollama is running, then:  ollama pull {OLLAMA_EMBED_MODEL}")
        return 1

    t0 = time.time()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if args.rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            log("Deleted existing collection (--rebuild).")
        except Exception:
            pass
    col = client.get_or_create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

    manifest = load_manifest_index(MANIFEST_JSONL)
    pdfs = sorted(RAW_PDF_DIR.rglob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    log(f"Corpus: {len(pdfs)} PDFs | manifest rows joined: {len(manifest)}")
    log(f"Index:  {CHROMA_DIR} (collection '{COLLECTION_NAME}', {col.count()} existing chunks)")
    ocr_cache = OCRCache(OCR_CACHE_DIR)
    cstats = ocr_cache.stats()
    if args.ocr != "off" or cstats["pages"]:
        log(f"OCR:    backend={args.ocr} (max {args.ocr_max_pages} pages/doc) | "
            f"cache: {cstats['pages']} pages from {cstats['docs']} docs"
            + (" [--reocr: cache reads disabled]" if args.reocr else ""))

    ocr_state: dict = {"failures": 0, "disabled": False, "pages_done": 0,
                       "cache_hits": 0}
    report = {"indexed": [], "skipped_existing": [], "empty_or_scanned": [], "failed": [],
              "quarantined": [], "tiers": {}, "categories": {}, "doc_types": {},
              "boilerplate_pages_skipped": 0, "llm_titles_used": 0,
              "ocr": {"backend": args.ocr, "pages_done": 0}}

    # ---- PASS 1: cheap triage of every new document (no chunk/embed yet) ------
    work: list[dict] = []
    for pdf in pdfs:
        rel = pdf.relative_to(RAW_PDF_DIR)
        category = pretty_category(rel.parts[0]) if len(rel.parts) > 1 else "Uncategorised"
        try:
            sha = sha256_of(pdf)
            doc_id = sha[:12]
            existing = col.get(where={"doc": doc_id}, limit=1)
            if existing["ids"]:
                report["skipped_existing"].append(str(rel))
                continue
            sample = triage_sample_text(pdf, doc_id, args.ocr, ocr_cache)
            tri = triage_doc(doc_id, pdf.name, category, sample,
                             use_llm=not args.no_llm_triage)
            report["tiers"][TIER_NAMES[tri["tier"]]] = \
                report["tiers"].get(TIER_NAMES[tri["tier"]], 0) + 1
            if tri["tier"] >= TIER_SCRAP and not args.include_scrap:
                report["quarantined"].append({"file": str(rel), "kind": tri["kind"],
                                              "reason": tri["reason"], "by": tri["by"]})
                log(f"QUARANTINE ({tri['by']}): {rel} - {tri['reason']}")
                continue
            work.append({"pdf": pdf, "rel": rel, "category": category,
                         "doc_id": doc_id, "tri": tri})
        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"file": str(rel), "error": str(exc)})
            log(f"FAILED (triage) {rel}: {exc}")

    work.sort(key=lambda w: w["tri"]["tier"])  # most valuable documents first
    log(f"Triage: {len(work)} to process (by tier: {report['tiers']}), "
        f"{len(report['quarantined'])} quarantined, "
        f"{len(report['skipped_existing'])} already indexed")
    if args.triage_only:
        INGEST_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        log(f"--triage-only: no processing done. Full verdicts in {INGEST_REPORT} "
            f"and triage_cache.json; override via triage_overrides.json.")
        return 0

    # ---- PASS 2: full processing, priority order, tiered OCR budgets ----------
    for i, w in enumerate(work, 1):
        pdf, rel, category = w["pdf"], w["rel"], w["category"]
        doc_id, tri = w["doc_id"], w["tri"]
        try:
            # Tier 1-2 earn the full OCR budget; tier 3 reference gets a taste.
            ocr_budget = args.ocr_max_pages if tri["tier"] <= 2 \
                else min(10, args.ocr_max_pages)
            pages, ocr_pages = extract_pages(pdf, doc_id, args.ocr,
                                             ocr_budget, ocr_state,
                                             cache=ocr_cache, reocr=args.reocr)
            row = manifest.get(pdf.name, {})
            title, confident = guess_title(pages, row.get("original_filename") or pdf.stem)
            if not confident and not args.no_llm_titles and pages:
                better = llm_title(pages[0])
                if better:
                    title = better
                    report["llm_titles_used"] += 1
            doc_type = classify_doc_type(category, title, pages[0] if pages else "")

            ids, docs, metas = [], [], []
            for pno, page_text in enumerate(pages, 1):
                if len(page_text.strip()) < MIN_PAGE_CHARS:
                    continue
                if is_boilerplate_page(page_text):
                    report["boilerplate_pages_skipped"] += 1
                    continue
                for ci, chunk in enumerate(chunk_text(page_text)):
                    ids.append(f"{doc_id}_p{pno}_c{ci}")
                    docs.append(chunk)
                    metas.append({
                        "doc": doc_id,
                        "title": title,
                        "category": category,
                        "file": pdf.name,
                        "page": pno,
                        "pages_total": len(pages),
                        "source_name": row.get("source_name") or "",
                        "source_url": row.get("source_url") or "",
                        "doc_url": row.get("final_url_after_redirects")
                                   or row.get("resolved_url") or "",
                        "ocr": ("cached" if args.ocr == "off" else args.ocr)
                               if pno in ocr_pages else "",
                        "doc_type": doc_type,
                        "tier": tri["tier"],
                    })

            if not ids:
                # Don't guess "scanned?" - measure why extraction yielded nothing.
                forensic = diagnose_doc(pdf, sample_pages=5)
                report["empty_or_scanned"].append({
                    "file": str(rel), "verdict": forensic["verdict"],
                    "reason": forensic["reason"]})
                hint = "" if args.ocr != "off" else " (retry with --ocr ollama or --ocr openai)"
                log(f"[{i}/{len(work)}] SKIP [{forensic['verdict']}] {rel} - "
                    f"{forensic['reason']}{hint}")
                continue

            for b in range(0, len(ids), BATCH_SIZE):
                batch_docs = docs[b:b + BATCH_SIZE]
                vectors = embed_texts(batch_docs, kind="document")
                col.upsert(ids=ids[b:b + BATCH_SIZE],
                           documents=batch_docs,
                           embeddings=vectors,
                           metadatas=metas[b:b + BATCH_SIZE])

            report["indexed"].append({"file": str(rel), "title": title,
                                      "doc_type": doc_type, "tier": tri["tier"],
                                      "pages": len(pages), "chunks": len(ids)})
            report["categories"][category] = report["categories"].get(category, 0) + 1
            report["doc_types"][doc_type] = report["doc_types"].get(doc_type, 0) + 1
            log(f"[{i}/{len(work)}] T{tri['tier']} {rel} -> {len(ids)} chunks "
                f"[{doc_type}] ({title[:50]})")

        except Exception as exc:  # noqa: BLE001
            report["failed"].append({"file": str(rel), "error": str(exc)})
            log(f"[{i}/{len(work)}] FAILED {rel}: {exc}")

    report["ocr"]["pages_done"] = ocr_state["pages_done"]
    report["ocr"]["cache_hits"] = ocr_state["cache_hits"]
    report["ocr"]["disabled_by_failures"] = bool(ocr_state["disabled"])
    report["total_chunks_in_collection"] = col.count()
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    INGEST_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    log("-" * 60)
    log(f"Done in {time.time() - t0:.1f}s | indexed {len(report['indexed'])} new, "
        f"quarantined {len(report['quarantined'])} scrap, "
        f"skipped {len(report['skipped_existing'])} existing, "
        f"{len(report['empty_or_scanned'])} empty/scanned, {len(report['failed'])} failed")
    log(f"Collection now holds {report['total_chunks_in_collection']} chunks.")
    log(f"Report: {INGEST_REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
