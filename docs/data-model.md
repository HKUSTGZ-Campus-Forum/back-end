# Backend model catalog

Declared SQLAlchemy tables from model source at `d02ce0e`, reconciled 2026-09-05. This is not an inspection of any live database. Read [model imports](../app/models/__init__.py), the linked class and [migrations](../migrations) before changing persistence; a retained declaration may support a legacy path.

[Academic guide](features/academic.md) explains course/catalog/offering/cart/plan distinctions; other ownership and transition rules are in the relevant [feature guides](README.md).

| Source | Declared table(s) |
|---|---|
| [academic_map.py](../app/models/academic_map.py) | `curriculum_programs` (CurriculumProgram), `curriculum_requirement_groups` (CurriculumRequirementGroup), `user_academic_profiles` (UserAcademicProfile), `user_course_records` (UserCourseRecord) |
| [admin_audit_log.py](../app/models/admin_audit_log.py) | `admin_audit_logs` (AdminAuditLog) |
| [agent_chat.py](../app/models/agent_chat.py) | `agent_conversations` (AgentConversation), `agent_messages` (AgentMessage) |
| [calendar.py](../app/models/calendar.py) | `user_calendar` (UserCalendar) |
| [calendar_emoji.py](../app/models/calendar_emoji.py) | `calendar_emojis` (CalendarEmoji) |
| [comment.py](../app/models/comment.py) | `comments` (Comment) |
| [contest.py](../app/models/contest.py) | `contest_info` (ContestInfo) |
| [contest_organizer.py](../app/models/contest_organizer.py) | `contest_organizers` (ContestOrganizer) |
| [contest_submission.py](../app/models/contest_submission.py) | `contest_submissions` (ContestSubmission) |
| [course.py](../app/models/course.py) | `courses` (Course) |
| [course_domain.py](../app/models/course_domain.py) | `course_catalog_versions` (CourseCatalogVersion), `course_catalog_requirements` (CourseCatalogRequirement), `course_requirement_edges` (CourseRequirementEdge), `course_offerings` (CourseOffering), `course_sections` (CourseSection), `course_meetings` (CourseMeeting), `sisn_sync_runs` (SisnSyncRun), `user_course_states` (UserCourseState), `user_course_attempts` (UserCourseAttempt), `user_offering_carts` (UserOfferingCart), `user_section_selections` (UserSectionSelection), `course_post_offering_targets` (CoursePostOfferingTarget) |
| [feedback.py](../app/models/feedback.py) | `feedbacks` (Feedback) |
| [feedback_audit_event.py](../app/models/feedback_audit_event.py) | `feedback_audit_events` (FeedbackAuditEvent) |
| [feedback_comment.py](../app/models/feedback_comment.py) | `feedback_comments` (FeedbackComment) |
| [feedback_merge_comment.py](../app/models/feedback_merge_comment.py) | `feedback_merge_comments` (FeedbackMergeComment) |
| [feedback_merge_request.py](../app/models/feedback_merge_request.py) | `feedback_merge_requests` (FeedbackMergeRequest) |
| [feedback_version.py](../app/models/feedback_version.py) | `feedback_versions` (FeedbackVersion) |
| [file.py](../app/models/file.py) | `files` (File) |
| [gugu_message.py](../app/models/gugu_message.py) | `gugu_messages` (GuguMessage) |
| [home_carousel_slide.py](../app/models/home_carousel_slide.py) | `home_carousel_slides` (HomeCarouselSlide) |
| [identity_type.py](../app/models/identity_type.py) | `identity_types` (IdentityType) |
| [notification.py](../app/models/notification.py) | `notifications` (Notification) |
| [oauth_authorization_code.py](../app/models/oauth_authorization_code.py) | `oauth_authorization_codes` (OAuthAuthorizationCode) |
| [oauth_client.py](../app/models/oauth_client.py) | `oauth_clients` (OAuthClient) |
| [oauth_token.py](../app/models/oauth_token.py) | `oauth_tokens` (OAuthToken) |
| [oidc_identity.py](../app/models/oidc_identity.py) | `user_oidc_identities` (OidcIdentity), `oidc_login_tickets` (OidcLoginTicket) |
| [post.py](../app/models/post.py) | `posts` (Post) |
| [project.py](../app/models/project.py) | `projects` (Project) |
| [push_subscription.py](../app/models/push_subscription.py) | `push_subscriptions` (PushSubscription) |
| [reaction.py](../app/models/reaction.py) | `reactions` (Reaction) |
| [reaction_emoji.py](../app/models/reaction_emoji.py) | `reaction_emojis` (ReactionEmoji) |
| [recruitment_attempt.py](../app/models/recruitment_attempt.py) | `recruitment_attempts` (RecruitmentAttempt) |
| [scheduler_cart.py](../app/models/scheduler_cart.py) | `scheduler_user_course_carts` (SchedulerUserCourseCart), `scheduler_user_bundle_carts` (SchedulerUserBundleCart) |
| [scheduler_lecture.py](../app/models/scheduler_lecture.py) | `scheduler_lectures` (SchedulerLecture) |
| [scheduler_map.py](../app/models/scheduler_map.py) | `scheduler_map_components` (SchedulerMapComponent), `scheduler_map_lines` (SchedulerMapLine) |
| [scheduler_plan.py](../app/models/scheduler_plan.py) | `scheduler_plans` (SchedulerPlan), `scheduler_plan_courses` (SchedulerPlanCourse), `scheduler_plan_sections` (SchedulerPlanSection) |
| [scheduler_popularity.py](../app/models/scheduler_popularity.py) | `scheduler_popularity_events` (SchedulerPopularityEvent), `scheduler_popularity_snapshot_runs` (SchedulerPopularitySnapshotRun), `scheduler_popularity_course_snapshots` (SchedulerPopularityCourseSnapshot), `scheduler_popularity_section_snapshots` (SchedulerPopularitySectionSnapshot) |
| [scheduler_section.py](../app/models/scheduler_section.py) | `scheduler_sections` (SchedulerSection) |
| [tag.py](../app/models/tag.py) | `tag_types` (TagType), `tags` (Tag) |
| [token.py](../app/models/token.py) | `jwt_token_blacklist` (TokenBlacklist), `sts_token_pool` (STSTokenPool) |
| [user.py](../app/models/user.py) | `users` (User) |
| [user_identity.py](../app/models/user_identity.py) | `user_identities` (UserIdentity) |
| [user_profile.py](../app/models/user_profile.py) | `user_profiles` (UserProfile) |
| [user_role.py](../app/models/user_role.py) | `user_roles` (UserRole) |

Columns, constraints, relationship cascades and serializers are deliberately linked rather than duplicated as a second schema. The pristine PostgreSQL test compares imported metadata with the migrated schema. Calendar models have no registered calendar API; the removed project-application artifact is not listed as an active model.
