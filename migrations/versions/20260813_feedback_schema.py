"""put feedback schema under Alembic ownership

Revision ID: 20260813_feedback_schema
Revises: 5202003d1ec0
Create Date: 2026-08-13

Historically these objects were created by ``db.metadata.create_all`` during
application startup.  This revision adopts that exact PostgreSQL schema when
it already exists, or creates it when none of the feedback tables exist.  A
partial or incompatible legacy schema is deliberately rejected before any DDL
is issued.
"""

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_feedback_schema"
down_revision = "5202003d1ec0"
branch_labels = None
depends_on = None


_FEEDBACK_TABLE_NAMES = (
    "feedbacks",
    "feedback_versions",
    "feedback_merge_requests",
    "feedback_comments",
    "feedback_merge_comments",
    "feedback_audit_events",
)


def _column(type_signature, nullable):
    return {"type": type_signature, "nullable": nullable}


_TABLE_SPECS = {
    "feedbacks": {
        "columns": {
            "id": _column(("integer",), False),
            "author_id": _column(("integer",), False),
            "title": _column(("varchar", 200), False),
            "status": _column(("varchar", 40), False),
            "current_version_id": _column(("integer",), True),
            "comments_ended": _column(("boolean",), False),
            "created_at": _column(("timestamp", True), False),
            "published_at": _column(("timestamp", True), True),
            "rejected_at": _column(("timestamp", True), True),
            "closed_at": _column(("timestamp", True), True),
            "updated_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("author_id",), "users", ("id",)),
            (("current_version_id",), "feedback_versions", ("id",)),
        },
        "uniques": set(),
        "indexes": {
            ("ix_feedbacks_author_id", ("author_id",), False),
            ("ix_feedbacks_status", ("status",), False),
            ("idx_feedbacks_status_updated", ("status", "updated_at"), False),
        },
    },
    "feedback_versions": {
        "columns": {
            "id": _column(("integer",), False),
            "feedback_id": _column(("integer",), False),
            "version_number": _column(("integer",), False),
            "markdown_content": _column(("text",), False),
            "created_by_user_id": _column(("integer",), False),
            "source_merge_request_id": _column(("integer",), True),
            "created_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("feedback_id",), "feedbacks", ("id",)),
            (("created_by_user_id",), "users", ("id",)),
            (("source_merge_request_id",), "feedback_merge_requests", ("id",)),
        },
        "uniques": {
            (
                "uq_feedback_versions_feedback_version_number",
                ("feedback_id", "version_number"),
            ),
        },
        "indexes": {
            ("ix_feedback_versions_feedback_id", ("feedback_id",), False),
        },
    },
    "feedback_merge_requests": {
        "columns": {
            "id": _column(("integer",), False),
            "feedback_id": _column(("integer",), False),
            "author_id": _column(("integer",), False),
            "base_version_id": _column(("integer",), False),
            "title": _column(("varchar", 200), False),
            "change_summary": _column(("text",), True),
            "proposed_markdown_content": _column(("text",), False),
            "status": _column(("varchar", 50), False),
            "author_reviewed_at": _column(("timestamp", True), True),
            "author_review_note": _column(("text",), True),
            "admin_reviewed_at": _column(("timestamp", True), True),
            "admin_review_note": _column(("text",), True),
            "merged_version_id": _column(("integer",), True),
            "created_at": _column(("timestamp", True), False),
            "updated_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("feedback_id",), "feedbacks", ("id",)),
            (("author_id",), "users", ("id",)),
            (("base_version_id",), "feedback_versions", ("id",)),
            (("merged_version_id",), "feedback_versions", ("id",)),
        },
        "uniques": set(),
        "indexes": {
            ("ix_feedback_merge_requests_feedback_id", ("feedback_id",), False),
            ("ix_feedback_merge_requests_author_id", ("author_id",), False),
            ("ix_feedback_merge_requests_status", ("status",), False),
            (
                "idx_feedback_merge_requests_feedback_status",
                ("feedback_id", "status"),
                False,
            ),
        },
    },
    "feedback_comments": {
        "columns": {
            "id": _column(("integer",), False),
            "feedback_id": _column(("integer",), False),
            "user_id": _column(("integer",), False),
            "parent_comment_id": _column(("integer",), True),
            "content": _column(("text",), False),
            "visibility": _column(("varchar", 20), False),
            "hidden_reason": _column(("text",), True),
            "hidden_by_admin_id": _column(("integer",), True),
            "hidden_at": _column(("timestamp", True), True),
            "created_at": _column(("timestamp", True), False),
            "updated_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("feedback_id",), "feedbacks", ("id",)),
            (("user_id",), "users", ("id",)),
            (("parent_comment_id",), "feedback_comments", ("id",)),
            (("hidden_by_admin_id",), "users", ("id",)),
        },
        "uniques": set(),
        "indexes": {
            ("ix_feedback_comments_feedback_id", ("feedback_id",), False),
            ("ix_feedback_comments_user_id", ("user_id",), False),
            ("ix_feedback_comments_visibility", ("visibility",), False),
        },
    },
    "feedback_merge_comments": {
        "columns": {
            "id": _column(("integer",), False),
            "merge_request_id": _column(("integer",), False),
            "user_id": _column(("integer",), False),
            "parent_comment_id": _column(("integer",), True),
            "content": _column(("text",), False),
            "visibility": _column(("varchar", 20), False),
            "hidden_reason": _column(("text",), True),
            "hidden_by_admin_id": _column(("integer",), True),
            "hidden_at": _column(("timestamp", True), True),
            "created_at": _column(("timestamp", True), False),
            "updated_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("merge_request_id",), "feedback_merge_requests", ("id",)),
            (("user_id",), "users", ("id",)),
            (("parent_comment_id",), "feedback_merge_comments", ("id",)),
            (("hidden_by_admin_id",), "users", ("id",)),
        },
        "uniques": set(),
        "indexes": {
            (
                "ix_feedback_merge_comments_merge_request_id",
                ("merge_request_id",),
                False,
            ),
            ("ix_feedback_merge_comments_user_id", ("user_id",), False),
            ("ix_feedback_merge_comments_visibility", ("visibility",), False),
        },
    },
    "feedback_audit_events": {
        "columns": {
            "id": _column(("integer",), False),
            "feedback_id": _column(("integer",), False),
            "merge_request_id": _column(("integer",), True),
            "actor_user_id": _column(("integer",), False),
            "event_type": _column(("varchar", 80), False),
            "event_payload": _column(("json",), False),
            "created_at": _column(("timestamp", True), False),
        },
        "foreign_keys": {
            (("feedback_id",), "feedbacks", ("id",)),
            (("merge_request_id",), "feedback_merge_requests", ("id",)),
            (("actor_user_id",), "users", ("id",)),
        },
        "uniques": set(),
        "indexes": {
            ("ix_feedback_audit_events_feedback_id", ("feedback_id",), False),
            (
                "ix_feedback_audit_events_merge_request_id",
                ("merge_request_id",),
                False,
            ),
            ("ix_feedback_audit_events_actor_user_id", ("actor_user_id",), False),
            ("ix_feedback_audit_events_event_type", ("event_type",), False),
        },
    },
}


