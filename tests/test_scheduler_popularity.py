from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_popularity import (
    SchedulerPopularityCourseSnapshot,
    SchedulerPopularityEvent,
    SchedulerPopularitySectionSnapshot,
    SchedulerPopularitySnapshotRun,
)
from app.models.user import User
from app.models.user_role import UserRole
from app.services.scheduler_popularity import (
    POPULARITY_HISTORY_END_AT,
    POPULARITY_HISTORY_EXPECTED_UNIVERSE,
    PopularityHistoryUniverse,
    collect_terminal_popularity_history_sample,
    collect_popularity_history_sample,
    popularity_history_sampling_status,
)


TEST_UNIVERSE = PopularityHistoryUniverse(
    "0003f0e60d6edc8760e19c960d1bcae6795f98a0e84c55837aeeea18d511eb47",
    1,
    2,
    0,
)


def snapshot_run(bucket_at, *, observed_at=None):
    return SchedulerPopularitySnapshotRun(
        semester_id="2610",
        bucket_at=bucket_at,
        observed_at=observed_at or bucket_at,
        universe_sha256=TEST_UNIVERSE.sha256,
        universe_course_count=TEST_UNIVERSE.course_count,
        universe_section_count=TEST_UNIVERSE.section_count,
        universe_meeting_count=TEST_UNIVERSE.meeting_count,
    )


