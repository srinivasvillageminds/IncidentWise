"""Polite PDF downloader with retries, dedup, and manifest integration."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .config import CrawlerConfig
from .manifest import Manifest, ManifestEntry
from .utils import (
    compute_sha256,
    get_domain,
    is_pdf_url,
    jittered,
    safe_filename,
    short_url_hash,
    slugify_source,
)

logger = logging.getLogger(__name__)


class Downloader:
    """Streams PDFs to disk via httpx with retries and content-type / magic checks."""

    _PDF_MAGIC = b"%PDF-"
    _CD_FILENAME_RE = re.compile(
        r"""filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?""",
        re.IGNORECASE,
    )

    def __init__(self, config: CrawlerConfig, manifest: Manifest):
        self.config = config
        self.manifest = manifest
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "Downloader":
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "application/pdf,*/*;q=0.8",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -------------------------------------------------------------------------

    async def download(
        self,
        *,
        source_name: str,
        source_url: str,
        discovered_url: str,
        resolved_url: str,
        discovered_link_text: str | None,
        crawl_depth: int,
    ) -> ManifestEntry:
        """Download (or cross-reference) a single PDF and append a manifest row."""
        # Cheap dedup by URL — avoids both the GET and any browser work.
        if not self.config.force and self.manifest.has_successful_download_for_url(resolved_url):
            existing = self.manifest.get_by_final_url(resolved_url)
            assert existing is not None
            logger.info("Cross-referencing existing download for %s", resolved_url)
            return self._add_cross_reference(
                existing=existing,
                source_name=source_name,
                source_url=source_url,
                discovered_url=discovered_url,
                resolved_url=resolved_url,
                discovered_link_text=discovered_link_text,
                crawl_depth=crawl_depth,
            )

        if self.config.dry_run:
            entry = ManifestEntry(
                source_name=source_name,
                source_url=source_url,
                discovered_link_text=discovered_link_text,
                discovered_url=discovered_url,
                resolved_url=resolved_url,
                domain=get_domain(resolved_url),
                crawl_depth=crawl_depth,
                error="dry-run",
            )
            self.manifest.add(entry)
            return entry

        assert self._client is not None, "Downloader must be used as an async context manager"

        tmp_dir = self.config.output_dir / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # State carried across retry attempts.
        last_error: str | None = None
        final_url: str = resolved_url
        status_code: int | None = None
        content_type: str | None = None
        cd_filename: str | None = None
        file_size: int | None = None
        sha256: str | None = None
        tmp_path: Path | None = None

        backoff = 1.0
        for attempt in range(1, self.config.retries + 1):
            fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".part")
            os.close(fd)
            tmp_path = Path(tmp_name)
            retry_after_override: float | None = None
            try:
                async with self._client.stream("GET", resolved_url) as resp:
                    status_code = resp.status_code
                    content_type = resp.headers.get("content-type")
                    cd_filename = self._parse_content_disposition(
                        resp.headers.get("content-disposition")
                    )
                    final_url = str(resp.url)
                    # Honour Retry-After when the server is rate-limiting us.
                    if status_code in (429, 503):
                        retry_after_override = self._parse_retry_after(
                            resp.headers.get("retry-after")
                        )
                    resp.raise_for_status()
                    ct_lower = (content_type or "").lower()
                    if "pdf" not in ct_lower and not is_pdf_url(final_url):
                        raise ValueError(f"Not a PDF (content-type={content_type!r})")
                    with tmp_path.open("wb") as out:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            out.write(chunk)
                # Magic-number sanity check — guards against HTML error pages
                # served at .pdf URLs.
                with tmp_path.open("rb") as f:
                    head = f.read(len(self._PDF_MAGIC))
                if head != self._PDF_MAGIC:
                    raise ValueError(f"Downloaded file is not a PDF (header={head!r})")
                file_size = tmp_path.stat().st_size
                sha256 = compute_sha256(tmp_path)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - errors surface via the manifest row
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt,
                    self.config.retries,
                    resolved_url,
                    last_error,
                )
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                if attempt < self.config.retries:
                    wait = (
                        retry_after_override
                        if retry_after_override is not None
                        else backoff
                    )
                    await asyncio.sleep(jittered(wait))
                    backoff *= 2

        if last_error is not None:
            entry = ManifestEntry(
                source_name=source_name,
                source_url=source_url,
                discovered_link_text=discovered_link_text,
                discovered_url=discovered_url,
                resolved_url=resolved_url,
                final_url_after_redirects=final_url,
                domain=get_domain(final_url or resolved_url),
                content_type=content_type,
                status_code=status_code,
                crawl_depth=crawl_depth,
                error=last_error,
            )
            self.manifest.add(entry)
            return entry

        assert sha256 is not None and tmp_path is not None

        async with self._lock:
            # Dedup by content hash: another seed may have produced the same file.
            if not self.config.force:
                existing_hash = self.manifest.get_by_hash(sha256)
                if existing_hash and existing_hash.local_file_path:
                    tmp_path.unlink(missing_ok=True)
                    logger.info(
                        "Hash dedup: %s matches existing %s",
                        resolved_url,
                        existing_hash.local_file_path,
                    )
                    return self._add_cross_reference(
                        existing=existing_hash,
                        source_name=source_name,
                        source_url=source_url,
                        discovered_url=discovered_url,
                        resolved_url=resolved_url,
                        discovered_link_text=discovered_link_text,
                        crawl_depth=crawl_depth,
                        content_type=content_type,
                        status_code=status_code,
                        final_url=final_url,
                        sha256=sha256,
                    )

            dest_dir = self.config.output_dir / slugify_source(source_name)
            dest_dir.mkdir(parents=True, exist_ok=True)
            original_filename = cd_filename or self._extract_original_filename(
                final_url, resolved_url
            )
            saved_name = safe_filename(
                final_url,
                original_filename=original_filename,
                hash_prefix=short_url_hash(final_url),
            )
            dest = dest_dir / saved_name
            counter = 1
            while dest.exists():
                stem, _, ext = saved_name.rpartition(".")
                dest = dest_dir / f"{stem}_{counter}.{ext}"
                counter += 1
            shutil.move(str(tmp_path), str(dest))

            entry = ManifestEntry(
                source_name=source_name,
                source_url=source_url,
                discovered_link_text=discovered_link_text,
                discovered_url=discovered_url,
                resolved_url=resolved_url,
                final_url_after_redirects=final_url,
                domain=get_domain(final_url),
                local_file_path=str(dest),
                original_filename=original_filename,
                saved_filename=dest.name,
                content_type=content_type,
                status_code=status_code,
                file_size_bytes=file_size,
                sha256=sha256,
                downloaded_at_utc=ManifestEntry.now_utc(),
                crawl_depth=crawl_depth,
            )
            self.manifest.add(entry)
            return entry

    # -------------------------------------------------------------------------

    def _add_cross_reference(
        self,
        *,
        existing: ManifestEntry,
        source_name: str,
        source_url: str,
        discovered_url: str,
        resolved_url: str,
        discovered_link_text: str | None,
        crawl_depth: int,
        content_type: str | None = None,
        status_code: int | None = None,
        final_url: str | None = None,
        sha256: str | None = None,
    ) -> ManifestEntry:
        entry = ManifestEntry(
            source_name=source_name,
            source_url=source_url,
            discovered_link_text=discovered_link_text,
            discovered_url=discovered_url,
            resolved_url=resolved_url,
            final_url_after_redirects=final_url or existing.final_url_after_redirects,
            domain=existing.domain or get_domain(final_url or resolved_url),
            local_file_path=existing.local_file_path,
            original_filename=existing.original_filename,
            saved_filename=existing.saved_filename,
            content_type=content_type or existing.content_type,
            status_code=status_code if status_code is not None else existing.status_code,
            file_size_bytes=existing.file_size_bytes,
            sha256=sha256 or existing.sha256,
            downloaded_at_utc=ManifestEntry.now_utc(),
            crawl_depth=crawl_depth,
        )
        self.manifest.add(entry)
        return entry

    @staticmethod
    def _parse_retry_after(value: str | None, *, cap_seconds: float = 300.0) -> float | None:
        """Parse RFC 7231 ``Retry-After`` (delta-seconds or HTTP-date), capped for safety."""
        if not value:
            return None
        value = value.strip()
        try:
            return min(max(0.0, float(value)), cap_seconds)
        except ValueError:
            pass
        try:
            from datetime import datetime, timezone
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(value)
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, min(delta, cap_seconds))
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _parse_content_disposition(cls, value: str | None) -> str | None:
        if not value:
            return None
        match = cls._CD_FILENAME_RE.search(value)
        if not match:
            return None
        return unquote(match.group(1).strip()) or None

    @staticmethod
    def _extract_original_filename(final_url: str, resolved_url: str) -> str | None:
        for url in (final_url, resolved_url):
            if not url:
                continue
            path = unquote(urlparse(url).path or "")
            name = path.rsplit("/", 1)[-1]
            if name:
                return name
        return None
