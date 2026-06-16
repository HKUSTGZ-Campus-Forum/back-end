import signal

import pytest

from app.services.academic_curriculum_evaluator import evaluate_requirement_group, evaluate_requirement_program


def _course(code, status, credits=3, source="catalog", area=None):
    return {
        "course_code": code,
        "title": code,
        "record_status": status,
        "credits": credits,
        "credit_source": source,
        "shared_majors": [],
        "area": area,
    }


def test_evaluator_keeps_independent_choice_groups_separate():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "calculus_i", "min_courses": 1, "courses": ["UFUG1102", "UFUG1105"]},
                {"type": "choose", "key": "calculus_ii", "min_courses": 1, "courses": ["UFUG1103", "UFUG1106"]},
                {
                    "type": "choose",
                    "key": "science",
                    "min_courses": 2,
                    "min_credits": 6,
                    "courses": ["UFUG1301", "UFUG1302", "UFUG1401", "UFUG1501"],
                },
            ],
        }
    }
    courses = {
        "UFUG1105": _course("UFUG1105", "completed"),
        "UFUG1106": _course("UFUG1106", "in_progress"),
        "UFUG1301": _course("UFUG1301", "completed"),
        "UFUG1501": _course("UFUG1501", "planned"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert [section["key"] for section in result["sections"]] == ["calculus_i", "calculus_ii", "science"]
    assert result["current"]["satisfied"] is False
    assert result["projected"]["satisfied"] is True
    assert result["sections"][2]["current"]["counted_courses"] == 1
    assert result["sections"][2]["projected"]["counted_courses"] == 2


def test_evaluator_reports_planned_course_without_counting_it_as_current():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "programming",
            "min_courses": 1,
            "courses": ["UFUG1601", "UFUG2601"],
        }
    }
    courses = {"UFUG2601": _course("UFUG2601", "planned", credits=4)}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    assert result["projected"]["satisfied"] is True
    cells = {cell["course_code"]: cell for cell in result["sections"][0]["cells"]}
    assert cells["UFUG2601"]["record_status"] == "planned"
    assert cells["UFUG2601"]["allocation_status"] == "planned"


def test_evaluator_counts_surplus_courses_in_satisfied_choice_group():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "science",
            "min_courses": 1,
            "courses": ["UFUG1301", "UFUG1302", "UFUG1401", "UFUG1501"],
        }
    }
    courses = {
        "UFUG1301": _course("UFUG1301", "completed"),
        "UFUG1501": _course("UFUG1501", "completed"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    assert result["current"]["counted_courses"] == 2
    assert result["sections"][0]["current"]["counted_courses"] == 2
    cells = {cell["course_code"]: cell for cell in result["sections"][0]["cells"]}
    assert cells["UFUG1301"]["allocation_status"] == "counted"
    assert cells["UFUG1501"]["allocation_status"] == "counted"


def test_evaluator_does_not_reuse_one_course_across_two_choice_leaves():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "strict", "min_courses": 1, "courses": ["AMAT2050"]},
                {"type": "choose", "key": "broad", "min_courses": 1, "courses": ["AMAT2050", "AMAT2320"]},
            ],
        }
    }
    courses = {"AMAT2050": _course("AMAT2050", "completed")}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    counted = [
        cell["course_code"]
        for section in result["sections"]
        for cell in section["cells"]
        if cell["allocation_status"] == "counted"
    ]
    assert counted == ["AMAT2050"]


def test_evaluator_uses_global_assignment_for_overlapping_pools():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "broad", "min_courses": 1, "courses": ["AMAT2050", "AMAT2320"]},
                {"type": "choose", "key": "strict", "min_courses": 1, "courses": ["AMAT2050"]},
            ],
        }
    }
    courses = {
        "AMAT2050": _course("AMAT2050", "completed"),
        "AMAT2320": _course("AMAT2320", "completed"),
    }

    result = evaluate_requirement_group(rule, courses)

    allocations = {
        section["key"]: [
            cell["course_code"]
            for cell in section["cells"]
            if cell["allocation_status"] == "counted"
        ]
        for section in result["sections"]
    }
    assert result["current"]["satisfied"] is True
    assert allocations == {"broad": ["AMAT2320"], "strict": ["AMAT2050"]}


def test_evaluator_enforces_credit_and_prefix_constraints():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "min_courses": 5,
            "min_credits": 15,
            "courses": ["AMAT1510", "AMAT2050", "AMAT2320", "AMAT2380", "SEEN3020"],
            "constraints": [{"type": "course_prefix", "value": "AMAT", "min_courses": 4}],
        }
    }
    courses = {
        code: _course(code, "completed")
        for code in ["AMAT1510", "AMAT2050", "AMAT2320", "AMAT2380", "SEEN3020"]
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    assert result["current"]["counted_courses"] == 5
    assert result["current"]["counted_credits"] == 15


def test_evaluator_warns_when_credit_threshold_cannot_be_proven():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "min_courses": 1,
            "min_credits": 3,
            "courses": ["MICS1010"],
        }
    }
    courses = {"MICS1010": _course("MICS1010", "completed", credits=None, source=None)}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    assert result["warnings"] == ["missing_credit:MICS1010"]


