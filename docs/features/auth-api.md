# Authentication and API boundaries

## Routing

[Registration](../../app/routes/__init__.py) assembles the API. Blueprint prefixes such as `/auth`, `/posts`, `/scheduler` are **Flask paths**; public callers use `/api/auth`, `/api/posts`, `/api/scheduler`. [School Nginx](../../deploy/school/nginx/unikorn.conf) strips `/api/` with the trailing slash on `proxy_pass`. [run.py](../../run.py) strips the same prefix for local development and listens on port 8000. Do not add `/api` to individual blueprints.

[Health](../../app/routes/health.py) exposes `/healthz` liveness and `/readyz` dependency readiness. Readiness checks PostgreSQL and Redis and returns 503 when either is unavailable. Liveness alone does not prove dependencies, SSO or a changed write route work.

Error envelopes vary between existing domains (`msg`, `error`, `code`, `success/data`). Preserve neighboring behavior and the actual frontend parser rather than inventing a universal response wrapper.

## School login and site sessions

Read [CAMPUS_SSO.md](../../CAMPUS_SSO.md), [OIDC routes](../../app/routes/oidc.py), [campus service](../../app/services/campus_oidc.py) and [identity models](../../app/models/oidc_identity.py).

School Authorization Code + PKCE/state/nonce completes on the backend. External identity is keyed by issuer/subject; verified institutional email linking must preserve canonical ownership. A short-lived single-use login ticket is exchanged by the frontend for UniKorn JWTs, keeping tokens out of callback query parameters. New SSO accounts confirm public profile details through the persisted onboarding state; existing linked accounts follow the migration's grandfathering rules.

[Auth routes](../../app/routes/auth.py) return `410 sso_only` for legacy password login/register/recovery/reset/change endpoints. Refresh and logout continue to support JWT sessions, blacklisting and account `auth_valid_after` cutoffs. [User routes](../../app/routes/user.py) own profile updates/onboarding; [institutional email](../../app/services/institutional_email.py) owns normalized ownership checks. Remaining email verification routes do not constitute a second login system.

[Identity verification](../../app/routes/identity.py) manages additional public identity badges and admin approval, distinct from OIDC login and verified email.

## Downstream OAuth

[oauth.py](../../app/routes/oauth.py) exposes authorization, token, userinfo, revoke and client management for applications using UniKorn as a provider. Its models are the `oauth_*` files, separate from `oidc_identity.py`. Inspect its own scope/client/ownership logic; do not delete it merely because password authentication was retired.

## Modify and verify

Trace route → permission/ownership checks → model serializer → frontend caller. Keep provider secrets server-side and omit private attributes from public-user responses. Use [prefix tests](../../tests/test_api_prefix_contract.py), [OIDC tests](../../tests/test_campus_oidc.py), [auth tests](../../tests/test_auth.py), [user tests](../../tests/test_user.py), [OAuth tests](../../tests/test_oauth.py) and account/email tests. A real SSO round-trip requires configured school access; unit tests do not prove a deployed callback or proxy is correct.

Update this guide and the detailed SSO reference when changing auth behavior or endpoint semantics.

## Profile visibility

`GET/PUT /api/users/me/profile-visibility` reads or atomically saves the current active user's three boolean preferences (`favorite_spaces`, `created_spaces`, `recent_posts`). PUT requires all three exact keys and boolean values; it cannot target another user. Defaults are false/true/true, preserving private favorites and previously visible works/posts. Public and authenticated user responses include `profile_visibility`. User responses use `Cache-Control: no-store`.

`GET /api/users/<id>/profile-posts` returns at most 10 non-deleted posts and refuses visitors with 403 when the section is hidden. Owners can still see their own content. Existing public forum posts/search and catalog entries are not unpublished by a profile setting. MakerSpace profile collections enforce the same rule server-side. These are profile display controls, not confidentiality settings for content already published elsewhere.

The connected-apps panel and its frontend requests were removed from account settings. The downstream OAuth provider, existing tokens and revocation API remain intact; this UI change does not revoke grants or change school SSO.

Migration: `20260908_profile_visibility`, three non-null boolean columns on `users`, with a separate no-op development lineage merge `20260908_merge_profile`. See [migration plan](../plans/2026-09-08-profile-visibility-migration.md). Tests: `tests/test_profile_visibility.py`, MakerSpace social tests and pristine PostgreSQL migration tests. Production activation still requires approval of the current migration plan.
