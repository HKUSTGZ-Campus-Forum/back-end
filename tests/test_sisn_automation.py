import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.extensions import db
from app.models.course_domain import (
    CourseCatalogVersion,
    CourseOffering,
    CourseSection,
    SisnSyncRun,
)
from app.scripts.import_scheduler_offerings import create_import_app
from app.services.sisn_offerings import SisnMappingError, adapt_proxy_envelope
from app.services.sisn_proxy_client import canonical_query, sign_request
from app.services.sisn_sync import SisnSyncGuards, run_sisn_sync


def _stable_json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _schedule(day, start, end, room):
    return {
        "weekdays": [day],
        "startTime": start,
        "endTime": end,
        "venue": room,
        "facilityId": f"FAC-{room}",
        "instructors": ["Prof Test"],
        "startDt": "2026-09-01",
        "endDt": "2026-12-20",
    }


def _api_class(number, class_type, section, associated, schedules):
    return {
        "classNbr": number,
        "classType": class_type,
        "section": section,
        "associatedClass": associated,
        "enrlCap": 30,
        "enrlTot": 12,
        "waitTot": 1,
        "consent": False,
        "remarks": "",
        "reserveCap": [],
        "schedules": schedules,
    }


def _payload(include_unmapped_associated=False):
    classes = [
        _api_class("1001", "E", "G1", 1, [_schedule(1, "09:00", "10:20", "A101")]),
        _api_class("1002", "N", "U1", 1, [_schedule(3, "11:00", "12:20", "A102")]),
    ]
    if include_unmapped_associated:
        classes.append(
            _api_class("1003", "N", "U1", 9999, [_schedule(5, "14:00", "15:20", "A103")])
        )
    return {
        "status": 200,
        "message": "success",
        "courses": [{
            "crseCode": "TEST1001",
            "subject": "TEST",
            "catalogNbr": "1001",
            "crseDesc": "Test course",
            "longDesc": "Official description",
            "credit": 3,
            "preReq": "",
            "coReq": "",
            "exclusion": "",
            "attributes": [{
                "crseAttr": "TEST",
                "crseAttrDesc": "Test attribute",
                "crseAttrValue": "VALUE",
                "crseAttrValueDesc": "Test value",
            }],
            "prevCrseCode": "TEST0999",
            "classes": classes,
        }],
    }


def _envelope(payload=None):
    payload = payload or _payload()
    return {
        "schema_version": 1,
        "source": "sisn",
        "requested_term": "2610",
        "fetched_at": "2026-08-19T12:00:00+00:00",
        "payload_sha256": hashlib.sha256(_stable_json(payload).encode()).hexdigest(),
        "payload": payload,
    }


def _baseline():
    return {
        "semester_id": "2610",
        "semester_name": "2026-27 Fall",
        "semester_start_date": "2026-09-01",
        "courses": [{
            "course_code": "TEST1001",
            "course_title": "Reviewed test course",
            "course_desc": "Reviewed description",
            "credit": 3,
            "pg_course": False,
            "sections": [
                {
                    "section_id": "1001",
                    "section_type": "L",
                    "name": "L01",
                    "bundle": 1,
                    "layer": 0,
                    "is_main": True,
                },
                {
                    "section_id": "1002",
                    "section_type": "T",
                    "name": "T01",
                    "bundle": 1,
                    "layer": 1,
                    "is_main": False,
                },
            ],
        }],
    }


class FakeClient:
    def __init__(self, envelope):
        self.envelope = envelope

    def fetch_class_quota(self, *, term):
        assert term == "2610"
        return self.envelope


@pytest.fixture
def app():
    app = create_import_app("sqlite:///:memory:")
    app.config.update(TESTING=True, CACHE_TYPE="SimpleCache")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def baseline_path(tmp_path: Path):
    path = tmp_path / "reviewed-baseline.json"
    path.write_text(json.dumps(_baseline()), encoding="utf-8")
    return path


