"""Safely reconcile the narrow legacy Course duplicate shape.

This repair intentionally supports only one observed shape: exactly two active
Course rows whose codes differ only by whitespace, with the compact code row
selected as the survivor.  Any reference to a loser outside
``user_course_records`` blocks the repair.

Dry-run is the default.  Apply requires the database name, the exact dry-run
plan SHA-256, and independently reviewed pair/record/tag counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import column, delete, func, inspect, select, table, text, update
from sqlalchemy.engine import make_url

from app.extensions import db
from app.models.academic_map import UserCourseRecord
from app.models.course import Course
from app.models.scheduler_cart import SchedulerUserBundleCart, SchedulerUserCourseCart
from app.models.tag import Tag, TagType, post_tags
from app.scripts.import_scheduler_offerings import create_import_app
from app.services.course_domain import normalize_course_code


PLAN_VERSION = 2
ALLOWED_LOSER_FOREIGN_KEY = ("user_course_records", ("course_id",))
APPLY_LOCK_TIMEOUT = "5s"
EXPECTED_COURSE_FOREIGN_KEYS = frozenset({
    ("course_catalog_versions", ("course_id",)),
    ("course_offerings", ("course_id",)),
    ("course_requirement_edges", ("from_course_id",)),
    ("course_requirement_edges", ("to_course_id",)),
    ("scheduler_sections", ("course_id",)),
    ("user_course_attempts", ("course_id",)),
    ("user_course_records", ("course_id",)),
    ("user_course_states", ("course_id",)),
})
COURSE_METADATA_FIELDS = (
    "name",
    "description",
    "instructor_id",
    "credits",
    "capacity",
    "subject",
    "catalog_number",
    "course_title_abbr",
    "pre_requirement",
    "co_requirement",
    "exclusion",
    "pg_course",
    "klms_course",
    "vector",
)


class ReconciliationError(RuntimeError):
    """Raised when mutation fails after controls have been accepted."""


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )


def plan_sha256(plan: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(plan).encode("utf-8")).hexdigest()


def _row_sha256(model: Any) -> str:
    """Fingerprint every persisted column without exposing full row contents."""
    row = {
        column.name: _json_value(getattr(model, column.name))
        for column in model.__table__.columns
    }
    return hashlib.sha256(_canonical_json(row).encode("utf-8")).hexdigest()


def _database_identity() -> dict[str, str]:
    url = make_url(db.engine.url)
    inspector = inspect(db.session.connection())
    return {
        "dialect": db.engine.dialect.name,
        "name": str(url.database or ""),
        "schema": str(inspector.default_schema_name or ""),
    }


def _course_snapshot(course: Course) -> dict[str, Any]:
    return {
        "id": course.id,
        "code": course.code,
        "normalized_code": course.normalized_code,
        "display_code": course.display_code,
        "name": course.name,
        "credits": course.credits,
        "subject": course.subject,
        "catalog_number": course.catalog_number,
        "is_active": bool(course.is_active),
        "is_deleted": bool(course.is_deleted),
        "created_at": _json_value(course.created_at),
        "updated_at": _json_value(course.updated_at),
        "row_sha256": _row_sha256(course),
    }


def _sort_objects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=_canonical_json)


def _duplicate_pairs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, list[Course]] = defaultdict(list)
    blockers: list[dict[str, Any]] = []

    for course in Course.query.order_by(Course.id).all():
        derived = normalize_course_code(course.code)
        stored = normalize_course_code(course.normalized_code)
        if stored and stored != derived:
            blockers.append({
                "type": "inconsistent_normalized_code",
                "course_id": course.id,
                "code": course.code,
                "normalized_code": course.normalized_code,
                "derived_code": derived,
            })
        if derived:
            groups[derived].append(course)

    pairs: list[dict[str, Any]] = []
    for normalized_code, courses in sorted(groups.items()):
        if len(courses) <= 1:
            continue

        compact = [course for course in courses if course.code == normalized_code]
        is_supported = (
            len(courses) == 2
            and len(compact) == 1
            and all(bool(course.is_active) and not bool(course.is_deleted) for course in courses)
        )
        if not is_supported:
            blockers.append({
                "type": "unsupported_duplicate_shape",
                "normalized_code": normalized_code,
                "rows": [_course_snapshot(course) for course in courses],
                "expected": "exactly two active rows with exactly one compact code",
            })
            continue

        survivor = compact[0]
        loser = next(course for course in courses if course.id != survivor.id)
        if (
            loser.code == survivor.code
            or "".join(loser.code.split()) != survivor.code
        ):
            blockers.append({
                "type": "unsupported_duplicate_shape",
                "normalized_code": normalized_code,
                "rows": [_course_snapshot(course) for course in courses],
                "expected": "codes must differ only by whitespace",
            })
            continue

        differing_fields = [
            field_name
            for field_name in COURSE_METADATA_FIELDS
            if getattr(survivor, field_name) != getattr(loser, field_name)
        ]
        pairs.append({
            "normalized_code": normalized_code,
            "survivor": _course_snapshot(survivor),
            "loser": _course_snapshot(loser),
            "metadata_policy": "compact_survivor_wins",
            "differing_metadata_fields": differing_fields,
        })

    return pairs, _sort_objects(blockers)


def _pair_indexes(
    pairs: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_loser_id = {pair["loser"]["id"]: pair for pair in pairs}
    by_loser_code = {pair["loser"]["code"]: pair for pair in pairs}
    by_normalized = {pair["normalized_code"]: pair for pair in pairs}
    return by_loser_id, by_loser_code, by_normalized


def _user_course_record_actions(
    pairs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_loser_id, by_loser_code, _ = _pair_indexes(pairs)
    actions: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    for record in UserCourseRecord.query.order_by(UserCourseRecord.id).all():
        id_pair = by_loser_id.get(record.course_id)
        code_pair = by_loser_code.get(record.course_code)
        if id_pair is None and code_pair is None:
            continue
        if id_pair is not None and code_pair is not None and id_pair is not code_pair:
            blockers.append({
                "type": "user_course_record_cross_pair_identity",
                "record_id": record.id,
                "course_id": record.course_id,
                "course_code": record.course_code,
            })
            continue

        pair = id_pair or code_pair
        survivor_id = pair["survivor"]["id"]
        loser_id = pair["loser"]["id"]
        if record.course_id not in {None, survivor_id, loser_id}:
            blockers.append({
                "type": "user_course_record_conflicting_course_id",
                "record_id": record.id,
                "course_id": record.course_id,
                "course_code": record.course_code,
                "expected_course_ids": [survivor_id, loser_id],
            })
            continue
        if normalize_course_code(record.course_code) != pair["normalized_code"]:
            blockers.append({
                "type": "user_course_record_conflicting_course_code",
                "record_id": record.id,
                "course_id": record.course_id,
                "course_code": record.course_code,
                "expected_normalized_code": pair["normalized_code"],
            })
            continue

        actions.append({
            "record_id": record.id,
            "record_row_sha256": _row_sha256(record),
            "old_course_id": record.course_id,
            "old_course_code": record.course_code,
            "new_course_id": survivor_id,
            "new_course_code": pair["survivor"]["code"],
        })

    return actions, _sort_objects(blockers)


def _normalized_code_uniqueness_audit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inspector = inspect(db.session.connection())
    schema = str(inspector.default_schema_name or "")
    candidates: list[dict[str, Any]] = []

    for constraint in inspector.get_unique_constraints(
        Course.__tablename__,
        schema=schema or None,
    ):
        columns = list(constraint.get("column_names") or ())
        if columns == ["normalized_code"]:
            candidates.append({
                "kind": "constraint",
                "name": constraint.get("name"),
                "columns": columns,
            })

    for index in inspector.get_indexes(Course.__tablename__, schema=schema or None):
        columns = list(index.get("column_names") or ())
        dialect_options = index.get("dialect_options") or {}
        is_partial = (
            dialect_options.get("postgresql_where") is not None
            or dialect_options.get("sqlite_where") is not None
        )
        if index.get("unique") and columns == ["normalized_code"] and not is_partial:
            candidates.append({
                "kind": "index",
                "name": index.get("name"),
                "columns": columns,
            })

    if db.engine.dialect.name == "postgresql":
        # PostgreSQL can expose an invalid/not-ready index through reflection;
        # such an index does not provide the invariant this destructive repair needs.
        enforced = bool(db.session.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM pg_index AS index_meta
                JOIN pg_class AS table_meta
                  ON table_meta.oid = index_meta.indrelid
                JOIN pg_namespace AS namespace_meta
                  ON namespace_meta.oid = table_meta.relnamespace
                WHERE namespace_meta.nspname = :schema
                  AND table_meta.relname = :table_name
                  AND index_meta.indisunique
                  AND index_meta.indisvalid
                  AND index_meta.indisready
                  AND index_meta.indpred IS NULL
                  AND index_meta.indexprs IS NULL
                  AND index_meta.indnkeyatts = 1
                  AND index_meta.indkey[0] = (
                      SELECT attribute_meta.attnum
                      FROM pg_attribute AS attribute_meta
                      WHERE attribute_meta.attrelid = table_meta.oid
                        AND attribute_meta.attname = 'normalized_code'
                        AND NOT attribute_meta.attisdropped
                  )
            )
        """), {
            "schema": schema,
            "table_name": Course.__tablename__,
        }).scalar_one())
    else:
        enforced = bool(candidates)

    audit = {
        "schema": schema,
        "table": Course.__tablename__,
        "column": "normalized_code",
        "enforced": enforced,
        "candidates": sorted(candidates, key=_canonical_json),
    }
    blockers = [] if enforced else [{
        "type": "missing_normalized_code_uniqueness",
        "schema": schema,
        "table": Course.__tablename__,
        "column": "normalized_code",
    }]
    return audit, blockers


