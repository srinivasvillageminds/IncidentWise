"""Deterministic PDF content forensics - know WHY a page yields no text.

"No extractable text" is a symptom with four different diseases, and the
right response differs:

  image_only_scan   pages are pictures                       -> OCR
  fonts_no_unicode  real text objects, but the font lacks a  -> OCR (pixels
                    usable ToUnicode/CID map so extraction      don't lie)
                    returns nothing; looks like perfect
                    digital text in a viewer
  vector_outlines   text drawn as curves (design-tool export) -> OCR
  encrypted/blank   permissions or genuinely empty            -> decrypt/skip

Everything here is measured, not guessed: character counts, image count and
area coverage, embedded fonts, vector drawing counts, encryption flags.

CLI:
  python pdf_forensics.py <file-or-dir> [...]   diagnose specific PDFs
  python pdf_forensics.py --empty-only          diagnose only the docs the
                                                last ingest skipped
Writes forensics_report.json next to this file.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

from config import APP_DIR, RAW_PDF_DIR

FORENSICS_REPORT = APP_DIR / "forensics_report.json"
INGEST_REPORT = APP_DIR / "ingest_report.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def diagnose_page(page) -> dict:
    text = page.get_text("text").strip()
    images = page.get_images(full=True)
    rect = page.rect
    page_area = max(rect.width * rect.height, 1.0)
    img_area = 0.0
    for img in images:
        try:
            for r in page.get_image_rects(img[0]):
                img_area += abs(r.width * r.height)
        except Exception:  # noqa: BLE001
            pass
    try:
        fonts = len(page.get_fonts())
    except Exception:  # noqa: BLE001
        fonts = 0
    try:
        drawings = len(page.get_drawings())
    except Exception:  # noqa: BLE001
        drawings = 0
    return {"chars": len(text), "images": len(images),
            "image_coverage": round(min(img_area / page_area, 1.0), 2),
            "fonts": fonts, "drawings": drawings}


def page_verdict(d: dict) -> str:
    if d["chars"] >= 40:
        return "digital_text"
    if d["images"] and d["image_coverage"] >= 0.45:
        return "image_only_scan"
    if d["fonts"] > 0:
        return "fonts_no_unicode"
    if d["drawings"] > 40:
        return "vector_outlines"
    if d["images"]:
        return "image_partial"
    return "blank"


_EXPLAIN = {
    "digital_text": "normal extractable text",
    "image_only_scan": "pages are images (true scan) - OCR is correct",
    "fonts_no_unicode": "embedded fonts but no usable text mapping - looks "
                        "digital in a viewer, extracts as nothing; OCR reads "
                        "the rendered pixels instead",
    "vector_outlines": "text drawn as vector curves - OCR the render",
    "image_partial": "some images, low coverage, no text",
    "blank": "no text, images, or fonts detected",
    "encrypted": "password-protected - OCR/extraction blocked until decrypted",
}


def diagnose_doc(path: Path, sample_pages: int = 10) -> dict:
    """Doc-level verdict from evenly sampled pages. Deterministic."""
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass:
                return {"file": path.name, "pages": doc.page_count,
                        "verdict": "encrypted", "reason": _EXPLAIN["encrypted"],
                        "page_verdicts": {}}
            n = doc.page_count
            idxs = list(range(n)) if n <= sample_pages else \
                sorted({round(i * (n - 1) / (sample_pages - 1)) for i in range(sample_pages)})
            verdicts = Counter()
            detail = []
            for i in idxs:
                d = diagnose_page(doc[i])
                v = page_verdict(d)
                verdicts[v] += 1
                detail.append({"page": i + 1, "verdict": v, **d})
            # Dominant non-digital verdict explains an "empty" doc best.
            non_digital = {k: c for k, c in verdicts.items() if k != "digital_text"}
            top = max(non_digital, key=non_digital.get) if non_digital else "digital_text"
            if verdicts.get("digital_text", 0) == len(idxs):
                top = "digital_text"
            return {"file": path.name, "pages": n, "verdict": top,
                    "reason": _EXPLAIN[top],
                    "page_verdicts": dict(verdicts), "sampled": detail}
    except Exception as exc:  # noqa: BLE001
        return {"file": path.name, "pages": 0, "verdict": "corrupt",
                "reason": f"cannot open/parse: {exc}", "page_verdicts": {}}


def _targets(args: list[str]) -> list[Path]:
    if "--empty-only" in args:
        if not INGEST_REPORT.exists():
            print("No ingest_report.json - run ingest first or pass paths.")
            return []
        rep = json.loads(INGEST_REPORT.read_text(encoding="utf-8"))
        out = []
        for item in rep.get("empty_or_scanned", []):
            rel = item["file"] if isinstance(item, dict) else item
            p = RAW_PDF_DIR / rel
            if p.exists():
                out.append(p)
        return out
    paths: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths += sorted(p.rglob("*.pdf"))
        elif "*" in a:
            paths += sorted(Path(".").glob(a))
        elif p.exists():
            paths.append(p)
    return paths


def main() -> int:
    args = sys.argv[1:]
    targets = _targets(args) if args else sorted(RAW_PDF_DIR.rglob("*.pdf"))
    if not targets:
        print("Nothing to diagnose.")
        return 1
    print(f"Diagnosing {len(targets)} PDF(s)...\n")
    results = []
    for p in targets:
        r = diagnose_doc(p)
        results.append(r)
        pv = ", ".join(f"{k}:{c}" for k, c in r["page_verdicts"].items())
        print(f"{r['verdict']:>18}  {p.name}  ({r['pages']}p; {pv})")
        print(f"{'':>18}  -> {r['reason']}")
    FORENSICS_REPORT.write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                encoding="utf-8")
    tally = Counter(r["verdict"] for r in results)
    print(f"\nSummary: {dict(tally)}")
    print(f"Report: {FORENSICS_REPORT}")
    if tally.get("fonts_no_unicode") or tally.get("vector_outlines"):
        print("Note: fonts_no_unicode / vector_outlines are NOT scans, but OCR "
              "is still the correct fix - the VLM reads the rendered page, "
              "which is immune to font-encoding pathology.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
