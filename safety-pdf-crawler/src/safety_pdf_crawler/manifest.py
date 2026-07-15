"""Manifest of downloaded PDFs (one row per (source, file) tuple)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MANIFEST_FIELDS: list[str] = [
    "id",
    "source_name",
    "source_url",
    "discovered_link_text",
    "discovered_url",
    "resolved_url",
    "final_url_after_redirects",
    "domain",
    "local_file_path",
    "original_filename",
    "saved_filename",
    "content_type",
    "status_code",
    "file_size_bytes",
    "sha256",
    "downloaded_at_utc",
    "crawl_depth",
    "error",
]


class ManifestEntry(BaseModel):
    """A single manifest row."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_name: str
    source_url: str
    discovered_link_text: str | None = None
    discovered_url: str
    resolved_url: str
    final_url_after_redirects: str | None = None
    domain: str = ""
    local_file_path: str | None = None
    original_filename: str | None = None
    saved_filename: str | None = None
    content_type: str | None = None
    status_code: int | None = None
    file_size_bytes: int | None = None
    sha256: str | None = None
    downloaded_at_utc: str | None = None
    crawl_depth: int = 0
    error: str | None = None

    def to_row(self) -> dict:
        return {k: getattr(self, k) for k in MANIFEST_FIELDS}

    @staticmethod
    def now_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Manifest:
    """In-memory + on-disk manifest with cheap dedup indexes.

    The JSONL file is appended to as entries land (so a long crawl is durable);
    the CSV is written in one pass at the end via :meth:`save_csv`.

    Dedup indexes are populated only for *successful* downloads (i.e. rows with a
    ``local_file_path`` and no ``error``); the first such row "wins" — subsequent
    rows for the same final URL or content hash are kept in :attr:`entries` so
    that source provenance is preserved, but they reference the existing file
    rather than creating a duplicate.
    """

    def __init__(self, manifest_dir: Path):
        self.manifest_dir = Path(manifest_dir)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.manifest_dir / "pdf_manifest.csv"
        self.jsonl_path = self.manifest_dir / "pdf_manifest.jsonl"
        self.entries: list[ManifestEntry] = []
        self._url_index: dict[str, ManifestEntry] = {}
        self._hash_index: dict[str, ManifestEntry] = {}

    # ----- I/O ---------------------------------------------------------------

    def load(self) -> None:
        if not self.jsonl_path.exists():
            return
        with self.jsonl_path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = ManifestEntry(**json.loads(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping malformed manifest line: %s", exc)
                    continue
                self._index(entry, append_to_disk=False)

    def add(self, entry: ManifestEntry) -> None:
        self._index(entry, append_to_disk=True)

    def save_csv(self) -> None:
        rows = [e.to_row() for e in self.entries]
        df = pd.DataFrame(rows, columns=MANIFEST_FIELDS)
        df.to_csv(self.csv_path, index=False)

    # ----- queries -----------------------------------------------------------

    def get_by_final_url(self, url: str) -> ManifestEntry | None:
        return self._url_index.get(url)

    def get_by_hash(self, sha256: str) -> ManifestEntry | None:
        return self._hash_index.get(sha256)

    def has_successful_download_for_url(self, url: str) -> bool:
        entry = self._url_index.get(url)
        return entry is not None and bool(entry.local_file_path) and not entry.error

    def __len__(self) -> int:
        return len(self.entries)

    # ----- internals ---------------------------------------------------------

    def _index(self, entry: ManifestEntry, *, append_to_disk: bool) -> None:
        self.entries.append(entry)
        if entry.local_file_path and not entry.error:
            if entry.final_url_after_redirects:
                self._url_index.setdefault(entry.final_url_after_redirects, entry)
            if entry.resolved_url:
                self._url_index.setdefault(entry.resolved_url, entry)
            if entry.sha256:
                self._hash_index.setdefault(entry.sha256, entry)
        if append_to_disk:
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_row(), ensure_ascii=False) + "\n")
