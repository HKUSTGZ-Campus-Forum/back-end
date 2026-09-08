# Profile visibility migration plan

Status: implemented and locally verified; production approval pending (2026-09-08).

## Scope and source

The migration adds `users.show_favorite_spaces`, `users.show_created_spaces`, and `users.show_recent_posts` as non-null booleans with database defaults false/true/true. Source is the committed Alembic revision `20260908_profile_visibility`, derived directly from the current school ancestor `20260907_maker_social`. The shared development branch also includes the no-op `20260908_merge_profile` merge; that merge is not needed for the isolated school candidate and must not pull unrelated development migrations into production.

Read-only school count on 2026-09-08 (Asia/Shanghai): 1,270 users, 1 MakerSpace, 0 favorites, 239 posts. Production head was `20260907_maker_social`. Recheck these counts before activation.

Target: school database `prod_unikorn`, table `users`. Operation: add three columns with constant defaults for all 1,270 existing accounts and future accounts. No new user rows, deletes, source imports or replacement of existing attributes. No changes to MakerSpace rows, favorites, posts, OAuth tokens, TeamUp ownership or any independent application tables. Existing favorites stay private; works and recent posts retain their current visibility until their owner changes the settings.

## Verification

API tests cover defaults, strict booleans, authentication, own-account updates, deleted users, visitor/admin denial, public favorites opt-in/withdrawal, reviewed metadata, private drafts, suspended works, viewer-specific reaction states and unchanged forum/catalog access. A disposable PostgreSQL 16 database upgraded through the complete lineage and verified false/true/true for a preexisting account. Existing migration hashes remain unchanged. A separate final migration rehearsal covers the final lineage.

Real local browser checks used only a synthetic account/database: save and refresh persistence, own hidden-section labels, guest-only public favorites, all sections hidden, keyboard switching, Chinese desktop 1440px and English mobile 390px with light/dark themes. Production data was not used for browser mutation tests.

## Activation and rollback

After explicit approval: first validate and deploy shared dev, then prepare paired school candidates from the current deployed SHAs that contain only this feature, record their main ancestry, and use the normal `school-production` manifest-only release. Set an approval reference only for this migration and this user's approval. Controller makes verified backup, applies Alembic transactionally, switches the release and checks public health. Expected production row-count deltas are zero in all product tables; only schema and Alembic revision change. Never transfer test rows.

An ACCESS EXCLUSIVE lock is briefly required to add columns; constant defaults on PostgreSQL 16 avoid a table rewrite. The controller's existing migration/health protections remain intact. Capture actual backup path, checksum and receipt at release time.

Do not automatically downgrade/drop preferences. After users have opted out, reverting to code that ignores their preferences could re-expose profile collections. Prefer a forward fix or a reviewed rollback build that still enforces the saved flags; any database restore needs a separately approved stop-write and verified backup procedure. Do not simply reactivate an old frontend/backend after opt-outs have been saved.

No production migration, release manifest update or existing OAuth grant deletion is authorized by this document itself.
