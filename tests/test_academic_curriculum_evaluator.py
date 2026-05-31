from app.services.academic_curriculum_evaluator import evaluate_requirement_group, evaluate_requirement_program


def _course(code, status, credits=3, source="catalog"):
    return {
        "course_code": code,
        "title": code,
        "record_status": status,
        "credits": credits,
        "credit_source": source,
        "shared_majors": [],
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
