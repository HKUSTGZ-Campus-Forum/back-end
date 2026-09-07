# 创意空间 / MakerSpace

Status: in progress. Backend baseline `3e804da`; frontend baseline `2836b88`.

## Accepted product

UniKorn `/makerspace` hosts a public approved catalog, private owner workspace,
repository connection guide, quota-limited build/preview, exact-artifact review
and publication. Existing TeamUp is a published externally managed integration
owned by the existing verified account specified in the user's request. Only
catalog ownership/navigation change; TeamUp runtime and data stay independent.

Users retain private GitHub repositories. Each space gets its own encrypted
read-only deploy private key; only its public key is shown. A signed webhook coalesces received source updates into durable private-build requests; failed GitHub deliveries require redelivery or a manual build. Main pushes never
publish. Public metadata and the approved artifact are snapshots, separate from
draft edits. Publication, suspension and key changes are audited.

The user selected a path under the main hostname instead of a wildcard domain.
Every runtime launch uses an unguessable path plus a distinct HttpOnly browser
credential. The gateway checks both on every resource; it strips host credentials
and upstream cookie/security headers, enforces an opaque-origin CSP sandbox even
for direct visits, and scopes script/style/connect/form sources to that launch.
The host iframe never gets `allow-same-origin` or top-navigation privileges.
Browser acceptance must verify module scripts, CSS, fetch, session expiration,
cross-space access and storage isolation; failure blocks runtime activation.

Untrusted builds/runtime require installed gVisor, cgroup limits, fixed-size disk
volumes, network filtering and immutable artifacts. Merely having page/API code
does not mean hosting is active. The school host had none of Docker/Podman/runsc
on the 2026-09-07 read-only audit. Installation and production database migration
need a concrete, separately approved plan and interactive sudo.

## Acceptance / remaining work

- Implement schema, owner and admin authorization, repository keys, jobs and
  browser-bound preview gateway; test private access and version publication.
- Implement sandbox runner and deployment assets; test limits and cleanup.
- Implement bilingual, responsive catalog/create/manage/review/open/guide pages.
- Replace TeamUp sidebar entry with MakerSpace; retain old TeamUp URLs.
- Produce read-only production preflight and seed dry-run for exactly one owner.
- Verify frontend full/i18n/build gates, backend/migration checks, live browsers.
- Update workspace AGENTS/CLAUDE/context, integration docs, source-owned guides
  and skill routing. Supersede the abandoned external dev-auto-deploy proposal.
- Obtain approval for the concrete production schema/data/infrastructure plan,
  release via paired main SHAs, and validate school URLs plus existing services.

No production approvals or infrastructure installation have occurred in this
implementation task yet. Do not infer completion from this plan.
