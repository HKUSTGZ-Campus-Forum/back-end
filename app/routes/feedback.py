from typing import Optional

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_comment import FeedbackComment
from app.models.feedback_merge_comment import FeedbackMergeComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.feedback_version import FeedbackVersion
from app.services.feedback_service import FeedbackService


feedback_bp = Blueprint("feedback", __name__, url_prefix="/feedbacks")
merge_request_bp = Blueprint("feedback_merge_request", __name__)


def _current_user_id() -> Optional[int]:
    identity = get_jwt_identity()
    if identity is None:
        return None

    try:
        return int(identity)
    except (TypeError, ValueError):
        return None


def _get_viewable_feedback(feedback_id: int, viewer_id: Optional[int]) -> Optional[Feedback]:
    feedback = db.session.get(Feedback, feedback_id)
    if feedback is None:
        return None

    if not FeedbackService.can_user_view(feedback, viewer_id):
        return None

    return feedback


@feedback_bp.route("", methods=["GET"])
def list_feedbacks():
    feedbacks = (
        Feedback.query.filter(
            Feedback.status.in_([Feedback.STATUS_PUBLISHED, Feedback.STATUS_CLOSED])
        )
        .order_by(Feedback.updated_at.desc())
        .all()
    )
    return jsonify({"feedbacks": [feedback.to_dict() for feedback in feedbacks]}), 200


@feedback_bp.route("", methods=["POST"])
@jwt_required()
def create_feedback():
    data = request.get_json() or {}
    title = " ".join(str(data.get("title", "")).split())
    markdown_content = str(data.get("markdown_content", "")).strip()

    if not title:
        return jsonify({"error": "Feedback title is required"}), 400

    if not markdown_content:
        return jsonify({"error": "Feedback markdown content is required"}), 400

    feedback = FeedbackService.create_feedback_submission(
        author_id=_current_user_id(),
        title=title,
        markdown_content=markdown_content,
    )
    return jsonify(feedback.to_dict(include_private=True)), 201


@feedback_bp.route("/mine", methods=["GET"])
@jwt_required()
def list_my_feedbacks():
    user_id = _current_user_id()
    feedbacks = (
        Feedback.query.filter_by(author_id=user_id)
        .order_by(Feedback.updated_at.desc())
        .all()
    )
    return jsonify({"feedbacks": [feedback.to_dict(include_private=True) for feedback in feedbacks]}), 200


@feedback_bp.route("/<int:feedback_id>", methods=["GET"])
@jwt_required(optional=True)
def get_feedback(feedback_id: int):
    viewer_id = _current_user_id()
    feedback = _get_viewable_feedback(feedback_id, viewer_id)

    if feedback is None:
        return jsonify({"error": "Feedback not found"}), 404

    include_private = feedback.author_id == viewer_id
    payload = feedback.to_dict(include_private=include_private)
    payload["comments"] = [
        comment.to_dict(viewer_user_id=viewer_id)
        for comment in feedback.comments.order_by(FeedbackComment.created_at.asc()).all()
    ]
    payload["merge_requests"] = [
        merge_request.to_dict()
        for merge_request in feedback.merge_requests.order_by(FeedbackMergeRequest.created_at.desc()).all()
    ]
    return jsonify(payload), 200


@feedback_bp.route("/<int:feedback_id>/versions", methods=["GET"])
@jwt_required(optional=True)
def get_feedback_versions(feedback_id: int):
    viewer_id = _current_user_id()
    feedback = _get_viewable_feedback(feedback_id, viewer_id)

    if feedback is None:
        return jsonify({"error": "Feedback not found"}), 404

    versions = (
        feedback.versions.order_by(FeedbackVersion.version_number.asc()).all()
    )
    return jsonify({"versions": [version.to_dict() for version in versions]}), 200


