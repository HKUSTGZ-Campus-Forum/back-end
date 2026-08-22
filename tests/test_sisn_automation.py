import hashlib
import json
import base64
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
from app.services.sisn_push_auth import canonical_push_message
from app.services.sisn_sync import (
    SisnSyncDuplicateRequest,
    SisnSyncGuards,
    run_sisn_sync,
)


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
                    "lectures": [{
                        "day": 2,
                        "start_time": "1300",
                        "end_time": "1420",
                        "room": "Reviewed Room",
                        "instructor": "Reviewed Instructor",
                    }],
                },
                {
                    "section_id": "1002",
                    "section_type": "T",
                    "name": "T01",
                    "bundle": 1,
                    "layer": 1,
                    "is_main": False,
                    "lectures": [{
                        "day": 4,
                        "start_time": "1500",
                        "end_time": "1620",
                        "room": "Reviewed Lab",
                        "instructor": "Reviewed TA",
                    }],
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
        "max_baseline_meeting_fallback_sections": 1,
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


def test_adapter_preserves_reviewed_meeting_when_sisn_schedule_is_empty():
    payload = _payload()
    payload["courses"][0]["classes"][1]["schedules"] = []

    adapted = adapt_proxy_envelope(
        _envelope(payload),
        term="2610",
        baseline=_baseline(),
        baseline_label="reviewed-baseline.json",
    )

    tutorial = adapted.snapshot["courses"][0]["sections"][1]
    assert tutorial["lectures"] == [{
        "day": 4,
        "start_time": 1500,
        "end_time": 1620,
        "room": "Reviewed Lab",
        "instructor": "Reviewed TA",
        "facility_id": None,
        "date_ranges": [],
    }]
    assert adapted.counts["baseline_meeting_fallback_sections"] == 1
    assert adapted.snapshot["provenance"]["baseline_meeting_fallbacks"] == [{
        "course_code": "TEST1001",
        "section_id": "1002",
        "section_name": "T01",
        "meeting_count": 1,
    }]
    assert adapted.warnings == [
        "preserved reviewed WCQ meetings for 1 class whose SISN schedules were empty"
    ]


def test_sync_guard_blocks_excessive_baseline_meeting_fallbacks(app, baseline_path):
    payload = _payload()
    payload["courses"][0]["classes"][1]["schedules"] = []

    result = run_sisn_sync(
        client=FakeClient(_envelope(payload)),
        term="2610",
        baseline_path=baseline_path,
        guards=_relaxed_guards(max_baseline_meeting_fallback_sections=0),
    )

    assert result.status == "blocked"
    run = SisnSyncRun.query.one()
    assert "baseline_meeting_fallback_sections=1 above reviewed maximum 0" in run.error_message


def test_official_course_titles_can_exceed_legacy_abbreviation_width():
    payload = _payload()
    official_title = "Advanced Economic Analysis of Cities and the Environment"
    assert len(official_title) > 48
    payload["courses"][0]["crseDesc"] = official_title
    adapted = adapt_proxy_envelope(
        _envelope(payload),
        term="2610",
        baseline=_baseline(),
        baseline_label="reviewed-baseline.json",
    )
    assert adapted.snapshot["courses"][0]["course_title_abbr"] == official_title


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


def test_sync_rejects_duplicate_request_id(app, baseline_path):
    client = FakeClient(_envelope())
    run_sisn_sync(
        client=client,
        term="2610",
        baseline_path=baseline_path,
        request_id="push-fixed-request-id",
        guards=_relaxed_guards(),
    )
    with pytest.raises(SisnSyncDuplicateRequest):
        run_sisn_sync(
            client=client,
            term="2610",
            baseline_path=baseline_path,
            request_id="push-fixed-request-id",
            guards=_relaxed_guards(),
        )


def _push_headers(private_key, body, *, nonce="fixed_nonce_12345678901234567890", timestamp=None):
    timestamp = str(timestamp or int(time.time()))
    signature = private_key.sign(canonical_push_message(
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    ))
    return {
        "Content-Type": "application/json",
        "X-UniKorn-Timestamp": timestamp,
        "X-UniKorn-Nonce": nonce,
        "X-UniKorn-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }


def test_signed_push_endpoint_applies_and_rejects_replay(app, baseline_path, tmp_path):
    from app.routes.scheduler import bp as scheduler_bp

    private_key = Ed25519PrivateKey.generate()
    public_key_path = tmp_path / "sisn-push-public.pem"
    public_key_path.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    app.register_blueprint(scheduler_bp)
    app.config.update({
        "SISN_PUSH_INGEST_ENABLED": True,
        "SISN_PUSH_PUBLIC_KEY_PATH": str(public_key_path),
        "SISN_PUSH_MAX_BODY_BYTES": 8 * 1024 * 1024,
        "SISN_PUSH_MAX_AGE_SECONDS": 300,
        "SISN_SYNC_TERM": "2610",
        "SISN_SYNC_BASELINE_PATH": str(baseline_path),
        "SISN_SYNC_ARCHIVE_DIR": "",
        "SISN_SYNC_ARCHIVE_RETENTION_FILES": 3,
        "SISN_SYNC_MIN_SOURCE_COURSES": 1,
        "SISN_SYNC_MAX_SOURCE_COURSES": 10,
        "SISN_SYNC_MIN_SOURCE_CLASSES": 1,
        "SISN_SYNC_MAX_SOURCE_CLASSES": 10,
        "SISN_SYNC_MIN_SOURCE_SCHEDULES": 1,
        "SISN_SYNC_MAX_SOURCE_SCHEDULES": 10,
        "SISN_SYNC_MIN_CANDIDATE_SECTIONS": 1,
        "SISN_SYNC_MAX_FALLBACK_MAIN_CLASSES": 1,
        "SISN_SYNC_MAX_MISSING_BASELINE_CLASSES": 1,
        "SISN_SYNC_MAX_OMITTED_UNSCHEDULED_CLASSES": 1,
        "SISN_SYNC_MAX_BASELINE_MEETING_FALLBACK_SECTIONS": 1,
    })
    body = _stable_json({
        "term": "2610",
        "mode": "apply",
        "envelope": _envelope(),
    }).encode()
    headers = _push_headers(private_key, body)

    response = app.test_client().post(
        "/scheduler/internal/sisn-ingest",
        data=body,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json["status"] == "applied"
    assert CourseSection.query.filter_by(status="active").count() == 2

    replay = app.test_client().post(
        "/scheduler/internal/sisn-ingest",
        data=body,
        headers=headers,
    )
    assert replay.status_code == 409

    app.config["SISN_SYNC_MIN_SOURCE_COURSES"] = 2
    blocked_headers = _push_headers(
        private_key,
        body,
        nonce="blocked_nonce_123456789012345678",
    )
    blocked = app.test_client().post(
        "/scheduler/internal/sisn-ingest",
        data=body,
        headers=blocked_headers,
    )
    assert blocked.status_code == 422
    assert blocked.json["status"] == "blocked"
    assert blocked.json["code"] == "SisnSyncBlocked"
    assert "outside reviewed range" in blocked.json["detail"]


def test_signed_push_endpoint_rejects_stale_or_tampered_request(app, baseline_path, tmp_path):
    from app.routes.scheduler import bp as scheduler_bp

    private_key = Ed25519PrivateKey.generate()
    public_key_path = tmp_path / "sisn-push-public.pem"
    public_key_path.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    app.register_blueprint(scheduler_bp)
    app.config.update(
        SISN_PUSH_INGEST_ENABLED=True,
        SISN_PUSH_PUBLIC_KEY_PATH=str(public_key_path),
        SISN_PUSH_MAX_BODY_BYTES=8 * 1024 * 1024,
        SISN_PUSH_MAX_AGE_SECONDS=300,
    )
    body = _stable_json({"term": "2610", "mode": "dry-run", "envelope": _envelope()}).encode()

    stale = app.test_client().post(
        "/scheduler/internal/sisn-ingest",
        data=body,
        headers=_push_headers(private_key, body, timestamp=int(time.time()) - 301),
    )
    assert stale.status_code == 401

    tampered_body = body.replace(b'"dry-run"', b'"apply"')
    tampered = app.test_client().post(
        "/scheduler/internal/sisn-ingest",
        data=tampered_body,
        headers=_push_headers(private_key, body, nonce="tampered_1234567890123456789012"),
    )
    assert tampered.status_code == 401
