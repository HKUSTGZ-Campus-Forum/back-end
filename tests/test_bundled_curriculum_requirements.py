import json
from pathlib import Path

import pytest


PAYLOAD = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "curriculum_requirements.json").read_text(encoding="utf-8")
)


def _program(code, cohort):
    for program in PAYLOAD["programs"]:
        cohorts = program.get("cohorts") or [program.get("cohort")]
        if program["code"] == code and cohort in cohorts:
            return program
    raise AssertionError(f"missing program {code} {cohort}")


def _group(code, cohort, key):
    program = _program(code, cohort)
    return next(group for group in program["requirement_groups"] if group["key"] == key)


def _leaf(group, key):
    def walk(node):
        if node.get("key") == key:
            return node
        for child in node.get("children", []):
            found = walk(child)
            if found:
                return found
        return None

    found = walk(group["rule"]["rule_tree"])
    assert found is not None, f"missing leaf {key}"
    return found


def _leaf_keys(group):
    return [leaf["key"] for leaf in group["rule"]["rule_tree"]["children"]]


def test_bundled_payload_includes_common_core_for_every_program():
    missing = [
        (program["code"], program.get("cohorts") or [program.get("cohort")])
        for program in PAYLOAD["programs"]
        if not any(group["key"] == "common_core" for group in program["requirement_groups"])
    ]
    assert missing == []


def test_ai_2025_common_core_matches_broadening_table():
    common_core = _group("AI", "2025", "common_core")
    leaves = {leaf["key"]: leaf for leaf in common_core["rule"]["rule_tree"]["children"]}

    assert leaves["broadening_arts"]["min_credits"] == 3
    assert leaves["broadening_humanities"]["min_credits"] == 3
    assert leaves["broadening_social_analysis"]["min_credits"] == 3
    assert leaves["broadening_elective"]["min_credits"] == 3
    assert "broadening_science" not in leaves
    assert "broadening_technology" not in leaves
    assert leaves["broadening_elective"]["constraints"] == [
        {"type": "exclude_course_areas", "values": ["S", "T"]}
    ]


def test_ftec_2025_common_core_matches_broadening_table():
    common_core = _group("FTEC", "2025", "common_core")
    leaves = {leaf["key"]: leaf for leaf in common_core["rule"]["rule_tree"]["children"]}

    assert leaves["broadening_arts"]["min_credits"] == 3
    assert leaves["broadening_humanities"]["min_credits"] == 3
    assert leaves["broadening_science"]["min_credits"] == 3
    assert leaves["broadening_elective"]["min_credits"] == 3
    assert "broadening_technology" not in leaves
    assert "broadening_social_analysis" not in leaves
    assert leaves["broadening_elective"]["constraints"] == [
        {"type": "exclude_course_areas", "values": ["T", "SA"]}
    ]


def test_roas_2025_common_core_matches_broadening_table():
    common_core = _group("ROAS", "2025", "common_core")
    leaves = {leaf["key"]: leaf for leaf in common_core["rule"]["rule_tree"]["children"]}

    assert leaves["broadening_arts"]["min_credits"] == 3
    assert leaves["broadening_humanities"]["min_credits"] == 3
    assert leaves["broadening_science"]["min_credits"] == 3
    assert leaves["broadening_social_analysis"]["min_credits"] == 3
    assert "broadening_technology" not in leaves
    assert "broadening_elective" not in leaves


def test_ai_2025_linear_algebra_is_not_fixed():
    group = _group("AI", "2025", "fundamental_courses")
    fixed = _leaf(group, "fixed_courses")
    linear_algebra = _leaf(group, "linear_algebra")
    assert "UFUG2103" not in fixed["courses"]
    assert linear_algebra["courses"] == ["UFUG2102", "UFUG2103"]


def test_amat_2024_chemistry_rule_matches_pdf():
    group = _group("AMAT", "2024", "fundamental_courses")
    fixed = _leaf(group, "fixed_courses")
    chemistry = _leaf(group, "chemistry")
    assert "UFUG1303" in fixed["courses"]
    assert chemistry["courses"] == ["UFUG1301", "UFUG1302"]


def test_mics_2025_major_electives_require_eleven_courses_and_31_credits():
    electives = _leaf(_group("MICS", "2025", "major_electives"), "major_electives")
    assert electives["min_courses"] == 11
    assert electives["min_credits"] == 31


def test_amat_2024_major_electives_require_four_amat_courses():
    electives = _leaf(_group("AMAT", "2024", "major_electives"), "major_electives")
    assert electives["constraints"] == [{"type": "course_prefix", "value": "AMAT", "min_courses": 4}]


def test_bundled_payload_covers_every_audited_pdf_source():
    assert {program["source_file"] for program in PAYLOAD["programs"]} == {
        "AI 23 24.pdf",
        "AI 25.pdf",
        "Curriculum Requirements - DSBD - 2023 cohort.pdf",
        "DSA 24.pdf",
        "DSA 25.pdf",
        "AMAT Curriculum Updated.pdf",
        "Curriculum Requirement - MSE - 2024 cohort - updated on May 19.pdf",
        "SMMG 23 24.pdf",
        "SMMG 25.pdf",
        "FTEC 25.pdf",
        "ROAS 25.pdf",
        "MICS 25.pdf",
        "SEEN 26.pdf",
    }


def test_bundled_payload_uses_rule_trees_without_flattened_rule_keys():
    for program in PAYLOAD["programs"]:
        for group in program["requirement_groups"]:
            assert set(group["rule"]) == {"rule_tree"}


