import os

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.extensions import db
from app.models.academic_map import UserCourseRecord
from app.models.course import Course
from app.models.post import Post
from app.models.scheduler_cart import SchedulerUserCourseCart
from app.models.scheduler_section import SchedulerSection
from app.models.tag import Tag, TagType, post_tags
from app.models.user import User
from app.models.user_role import UserRole
from app.scripts.import_scheduler_offerings import create_import_app
import app.scripts.reconcile_course_duplicates as reconcile_course_duplicates
from app.scripts.reconcile_course_duplicates import (
    EXPECTED_COURSE_FOREIGN_KEYS,
    build_reconciliation_plan,
    plan_sha256,
    run_reconciliation,
)


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    app = create_import_app("sqlite:///:memory:")
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _user():
    role = UserRole(name="reconcile-user")
    db.session.add(role)
    db.session.flush()
    user = User(
        username="reconcile-user",
        password_hash="test-password-hash",
        email="reconcile@connect.hkust-gz.edu.cn",
        email_verified=True,
        role_id=role.id,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _pair(code="TEST1001", spaced_code="TEST 1001"):
    survivor = Course(
        code=code,
        normalized_code=code,
        name="Canonical Course",
        credits=3,
        subject=code[:4],
        catalog_number=code[4:],
    )
    loser = Course(
        code=spaced_code,
        normalized_code=None,
        name="Legacy Course",
        credits=3,
    )
    db.session.add_all([survivor, loser])
    db.session.flush()
    return survivor, loser


def _apply_from_plan(plan):
    return run_reconciliation(
        apply=True,
        expected_database=plan["database"]["name"],
        expected_plan_sha256=plan_sha256(plan),
        expected_pairs=plan["pair_count"],
        expected_records=plan["user_course_record_count"],
        expected_tags=plan["tag_count"],
    )


def test_dry_run_is_stable_and_does_not_mutate(app):
    with app.app_context():
        survivor, loser = _pair()
        user = _user()
        record = UserCourseRecord(
            user_id=user.id,
            course_id=loser.id,
            course_code=loser.code,
            status=UserCourseRecord.STATUS_COMPLETED,
        )
        db.session.add(record)
        db.session.commit()

        first = run_reconciliation()
        second = run_reconciliation()

        assert first["status"] == "dry-run"
        assert first["plan_sha256"] == second["plan_sha256"]
        assert first["plan"]["pair_count"] == 1
        assert first["plan"]["user_course_record_count"] == 1
        assert first["plan"]["blockers"] == []
        assert db.session.get(Course, loser.id) is not None
        assert db.session.get(UserCourseRecord, record.id).course_id == loser.id
        assert db.session.get(Course, survivor.id).normalized_code == "TEST1001"


def test_plan_requires_exact_course_schema_invariants(app):
    with app.app_context():
        _pair()
        db.session.commit()

        plan = build_reconciliation_plan()

        assert plan["normalized_code_uniqueness"]["enforced"] is True
        actual_foreign_keys = {
            (item["table"], tuple(item["columns"]))
            for item in plan["foreign_keys"]
        }
        assert actual_foreign_keys == EXPECTED_COURSE_FOREIGN_KEYS
        assert plan["blockers"] == []


def test_missing_normalized_code_uniqueness_blocks(monkeypatch, app):
    with app.app_context():
        _pair()
        db.session.commit()
        real_inspector = inspect(db.engine)

        class InspectorWithoutNormalizedUnique:
            def __getattr__(self, name):
                return getattr(real_inspector, name)

            def get_unique_constraints(self, table_name, schema=None):
                return [
                    item
                    for item in real_inspector.get_unique_constraints(table_name, schema=schema)
                    if item.get("column_names") != ["normalized_code"]
                ]

            def get_indexes(self, table_name, schema=None):
                return [
                    item
                    for item in real_inspector.get_indexes(table_name, schema=schema)
                    if item.get("column_names") != ["normalized_code"]
                ]

        proxy = InspectorWithoutNormalizedUnique()
        monkeypatch.setattr(reconcile_course_duplicates, "inspect", lambda _engine: proxy)

        plan = build_reconciliation_plan()

        assert plan["normalized_code_uniqueness"]["enforced"] is False
        assert any(
            blocker["type"] == "missing_normalized_code_uniqueness"
            for blocker in plan["blockers"]
        )


def test_missing_expected_course_foreign_key_blocks(monkeypatch, app):
    with app.app_context():
        _pair()
        db.session.commit()
        real_inspector = inspect(db.engine)

        class InspectorWithoutSchedulerCourseForeignKey:
            def __getattr__(self, name):
                return getattr(real_inspector, name)

            def get_foreign_keys(self, table_name, schema=None):
                foreign_keys = real_inspector.get_foreign_keys(table_name, schema=schema)
                if table_name != "scheduler_sections":
                    return foreign_keys
                return [
                    item
                    for item in foreign_keys
                    if item.get("constrained_columns") != ["course_id"]
                ]

        proxy = InspectorWithoutSchedulerCourseForeignKey()
        monkeypatch.setattr(reconcile_course_duplicates, "inspect", lambda _engine: proxy)

        plan = build_reconciliation_plan()

        assert any(
            blocker["type"] == "missing_expected_course_foreign_key"
            and blocker["table"] == "scheduler_sections"
            and blocker["columns"] == ["course_id"]
            for blocker in plan["blockers"]
        )


def test_unexpected_course_foreign_key_blocks_even_without_rows(app):
    with app.app_context():
        _pair()
        db.session.execute(text("""
            CREATE TABLE rogue_course_refs (
                id INTEGER PRIMARY KEY,
                course_id INTEGER,
                FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
            )
        """))
        db.session.commit()

        plan = build_reconciliation_plan()

        assert any(
            blocker["type"] == "unsupported_course_foreign_key"
            and blocker["table"] == "rogue_course_refs"
            for blocker in plan["blockers"]
        )
        db.session.execute(text("DROP TABLE rogue_course_refs"))
        db.session.commit()


def test_cross_schema_course_foreign_key_blocks(monkeypatch, app):
    with app.app_context():
        _survivor, loser = _pair()
        db.session.commit()
        inspector = inspect(db.session.connection())
        schema = str(inspector.default_schema_name or "")
        table_names = set(inspector.get_table_names(schema=schema or None))
        default_rows = reconcile_course_duplicates._reflected_course_foreign_keys(
            inspector,
            schema,
            table_names,
        )
        cross_schema_row = {
            "schema": "audit",
            "table": "course_audit",
            "constraint": "fk_course_audit_course",
            "columns": ["course_id"],
            "referred_schema": schema,
            "referred_table": "courses",
            "referred_columns": ["id"],
            "ondelete": "CASCADE",
            "validated": True,
        }
        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")
        monkeypatch.setattr(
            reconcile_course_duplicates,
            "_postgresql_course_foreign_keys",
            lambda _schema: [*default_rows, cross_schema_row],
        )

        inventory, blockers = reconcile_course_duplicates._inventory_course_foreign_keys(
            [loser.id]
        )

        assert any(
            item["schema"] == "audit" and item["table"] == "course_audit"
            for item in inventory
        )
        assert any(
            blocker["type"] == "unsupported_course_foreign_key"
            and blocker["schema"] == "audit"
            and blocker["table"] == "course_audit"
            for blocker in blockers
        )


def test_plan_introspection_uses_the_session_transaction(monkeypatch, app):
    with app.app_context():
        _pair()
        db.session.commit()
        session_connection = db.session.connection()
        inspected_connectables = []

        def inspect_session_connection(connectable):
            inspected_connectables.append(connectable)
            return inspect(connectable)

        monkeypatch.setattr(
            reconcile_course_duplicates,
            "inspect",
            inspect_session_connection,
        )

        plan = build_reconciliation_plan()

        assert plan["blockers"] == []
        assert inspected_connectables
        assert all(
            connectable is session_connection
            for connectable in inspected_connectables
        )


def test_apply_merges_records_tags_and_post_links(app):
    with app.app_context():
        survivor, loser = _pair()
        user = _user()
        record = UserCourseRecord(
            user_id=user.id,
            course_id=loser.id,
            course_code=loser.code,
            status=UserCourseRecord.STATUS_COMPLETED,
        )
        course_type = TagType(name="COURSE")
        user_type = TagType(name="user")
        db.session.add_all([record, course_type, user_type])
        db.session.flush()

        source_plain = Tag(name=loser.code, tag_type_id=course_type.id)
        target_plain = Tag(name=survivor.code, tag_type_id=user_type.id)
        source_semester = Tag(name=f"{loser.code}-25-26Spring", tag_type_id=course_type.id)
        post_source = Post(user_id=user.id, title="Source", content="Source")
        post_target = Post(user_id=user.id, title="Target", content="Target")
        db.session.add_all([source_plain, target_plain, source_semester, post_source, post_target])
        db.session.flush()
        db.session.execute(post_tags.insert(), [
            {"post_id": post_source.id, "tag_id": source_plain.id},
            {"post_id": post_target.id, "tag_id": target_plain.id},
            {"post_id": post_source.id, "tag_id": source_semester.id},
        ])
        source_plain_id = source_plain.id
        source_semester_id = source_semester.id
        target_plain_id = target_plain.id
        survivor_id = survivor.id
        loser_id = loser.id
        record_id = record.id
        post_source_id = post_source.id
        post_target_id = post_target.id
        db.session.commit()

        plan = build_reconciliation_plan()
        assert plan["tag_count"] == 2
        assert plan["tags"]["rename_count"] == 1
        assert plan["tags"]["merge_count"] == 1
        result = _apply_from_plan(plan)

        assert result["status"] == "applied"
        assert result["applied"] == {
            "pairs_reconciled": 1,
            "losers_deleted": 1,
            "user_course_records_updated": 1,
            "tags_renamed": 1,
            "tags_merged": 1,
            "post_links_copied": 1,
        }
        assert db.session.get(Course, loser_id) is None
        canonical = db.session.get(Course, survivor_id)
        assert canonical.normalized_code == "TEST1001"
        updated_record = db.session.get(UserCourseRecord, record_id)
        assert (updated_record.course_id, updated_record.course_code) == (
            survivor_id,
            "TEST1001",
        )

        assert db.session.get(Tag, source_plain_id) is None
        merged_tag = db.session.get(Tag, target_plain_id)
        assert merged_tag.name == survivor.code
        assert merged_tag.tag_type.name.lower() == TagType.COURSE
        assert set(db.session.execute(
            select(post_tags.c.post_id).where(post_tags.c.tag_id == merged_tag.id)
        ).scalars()) == {post_source_id, post_target_id}

        renamed_tag = db.session.get(Tag, source_semester_id)
        assert renamed_tag.name == f"{survivor.code}-25-26Spring"
        assert renamed_tag.tag_type.name.lower() == TagType.COURSE
        assert set(db.session.execute(
            select(post_tags.c.post_id).where(post_tags.c.tag_id == renamed_tag.id)
        ).scalars()) == {post_source_id}


def test_apply_requires_exact_controls_and_rolls_back(app):
    with app.app_context():
        _survivor, loser = _pair()
        db.session.commit()
        plan = build_reconciliation_plan()

        result = run_reconciliation(
            apply=True,
            expected_database=plan["database"]["name"],
            expected_plan_sha256=plan_sha256(plan),
            expected_pairs=1,
            expected_records=1,
            expected_tags=0,
        )

        assert result["status"] == "blocked"
        assert result["control_errors"] == [
            "records count mismatch: actual=0 expected=1"
        ]
        assert db.session.get(Course, loser.id) is not None


def test_loser_reference_outside_user_records_blocks(app):
    with app.app_context():
        _survivor, loser = _pair()
        db.session.add(SchedulerSection(
            semester_id="2530",
            section_id="TEST1001-L01",
            course_id=loser.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=30,
            section_type="L",
            is_main=True,
        ))
        db.session.commit()

        plan = build_reconciliation_plan()
        assert any(
            blocker["type"] == "blocked_loser_foreign_key_references"
            and blocker["table"] == "scheduler_sections"
            for blocker in plan["blockers"]
        )
        assert any(
            item["table"] == "user_course_records"
            for item in plan["foreign_keys"]
        )
        result = _apply_from_plan(plan)
        assert result["status"] == "blocked"
        assert db.session.get(Course, loser.id) is not None


def test_legacy_cart_alias_blocks(app):
    with app.app_context():
        _survivor, loser = _pair()
        user = _user()
        db.session.add(SchedulerUserCourseCart(
            user_id=user.id,
            semester_id="2530",
            course_code=loser.code,
            enabled=True,
        ))
        db.session.commit()

        plan = build_reconciliation_plan()

        assert plan["legacy_carts"]["course_row_count"] == 1
        assert any(
            blocker["type"] == "legacy_cart_alias_references"
            for blocker in plan["blockers"]
        )


def test_unsupported_duplicate_shape_blocks(app):
    with app.app_context():
        _pair()
        db.session.add(Course(
            code="TEST\t1001",
            normalized_code=None,
            name="Third Variant",
            credits=3,
        ))
        db.session.commit()

        plan = build_reconciliation_plan()

        assert plan["pair_count"] == 0
        assert any(
            blocker["type"] == "unsupported_duplicate_shape"
            and blocker["normalized_code"] == "TEST1001"
            for blocker in plan["blockers"]
        )


def test_apply_failure_rolls_back_record_rewrite(monkeypatch, app):
    with app.app_context():
        survivor, loser = _pair()
        user = _user()
        record = UserCourseRecord(
            user_id=user.id,
            course_id=loser.id,
            course_code=loser.code,
            status=UserCourseRecord.STATUS_COMPLETED,
        )
        db.session.add(record)
        db.session.commit()
        plan = build_reconciliation_plan()

        def fail_after_record_rewrite(_plan):
            raise RuntimeError("injected tag failure")

        monkeypatch.setattr(
            reconcile_course_duplicates,
            "_apply_tags",
            fail_after_record_rewrite,
        )
        with pytest.raises(RuntimeError, match="injected tag failure"):
            _apply_from_plan(plan)

        db.session.expire_all()
        assert db.session.get(Course, loser.id) is not None
        unchanged = db.session.get(UserCourseRecord, record.id)
        assert (unchanged.course_id, unchanged.course_code) == (loser.id, loser.code)
        assert db.session.get(Course, survivor.id).normalized_code == survivor.code


def test_plan_fingerprints_all_course_and_tag_columns(app):
    with app.app_context():
        survivor, loser = _pair()
        course_type = TagType(name=TagType.COURSE)
        db.session.add(course_type)
        db.session.flush()
        source_tag = Tag(
            name=loser.code,
            tag_type_id=course_type.id,
            description="original",
        )
        db.session.add(source_tag)
        db.session.commit()

        first = build_reconciliation_plan()
        first_pair = first["pairs"][0]
        first_tag = first["tags"]["actions"][0]

        # Raw SQL deliberately bypasses Course.updated_at so the row fingerprint,
        # rather than a timestamp side effect, must detect the drift.
        db.session.execute(
            text("UPDATE courses SET vector = :vector WHERE id = :course_id"),
            {"vector": "[1-2-3:4]", "course_id": survivor.id},
        )
        db.session.commit()
        second = build_reconciliation_plan()
        second_pair = second["pairs"][0]

        assert (
            first_pair["survivor"]["row_sha256"]
            != second_pair["survivor"]["row_sha256"]
        )
        assert plan_sha256(first) != plan_sha256(second)

        db.session.execute(
            text("UPDATE tags SET description = :description WHERE id = :tag_id"),
            {"description": "changed", "tag_id": source_tag.id},
        )
        db.session.commit()
        third = build_reconciliation_plan()
        third_tag = third["tags"]["actions"][0]

        assert (
            first_tag["source_tag_row_sha256"]
            != third_tag["source_tag_row_sha256"]
        )
        assert plan_sha256(second) != plan_sha256(third)


def test_apply_deletes_loser_before_claiming_its_normalized_code(app):
    with app.app_context():
        survivor, loser = _pair()
        survivor.normalized_code = None
        db.session.commit()
        loser.normalized_code = survivor.code
        db.session.commit()
        survivor_id = survivor.id
        loser_id = loser.id

        plan = build_reconciliation_plan()
        assert plan["blockers"] == []
        result = _apply_from_plan(plan)

        assert result["status"] == "applied"
        assert db.session.get(Course, loser_id) is None
        assert db.session.get(Course, survivor_id).normalized_code == "TEST1001"


def test_apply_expires_identity_map_after_lock_before_replan(monkeypatch, app):
    with app.app_context():
        _pair()
        db.session.commit()
        reviewed_plan = build_reconciliation_plan()
        events = []
        real_build_plan = reconcile_course_duplicates.build_reconciliation_plan
        real_expire_all = db.session.expire_all

        def record_build_plan():
            events.append("build")
            return real_build_plan()

        def record_lock(_plan):
            events.append("lock")

        def record_expire_all():
            events.append("expire")
            real_expire_all()

        monkeypatch.setattr(
            reconcile_course_duplicates,
            "build_reconciliation_plan",
            record_build_plan,
        )
        monkeypatch.setattr(
            reconcile_course_duplicates,
            "_lock_apply_tables",
            record_lock,
        )
        monkeypatch.setattr(db.session, "expire_all", record_expire_all)

        result = _apply_from_plan(reviewed_plan)

        assert result["status"] == "applied"
        assert events[:4] == ["build", "lock", "expire", "build"]


def test_postgresql_locks_set_a_finite_timeout_first(monkeypatch, app):
    with app.app_context():
        statements = []
        real_inspector = inspect(db.engine)

        class PostgreSQLInspector:
            default_schema_name = "public"

            def get_table_names(self, schema=None):
                return real_inspector.get_table_names()

        def capture_execute(statement, parameters=None):
            statements.append((str(statement), parameters))

        monkeypatch.setattr(db.engine.dialect, "name", "postgresql")
        monkeypatch.setattr(
            reconcile_course_duplicates,
            "inspect",
            lambda _engine: PostgreSQLInspector(),
        )
        monkeypatch.setattr(db.session, "execute", capture_execute)

        reconcile_course_duplicates._lock_apply_tables({"foreign_keys": []})

        assert "set_config('lock_timeout'" in statements[0][0]
        assert statements[0][1] == {
            "lock_timeout": reconcile_course_duplicates.APPLY_LOCK_TIMEOUT,
        }
        assert statements[1][0].startswith("LOCK TABLE ")