def _count_loser_references(
    schema: str,
    table_name: str,
    column_name: str,
    loser_ids: list[int],
) -> int:
    if not loser_ids:
        return 0
    referenced_table = table(
        table_name,
        column(column_name),
        schema=schema or None,
    )
    return int(db.session.execute(
        select(func.count())
        .select_from(referenced_table)
        .where(referenced_table.c[column_name].in_(loser_ids))
    ).scalar_one())


def _postgresql_course_foreign_keys(schema: str) -> list[dict[str, Any]]:
    """Read every FK to the target Course table, including other schemas."""
    rows = db.session.execute(text("""
        SELECT
            source_namespace.nspname AS schema,
            source_table.relname AS table_name,
            constraint_meta.conname AS constraint_name,
            array_agg(
                source_attribute.attname::text
                ORDER BY key_position.position
            ) AS columns,
            target_namespace.nspname AS referred_schema,
            target_table.relname AS referred_table,
            array_agg(
                target_attribute.attname::text
                ORDER BY key_position.position
            ) AS referred_columns,
            CASE constraint_meta.confdeltype
                WHEN 'a' THEN 'NO ACTION'
                WHEN 'r' THEN 'RESTRICT'
                WHEN 'c' THEN 'CASCADE'
                WHEN 'n' THEN 'SET NULL'
                WHEN 'd' THEN 'SET DEFAULT'
            END AS ondelete,
            constraint_meta.convalidated AS validated
        FROM pg_constraint AS constraint_meta
        JOIN pg_class AS source_table
          ON source_table.oid = constraint_meta.conrelid
        JOIN pg_namespace AS source_namespace
          ON source_namespace.oid = source_table.relnamespace
        JOIN pg_class AS target_table
          ON target_table.oid = constraint_meta.confrelid
        JOIN pg_namespace AS target_namespace
          ON target_namespace.oid = target_table.relnamespace
        JOIN LATERAL generate_subscripts(
            constraint_meta.conkey,
            1
        ) AS key_position(position) ON TRUE
        JOIN pg_attribute AS source_attribute
          ON source_attribute.attrelid = source_table.oid
         AND source_attribute.attnum = constraint_meta.conkey[key_position.position]
        JOIN pg_attribute AS target_attribute
          ON target_attribute.attrelid = target_table.oid
         AND target_attribute.attnum = constraint_meta.confkey[key_position.position]
        WHERE constraint_meta.contype = 'f'
          AND target_namespace.nspname = :schema
          AND target_table.relname = :table_name
        GROUP BY
            source_namespace.nspname,
            source_table.relname,
            constraint_meta.conname,
            target_namespace.nspname,
            target_table.relname,
            constraint_meta.confdeltype,
            constraint_meta.convalidated
        ORDER BY
            source_namespace.nspname,
            source_table.relname,
            constraint_meta.conname
    """), {
        "schema": schema,
        "table_name": Course.__tablename__,
    }).mappings().all()
    return [
        {
            "schema": str(row["schema"]),
            "table": str(row["table_name"]),
            "constraint": row["constraint_name"],
            "columns": list(row["columns"] or ()),
            "referred_schema": str(row["referred_schema"]),
            "referred_table": str(row["referred_table"]),
            "referred_columns": list(row["referred_columns"] or ()),
            "ondelete": row["ondelete"],
            "validated": bool(row["validated"]),
        }
        for row in rows
    ]