def test_evaluator_supports_credit_only_elective_thresholds():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "kind": "elective",
            "min_credits": 6,
            "courses": ["SMMG2030", "SMMG2640"],
        }
    }
    courses = {
        "SMMG2030": _course("SMMG2030", "completed"),
        "SMMG2640": _course("SMMG2640", "completed"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    assert result["current"]["required_courses"] is None
    assert result["current"]["counted_credits"] == 6
    assert result["current"]["required_credits"] == 6


def test_evaluator_prevents_reuse_across_required_and_elective_groups():
    groups = [
        {
            "key": "major_required",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_choice",
                    "min_courses": 1,
                    "courses": ["AIAA2205", "DSAA1001"],
                }
            },
        },
        {
            "key": "major_electives",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
    ]
    courses = {"AIAA2205": _course("AIAA2205", "completed")}

    result = evaluate_requirement_program(groups, courses)

    assert result[0]["current"]["satisfied"] is True
    assert result[1]["current"]["satisfied"] is False


def test_evaluator_allows_explicit_leaf_reuse():
    groups = [
        {
            "key": "major_required",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_choice",
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
        {
            "key": "major_electives",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "allow_reuse": True,
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
    ]
    courses = {"AIAA2205": _course("AIAA2205", "completed")}

    result = evaluate_requirement_program(groups, courses)

    assert result[0]["current"]["satisfied"] is True
    assert result[1]["current"]["satisfied"] is True


def test_evaluator_enforces_common_core_course_area_constraints():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "broadening_arts",
            "kind": "elective",
            "min_credits": 3,
            "courses": ["UCUG1500", "UCUG1600"],
            "constraints": [{"type": "course_area", "value": "A", "min_credits": 3}],
        }
    }
    courses = {
        "UCUG1500": _course("UCUG1500", "completed", area="A"),
        "UCUG1600": _course("UCUG1600", "completed", area="H"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    counted = [
        cell["course_code"]
        for cell in result["sections"][0]["cells"]
        if cell["allocation_status"] == "counted"
    ]
    assert counted == ["UCUG1500"]


def test_evaluator_excludes_home_areas_from_common_core_broadening_electives():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "broadening_elective",
            "kind": "elective",
            "min_credits": 3,
            "courses": ["UCUG1702", "UCUG1807"],
            "constraints": [{"type": "exclude_course_areas", "values": ["S", "T"]}],
        }
    }
    courses = {
        "UCUG1702": _course("UCUG1702", "completed", area="S"),
        "UCUG1807": _course("UCUG1807", "completed", area="SA"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    counted = [
        cell["course_code"]
        for cell in result["sections"][0]["cells"]
        if cell["allocation_status"] == "counted"
    ]
    assert counted == ["UCUG1807"]


def test_evaluator_handles_large_common_core_choice_pools_quickly():
    courses = {
        **{f"UCUG15{i:02d}": _course(f"UCUG15{i:02d}", "completed", area="A") for i in range(10)},
        **{f"UCUG16{i:02d}": _course(f"UCUG16{i:02d}", "completed", area="H") for i in range(5)},
        **{f"UCUG18{i:02d}": _course(f"UCUG18{i:02d}", "completed", area="SA") for i in range(10)},
        **{f"UCUG23{i:02d}": _course(f"UCUG23{i:02d}", "completed", area="UxOP") for i in range(10)},
    }
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {
                    "type": "choose",
                    "key": "arts",
                    "kind": "elective",
                    "min_credits": 3,
                    "courses": [code for code, course in courses.items() if course["area"] == "A"],
                    "constraints": [{"type": "course_area", "value": "A", "min_credits": 3}],
                },
                {
                    "type": "choose",
                    "key": "humanities",
                    "kind": "elective",
                    "min_credits": 3,
                    "courses": [code for code, course in courses.items() if course["area"] == "H"],
                    "constraints": [{"type": "course_area", "value": "H", "min_credits": 3}],
                },
                {
                    "type": "choose",
                    "key": "social_analysis",
                    "kind": "elective",
                    "min_credits": 3,
                    "courses": [code for code, course in courses.items() if course["area"] == "SA"],
                    "constraints": [{"type": "course_area", "value": "SA", "min_credits": 3}],
                },
                {
                    "type": "choose",
                    "key": "experiencing",
                    "kind": "elective",
                    "min_credits": 3,
                    "courses": list(courses),
                },
            ],
        }
    }

    def timeout_handler(_signum, _frame):
        raise TimeoutError("common core evaluation timed out")

    previous = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        result = evaluate_requirement_group(rule, courses)
    except TimeoutError as exc:
        pytest.fail(str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert result["current"]["satisfied"] is True
    assert result["current"]["counted_credits"] >= 12