def _fail(message):
    raise RuntimeError(
        "feedback schema migration refused an incompatible database: " + message
    )


def _relation_kind(bind, relation_name):
    return bind.execute(
        sa.text(
            """
            SELECT relation.relkind
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :relation_name
            """
        ),
        {"relation_name": relation_name},
    ).scalar_one_or_none()


def _type_signature(column_type):
    if isinstance(column_type, postgresql.JSONB):
        return ("jsonb",)
    if isinstance(column_type, sa.JSON):
        return ("json",)
    if isinstance(column_type, sa.Text):
        return ("text",)
    if isinstance(column_type, sa.String):
        return ("varchar", column_type.length)
    if isinstance(column_type, sa.DateTime):
        return ("timestamp", bool(column_type.timezone))
    if isinstance(column_type, sa.Boolean):
        return ("boolean",)
    if isinstance(column_type, sa.Integer) and not isinstance(
        column_type,
        (sa.BigInteger, sa.SmallInteger),
    ):
        return ("integer",)
    return ("unsupported", str(column_type))


def _assert_columns(inspector, table_name, expected_columns):
    actual_columns = {
        column["name"]: column
        for column in inspector.get_columns(table_name)
    }
    if set(actual_columns) != set(expected_columns):
        _fail(
            f"{table_name} columns are {sorted(actual_columns)}; "
            f"expected {sorted(expected_columns)}"
        )

    for column_name, expected in expected_columns.items():
        actual = actual_columns[column_name]
        actual_type = _type_signature(actual["type"])
        if actual_type != expected["type"]:
            _fail(
                f"{table_name}.{column_name} has type {actual_type}; "
                f"expected {expected['type']}"
            )
        if bool(actual["nullable"]) != expected["nullable"]:
            _fail(
                f"{table_name}.{column_name} nullable={actual['nullable']}; "
                f"expected {expected['nullable']}"
            )
        if column_name != "id" and actual.get("default") is not None:
            _fail(
                f"{table_name}.{column_name} has unexpected server default "
                f"{actual['default']!r}"
            )
        if actual.get("identity") is not None or actual.get("computed") is not None:
            _fail(f"{table_name}.{column_name} is unexpectedly generated")


