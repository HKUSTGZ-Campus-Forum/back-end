import copy

import pytest

from app.scripts.fetch_hkustgz_wcq import (
    WCQPreparationError,
    _build_parser,
    build_snapshot,
    parse_index,
    parse_subject_page,
    validate_snapshot,
)


INDEX_HTML = """
<!doctype html>
<html>
  <head><title>AIAA - HKUST Class Schedule &amp; Quota</title></head>
  <body>
    <a href="/wcq/cgi-bin/2610/">2026-27 Fall</a>
    <div class="depts">
      <a class="ug" href="/wcq/cgi-bin/2610/subject/AIAA">AIAA</a>
      <a class="pg" href="/wcq/cgi-bin/2610/subject/TEST">TEST</a>
    </div>
  </body>
</html>
"""


def _page(subject: str, courses: str) -> str:
    return f"""
    <!doctype html>
    <html>
      <head><title>{subject} - HKUST Class Schedule &amp; Quota</title></head>
      <body><div id="classes">{courses}</div></body>
    </html>
    """


AIAA_COURSE = """
<div class="course">
  <div class="courseinfo">
    <table>
      <tr><th>PRE-REQUISITE</th><td>UFUG 2601</td></tr>
      <tr><th>CO-REQUISITE</th><td>DSAA 1001</td></tr>
      <tr><th>EXCLUSION</th><td>AIAA 9999</td></tr>
      <tr><th>DESCRIPTION</th><td>A representative course.</td></tr>
      <tr><th>VECTOR</th><td>3-0-1:4</td></tr>
    </table>
  </div>
  <div class="matching">[Matching between Lecture &amp; Lab required]</div>
  <h2>AIAA 2205 - Introduction to Artificial Intelligence (3 units)</h2>
  <table class="sections">
    <tr><th>Section</th></tr>
    <tr class="newsect secteven">
      <td>L01 (6550)</td>
      <td>01-SEP-2026 - 30-SEP-2026<br>MoWe 10:30AM - 11:50AM</td>
      <td>Room A</td>
      <td><a>ALPHA, Ada</a></td>
      <td><span>20</span><div>Special quota: 20/21/-1</div></td>
      <td>21</td><td>-1</td><td>-1</td><td>&nbsp;</td>
    </tr>
    <tr class="secteven">
      <td>01-OCT-2026 - 30-NOV-2026<br>Mo 10:30AM - 11:50AM</td>
      <td>Room B</td>
      <td><a>BETA, Bo</a></td>
    </tr>
    <tr class="newsect sectodd">
      <td>LA01 (6551)</td>
      <td>Fr 12:00PM - 12:50PM</td><td>Lab A</td><td>TBA</td>
      <td>20</td><td>0</td><td>20</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect secteven">
      <td>T01 (6552)</td>
      <td>Tu 01:30PM - 02:20PM</td><td>Room C</td><td>TBA</td>
      <td>20</td><td>0</td><td>20</td><td>0</td><td>&nbsp;</td>
    </tr>
  </table>
</div>
"""


R_ONLY_COURSE = """
<div class="course">
  <div class="courseinfo"><table>
    <tr><th>DESCRIPTION</th><td>Independent research.</td></tr>
  </table></div>
  <h2>TEST 6091A - Independent Study (3 units)</h2>
  <table class="sections">
    <tr><th>Section</th></tr>
    <tr class="newsect secteven">
      <td>R01 (7001)</td><td>TBA</td><td>No room required</td><td>TBA</td>
      <td>5</td><td>0</td><td>5</td><td>0</td><td>&nbsp;</td>
    </tr>
  </table>
</div>
"""


ALPHANUMERIC_LABS_COURSE = """
<div class="course">
  <div class="courseinfo"><table>
    <tr><th>DESCRIPTION</th><td>A course with alternative labs.</td></tr>
  </table></div>
  <div class="matching">[Matching between Lecture &amp; Lab required]</div>
  <h2>AIAA 2601 - Programming Studio (3 units)</h2>
  <table class="sections">
    <tr><th>Section</th></tr>
    <tr class="newsect secteven">
      <td>L02 (7100)</td><td>Mo 09:00AM - 09:50AM</td><td>Room L</td><td>TBA</td>
      <td>20</td><td>0</td><td>20</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect sectodd">
      <td>LA2A (7101)</td><td>Tu 09:00AM - 09:50AM</td><td>Lab A</td><td>TBA</td>
      <td>10</td><td>0</td><td>10</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect secteven">
      <td>LA2B (7102)</td><td>We 09:00AM - 09:50AM</td><td>Lab B</td><td>TBA</td>
      <td>10</td><td>0</td><td>10</td><td>0</td><td>&nbsp;</td>
    </tr>
  </table>
</div>
"""


def test_parse_index_uses_reproducible_front_controller_urls():
    term_name, subjects = parse_index(
        INDEX_HTML,
        term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
    )

    assert term_name == "2026-27 Fall"
    assert [(subject.code, subject.classification) for subject in subjects] == [
        ("AIAA", "ug"),
        ("TEST", "pg"),
    ]
    assert subjects[0].url == (
        "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/index.php?term=2610&subject=AIAA"
    )


