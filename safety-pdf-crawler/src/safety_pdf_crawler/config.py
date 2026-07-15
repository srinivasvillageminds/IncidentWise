"""Configuration models for safety-pdf-crawler."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_USER_AGENT = (
    "safety-pdf-crawler/0.1 (+research; contact via GitHub issues)"
)


class Seed(BaseModel):
    """A single entry from seeds.yaml."""

    name: str
    url: str
    category: str | None = None


class SeedsFile(BaseModel):
    """Top-level schema for seeds.yaml."""

    seeds: list[Seed]


class CrawlerConfig(BaseModel):
    """Runtime configuration assembled from CLI flags."""

    seeds_path: Path
    output_dir: Path = Path("data/raw_pdfs")
    manifest_dir: Path = Path("data/manifests")
    delay: float = Field(default=1.0, ge=0.0)
    timeout: float = Field(default=30.0, gt=0.0)
    retries: int = Field(default=3, ge=1)
    max_depth: int = Field(default=0, ge=0)
    same_domain_only: bool = False
    force: bool = False
    dry_run: bool = False
    respect_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT
