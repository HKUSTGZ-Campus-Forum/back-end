from pathlib import Path


def _load_revision(path):
    namespace = {}
    exec(path.read_text(encoding="utf-8"), namespace)
    return namespace["revision"]


def test_alembic_revision_ids_fit_default_version_column():
    version_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"

    revisions = {
        path.name: _load_revision(path)
        for path in version_dir.glob("*.py")
    }

    assert revisions
    assert {
        name: revision
        for name, revision in revisions.items()
        if len(revision) > 32
    } == {}
