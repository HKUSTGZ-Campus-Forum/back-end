# MakerSpace control plane

MakerSpace keeps creator repositories independent while UniKorn owns account attribution, the published catalog, private build admission and exact-version review. The frontend entrance is `/makerspace`; this backend registers `/makerspace` and the public proxy adds `/api`.

School hosting was activated on 2026-09-07. See the [verified release record](../../deploy/makerspace/school-release-20260907.md); recheck live capabilities before claiming a new repository is connected.

## Source and contracts

- [Models](../../app/models/makerspace.py): six tables, separate private settings and published metadata, immutable deployment snapshots, audit events, browser sessions, signed webhook deliveries and worker heartbeats.
- [Service](../../app/services/makerspace_service.py): verified-account ownership, three-space quota, per-space repository keys, encrypted environments, deployment/review transitions, session checks and log redaction.
- [Routes](../../app/routes/makerspace.py): catalog, owner management, admin review/source download, signed push intake and token-protected worker protocol.
- [Resource gateway](../../app/services/makerspace_proxy.py): fixed loopback destinations, bounded HTTP proxy, rewritten relative assets, opaque CSP sandbox and credential stripping.
- [Trusted runner](../../deploy/makerspace/runtime.py): root-owned platform code, read-only Git credentials, runsc execution, fixed-size filesystems and fixed resource/network policy. Creator commands run only inside the container.
- [School plan](../../deploy/makerspace/migration-plan.md) and [operations](../../deploy/makerspace/README.md): prerequisites, approval, verification and rollback. Source implementation is not evidence that a host has been installed or activated.

## State and permission rules

Catalog queries return only published metadata. Draft edits never change the public title, description, category or deployment. Only the verified owner can edit settings, rotate repository keys, change environment variables, queue a build, archive or submit a ready deployment. Unsubmitted private work is not browseable by another administrator; a pending review grants the reviewer access to that snapshot and preview.

Source SHA and artifact SHA-256 are checked again on review. Authors cannot approve their own work. Approval queues a public runtime using the reviewed artifact and separate public storage. Only its successful worker receipt updates the public pointer. New pushes are signed, deduplicated, and coalesced into a durable desired SHA while builds run. GitHub delivery failures require redelivery or a manual build; there is no claim of automatic GitHub retries.

Repository private keys, webhook secrets and environment values use the dedicated Fernet `MAKERSPACE_ENCRYPTION_KEY`; no fallback to the JWT key is allowed. The API returns only public deploy keys and environment names. A rotated webhook secret is shown once. Worker jobs use a distinct random `MAKERSPACE_WORKER_TOKEN`; public Nginx must block `/api/makerspace/worker/`.

## Browser session isolation

Each launch requires a fresh random path and an independent HttpOnly/Secure/Partitioned cookie. A platform-only bootstrap exchanges a one-use, 60-second ticket inside the opaque iframe to establish the correct CHIPS partition before any creator HTML runs. This supports browsers blocking ordinary third-party cookies without granting `allow-same-origin`. Session cookies, JWTs and host headers are never forwarded to creator processes. All runtime responses enforce HTTP CSP sandbox, including direct navigation; the host iframe enforces the same restriction.

Every resource checks the session, expiry, owner/reviewer eligibility and current published pointer. Logout revokes viewer sessions; account recovery/deletion also invalidates private access. The PWA service worker must bypass all MakerSpace API paths. `/makerspace/<slug>` is the stable shared entrance; a private runtime URL alone is not authorization.

## API groups

