from datetime import datetime, timezone
from typing import Optional

from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_audit_event import FeedbackAuditEvent
from app.models.feedback_comment import FeedbackComment
from app.models.feedback_merge_comment import FeedbackMergeComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.feedback_version import FeedbackVersion


class FeedbackService:
    @staticmethod
    def _notification_service():
        from app.services.notification_service import NotificationService

        return NotificationService

    @staticmethod
    def create_feedback_submission(author_id: int, title: str, markdown_content: str) -> Feedback:
        feedback = Feedback(
            author_id=author_id,
            title=title,
            status=Feedback.STATUS_PENDING_REVIEW,
        )
        db.session.add(feedback)
        db.session.flush()

        version = FeedbackVersion(
            feedback_id=feedback.id,
            version_number=1,
            markdown_content=markdown_content,
            created_by_user_id=author_id,
        )
        db.session.add(version)
        db.session.flush()

        feedback.current_version_id = version.id
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=author_id,
            event_type="feedback_submitted",
            event_payload={},
        )
        db.session.commit()
        return feedback

    @staticmethod
    def record_event(
        feedback_id: int,
        merge_request_id: Optional[int],
        actor_user_id: int,
        event_type: str,
        event_payload: dict,
    ) -> FeedbackAuditEvent:
        event = FeedbackAuditEvent(
            feedback_id=feedback_id,
            merge_request_id=merge_request_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            event_payload=event_payload,
        )
        db.session.add(event)
        return event

    @staticmethod
    def is_publicly_visible(feedback: Feedback) -> bool:
        return feedback.status in {Feedback.STATUS_PUBLISHED, Feedback.STATUS_CLOSED}

    @staticmethod
    def can_user_view(feedback: Feedback, user_id: Optional[int]) -> bool:
        return FeedbackService.is_publicly_visible(feedback) or feedback.author_id == user_id

    @staticmethod
    def create_feedback_comment(
        feedback_id: int,
        user_id: int,
        content: str,
        parent_comment_id: Optional[int] = None,
    ) -> FeedbackComment:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if not FeedbackService.is_publicly_visible(feedback):
            raise PermissionError("Feedback is not open for public discussion")

        if feedback.comments_ended:
            raise RuntimeError("Comments have ended for this feedback")

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Comment content is required")

        parent_comment = None
        if parent_comment_id is not None:
            parent_comment = db.session.get(FeedbackComment, parent_comment_id)
            if parent_comment is None or parent_comment.feedback_id != feedback_id:
                raise ValueError("Parent comment does not belong to this feedback")

        comment = FeedbackComment(
            feedback_id=feedback_id,
            user_id=user_id,
            parent_comment_id=parent_comment.id if parent_comment else None,
            content=normalized_content,
        )
        db.session.add(comment)
        db.session.commit()
        return comment

    @staticmethod
    def create_merge_request(
        feedback_id: int,
        proposer_id: int,
        change_summary: str,
        proposed_markdown_content: str,
    ) -> FeedbackMergeRequest:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if feedback.status != Feedback.STATUS_PUBLISHED:
            raise PermissionError("Merge requests can only be opened on published feedback")

        if feedback.current_version is None:
            raise RuntimeError("Feedback does not have a current version")

        normalized_content = proposed_markdown_content.strip()
        if not normalized_content:
            raise ValueError("Proposed markdown content is required")

        normalized_summary = change_summary.strip()
        merge_request = FeedbackMergeRequest(
            feedback_id=feedback.id,
            author_id=proposer_id,
            base_version_id=feedback.current_version.id,
            title=f"Update: {feedback.title}",
            change_summary=normalized_summary or None,
            proposed_markdown_content=normalized_content,
            status=FeedbackMergeRequest.STATUS_OPEN,
        )
        db.session.add(merge_request)
        db.session.flush()
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=merge_request.id,
            actor_user_id=proposer_id,
            event_type="merge_request_created",
            event_payload={},
        )
        FeedbackService._notification_service().create_feedback_merge_request_created_notification(
            merge_request=merge_request,
            sender_id=proposer_id,
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def update_merge_request_content(
        merge_request_id: int,
        author_user_id: int,
        proposed_markdown_content: str,
    ) -> FeedbackMergeRequest:
        merge_request = FeedbackService._get_author_reviewable_merge_request(
            merge_request_id=merge_request_id,
            author_user_id=author_user_id,
        )

        normalized_content = proposed_markdown_content.strip()
        if not normalized_content:
            raise ValueError("Proposed markdown content is required")

        merge_request.proposed_markdown_content = normalized_content
        merge_request.updated_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=author_user_id,
            event_type="merge_request_content_updated_by_author",
            event_payload={},
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def create_merge_request_comment(
        merge_request_id: int,
        user_id: int,
        content: str,
        parent_comment_id: Optional[int] = None,
    ) -> FeedbackMergeComment:
        merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
        if merge_request is None:
            raise ValueError("Merge request not found")

        if not FeedbackService.is_publicly_visible(merge_request.feedback):
            raise PermissionError("Merge request discussion is not open for public viewing")

        if merge_request.feedback.comments_ended:
            raise RuntimeError("Comments have ended for this feedback")

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Comment content is required")

        parent_comment = None
        if parent_comment_id is not None:
            parent_comment = db.session.get(FeedbackMergeComment, parent_comment_id)
            if parent_comment is None or parent_comment.merge_request_id != merge_request_id:
                raise ValueError("Parent comment does not belong to this merge request")

        comment = FeedbackMergeComment(
            merge_request_id=merge_request_id,
            user_id=user_id,
            parent_comment_id=parent_comment.id if parent_comment else None,
            content=normalized_content,
        )
        db.session.add(comment)
        db.session.commit()
        return comment

    @staticmethod
    def withdraw_merge_request(merge_request_id: int, proposer_id: int) -> FeedbackMergeRequest:
        merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
        if merge_request is None:
            raise ValueError("Merge request not found")

        if merge_request.author_id != proposer_id:
            raise PermissionError("Only the proposer can withdraw this merge request")

        if merge_request.status not in {
            FeedbackMergeRequest.STATUS_OPEN,
            FeedbackMergeRequest.STATUS_AUTHOR_CHANGES_REQUESTED,
        }:
            raise RuntimeError("This merge request can no longer be withdrawn")

        merge_request.status = FeedbackMergeRequest.STATUS_WITHDRAWN
        merge_request.updated_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=proposer_id,
            event_type="merge_request_withdrawn",
            event_payload={},
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def author_request_changes(
        merge_request_id: int,
        author_user_id: int,
        note: Optional[str],
    ) -> FeedbackMergeRequest:
        merge_request = FeedbackService._get_author_reviewable_merge_request(
            merge_request_id=merge_request_id,
            author_user_id=author_user_id,
        )
        merge_request.status = FeedbackMergeRequest.STATUS_AUTHOR_CHANGES_REQUESTED
        merge_request.author_reviewed_at = datetime.now(timezone.utc)
        merge_request.author_review_note = note.strip() if note else None
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=author_user_id,
            event_type="merge_request_changes_requested",
            event_payload={"note": merge_request.author_review_note},
        )
        FeedbackService._notification_service().create_feedback_merge_request_changes_requested_notification(
            merge_request=merge_request,
            sender_id=author_user_id,
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def author_reject_merge_request(
        merge_request_id: int,
        author_user_id: int,
        note: Optional[str],
    ) -> FeedbackMergeRequest:
        merge_request = FeedbackService._get_author_reviewable_merge_request(
            merge_request_id=merge_request_id,
            author_user_id=author_user_id,
        )
        merge_request.status = FeedbackMergeRequest.STATUS_AUTHOR_REJECTED
        merge_request.author_reviewed_at = datetime.now(timezone.utc)
        merge_request.author_review_note = note.strip() if note else None
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=author_user_id,
            event_type="merge_request_rejected_by_author",
            event_payload={"note": merge_request.author_review_note},
        )
        FeedbackService._notification_service().create_feedback_merge_request_rejected_by_author_notification(
            merge_request=merge_request,
            sender_id=author_user_id,
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def author_accept_merge_request(
        merge_request_id: int,
        author_user_id: int,
        note: Optional[str],
    ) -> FeedbackMergeRequest:
        merge_request = FeedbackService._get_author_reviewable_merge_request(
            merge_request_id=merge_request_id,
            author_user_id=author_user_id,
        )
        merge_request.status = FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN
        merge_request.author_reviewed_at = datetime.now(timezone.utc)
        merge_request.author_review_note = note.strip() if note else None
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=author_user_id,
            event_type="merge_request_accepted_by_author",
            event_payload={"note": merge_request.author_review_note},
        )
        FeedbackService._notification_service().create_feedback_merge_request_ready_for_admin_notification(
            merge_request=merge_request,
            sender_id=author_user_id,
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def admin_merge_request(
        merge_request_id: int,
        admin_user_id: int,
        note: Optional[str],
    ) -> FeedbackVersion:
        merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
        if merge_request is None:
            raise ValueError("Merge request not found")

        if merge_request.status != FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN:
            raise RuntimeError("Merge request is not ready for admin merge")

        next_version_number = merge_request.feedback.versions.count() + 1
        version = FeedbackVersion(
            feedback_id=merge_request.feedback_id,
            version_number=next_version_number,
            markdown_content=merge_request.proposed_markdown_content,
            created_by_user_id=merge_request.author_id,
            source_merge_request_id=merge_request.id,
        )
        db.session.add(version)
        db.session.flush()

        merge_request.merged_version_id = version.id
        merge_request.status = FeedbackMergeRequest.STATUS_MERGED
        merge_request.admin_reviewed_at = datetime.now(timezone.utc)
        merge_request.admin_review_note = note.strip() if note else None
        merge_request.feedback.current_version_id = version.id
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=admin_user_id,
            event_type="merge_request_merged",
            event_payload={"note": merge_request.admin_review_note},
        )
        FeedbackService._notification_service().create_feedback_merge_request_merged_notifications(
            merge_request=merge_request,
            sender_id=admin_user_id,
        )
        db.session.commit()
        return version

    @staticmethod
    def publish_feedback(feedback_id: int, admin_user_id: int) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if feedback.status != Feedback.STATUS_PENDING_REVIEW:
            raise RuntimeError("Only pending feedback can be approved")

        feedback.status = Feedback.STATUS_PUBLISHED
        feedback.published_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type="feedback_published",
            event_payload={},
        )
        FeedbackService._notification_service().create_feedback_published_notification(
            feedback=feedback,
            admin_user_id=admin_user_id,
        )
        db.session.commit()
        return feedback

    @staticmethod
    def reject_feedback(
        feedback_id: int,
        admin_user_id: int,
        note: Optional[str],
    ) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if feedback.status != Feedback.STATUS_PENDING_REVIEW:
            raise RuntimeError("Only pending feedback can be rejected")

        feedback.status = Feedback.STATUS_REJECTED
        feedback.rejected_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type="feedback_rejected",
            event_payload={"note": note.strip() if note else None},
        )
        FeedbackService._notification_service().create_feedback_rejected_notification(
            feedback=feedback,
            admin_user_id=admin_user_id,
            note=note.strip() if note else None,
        )
        db.session.commit()
        return feedback

    @staticmethod
    def admin_reject_merge_request(
        merge_request_id: int,
        admin_user_id: int,
        note: Optional[str],
    ) -> FeedbackMergeRequest:
        merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
        if merge_request is None:
            raise ValueError("Merge request not found")

        if merge_request.status != FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN:
            raise RuntimeError("Merge request is not awaiting admin review")

        merge_request.status = FeedbackMergeRequest.STATUS_ADMIN_REJECTED
        merge_request.admin_reviewed_at = datetime.now(timezone.utc)
        merge_request.admin_review_note = note.strip() if note else None
        FeedbackService.record_event(
            feedback_id=merge_request.feedback_id,
            merge_request_id=merge_request.id,
            actor_user_id=admin_user_id,
            event_type="merge_request_rejected_by_admin",
            event_payload={"note": merge_request.admin_review_note},
        )
        FeedbackService._notification_service().create_feedback_merge_request_rejected_by_admin_notifications(
            merge_request=merge_request,
            sender_id=admin_user_id,
        )
        db.session.commit()
        return merge_request

    @staticmethod
    def close_feedback(feedback_id: int, admin_user_id: int) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if feedback.status != Feedback.STATUS_PUBLISHED:
            raise RuntimeError("Only published feedback can be closed")

        feedback.status = Feedback.STATUS_CLOSED
        feedback.closed_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type="feedback_closed",
            event_payload={},
        )
        db.session.commit()
        return feedback

    @staticmethod
    def reopen_feedback(feedback_id: int, admin_user_id: int) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        if feedback.status != Feedback.STATUS_CLOSED:
            raise RuntimeError("Only closed feedback can be reopened")

        feedback.status = Feedback.STATUS_PUBLISHED
        feedback.closed_at = None
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type="feedback_reopened",
            event_payload={},
        )
        db.session.commit()
        return feedback

    @staticmethod
    def set_feedback_comments_state(
        feedback_id: int,
        admin_user_id: int,
        comments_ended: bool,
    ) -> Feedback:
        feedback = db.session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError("Feedback not found")

        feedback.comments_ended = comments_ended
        event_type = "feedback_comments_ended" if comments_ended else "feedback_comments_resumed"
        FeedbackService.record_event(
            feedback_id=feedback.id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type=event_type,
            event_payload={},
        )
        db.session.commit()
        return feedback

    @staticmethod
    def hide_feedback_comment(
        comment_id: int,
        admin_user_id: int,
        reason: str,
    ) -> FeedbackComment:
        comment = db.session.get(FeedbackComment, comment_id)
        if comment is None:
            raise ValueError("Feedback comment not found")

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("Hide reason is required")

        comment.visibility = FeedbackComment.VISIBILITY_HIDDEN
        comment.hidden_reason = normalized_reason
        comment.hidden_by_admin_id = admin_user_id
        comment.hidden_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=comment.feedback_id,
            merge_request_id=None,
            actor_user_id=admin_user_id,
            event_type="feedback_comment_hidden",
            event_payload={"comment_id": comment.id, "reason": normalized_reason},
        )
        db.session.commit()
        return comment

    @staticmethod
    def hide_merge_request_comment(
        comment_id: int,
        admin_user_id: int,
        reason: str,
    ) -> FeedbackMergeComment:
        comment = db.session.get(FeedbackMergeComment, comment_id)
        if comment is None:
            raise ValueError("Merge request comment not found")

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("Hide reason is required")

        comment.visibility = FeedbackMergeComment.VISIBILITY_HIDDEN
        comment.hidden_reason = normalized_reason
        comment.hidden_by_admin_id = admin_user_id
        comment.hidden_at = datetime.now(timezone.utc)
        FeedbackService.record_event(
            feedback_id=comment.merge_request.feedback_id,
            merge_request_id=comment.merge_request_id,
            actor_user_id=admin_user_id,
            event_type="merge_request_comment_hidden",
            event_payload={"comment_id": comment.id, "reason": normalized_reason},
        )
        db.session.commit()
        return comment

    @staticmethod
    def _get_author_reviewable_merge_request(
        merge_request_id: int,
        author_user_id: int,
    ) -> FeedbackMergeRequest:
        merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
        if merge_request is None:
            raise ValueError("Merge request not found")

        if merge_request.feedback.author_id != author_user_id:
            raise PermissionError("Only the feedback author can review this merge request")

        if merge_request.status not in {
            FeedbackMergeRequest.STATUS_OPEN,
            FeedbackMergeRequest.STATUS_AUTHOR_CHANGES_REQUESTED,
        }:
            raise RuntimeError("This merge request is not awaiting author review")

        return merge_request
