"""Register the existing TeamUp application without moving its runtime or data."""
from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa

revision = "20260907_teamup_makerspace"
down_revision = "20260907_makerspace"
branch_labels = None
depends_on = None

# School preflight requires the creator account before migration.
expected_seed_counts = {"public.maker_spaces": 1, "public.maker_audit_events": 1}

OWNER_EMAIL = "fning477@connect.hkust-gz.edu.cn"
SPACE_ID = "1d2c18d90c3f4f00812c318ebd5876ef"


def upgrade():
    connection = op.get_bind()
    owners = connection.execute(sa.text("SELECT id FROM users WHERE lower(email)=:email AND email_verified=true AND is_deleted=false"), {"email": OWNER_EMAIL}).fetchall()
    if len(owners) > 1:
        raise RuntimeError("TeamUp creator identity is ambiguous; stop for account verification")
    if not owners:
        # Disposable CI/dev databases need not contain a real school account.
        # Production preflight MUST establish one match before this release.
        return
    owner_id = owners[0][0]
    existing = connection.execute(sa.text("SELECT id, owner_id, kind, external_path FROM maker_spaces WHERE slug='teamup'")).fetchone()
    if existing:
        if tuple(existing) != (SPACE_ID, owner_id, "external", "/teamup/"):
            raise RuntimeError("Existing TeamUp registration differs; explicit ownership migration required")
        return
    stamp = datetime.now(timezone.utc)
    public = {"title_zh": "课程组队", "title_en": "Course TeamUp", "description_zh": "寻找同课程的伙伴，创建小组、申请加入，一起完成课程项目。", "description_en": "Find classmates, create a team and work together on course projects.", "category": "learning"}
    table = sa.table("maker_spaces", sa.column("id", sa.String()), sa.column("slug", sa.String()), sa.column("owner_id", sa.Integer()), sa.column("title_zh", sa.String()), sa.column("title_en", sa.String()), sa.column("description_zh", sa.Text()), sa.column("description_en", sa.Text()), sa.column("category", sa.String()), sa.column("status", sa.String()), sa.column("kind", sa.String()), sa.column("external_path", sa.String()), sa.column("repository", sa.String()), sa.column("branch", sa.String()), sa.column("settings", sa.JSON()), sa.column("auto_deploy", sa.Boolean()), sa.column("published_metadata", sa.JSON()), sa.column("version", sa.Integer()), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    connection.execute(table.insert().values(id=SPACE_ID, slug="teamup", owner_id=owner_id, **public, status="published", kind="external", external_path="/teamup/", repository="Endorphin-168/group-match-mini", branch="main", settings={"companion_repository": "Endorphin-168/group-match-backend"}, auto_deploy=False, published_metadata=public, version=1, created_at=stamp, updated_at=stamp))
    audit = sa.table("maker_audit_events", sa.column("space_id", sa.String()), sa.column("actor_id", sa.Integer()), sa.column("action", sa.String()), sa.column("details", sa.JSON()), sa.column("created_at", sa.DateTime(timezone=True)))
    connection.execute(audit.insert().values(space_id=SPACE_ID, actor_id=None, action="existing_integration_registered", details={"source": "explicit-owner-assignment-in-release-plan", "runtime_moved": False}, created_at=stamp))


def downgrade():
    raise RuntimeError("Do not delete a creator's space through automatic downgrade")