def test_reviewed_package_exactly_matches_production_universe_constant():
    package_path = (
        Path(__file__).resolve().parents[1]
        / "app/data/pending/scheduler_offerings/26-27fall.json"
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))
    courses = []
    sections = []
    meetings = []
    for course in package["courses"]:
        course_code = "".join(course["course_code"].split()).upper()
        courses.append([course_code])
        for section in course["sections"]:
            section_id = str(section["section_id"]).strip()
            sections.append([course_code, section_id])
            for meeting in section["lectures"]:
                meetings.append([
                    course_code,
                    section_id,
                    int(meeting["day"]),
                    int(meeting["start_time"]),
                    int(meeting["end_time"]),
                    str(meeting["room"]),
                    str(meeting["instructor"]),
                ])
    payload = {
        "semester_id": package["semester_id"],
        "courses": sorted(courses),
        "sections": sorted(sections),
        "meetings": sorted(meetings),
    }
    digest = hashlib.sha256(json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    assert (
        digest,
        len(courses),
        len(sections),
        len(meetings),
    ) == POPULARITY_HISTORY_EXPECTED_UNIVERSE


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


def create_user(
    username,
    email,
    *,
    verified=True,
    deleted=False,
    created_at=None,
):
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="Regular user")
        db.session.add(role)
        db.session.flush()
    user = User(
        username=username,
        email=email,
        email_verified=verified,
        is_deleted=deleted,
        role_id=role.id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def headers_for(user):
    token = create_access_token(identity=str(user.id))
    return {"Authorization": f"Bearer {token}"}


def create_offering(code="POP1001", semester="2530", *, status="offered"):
    course = Course(
        code=code,
        normalized_code=code.replace(" ", "").upper(),
        name=f"Popularity {code}",
        credits=3,
        subject="TEST",
    )
    db.session.add(course)
    db.session.flush()
    offering = CourseOffering(
        course_id=course.id,
        semester_id=semester,
        offering_code=code,
        title_snapshot=course.name,
        credits_snapshot=3,
        source="test",
        status=status,
    )
    db.session.add(offering)
    db.session.flush()
    sections = [
        CourseSection(
            offering_id=offering.id,
            source_section_id=f"{code}-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=30,
            is_main=True,
        ),
        CourseSection(
            offering_id=offering.id,
            source_section_id=f"{code}-T01",
            name="T01",
            section_type="T",
            bundle=1,
            layer=1,
            quota=15,
            is_main=False,
        ),
    ]
    db.session.add_all(sections)
    db.session.flush()
    return course, offering, sections


def add_cart(user, offering, sections, *, enabled=False, selected=(True, True)):
    db.session.add(UserOfferingCart(
        user_id=user.id,
        offering_id=offering.id,
        enabled=enabled,
    ))
    for section, selection_enabled in zip(sections, selected):
        db.session.add(UserSectionSelection(
            user_id=user.id,
            offering_id=offering.id,
            section_id=section.id,
            enabled=selection_enabled,
            source="cart",
        ))


def test_popularity_requires_authenticated_verified_canonical_institutional_viewer(client, app):
    assert client.get("/scheduler/popularity/2530?course_codes=POP1001").status_code == 401

    with app.app_context():
        unverified = create_user("unverified", "unverified@hkust-gz.edu.cn", verified=False)
        external = create_user("external", "external@example.com")
        oldest = create_user(
            "oldest",
            "duplicate@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        duplicate = create_user("duplicate", " DUPLICATE@HKUST-GZ.EDU.CN ")
        db.session.commit()
        unverified_headers = headers_for(unverified)
        external_headers = headers_for(external)
        oldest_headers = headers_for(oldest)
        duplicate_headers = headers_for(duplicate)

    assert client.get("/scheduler/popularity/2530", headers=unverified_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=external_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=duplicate_headers).status_code == 403
    assert client.get("/scheduler/popularity/2530", headers=oldest_headers).status_code == 200


def test_popularity_is_cart_scoped_exact_anonymous_and_excludes_ineligible_duplicates(client, app):
    with app.app_context():
        _, offering, sections = create_offering()
        _, hidden_offering, hidden_sections = create_offering("POP2001")
        viewer = create_user("viewer", "viewer@hkust-gz.edu.cn")
        looking = create_user("looking", "looking@connect.hkust-gz.edu.cn")
        scheduling = create_user("scheduling", "scheduling@hkust-gz.edu.cn")
        unverified = create_user("unverified_count", "uv@hkust-gz.edu.cn", verified=False)
        external = create_user("external_count", "outside@example.com")
        deleted = create_user("deleted_count", "deleted@hkust-gz.edu.cn", deleted=True)
        canonical = create_user(
            "canonical_count",
            "same@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        duplicate = create_user("duplicate_count", " SAME@HKUST-GZ.EDU.CN ")
        create_user(
            "canonical_without_cart",
            "no_cart@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        )
        duplicate_with_cart = create_user(
            "duplicate_with_cart",
            " NO_CART@HKUST-GZ.EDU.CN ",
        )

        add_cart(viewer, offering, sections, selected=(False, False))
        add_cart(looking, offering, sections, enabled=False, selected=(True, False))
        add_cart(scheduling, offering, sections, enabled=True, selected=(True, True))
        add_cart(unverified, offering, sections)
        add_cart(external, offering, sections)
        add_cart(deleted, offering, sections)
        add_cart(canonical, offering, sections, enabled=False, selected=(True, True))
        add_cart(duplicate, offering, sections, enabled=True, selected=(True, True))
        add_cart(duplicate_with_cart, offering, sections, enabled=True, selected=(True, True))
        add_cart(looking, hidden_offering, hidden_sections)
        db.session.commit()
        viewer_headers = headers_for(viewer)

    response = client.get(
        "/scheduler/popularity/2530?course_codes=pop%201001,POP2001,POP1001",
        headers=viewer_headers,
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Authorization"
    data = response.get_json()
    assert set(data) == {"semester_id", "generated_at", "courses"}
    assert data["semester_id"] == "2530"
    assert len(data["courses"]) == 1
    course = data["courses"][0]
    assert set(course) == {"course_code", "looking_count", "scheduling_count", "sections"}
    assert course["course_code"] == "POP1001"
    # viewer + looking + canonical; the newer duplicate and ineligible accounts do not count.
    assert course["looking_count"] == 3
    assert course["scheduling_count"] == 1
    assert course["sections"] == [
        {"section_id": "POP1001-L01", "looking_count": 2, "scheduling_count": 1},
        {"section_id": "POP1001-T01", "looking_count": 1, "scheduling_count": 1},
    ]
    serialized = str(data).lower()
    for private_key in ("user_id", "username", "email", "event", "offering_id"):
        assert private_key not in serialized


def test_popularity_rejects_cross_offering_selection_and_initializes_zero_sections(client, app):
    with app.app_context():
        _, offering, sections = create_offering()
        _, other_offering, other_sections = create_offering("POP3001")
        viewer = create_user("viewer_cross", "viewer_cross@hkust-gz.edu.cn")
        contributor = create_user("contributor_cross", "contributor_cross@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections, selected=(False, False))
        db.session.add(UserOfferingCart(
            user_id=contributor.id,
            offering_id=offering.id,
            enabled=False,
        ))
        # The section FK is valid, but its offering deliberately disagrees with the selection.
        db.session.add(UserSectionSelection(
            user_id=contributor.id,
            offering_id=offering.id,
            section_id=other_sections[0].id,
            enabled=True,
            source="cart",
        ))
        db.session.commit()
        viewer_headers = headers_for(viewer)

    course = client.get(
        "/scheduler/popularity/2530?course_codes=POP1001",
        headers=viewer_headers,
    ).get_json()["courses"][0]
    assert course["looking_count"] == 2
    assert course["sections"] == [
        {"section_id": "POP1001-L01", "looking_count": 0, "scheduling_count": 0},
        {"section_id": "POP1001-T01", "looking_count": 0, "scheduling_count": 0},
    ]


def test_popularity_empty_filter_limit_and_archived_cart_scope(client, app):
    with app.app_context():
        _, offering, sections = create_offering(status="archived")
        viewer = create_user("viewer_limits", "viewer_limits@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        db.session.commit()
        viewer_headers = headers_for(viewer)

    empty = client.get("/scheduler/popularity/2530", headers=viewer_headers)
    assert empty.status_code == 200
    assert empty.get_json()["courses"] == []

    archived = client.get(
        "/scheduler/popularity/2530?course_codes=POP1001",
        headers=viewer_headers,
    )
    assert archived.status_code == 200
    assert archived.get_json()["courses"] == []

    too_many = ",".join(f"TEST{i:04d}" for i in range(31))
    limited = client.get(
        f"/scheduler/popularity/2530?course_codes={too_many}",
        headers=viewer_headers,
    )
    assert limited.status_code == 400


def test_history_sampler_is_sparse_idempotent_and_counts_only_canonical_users(app):
    sampled_at = datetime(2026, 8, 12, 4, 2, tzinfo=timezone.utc)
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        looking = create_user("history_looking", "history_looking@hkust-gz.edu.cn")
        scheduling = create_user("history_scheduling", "history_scheduling@connect.hkust-gz.edu.cn")
        canonical = create_user(
            "history_canonical",
            "history_duplicate@hkust-gz.edu.cn",
            created_at=sampled_at - timedelta(days=2),
        )
        duplicate = create_user("history_duplicate", " HISTORY_DUPLICATE@HKUST-GZ.EDU.CN ")
        unverified = create_user(
            "history_unverified",
            "history_unverified@hkust-gz.edu.cn",
            verified=False,
        )
        add_cart(looking, offering, sections, enabled=False, selected=(True, False))
        add_cart(scheduling, offering, sections, enabled=True, selected=(True, True))
        add_cart(canonical, offering, sections, enabled=False, selected=(True, True))
        add_cart(duplicate, offering, sections, enabled=True, selected=(True, True))
        add_cart(unverified, offering, sections, enabled=True, selected=(True, True))
        db.session.commit()

        created = collect_popularity_history_sample(
            sampled_at=sampled_at,
            expected_universe=TEST_UNIVERSE,
            _observed_at=sampled_at + timedelta(seconds=3),
        )
        repeated = collect_popularity_history_sample(
            sampled_at=sampled_at + timedelta(minutes=2),
            expected_universe=TEST_UNIVERSE,
        )

        assert created == {
            "status": "completed",
            "semester_id": "2610",
            "bucket_at": "2026-08-12T04:00:00Z",
            "observed_at": "2026-08-12T04:02:03Z",
            "course_facts": 1,
            "section_facts": 2,
        }
        assert repeated["status"] == "already_completed"
        assert SchedulerPopularitySnapshotRun.query.count() == 1
        course_fact = SchedulerPopularityCourseSnapshot.query.one()
        assert (course_fact.looking_count, course_fact.scheduling_count) == (2, 1)
        section_facts = {
            row.section_source_id: (row.looking_count, row.scheduling_count)
            for row in SchedulerPopularitySectionSnapshot.query.all()
        }
        assert section_facts == {
            "POP1001-L01": (2, 1),
            "POP1001-T01": (1, 1),
        }
        for model in (SchedulerPopularitySnapshotRun, SchedulerPopularityCourseSnapshot,
                      SchedulerPopularitySectionSnapshot):
            assert "user_id" not in model.__table__.columns


def test_history_api_is_verified_cart_scoped_anonymous_and_preserves_gaps_and_zeros(client, app):
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_viewer", "history_viewer@hkust-gz.edu.cn")
        outsider = create_user("history_outsider", "history_outsider@hkust-gz.edu.cn")
        unverified = create_user(
            "history_unverified_viewer",
            "history_unverified_viewer@hkust-gz.edu.cn",
            verified=False,
        )
        add_cart(viewer, offering, sections, selected=(False, False))
        runs = [
            snapshot_run(datetime(2026, 8, 12, 4, minute, tzinfo=timezone.utc))
            for minute in (0, 10)
        ]
        db.session.add_all(runs)
        db.session.commit()
        viewer_headers = headers_for(viewer)
        outsider_headers = headers_for(outsider)
        unverified_headers = headers_for(unverified)

    query = (
        "course_code=pop%201001&section_id=POP1001-L01"
        "&from=2026-08-12T12:00:00%2B08:00"
        "&to=2026-08-12T12:10:00%2B08:00&resolution=auto"
    )
    assert client.get(f"/scheduler/popularity/2610/history?{query}").status_code == 401
    assert client.get(
        f"/scheduler/popularity/2610/history?{query}",
        headers=unverified_headers,
    ).status_code == 403
    assert client.get(
        f"/scheduler/popularity/2610/history?{query}",
        headers=outsider_headers,
    ).status_code == 404

    response = client.get(
        f"/scheduler/popularity/2610/history?{query}",
        headers=viewer_headers,
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Authorization"
    payload = response.get_json()
    assert payload["semester_id"] == "2610"
    assert payload["course_code"] == "POP1001"
    assert payload["section_id"] == "POP1001-L01"
    assert payload["tracking_started_at"] == "2026-08-12T04:00:00Z"
    assert payload["tracking_ends_at"] == "2026-09-30T15:59:00Z"
    assert payload["source_interval_seconds"] == 300
    assert payload["effective_interval_seconds"] == 300
    assert payload["latest_scheduled_sample_at"] == "2026-08-12T04:10:00Z"
    assert payload["latest_observed_sample_at"] == "2026-08-12T04:10:00Z"
    assert payload["requested_coverage_end_at"] == "2026-08-12T04:10:00Z"
    assert payload["sampling_state"] == "stale"
    assert payload["terminal_present"] is False
    assert payload["coverage_buckets"] == [
        {
            "bucket_at": "2026-08-12T04:00:00Z",
            "expected_samples": 1,
            "observed_samples": 1,
            "partial": False,
        },
        {
            "bucket_at": "2026-08-12T04:05:00Z",
            "expected_samples": 1,
            "observed_samples": 0,
            "partial": True,
        },
        {
            "bucket_at": "2026-08-12T04:10:00Z",
            "expected_samples": 1,
            "observed_samples": 1,
            "partial": False,
        },
    ]
    assert payload["points"] == [
        {
            "sampled_at": "2026-08-12T04:00:00Z",
            "observed_at": "2026-08-12T04:00:00Z",
            "looking_count": 0,
            "scheduling_count": 0,
        },
        {
            "sampled_at": "2026-08-12T04:10:00Z",
            "observed_at": "2026-08-12T04:10:00Z",
            "looking_count": 0,
            "scheduling_count": 0,
        },
    ]
    serialized = str(payload).lower()
    for private_key in ("user_id", "username", "email", "offering_id"):
        assert private_key not in serialized


def test_history_sampler_rejects_same_size_swapped_universe(app):
    sampled_at = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    with app.app_context():
        _course, _offering, sections = create_offering(semester="2610")
        expected = TEST_UNIVERSE
        sections[0].source_section_id = "SWAPPED-L01"
        db.session.commit()

        with pytest.raises(RuntimeError, match="unreviewed semester universe"):
            collect_popularity_history_sample(
                sampled_at=sampled_at,
                expected_universe=expected,
            )
        assert SchedulerPopularitySnapshotRun.query.count() == 0


def test_history_sampler_persists_universe_and_rejects_midstream_change(app):
    first_at = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    with app.app_context():
        _course, _offering, sections = create_offering(semester="2610")
        db.session.commit()
        collect_popularity_history_sample(
            sampled_at=first_at,
            expected_universe=TEST_UNIVERSE,
            _observed_at=first_at + timedelta(seconds=2),
        )
        first = SchedulerPopularitySnapshotRun.query.one()
        assert first.observed_at.replace(tzinfo=timezone.utc) == first_at + timedelta(seconds=2)
        assert first.universe_sha256 == TEST_UNIVERSE.sha256
        assert (
            first.universe_course_count,
            first.universe_section_count,
            first.universe_meeting_count,
        ) == (1, 2, 0)

        sections[1].source_section_id = "SWAPPED-T01"
        changed_universe = PopularityHistoryUniverse(
            "2ba1b9cf5f75f5d21ae50c2a0087bd14784fb8b16d4867167d5532c177d8bed6",
            1,
            2,
            0,
        )
        db.session.commit()
        with pytest.raises(RuntimeError, match="changed after tracking started"):
            collect_popularity_history_sample(
                sampled_at=first_at + timedelta(minutes=5),
                expected_universe=changed_universe,
            )
        assert SchedulerPopularitySnapshotRun.query.count() == 1


def test_history_api_validates_time_range_resolution_and_scope(client, app):
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_validation", "history_validation@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        db.session.commit()
        auth_headers = headers_for(viewer)

    base = "/scheduler/popularity/2610/history?course_code=POP1001"
    assert client.get(base, headers=auth_headers).status_code == 400
    assert client.get(
        f"{base}&from=2026-08-12T00:00:00&to=2026-08-13T00:00:00Z",
        headers=auth_headers,
    ).status_code == 400
    assert client.get(
        f"{base}&from=2026-08-13T00:00:00Z&to=2026-08-12T00:00:00Z",
        headers=auth_headers,
    ).status_code == 400
    assert client.get(
        f"{base}&from=2026-08-12T00:00:00Z&to=2026-08-13T00:00:00Z&resolution=hour",
        headers=auth_headers,
    ).status_code == 400
    assert client.get(
        f"{base}&section_id=missing&from=2026-08-12T00:00:00Z&to=2026-08-13T00:00:00Z",
        headers=auth_headers,
    ).status_code == 404
    assert client.get(
        "/scheduler/popularity/2530/history?course_code=POP1001"
        "&from=2026-08-12T00:00:00Z&to=2026-08-13T00:00:00Z",
        headers=auth_headers,
    ).status_code == 404


def test_history_sampler_has_hard_cutoff_and_exact_terminal_sample(app):
    with app.app_context():
        create_offering(semester="2610")
        db.session.commit()
        with pytest.raises(ValueError, match="before"):
            collect_terminal_popularity_history_sample(
                now=POPULARITY_HISTORY_END_AT - timedelta(microseconds=1),
                expected_universe=TEST_UNIVERSE,
            )
        with patch(
            "app.services.scheduler_popularity.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = POPULARITY_HISTORY_END_AT
            terminal = collect_terminal_popularity_history_sample(
                now=POPULARITY_HISTORY_END_AT,
                expected_universe=TEST_UNIVERSE,
            )
        with pytest.raises(ValueError, match="outside"):
            collect_terminal_popularity_history_sample(
                now=POPULARITY_HISTORY_END_AT + timedelta(seconds=121),
                expected_universe=TEST_UNIVERSE,
            )
        after = collect_popularity_history_sample(
            sampled_at=POPULARITY_HISTORY_END_AT + timedelta(seconds=1),
            expected_universe=TEST_UNIVERSE,
        )
        assert terminal["status"] == "completed"
        assert terminal["bucket_at"] == "2026-09-30T15:59:00Z"
        assert terminal["observed_at"] == "2026-09-30T15:59:00Z"
        assert after["status"] == "after_cutoff"
        assert SchedulerPopularitySnapshotRun.query.count() == 1


def test_terminal_sample_exposes_delayed_observation_without_backdating(app):
    observed_at = POPULARITY_HISTORY_END_AT + timedelta(seconds=17)
    with app.app_context():
        create_offering(semester="2610")
        db.session.commit()
        with patch(
            "app.services.scheduler_popularity.datetime",
            wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value = observed_at
            terminal = collect_terminal_popularity_history_sample(
                now=observed_at,
                expected_universe=TEST_UNIVERSE,
            )
        assert terminal["bucket_at"] == "2026-09-30T15:59:00Z"
        assert terminal["observed_at"] == "2026-09-30T15:59:17Z"
        run = SchedulerPopularitySnapshotRun.query.one()
        assert run.bucket_at != run.observed_at


def test_history_baseline_uses_exact_deployment_time_only_once(app):
    deployed_at = datetime(2026, 8, 12, 4, 2, 17, 123456, tzinfo=timezone.utc)
    with app.app_context():
        create_offering(semester="2610")
        db.session.commit()
        baseline = collect_popularity_history_sample(
            sampled_at=deployed_at,
            baseline=True,
            expected_universe=TEST_UNIVERSE,
            _observed_at=deployed_at,
        )
        same_bucket = collect_popularity_history_sample(
            sampled_at=deployed_at,
            expected_universe=TEST_UNIVERSE,
        )
        redeploy = collect_popularity_history_sample(
            sampled_at=deployed_at + timedelta(days=1),
            baseline=True,
            expected_universe=TEST_UNIVERSE,
        )

        assert baseline["status"] == "completed"
        assert baseline["bucket_at"] == "2026-08-12T04:02:17.123456Z"
        assert same_bucket["status"] == "covered_by_baseline"
        assert same_bucket["bucket_at"] == baseline["bucket_at"]
        assert redeploy["status"] == "tracking_already_started"
        assert redeploy["bucket_at"] == baseline["bucket_at"]
        assert SchedulerPopularitySnapshotRun.query.count() == 1


def test_history_freshness_is_measured_against_cutoff_after_tracking_ends(app):
    with app.app_context():
        db.session.add(snapshot_run(POPULARITY_HISTORY_END_AT))
        db.session.commit()

        status = popularity_history_sampling_status(
            now=POPULARITY_HISTORY_END_AT + timedelta(days=30),
        )
        assert status["latest_bucket_at"] == "2026-09-30T15:59:00Z"
        assert status["latest_observed_at"] == "2026-09-30T15:59:00Z"
        assert status["age_seconds"] == 0
        assert status["sampling_state"] == "ended_complete"
        assert status["terminal_present"] is True


def test_history_api_auto_downsamples_long_ranges_to_bounded_points(client, app):
    started_at = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_long_range", "history_long_range@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        db.session.add_all([
            snapshot_run(started_at + timedelta(minutes=5 * index))
            for index in range(1201)
        ])
        db.session.commit()
        auth_headers = headers_for(viewer)

    response = client.get(
        "/scheduler/popularity/2610/history?course_code=POP1001"
        "&from=2026-08-12T00:00:00Z&to=2026-08-16T04:00:00Z&resolution=auto",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["effective_interval_seconds"] == 600
    assert len(payload["points"]) <= 1000


def test_history_api_unaligned_range_never_exceeds_hard_point_cap(client, app):
    started_at = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_unaligned", "history_unaligned@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        db.session.add_all([
            snapshot_run(started_at + timedelta(minutes=5 * index))
            for index in range(2001)
        ])
        db.session.commit()
        auth_headers = headers_for(viewer)

    response = client.get(
        "/scheduler/popularity/2610/history?course_code=POP1001"
        "&from=2026-08-12T00:00:01Z&to=2026-08-18T22:40:01Z&resolution=auto",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["points"]) <= 1000
    assert len(payload["coverage_buckets"]) <= 1000


def test_downsampled_coverage_counts_unaligned_baseline_as_additional_expected_sample(
    client,
    app,
):
    baseline_at = datetime(2026, 8, 12, 4, 2, tzinfo=timezone.utc)
    with app.app_context():
        _, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_baseline_gap", "history_baseline_gap@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        # Force auto resolution to ten minutes while leaving the aligned 04:05
        # source slot absent. The 04:02 deployment baseline must not conceal it.
        db.session.add_all([
            snapshot_run(baseline_at),
            *[
                snapshot_run(
                    datetime(2026, 8, 12, 4, 10, tzinfo=timezone.utc)
                    + timedelta(minutes=5 * index)
                )
                for index in range(1200)
            ],
        ])
        db.session.commit()
        auth_headers = headers_for(viewer)

    response = client.get(
        "/scheduler/popularity/2610/history?course_code=POP1001"
        "&from=2026-08-12T04:02:00Z&to=2026-08-16T08:07:00Z&resolution=auto",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["effective_interval_seconds"] == 600
    first = payload["coverage_buckets"][0]
    assert first == {
        "bucket_at": "2026-08-12T04:00:00Z",
        "expected_samples": 2,
        "observed_samples": 1,
        "partial": True,
    }


def test_history_coverage_does_not_report_future_slots_as_missing(client, app):
    with app.app_context():
        _course, offering, sections = create_offering(semester="2610")
        viewer = create_user("history_future", "history_future@hkust-gz.edu.cn")
        add_cart(viewer, offering, sections)
        now = datetime.now(timezone.utc)
        observed_bucket = now - timedelta(minutes=5)
        observed_bucket = observed_bucket.replace(
            minute=observed_bucket.minute - observed_bucket.minute % 5,
            second=0,
            microsecond=0,
        )
        db.session.add(snapshot_run(observed_bucket))
        db.session.commit()
        auth_headers = headers_for(viewer)

    future_end = min(now + timedelta(hours=1), POPULARITY_HISTORY_END_AT)
    query = urlencode({
        "course_code": "POP1001",
        "from": observed_bucket.isoformat(),
        "to": future_end.isoformat(),
    })
    response = client.get(
        f"/scheduler/popularity/2610/history?{query}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["requested_coverage_end_at"] == future_end.isoformat().replace("+00:00", "Z")
    assert all(
        datetime.fromisoformat(bucket["bucket_at"].replace("Z", "+00:00")) <= now
        for bucket in payload["coverage_buckets"]
    )


def test_cart_mutations_log_only_actual_anonymous_transitions(client, app):
    with app.app_context():
        create_offering()
        user = create_user("event_user", "event_user@hkust-gz.edu.cn")
        db.session.commit()
        user_headers = headers_for(user)

    added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=user_headers,
    )
    assert added.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.filter_by(reason="cart_added").count() == 3
        assert "user_id" not in SchedulerPopularityEvent.__table__.columns

    no_op = client.put(
        "/scheduler/cart/2530/course/POP1001/toggle",
        json={"enabled": False},
        headers=user_headers,
    )
    assert no_op.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 3

    enabled = client.put(
        "/scheduler/cart/2530/course/POP1001/toggle",
        json={"enabled": True},
        headers=user_headers,
    )
    assert enabled.status_code == 200
    with app.app_context():
        toggles = SchedulerPopularityEvent.query.filter_by(reason="course_toggled").all()
        assert len(toggles) == 3
        assert {(event.from_state, event.to_state) for event in toggles} == {
            ("looking", "scheduling")
        }

    disabled_bundle = client.put(
        "/scheduler/cart/2530/bundle/POP1001/1/1/toggle",
        json={"enabled": False},
        headers=user_headers,
    )
    assert disabled_bundle.status_code == 200
    with app.app_context():
        bundle_events = SchedulerPopularityEvent.query.filter_by(reason="bundle_toggled").all()
        assert len(bundle_events) == 1
        assert (bundle_events[0].from_state, bundle_events[0].to_state) == ("scheduling", None)

    removed = client.delete(
        "/scheduler/cart/2530/remove/POP1001",
        headers=user_headers,
    )
    assert removed.status_code == 200
    with app.app_context():
        removal_events = SchedulerPopularityEvent.query.filter_by(reason="cart_removed").all()
        assert len(removal_events) == 2
        assert sum(event.section_id is None for event in removal_events) == 1


@pytest.mark.parametrize("route", [
    "/scheduler/cart/2530/course/POP1001/toggle",
    "/scheduler/cart/2530/bundle/POP1001/1/0/toggle",
    "/scheduler/cart/2530/layer/POP1001/0/toggle",
])
def test_cart_toggles_require_boolean_enabled(client, app, route):
    with app.app_context():
        _, offering, sections = create_offering()
        user = create_user(f"bool_{route.split('/')[-2]}", f"bool_{route.split('/')[-2]}@hkust-gz.edu.cn")
        add_cart(user, offering, sections)
        db.session.commit()
        user_headers = headers_for(user)

    response = client.put(route, json={"enabled": "false"}, headers=user_headers)
    assert response.status_code == 400
    assert response.get_json() == {"error": "enabled must be a boolean"}
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 0


def test_ineligible_and_non_offered_mutations_do_not_log(client, app):
    with app.app_context():
        create_offering("ARCH1001", status="archived")
        create_offering("POP1001")
        user = create_user("unverified_event", "unverified_event@hkust-gz.edu.cn", verified=False)
        create_user(
            "canonical_event",
            "duplicate_event@hkust-gz.edu.cn",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        duplicate = create_user("duplicate_event", " DUPLICATE_EVENT@HKUST-GZ.EDU.CN ")
        db.session.commit()
        user_headers = headers_for(user)
        duplicate_headers = headers_for(duplicate)

    archived = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "ARCH1001"},
        headers=user_headers,
    )
    assert archived.status_code == 422
    added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=user_headers,
    )
    assert added.status_code == 200
    duplicate_added = client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "POP1001"},
        headers=duplicate_headers,
    )
    assert duplicate_added.status_code == 200
    with app.app_context():
        assert SchedulerPopularityEvent.query.count() == 0
