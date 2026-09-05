# Backend route declaration index

Static declarations from `app/routes/*.py` at `d02ce0e`, 2026-09-05. This is a handler lookup, **not** an OpenAPI schema or permission matrix. Inspect the linked function and its helpers/decorators for JWT, ownership, admin and response behavior. Password-route declarations remain but return `410 sso_only`; public `/api` is supplied by the proxy/local entry point. Update this index whenever route declarations change.

## academic_map

Source: [academic_map.py](../app/routes/academic_map.py). Profile, course history import/records and evaluated academic progress.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/academic-map/profile` | `get_profile` |
| PUT | `/academic-map/profile` | `update_profile` |
| GET | `/academic-map/summary` | `get_summary` |
| PUT | `/academic-map/courses/<course_code>/interest` | `mark_course_interested` |
| DELETE | `/academic-map/courses/<course_code>/interest` | `cancel_course_interested` |
| POST | `/academic-map/import/parse` | `parse_import` |
| POST | `/academic-map/records/bulk` | `save_records_bulk` |
| PUT | `/academic-map/records/<int:record_id>` | `update_record` |
| DELETE | `/academic-map/records/<int:record_id>` | `delete_record` |
| DELETE | `/academic-map/records` | `clear_records` |
| DELETE | `/academic-map/grades` | `delete_grades` |

## admin

Source: [admin.py](../app/routes/admin.py). Admin overview, users/content moderation, summaries and audit.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/admin/overview` | `overview` |
| GET | `/admin/overview/trends` | `overview_trends` |
| GET | `/admin/audit-logs` | `audit_logs` |
| GET | `/admin/audit-logs/summary` | `audit_logs_summary` |
| GET | `/admin/users` | `list_users` |
| POST | `/admin/users/<int:user_id>/role` | `update_user_role` |
| POST | `/admin/users/<int:user_id>/delete` | `delete_user` |
| POST | `/admin/users/<int:user_id>/restore` | `restore_user` |
| GET | `/admin/content/summary` | `content_summary` |
| GET | `/admin/courses/summary` | `courses_summary` |
| GET | `/admin/matching/summary` | `matching_summary` |
| GET | `/admin/contest/summary` | `contest_summary` |
| GET | `/admin/operations/summary` | `operations_summary` |
| GET | `/admin/content/posts` | `list_posts` |
| GET | `/admin/content/comments` | `list_comments` |
| GET | `/admin/content/gugu` | `list_gugu_messages` |
| GET | `/admin/content/files` | `list_files` |
| POST | `/admin/content/posts/<int:post_id>/delete` | `delete_post` |
| POST | `/admin/content/posts/<int:post_id>/restore` | `restore_post` |
| POST | `/admin/content/comments/<int:comment_id>/delete` | `delete_comment` |
| POST | `/admin/content/comments/<int:comment_id>/restore` | `restore_comment` |
| POST | `/admin/content/gugu/<int:message_id>/delete` | `delete_gugu_message` |
| POST | `/admin/content/gugu/<int:message_id>/restore` | `restore_gugu_message` |
| POST | `/admin/content/files/<int:file_id>/delete` | `delete_file` |
| POST | `/admin/content/files/<int:file_id>/restore` | `restore_file` |

## agent

Source: [agent.py](../app/routes/agent.py). Assistant status, context, conversation history and chat.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/agent/status` | `get_status` |
| GET | `/agent/context` | `get_context` |
| GET | `/agent/conversations` | `list_conversations` |
| GET | `/agent/conversations/<string:public_id>` | `get_conversation` |
| DELETE | `/agent/conversations/<string:public_id>` | `delete_conversation` |
| POST | `/agent/chat` | `send_message` |

## analytics

Source: [analytics.py](../app/routes/analytics.py). Public hot posts and summaries.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/analytics/daily-summary` | `get_daily_summary` |
| GET | `/analytics/hot-posts` | `get_hot_posts` |

## auth

