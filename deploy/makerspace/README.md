# MakerSpace operations

The [migration plan](migration-plan.md) is the current activation boundary. This directory is platform-owned code, not a template copied into creator repositories. The user-facing entrance is `/makerspace`; creators connect through a per-space read-only GitHub Deploy Key and signed webhook.

## Install and activate

1. Review the exact candidate code and migration plan, obtain the required current approval, and audit existing school services/routes. Keep the runtime and main-site data boundaries distinct.
2. Through an interactive SSH/sudo terminal, run the reviewed `install-host.py --approval-reference '<actual current approval reference>' --install-docker` on the school host. It checks the hostname, installs only platform-owned components and keeps hosting disabled. It does not run SQL or activate the main-site route.
3. Verify `/etc/unikorn-makerspace/worker.json` is root-only, `backend.env` is root:unikorn 0640, and the trusted code, runtime binaries/companions, public GitHub host keys and directories are not writable by creators. Back up both secret configuration files to root-only protected storage without printing them.
4. Before any creator state exists, run `verify-sandbox.py` from the reviewed checkout as root. The worker must be stopped; the verifier acquires its lock and refuses nonempty worker state. It uses unique disposable identities and checks gVisor, actual storage exhaustion, network denial, separate preview/public data and backup verification. Git transport is replaced with a local source fixture in this test.
5. Review/include `nginx-locations.conf` inside the existing school UniKorn server block, preserving other includes. `nginx -t` must pass before reload. Generic host `X-Frame-Options: DENY`/referrer headers must not override runtime policy. Worker routes must remain unavailable through the public site.
6. Publish the approved paired frontend/backend commits using the existing `school-production` manifest/controller. The two approved schema/data revisions run only through the trusted release script's verified-backup/migration/health gates.
7. In an interactive approved maintenance step, set `MAKERSPACE_HOSTING_ENABLED=true` in the dedicated backend environment file, restart only the main backend as required to load it, and enable/start the MakerSpace worker and backup timer. The worker has a separate token; it never receives the main JWT signing key or DB credentials.
8. Complete all checks in the migration plan, including a real connected private repository build before reporting that repository as integrated. Record exact receipts and deployment status; never record secrets.

`install-host.py` is intentionally separate from routine source deployments: updating a root-owned controller is an explicit platform operation. Routine creator updates do not modify Nginx, systemd, host credentials or main-site services.

## Configuration

| Setting | Purpose |
|---|---|
| `MAKERSPACE_ENCRYPTION_KEY` | Dedicated Fernet key for repository and environment secrets |
| `MAKERSPACE_WORKER_TOKEN` | Dedicated control-plane worker credential |
| `MAKERSPACE_WORKER_ID` | Single admitted worker, default `school-makerspace` |
| `MAKERSPACE_HOSTING_ENABLED` | Fail-closed admission flag, default false |
| `MAKERSPACE_PUBLIC_ORIGIN` | Exact browser origin for CSP, default school HTTPS origin |
| `MAKERSPACE_REVIEW_DIRECTORY` | Root-owned exact source archives readable by the backend review group |
| `MAKERSPACE_MAX_PER_USER` | Per-author active-space limit, default 3 |

Worker configuration admits only the fixed loopback main API, digest-pinned images and a bounded instance count. Git host keys are pinned in the root-owned library; never use `StrictHostKeyChecking=no`. Use separate private keys per repository and do not enable write access.

## Failure, data and recovery

- A failed sandbox/firewall check stops only MakerSpace containers and exits the worker. Systemd retries the worker; recovery starts retained containers only after isolation checks pass. Normal reconciliation also restarts retained exited runtimes.
- Expired jobs fail rather than silently replay. Lost callbacks retain local state/ports until control-plane reconciliation revokes stale session targets. Old build volumes are collected; creator persistent data is retained.
- Public pointer changes only after the approved artifact starts with public storage and passes health checks. A failed new build/release leaves the existing public pointer unchanged. A creator can retry a failed approved publication without changing its artifact.
- `POST /api/makerspace/admin/<slug>/suspend` with an authenticated administrator and a reason removes visibility and revokes sessions. The next worker reconciliation stops its runtimes. External historical integrations need their own incident procedure.
- Seven verified data snapshots per volume are retained. `backup.py` freezes only the data filesystem during its snapshot copy and verifies the compressed data before pruning older snapshots. Back up the credential encryption key separately. Restore only after stopping the affected runtime and obtaining the appropriate concrete data recovery approval.
- Main-site application rollback follows `deploy/school/rollback-release.sh`; it preserves the additive MakerSpace tables. Do not invoke an automatic schema downgrade or restore a full database over newer unrelated writes.

## Local verification

Run backend permission and rollout tests from [the feature guide](../../docs/features/makerspace.md). Real gVisor verification requires an empty Linux host with this directory installed root-owned, prepared quota directories/network/firewall, and pinned images. Use an isolated disposable VM; never point the verifier at active creator state.

The browser contract requires recent CHIPS-capable browsers. Check real module imports and relative fetch with third-party cookies blocked, parent/main localStorage denial, copied preview URL denial, logout revocation, and forced uncached access. Do not add `allow-same-origin` to address a compatibility problem.

## School network verification

The school host blocks DNS to `1.1.1.1`; the tested public resolver is `223.5.5.5`. Both Docker DNS and the exact UDP/TCP 53 firewall rules use that address. Keep all private/host/lateral denials. Verify npm and PyPI HTTPS from the isolated **build** sandbox; preview/public runtimes must fail new outbound DNS/HTTP/HTTPS connections. An existing installation needs a reviewed replacement of only its two DNS rules while the worker and creator containers are stopped; `firewall.py` deliberately refuses mismatched existing chains.

If Docker Hub transport is blocked, transfer verified OCI content over SSH, retain the original index and amd64 manifest/layer digests, import with `docker load --platform linux/amd64`, and verify the original pinned image references resolve locally. Do not substitute a moving mirror tag. Finish protected configuration backup, unit validation and `verify-sandbox.py` before activation.

The trusted runner mounts a root-owned `resolv.conf` read-only because gVisor does not reach Docker’s embedded loopback DNS resolver. DNS still passes through the MakerSpace egress policy.

## Closed-runtime upgrade candidate

`upgrade-closed-runtime.py` is a separately approved operation for the existing school installation with no creator deployments. It retains backups, creates the build network, installs only reviewed platform files, and requires real runsc/network/volume verification before restarting the worker. Do not infer installation from a main deploy. The gateway requires a fresh `closed_runtime:v1` capability before approving or executing exchanges.
