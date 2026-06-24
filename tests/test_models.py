from datetime import timezone

from ytcurator.models import parse_iso8601_duration, parse_rfc3339


def test_parse_duration_full():
    assert parse_iso8601_duration("PT1H2M30S") == 3750


def test_parse_duration_partial():
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("PT12M") == 720
    assert parse_iso8601_duration("P1DT2H") == 93600


def test_parse_duration_garbage_is_zero():
    assert parse_iso8601_duration("") == 0
    assert parse_iso8601_duration("P0D") == 0  # live streams
    assert parse_iso8601_duration("nonsense") == 0


def test_parse_rfc3339_z_suffix():
    dt = parse_rfc3339("2026-06-24T10:00:00Z")
    assert dt.tzinfo == timezone.utc
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 6, 24, 10)