def _assert_serial_primary_key(bind, inspector, table_name):
    primary_key = inspector.get_pk_constraint(table_name)
    if tuple(primary_key.get("constrained_columns") or ()) != ("id",):
        _fail(f"{table_name} must have exactly id as its primary key")

    schema_name = bind.execute(sa.text("SELECT current_schema()" )).scalar_one()
    qualified_table = f"{schema_name}.{table_name}"
    serial_sequence = bind.execute(
        sa.text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
        {"table_name": qualified_table},
    ).scalar_one()
    if serial_sequence is None:
        _fail(f"{table_name}.id must own a PostgreSQL serial sequence")

    identity_kind, default_expression = bind.execute(
        sa.text(
            """
            SELECT attribute.attidentity,
                   pg_get_expr(attribute_default.adbin, attribute_default.adrelid)
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attname = 'id'
             AND NOT attribute.attisdropped
            LEFT JOIN pg_catalog.pg_attrdef AS attribute_default
              ON attribute_default.adrelid = relation.oid
             AND attribute_default.adnum = attribute.attnum
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :table_name
            """
        ),
        {"table_name": table_name},
    ).one()
    if identity_kind:
        _fail(f"{table_name}.id is an identity column; expected serial")

    match = re.fullmatch(r"nextval\('([^']+)'::regclass\)", default_expression or "")
    if match is None:
        _fail(
            f"{table_name}.id default {default_expression!r} does not use its serial sequence"
        )
    default_uses_owned_sequence = bind.execute(
        sa.text(
            "SELECT to_regclass(:default_sequence)::oid = "
            "to_regclass(:owned_sequence)::oid"
        ),
        {
            "default_sequence": match.group(1),
            "owned_sequence": serial_sequence,
        },
    ).scalar_one()
    if not default_uses_owned_sequence:
        _fail(f"{table_name}.id default does not use its owned serial sequence")


def _assert_foreign_keys(inspector, table_name, expected_foreign_keys):
    actual_foreign_keys = set()
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("referred_schema") is not None:
            _fail(
                f"{table_name} foreign key unexpectedly references schema "
                f"{foreign_key['referred_schema']!r}"
            )
        options = foreign_key.get("options") or {}
        if options:
            _fail(f"{table_name} foreign key has unexpected options {options!r}")
        actual_foreign_keys.add(
            (
                tuple(foreign_key.get("constrained_columns") or ()),
                foreign_key.get("referred_table"),
                tuple(foreign_key.get("referred_columns") or ()),
            )
        )
    if actual_foreign_keys != expected_foreign_keys:
        _fail(
            f"{table_name} foreign keys are {sorted(actual_foreign_keys)!r}; "
            f"expected {sorted(expected_foreign_keys)!r}"
        )


