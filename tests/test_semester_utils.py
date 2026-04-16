from datetime import datetime

from app.utils.semester import (
    format_offering_display_tag,
    infer_offering_from_datetime,
    normalize_offering_identifier,
    parse_offering_display_tag,
)


def test_parse_and_format_offering_display_tag_round_trip():
    assert format_offering_display_tag("2025", "fall") == "25-26Fall"
    assert parse_offering_display_tag("25-26Fall") == ("2025", "fall")


def test_normalize_offering_identifier_accepts_display_and_internal_codes():
    assert normalize_offering_identifier("25-26Fall") == ("2025", "fall")
    assert normalize_offering_identifier("2024spring") == ("2024", "spring")


def test_infer_offering_from_datetime_uses_academic_year_windows():
    assert infer_offering_from_datetime(datetime(2025, 6, 15)) == ("2024", "spring")
    assert infer_offering_from_datetime(datetime(2025, 10, 1)) == ("2025", "fall")
    assert infer_offering_from_datetime(datetime(2026, 1, 8)) == ("2025", "fall")
