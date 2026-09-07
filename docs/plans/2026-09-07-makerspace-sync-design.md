# MakerSpace closed runtime and directional exchange

Status: in progress. Production migration and runner installation await a current, concrete approval plan.

## Contract

All creator applications, including TeamUp, run in the same closed gVisor runtime. Build networking cannot access production volumes or runtime environment secrets. Runtime networking denies new outbound connections. School identity is represented by an application-scoped opaque subject, never the host JWT, email or database credentials.

Data exchange is an explicit exception administered by the platform. A grant is immutable and bound to one space, reviewed deployment/artifact, external service, resource, direction, typed field allowlist, purpose, retention and expiry. Export and import require separate independent reviews. Changed contracts require new requests. Revocation, expiry, suspension or a different published deployment stop exchange. Secrets are generated once after approval, stored hashed, rotated by the owner, and never forwarded to creator code.

The external backend polls approved exports and submits approved imports to the platform gateway. HTTP initiation does not change data direction. This avoids arbitrary outbound URLs and SSRF while enabling bidirectional data exchange. School and external databases remain independent. Applications implement fixed internal adapter routes; the gateway never accepts SQL or an arbitrary upstream URL. Imports use idempotency keys and optimistic versions. Business validation and record-level access remain the adapter's responsibility. Reputation scores have one authoritative computation; do not accept score overwrites as generic events.

## Implementation and acceptance

- [x] Directional request/review/revoke/token/audit model and APIs, immutable policy digest.
- [x] Bounded typed export/import gateway, replay/conflict/rate-limit/expiry/deployment checks.
- [x] Bilingual owner and admin UI, developer protocol and operational documentation.
- [x] Separate build/runtime networks, closed runtime verification and safe upgrade procedure.
- [x] Scoped identity bridge and TeamUp sandbox adapter in its independent repositories.
- [x] Synthetic end-to-end integration, desktop/mobile UI and migration rehearsal.
- [ ] Shared dev deployment and checks.
- [ ] Exact production record counts, backup/restore rehearsal and approval plan.
- [ ] Approved production activation and developer handoff; no external grant auto-approved.

Only synthetic records enter test environments. Existing school TeamUp records remain untouched until a count-checked migration plan is explicitly approved. No claim of deployment based on code or documentation alone.

Verification: backend 941 tests passed (7 optional integrations skipped); pristine PostgreSQL and scoped school social→sync upgrade/downgrade/re-upgrade passed; independent TeamUp backend 20 tests passed and frontend 11 tests passed. Actual local opaque iframe and real gateway→TeamUp adapter exercised all four resource/direction combinations, import deletion/idempotency/conflicts, typed-field denial and revocation, using synthetic data only. Chinese/English, desktop/mobile were inspected; a narrow-frame overflow was fixed. Real closed runsc verification awaits approved host upgrade. Shared dev has no runner and must remain fail-closed.