def test_parse_subject_preserves_info_occupancy_matching_and_canonical_meetings():
    parsed = parse_subject_page(
        _page("AIAA", AIAA_COURSE), expected_subject="AIAA"
    )

    assert len(parsed.courses) == 1
    course = parsed.courses[0]
    assert course["course_code"] == "AIAA2205"
    assert course["pre_requirement"] == "UFUG 2601"
    assert course["co_requirement"] == "DSAA 1001"
    assert course["exclusion"] == "AIAA 9999"
    assert course["course_info"]["VECTOR"] == "3-0-1:4"

    sections = {section["name"]: section for section in course["sections"]}
    lecture = sections["L01"]
    assert (lecture["quota"], lecture["enrol"], lecture["avail"], lecture["wait"]) == (
        20,
        21,
        -1,
        -1,
    )
    assert lecture["is_main"] is True
    assert lecture["layer"] == 0
    assert sections["LA01"]["layer"] == 0
    assert sections["T01"]["layer"] == 1
    assert lecture["lectures"] == [
        {
            "day": 1,
            "start_time": "1030",
            "end_time": "1150",
            "room": "Room A & Room B",
            "instructor": "ALPHA, Ada & BETA, Bo",
            "source_date_times": [
                "01-SEP-2026 - 30-SEP-2026\nMoWe 10:30AM - 11:50AM",
                "01-OCT-2026 - 30-NOV-2026\nMo 10:30AM - 11:50AM",
            ],
        },
        {
            "day": 3,
            "start_time": "1030",
            "end_time": "1150",
            "room": "Room A",
            "instructor": "ALPHA, Ada",
            "source_date_times": [
                "01-SEP-2026 - 30-SEP-2026\nMoWe 10:30AM - 11:50AM"
            ],
        },
    ]


def test_research_only_course_uses_compatibility_main_type_fallback():
    parsed = parse_subject_page(
        _page("TEST", R_ONLY_COURSE), expected_subject="TEST"
    )

    section = parsed.courses[0]["sections"][0]
    assert section["section_type"] == "R"
    assert section["is_main"] is True
    assert section["layer"] == 0
    assert section["lectures"] == []


def test_alphanumeric_matched_labs_become_explicit_alternative_layer():
    parsed = parse_subject_page(
        _page("AIAA", ALPHANUMERIC_LABS_COURSE), expected_subject="AIAA"
    )

    sections = {section["name"]: section for section in parsed.courses[0]["sections"]}
    assert sections["L02"]["layer"] == 0
    assert "source_bundle" not in sections["L02"]
    assert (sections["LA2A"]["layer"], sections["LA2A"]["bundle"]) == (1, 1)
    assert (sections["LA2B"]["layer"], sections["LA2B"]["bundle"]) == (1, 2)
    assert sections["LA2A"]["source_bundle"] == 2
    assert sections["LA2B"]["source_bundle"] == 2


def test_build_snapshot_requires_every_advertised_subject_and_records_hashes():
    pages = {
        "AIAA": _page("AIAA", AIAA_COURSE + ALPHANUMERIC_LABS_COURSE),
        "TEST": _page("TEST", R_ONLY_COURSE),
    }
    snapshot = build_snapshot(
        index_html=INDEX_HTML,
        term="2610",
        term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
        retrieved_at="2026-08-09T15:18:42Z",
        semester_start_date="2026-09-01",
        subject_html=pages,
    )

    assert snapshot["semester_id"] == "2610"
    assert snapshot["provenance"]["retrieved_at"] == "2026-08-09T15:18:42Z"
    assert len(snapshot["provenance"]["term_index_sha256"]) == 64
    assert all(
        len(subject["sha256"]) == 64
        for subject in snapshot["provenance"]["subjects"]
    )
    assert snapshot["provenance"]["approximations"][0] == {
        "kind": "matched_alphanumeric_section_bundles",
        "course_code": "AIAA2601",
        "section_type": "LA",
        "source_sections": [
            {
                "name": "LA2A",
                "section_id": "7101",
                "source_bundle": 2,
                "prepared_bundle": 1,
            },
            {
                "name": "LA2B",
                "section_id": "7102",
                "source_bundle": 2,
                "prepared_bundle": 2,
            },
        ],
        "explanation": snapshot["provenance"]["approximations"][0]["explanation"],
    }
    assert snapshot["provenance"]["approximations"][1] == {
        "kind": "date_ranges_collapsed_to_weekly_meetings",
        "affected_canonical_meetings": 2,
        "retained_date_range_values": 3,
        "explanation": snapshot["provenance"]["approximations"][1]["explanation"],
    }
    assert validate_snapshot(
        snapshot,
        expected_subjects=2,
        expected_courses=3,
        expected_offered_courses=3,
        expected_sections=7,
        expected_lectures=7,
    ) == {
        "subjects": 2,
        "courses": 3,
        "offered_courses": 3,
        "sections": 7,
        "lectures": 7,
    }

    with pytest.raises(WCQPreparationError, match="missing=TEST"):
        build_snapshot(
            index_html=INDEX_HTML,
            term="2610",
            term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
            retrieved_at="2026-08-09T15:18:42Z",
            semester_start_date="2026-09-01",
            subject_html={"AIAA": pages["AIAA"]},
        )


def test_validation_rejects_duplicate_sections_and_wrong_control_totals():
    snapshot = build_snapshot(
        index_html=INDEX_HTML,
        term="2610",
        term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
        retrieved_at="2026-08-09T15:18:42Z",
        semester_start_date="2026-09-01",
        subject_html={
            "AIAA": _page("AIAA", AIAA_COURSE),
            "TEST": _page("TEST", R_ONLY_COURSE),
        },
    )
    duplicate = copy.deepcopy(snapshot)
    duplicate["courses"][1]["sections"][0]["section_id"] = "6550"

    with pytest.raises(WCQPreparationError, match="duplicate section identity"):
        validate_snapshot(duplicate)
    with pytest.raises(WCQPreparationError, match=r"courses=2 \(expected 3\)"):
        validate_snapshot(snapshot, expected_courses=3)


def test_help_explicitly_states_that_the_generator_does_not_import_or_deploy():
    help_text = _build_parser().format_help()

    assert "never connects to the DB" in help_text
    assert "never imports or deploys data" in help_text
