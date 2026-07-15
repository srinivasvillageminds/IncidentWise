from pathlib import Path

import pandas as pd

from safety_pdf_crawler.manifest import MANIFEST_FIELDS, Manifest, ManifestEntry


def _make_entry(**overrides) -> ManifestEntry:
    base = dict(
        source_name="src",
        source_url="https://s.test/page",
        discovered_url="/a.pdf",
        resolved_url="https://s.test/a.pdf",
        final_url_after_redirects="https://s.test/a.pdf",
        domain="s.test",
        local_file_path="/tmp/a.pdf",
        original_filename="a.pdf",
        saved_filename="hash_a.pdf",
        content_type="application/pdf",
        status_code=200,
        file_size_bytes=42,
        sha256="deadbeef",
        downloaded_at_utc="2026-01-01T00:00:00Z",
        crawl_depth=0,
    )
    base.update(overrides)
    return ManifestEntry(**base)


def test_round_trip_jsonl_and_csv(tmp_path: Path):
    m = Manifest(tmp_path)
    e = _make_entry()
    m.add(e)
    m.save_csv()

    assert m.jsonl_path.exists()
    assert m.csv_path.exists()
    assert m.get_by_final_url("https://s.test/a.pdf") is e
    assert m.get_by_hash("deadbeef") is e
    assert m.has_successful_download_for_url("https://s.test/a.pdf")

    # Reload manifest from disk and check it preserves indexes.
    m2 = Manifest(tmp_path)
    m2.load()
    assert len(m2) == 1
    assert m2.get_by_hash("deadbeef") is not None
    assert m2.has_successful_download_for_url("https://s.test/a.pdf")


def test_csv_has_exact_required_columns(tmp_path: Path):
    m = Manifest(tmp_path)
    m.add(_make_entry())
    m.save_csv()
    df = pd.read_csv(m.csv_path)
    assert list(df.columns) == MANIFEST_FIELDS


def test_first_successful_entry_wins_dedup_index(tmp_path: Path):
    m = Manifest(tmp_path)
    e1 = _make_entry(source_name="a", source_url="u1")
    e2 = _make_entry(source_name="b", source_url="u2")
    m.add(e1)
    m.add(e2)
    # Both rows are recorded; the first is canonical in the dedup indexes.
    assert len(m) == 2
    assert m.get_by_final_url("https://s.test/a.pdf") is e1
    assert m.get_by_hash("deadbeef") is e1


def test_error_entries_do_not_populate_dedup_indexes(tmp_path: Path):
    m = Manifest(tmp_path)
    err = _make_entry(local_file_path=None, sha256=None, error="HTTPError: 503")
    m.add(err)
    assert len(m) == 1
    assert m.get_by_final_url("https://s.test/a.pdf") is None
    assert not m.has_successful_download_for_url("https://s.test/a.pdf")


def test_jsonl_appended_per_add(tmp_path: Path):
    m = Manifest(tmp_path)
    m.add(_make_entry(source_name="a"))
    m.add(_make_entry(source_name="b"))
    lines = m.jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
