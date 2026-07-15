"""Command-line entry point for safety-pdf-crawler."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .config import DEFAULT_USER_AGENT, CrawlerConfig, Seed, SeedsFile
from .crawler import SafetyCrawler
from .downloader import Downloader
from .manifest import Manifest

console = Console()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="safety-pdf-crawler",
        description=(
            "Polite crawler for downloading publicly available Indian process-safety PDFs "
            "with full per-source provenance."
        ),
    )
    p.add_argument("--seeds", type=Path, default=Path("seeds.yaml"), help="Path to seeds YAML file")
    p.add_argument("--out", dest="output_dir", type=Path, default=Path("data/raw_pdfs"),
                   help="Output directory for downloaded PDFs")
    p.add_argument("--manifest-dir", type=Path, default=Path("data/manifests"),
                   help="Directory for the CSV / JSONL manifest")
    p.add_argument("--delay", type=float, default=1.0, help="Polite delay between requests (seconds)")
    p.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout (seconds)")
    p.add_argument("--retries", type=int, default=3, help="Max attempts per PDF")
    p.add_argument("--max-depth", type=int, default=0,
                   help="0 = only PDFs linked from the seed page; N = follow N levels of internal links")
    p.add_argument("--same-domain-only", action="store_true",
                   help="Restrict link-following and downloads to the seed's own domain")
    p.add_argument("--force", action="store_true",
                   help="Re-download even if URL/hash is already in the manifest")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan and record manifest rows but do not download files")
    p.add_argument("--no-robots", action="store_true",
                   help="Skip robots.txt checks (only if you have separately confirmed permission)")
    p.add_argument("--user-agent", type=str, default=None, help="Override the User-Agent string")
    p.add_argument("--seed-name", action="append", default=None,
                   help="Process only the named seed (repeatable)")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    p.add_argument("--version", action="version", version=f"safety-pdf-crawler {__version__}")
    return p.parse_args(argv)


def load_seeds(path: Path) -> list[Seed]:
    if not path.exists():
        raise SystemExit(f"seeds file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return SeedsFile.model_validate(data).seeds


async def run(config: CrawlerConfig, seed_filter: list[str] | None) -> int:
    seeds = load_seeds(config.seeds_path)
    if seed_filter:
        wanted = set(seed_filter)
        seeds = [s for s in seeds if s.name in wanted]
        if not seeds:
            console.print(f"[red]No seeds matched filter: {sorted(wanted)}[/red]")
            return 2

    config.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(config.manifest_dir)
    manifest.load()
    console.print(
        f"Manifest loaded: [bold]{len(manifest)}[/bold] existing rows "
        f"from [cyan]{manifest.jsonl_path}[/cyan]"
    )
    console.print(
        f"Settings: delay={config.delay}s timeout={config.timeout}s retries={config.retries} "
        f"max_depth={config.max_depth} same_domain_only={config.same_domain_only} "
        f"force={config.force} dry_run={config.dry_run} respect_robots={config.respect_robots}"
    )

    async with Downloader(config, manifest) as downloader:
        async with SafetyCrawler(config, manifest, downloader) as crawler:
            for seed in seeds:
                try:
                    await crawler.crawl_seed(seed)
                except Exception:  # noqa: BLE001
                    logging.exception("Unhandled error while processing seed %s", seed.name)

    manifest.save_csv()
    console.print(
        f"[green]Done.[/green] {len(manifest)} total manifest rows → [cyan]{manifest.csv_path}[/cyan]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False, console=console)],
    )

    config_kwargs: dict = {
        "seeds_path": args.seeds,
        "output_dir": args.output_dir,
        "manifest_dir": args.manifest_dir,
        "delay": args.delay,
        "timeout": args.timeout,
        "retries": args.retries,
        "max_depth": args.max_depth,
        "same_domain_only": args.same_domain_only,
        "force": args.force,
        "dry_run": args.dry_run,
        "respect_robots": not args.no_robots,
        "user_agent": args.user_agent or DEFAULT_USER_AGENT,
    }
    config = CrawlerConfig(**config_kwargs)
    return asyncio.run(run(config, args.seed_name))


if __name__ == "__main__":
    sys.exit(main())
