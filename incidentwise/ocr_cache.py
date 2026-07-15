"""Content-addressed cache for OCR output.

OCR is the most expensive step in the pipeline (minutes/page on a local
VLM, money/page on OpenAI). Results are deterministic for a given page,
so they are cached once and reused forever:

    ocr_cache/{sha256[:12]}/p0007.txt    - OCR text for page 7 (may be empty)
    ocr_cache/{sha256[:12]}/meta.json    - backend/model/timestamp per page

Keyed by content hash, not path - renamed or re-crawled files still hit.
Empty results are cached too, so hopeless pages are not re-OCR'd every run.
Cache reads work even with --ocr off (an earlier OCR pass is never lost on
--rebuild). Use --reocr to deliberately regenerate, e.g. after switching
to a better VLM. Plain-text files: eyeball them to judge OCR quality.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config import OLLAMA_VLM_MODEL, OPENAI_VLM_MODEL


class OCRCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _doc_dir(self, doc_id: str) -> Path:
        return self.root / doc_id

    def get(self, doc_id: str, page: int) -> str | None:
        """Cached text for a page, '' if cached-as-empty, None if not cached."""
        p = self._doc_dir(doc_id) / f"p{page:04d}.txt"
        if not p.exists():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None

    def put(self, doc_id: str, page: int, text: str, backend: str,
            source_file: str = "") -> None:
        d = self._doc_dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"p{page:04d}.txt").write_text(text, encoding="utf-8")

        meta_path = d / "meta.json"
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        model = {"ollama": OLLAMA_VLM_MODEL, "openai": OPENAI_VLM_MODEL}.get(backend, "")
        meta.setdefault("source_file", source_file)
        meta.setdefault("pages", {})[str(page)] = {
            "backend": backend, "model": model, "chars": len(text),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def stats(self) -> dict:
        docs = pages = 0
        if self.root.exists():
            for d in self.root.iterdir():
                if d.is_dir():
                    n = len(list(d.glob("p*.txt")))
                    if n:
                        docs += 1
                        pages += n
        return {"docs": docs, "pages": pages}