def _reflected_course_foreign_keys(
    inspector: Any,
    schema: str,
    table_names: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table_name in sorted(table_names):
        for foreign_key in inspector.get_foreign_keys(
            table_name,
            schema=schema or None,
        ):
            referred_schema = str(foreign_key.get("referred_schema") or schema)
            if (
                foreign_key.get("referred_table") != Course.__tablename__
                or referred_schema != schema
            ):
                continue
            rows.append({
                "schema": schema,
                "table": table_name,
                "constraint": foreign_key.get("name"),
                "columns": list(foreign_key.get("constrained_columns") or ()),
                "referred_schema": referred_schema,
                "referred_table": foreign_key.get("referred_table"),
                "referred_columns": list(foreign_key.get("referred_columns") or ()),
                "ondelete": (foreign_key.get("options") or {}).get("ondelete"),
                "validated": True,
            })
    return rows


def _inventory_course_foreign_keys(
    loser_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inspector = inspect(db.session.connection())
    schema = str(inspector.default_schema_name or "")
    table_names = set(inspector.get_table_names(schema=schema or None))
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    seen: dict[tuple[str, tuple[str, ...]], int] = defaultdict(int)

    foreign_keys = (
        _postgresql_course_foreign_keys(schema)
        if db.engine.dialect.name == "postgresql"
        else _reflected_course_foreign_keys(inspector, schema, table_names)
    )
    for foreign_key in foreign_keys:
        source_schema = str(foreign_key["schema"])
        table_name = str(foreign_key["table"])
        columns = tuple(foreign_key["columns"])
        referred_columns = tuple(foreign_key["referred_columns"])
        key = (table_name, columns)
        is_default_schema = source_schema == schema
        if is_default_schema:
            seen[key] += 1
        entry = {
            "schema": source_schema,
            "table": table_name,
            "constraint": foreign_key["constraint"],
            "columns": list(columns),
            "referred_schema": foreign_key["referred_schema"],
            "referred_table": foreign_key["referred_table"],
            "referred_columns": list(referred_columns),
            "ondelete": foreign_key["ondelete"],
            "validated": foreign_key["validated"],
            "loser_reference_count": None,
        }

        is_supported_shape = len(columns) == 1 and referred_columns == ("id",)
        is_expected = is_default_schema and key in EXPECTED_COURSE_FOREIGN_KEYS
        if (
            not is_supported_shape
            or not is_expected
            or not foreign_key["validated"]
        ):
            blockers.append({
                "type": "unsupported_course_foreign_key",
                "schema": source_schema,
                "table": table_name,
                "constraint": foreign_key["constraint"],
                "columns": list(columns),
                "referred_columns": list(referred_columns),
                "validated": foreign_key["validated"],
            })
            inventory.append(entry)
            continue

        count = _count_loser_references(
            source_schema,
            table_name,
            columns[0],
            loser_ids,
        )
        entry["loser_reference_count"] = count
        inventory.append(entry)

        if count and key != ALLOWED_LOSER_FOREIGN_KEY:
            blockers.append({
                "type": "blocked_loser_foreign_key_references",
                "schema": source_schema,
                "table": table_name,
                "constraint": foreign_key["constraint"],
                "columns": list(columns),
                "loser_reference_count": count,
            })

    for table_name, columns in sorted(EXPECTED_COURSE_FOREIGN_KEYS):
        occurrence_count = seen.get((table_name, columns), 0)
        if occurrence_count == 1:
            continue

        reference_count = None
        if table_name in table_names:
            available_columns = {
                item["name"]
                for item in inspector.get_columns(table_name, schema=schema or None)
            }
            if len(columns) == 1 and columns[0] in available_columns:
                reference_count = _count_loser_references(
                    schema,
                    table_name,
                    columns[0],
                    loser_ids,
                )
        blockers.append({
            "type": (
                "missing_expected_course_foreign_key"
                if occurrence_count == 0
                else "duplicate_expected_course_foreign_key"
            ),
            "schema": schema,
            "table": table_name,
            "columns": list(columns),
            "constraint_count": occurrence_count,
            "loser_reference_count": reference_count,
        })

    return _sort_objects(inventory), _sort_objects(blockers)


def _legacy_cart_audit(
    pairs: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, _, by_normalized = _pair_indexes(pairs)
    course_rows = []
    bundle_rows = []

    for row in SchedulerUserCourseCart.query.order_by(
        SchedulerUserCourseCart.user_id,
        SchedulerUserCourseCart.semester_id,
        SchedulerUserCourseCart.course_code,
    ).all():
        pair = by_normalized.get(normalize_course_code(row.course_code))
        if pair is None or row.course_code == pair["survivor"]["code"]:
            continue
        course_rows.append({
            "user_id": row.user_id,
            "semester_id": row.semester_id,
            "course_code": row.course_code,
        })

    for row in SchedulerUserBundleCart.query.order_by(
        SchedulerUserBundleCart.user_id,
        SchedulerUserBundleCart.semester_id,
        SchedulerUserBundleCart.course_code,
        SchedulerUserBundleCart.id,
        SchedulerUserBundleCart.layer,
    ).all():
        pair = by_normalized.get(normalize_course_code(row.course_code))
        if pair is None or row.course_code == pair["survivor"]["code"]:
            continue
        bundle_rows.append({
            "user_id": row.user_id,
            "semester_id": row.semester_id,
            "course_code": row.course_code,
            "bundle_id": row.id,
            "layer": row.layer,
        })

    blockers = []
    if course_rows or bundle_rows:
        blockers.append({
            "type": "legacy_cart_alias_references",
            "course_cart_count": len(course_rows),
            "bundle_cart_count": len(bundle_rows),
        })
    return {
        "course_rows": course_rows,
        "bundle_rows": bundle_rows,
        "course_row_count": len(course_rows),
        "bundle_row_count": len(bundle_rows),
    }, blockers


def _course_tag_type_plan() -> dict[str, Any]:
    candidates = (
        TagType.query
        .filter(func.lower(TagType.name) == TagType.COURSE.lower())
        .order_by(TagType.id)
        .all()
    )
    selected = next((item for item in candidates if item.name == TagType.COURSE), None)
    selected = selected or (candidates[0] if candidates else None)
    return {
        "action": "reuse" if selected else "create",
        "selected_id": selected.id if selected else None,
        "selected_name": selected.name if selected else TagType.COURSE,
        "candidate_ids": [item.id for item in candidates],
    }


def _tag_actions(pairs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, by_loser_code, _ = _pair_indexes(pairs)
    tags = Tag.query.order_by(Tag.id).all()
    tags_by_name = {tag.name: tag for tag in tags}
    type_name_by_id = {
        tag_type.id: tag_type.name
        for tag_type in TagType.query.order_by(TagType.id).all()
    }
    post_ids_by_tag: dict[int, set[int]] = defaultdict(set)
    for post_id, tag_id in db.session.execute(
        select(post_tags.c.post_id, post_tags.c.tag_id)
    ).all():
        post_ids_by_tag[tag_id].add(post_id)

    actions = []
    blockers: list[dict[str, Any]] = []
    for source in tags:
        pair = by_loser_code.get(source.name)
        if pair is None and "-" in source.name:
            pair = by_loser_code.get(source.name.split("-", 1)[0])
        if pair is None:
            continue

        loser_code = pair["loser"]["code"]
        if source.name != loser_code and not source.name.startswith(f"{loser_code}-"):
            continue
        target_name = pair["survivor"]["code"] + source.name[len(loser_code):]
        target = tags_by_name.get(target_name)
        if target is not None and target.id == source.id:
            blockers.append({
                "type": "tag_maps_to_itself",
                "tag_id": source.id,
                "tag_name": source.name,
            })
            continue

        source_posts = post_ids_by_tag.get(source.id, set())
        target_posts = post_ids_by_tag.get(target.id, set()) if target is not None else set()
        actions.append({
            "normalized_code": pair["normalized_code"],
            "source_tag_id": source.id,
            "source_tag_row_sha256": _row_sha256(source),
            "source_name": source.name,
            "source_type": type_name_by_id.get(source.tag_type_id),
            "target_tag_id": target.id if target is not None else None,
            "target_tag_row_sha256": _row_sha256(target) if target is not None else None,
            "target_name": target_name,
            "target_type": type_name_by_id.get(target.tag_type_id) if target is not None else None,
            "action": "merge" if target is not None else "rename",
            "source_post_ids": sorted(source_posts),
            "target_post_ids": sorted(target_posts),
            "post_ids_to_link": sorted(source_posts - target_posts),
            "overlapping_post_link_count": len(source_posts & target_posts),
        })

    return {
        "course_tag_type": _course_tag_type_plan(),
        "actions": sorted(actions, key=lambda item: (item["source_name"], item["source_tag_id"])),
        "tag_count": len(actions),
        "rename_count": sum(item["action"] == "rename" for item in actions),
        "merge_count": sum(item["action"] == "merge" for item in actions),
        "source_post_link_count": sum(len(item["source_post_ids"]) for item in actions),
        "post_links_to_copy_count": sum(len(item["post_ids_to_link"]) for item in actions),
        "overlapping_post_link_count": sum(item["overlapping_post_link_count"] for item in actions),
    }, blockers


def build_reconciliation_plan() -> dict[str, Any]:
    pairs, blockers = _duplicate_pairs()
    loser_ids = [pair["loser"]["id"] for pair in pairs]

    normalized_code_uniqueness, uniqueness_blockers = _normalized_code_uniqueness_audit()
    record_actions, record_blockers = _user_course_record_actions(pairs)
    foreign_keys, foreign_key_blockers = _inventory_course_foreign_keys(loser_ids)
    legacy_carts, legacy_cart_blockers = _legacy_cart_audit(pairs)
    tags, tag_blockers = _tag_actions(pairs)

    blockers.extend(uniqueness_blockers)
    blockers.extend(record_blockers)
    blockers.extend(foreign_key_blockers)
    blockers.extend(legacy_cart_blockers)
    blockers.extend(tag_blockers)

    return {
        "version": PLAN_VERSION,
        "database": _database_identity(),
        "pair_count": len(pairs),
        "loser_count": len(loser_ids),
        "user_course_record_count": len(record_actions),
        "loser_fk_user_course_record_count": sum(
            action["old_course_id"] in loser_ids for action in record_actions
        ),
        "tag_count": tags["tag_count"],
        "pairs": pairs,
        "user_course_records": record_actions,
        "normalized_code_uniqueness": normalized_code_uniqueness,
        "foreign_keys": foreign_keys,
        "legacy_carts": legacy_carts,
        "tags": tags,
        "blockers": _sort_objects(blockers),
    }


def _control_errors(
    plan: dict[str, Any],
    sha256: str,
    *,
    expected_database: str | None,
    expected_plan_sha256: str | None,
    expected_pairs: int | None,
    expected_records: int | None,
    expected_tags: int | None,
) -> list[str]:
    required = {
        "expected_database": expected_database,
        "expected_plan_sha256": expected_plan_sha256,
        "expected_pairs": expected_pairs,
        "expected_records": expected_records,
        "expected_tags": expected_tags,
    }
    errors = [f"{name} is required for apply" for name, value in required.items() if value is None]
    if errors:
        return errors

    if expected_database != plan["database"]["name"]:
        errors.append(
            f"database mismatch: actual={plan['database']['name']!r} expected={expected_database!r}"
        )
    if expected_plan_sha256 != sha256:
        errors.append(f"plan SHA-256 mismatch: actual={sha256} expected={expected_plan_sha256}")
    for label, expected, actual in (
        ("pairs", expected_pairs, plan["pair_count"]),
        ("records", expected_records, plan["user_course_record_count"]),
        ("tags", expected_tags, plan["tag_count"]),
    ):
        if expected != actual:
            errors.append(f"{label} count mismatch: actual={actual} expected={expected}")
    return errors


def _lock_apply_tables(plan: dict[str, Any]) -> None:
    if db.engine.dialect.name != "postgresql":
        return
    inspector = inspect(db.session.connection())
    schema = str(inspector.default_schema_name or "")
    existing = set(inspector.get_table_names(schema=schema or None))
    table_names = {
        Course.__tablename__,
        UserCourseRecord.__tablename__,
        Tag.__tablename__,
        TagType.__tablename__,
        post_tags.name,
        SchedulerUserCourseCart.__tablename__,
        SchedulerUserBundleCart.__tablename__,
    }
    table_names.update(item["table"] for item in plan["foreign_keys"])
    table_names &= existing
    preparer = db.engine.dialect.identifier_preparer
    schema_prefix = f"{preparer.quote_schema(schema)}." if schema else ""
    rendered = ", ".join(
        f"{schema_prefix}{preparer.quote_identifier(name)}"
        for name in sorted(table_names)
    )
    if rendered:
        db.session.execute(
            text("SELECT set_config('lock_timeout', :lock_timeout, true)"),
            {"lock_timeout": APPLY_LOCK_TIMEOUT},
        )
        db.session.execute(text(f"LOCK TABLE {rendered} IN SHARE ROW EXCLUSIVE MODE"))


def _ensure_course_tag_type(plan: dict[str, Any]) -> int:
    selected_id = plan["tags"]["course_tag_type"]["selected_id"]
    if selected_id is not None:
        selected = db.session.get(TagType, selected_id)
        if selected is None or selected.name.lower() != TagType.COURSE.lower():
            raise ReconciliationError("planned course tag type no longer exists")
        return selected.id

    existing = (
        TagType.query
        .filter(func.lower(TagType.name) == TagType.COURSE.lower())
        .order_by(TagType.id)
        .first()
    )
    if existing is not None:
        return existing.id
    created = TagType(name=TagType.COURSE)
    db.session.add(created)
    db.session.flush()
    return created.id


def _apply_user_course_records(plan: dict[str, Any]) -> int:
    updated = 0
    for action in plan["user_course_records"]:
        result = db.session.execute(
            update(UserCourseRecord)
            .where(UserCourseRecord.id == action["record_id"])
            .values(
                course_id=action["new_course_id"],
                course_code=action["new_course_code"],
            )
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise ReconciliationError(
                f"user course record {action['record_id']} changed after planning"
            )
        updated += 1
    db.session.flush()
    db.session.expire_all()
    return updated


def _apply_tags(plan: dict[str, Any]) -> dict[str, int]:
    course_tag_type_id = _ensure_course_tag_type(plan)
    renamed = 0
    merged = 0
    linked = 0

    for action in plan["tags"]["actions"]:
        source = db.session.get(Tag, action["source_tag_id"])
        if source is None or source.name != action["source_name"]:
            raise ReconciliationError(
                f"source tag {action['source_tag_id']} changed after planning"
            )

        if action["action"] == "rename":
            collision = Tag.query.filter_by(name=action["target_name"]).first()
            if collision is not None and collision.id != source.id:
                raise ReconciliationError(
                    f"tag target {action['target_name']!r} appeared after planning"
                )
            source.name = action["target_name"]
            source.tag_type_id = course_tag_type_id
            renamed += 1
            continue

        target = db.session.get(Tag, action["target_tag_id"])
        if target is None or target.name != action["target_name"]:
            raise ReconciliationError(
                f"target tag {action['target_tag_id']} changed after planning"
            )
        target.tag_type_id = course_tag_type_id
        for post_id in action["post_ids_to_link"]:
            db.session.execute(post_tags.insert().values(
                post_id=post_id,
                tag_id=target.id,
            ))
            linked += 1
        db.session.execute(delete(post_tags).where(post_tags.c.tag_id == source.id))
        db.session.execute(delete(Tag).where(Tag.id == source.id))
        merged += 1

    db.session.flush()
    return {
        "course_tag_type_id": course_tag_type_id,
        "renamed": renamed,
        "merged": merged,
        "post_links_copied": linked,
    }


def _verify_no_loser_foreign_keys(plan: dict[str, Any]) -> None:
    loser_ids = [pair["loser"]["id"] for pair in plan["pairs"]]
    remaining = []
    for item in plan["foreign_keys"]:
        columns = item["columns"]
        if len(columns) != 1 or item["referred_columns"] != ["id"]:
            remaining.append({**item, "verification_error": "unsupported foreign key shape"})
            continue
        referenced_table = table(
            item["table"],
            column(columns[0]),
            schema=item.get("schema") or None,
        )
        count = int(db.session.execute(
            select(func.count())
            .select_from(referenced_table)
            .where(referenced_table.c[columns[0]].in_(loser_ids))
        ).scalar_one()) if loser_ids else 0
        if count:
            remaining.append({**item, "loser_reference_count": count})
    if remaining:
        raise ReconciliationError(
            "loser foreign-key references remain after the planned rewrites: "
            + _canonical_json({"remaining": remaining})
        )


def _apply_courses(plan: dict[str, Any]) -> int:
    loser_ids = [pair["loser"]["id"] for pair in plan["pairs"]]
    _verify_no_loser_foreign_keys(plan)

    if loser_ids:
        result = db.session.execute(delete(Course).where(Course.id.in_(loser_ids)))
        if result.rowcount != len(loser_ids):
            raise ReconciliationError(
                f"deleted {result.rowcount} loser courses; expected {len(loser_ids)}"
            )

    for pair in plan["pairs"]:
        result = db.session.execute(
            update(Course)
            .where(Course.id == pair["survivor"]["id"])
            .values(normalized_code=pair["normalized_code"])
        )
        if result.rowcount != 1:
            raise ReconciliationError(
                f"survivor course {pair['survivor']['id']} changed after planning"
            )
    db.session.flush()
    return len(loser_ids)


def _verify_applied_plan(plan: dict[str, Any], course_tag_type_id: int | None = None) -> None:
    loser_ids = [pair["loser"]["id"] for pair in plan["pairs"]]
    if loser_ids and Course.query.filter(Course.id.in_(loser_ids)).count():
        raise ReconciliationError("one or more loser courses still exist")

    for pair in plan["pairs"]:
        survivor = db.session.get(Course, pair["survivor"]["id"])
        if (
            survivor is None
            or survivor.code != pair["survivor"]["code"]
            or survivor.normalized_code != pair["normalized_code"]
        ):
            raise ReconciliationError(
                f"survivor {pair['survivor']['id']} failed normalization verification"
            )

    for action in plan["user_course_records"]:
        record = db.session.get(UserCourseRecord, action["record_id"])
        if (
            record is None
            or record.course_id != action["new_course_id"]
            or record.course_code != action["new_course_code"]
        ):
            raise ReconciliationError(
                f"user course record {action['record_id']} failed verification"
            )

    for action in plan["tags"]["actions"]:
        if action["action"] == "rename":
            target = db.session.get(Tag, action["source_tag_id"])
        else:
            target = db.session.get(Tag, action["target_tag_id"])
            if db.session.get(Tag, action["source_tag_id"]) is not None:
                raise ReconciliationError(
                    f"merged source tag {action['source_tag_id']} still exists"
                )
        if target is None or target.name != action["target_name"]:
            raise ReconciliationError(
                f"tag target {action['target_name']!r} failed verification"
            )
        if course_tag_type_id is not None and target.tag_type_id != course_tag_type_id:
            raise ReconciliationError(
                f"tag target {action['target_name']!r} is not a course tag"
            )
        target_post_ids = set(db.session.execute(
            select(post_tags.c.post_id).where(post_tags.c.tag_id == target.id)
        ).scalars())
        if not set(action["source_post_ids"]).issubset(target_post_ids):
            raise ReconciliationError(
                f"tag target {action['target_name']!r} lost post associations"
            )


def _apply_plan(plan: dict[str, Any]) -> dict[str, Any]:
    records_updated = _apply_user_course_records(plan)
    tag_summary = _apply_tags(plan)
    losers_deleted = _apply_courses(plan)
    db.session.expire_all()
    _verify_applied_plan(
        plan,
        course_tag_type_id=tag_summary["course_tag_type_id"],
    )
    return {
        "pairs_reconciled": plan["pair_count"],
        "losers_deleted": losers_deleted,
        "user_course_records_updated": records_updated,
        "tags_renamed": tag_summary["renamed"],
        "tags_merged": tag_summary["merged"],
        "post_links_copied": tag_summary["post_links_copied"],
    }


def run_reconciliation(
    *,
    apply: bool = False,
    expected_database: str | None = None,
    expected_plan_sha256: str | None = None,
    expected_pairs: int | None = None,
    expected_records: int | None = None,
    expected_tags: int | None = None,
) -> dict[str, Any]:
    try:
        plan = build_reconciliation_plan()
        sha256 = plan_sha256(plan)
        result = {
            "status": "blocked" if plan["blockers"] else "dry-run",
            "mode": "apply" if apply else "dry-run",
            "plan_sha256": sha256,
            "plan": plan,
        }

        if not apply:
            db.session.rollback()
            return result

        control_errors = _control_errors(
            plan,
            sha256,
            expected_database=expected_database,
            expected_plan_sha256=expected_plan_sha256,
            expected_pairs=expected_pairs,
            expected_records=expected_records,
            expected_tags=expected_tags,
        )
        if plan["blockers"] or control_errors:
            result["status"] = "blocked"
            result["control_errors"] = control_errors
            db.session.rollback()
            return result

        _lock_apply_tables(plan)
        # The first plan populated the ORM identity map before locks were held.
        # Force the locked replan to reload rows that another transaction could
        # have committed while this transaction was waiting for those locks.
        db.session.expire_all()
        locked_plan = build_reconciliation_plan()
        locked_sha256 = plan_sha256(locked_plan)
        if locked_sha256 != sha256 or locked_sha256 != expected_plan_sha256:
            result["status"] = "blocked"
            result["control_errors"] = [
                "reconciliation plan changed while acquiring apply locks: "
                f"before={sha256} locked={locked_sha256}"
            ]
            result["locked_plan_sha256"] = locked_sha256
            db.session.rollback()
            return result

        applied = _apply_plan(locked_plan)
        db.session.commit()
        result["status"] = "applied"
        result["applied"] = applied
        return result
    except Exception:
        db.session.rollback()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile simple whitespace-variant Course duplicates.",
    )
    parser.add_argument("--database-url", help="Optional database URL; otherwise DATABASE_URL is used.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reviewed plan. Without this flag the command is a dry-run.",
    )
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--expected-pairs", type=int)
    parser.add_argument("--expected-records", type=int)
    parser.add_argument("--expected-tags", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    app = create_import_app(args.database_url)
    try:
        with app.app_context():
            result = run_reconciliation(
                apply=args.apply,
                expected_database=args.expected_database,
                expected_plan_sha256=args.expected_plan_sha256,
                expected_pairs=args.expected_pairs,
                expected_records=args.expected_records,
                expected_tags=args.expected_tags,
            )
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "message": str(exc),
        }, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "blocked":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