| Public suffix after `/api/makerspace` | Access / purpose |
|---|---|
| `GET /capabilities`, `GET /` | Hosting state, quotas, approved catalog |
| `GET /mine`, `POST /`, `PUT /<slug>` | Verified owner management |
| `GET /<slug>` | Public metadata, or permission-scoped private detail |
| `POST /<slug>/credentials`, `PUT /<slug>/environment` | Owner repository credentials and encrypted variables |
| `POST /<slug>/deployments` | Queue a private build |
| `POST /<slug>/deployments/<id>/{submit,withdraw,retry-publication}` | Owner publication workflow |
| `POST /<slug>/{launch,archive}` | Session launch or owner archive |
| `GET /admin/reviews` | Independent admin review queue |
| `GET /admin/<slug>/deployments/<id>/source` | Authenticated exact source archive; never a public URL |
| `POST /admin/<slug>/deployments/<id>/review` | Approve/reject the specified SHA and digest |
| `POST /admin/<slug>/suspend` | Emergency platform suspension; revokes sessions |
| `POST /hooks/<space-id>` | HMAC-verified GitHub pushes |
| `/worker/*` | Loopback-only, separate worker token; no browser use |
| `/run/<session>/*` | Session-authorized opaque runtime resources |

## Verification

Run `tests/test_makerspace.py` for ownership, independent review, publication isolation, webhook replay, secret snapshots, one-use bootstrap and logout. `tests/test_makerspace_rollout.py` rehearses the current school revision to the two MakerSpace revisions on a disposable PostgreSQL database; it must not target production. `deploy/makerspace/verify-sandbox.py` runs real gVisor resource/network/storage/backup checks only on an empty prepared host and refuses live worker state.

## Covers and social collections

- `GET /makerspace/users/<user_id>` returns published work and reviewed metadata to other viewers; the verified owner additionally sees private drafts. Repository settings are excluded from profile serialization.
- `GET /makerspace/favorites` requires a verified active user and returns only that user's currently published saved works. The profile endpoint added below exposes saved works only after explicit owner opt-in.
- `PUT/DELETE /makerspace/<slug>/likes` and `/favorites` require a published space and an active verified account. Composite `(space_id,user_id)` primary keys and a space row lock make requests idempotent. Catalog/detail/profile results add aggregate counts and viewer-specific booleans.
- Cover uploads reuse `/files/upload` and authenticated `/files/<id>/complete`, with `file_type=maker_cover`, `entity_type=makerspace`, no `entity_id`, PNG/JPEG/WebP only and a 5 MiB limit checked against OSS metadata. `PUT /makerspace/<slug>/cover` accepts an owner-uploaded, verified file ID or null. Only the creator may bind/remove it; historical TeamUp ownership grants this display operation without granting runtime deployment rights.
- `GET /makerspace/<slug>/cover` rechecks space visibility on every request. Responses are no-store/nosniff and sandboxed; private images need the owner's or pending reviewer's bearer token. The general public file endpoint cannot deliver these files. Referenced images cannot be directly deleted or swept as abandoned uploads. Detached uploads become eligible for normal stale-file cleanup.

Cover display edits do not change code/settings snapshots or activate a new runtime. Published titles/descriptions retain the existing review boundary. Neither reactions nor display uploads forward identity to creator code. `users` and `favorites` are reserved slug segments.

Schema and rollout: [social migration plan](../../deploy/makerspace/social-migration-plan.md). School deployment needs new approval; initial MakerSpace approval cannot be reused. Tests: `test_makerspace_social.py`, existing file tests, and PostgreSQL rollout/pristine tests.

## Closed runtime and external exchange

The candidate introduces [directional exchange](makerspace-sync.md), separate approvals, scoped viewer identities and a closed runtime network. Deployment and the TeamUp cutover require the current migration plan; the historical trusted-app description above remains the live state until that cutover.

### Profile collection visibility

Account preferences now gate author and saved-work sections for visitors. Favorites remain private by default and become public only after the owner opts in. `GET /makerspace/users/<id>/favorites` checks that opt-in, returns only published/reviewed metadata, and decorates reactions for the viewer (not the collector). Hidden creator sections reject visitor requests to `/makerspace/users/<id>`. Owners retain private access; `/makerspace/favorites` remains owner-only. These controls never reveal drafts or unpublish catalog entries. See the [account contract](auth-api.md#profile-visibility) and its migration approval plan; this documents candidate behavior, not production activation.
