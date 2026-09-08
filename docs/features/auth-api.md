# Profile visibility API

## Profile visibility

`GET/PUT /api/users/me/profile-visibility` reads or atomically saves the current active user's three boolean preferences (`favorite_spaces`, `created_spaces`, `recent_posts`). PUT requires all three exact keys and boolean values; it cannot target another user. Defaults are false/true/true, preserving private favorites and previously visible works/posts. Public and authenticated user responses include `profile_visibility`. User responses use `Cache-Control: no-store`.

`GET /api/users/<id>/profile-posts` returns at most 10 non-deleted posts and refuses visitors with 403 when the section is hidden. Owners can still see their own content. Existing public forum posts/search and catalog entries are not unpublished by a profile setting. MakerSpace profile collections enforce the same rule server-side. These are profile display controls, not confidentiality settings for content already published elsewhere.

The connected-apps panel and its frontend requests were removed from account settings. The downstream OAuth provider, existing tokens and revocation API remain intact; this UI change does not revoke grants or change school SSO.

Migration: `20260908_profile_visibility`, three non-null boolean columns on `users`, with a separate no-op development lineage merge `20260908_merge_profile`. See [migration plan](../plans/2026-09-08-profile-visibility-migration.md). Tests: `tests/test_profile_visibility.py`, MakerSpace social tests and pristine PostgreSQL migration tests. Production activation still requires approval of the current migration plan.