def _assert_uniques(inspector, table_name, expected_uniques):
    actual_uniques = {
        (
            constraint.get("name"),
            tuple(constraint.get("column_names") or ()),
        )
        for constraint in inspector.get_unique_constraints(table_name)
    }
    if actual_uniques != expected_uniques:
        _fail(
            f"{table_name} unique constraints are {sorted(actual_uniques)!r}; "
            f"expected {sorted(expected_uniques)!r}"
        )


def _assert_indexes(inspector, table_name, expected_indexes):
    actual_indexes = set()
    for index in inspector.get_indexes(table_name):
        if index.get("duplicates_constraint"):
            continue
        column_names = tuple(index.get("column_names") or ())
        if not all(column_names):
            _fail(f"{table_name} has an unsupported expression index {index!r}")
        dialect_options = index.get("dialect_options") or {}
        where_clause = dialect_options.get("postgresql_where")
        include_columns = tuple(
            dialect_options.get("postgresql_include")
            or index.get("include_columns")
            or ()
        )
        column_sorting = index.get("column_sorting") or {}
        nulls_not_distinct = dialect_options.get("postgresql_nulls_not_distinct")
        if (
            where_clause is not None
            or include_columns
            or column_sorting
            or nulls_not_distinct
        ):
            _fail(f"{table_name} has an unexpected specialized index {index!r}")
        actual_indexes.add(
            (index.get("name"), column_names, bool(index.get("unique")))
        )
    if actual_indexes != expected_indexes:
        _fail(
            f"{table_name} indexes are {sorted(actual_indexes)!r}; "
            f"expected {sorted(expected_indexes)!r}"
        )


def _assert_table(bind, inspector, table_name, spec):
    if _relation_kind(bind, table_name) != "r":
        _fail(f"{table_name} is not an ordinary PostgreSQL table")
    _assert_columns(inspector, table_name, spec["columns"])
    _assert_serial_primary_key(bind, inspector, table_name)
    _assert_foreign_keys(inspector, table_name, spec["foreign_keys"])
    _assert_uniques(inspector, table_name, spec["uniques"])
    _assert_indexes(inspector, table_name, spec["indexes"])
    check_constraints = inspector.get_check_constraints(table_name)
    if check_constraints:
        _fail(f"{table_name} has unexpected check constraints {check_constraints!r}")


def _notification_link_state(bind, inspector):
    if _relation_kind(bind, "notifications") != "r":
        _fail("notifications is missing or is not an ordinary PostgreSQL table")
    notification_columns = {
        column["name"]: column
        for column in inspector.get_columns("notifications")
    }
    link_column = notification_columns.get("link_url")
    if link_column is None:
        return "missing"
    if _type_signature(link_column["type"]) != ("varchar", 255):
        _fail("notifications.link_url must be VARCHAR(255)")
    if not link_column["nullable"]:
        _fail("notifications.link_url must be nullable")
    if link_column.get("default") is not None:
        _fail("notifications.link_url must not have a server default")
    if link_column.get("identity") is not None or link_column.get("computed") is not None:
        _fail("notifications.link_url must not be generated")
    return "compatible"


def _assert_expected_names_are_free(bind):
    expected_relation_names = {
        *(f"{table_name}_id_seq" for table_name in _FEEDBACK_TABLE_NAMES),
        *(f"{table_name}_pkey" for table_name in _FEEDBACK_TABLE_NAMES),
        "uq_feedback_versions_feedback_version_number",
        *(
            index_name
            for spec in _TABLE_SPECS.values()
            for index_name, _columns, _unique in spec["indexes"]
        ),
    }
    collisions = sorted(
        relation_name
        for relation_name in expected_relation_names
        if _relation_kind(bind, relation_name) is not None
    )
    if collisions:
        _fail(
            "feedback tables are absent but expected relation names already exist: "
            + ", ".join(collisions)
        )


