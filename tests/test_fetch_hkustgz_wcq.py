import copy
import json
from pathlib import Path

import pytest
import requests

from app.scripts.fetch_hkustgz_wcq import (
    WCQPreparationError,
    _build_parser,
    _fetch_text,
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


UFUG_INDEX_HTML = """
<!doctype html>
<html>
  <head><title>UFUG - HKUST Class Schedule &amp; Quota</title></head>
  <body>
    <a href="/wcq/cgi-bin/2610/">2026-27 Fall</a>
    <div class="depts">
      <a class="ug" href="/wcq/cgi-bin/2610/subject/UFUG">UFUG</a>
    </div>
  </body>
</html>
"""


UFUG_1301_WITH_UNSCHEDULED_FALLBACK = """
<div class="course">
  <div class="courseinfo">
    <div class="matching">[Matching between Lecture &amp; Lab required]</div>
    <table><tr><th>DESCRIPTION</th><td>General chemistry.</td></tr></table>
  </div>
  <h2>UFUG 1301 - General Chemistry (3 units)</h2>
  <table class="sections">
    <tr><th>Section</th></tr>
    <tr class="newsect secteven">
      <td>L01 (6187)</td><td>We 01:30PM - 04:20PM</td><td>Rm 149, E1</td>
      <td>HE, Quanfu</td><td>24</td><td>0</td><td>24</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect sectodd">
      <td>TBA (6951)</td><td>TBA</td><td>TBA</td><td>TBA</td>
      <td>5</td><td>0</td><td>5</td><td>0</td>
      <td>
        <div class="popup classnotes"><div class="popupdetail">
          &gt; If the lab sessions listed in the course schedule are not suitable
          for your availability, or if you were unable to enroll in a session,
          you can contact the instructor to request access to the &quot;TBA&quot;
          section.
          The lab class schedule will be coordinated later based on the
          availability of students who select &quot;TBA.&quot; Please pay attention to
          further notifications.
        </div></div>
        <div class="popup consent"><div class="popupdetail">
          Instructor Consent Required
        </div></div>&nbsp;
      </td>
    </tr>
    <tr class="newsect secteven">
      <td>LA01 (6189)</td><td>We 01:30PM - 04:20PM</td>
      <td>Mendeleev Lab</td><td>HE, Quanfu</td>
      <td>24</td><td>0</td><td>24</td><td>0</td><td>&nbsp;</td>
    </tr>
  </table>
</div>
"""


UFUG_1302_WITH_UNSCHEDULED_FALLBACK = """
<div class="course">
  <div class="courseinfo">
    <div class="matching">[Matching between Lecture &amp; Tutorial required]</div>
    <table><tr><th>DESCRIPTION</th><td>Honors chemistry.</td></tr></table>
  </div>
  <h2>UFUG 1302 - Honors Chemistry A (3 units)</h2>
  <table class="sections">
    <tr><th>Section</th></tr>
    <tr class="newsect secteven">
      <td>L01 (6364)</td><td>TuTh 12:00PM - 01:20PM</td><td>Rm 239, E1</td>
      <td>CAO, Bei</td><td>48</td><td>0</td><td>48</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect sectodd">
      <td>T01 (6366)</td><td>Fr 02:00PM - 02:50PM</td><td>Rm 235, E1</td>
      <td>CAO, Bei</td><td>48</td><td>0</td><td>48</td><td>0</td><td>&nbsp;</td>
    </tr>
    <tr class="newsect secteven">
      <td>TBA (6952)</td><td>TBA</td><td>TBA</td><td>TBA</td>
      <td>5</td><td>0</td><td>5</td><td>0</td>
      <td><div class="popup consent"><div class="popupdetail">
        Instructor Consent Required
      </div></div>&nbsp;</td>
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
    assert course["vector"] == "3-0-1:4"
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


def test_fetch_text_uses_requests_headers_status_and_declared_charset(monkeypatch):
    calls = []

    class Response:
        headers = {"Content-Type": 'Text/HTML; charset = "iso-8859-1"'}
        content = "café".encode("iso-8859-1")

        def raise_for_status(self):
            calls.append("raise_for_status")

    def get(url, *, timeout, headers):
        calls.append((url, timeout, headers))
        return Response()

    monkeypatch.setattr("app.scripts.fetch_hkustgz_wcq.requests.get", get)

    assert _fetch_text("https://wcq.example/2610", timeout=4.5) == "café"
    assert calls == [
        (
            "https://wcq.example/2610",
            4.5,
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/132.0 Safari/537.36 "
                    "campusForum-scheduler-data-preparation/1.0"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        ),
        "raise_for_status",
    ]


def test_fetch_text_rejects_non_html_and_wraps_transport_errors(monkeypatch):
    class JsonResponse:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        content = b"{}"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.scripts.fetch_hkustgz_wcq.requests.get",
        lambda *args, **kwargs: JsonResponse(),
    )
    with pytest.raises(
        WCQPreparationError,
        match="expected HTML, received application/json",
    ):
        _fetch_text("https://wcq.example/not-html", timeout=1)

    def timeout(*args, **kwargs):
        raise requests.Timeout("source did not respond")

    monkeypatch.setattr("app.scripts.fetch_hkustgz_wcq.requests.get", timeout)
    with pytest.raises(
        WCQPreparationError,
        match="failed to fetch .*source did not respond",
    ):
        _fetch_text("https://wcq.example/timeout", timeout=1)


def test_committed_snapshot_preserves_every_official_vector_value():
    snapshot_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "data"
        / "pending"
        / "scheduler_offerings"
        / "26-27fall.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    vector_courses = {
        course["course_code"]: course["vector"]
        for course in snapshot["courses"]
        if "VECTOR" in course["course_info"]
    }

    assert vector_courses == {
        "IOTA6910L": "[0-0-0:0]",
        "IOTA6910M": "[3-0-0:3]",
        "UGOD6100L": "[3-0-0:3]",
    }
    assert all(
        course["vector"] == course["course_info"].get("VECTOR")
        for course in snapshot["courses"]
    )


def test_reviewed_tba_rows_are_provenance_only_and_never_scheduler_layers():
    subject_html = _page(
        "UFUG",
        UFUG_1301_WITH_UNSCHEDULED_FALLBACK
        + UFUG_1302_WITH_UNSCHEDULED_FALLBACK,
    )
    parsed = parse_subject_page(subject_html, expected_subject="UFUG")
    courses = {course["course_code"]: course for course in parsed.courses}

    assert [
        section["name"] for section in courses["UFUG1301"]["sections"]
    ] == ["L01", "LA01"]
    assert [
        section["name"] for section in courses["UFUG1302"]["sections"]
    ] == ["L01", "T01"]
    assert all(
        section["layer"] == 0
        for course in courses.values()
        for section in course["sections"]
    )
    assert [
        (section["course_code"], section["section_id"])
        for section in parsed.unscheduled_sections
    ] == [("UFUG1301", "6951"), ("UFUG1302", "6952")]

    first = parsed.unscheduled_sections[0]
    assert first == {
        "course_code": "UFUG1301",
        "semester_id": "2610",
        "source_name": "TBA",
        "source_label": "TBA (6951)",
        "section_id": "6951",
        "date_time": "TBA",
        "room": "TBA",
        "instructor": "TBA",
        "quota": 5,
        "enrol": 0,
        "avail": 5,
        "wait": 0,
        "remarks": [
            "> If the lab sessions listed in the course schedule are not "
            "suitable for your availability, or if you were unable to enroll "
            "in a session, you can contact the instructor to request access "
            'to the "TBA" section. The lab class schedule will be coordinated '
            "later based on the availability of students who select "
            '"TBA." Please pay attention to further notifications.',
            "Instructor Consent Required",
        ],
        "review_basis": (
            "The source note explicitly identifies this as an opt-in lab "
            "fallback whose meeting will be coordinated later."
        ),
        "source_row_classes": ["newsect", "sectodd"],
        "source_cell_values": first["source_cell_values"],
    }
    assert len(first["source_cell_values"]) == 9
    assert first["source_cell_values"][:8] == [
        "TBA (6951)",
        "TBA",
        "TBA",
        "TBA",
        "5",
        "0",
        "5",
        "0",
    ]
    assert "Instructor Consent Required" in first["source_cell_values"][8]

    snapshot = build_snapshot(
        index_html=UFUG_INDEX_HTML,
        term="2610",
        term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
        retrieved_at="2026-08-11T00:00:00Z",
        semester_start_date="2026-09-01",
        subject_html={"UFUG": subject_html},
    )
    assert snapshot["provenance"]["unscheduled_sections"] == (
        parsed.unscheduled_sections
    )
    omissions = [
        item
        for item in snapshot["provenance"]["approximations"]
        if item["kind"] == "unscheduled_source_section_omitted"
    ]
    assert [
        (item["course_code"], item["section_id"], item["retained_at"])
        for item in omissions
    ] == [
        ("UFUG1301", "6951", "provenance.unscheduled_sections"),
        ("UFUG1302", "6952", "provenance.unscheduled_sections"),
    ]
    assert validate_snapshot(
        snapshot,
        expected_subjects=1,
        expected_courses=2,
        expected_offered_courses=2,
        expected_sections=4,
        expected_lectures=5,
        expected_unscheduled_sections=2,
    ) == {
        "subjects": 1,
        "courses": 2,
        "offered_courses": 2,
        "sections": 4,
        "lectures": 5,
        "unscheduled_sections": 2,
    }


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("TBA (6951)", "TBA (7999)", "unreviewed unscheduled section"),
        (
            "<td>TBA</td><td>TBA</td><td>TBA</td>",
            "<td>Fr 09:00AM - 09:50AM</td><td>TBA</td><td>TBA</td>",
            "reviewed unscheduled section changed",
        ),
        (
            "The lab class schedule will be coordinated later",
            "The lab may be scheduled later",
            "reviewed unscheduled section changed",
        ),
    ],
)
def test_reviewed_tba_row_fails_closed_if_identity_or_semantics_change(
    old, new, message
):
    changed = UFUG_1301_WITH_UNSCHEDULED_FALLBACK.replace(old, new, 1)

    with pytest.raises(WCQPreparationError, match=message):
        parse_subject_page(_page("UFUG", changed), expected_subject="UFUG")


def test_unscheduled_control_total_detects_silent_provenance_loss():
    subject_html = _page(
        "UFUG",
        UFUG_1301_WITH_UNSCHEDULED_FALLBACK
        + UFUG_1302_WITH_UNSCHEDULED_FALLBACK,
    )
    snapshot = build_snapshot(
        index_html=UFUG_INDEX_HTML,
        term="2610",
        term_url="https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
        retrieved_at="2026-08-11T00:00:00Z",
        semester_start_date="2026-09-01",
        subject_html={"UFUG": subject_html},
    )
    snapshot["provenance"]["unscheduled_sections"].pop()

    with pytest.raises(
        WCQPreparationError,
        match=r"unscheduled_sections=1 \(expected 2\)",
    ):
        validate_snapshot(snapshot, expected_unscheduled_sections=2)

    assert _build_parser().parse_args([]).expected_unscheduled_sections == 2


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
        expected_unscheduled_sections=0,
    ) == {
        "subjects": 2,
        "courses": 3,
        "offered_courses": 3,
        "sections": 7,
        "lectures": 7,
        "unscheduled_sections": 0,
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
