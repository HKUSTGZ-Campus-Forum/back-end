# Workspace entry-point templates

The parent workspace is not a Git repository. These templates version its reconstructed entry points. Apply them at the workspace root only; relative links in the fenced templates resolve there. Application repositories remain independently clonable. The local review index `DOCUMENTATION.md` should be regenerated for the actual checkout paths when integrating/moving worktrees.

## AGENTS.md

```markdown
# UniKorn workspace — agent entry point

This directory contains independent repositories and worktrees. It is not a Git repository. These instructions apply to workspace coordination; the selected application's own `AGENTS.md` governs implementation, with higher-priority user instructions taking precedence.

## Select the code before reading feature docs

1. Identify the task's actual checkout. Check its Git branch, upstream, HEAD and working changes. Do not infer application state from a command run at the workspace root.
2. Read that checkout's `AGENTS.md`, `CLAUDE.md` if relevant, and `docs/README.md`. Use the task index to reach its feature guide, source files and tests; read architecture for cross-cutting work.
3. If an older checkout lacks a documentation index, use [DOCUMENTATION.md](DOCUMENTATION.md) to locate the reconstructed references, but verify every claim against the code you are editing. Do not copy newer behavior into an older branch's documentation as though already implemented.
4. The primary `back-end/` and `front-end/` directories can contain unfinished local work. Preserve unrelated changes; use a separate worktree for work on a different baseline. `Docs/`, `back-end/Docs/`, `CoursePlan.search/`, `campus-deploy/` and `.worktrees/` have separate ownership/history; inspect the relevant repository before touching them.

## Documentation is part of the change

For **every notable modification**, update the affected current docs with the implementation and record notable behavior/engineering changes in the repository changelog. Notable includes behavior, API/data contracts, architecture, configuration/dependencies, operations, tests and developer conventions. Update both repositories when a shared contract changes, or explicitly identify the remaining counterpart.

Keep instructions short and route detail to focused docs. Read and follow the selected repository's `docs/maintenance.md`. New feature plans must have explicit status and scope; completed behavior belongs in a current reference. Historical docs, old test results and old approvals are not current facts or authorization.

## Engineering scope and operations

Use existing architecture and the smallest direct change. Preserve auth, SSR, localization, themes, data provenance and migration boundaries. Do not add speculative compatibility layers, defensive frameworks, scoring systems or hashes without a concrete project need. Report confirmed defects, including uncommon supported cases; do not manufacture findings. Current code, tests and applicable instructions outrank historical prose.

Before each check, identify the failure it detects and what the result would change. Start narrow, satisfy applicable integration gates and report actual outcomes/limitations. Avoid installing dependencies, starting services or repeatedly rechecking settled work for a documentation-only task.

Authentication, production data, schema/imports and deployments require the selected repository's operational references. Preserve independent services and never expose credentials, tokens, environment contents or live user data. Honor existing user authorization; do not repeat a settled permission request. Do not infer production approval from a branch name or successful dev deployment.

Finish with what changed, docs updated, checks/outcomes and remaining limitations. Archive superseded documentation in its owning Git history; do not leave old operating instructions competing with the current guide.
```

## CLAUDE.md

```markdown
# Claude workspace entry point

Read and follow [AGENTS.md](AGENTS.md), then the selected application's own `AGENTS.md` and `docs/README.md`. The workspace is a container for independent Git repositories; identify the actual checkout before implementation.

Use [DOCUMENTATION.md](DOCUMENTATION.md) to locate documentation and Git archives. Update the relevant docs in the same change as **every notable modification**, according to the selected repository's documentation maintenance policy. Do not rely on an old development log as current architecture or deployment evidence.
```
