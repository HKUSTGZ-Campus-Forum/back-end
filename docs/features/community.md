# Community, files and notifications

## Discussion and discovery

[Posts](../../app/routes/post.py), [comments](../../app/routes/comment.py), [tags](../../app/routes/tag.py) and [reactions](../../app/routes/reaction.py) use the corresponding models. Public read paths and authenticated writes are chosen per handler. Preserve soft deletion, ownership and visibility when joining content. Course reviews also create structured offering targets; tags alone are not authoritative course/semester links.

[Analytics](../../app/routes/analytics.py) provides hot posts and summaries; [search](../../app/routes/search.py) searches posts/users/tags/courses and prepares preview snippets. Their ordering and visibility should be checked against route code rather than historical scoring descriptions. [Gugu](../../app/routes/gugu.py) is a separate message/reply surface with deletion and admin handling.

[Content moderation](../../app/services/content_moderation_service.py) integrates Aliyun Green. The current service allows content when its client is unavailable; callers and tests define the actual behavior. A docs refresh does not change this policy.

## Files and avatars

Read [file routes](../../app/routes/file.py), [OSSService](../../app/services/file_service.py), [File model](../../app/models/file.py), [STS token model](../../app/models/token.py).

1. An authenticated upload request validates type/metadata and creates a file record plus signed upload URL.
2. The browser uploads bytes directly to OSS using that signature.
3. Callback/completion handling verifies the uploaded object; the current frontend explicitly calls the completion endpoint. A successful byte transfer alone is not completion.
4. Content is bound to its owning entity through the established file associations. Public view/avatar and protected proxy paths apply their own visibility rules.
5. Deletion/abandoned upload cleanup uses management credentials; temporary upload credentials are not general object-management authority.

Do not persist expiring URLs as identity. Public avatars are delivered through UniKorn same-origin routes; file routes also support proxy/inline handling needed by document viewers. Preserve MIME, size, ownership and completion checks when changing the flow.

## Notifications and push

[NotificationService](../../app/services/notification_service.py) creates stored notifications and invokes [PushService](../../app/services/push_service.py); [notification routes](../../app/routes/notification.py) expose recipient-owned reads, unread counts and updates. [Push routes](../../app/routes/push.py), [subscriptions](../../app/models/push_subscription.py) and [endpoint validation](../../app/utils/push_endpoints.py) handle web push registration/delivery. The frontend service worker is responsible for browser display, clicks and badge behavior.

VAPID configuration, actual browser permissions and the worker's availability are environment-dependent. Do not claim cross-device delivery is verified from database tests alone. New notification types must preserve recipient privacy and a valid frontend navigation destination.

## Verification and documentation

Use [post](../../tests/test_post.py), [post tags](../../tests/test_post_tags.py), [course discussions](../../tests/test_course_discussions.py), [search previews](../../tests/test_search_preview.py), [upload flow](../../tests/test_file_upload_flow.py), [upload status](../../tests/test_file_upload_status.py), [avatars](../../tests/test_avatar_delivery.py), [push](../../tests/test_push_security.py), and [gugu](../../tests/test_gugu.py) tests. Update the frontend community/upload reference too when the wire contract changes.
