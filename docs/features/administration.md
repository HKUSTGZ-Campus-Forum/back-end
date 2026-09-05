# Administration, feedback, contests and carousel

## Permission and audit foundation

[Shared permission helpers](../../app/utils/permissions.py), [admin routes](../../app/routes/admin.py) and [audit service](../../app/services/admin_audit_service.py) define administrative access and mutation records. Do not treat a hidden frontend button as authorization. Check the actual handler: some domains use an admin role, contest managers have their own rules, and recruitment administration uses a verified-email allowlist.

The admin console exposes overview/trends, audit records, user role/delete/restore actions, content moderation and domain summaries. Preserve safeguards around remaining active administrators, soft deletion and audit attribution. The [audit model](../../app/models/admin_audit_log.py) is separate from feedback's transition/event records.

## Feedback state machine

[Feedback routes](../../app/routes/feedback.py), [admin routes](../../app/routes/feedback_admin.py) and [FeedbackService](../../app/services/feedback_service.py) implement publication, version history, comments, merge requests and author/admin transitions. Relevant models include feedback, versions, comments, merge requests/comments and audit events. A merge request is not a direct overwrite of published content; author review and admin decisions are separate transitions. Preserve visibility, current-version relationships and notification links through edits and moderation.

## Identity and contests

[Identity routes](../../app/routes/identity.py) manage requests, approve/reject/revoke and display identity selection. [Contest routes](../../app/routes/contest.py) handle contest info, organizers, user submissions by track, placeholder registration and CSV export. Inspect per-action manager/owner checks and validation before broadening the generic admin layer.

## Homepage carousel

[Carousel routes](../../app/routes/home_carousel.py) separate public visible slides from admin create/update/reorder/archive/restore. [HomeCarouselSlide](../../app/models/home_carousel_slide.py) records locale, display content, ordering and archive state. Uploaded carousel images go through the file subsystem; do not bypass binding or visibility checks with arbitrary storage paths.

## Modify and verify

Run [admin console](../../tests/test_admin_console.py), [identity](../../tests/test_identity_admin.py), [feedback admin](../../tests/test_feedback_admin.py), [feedback merge requests](../../tests/test_feedback_merge_requests.py), [feedback notifications](../../tests/test_feedback_notifications.py), [carousel](../../tests/test_home_carousel.py), and any affected model/migration tests. The test tree does not provide equally dedicated coverage for every contest handler; name that limitation and verify changed contest behavior proportionately.

Update this guide and the frontend administration reference with notable action/state/permission changes. Historical admin phase plans are archived; their task checkboxes do not establish what remains to implement.