def _relaxed_guards(**overrides):
    values = {
        "min_source_courses": 1,
        "max_source_courses": 10,
        "min_source_classes": 1,
        "max_source_classes": 10,
        "min_source_schedules": 1,
        "max_source_schedules": 10,
        "min_candidate_sections": 1,
        "max_fallback_main_classes": 1,
        "max_missing_baseline_classes": 1,
        "max_omitted_unscheduled_classes": 1,
    }
    values.update(overrides)
    return SisnSyncGuards(**values)


def test_hmac_signature_has_stable_cross_runtime_contract():
    query = canonical_query({"term": "2610", "crseCode": "TEST 1001"})
    assert query == "crseCode=TEST%201001&term=2610"
    assert sign_request(
        "s" * 32,
        "1787121600",
        "fixed-nonce",
        "GET",
        "/api/internal/sisn/class-quota",
        query,
    ) == "6addc45e8d23bf8d06887e31c1adfe52a442ef7215590a4ec5d3527cdebbc871"


def test_adapter_preserves_reviewed_grouping_and_uses_official_live_fields():
    adapted = adapt_proxy_envelope(
        _envelope(),
        term="2610",
        baseline=_baseline(),
        baseline_label="reviewed-baseline.json",
    )
    sections = adapted.snapshot["courses"][0]["sections"]
    assert [(item["name"], item["bundle"], item["layer"]) for item in sections] == [
        ("L01", 1, 0),
        ("T01", 1, 1),
    ]
    assert sections[0]["quota"] == 30
    assert sections[0]["enrol"] == 12
    assert sections[0]["lectures"][0]["date_ranges"] == [{
        "start_date": "2026-09-01",
        "end_date": "2026-12-20",
        "facility_id": "FAC-A101",
    }]


def test_adapter_fails_closed_for_new_scheduled_associated_class():
    with pytest.raises(SisnMappingError, match="no reviewed WCQ mapping"):
        adapt_proxy_envelope(
            _envelope(_payload(include_unmapped_associated=True)),
            term="2610",
            baseline=_baseline(),
            baseline_label="reviewed-baseline.json",
        )


def test_sync_dry_run_apply_and_idempotent_skip(app, baseline_path):
    client = FakeClient(_envelope())
    dry_run = run_sisn_sync(
        client=client,
        term="2610",
        baseline_path=baseline_path,
        guards=_relaxed_guards(),
    )
    assert dry_run.status == "dry-run"
    assert CourseOffering.query.count() == 0

    applied = run_sisn_sync(
        client=client,
        term="2610",
        baseline_path=baseline_path,
        mode="apply",
        guards=_relaxed_guards(),
    )
    assert applied.status == "applied"
    assert CourseOffering.query.filter_by(semester_id="2610", status="offered").count() == 1
    assert CourseSection.query.filter_by(status="active").count() == 2
    version = CourseCatalogVersion.query.filter_by(source="sisn").one()
    assert version.source_metadata["previous_course_code"] == "TEST0999"
    assert version.source_metadata["attributes"][0]["crseAttr"] == "TEST"

    repeated = run_sisn_sync(
        client=client,
        term="2610",
        baseline_path=baseline_path,
        mode="apply",
        guards=_relaxed_guards(),
    )
    assert repeated.status == "skipped"
    assert SisnSyncRun.query.filter_by(status="applied").count() == 1


def test_sync_guard_records_blocked_run_without_mutating_offerings(app, baseline_path):
    result = run_sisn_sync(
        client=FakeClient(_envelope()),
        term="2610",
        baseline_path=baseline_path,
        mode="apply",
        guards=_relaxed_guards(min_source_courses=2),
    )
    assert result.status == "blocked"
    assert CourseOffering.query.count() == 0
    run = SisnSyncRun.query.one()
    assert run.status == "blocked"
    assert "outside reviewed range" in run.error_message
