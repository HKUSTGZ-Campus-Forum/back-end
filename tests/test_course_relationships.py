import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogRequirement,
    CourseCatalogVersion,
    CourseRequirementEdge,
)
from app.services.course_relationships import (
    current_rule_catalog_version,
    parse_requirement,
    relationship_summary,
)
from app.services.official_course_catalog_sync import sync_official_course_catalog_records


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    AUTO_INIT_ON_STARTUP = False
    JWT_SECRET_KEY = "test-secret-for-course-relationships"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def official_record(code, title, prerequisite=None, corequisite=None, exclusion=None):
    return {
        "crseCode": code,
        "crsePrefix": code[:4],
        "catalogNbr": code[4:],
        "crseTitle": title,
        "crseDescr": f"Description for {title}",
        "minUnits": "3.00",
        "crsePrerequisite": prerequisite,
        "crseCorequisite": corequisite,
        "crseExclusion": exclusion,
        "termCode": "2610",
        "termName": "2026-27 Fall",
        "acadYearFull": "2026-27",
        "adsSyncTime": "2026-08-24 05:35:00",
    }


def test_requirement_parser_preserves_boolean_structure_and_mixed_text():
    parsed = parse_requirement("(UFUG 1103 OR UFUG 1106) AND UFUG 2102")
    assert parsed.requirement_kind == "course"
    assert parsed.course_codes == ("UFUG1103", "UFUG1106", "UFUG2102")
    assert parsed.expression_json == {
        "op": "AND",
        "items": [
            {
                "op": "OR",
                "items": [
                    {"course_code": "UFUG1103"},
                    {"course_code": "UFUG1106"},
                ],
            },
            {"course_code": "UFUG2102"},
        ],
    }

    mixed = parse_requirement("DSAA 1001 OR FTEC 3130 for FTEC Major Only")
    assert mixed.requirement_kind == "mixed"
    assert mixed.expression_json == {}
    assert mixed.course_codes == ("DSAA1001", "FTEC3130")


def test_official_sync_is_idempotent_and_builds_reverse_downstream(app, client):
    records = [
        official_record("UFUG1103", "Calculus I"),
        official_record("UFUG1106", "Calculus IA"),
        official_record("UFUG2102", "Applied Statistics"),
        official_record(
            "UFUG2103",
            "Linear Algebra",
            prerequisite="UFUG 1103 OR UFUG 1106",
            corequisite="UFUG 2102",
        ),
        official_record("DSAA1085", "Discrete Mathematics", prerequisite="UFUG 2103"),
    ]
    with app.app_context():
        dry_run = sync_official_course_catalog_records(
            records, term="2610", min_courses=1, max_courses=10
        )
        assert dry_run["status"] == "dry-run"
        assert Course.query.count() == 0

        first = sync_official_course_catalog_records(
            records, term="2610", apply=True, min_courses=1, max_courses=10
        )
        second = sync_official_course_catalog_records(
            records, term="2610", apply=True, min_courses=1, max_courses=10
        )
        assert first["catalog_versions_created"] == 5
        assert first["requirements"] == 15
        assert second["catalog_versions_created"] == 0
        assert CourseCatalogVersion.query.count() == 5
        assert CourseCatalogRequirement.query.count() == 15
        assert CourseRequirementEdge.query.count() == 4

        stale_requirement = CourseCatalogRequirement.query.first()
        stale_requirement.parser_version = "old-parser"
        db.session.commit()
        repaired = sync_official_course_catalog_records(
            records, term="2610", apply=True, min_courses=1, max_courses=10
        )
        assert repaired["catalog_versions_created"] == 0
        assert repaired["catalog_versions_rebuilt"] == 1
        assert CourseCatalogRequirement.query.count() == 15
        assert CourseRequirementEdge.query.count() == 4

        linear_algebra = Course.query.filter_by(normalized_code="UFUG2103").one()
        summary = relationship_summary(linear_algebra)
        assert summary["provenance"]["source"] == "sis_course_catalog"
        assert summary["provenance"]["is_fallback"] is False
        assert [item["code"] for item in summary["downstream"]] == ["DSAA1085"]

    overview = client.get("/courses/by-code/UFUG2103/overview")
    assert overview.status_code == 200
    payload = overview.get_json()
    assert payload["course"]["pre_requirement"] == "UFUG 1103 OR UFUG 1106"
    assert payload["relationships"]["provenance"]["source"] == "sis_course_catalog"
    assert payload["prerequisite_summary"]["downstream"][0]["code"] == "DSAA1085"

    graph = client.get("/courses/relationships/graph")
    assert graph.status_code == 200
    graph_payload = graph.get_json()
    assert graph_payload["metadata"]["source"] == "sis_course_catalog"
    assert {item["id"] for item in graph_payload["components"]} >= {
        "UFUG1103", "UFUG1106", "UFUG2103", "DSAA1085"
    }
    assert any(item["id"].startswith("logic-prerequisite-") for item in graph_payload["components"])


def test_offering_snapshot_version_cannot_override_official_rules(app):
    with app.app_context():
        course = Course(code="TEST2205", normalized_code="TEST2205", name="Test", credits=3)
        db.session.add(course)
        db.session.flush()
        official = CourseCatalogVersion(
            course_id=course.id,
            source="sis_course_catalog",
            source_version="2610:official",
            title="Test",
            credits=3,
            pre_requirement_raw="TEST 1001",
            effective_from_semester_id="2610",
        )
        offering_snapshot = CourseCatalogVersion(
            course_id=course.id,
            source="sisn",
            source_version="2630",
            title="Test",
            credits=3,
            pre_requirement_raw="TEST 1999",
            effective_from_semester_id="2630",
        )
        db.session.add_all([official, offering_snapshot])
        db.session.commit()

        assert current_rule_catalog_version(course).id == official.id
        summary = relationship_summary(course)
        prerequisite = next(
            item for item in summary["requirements"] if item["relation_type"] == "prerequisite"
        )
        assert prerequisite["raw_text"] == "TEST 1001"
        assert prerequisite["source"] == "sis_course_catalog"