Source: [auth.py](../app/routes/auth.py). Retired password routes, email verification, JWT refresh/logout and loaders.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/auth/register` | `legacy_auth_disabled` |
| POST | `/auth/login` | `legacy_auth_disabled` |
| POST | `/auth/forgot-password` | `legacy_auth_disabled` |
| POST | `/auth/reset-password` | `legacy_auth_disabled` |
| POST | `/auth/change-password` | `legacy_auth_disabled` |
| POST | `/auth/verify-email` | `verify_email` |
| POST | `/auth/resend-verification` | `resend_verification` |
| POST | `/auth/refresh` | `refresh` |
| POST | `/auth/logout` | `logout` |

## background_tasks

Source: [background_tasks.py](../app/routes/background_tasks.py). Worker status and embedding maintenance controls.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/background-tasks/status` | `get_background_tasks_status` |
| POST | `/background-tasks/embedding-maintenance/run` | `run_embedding_maintenance` |
| GET | `/background-tasks/embedding-service/stats` | `get_embedding_service_stats` |
| GET | `/background-tasks/health` | `health_check` |

## cache

Source: [cache.py](../app/routes/cache.py). Cache stats/maintenance and matching invalidation.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/admin/cache/stats` | `get_cache_stats` |
| POST | `/admin/cache/clear` | `clear_cache` |
| POST | `/admin/cache/warm` | `warm_cache` |
| POST | `/admin/cache/refresh` | `refresh_cache` |
| GET | `/admin/cache/matching/stats` | `get_matching_cache_stats` |
| POST | `/admin/cache/matching/warm/<int:user_id>` | `warm_matching_cache` |
| POST | `/admin/cache/matching/clear` | `clear_matching_cache` |
| POST | `/cache/matching/invalidate/profile/<int:profile_id>` | `invalidate_profile_cache` |

## comment

Source: [comment.py](../app/routes/comment.py). Comment CRUD and post comment listing.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/comments` | `get_comments` |
| POST | `/comments` | `create_comment` |
| GET | `/comments/<int:comment_id>` | `get_comment` |
| PUT | `/comments/<int:comment_id>` | `update_comment` |
| DELETE | `/comments/<int:comment_id>` | `delete_comment` |
| GET | `/comments/post/<int:post_id>` | `get_post_comments` |

## contest

Source: [contest.py](../app/routes/contest.py). Contest configuration, organizers, track submissions and export.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/contest` | `get_contest` |
| GET | `/contest/my-role` | `get_my_role` |
| GET | `/contest/my-submission` | `get_my_submission` |
| POST | `/contest/submit` | `submit_project` |
| PUT | `/contest` | `update_contest` |
| GET | `/contest/submissions` | `get_all_submissions` |
| GET | `/contest/organizers` | `get_organizers` |
| POST | `/contest/organizers` | `add_organizer` |
| DELETE | `/contest/organizers/<int:target_user_id>` | `remove_organizer` |
| GET | `/contest/submissions/export` | `export_submissions_csv` |

## course

Source: [course.py](../app/routes/course.py). Course discovery, canonical overview, relationships and semester discussions.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/courses/filters` | `get_course_filters` |
| GET | `/courses` | `get_courses` |
| POST | `/courses` | `create_course` |
| GET | `/courses/resolve/<identifier>` | `resolve_course_identifier` |
| GET | `/courses/by-code/<code>/overview` | `get_course_overview` |
| GET | `/courses/relationships/graph` | `get_course_relationship_graph` |
| GET | `/courses/<int:course_id>` | `get_course` |
| PUT | `/courses/<int:course_id>` | `update_course` |
| DELETE | `/courses/<int:course_id>` | `delete_course` |
| POST | `/courses/<int:course_id>/semester/<semester>` | `add_semester` |
| GET | `/courses/<int:course_id>/posts` | `get_course_posts` |
| GET | `/courses/<int:course_id>/discussions` | `get_course_discussions` |
| GET | `/courses/<int:course_id>/reviews` | `get_course_reviews` |
| GET | `/courses/<int:course_id>/semesters` | `get_course_semesters` |
| POST | `/courses/<int:course_id>/semesters/validate` | `validate_course_semester` |

## feedback

Source: [feedback.py](../app/routes/feedback.py). Feedback publication, versions, comments and merge-request transitions.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/feedbacks` | `list_feedbacks` |
| POST | `/feedbacks` | `create_feedback` |
| GET | `/feedbacks/mine` | `list_my_feedbacks` |
| GET | `/feedbacks/<int:feedback_id>` | `get_feedback` |
| GET | `/feedbacks/<int:feedback_id>/versions` | `get_feedback_versions` |
| POST | `/feedbacks/<int:feedback_id>/comments` | `create_feedback_comment` |
| POST | `/feedbacks/<int:feedback_id>/merge-requests` | `create_merge_request` |
| GET | `/merge-requests/<int:merge_request_id>` | `get_merge_request` |
| PUT | `/merge-requests/<int:merge_request_id>/proposed-content` | `update_merge_request_content` |
| POST | `/merge-requests/<int:merge_request_id>/comments` | `create_merge_request_comment` |
| POST | `/merge-requests/<int:merge_request_id>/withdraw` | `withdraw_merge_request` |
| POST | `/merge-requests/<int:merge_request_id>/request-changes` | `request_merge_request_changes` |
| POST | `/merge-requests/<int:merge_request_id>/reject` | `reject_merge_request` |
| POST | `/merge-requests/<int:merge_request_id>/accept` | `accept_merge_request` |

## feedback_admin

Source: [feedback_admin.py](../app/routes/feedback_admin.py). Administrative feedback/merge/comment decisions.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/admin/feedbacks` | `list_feedbacks` |
| GET | `/admin/feedbacks/pending` | `list_pending_feedback` |
| GET | `/admin/merge-requests` | `list_merge_requests` |
| POST | `/admin/feedbacks/<int:feedback_id>/approve` | `approve_feedback` |
| POST | `/admin/feedbacks/<int:feedback_id>/reject` | `reject_feedback` |
| POST | `/admin/feedbacks/<int:feedback_id>/close` | `close_feedback` |
| POST | `/admin/feedbacks/<int:feedback_id>/reopen` | `reopen_feedback` |
| POST | `/admin/feedbacks/<int:feedback_id>/end-comments` | `end_feedback_comments` |
| POST | `/admin/feedbacks/<int:feedback_id>/resume-comments` | `resume_feedback_comments` |
| GET | `/admin/merge-requests/pending` | `list_pending_merge_requests` |
| POST | `/admin/merge-requests/<int:merge_request_id>/approve` | `approve_merge_request` |
| POST | `/admin/merge-requests/<int:merge_request_id>/reject` | `reject_merge_request` |
| POST | `/admin/feedback-comments/<int:comment_id>/hide` | `hide_feedback_comment` |
| POST | `/admin/feedback-merge-comments/<int:comment_id>/hide` | `hide_feedback_merge_comment` |

## file

Source: [file.py](../app/routes/file.py). Signed uploads, completion/callback, proxy/view/avatar and cleanup.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/files/upload` | `generate_upload_url` |
| POST | `/files/callback` | `oss_callback` |
| GET | `/files/<int:file_id>` | `get_file_route` |
| POST | `/files/<int:file_id>/complete` | `complete_upload_route` |
| DELETE | `/files/<int:file_id>` | `delete_file_route` |
| GET | `/files` | `get_user_files` |
| GET | `/files/debug/sts-pool` | `debug_sts_pool` |
| POST | `/files/debug/maintain-sts-pool` | `debug_maintain_sts_pool` |
| GET | `/files/view/<int:file_id>` | `public_view_file` |
| GET | `/files/avatar/<int:file_id>` | `public_avatar_file` |
| GET | `/files/proxy/<int:file_id>` | `proxy_file` |

## gugu

Source: [gugu.py](../app/routes/gugu.py). Message/reply wall and deletion.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/gugu/messages` | `get_messages` |
| GET | `/gugu/recent` | `get_recent_messages` |
| POST | `/gugu/messages` | `send_message` |
| DELETE | `/gugu/messages/<int:message_id>` | `delete_message` |
| GET | `/gugu/stats` | `get_chat_stats` |

## health

Source: [health.py](../app/routes/health.py). Liveness and database/Redis readiness.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/healthz` | `liveness` |
| GET | `/readyz` | `readiness` |

## home_carousel

Source: [home_carousel.py](../app/routes/home_carousel.py). Public carousel and admin slide lifecycle.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/home/carousel` | `list_public_carousel` |
| GET | `/admin/carousel` | `list_admin_carousel` |
| POST | `/admin/carousel` | `create_admin_carousel_slide` |
| PATCH | `/admin/carousel/<int:slide_id>` | `update_admin_carousel_slide` |
| POST | `/admin/carousel/reorder` | `reorder_admin_carousel_slides` |
| POST | `/admin/carousel/<int:slide_id>/archive` | `archive_admin_carousel_slide` |
| POST | `/admin/carousel/<int:slide_id>/restore` | `restore_admin_carousel_slide` |

