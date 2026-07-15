from pathlib import Path

from safety_pdf_crawler.utils import (
    compute_sha256,
    get_domain,
    is_pdf_url,
    jittered,
    normalize_url,
    safe_filename,
    same_domain,
    short_url_hash,
    slugify_source,
)


# ----- is_pdf_url ------------------------------------------------------------

def test_is_pdf_url_simple_extensions():
    assert is_pdf_url("https://example.com/foo.pdf")
    assert is_pdf_url("https://example.com/Foo.PDF")
    assert is_pdf_url("https://example.com/path/to/Foo.pdf")


def test_is_pdf_url_with_query():
    assert is_pdf_url("https://example.com/path/foo.pdf?download=1")
    assert is_pdf_url(
        "https://www.sansad.in/getFile/loksabhaquestions/annex/1712/AU460.pdf?source=pqals"
    )


def test_is_pdf_url_negatives():
    assert not is_pdf_url("")
    assert not is_pdf_url("https://example.com/")
    assert not is_pdf_url("https://example.com/foo")
    assert not is_pdf_url("https://example.com/foo.html")
    assert not is_pdf_url("https://example.com/foo.pdfx")  # not a real .pdf token


# ----- normalize_url ---------------------------------------------------------

def test_normalize_url_relative_paths():
    base = "https://www.example.com/section/a.html"
    assert normalize_url("/foo/bar.pdf", base) == "https://www.example.com/foo/bar.pdf"
    assert normalize_url("./b.pdf", base) == "https://www.example.com/section/b.pdf"
    assert normalize_url("c.pdf", base) == "https://www.example.com/section/c.pdf"


def test_normalize_url_absolute_passthrough():
    base = "https://www.example.com/"
    assert normalize_url("https://other.com/c.pdf", base) == "https://other.com/c.pdf"


# ----- domain helpers --------------------------------------------------------

def test_domain_and_same_domain():
    assert get_domain("https://www.oisd.gov.in/foo") == "www.oisd.gov.in"
    assert same_domain("https://x.gov.in/a", "https://x.gov.in/b")
    assert not same_domain("https://x.gov.in/a", "https://y.gov.in/b")


# ----- filename / slug helpers ----------------------------------------------

def test_safe_filename_strips_unsafe_chars():
    name = safe_filename("https://example.com/My Report (2023).PDF")
    assert " " not in name
    assert "(" not in name and ")" not in name
    assert name.lower().endswith(".pdf")


def test_safe_filename_prefix_and_extension():
    name = safe_filename(
        "https://example.com/path/report.pdf",
        hash_prefix="abc12345",
        original_filename="Final Report.pdf",
    )
    assert name.startswith("abc12345_")
    assert name.lower().endswith(".pdf")


def test_safe_filename_fallback_when_no_basename():
    # URL has no path basename — must still produce a usable filename.
    name = safe_filename("https://example.com/", hash_prefix="deadbeef0")
    assert name.endswith(".pdf")
    assert name.startswith("deadbeef0_")


def test_short_url_hash_is_stable_and_short():
    a = short_url_hash("https://example.com/a")
    b = short_url_hash("https://example.com/a")
    assert a == b
    assert len(a) == 10
    assert a != short_url_hash("https://example.com/b")


def test_slugify_source_handles_spaces_and_punct():
    assert slugify_source("OISD Case Studies!") == "oisd-case-studies"
    # Empty/punct-only input falls back to "unknown".
    assert slugify_source("!!!") == "unknown"


# ----- compute_sha256 --------------------------------------------------------

# ----- jittered --------------------------------------------------------------

def test_jittered_within_expected_range():
    for _ in range(200):
        v = jittered(1.0, spread=0.3)
        assert 0.7 <= v <= 1.3


def test_jittered_zero_spread_is_identity():
    assert jittered(2.5, spread=0.0) == 2.5
    assert jittered(0.0, spread=0.3) == 0.0


def test_jittered_scales_proportionally():
    # Same logical request: 10x the base value should produce 10x the bound.
    for _ in range(50):
        v = jittered(10.0, spread=0.3)
        assert 7.0 <= v <= 13.0


# ----- compute_sha256 --------------------------------------------------------

def test_compute_sha256_known_value(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    # Known SHA256 of "hello world".
    assert compute_sha256(p) == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )
