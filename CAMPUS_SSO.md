# HKUST(GZ) Campus SSO integration

UniKorn is an OpenID Connect relying party for the HKUST(GZ) SSO provider. The
implementation uses Authorization Code Flow, PKCE S256, OIDC nonce/state
validation through Authlib, server-side token exchange, and the `openid
profile` scopes documented by the school.

## Public endpoints

- Start login: `GET /api/auth/oidc/login`
- Registered callback: `GET /api/auth/oidc/callback`
- Exchange the one-time UniKorn ticket: `POST /api/auth/oidc/exchange`
- Capability status: `GET /api/auth/oidc/status`
- Local logout: `POST /api/auth/logout` (returns the provider logout URL for an
  active OIDC session)

The callback never places UniKorn access or refresh tokens in a URL. It creates
a random, hashed, single-use database ticket with a two-minute lifetime. The
frontend exchanges that ticket once and then receives the normal UniKorn JWT
pair.

Campus SSO is the only supported end-user authentication method. Password
login, self-registration, password recovery, and password changes return
`410 sso_only`; the legacy `POST /api/users` account-creation alias is disabled
as well. Existing password hashes remain stored for data compatibility but are
not accepted by any authentication route.

## Account linking rules

The stable identity key is `(issuer, sub)`, stored in
`user_oidc_identities`. On first login:

1. An existing `(issuer, sub)` mapping wins, even if the school later changes
   the user's display name or email.
2. A matching active UniKorn account is linked only when its institutional
   email is already verified.
3. An unverified local email collision is rejected for administrator review.
4. If no local account exists, UniKorn creates one with a verified school email
   and an unknown random local password.

## Deployment configuration

Copy the non-secret values from `.env.example` and set these through deployment
secrets:

```dotenv
CAMPUS_SSO_ENABLED=true
CAMPUS_SSO_CLIENT_ID=<issued by HKUST(GZ)>
CAMPUS_SSO_CLIENT_SECRET=<issued by HKUST(GZ)>
CAMPUS_SSO_ISSUER=https://sso.hkust-gz.edu.cn
CAMPUS_SSO_METADATA_URL=https://sso.hkust-gz.edu.cn/.well-known/openid-configuration
CAMPUS_SSO_END_SESSION_ENDPOINT=https://sso.hkust-gz.edu.cn/connect/endsession
FRONTEND_BASE_URL=https://unikorn.hkust-gz.edu.cn
CAMPUS_SSO_COOKIE_SECURE=true
SESSION_COOKIE_SECURE=true
```

The school must register these values exactly:

```text
Redirect URI: https://unikorn.hkust-gz.edu.cn/api/auth/oidc/callback
Post Logout Redirect URI: https://unikorn.hkust-gz.edu.cn/
```

Never send `client_secret` to `/connect/authorize` or expose it to frontend
code. It is used only by the backend at the token endpoint via
`client_secret_basic`.

The client secret must remain in the protected server environment and must
never be committed or written into application logs. Restart the production
API after changing any OIDC setting.

## Database migration

Revision `20260819_campus_oidc` merges the two existing Alembic heads and adds:

- `user_oidc_identities`: stable external-to-local account mappings.
- `oidc_login_tickets`: short-lived, one-time SPA exchange tickets.

The migration only adds tables and indexes. It does not update, replace, or
delete existing user records. Downgrade removes the two new tables.

## Production verification

- Confirm `unikorn.hkust-gz.edu.cn` has a valid TLS certificate and routes
  `/api/*` to the Flask backend.
- Run end-to-end login, account-linking, cancellation, expiry, and logout tests
  with real school accounts after each authentication release.
- Confirm `/api/auth/oidc/status` reports the provider as available.
- Confirm every legacy password route returns `410 sso_only` and that the login
  page exposes no local-account controls.
