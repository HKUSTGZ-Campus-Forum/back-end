import json
import os
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup
from app.scripts.import_pending_academic_data import (
    CurriculumExpectations,
    PendingAcademicDataValidationError,
    PENDING_SCHEDULER_SUBJECTS,
    build_parser,
    file_sha256,
    load_curriculum_file,
    run_pending_curriculum_update,
    run_pending_scheduler_update,
    validate_curriculum_payload,
)
from app.scripts.import_scheduler_offerings import (
    SnapshotExpectations,
    load_offerings_file,
    snapshot_counts,
)


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
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def curriculum_payload():
    return {
        "programs": [
            {
                "code": "AI",
                "cohort": "2026",
                "name_en": "Artificial Intelligence",
                "source_url": "https://ait.hkust-gz.edu.cn/programs/undergraduate-program/",
                "source_pdf_sha256": "a" * 64,
                "source_retrieved_at": "2026-08-09",
                "total_min_credits": 120,
                "common_core_min_credits": 30,
                "major_min_credits": 85,
                "home_areas": ["Information Hub"],
                "requirement_groups": [
                    {
                        "key": "major_required",
                        "name_en": "Major Required Courses",
                        "category": "major_required",
                        "min_credits": 6,
                        "rule": {
                            "rule_tree": {
                                "type": "required",
                                "courses": ["AIAA 2205", "AIAA4490"],
                            }
                        },
                    },
                    {
                        "key": "major_electives",
                        "name_en": "Major Elective Courses",
                        "category": "major_elective",
                        "min_courses": 1,
                        "rule": {
                            "rule_tree": {
                                "type": "choose",
                                "courses": ["AIAA4001"],
                            }
                        },
                    },
                ],
            }
        ]
    }


EXPECTED_COUNTS = CurriculumExpectations(
    program_definitions=1,
    program_cohorts=1,
    requirement_groups=2,
    unique_course_codes=3,
)

PENDING_DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "pending"
REVIEWED_SCHEDULER_SHA256 = (
    "64cf81e1cabe6bef350b6be1c29206329fe22bfe7e6d820eedd812c419d347cc"
)
REVIEWED_CURRICULUM_SHA256 = (
    "a99cbe5c120ba5fcd707651f4609a6ca08e6d5bfa205734979ad6d9739f6b056"
)
REVIEWED_CURRICULUM_COUNTS = CurriculumExpectations(8, 8, 32, 292)


def write_payload(tmp_path, payload):
    path = tmp_path / "curriculum.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_reviewed_pending_scheduler_package_matches_locked_controls():
    path = PENDING_DATA_DIR / "scheduler_offerings" / "26-27fall.json"

    assert file_sha256(path) == REVIEWED_SCHEDULER_SHA256
    snapshot = load_offerings_file(path, "2610")
    assert snapshot_counts(snapshot) == SnapshotExpectations(383, 383, 801, 824)


def test_reviewed_pending_curriculum_package_matches_locked_controls():
    path = PENDING_DATA_DIR / "curriculum_requirements_2026.json"

    assert file_sha256(path) == REVIEWED_CURRICULUM_SHA256
    snapshot = load_curriculum_file(path, REVIEWED_CURRICULUM_COUNTS)
    assert snapshot.counts == REVIEWED_CURRICULUM_COUNTS


def test_validate_curriculum_payload_requires_reviewed_exact_counts():
    snapshot = validate_curriculum_payload(curriculum_payload(), EXPECTED_COUNTS)

    assert snapshot.counts == EXPECTED_COUNTS
    assert snapshot.program_groups == {
        ("AI", "2026"): {"major_required", "major_electives"}
    }

    with pytest.raises(
        PendingAcademicDataValidationError,
        match="does not match independently reviewed counts",
    ):
        validate_curriculum_payload(
            curriculum_payload(),
            CurriculumExpectations(1, 1, 3, 3),
        )


