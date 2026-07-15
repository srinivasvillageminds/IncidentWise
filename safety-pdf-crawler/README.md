# safety-pdf-crawler

Polite, resumable crawler for downloading **publicly available** Indian process-safety PDFs (OISD case studies, PNGRB incident analyses, PESO annual reports, NDMA chemical-disaster material, etc.) while preserving full per-link provenance so downstream tooling can filter the corpus by source page.

Built on [Crawl4AI](https://github.com/unclecode/crawl4ai), `httpx`, and BeautifulSoup.

## What it does

For each seed in `seeds.yaml`:

1. Fetches the page (Crawl4AI, headless Chromium).
2. Extracts every `<a href>` link and resolves it against the page URL.
3. Identifies PDF links (URL path ending with or containing `.pdf`, case-insensitive; verified again via response `Content-Type` and the `%PDF-` magic header during download).
4. Downloads each PDF over `httpx` with retries, exponential backoff, polite delay, and a transparent User-Agent.
5. Writes **one manifest row per (source, PDF) tuple** to `data/manifests/pdf_manifest.{csv,jsonl}`, so the same PDF discovered from two seeds yields two manifest rows but only one file on disk.

If a seed URL is itself a PDF, the crawler downloads it directly (skipping the browser render).

## Manifest fields

| Field                       | Purpose                                                                                     |
|-----------------------------|---------------------------------------------------------------------------------------------|
| `id`                        | UUID for this manifest row                                                                  |
| `source_name`               | Name from `seeds.yaml` (e.g. `oisd_case_studies`)                                           |
| `source_url`                | The seed page URL — **use this to filter the corpus by source page**                        |
| `discovered_link_text`      | Anchor text from the HTML                                                                   |
| `discovered_url`            | Raw `href` as it appeared on the page (may be relative)                                     |
| `resolved_url`              | Absolute URL we issued the GET against                                                      |
| `final_url_after_redirects` | URL after HTTP redirects                                                                    |
| `domain`                    | Host portion of the final URL                                                               |
| `local_file_path`           | Path to the saved PDF (shared across rows that point to the same file)                      |
| `original_filename`         | Best-effort filename from the URL or `Content-Disposition`                                  |
| `saved_filename`            | Sanitized name actually used on disk (`<urlhash>_<safe-original>.pdf`)                      |
| `content_type`              | Response `Content-Type`                                                                     |
| `status_code`               | Final HTTP status                                                                           |
| `file_size_bytes`           | Size on disk                                                                                |
| `sha256`                    | SHA-256 of the downloaded bytes; reused for dedup                                           |
| `downloaded_at_utc`         | ISO-8601 timestamp (UTC) of this manifest row                                               |
| `crawl_depth`               | `0` for links found on the seed page; `>0` for links found via `--max-depth`                |
| `error`                     | If non-empty, the download failed and `local_file_path` will be empty                       |

## Install

```bash
python -m venv .venv
. .venv/Scripts/activate            # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium         # Crawl4AI uses a real headless browser
```

Python **3.11+** required.

## Usage

```bash
# Default run: download PDFs linked directly from each seed.
safety-pdf-crawler --seeds seeds.yaml

# Crawl one level into the seed's own domain.
safety-pdf-crawler --max-depth 1 --same-domain-only

# Plan-only run: walk pages and write manifest rows, but don't fetch any PDFs.
safety-pdf-crawler --dry-run

# Re-run only one seed.
safety-pdf-crawler --seed-name oisd_case_studies

# Force a re-download (ignore existing manifest entries).
safety-pdf-crawler --force
```

### CLI flags

| Flag                  | Default              | Purpose                                                                |
|-----------------------|----------------------|------------------------------------------------------------------------|
| `--seeds`             | `seeds.yaml`         | Path to the seeds file                                                 |
| `--out`               | `data/raw_pdfs`      | Output directory (one subdir per source, slugified)                    |
| `--manifest-dir`      | `data/manifests`     | Where the CSV / JSONL manifest is written                              |
| `--delay`             | `1.0`                | Seconds to wait between requests                                       |
| `--timeout`           | `30`                 | Per-request timeout in seconds                                         |
| `--retries`           | `3`                  | Max attempts per PDF (exponential backoff)                             |
| `--max-depth`         | `0`                  | `0` = only PDFs linked directly from the seed page                     |
| `--same-domain-only`  | off                  | Only follow links / download PDFs on the seed's own domain             |
| `--force`             | off                  | Re-download even if the URL or hash is already in the manifest         |
| `--dry-run`           | off                  | Walk pages and record manifest rows but do not download                |
| `--no-robots`         | off                  | Skip `robots.txt` checks (only if you have separately confirmed access)|
| `--user-agent`        | (built-in)           | Override the User-Agent string                                         |
| `--seed-name NAME`    | (all)                | Only process the named seed (repeatable)                               |
| `-v, --verbose`       | off                  | Verbose logging                                                        |

Re-running the tool is **resumable**: the manifest is loaded on startup and previously-successful URLs / hashes are skipped unless `--force` is given.

### Crawling tips per seed

Some seeds (`ndma_chemical`, `gujarat_dish_statistics`) are *hub pages* that mostly link to sub-pages, with PDFs one click deeper. For those, run with one level of depth scoped to the seed's own domain:

```bash
safety-pdf-crawler --seed-name ndma_chemical --max-depth 1 --same-domain-only
safety-pdf-crawler --seed-name gujarat_dish_statistics --max-depth 1 --same-domain-only
```

The other seeds in `seeds.yaml` either link to PDFs directly from the seed page or are themselves direct PDFs, so the default `--max-depth 0` is correct.

## Filtering the corpus later

Because every manifest row preserves `source_name`, `source_url`, and `domain`, you can build a per-source view trivially:

```python
import pandas as pd
m = pd.read_csv("data/manifests/pdf_manifest.csv")
oisd_only = m[m["source_name"] == "oisd_case_studies"]
unique_pdfs_per_source = m.dropna(subset=["sha256"]).groupby("source_name")["sha256"].nunique()
```

## Ethical use

This tool is intended for downloading **publicly available** safety-related reports from Indian government and quasi-government sources for legitimate research. It is **not** designed for and must not be used for:

- bypassing authentication, paywalls, CAPTCHAs, or private/hidden APIs;
- scraping personal data;
- ignoring `robots.txt` (use `--no-robots` **only** after you have separately confirmed permission);
- hammering servers — defaults are deliberately conservative; please keep them polite.

Update the contact email in the User-Agent (`src/safety_pdf_crawler/config.py`, `DEFAULT_USER_AGENT`) before running so site operators can reach you if there is a problem.

## Layout

```
safety-pdf-crawler/
├── README.md
├── pyproject.toml
├── seeds.yaml
├── src/safety_pdf_crawler/
│   ├── __init__.py
│   ├── cli.py
│   ├── crawler.py
│   ├── downloader.py
│   ├── manifest.py
│   ├── utils.py
│   └── config.py
├── tests/
│   ├── test_utils.py
│   └── test_manifest.py
└── data/
    ├── raw_pdfs/<source_slug>/...
    └── manifests/pdf_manifest.{csv,jsonl}
```

## Tests

```bash
pytest
```

The bundled tests cover utilities and manifest round-trip; they do not require network access.

## License

MIT.
