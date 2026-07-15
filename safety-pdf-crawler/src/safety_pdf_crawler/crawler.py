"""BFS crawler over seed pages, powered by Crawl4AI for rendering."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from bs4 import BeautifulSoup
from rich.console import Console

from .config import CrawlerConfig, Seed
from .downloader import Downloader
from .manifest import Manifest
from .utils import (
    can_fetch_robots,
    is_pdf_url,
    jittered,
    normalize_url,
    same_domain,
)

logger = logging.getLogger(__name__)
console = Console()


class SafetyCrawler:
    """Render seed pages with Crawl4AI, extract links with BeautifulSoup, hand PDFs to the downloader."""

    def __init__(self, config: CrawlerConfig, manifest: Manifest, downloader: Downloader):
        self.config = config
        self.manifest = manifest
        self.downloader = downloader
        self._crawl4ai: Any = None

    async def __aenter__(self) -> "SafetyCrawler":
        # Imported lazily so unit tests and `--help` don't pay the Playwright cost.
        from crawl4ai import AsyncWebCrawler, BrowserConfig

        browser_cfg = BrowserConfig(
            headless=True,
            verbose=False,
            user_agent=self.config.user_agent,
            # Legitimate desktop-browser hints, not fingerprint spoofing — these
            # make the request look like a normal English-India browser visit
            # rather than the default headless-Chromium signature.
            headers={"Accept-Language": "en-IN,en;q=0.9"},
        )
        self._crawl4ai = AsyncWebCrawler(config=browser_cfg)
        await self._crawl4ai.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._crawl4ai is not None:
            await self._crawl4ai.__aexit__(exc_type, exc, tb)
            self._crawl4ai = None

    # -------------------------------------------------------------------------

    async def crawl_seed(self, seed: Seed) -> None:
        console.print(f"[bold cyan]Seed[/bold cyan] [white]{seed.name}[/white] → {seed.url}")

        if self.config.respect_robots and not can_fetch_robots(seed.url, self.config.user_agent):
            console.print(f"  [yellow]robots.txt disallows {seed.url} — skipping[/yellow]")
            return

        # Seed is itself a PDF — download directly, no rendering.
        if is_pdf_url(seed.url):
            await self.downloader.download(
                source_name=seed.name,
                source_url=seed.url,
                discovered_url=seed.url,
                resolved_url=seed.url,
                discovered_link_text=None,
                crawl_depth=0,
            )
            await asyncio.sleep(jittered(self.config.delay))
            return

        # BFS over pages within this seed.
        queue: deque[tuple[str, int]] = deque([(seed.url, 0)])
        visited: set[str] = set()

        while queue:
            page_url, depth = queue.popleft()
            if page_url in visited:
                continue
            visited.add(page_url)

            if self.config.respect_robots and not can_fetch_robots(page_url, self.config.user_agent):
                console.print(f"  [yellow]robots.txt disallows page {page_url}[/yellow]")
                continue

            html, page_status = await self._fetch_page(page_url)
            if not html:
                console.print(
                    f"  [red]Failed to render {page_url} (status={page_status})[/red]"
                )
                continue

            pdf_links, html_links = self._extract_links(html, base=page_url)
            console.print(
                f"  [dim]depth={depth}[/dim] {page_url} → "
                f"[green]{len(pdf_links)} pdf candidates[/green], "
                f"{len(html_links)} html links"
            )

            for link in pdf_links:
                resolved = link["resolved"]
                if self.config.same_domain_only and not same_domain(resolved, seed.url):
                    continue
                if (
                    not self.config.force
                    and self.manifest.has_successful_download_for_url(resolved)
                ):
                    # Still record the cross-reference so source filtering stays accurate.
                    await self.downloader.download(
                        source_name=seed.name,
                        source_url=seed.url,
                        discovered_url=link["raw"],
                        resolved_url=resolved,
                        discovered_link_text=link["text"],
                        crawl_depth=depth,
                    )
                    continue
                await self.downloader.download(
                    source_name=seed.name,
                    source_url=seed.url,
                    discovered_url=link["raw"],
                    resolved_url=resolved,
                    discovered_link_text=link["text"],
                    crawl_depth=depth,
                )
                await asyncio.sleep(jittered(self.config.delay))

            if depth < self.config.max_depth:
                for link in html_links:
                    resolved = link["resolved"]
                    if self.config.same_domain_only and not same_domain(resolved, seed.url):
                        continue
                    if resolved in visited:
                        continue
                    queue.append((resolved, depth + 1))

    # -------------------------------------------------------------------------

    async def _fetch_page(self, url: str) -> tuple[str | None, int | None]:
        assert self._crawl4ai is not None, "SafetyCrawler must be used as an async context manager"
        try:
            from crawl4ai import CacheMode, CrawlerRunConfig

            run_cfg = CrawlerRunConfig(
                # ENABLED keeps Crawl4AI's SQLite page cache warm across runs so a
                # resumed crawl doesn't re-render seed pages it has already seen.
                # --force flips this to BYPASS for an explicit refresh.
                cache_mode=CacheMode.BYPASS if self.config.force else CacheMode.ENABLED,
                page_timeout=int(self.config.timeout * 1000),
                # Give JS-rendered tables (OISD archived case studies, PNGRB lists)
                # a moment to populate before we snapshot the HTML for link extraction.
                delay_before_return_html=1.5,
            )
            result = await self._crawl4ai.arun(url=url, config=run_cfg)
            if not getattr(result, "success", False):
                logger.warning(
                    "Crawl4AI fetch failed for %s: %s",
                    url,
                    getattr(result, "error_message", None),
                )
                return None, getattr(result, "status_code", None)
            return result.html, getattr(result, "status_code", 200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exception fetching %s: %s", url, exc)
            return None, None

    @staticmethod
    def _extract_links(html: str, *, base: str) -> tuple[list[dict], list[dict]]:
        """Split anchor links into (pdf_candidates, html_candidates)."""
        soup = BeautifulSoup(html, "html.parser")
        pdfs: list[dict] = []
        htmls: list[dict] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            resolved = normalize_url(href, base)
            if resolved in seen:
                continue
            seen.add(resolved)
            text = (a.get_text() or "").strip() or None
            entry = {"raw": href, "resolved": resolved, "text": text}
            if is_pdf_url(resolved):
                pdfs.append(entry)
            else:
                htmls.append(entry)
        return pdfs, htmls