def test_validate_curriculum_payload_requires_official_source_url():
    payload = curriculum_payload()
    payload["programs"][0]["source_url"] = "https://example.com/requirements.pdf"

    with pytest.raises(
        PendingAcademicDataValidationError,
        match="official HTTPS hkust-gz.edu.cn URL",
    ):
        validate_curriculum_payload(payload, EXPECTED_COUNTS)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("home_areas", "Information Hub", "expected a list"),
        ("is_active", "false", "expected a boolean"),
        ("source_pdf_sha256", "not-a-hash", "64 lowercase hex"),
        ("source_retrieved_at", "09/08/2026", "expected an ISO date"),
    ],
)
def test_validate_curriculum_payload_rejects_malformed_program_metadata(
    field_name,
    value,
    message,
):
    payload = curriculum_payload()
    payload["programs"][0][field_name] = value

    with pytest.raises(PendingAcademicDataValidationError, match=message):
        validate_curriculum_payload(payload, EXPECTED_COUNTS)


def test_validate_curriculum_payload_rejects_invalid_sort_order():
    payload = curriculum_payload()
    payload["programs"][0]["requirement_groups"][0]["sort_order"] = "first"

    with pytest.raises(
        PendingAcademicDataValidationError,
        match="sort_order: expected a non-negative integer",
    ):
        validate_curriculum_payload(payload, EXPECTED_COUNTS)


def test_validate_curriculum_payload_rejects_duplicate_canonical_program_cohort():
    payload = curriculum_payload()
    duplicate = dict(payload["programs"][0])
    duplicate["code"] = "ai"
    payload["programs"].append(duplicate)

    with pytest.raises(
        PendingAcademicDataValidationError,
        match="duplicate canonical program/cohort",
    ):
        validate_curriculum_payload(
            payload,
            CurriculumExpectations(2, 2, 4, 3),
        )


def test_pending_curriculum_dry_run_does_not_mutate_database(app, tmp_path):
    path = write_payload(tmp_path, curriculum_payload())

    with app.app_context():
        result = run_pending_curriculum_update(
            mode="dry-run",
            file_path=path,
            expected_sha256=file_sha256(path),
            expected_counts=EXPECTED_COUNTS,
        )

        assert result.status == "dry-run"
        assert result.plan.program_rows_to_insert == 1
        assert CurriculumProgram.query.count() == 0
        assert CurriculumRequirementGroup.query.count() == 0


def test_pending_curriculum_hash_mismatch_blocks_before_import(app, tmp_path):
    path = write_payload(tmp_path, curriculum_payload())

    with app.app_context():
        result = run_pending_curriculum_update(
            mode="apply",
            file_path=path,
            expected_sha256="0" * 64,
            expected_counts=EXPECTED_COUNTS,
        )

        assert result.status == "blocked"
        assert "hash mismatch" in result.message
        assert CurriculumProgram.query.count() == 0


def test_pending_curriculum_missing_file_is_blocked_without_traceback(app, tmp_path):
    path = tmp_path / "missing.json"

    with app.app_context():
        result = run_pending_curriculum_update(
            mode="dry-run",
            file_path=path,
            expected_sha256="0" * 64,
            expected_counts=EXPECTED_COUNTS,
        )

    assert result.status == "blocked"
    assert "Unable to hash pending curriculum JSON" in result.message


