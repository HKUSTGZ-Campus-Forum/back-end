from app.services.scheduler_policy import course_credit_policy, course_selection_policy


def module(code, remarks, *, layer):
    return {
        "section_type": code,
        "remarks": remarks,
        "layer": layer,
    }


def test_ctdl_selection_is_required_m01_plus_any_two_of_six_electives():
    policy = course_selection_policy(
        "UCUG 1000",
        [
            module("M01", "Basics · KLMS module credit: 1", layer=0),
            module("M02", "Social Science · KLMS module credit: 1", layer=1),
            module("M03", "Entrepreneurship · KLMS module credit: 1", layer=1),
            module("M04", "Science · KLMS module credit: 1", layer=1),
            module("M05", "Engineering · KLMS module credit: 1", layer=2),
            module("M06", "Business · KLMS module credit: 1", layer=2),
            module("M07", "Technology · KLMS module credit: 1", layer=2),
        ],
    )

    assert policy["kind"] == "module"
    assert policy["groups"] == [
        {
            "id": "required",
            "role": "required",
            "min_select": 1,
            "max_select": 1,
            "module_codes": ["M01"],
        },
        {
            "id": "electives",
            "role": "elective",
            "min_select": 2,
            "max_select": 2,
            "module_codes": ["M02", "M03", "M04", "M05", "M06", "M07"],
        },
    ]
    assert policy["modules"][4] == {
        "code": "M05",
        "title": "Engineering",
        "credit": 1,
        "available": True,
    }


def test_ucug1002_requires_m01_and_one_elective():
    policy = course_selection_policy("UCUG1002", [])
    assert policy["groups"][0]["module_codes"] == ["M01"]
    assert policy["groups"][1]["module_codes"] == ["M02", "M03", "M04", "M05", "M06"]
    assert policy["groups"][1]["min_select"] == policy["groups"][1]["max_select"] == 1


def test_moes_credit_is_academic_credit_but_not_semester_load_credit():
    assert course_credit_policy("MOES", 3) == {
        "credit": 3,
        "counts_toward_term_load": False,
        "term_load_credit": 0,
    }
    assert course_credit_policy("AIAA", 3)["term_load_credit"] == 3
