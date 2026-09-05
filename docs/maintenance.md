# Documentation maintenance

This policy applies to every developer and coding agent. Documentation is part of the implementation, not a separate follow-up task.

## Read the smallest relevant set

1. Read the repository's `AGENTS.md` and [documentation index](README.md).
2. Choose the feature/reference page for the task, then follow its source and test links.
3. Check the actual branch, source, callers and tests before changing behavior. A historical plan or successful old test run is not evidence of current behavior or deployment.
4. Read operational boundaries before auth, migration, import, external-service or deployment work.

## Update docs with every notable modification

A change is notable when it changes user behavior, an API or data contract, architecture, configuration, dependency/runtime requirements, deployment, verification commands, or an important development convention. Fixes to an undocumented behavior are notable too.

Update the relevant current reference in the same change as the code. Explain the resulting behavior, its source files and applicable checks. Record user-visible and significant technical changes under `CHANGELOG.md` → `Unreleased`; do not invent a release date or claim deployment. For a cross-repository contract, update both sides or explicitly record the outstanding counterpart and compatibility constraint. Update this index and the source map when adding, moving or retiring a subsystem. Keep `AGENTS.md` short; store feature detail in docs and link it.

For a small internal refactor or typo with no changed contract, do not manufacture a changelog entry. State why documentation was unnecessary in the handoff. This exception does not cover notable changes.

## Document roles

| Location | Purpose |
|---|---|
| `AGENTS.md` | Authoritative repository workflow and reading routes |
| `CLAUDE.md` | Claude entry point directing it to the same rules and index |
| `README.md` | Project introduction and getting started |
| `docs/README.md` | Task-oriented documentation index |
| `docs/architecture.md`, `docs/source-map.md` | System boundaries, data flow, source and test navigation |
| `docs/features/` and focused API references | Current implemented contracts and behavior |
| `docs/development.md`, `docs/testing.md` | Setup, commands and verification choices |
| `docs/plans/` | New, explicitly scoped designs and implementation plans |
| `docs/history.md` | Git archive retrieval and superseded-document map |

For a large change, create `docs/plans/YYYY-MM-DD-topic-design.md` and, if useful, a separate task plan. State status (`proposed`, `in progress`, `implemented`, or `superseded`), scope, source baseline, decisions, acceptance criteria and remaining work. Historical approval is not approval for another production operation. Once implemented, reconcile the current reference; do not leave the plan as the only specification.

## Evidence and completion

Use relative Markdown links to real source files and neighboring docs. Include specific tests rather than an invented universal verification command. Keep observed facts, intended behavior and environment-dependent unknowns distinct. Recheck examples and links after moving a document; preserve useful old detail in Git. Never copy secrets, live user data, provider keys or environment-file contents into documentation.

The handoff must state what changed, which docs were updated, the checks and outcomes, and any unresolved limitation. For documentation-only changes, validate links, commands and source claims; application builds are unnecessary unless the documentation change affects a build/deploy contract or an applicable check requires them.