@pytest.mark.parametrize(
    ("code", "cohort", "keys"),
    [
        ("AI", "2023", ["fixed_courses", "programming", "calculus_i", "calculus_ii", "linear_algebra", "science"]),
        ("AI", "2024", ["fixed_courses", "programming", "calculus_i", "calculus_ii", "linear_algebra", "science"]),
        ("AI", "2025", ["fixed_courses", "calculus_i", "calculus_ii", "linear_algebra", "science"]),
        ("AI", "2026", ["fixed_courses", "calculus_i", "calculus_ii", "linear_algebra", "science"]),
        ("DSA", "2023", ["fixed_courses", "calculus_i", "calculus_ii", "science", "interdisciplinary", "discrete_math"]),
        ("DSA", "2024", ["fixed_courses", "calculus_i", "calculus_ii", "science", "intro_cs", "interdisciplinary", "discrete_math", "linear_algebra"]),
        ("DSA", "2025", ["fixed_courses", "calculus_i", "calculus_ii", "science", "intro_cs", "discrete_math", "linear_algebra"]),
        ("DSA", "2026", ["fixed_courses", "calculus_i", "calculus_ii", "science", "intro_cs", "discrete_math", "linear_algebra"]),
        ("AMAT", "2024", ["fixed_courses", "programming", "calculus_i", "calculus_ii", "chemistry", "physics_i", "physics_ii", "linear_algebra"]),
        ("AMAT", "2025", ["fixed_courses", "programming", "calculus_i", "calculus_ii", "physics_i", "physics_ii", "linear_algebra"]),
        ("SMMG", "2023", ["fixed_courses", "programming", "chemistry", "physics_i", "physics_ii", "calculus_i", "calculus_ii", "linear_algebra"]),
        ("SMMG", "2024", ["fixed_courses", "programming", "chemistry", "physics_i", "physics_ii", "calculus_i", "calculus_ii", "linear_algebra"]),
        ("SMMG", "2025", ["fixed_courses", "intro_programming", "advanced_programming", "science", "physics_i", "physics_ii", "calculus_i", "calculus_ii", "linear_algebra"]),
        ("SMMG", "2026", ["fixed_courses", "intro_programming", "advanced_programming", "science", "physics_i", "physics_ii", "calculus_i", "calculus_ii", "linear_algebra"]),
        ("FTEC", "2025", ["fixed_courses", "calculus_i", "calculus_ii", "programming"]),
        ("ROAS", "2025", ["fixed_courses", "programming", "physics_i", "physics_ii", "calculus_i", "calculus_ii", "linear_algebra"]),
        ("MICS", "2025", ["fixed_courses", "calculus_i", "calculus_ii", "mathematics", "physics_i", "physics_ii"]),
        ("SEE", "2026", ["fixed_courses", "programming", "physics_i", "physics_ii", "calculus_i", "calculus_ii"]),
    ],
)
def test_fundamental_rule_tree_matches_pdf_leaf_structure(code, cohort, keys):
    assert _leaf_keys(_group(code, cohort, "fundamental_courses")) == keys


@pytest.mark.parametrize(
    ("code", "cohort", "keys"),
    [
        ("DSA", "2023", ["fixed_courses", "intro_major", "machine_learning"]),
        ("DSA", "2024", ["fixed_courses", "intro_major", "machine_learning"]),
        ("DSA", "2025", ["fixed_courses", "intro_major", "machine_learning"]),
        ("DSA", "2026", ["fixed_courses", "intro_major", "machine_learning"]),
        ("AMAT", "2024", ["fixed_courses", "capstone"]),
        ("AMAT", "2025", ["fixed_courses", "capstone"]),
        ("SMMG", "2023", ["fixed_courses", "training", "capstone"]),
        ("SMMG", "2024", ["fixed_courses", "training", "capstone"]),
        ("SMMG", "2025", ["fixed_courses", "training", "probability", "capstone"]),
        ("SMMG", "2026", ["fixed_courses", "training", "probability", "capstone"]),
        ("SEE", "2026", ["fixed_courses", "materials"]),
    ],
)
def test_major_required_rule_tree_matches_pdf_leaf_structure(code, cohort, keys):
    assert _leaf_keys(_group(code, cohort, "major_required")) == keys


@pytest.mark.parametrize(
    ("code", "cohort", "min_courses", "min_credits"),
    [
        ("AI", "2023", 8, 24), ("AI", "2024", 8, 24), ("AI", "2025", 8, 24), ("AI", "2026", 8, 24),
        ("DSA", "2023", 8, 24), ("DSA", "2024", 8, 24), ("DSA", "2025", 8, 24), ("DSA", "2026", 8, 24),
        ("AMAT", "2024", 5, 15), ("AMAT", "2025", 5, 15),
        ("SMMG", "2023", None, 18), ("SMMG", "2024", None, 18), ("SMMG", "2025", None, 18), ("SMMG", "2026", None, 18),
        ("FTEC", "2025", 5, 15), ("ROAS", "2025", 7, 21), ("MICS", "2025", 11, 31), ("SEE", "2026", 7, 21),
    ],
)
def test_major_elective_rule_tree_matches_pdf_thresholds(code, cohort, min_courses, min_credits):
    electives = _leaf(_group(code, cohort, "major_electives"), "major_electives")
    assert electives.get("min_courses") == min_courses
    assert electives["min_credits"] == min_credits
