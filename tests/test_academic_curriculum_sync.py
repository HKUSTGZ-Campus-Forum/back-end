import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_sync_curriculum_requirements_upserts_and_removes_stale_groups(app):
    from app.services.academic_curriculum_sync import sync_curriculum_requirements_from_payload

    payload = {
        "programs": [
            {
                "code": "ai",
                "cohort": "2025",
                "name_en": "Artificial Intelligence",
                "name_zh": "人工智能",
                "total_min_credits": 120,
                "common_core_min_credits": 30,
                "major_min_credits": 85,
                "home_areas": ["Information Hub"],
                "requirement_groups": [
                    {
                        "key": "major_required",
                        "name_en": "Major Required Courses",
                        "name_zh": "专业必修课程",
                        "category": "major_required",
                        "min_credits": 40,
                        "min_courses": 13,
                        "rule": {"required_courses": ["AIAA1010", "AIAA2205"]},
                        "sort_order": 20,
                    }
                ],
            }
        ]
    }

    with app.app_context():
        first = sync_curriculum_requirements_from_payload(payload)
        program = CurriculumProgram.query.filter_by(code="AI", cohort="2025").one()
        group = CurriculumRequirementGroup.query.filter_by(program_id=program.id, key="major_required").one()

        assert first == {"programs_upserted": 1, "groups_upserted": 1, "groups_removed": 0, "programs_skipped": 0}
        assert program.name_zh == "人工智能"
        assert program.major_min_credits == 85
        assert program.home_areas == ["Information Hub"]
        assert group.min_courses == 13
        assert group.rule == {"required_courses": ["AIAA1010", "AIAA2205"]}

        stale = CurriculumRequirementGroup(
            program_id=program.id,
            key="stale",
            name_en="Stale",
            category="major",
            rule={"courses": ["OLD1000"]},
        )
        db.session.add(stale)
        db.session.commit()

        payload["programs"][0]["requirement_groups"][0]["min_courses"] = 2
        second = sync_curriculum_requirements_from_payload(payload)
        updated = CurriculumRequirementGroup.query.filter_by(program_id=program.id, key="major_required").one()

        assert second == {"programs_upserted": 1, "groups_upserted": 1, "groups_removed": 1, "programs_skipped": 0}
        assert updated.min_courses == 2
        assert CurriculumRequirementGroup.query.filter_by(program_id=program.id, key="stale").first() is None


def test_bundled_curriculum_payload_contains_official_ai_requirement_rows(app):
    from app.services.academic_curriculum_sync import sync_curriculum_requirements_from_file

    with app.app_context():
        result = sync_curriculum_requirements_from_file()
        program = CurriculumProgram.query.filter_by(code="AI", cohort="2025").one()
        group = CurriculumRequirementGroup.query.filter_by(program_id=program.id, key="major_required").one()

    assert result["programs_upserted"] >= 8
    assert program.name_en == "Artificial Intelligence"
    assert program.name_zh == "人工智能"
    assert program.total_min_credits == 120
    assert program.major_min_credits == 85
    assert "AIAA2205" in group.rule["required_courses"]
    assert "AIAA4490" in group.rule["required_courses"]


def test_sync_curriculum_requirements_expands_multiple_cohorts(app):
    from app.services.academic_curriculum_sync import sync_curriculum_requirements_from_payload

    payload = {
        "programs": [
            {
                "code": "DSBD",
                "cohorts": ["2025", "2026"],
                "name_en": "Data Science and Big Data Technology",
                "name_zh": "数据科学与大数据",
                "requirement_groups": [
                    {
                        "key": "major_electives",
                        "name_en": "Major Elective Courses",
                        "category": "major_elective",
                        "min_courses": 8,
                        "rule": {"electives": ["DSAA4011"]},
                    }
                ],
            }
        ]
    }

    with app.app_context():
        result = sync_curriculum_requirements_from_payload(payload)
        programs = CurriculumProgram.query.filter_by(code="DSA").order_by(CurriculumProgram.cohort.asc()).all()

    assert result["programs_upserted"] == 2
    assert [program.cohort for program in programs] == ["2025", "2026"]