def test_pending_curriculum_blocks_omitted_existing_groups(app, tmp_path):
    path = write_payload(tmp_path, curriculum_payload())

    with app.app_context():
        program = CurriculumProgram(
            code="AI",
            cohort="2026",
            name_en="Artificial Intelligence",
        )
        db.session.add(program)
        db.session.flush()
        db.session.add_all([
            CurriculumRequirementGroup(
                program_id=program.id,
                key="major_required",
                name_en="Major Required Courses",
                category="major_required",
            ),
            CurriculumRequirementGroup(
                program_id=program.id,
                key="major_electives",
                name_en="Major Elective Courses",
                category="major_elective",
            ),
            CurriculumRequirementGroup(
                program_id=program.id,
                key="legacy_group",
                name_en="Legacy",
                category="major",
            ),
        ])
        db.session.commit()

        result = run_pending_curriculum_update(
            mode="apply",
            file_path=path,
            expected_sha256=file_sha256(path),
            expected_counts=EXPECTED_COUNTS,
        )

        assert result.status == "blocked"
        assert result.plan.omitted_group_keys == ["AI/2026/legacy_group"]
        assert CurriculumRequirementGroup.query.filter_by(
            program_id=program.id,
            key="legacy_group",
        ).one()


def test_pending_curriculum_apply_requires_explicit_cli_flag(app, tmp_path):
    path = write_payload(tmp_path, curriculum_payload())
    base_args = [
        "curriculum",
        "--file", str(path),
        "--expected-sha256", file_sha256(path),
        "--expected-program-definitions", "1",
        "--expected-program-cohorts", "1",
        "--expected-requirement-groups", "2",
        "--expected-unique-course-codes", "3",
    ]

    assert build_parser().parse_args(base_args).apply is False
    assert build_parser().parse_args([*base_args, "--apply"]).apply is True


def test_pending_scheduler_is_fixed_to_2610_and_defaults_to_dry_run(
    monkeypatch,
    tmp_path,
):
    captured = {}
    sentinel = object()

    def fake_run_deploy_scheduler_offering_update(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "app.scripts.import_pending_academic_data.run_deploy_scheduler_offering_update",
        fake_run_deploy_scheduler_offering_update,
    )
    path = tmp_path / "26-27fall.json"
    path.write_text(json.dumps({
        "semester_start_date": "2026-09-01",
        "provenance": {
            "source_name": "HKUST-GZ Class Schedule & Quota",
            "term_url": "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
            "retrieved_at": "2026-08-09T12:00:00Z",
            "subjects": [
                {
                    "code": code,
                    "url": (
                        "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/index.php"
                        f"?term=2610&subject={code}"
                    ),
                }
                for code in sorted(PENDING_SCHEDULER_SUBJECTS)
            ],
        },
    }), encoding="utf-8")
    expected_counts = SnapshotExpectations(1, 1, 1, 1)

    result = run_pending_scheduler_update(
        mode="dry-run",
        file_path=path,
        expected_sha256="a" * 64,
        expected_counts=expected_counts,
    )
    args = build_parser().parse_args([
        "scheduler",
        "--file", str(path),
        "--expected-sha256", "a" * 64,
        "--expected-courses", "1",
        "--expected-offered-courses", "1",
        "--expected-sections", "1",
        "--expected-lectures", "1",
    ])

    assert result is sentinel
    assert captured == {
        "mode": "dry-run",
        "file_path": path,
        "expected_semester_id": "2610",
        "expected_sha256": "a" * 64,
        "expected_counts": expected_counts,
    }
    assert args.apply is False


def test_pending_scheduler_requires_complete_official_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.scripts.import_pending_academic_data.run_deploy_scheduler_offering_update",
        lambda **_kwargs: object(),
    )
    path = tmp_path / "26-27fall.json"
    path.write_text(json.dumps({
        "semester_start_date": "2026-09-01",
        "provenance": {
            "source_name": "HKUST-GZ Class Schedule & Quota",
            "term_url": "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
            "retrieved_at": "2026-08-09T12:00:00Z",
            "subjects": [],
        },
    }), encoding="utf-8")

    result = run_pending_scheduler_update(
        mode="dry-run",
        file_path=path,
        expected_sha256=file_sha256(path),
        expected_counts=SnapshotExpectations(1, 1, 1, 1),
    )

    assert result.status == "blocked"
    assert "reviewed 2610 subjects" in result.message
