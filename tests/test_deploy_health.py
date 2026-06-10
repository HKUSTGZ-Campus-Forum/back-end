from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_revision_chain_references_existing_revisions():
    revision_files = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    revisions = {}
    down_revisions = {}

    for path in revision_files:
        text = path.read_text(encoding="utf-8")
        revision_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if not revision_match:
            continue
        revision = revision_match.group(1)
        revisions[revision] = path

        down_match = re.search(r'^down_revision\s*=\s*(.+)$', text, re.MULTILINE)
        down_revisions[revision] = down_match.group(1).strip() if down_match else "None"

    missing = []
    for revision, raw_down_revision in down_revisions.items():
        if raw_down_revision in {"None", "null"}:
            continue
        for down_revision in re.findall(r'["\']([^"\']+)["\']', raw_down_revision):
            if down_revision not in revisions:
                missing.append((revision, down_revision, revisions[revision].name))

    assert missing == []


def test_alembic_revision_ids_fit_existing_version_table_width():
    revision_files = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    too_long = []

    for path in revision_files:
        text = path.read_text(encoding="utf-8")
        revision_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if revision_match and len(revision_match.group(1)) > 32:
            too_long.append((path.name, revision_match.group(1)))

    assert too_long == []


def test_course_domain_migration_is_safe_for_auto_initialized_dev_tables():
    migration_path = ROOT / "migrations" / "versions" / "20260607_course_domain_redesign.py"
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "domain_tables.issubset" in migration_text
    assert "return" in migration_text
    assert "_has_unique_constraint_or_index" in migration_text


def test_auto_initialized_migrations_are_idempotent():
    guarded_migrations = [
        (
            "20260529_academic_map.py",
            ['_table_exists("curriculum_programs")', '_index_exists("curriculum_programs"'],
        ),
        (
            "20260531_add_scheduler_fields.py",
            ["column.name not in existing"],
        ),
        (
            "20260531_add_scheduler_section_lecture_map_cart.py",
            ['inspector.has_table("scheduler_sections")', '"idx_scheduler_sections_course" not in indexes'],
        ),
        (
            "20260607_course_domain_redesign.py",
            ["domain_tables.issubset"],
        ),
    ]

    for filename, guard_texts in guarded_migrations:
        migration_text = (ROOT / "migrations" / "versions" / filename).read_text(encoding="utf-8")
        for guard_text in guard_texts:
            assert guard_text in migration_text


def test_deploy_workflows_fail_on_migration_errors_and_use_committed_revisions():
    workflow_paths = [
        ROOT / ".github" / "workflows" / "deploy.yml",
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml",
    ]

    for path in workflow_paths:
        deploy_workflow = path.read_text(encoding="utf-8")
        assert "set -e" in deploy_workflow
        assert "flask db upgrade heads" in deploy_workflow
        assert "flask db migrate" not in deploy_workflow


def test_production_deploy_backfills_2024_25_scheduler_offerings():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-backend-prod.yml").read_text(encoding="utf-8")

    assert "python -m app.scripts.backfill_legacy_scheduler_offerings --semesters 2430 2440 --apply" in deploy_workflow
    assert deploy_workflow.index("python -m app.scripts.init_db") < deploy_workflow.index(
        "python -m app.scripts.backfill_legacy_scheduler_offerings"
    )


def test_course_domain_migration_workflows_support_dry_run_and_apply():
    workflow_paths = [
        ROOT / ".github" / "workflows" / "migrate-course-domain-dev.yml",
        ROOT / ".github" / "workflows" / "migrate-course-domain-prod.yml",
    ]

    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "workflow_dispatch" in workflow
        assert "mode" in workflow
        assert "--dry-run" in workflow
        assert "--apply" in workflow
        assert "python -m app.scripts.migrate_course_domain" in workflow
        assert "course-domain-anomalies" in workflow


def test_course_domain_prod_migration_workflow_requires_backup_before_apply():
    workflow_path = ROOT / ".github" / "workflows" / "migrate-course-domain-prod.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "environment: production" in workflow
    assert "pg_dump prod_unikorn" in workflow
    assert "refusing to apply migration" in workflow
    assert "prod-unikorn-api.service" in workflow
