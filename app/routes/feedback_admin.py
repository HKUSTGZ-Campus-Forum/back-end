from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.models.feedback import Feedback
from app.models.feedback_comment import FeedbackComment
from app.models.feedback_merge_comment import FeedbackMergeComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.services.feedback_service import FeedbackService
from app.utils.permissions import require_admin_user


feedback_admin_bp = Blueprint("feedback_admin", __name__, url_prefix="/admin")


def _admin_guard():
    admin_user, error = require_admin_user()
    if error:
        return None, error
    return admin_user, None


def _feedback_action(handler, feedback_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json(silent=True) or {}

    try:
        feedback = handler(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
            note=payload.get("note"),
        )
    except TypeError:
        feedback = handler(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(feedback.to_dict(include_private=True)), 200


@feedback_admin_bp.route("/feedbacks/pending", methods=["GET"])
@jwt_required()
def list_pending_feedback():
    admin_user, error = _admin_guard()
    if error:
        return error

    feedbacks = (
        Feedback.query.filter_by(status=Feedback.STATUS_PENDING_REVIEW)
        .order_by(Feedback.created_at.asc())
        .all()
    )
    return jsonify({"feedbacks": [feedback.to_dict(include_private=True) for feedback in feedbacks]}), 200


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/approve", methods=["POST"])
@jwt_required()
def approve_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.publish_feedback, feedback_id)


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/reject", methods=["POST"])
@jwt_required()
def reject_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.reject_feedback, feedback_id)


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/close", methods=["POST"])
@jwt_required()
def close_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.close_feedback, feedback_id)


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/reopen", methods=["POST"])
@jwt_required()
def reopen_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.reopen_feedback, feedback_id)


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/end-comments", methods=["POST"])
@jwt_required()
def end_feedback_comments(feedback_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    try:
        feedback = FeedbackService.set_feedback_comments_state(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
            comments_ended=True,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify(feedback.to_dict(include_private=True)), 200


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/resume-comments", methods=["POST"])
@jwt_required()
def resume_feedback_comments(feedback_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    try:
        feedback = FeedbackService.set_feedback_comments_state(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
            comments_ended=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify(feedback.to_dict(include_private=True)), 200


@feedback_admin_bp.route("/merge-requests/pending", methods=["GET"])
@jwt_required()
def list_pending_merge_requests():
    admin_user, error = _admin_guard()
    if error:
        return error

    merge_requests = (
        FeedbackMergeRequest.query.filter_by(
            status=FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN
        )
        .order_by(FeedbackMergeRequest.updated_at.asc())
        .all()
    )
    return jsonify({"merge_requests": [item.to_dict() for item in merge_requests]}), 200


@feedback_admin_bp.route("/merge-requests/<int:merge_request_id>/approve", methods=["POST"])
@jwt_required()
def approve_merge_request(merge_request_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json(silent=True) or {}

    try:
        FeedbackService.admin_merge_request(
            merge_request_id=merge_request_id,
            admin_user_id=admin_user.id,
            note=payload.get("note"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    merge_request = FeedbackMergeRequest.query.get(merge_request_id)
    return jsonify(merge_request.to_dict()), 200


@feedback_admin_bp.route("/merge-requests/<int:merge_request_id>/reject", methods=["POST"])
@jwt_required()
def reject_merge_request(merge_request_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json(silent=True) or {}

    try:
        merge_request = FeedbackService.admin_reject_merge_request(
            merge_request_id=merge_request_id,
            admin_user_id=admin_user.id,
            note=payload.get("note"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(merge_request.to_dict()), 200


@feedback_admin_bp.route("/feedback-comments/<int:comment_id>/hide", methods=["POST"])
@jwt_required()
def hide_feedback_comment(comment_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json() or {}
    try:
        comment = FeedbackService.hide_feedback_comment(
            comment_id=comment_id,
            admin_user_id=admin_user.id,
            reason=str(payload.get("reason", "")),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Feedback comment not found" else 400
        return jsonify({"error": message}), status_code

    return jsonify(comment.to_dict(viewer_user_id=admin_user.id, viewer_is_admin=True)), 200


@feedback_admin_bp.route("/feedback-merge-comments/<int:comment_id>/hide", methods=["POST"])
@jwt_required()
def hide_feedback_merge_comment(comment_id: int):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json() or {}
    try:
        comment = FeedbackService.hide_merge_request_comment(
            comment_id=comment_id,
            admin_user_id=admin_user.id,
            reason=str(payload.get("reason", "")),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Merge request comment not found" else 400
        return jsonify({"error": message}), status_code

    return jsonify(comment.to_dict(viewer_user_id=admin_user.id, viewer_is_admin=True)), 200