## identity

Source: [identity.py](../app/routes/identity.py). Identity badge requests, review and display selection.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/identities/types` | `get_identity_types` |
| POST | `/identities/requests` | `request_verification` |
| GET | `/identities/my-requests` | `get_my_verification_requests` |
| GET | `/identities/my-identities` | `get_my_identities` |
| GET | `/identities/my-verified` | `get_my_verified_identities` |
| PUT | `/identities/<int:verification_id>/update` | `update_verification_request` |
| POST | `/identities/<int:verification_id>/withdraw` | `withdraw_verification_request` |
| GET | `/identities/admin/requests` | `list_admin_verification_requests` |
| GET | `/identities/admin/pending` | `get_pending_verifications` |
| POST | `/identities/admin/<int:verification_id>/approve` | `approve_verification` |
| POST | `/identities/admin/<int:verification_id>/reject` | `reject_verification` |
| POST | `/identities/admin/<int:verification_id>/revoke` | `revoke_verification` |
| GET | `/admin/identity/requests` | `list_admin_identity_requests` |
| GET | `/admin/identity/pending` | `list_pending_identity_requests` |
| POST | `/admin/identity/<int:verification_id>/approve` | `approve_identity_request` |
| POST | `/admin/identity/<int:verification_id>/reject` | `reject_identity_request` |
| POST | `/admin/identity/<int:verification_id>/revoke` | `revoke_identity_request` |
| POST | `/identities/display-identity` | `set_user_display_identity` |
| POST | `/identities/posts/<int:post_id>/set-identity` | `set_post_display_identity` |
| POST | `/identities/comments/<int:comment_id>/set-identity` | `set_comment_display_identity` |
| POST | `/identities/gugu-messages/<int:message_id>/set-identity` | `set_gugu_message_display_identity` |

## matching

Source: [matching.py](../app/routes/matching.py). Project/teammate recommendations, search and compatibility.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/matching/projects` | `get_project_recommendations` |
| GET | `/matching/teammates/<int:project_id>` | `get_teammate_recommendations` |
| GET | `/matching/dashboard` | `get_matching_dashboard` |
| GET | `/matching/contact-visibility/<int:target_user_id>` | `check_contact_visibility` |
| POST | `/matching/profile/refresh-embedding` | `refresh_profile_embedding` |
| GET | `/matching/teammates` | `get_profile_based_teammates` |
| GET | `/matching/search-projects` | `search_projects_by_text` |
| GET | `/matching/search-teammates` | `search_teammates_by_text` |
| GET | `/matching/templates/profile` | `get_profile_template` |
| GET | `/matching/templates/projects` | `get_project_templates` |

## notification

Source: [notification.py](../app/routes/notification.py). Recipient notifications, unread count and read/delete state.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/notifications` | `get_notifications` |
| GET | `/notifications/unread-count` | `get_unread_count` |
| PUT | `/notifications/<int:notification_id>/read` | `mark_notification_read` |
| PUT | `/notifications/mark-all-read` | `mark_all_notifications_read` |
| DELETE | `/notifications/<int:notification_id>` | `delete_notification` |
| GET | `/notifications/<int:notification_id>` | `get_notification` |

## oauth

Source: [oauth.py](../app/routes/oauth.py). UniKorn as OAuth provider and client management.

| Methods | Flask path | Handler |
|---|---|---|
| GET,POST | `/oauth/authorize` | `authorize` |
| POST | `/oauth/token` | `token` |
| GET,POST | `/oauth/userinfo` | `userinfo` |
| POST | `/oauth/revoke` | `revoke` |
| GET,POST | `/oauth/clients` | `manage_clients` |

## oidc

Source: [oidc.py](../app/routes/oidc.py). UniKorn as school OIDC relying party and ticket exchange.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/auth/oidc/status` | `status` |
| GET | `/auth/oidc/login` | `login` |
| GET | `/auth/oidc/callback` | `callback` |
| POST | `/auth/oidc/exchange` | `exchange` |

