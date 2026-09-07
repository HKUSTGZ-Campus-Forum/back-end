# Changelog

Notable behavior and engineering changes belong here with their implementation. This log starts with the documentation reconstruction; earlier application history remains in Git. No version or deployment is implied by an Unreleased entry.

## Unreleased

### Fixed

- Complete iPhone Web Push delivery with owned current-device tests, Chinese/English test copy, bounded sends, isolated provider claims, expired-subscription cleanup and same-device account isolation.
- Stop sending silent badge-only pushes on notification reads; clients update badges in the foreground or with visible notifications. Subscription removal is idempotent. No schema or bulk data migration is included.


### Changed

- Rebuilt developer/agent documentation with task indexes, architecture, complete route/model navigation, feature contracts, setup/testing guidance and a same-change documentation maintenance policy. `CLAUDE.md` now routes to the shared `AGENTS.md` rules.
- Archived superseded documentation under `docs/archive-20260905`, including previously untracked workspace and standalone Docs sources. Preserved stable SSO/API/deployment reference paths and clarified historical versus current evidence.
