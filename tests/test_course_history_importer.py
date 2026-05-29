from app.services.course_history_importer import parse_course_history_text


def test_parse_tabular_course_history_text():
    pasted = """Course\tDescription\tTerm\tGrade\tUnits\tStatus
AIAA 1010\tAcad. Orient for AI Ss\t2024-25 Spring\tP\t1.00
AIAA 2205\tIntroduction to AI\t2024-25 Summer\tA+\t3.00
AIAA 2711\tMathematics for AI\t2025-26 Spring\t\t3.00
UFUG 2601\tC++ Programming\t2024-25 Fall\tB+\t4.00
"""

    rows = parse_course_history_text(pasted)

    assert [row["course_code"] for row in rows] == ["AIAA 1010", "AIAA 2205", "AIAA 2711", "UFUG 2601"]
    assert rows[0]["status"] == "completed"
    assert rows[1]["grade"] == "A+"
    assert rows[2]["status"] == "planned"
    assert rows[2]["needs_review"] is True
    assert rows[3]["units"] == 4.0


def test_parse_space_separated_rows_from_dirty_copy():
    pasted = """AIAA 2205 Introduction to AI 2024-25 Summer A+ 3.00
UCUG 1001 Habits, Mindsets, and Wellness 2024-25 Spring P 3.00
UCUG 1807 Thinking like an Economist I 2025-26 Spring 3.00
"""

    rows = parse_course_history_text(pasted)

    assert len(rows) == 3
    assert rows[0]["course_code"] == "AIAA 2205"
    assert rows[0]["term_label"] == "2024-25 Summer"
    assert rows[1]["grade"] == "P"
    assert rows[2]["grade"] is None
    assert rows[2]["status"] == "planned"