## post

Source: [post.py](../app/routes/post.py). Post CRUD, course-review targets and tag normalization.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/posts` | `get_posts` |
| POST | `/posts` | `create_post` |
| GET | `/posts/<int:post_id>` | `get_post` |
| PUT | `/posts/<int:post_id>` | `update_post` |
| DELETE | `/posts/<int:post_id>` | `delete_post` |
| GET | `/posts/hot` | `get_hot_posts` |
| GET | `/posts/search` | `search_posts` |

## profile

Source: [profile.py](../app/routes/profile.py). User matching profiles and embedding refresh.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/profiles` | `get_current_user_profile` |
| POST | `/profiles` | `create_or_update_profile` |
| GET | `/profiles/<int:profile_id>` | `get_profile_by_id` |
| GET | `/profiles/user/<int:user_id>` | `get_profile_by_user_id` |
| GET | `/profiles/` | `search_profiles` |
| POST | `/profiles/refresh-embedding` | `refresh_profile_embedding` |
| GET | `/profiles/stats` | `get_profile_stats` |

## project

Source: [project.py](../app/routes/project.py). Project CRUD and recommendations.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/projects/` | `get_projects` |
| POST | `/projects/` | `create_project` |
| GET | `/projects/<int:project_id>` | `get_project` |
| PUT | `/projects/<int:project_id>` | `update_project` |
| DELETE | `/projects/<int:project_id>` | `delete_project` |
| GET | `/projects/<int:project_id>/match` | `get_project_matches` |
| GET | `/projects/my` | `get_my_projects` |
| GET | `/projects/stats` | `get_project_stats` |

## project_interview

Source: [project_interview.py](../app/routes/project_interview.py). Model-assisted project description interview.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/project-interview/start` | `start_interview` |
| POST | `/project-interview/answer` | `submit_answer` |
| POST | `/project-interview/synthesize` | `synthesize_description` |

## push

Source: [push.py](../app/routes/push.py). Web push keys, subscriptions and delivery tests.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/push/vapid-public-key` | `get_vapid_public_key` |
| POST | `/push/subscribe` | `subscribe_to_push` |
| POST | `/push/unsubscribe` | `unsubscribe_from_push` |
| GET | `/push/subscriptions` | `get_push_subscriptions` |
| POST | `/push/test` | `test_push_notification` |
| POST | `/push/test/<int:target_user_id>` | `test_push_to_user` |

## reaction

Source: [reaction.py](../app/routes/reaction.py). Post/comment emoji reactions.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/reactions/emojis` | `get_emojis` |
| GET | `/reactions/emojis/<int:emoji_id>` | `get_emoji` |
| GET | `/reactions/posts/<int:post_id>/reactions` | `get_post_reactions` |
| POST | `/reactions/posts/<int:post_id>/reactions` | `add_post_reaction` |
| DELETE | `/reactions/posts/<int:post_id>/reactions` | `remove_post_reaction` |
| GET | `/reactions/comments/<int:comment_id>/reactions` | `get_comment_reactions` |
| POST | `/reactions/comments/<int:comment_id>/reactions` | `add_comment_reaction` |
| DELETE | `/reactions/comments/<int:comment_id>/reactions` | `remove_comment_reaction` |
| GET | `/reactions/users/<int:user_id>` | `get_user_reactions` |

## recruitment

