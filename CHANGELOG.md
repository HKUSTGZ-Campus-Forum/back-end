# Changelog

## Unreleased

- Add an owner-only published sync declaration catalog; validate selected resource fields, direction and version again on submission without opening data exchange.

- Added account-owned profile visibility for favorites, created works and recent posts; favorites stay private by default. Production database approval received on 2026-09-08.

- MakerSpace owner covers, idempotent likes/favorites and private saved-work collections. See `deploy/makerspace/social-migration-plan.md`; production approval remains pending.

## MakerSpace directional exchange candidate

Add independently reviewed import/export grants, scoped viewer identities, typed exchange and metadata-only audit. Closed-runtime installation and TeamUp data cutover require the current migration plan.
