from datetime import datetime

from app.utils.semester import (
    format_offering_display_tag,
    infer_offering_from_datetime,
    is_offering_not_newer,
    normalize_offering_identifier,
    parse_offering_display_tag,
    sort_semesters,
)


def test_parse_and_format_offering_display_tag_round_trip():
    assert format_offering_display_tag("2025", "fall") == "25-26Fall"
    assert parse_offering_display_tag("25-26Fall") == ("2025", "fall")


def test_normalize_offering_identifier_accepts_display_and_internal_codes():
    assert normalize_offering_identifier("25-26Fall") == ("2025", "fall")
    assert normalize_offering_identifier("2024spring") == ("2024", "spring")


def test_infer_offering_from_datetime_uses_academic_year_windows():
    assert infer_offering_from_datetime(datetime(2025, 6, 15)) == ("2024", "spring")
    assert infer_offering_from_datetime(datetime(2025, 7, 1)) == ("2024", "summer")
    assert infer_offering_from_datetime(datetime(2025, 10, 1)) == ("2025", "fall")
    assert infer_offering_from_datetime(datetime(2026, 1, 8)) == ("2025", "fall")


def test_sort_semesters_uses_academic_year_chronology_with_newest_first():
    semesters = ["2025fall", "2025summer", "2025spring", "2024fall"]
    assert sort_semesters(semesters) == ["2025summer", "2025spring", "2025fall", "2024fall"]


def test_is_offering_not_newer_respects_academic_year_order():
    assert is_offering_not_newer(("2025", "fall"), ("2025", "spring")) is True
    assert is_offering_not_newer(("2024", "summer"), ("2025", "spring")) is True
    assert is_offering_not_newer(("2025", "summer"), ("2025", "spring")) is False
