# Documentation history and recovery

The local Git tag **`docs/archive-20260905`** preserves the documentation before reconstruction against backend `d02ce0e`. Git history is the archive; obsolete prose and old executable plan snippets are not part of the active agent reading path. Tags/branches remain local until explicitly published.

Read an old document without changing the checkout:

```bash
git show docs/archive-20260905:AGENTS.md
git ls-tree -r --name-only docs/archive-20260905 -- docs
```

Export the original tracked documentation, if needed:

```bash
git archive --format=tar --output=/tmp/unikorn-backend-docs-20260905.tar docs/archive-20260905 -- AGENTS.md CLAUDE.md docs
```

Use `git show TAG:path` for other root files. Do not restore the entire old checkout to recover one document. Archive instructions and historical approval/test claims are not current authorization or verification.

The backend archive also contains `docs/legacy-workspace-snapshot/`: 41 original workspace Markdown files and standalone Docs Markdown/YAML files. This includes files that were previously untracked or outside the application repositories. The primary working checkouts and standalone Docs repositories were not reset or cleaned.

## Superseded documents

| Archived path | Current starting point |
|---|---|
| `docs/admin-system-phase-0.md` | [Current guide](features/administration.md) |
| `docs/admin-system-phase-1.md` | [Current guide](features/administration.md) |
| `docs/admin-system-phase-2.md` | [Current guide](features/administration.md) |
| `docs/admin-system-plan.md` | [Current guide](features/administration.md) |
| `docs/superpowers/plans/2026-05-31-academic-map-curriculum-rule-tree.md` | [Current guide](features/academic.md) |
| `docs/superpowers/specs/2026-05-31-academic-map-curriculum-rule-tree-design.md` | [Current guide](features/academic.md) |

Existing API contracts and production runbooks retain their stable paths because source, tests and operational procedures refer to them. Historical source counts, completed checklists and old deployment receipts remain historical even when a reference is retained.
