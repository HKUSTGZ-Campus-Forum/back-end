# School MakerSpace deployment — 2026-09-07

Activated and verified at 16:33:29 Asia/Shanghai; hosting worker enabled at 16:40:52. Backend `96faa515b6a4f266d123d9f3a1ba43af1e425076`, frontend `49bee6e792f043616745d307defd5887b82c3be2`, control `c1949dc5b8618e2e6269c895b202c0657bfae413`. [Exact paired CI passed](https://github.com/HKUSTGZ-Campus-Forum/back-end/actions/runs/34099947235).

The user's explicit approval is recorded in that control manifest as `codex:01a07a68-92bb-79f1-8396-f7e405107df7:2026-09-07:user-approved-makerspace-plan`; it cannot authorize any later migration. The historical deployed commit still contains the pre-approval plan snapshot.

Alembic reached `20260907_teamup_makerspace`. Initial counts were six new tables, one TeamUp catalog row, one attribution audit, zero deployments/sessions/webhooks/workers before worker activation. TeamUp is published, owned by verified account 1256, and retains `/teamup/`. Existing user and TeamUp business data were not migrated. Its independent deployment remains unchanged.

The controller's pre-migration verified backup is `/srv/unikorn/backups/database/prod_unikorn-20260907T083249Z.dump`, SHA-256 `76c26a07ed5203b2a68592bdbe3f88b0f92c845c1467a9e84dc1bc491e9e000e`. Root-only encryption/config backup is `/srv/unikorn-makerspace/backups/platform-secrets-20260907T081124Z.tar.gz`. Installation metadata is `/srv/unikorn-makerspace/installation.json`; never expose credential contents.

Docker and pinned gVisor/image installation, real sandbox build, npm/PyPI HTTPS, disk ENOSPC, private-network denial, preview/public data separation and verified data backup passed. Worker and daily data backup timer are enabled; `/api/makerspace/capabilities` reports both hosting and credentials ready. Resource limits and isolation rules remain those in the operations guide. GitHub SSH transport and pinned host keys passed; specific private repositories still require their own administrator to install the generated public Deploy Key before a real build can be claimed.

School homepage, frontend/backend/auth health, catalog/guide/TeamUp detail returned 200. Anonymous protected writes and owner/review APIs returned 401; public worker and invalid runtime session paths returned 404. Desktop and 390px mobile-width production UI were inspected, with full Chinese/English and dark-mode flows tested locally. No production test user/space was created. TeamUp, MeetCampus and CoursePlan retained their pre-release PIDs and passed health checks.

Shared dev hosts the control-plane UI/API but has no creator runner/credentials, so builds remain disabled there. Routine new creators use the MakerSpace UI and independent exact-version review, not per-app Nginx/systemd installation. Root-owned worker changes remain explicit platform operations, separate from source releases.