def _preflight():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        _fail("this adoption migration supports PostgreSQL only")

    relation_kinds = {
        table_name: _relation_kind(bind, table_name)
        for table_name in _FEEDBACK_TABLE_NAMES
    }
    present = {name for name, kind in relation_kinds.items() if kind is not None}
    if present and present != set(_FEEDBACK_TABLE_NAMES):
        missing = sorted(set(_FEEDBACK_TABLE_NAMES) - present)
        _fail(
            f"partial feedback schema; present={sorted(present)}, missing={missing}"
        )
    for table_name, relation_kind in relation_kinds.items():
        if relation_kind is not None and relation_kind != "r":
            _fail(f"{table_name} exists with PostgreSQL relkind {relation_kind!r}")

    inspector = sa.inspect(bind)
    notification_link_state = _notification_link_state(bind, inspector)
    if present:
        for table_name, spec in _TABLE_SPECS.items():
            _assert_table(bind, inspector, table_name, spec)
        feedback_state = "compatible"
    else:
        _assert_expected_names_are_free(bind)
        feedback_state = "missing"
    return feedback_state, notification_link_state


def _create_feedback_tables():
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("comments_ended", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feedback_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feedback_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("markdown_content", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("source_merge_request_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedbacks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "feedback_id",
            "version_number",
            name="uq_feedback_versions_feedback_version_number",
        ),
    )
    op.create_table(
        "feedback_merge_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feedback_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("proposed_markdown_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("author_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_review_note", sa.Text(), nullable=True),
        sa.Column("admin_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admin_review_note", sa.Text(), nullable=True),
        sa.Column("merged_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["base_version_id"], ["feedback_versions.id"]),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedbacks.id"]),
        sa.ForeignKeyConstraint(["merged_version_id"], ["feedback_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key(
        "feedbacks_current_version_id_fkey",
        "feedbacks",
        "feedback_versions",
        ["current_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "feedback_versions_source_merge_request_id_fkey",
        "feedback_versions",
        "feedback_merge_requests",
        ["source_merge_request_id"],
        ["id"],
    )
    op.create_table(
        "feedback_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feedback_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_comment_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("hidden_reason", sa.Text(), nullable=True),
        sa.Column("hidden_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedbacks.id"]),
        sa.ForeignKeyConstraint(["hidden_by_admin_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["feedback_comments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feedback_merge_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("merge_request_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_comment_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("hidden_reason", sa.Text(), nullable=True),
        sa.Column("hidden_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["hidden_by_admin_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["merge_request_id"],
            ["feedback_merge_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"],
            ["feedback_merge_comments.id"],
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "feedback_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feedback_id", sa.Integer(), nullable=False),
        sa.Column("merge_request_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("event_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedbacks.id"]),
        sa.ForeignKeyConstraint(
            ["merge_request_id"],
            ["feedback_merge_requests.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    for table_name, spec in _TABLE_SPECS.items():
        for index_name, columns, unique in sorted(spec["indexes"]):
            op.create_index(index_name, table_name, list(columns), unique=unique)


def upgrade():
    feedback_state, notification_link_state = _preflight()
    if feedback_state == "missing":
        _create_feedback_tables()
    if notification_link_state == "missing":
        op.add_column(
            "notifications",
            sa.Column("link_url", sa.String(length=255), nullable=True),
        )

    # Validate the resulting schema too.  PostgreSQL transactional DDL means an
    # implementation or environmental mismatch rolls the whole revision back.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, spec in _TABLE_SPECS.items():
        _assert_table(bind, inspector, table_name, spec)
    if _notification_link_state(bind, inspector) != "compatible":
        _fail("notifications.link_url was not installed")


def downgrade():
    # This revision adopts tables that may predate Alembic and may contain user
    # data.  Alembic cannot distinguish adopted objects from objects it created,
    # so an automatic downgrade could destroy production feedback.  Roll back
    # by restoring a verified pre-migration backup or use a reviewed forward
    # migration instead.
    raise RuntimeError(
        "downgrade refused: the feedback schema may contain adopted user data; "
        "restore a verified backup or apply a reviewed forward migration"
    )
