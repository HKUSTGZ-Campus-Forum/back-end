# Directional data exchange

Status: implemented in the candidate branch; see the task's deployment record for actual activation. TeamUp migration and runner upgrade require separately approved production operations.

## Owner and administrator workflow

The owner's space page exposes **External data exchange approvals**. Submit one request for each resource/direction. Provide an external service name and HTTPS origin (an identification label, not an outbound URL), exact field names/types, purpose, machine-readable record scope supported by the adapter, retention/deletion/conflict rules, and an expiry within 90 days. The current reviewed public deployment and artifact are bound automatically. The administrator uses `/makerspace/review`, independently checks the exact source/adapter and scope, and approves or rejects with a note. Authors cannot self-approve. Publication approval is distinct from exchange approval.

After approval the owner generates a one-time-visible random credential for the external **backend**. Only its hash is stored. Rotation invalidates the previous credential. Owners and administrators can revoke grants. Expiry, account disablement, application suspension, a changed published version or unavailable closed-runtime heartbeat blocks exchange. Revocation does not retrieve external copies already delivered.

Before approving, the gateway queries the reviewed runtime's fixed `/__unikorn/sync/contract` endpoint and checks direction, resource, field types and record scope. A missing/mismatched adapter prevents approval. This technical check supplements the administrator's source and data-purpose review.

## Gateway protocol

`POST /api/makerspace/exchange/<grant-id>` accepts `Authorization: Bearer <exchange-secret>`. Never use a host JWT or put this secret into browser/mini-program code. No SQL, arbitrary URL, forwarded cookie, redirect, or direct database access is supported. Each successful approval authorizes exactly one direction; both directions require two approved grants. HTTP requests are initiated by the external backend, which polls exports and submits imports. Data flows both ways without opening outbound connections from the creator runtime.

Export body:

```json
{"direction":"export","cursor":"","limit":50}
```

Export response:

```json
{"records":[{"id":"school:1","version":1,"deleted":false,"data":{"nickname":"Synthetic member","credit_score":60}}],"next_cursor":"1"}
```

Store the cursor only after the external transaction applies every record. Deduplicate by resource/record ID/version; ignore already-applied versions. Tombstones contain `data:{}`. Keep the previous cursor on errors. Poll conservatively; the gateway permits 60 requests/minute/grant and 50 records/page, bounded 64 KiB responses.

Import body:

```json
{"direction":"import","event_id":"external-event-1","record_id":"external:1","expected_version":0,"data":{"nickname":"Synthetic member","credit_score":60}}
```

Import result contains only event ID, record ID, resulting version and `status:"applied"`. Retry the **same event and body** after a timeout. Reusing an event ID with different content returns 409. A version conflict returns 409 and must be reconciled, never blindly overwritten. The adapter must also persist event deduplication in the same business transaction, because a process can fail after the application commits but before the gateway records its receipt.

To delete a mirror, add `"deleted":true` and send `"data":{}` with the same expected-version/event rules. TeamUp hides expired mirrors immediately and clears their payloads within 30 seconds while its Gunicorn workers run. Retention is capped by both the grant expiry and approved retention days. Only tombstone/version metadata is retained for replay safety. External operators must separately implement their approved retention and deletion duties.

Fields must exactly match the approved names/types; nested objects, unlisted fields and wrong scalar types are rejected. String values are capped at 2,000 characters. A typed allowlist is not semantic content moderation: administrators must inspect the source implementation, and untrusted text fields cannot be assumed incapable of carrying personal data.

## Application adapter

Implement fixed internal `POST /__unikorn/sync/export` and `/__unikorn/sync/import` routes. The platform chooses the public runtime port; previews never participate. It supplies `X-Unikorn-Sync:v1` and a JSON envelope with grant ID, policy digest, resource, fields and record scope. Browser resource requests to `/__unikorn/*` are rejected before proxying. The sandbox must not have another public listener or permit lateral containers. The adapter enforces its supported scopes and business invariants. Never implement an arbitrary SQL/table/URL proxy.

TeamUp supports `groups` / `public_groups` and `members` / `member_reputation`. Its schema is declared in its independent `makerspace_adapter.py`. Imported external records are stored in a separate mirror namespace and displayed as external data. They do not overwrite local groups or local reputation and are not re-exported, preventing loops. An imported reputation value is the external service's score, not a school-verified score. Shared user account linking and cross-system membership commands require a new reviewed contract; this version does not trust external email/user IDs as school identities.

## Runtime and identity

Build uses `unikorn-makerbuild`, with constrained public dependency networking and no runtime secrets or data volumes. Public/preview processes use `unikorn-makerspace` with outbound new connections denied; only replies to inbound gateway requests pass. Runtime network policy must be verified before the worker advertises `closed_runtime:v1`. Credentials, review metadata and audit stay in the platform database; business data remains in each sandbox.

Authenticated viewer sessions receive a random per-space `X-Unikorn-Subject` mapped in `maker_identities`; no school user ID/email/JWT is forwarded. The gateway sets `X-Unikorn-Context` to preview/public and strips user-supplied versions of these headers. Deleted, unverified and revoked user sessions cannot retain delegated identity.

Audit records contain actions/counts/timestamps, never student payloads or credentials. Request purpose and reviewer notes must not contain student records. Metadata retention and grant renewal do not authorize retaining external business data beyond the approved policy.

## Verification

`tests/test_makerspace_sync.py`, `tests/test_makerspace.py`, `tests/test_makerspace_runtime.py`; PostgreSQL pristine/rollout checks; actual browser opaque-frame checks; real runsc verification through the approved runner-upgrade procedure. See `deploy/makerspace/upgrade-closed-runtime.py`. Never claim the old network policy is closed merely because new code has been published.