@feedback_bp.route("/<int:feedback_id>/comments", methods=["POST"])
@jwt_required()
def create_feedback_comment(feedback_id: int):
    user_id = _current_user_id()
    payload = request.get_json() or {}

    try:
        comment = FeedbackService.create_feedback_comment(
            feedback_id=feedback_id,
            user_id=user_id,
            content=str(payload.get("content", "")),
            parent_comment_id=payload.get("parent_comment_id"),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Feedback not found" else 400
        return jsonify({"error": message}), status_code
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(comment.to_dict(viewer_user_id=user_id)), 201


@feedback_bp.route("/<int:feedback_id>/merge-requests", methods=["POST"])
@jwt_required()
def create_merge_request(feedback_id: int):
    user_id = _current_user_id()
    payload = request.get_json() or {}

    try:
        merge_request = FeedbackService.create_merge_request(
            feedback_id=feedback_id,
            proposer_id=user_id,
            change_summary=str(payload.get("change_summary", "")),
            proposed_markdown_content=str(payload.get("proposed_markdown_content", "")),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Feedback not found" else 400
        return jsonify({"error": message}), status_code
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(merge_request.to_dict()), 201


@merge_request_bp.route("/merge-requests/<int:merge_request_id>", methods=["GET"])
@jwt_required(optional=True)
def get_merge_request(merge_request_id: int):
    merge_request = db.session.get(FeedbackMergeRequest, merge_request_id)
    if merge_request is None:
        return jsonify({"error": "Merge request not found"}), 404

    viewer_id = _current_user_id()
    if not FeedbackService.can_user_view(merge_request.feedback, viewer_id):
        return jsonify({"error": "Merge request not found"}), 404

    payload = merge_request.to_dict()
    payload["comments"] = [
        comment.to_dict(viewer_user_id=viewer_id)
        for comment in merge_request.comments.order_by(FeedbackMergeComment.created_at.asc()).all()
    ]
    return jsonify(payload), 200


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/proposed-content", methods=["PUT"])
@jwt_required()
def update_merge_request_content(merge_request_id: int):
    payload = request.get_json() or {}

    try:
        merge_request = FeedbackService.update_merge_request_content(
            merge_request_id=merge_request_id,
            author_user_id=_current_user_id(),
            proposed_markdown_content=str(payload.get("proposed_markdown_content", "")),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Merge request not found" else 400
        return jsonify({"error": message}), status_code
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(merge_request.to_dict()), 200


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/comments", methods=["POST"])
@jwt_required()
def create_merge_request_comment(merge_request_id: int):
    payload = request.get_json() or {}

    try:
        comment = FeedbackService.create_merge_request_comment(
            merge_request_id=merge_request_id,
            user_id=_current_user_id(),
            content=str(payload.get("content", "")),
            parent_comment_id=payload.get("parent_comment_id"),
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Merge request not found" else 400
        return jsonify({"error": message}), status_code
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(comment.to_dict(viewer_user_id=_current_user_id())), 201


def _handle_merge_request_transition(handler, merge_request_id: int):
    payload = request.get_json() or {}
    note = payload.get("note")

    try:
        merge_request = handler(
            merge_request_id=merge_request_id,
            author_user_id=_current_user_id(),
            note=str(note) if note is not None else None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(merge_request.to_dict()), 200


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/withdraw", methods=["POST"])
@jwt_required()
def withdraw_merge_request(merge_request_id: int):
    try:
        merge_request = FeedbackService.withdraw_merge_request(
            merge_request_id=merge_request_id,
            proposer_id=_current_user_id(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(merge_request.to_dict()), 200


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/request-changes", methods=["POST"])
@jwt_required()
def request_merge_request_changes(merge_request_id: int):
    return _handle_merge_request_transition(
        FeedbackService.author_request_changes,
        merge_request_id,
    )


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/reject", methods=["POST"])
@jwt_required()
def reject_merge_request(merge_request_id: int):
    return _handle_merge_request_transition(
        FeedbackService.author_reject_merge_request,
        merge_request_id,
    )


@merge_request_bp.route("/merge-requests/<int:merge_request_id>/accept", methods=["POST"])
@jwt_required()
def accept_merge_request(merge_request_id: int):
    return _handle_merge_request_transition(
        FeedbackService.author_accept_merge_request,
        merge_request_id,
    )
