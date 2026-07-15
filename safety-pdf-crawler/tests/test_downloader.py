from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from safety_pdf_crawler.downloader import Downloader


def test_retry_after_integer_seconds():
    assert Downloader._parse_retry_after("5") == 5.0
    assert Downloader._parse_retry_after("0") == 0.0
    assert Downloader._parse_retry_after("12.5") == 12.5


def test_retry_after_negative_is_clamped_to_zero():
    assert Downloader._parse_retry_after("-5") == 0.0


def test_retry_after_caps_runaway_values():
    assert Downloader._parse_retry_after("99999", cap_seconds=300.0) == 300.0
    assert Downloader._parse_retry_after("60", cap_seconds=300.0) == 60.0


def test_retry_after_none_and_blank():
    assert Downloader._parse_retry_after(None) is None
    assert Downloader._parse_retry_after("") is None
    assert Downloader._parse_retry_after("   ") is None


def test_retry_after_malformed_returns_none():
    assert Downloader._parse_retry_after("not a date or number") is None


def test_retry_after_http_date_in_past_is_zero():
    past = "Wed, 01 Jan 2020 00:00:00 GMT"
    assert Downloader._parse_retry_after(past) == 0.0


def test_retry_after_http_date_in_future():
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    val = format_datetime(future, usegmt=True)
    result = Downloader._parse_retry_after(val)
    assert result is not None
    # Allow some slack for the time elapsed during the test itself.
    assert 30.0 <= result <= 90.0
