from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.feedback import Feedback
from app.models.feedback_comment import FeedbackComment
from app.models.feedback_merge_comment import FeedbackMergeComment
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.services.admin_audit_service import log_admin_action
from app.services.feedback_service import FeedbackService
from app.utils.permissions import require_admin_user


feedback_admin_bp = Blueprint("feedback_admin", __name__, url_prefix="/admin")


def _admin_guard():
    admin_user, error = require_admin_user()
    if error:
        return None, error
    return admin_user, None


def _feedback_action(handler, feedback_id: int, action_name: str):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json(silent=True) or {}

    try:
        feedback = handler(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
            note=payload.get("note"),
            commit=False,
        )
    except TypeError:
        feedback = handler(
            feedback_id=feedback_id,
            admin_user_id=admin_user.id,
            commit=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    log_admin_action(
        admin_user,
        action_name,
        "feedback",
        feedback.id,
        feedback.title,
        payload.get("note"),
        {"status": feedback.status},
    )
    db.session.commit()
    return jsonify(feedback.to_dict(include_private=True)), 200


def _counts_for(model, statuses):
    counts = {
        status: model.query.filter_by(status=status).count()
        for status in statuses
    }
    counts["total"] = sum(counts.values())
    return counts


def _pagination_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid page"}), 400)

    try:
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid per_page"}), 400)

    return page, per_page, None


@feedback_admin_bp.route("/feedbacks", methods=["GET"])
@jwt_required()
def list_feedbacks():
    admin_user, error = _admin_guard()
    if error:
        return error

    statuses = [
        Feedback.STATUS_PENDING_REVIEW,
        Feedback.STATUS_REJECTED,
        Feedback.STATUS_PUBLISHED,
        Feedback.STATUS_CLOSED,
    ]
    status = request.args.get("status")
    if status and status not in statuses:
        return jsonify({"error": "Invalid status"}), 400

    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error

    query = Feedback.query
    if status:
        query = query.filter_by(status=status)

    sort = request.args.get("sort", "newest")
    if sort == "oldest":
        query = query.order_by(Feedback.updated_at.asc())
    else:
        query = query.order_by(Feedback.updated_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "feedbacks": [item.to_dict(include_private=True) for item in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
        "counts": _counts_for(Feedback, statuses),
    }), 200


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


@feedback_admin_bp.route("/merge-requests", methods=["GET"])
@jwt_required()
def list_merge_requests():
    admin_user, error = _admin_guard()
    if error:
        return error

    statuses = [
        FeedbackMergeRequest.STATUS_OPEN,
        FeedbackMergeRequest.STATUS_AUTHOR_CHANGES_REQUESTED,
        FeedbackMergeRequest.STATUS_AUTHOR_REJECTED,
        FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
        FeedbackMergeRequest.STATUS_ADMIN_REJECTED,
        FeedbackMergeRequest.STATUS_MERGED,
        FeedbackMergeRequest.STATUS_WITHDRAWN,
    ]
    status = request.args.get("status")
    if status and status not in statuses:
        return jsonify({"error": "Invalid status"}), 400

    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error

    query = FeedbackMergeRequest.query
    if status:
        query = query.filter_by(status=status)

    sort = request.args.get("sort", "newest")
    if sort == "oldest":
        query = query.order_by(FeedbackMergeRequest.updated_at.asc())
    else:
        query = query.order_by(FeedbackMergeRequest.updated_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "merge_requests": [item.to_dict() for item in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
        "counts": _counts_for(FeedbackMergeRequest, statuses),
    }), 200


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/approve", methods=["POST"])
@jwt_required()
def approve_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.publish_feedback, feedback_id, "feedback.approve")


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/reject", methods=["POST"])
@jwt_required()
def reject_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.reject_feedback, feedback_id, "feedback.reject")


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/close", methods=["POST"])
@jwt_required()
def close_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.close_feedback, feedback_id, "feedback.close")


@feedback_admin_bp.route("/feedbacks/<int:feedback_id>/reopen", methods=["POST"])
@jwt_required()
def reopen_feedback(feedback_id: int):
    return _feedback_action(FeedbackService.reopen_feedback, feedback_id, "feedback.reopen")


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
            commit=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    log_admin_action(
        admin_user,
        "feedback.comments_end",
        "feedback",
        feedback.id,
        feedback.title,
        None,
        metadata={"comments_ended": True},
    )
    db.session.commit()
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
            commit=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    log_admin_action(
        admin_user,
        "feedback.comments_resume",
        "feedback",
        feedback.id,
        feedback.title,
        metadata={"comments_ended": False},
    )
    db.session.commit()
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
            commit=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    merge_request = FeedbackMergeRequest.query.get(merge_request_id)
    log_admin_action(
        admin_user,
        "feedback.merge_approve",
        "feedback_merge_request",
        merge_request.id,
        merge_request.title,
        payload.get("note"),
        {"feedback_id": merge_request.feedback_id, "status": merge_request.status},
    )
    db.session.commit()
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
            commit=False,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    log_admin_action(
        admin_user,
        "feedback.merge_reject",
        "feedback_merge_request",
        merge_request.id,
        merge_request.title,
        payload.get("note"),
        {"feedback_id": merge_request.feedback_id, "status": merge_request.status},
    )
    db.session.commit()
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
            commit=False,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Feedback comment not found" else 400
        return jsonify({"error": message}), status_code

    log_admin_action(
        admin_user,
        "feedback.comment_hide",
        "feedback_comment",
        comment.id,
        (comment.content or "")[:255],
        payload.get("reason"),
        {"feedback_id": comment.feedback_id},
    )
    db.session.commit()
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
            commit=False,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Merge request comment not found" else 400
        return jsonify({"error": message}), status_code

    log_admin_action(
        admin_user,
        "feedback.merge_comment_hide",
        "feedback_merge_comment",
        comment.id,
        (comment.content or "")[:255],
        payload.get("reason"),
        {"merge_request_id": comment.merge_request_id, "feedback_id": comment.merge_request.feedback_id},
    )
    db.session.commit()
    return jsonify(comment.to_dict(viewer_user_id=admin_user.id, viewer_is_admin=True)), 200
