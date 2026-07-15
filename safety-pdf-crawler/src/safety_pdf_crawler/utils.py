"""Utility helpers: URL handling, filenames, hashing, robots.txt."""

from __future__ import annotations

import hashlib
import logging
import random
import re
from pathlib import Path
from urllib import robotparser
from urllib.parse import unquote, urljoin, urlparse

from slugify import slugify

logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PDF_TOKEN_RE = re.compile(r"\.pdf\b", re.IGNORECASE)


def slugify_source(name: str) -> str:
    """Filesystem-safe directory name for a source."""
    return slugify(name, max_length=80) or "unknown"


def normalize_url(href: str, base: str) -> str:
    """Resolve a possibly-relative href against a base URL."""
    return urljoin(base, href.strip())


def get_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def same_domain(a: str, b: str) -> bool:
    return get_domain(a) == get_domain(b)


def is_pdf_url(url: str) -> bool:
    """URL-based heuristic for PDF links.

    Matches:
      - path ending in ``.pdf`` (case-insensitive)
      - ``.pdf`` appearing as a token elsewhere in path or query
        (covers endpoints like ``/getFile/.../AU460.pdf?source=pqals``).
    """
    if not url:
        return False
    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    if path.lower().endswith(".pdf"):
        return True
    if _PDF_TOKEN_RE.search(path):
        return True
    query = unquote(parsed.query or "")
    if _PDF_TOKEN_RE.search(query):
        return True
    return False


def short_url_hash(url: str, length: int = 10) -> str:
    """Short stable hash used as a filename prefix to avoid collisions."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:length]


def safe_filename(
    url: str,
    *,
    original_filename: str | None = None,
    hash_prefix: str | None = None,
    max_length: int = 120,
) -> str:
    """Build a filesystem-safe ``.pdf`` filename, optionally prefixed by a short hash."""
    base = original_filename
    if not base:
        parsed = urlparse(url)
        path = unquote(parsed.path or "")
        base = path.rsplit("/", 1)[-1] or "download.pdf"
    base = _SAFE_FILENAME_RE.sub("_", base).strip("._-") or "download.pdf"
    if not base.lower().endswith(".pdf"):
        base = base + ".pdf"
    stem, _, ext = base.rpartition(".")
    if len(stem) > max_length:
        stem = stem[:max_length].rstrip("._-") or "file"
    base = f"{stem}.{ext}"
    if hash_prefix:
        return f"{hash_prefix}_{base}"
    return base


def jittered(value: float, *, spread: float = 0.3) -> float:
    """Multiply ``value`` by a uniform random factor in ``[1 - spread, 1 + spread]``.

    Used to randomise polite-crawl delays so requests don't fire on a perfectly
    regular cadence (which itself reads as a bot signature).
    """
    if spread <= 0:
        return value
    return value * random.uniform(1.0 - spread, 1.0 + spread)


def compute_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Streaming SHA-256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def can_fetch_robots(url: str, user_agent: str, *, timeout: float = 10.0) -> bool:
    """Best-effort robots.txt check.

    Returns ``True`` (allow) if robots.txt is missing, unreachable, or unparseable —
    callers may flip the conservative behaviour via the ``--no-robots`` flag.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        import httpx

        with httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}) as client:
            r = client.get(robots_url)
        if r.status_code >= 400:
            return True
        rp = robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except Exception as exc:  # noqa: BLE001 - fail-open on robots fetch errors
        logger.debug("robots.txt check failed for %s: %s", robots_url, exc)
        return True