Source: [recruitment.py](../app/routes/recruitment.py). Challenge config/runs, ranking and allowlisted admin overview.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/recruitment/config` | `get_recruitment_config` |
| GET | `/recruitment/status` | `get_recruitment_status` |
| GET | `/recruitment/leaderboard` | `get_recruitment_leaderboard` |
| GET | `/recruitment/admin/overview` | `get_recruitment_admin_overview` |
| POST | `/recruitment/run` | `run_recruitment_challenge` |

## scheduler

Source: [scheduler.py](../app/routes/scheduler.py). Semester/course/section/cart/popularity/map API and SISN ingest.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/scheduler/internal/sisn-ingest` | `ingest_sisn_snapshot` |
| GET | `/scheduler/semesters` | `list_semesters` |
| GET | `/scheduler/courses/search` | `search_courses` |
| GET | `/scheduler/subjects` | `list_subjects` |
| GET | `/scheduler/courses/<code>` | `get_course_detail` |
| GET | `/scheduler/popularity/<semester>` | `get_popularity` |
| GET | `/scheduler/popularity/<semester>/history` | `get_popularity_history` |
| GET | `/scheduler/cart/<semester>` | `get_cart` |
| DELETE | `/scheduler/cart/<semester>` | `clear_cart` |
| POST | `/scheduler/cart/<semester>/add` | `add_to_cart` |
| DELETE | `/scheduler/cart/<semester>/remove/<code>` | `remove_from_cart` |
| PUT | `/scheduler/cart/<semester>/course/<code>/toggle` | `toggle_course_enabled` |
| PUT | `/scheduler/cart/<semester>/bundle/<code>/<int:bundle_id>/<int:layer>/toggle` | `toggle_bundle_enabled` |
| PUT | `/scheduler/cart/<semester>/layer/<code>/<int:layer>/toggle` | `toggle_layer_enabled` |
| GET | `/scheduler/map/components` | `get_map_components` |
| GET | `/scheduler/map/lines` | `get_map_lines` |
| GET | `/scheduler/map/courses` | `get_map_courses` |

## scheduler_plan

Source: [scheduler_plan.py](../app/routes/scheduler_plan.py). Saved plan visibility, versioning, clone and apply.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/scheduler/plans` | `create_saved_plan` |
| GET | `/scheduler/plans/mine` | `list_my_plans` |
| GET | `/scheduler/plans/shared` | `list_shared_plans` |
| GET | `/scheduler/plans/<string:public_id>` | `get_saved_plan` |
| PATCH | `/scheduler/plans/<string:public_id>` | `update_saved_plan` |
| DELETE | `/scheduler/plans/<string:public_id>` | `delete_saved_plan` |
| POST | `/scheduler/plans/<string:public_id>/clone` | `clone_saved_plan` |
| POST | `/scheduler/plans/<string:public_id>/apply` | `apply_saved_plan` |

## search

Source: [search.py](../app/routes/search.py). Post/user/tag/course/global search and previews.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/search/posts` | `search_posts` |
| GET | `/search/users` | `search_users` |
| GET | `/search/tags` | `search_tags` |
| GET | `/search/courses` | `search_courses` |
| GET | `/search/global` | `global_search` |

## tag

Source: [tag.py](../app/routes/tag.py). Tags and post associations.

| Methods | Flask path | Handler |
|---|---|---|
| GET | `/tags` | `get_tags` |
| POST | `/tags` | `create_tag` |
| GET | `/tags/<int:tag_id>` | `get_tag` |
| PUT | `/tags/<int:tag_id>` | `update_tag` |
| DELETE | `/tags/<int:tag_id>` | `delete_tag` |
| GET | `/tags/popular` | `get_popular_tags` |
| GET | `/tags/<int:tag_id>/posts` | `get_tag_posts` |
| GET | `/tags/post/<int:post_id>` | `get_post_tags` |
| GET | `/tags/trending` | `get_trending_tags` |
| POST | `/tags/post/<int:post_id>/tags` | `add_tags_to_post` |
| DELETE | `/tags/post/<int:post_id>/tags/<int:tag_id>` | `remove_tag_from_post` |

## user

Source: [user.py](../app/routes/user.py). Profiles, onboarding, public identity and owned OAuth tokens.

| Methods | Flask path | Handler |
|---|---|---|
| POST | `/users` | `create_user` |
| GET | `/users/<int:user_id>` | `get_user` |
| PUT | `/users/<int:user_id>` | `update_user` |
| POST | `/users/me/onboarding` | `complete_onboarding` |
| DELETE | `/users/<int:user_id>` | `delete_user` |
| GET | `/users/public/<int:user_id>` | `get_public_user_info` |
| GET | `/users/<int:user_id>/stats` | `get_user_stats` |
| POST | `/users/<int:user_id>/add-email` | `add_email_to_existing_user` |
| GET | `/users/<int:user_id>/oauth-tokens` | `get_user_oauth_tokens` |
| POST | `/users/<int:user_id>/oauth-tokens/<int:token_id>/revoke` | `revoke_user_oauth_token` |
