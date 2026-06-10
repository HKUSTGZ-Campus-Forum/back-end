from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func, or_

from app.extensions import db
from app.models.admin_audit_log import AdminAuditLog
from app.models.comment import Comment
from app.models.course import Course
from app.models.course_domain import CourseMeeting, CourseOffering, CourseSection
from app.models.feedback import Feedback
from app.models.feedback_merge_request import FeedbackMergeRequest
from app.models.file import File
from app.models.gugu_message import GuguMessage
from app.models.identity_type import IdentityType
from app.models.notification import Notification
from app.models.oauth_client import OAuthClient
from app.models.oauth_token import OAuthToken
from app.models.post import Post
from app.models.project import Project
from app.models.push_subscription import PushSubscription
from app.models.tag import Tag
from app.models.token import STSTokenPool
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.models.user_profile import UserProfile
from app.models.user_role import UserRole
from app.utils.permissions import require_admin_user


admin_bp = Blueprint("admin_console", __name__, url_prefix="/admin")


def _admin_guard():
    admin_user, error = require_admin_user()
    if error:
        return None, error
    return admin_user, None


def _pagination_args(default_per_page=20):
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid page"}), 400)

    try:
        per_page = min(100, max(1, int(request.args.get("per_page", default_per_page))))
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid per_page"}), 400)

    return page, per_page, None


def _count(query):
    return query.count()


def _status_counts(model, column, statuses):
    return {status: model.query.filter(column == status).count() for status in statuses}


def _log_admin_action(actor, action, target_type, target_id=None, target_label=None, note=None, metadata=None):
    log = AdminAuditLog(
        actor_user_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        note=note,
        metadata_json=metadata or {},
    )
    db.session.add(log)
    return log


def _user_summary(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified,
        "phone_verified": user.phone_verified,
        "role_id": user.role_id,
        "role_name": user.get_role_name(),
        "is_deleted": user.is_deleted,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
    }


def _post_summary(post):
    return {
        "id": post.id,
        "user_id": post.user_id,
        "author": post.author.username if post.author else None,
        "title": post.title,
        "comment_count": post.comment_count,
        "reaction_count": post.reaction_count,
        "view_count": post.view_count,
        "is_deleted": post.is_deleted,
        "deleted_at": post.deleted_at.isoformat() if post.deleted_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
    }


def _comment_summary(comment):
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "user_id": comment.user_id,
        "author": comment.author.username if comment.author else None,
        "content": comment.content,
        "parent_comment_id": comment.parent_comment_id,
        "is_deleted": comment.is_deleted,
        "deleted_at": comment.deleted_at.isoformat() if comment.deleted_at else None,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
    }


def _content_summary():
    return {
        "posts": {
            "total": _count(Post.query),
            "active": _count(Post.query.filter_by(is_deleted=False)),
            "deleted": _count(Post.query.filter_by(is_deleted=True)),
        },
        "comments": {
            "total": _count(Comment.query),
            "active": _count(Comment.query.filter_by(is_deleted=False)),
            "deleted": _count(Comment.query.filter_by(is_deleted=True)),
        },
        "tags": {
            "total": _count(Tag.query),
        },
        "files": {
            "total": _count(File.query),
            "uploaded": _count(File.query.filter_by(status="uploaded")),
            "pending": _count(File.query.filter_by(status="pending")),
            "deleted": _count(File.query.filter_by(is_deleted=True)),
        },
        "gugu": {
            "messages": _count(GuguMessage.query),
        },
    }


def _overview_metrics():
    feedback_statuses = [
        Feedback.STATUS_PENDING_REVIEW,
        Feedback.STATUS_REJECTED,
        Feedback.STATUS_PUBLISHED,
        Feedback.STATUS_CLOSED,
    ]
    merge_statuses = [
        FeedbackMergeRequest.STATUS_OPEN,
        FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN,
        FeedbackMergeRequest.STATUS_ADMIN_REJECTED,
        FeedbackMergeRequest.STATUS_MERGED,
        FeedbackMergeRequest.STATUS_WITHDRAWN,
    ]
    identity_statuses = [
        UserIdentity.PENDING,
        UserIdentity.APPROVED,
        UserIdentity.REJECTED,
        UserIdentity.REVOKED,
    ]
    return {
        "users": {
            "total": _count(User.query),
            "active": _count(User.query.filter_by(is_deleted=False)),
            "deleted": _count(User.query.filter_by(is_deleted=True)),
            "admins": _count(User.query.join(UserRole).filter(UserRole.name == UserRole.ADMIN)),
            "email_verified": _count(User.query.filter_by(email_verified=True)),
        },
        "content": _content_summary(),
        "feedback": {
            "total": _count(Feedback.query),
            **_status_counts(Feedback, Feedback.status, feedback_statuses),
        },
        "merge_requests": {
            "total": _count(FeedbackMergeRequest.query),
            **_status_counts(FeedbackMergeRequest, FeedbackMergeRequest.status, merge_statuses),
        },
        "identity": {
            "types": _count(IdentityType.query),
            "requests": _count(UserIdentity.query),
            **_status_counts(UserIdentity, UserIdentity.status, identity_statuses),
        },
        "courses": {
            "courses": _count(Course.query),
            "active_courses": _count(Course.query.filter_by(is_active=True, is_deleted=False)),
            "offerings": _count(CourseOffering.query),
            "sections": _count(CourseSection.query),
            "meetings": _count(CourseMeeting.query),
        },
        "academic_map": _academic_map_summary(),
        "matching": {
            "projects": _count(Project.query),
            "active_projects": _count(Project.query.filter_by(is_deleted=False)),
            "profiles": _count(UserProfile.query),
            "active_profiles": _count(UserProfile.query.filter_by(is_active=True)),
        },
        "contest": _contest_summary(),
        "operations": _operations_summary(),
    }


def _academic_map_summary():
    from app.models.academic_map import (
        CurriculumProgram,
        CurriculumRequirementGroup,
        UserAcademicProfile,
        UserCourseRecord,
    )

    return {
        "programs": _count(CurriculumProgram.query),
        "requirement_groups": _count(CurriculumRequirementGroup.query),
        "user_profiles": _count(UserAcademicProfile.query),
        "course_records": _count(UserCourseRecord.query),
        "records_needing_review": _count(UserCourseRecord.query.filter_by(needs_review=True)),
    }


def _contest_summary():
    from app.models.contest import ContestInfo
    from app.models.contest_organizer import ContestOrganizer
    from app.models.contest_submission import ContestSubmission

    return {
        "contests": _count(ContestInfo.query),
        "active_contests": _count(ContestInfo.query.filter_by(is_active=True)),
        "organizers": _count(ContestOrganizer.query),
        "submissions": _count(ContestSubmission.query),
    }


def _operations_summary():
    return {
        "files": _count(File.query),
        "sts_tokens": _count(STSTokenPool.query),
        "valid_sts_tokens": _count(STSTokenPool.query.filter(STSTokenPool.expiration > datetime.now(timezone.utc))),
        "oauth_clients": _count(OAuthClient.query),
        "oauth_tokens": _count(OAuthToken.query),
        "notifications": _count(Notification.query),
        "unread_notifications": _count(Notification.query.filter_by(read=False)),
        "push_subscriptions": _count(PushSubscription.query),
    }


@admin_bp.route("/overview", methods=["GET"])
@jwt_required()
def overview():
    admin_user, error = _admin_guard()
    if error:
        return error

    pending = {
        "feedbacks": _count(Feedback.query.filter_by(status=Feedback.STATUS_PENDING_REVIEW)),
        "merge_requests": _count(FeedbackMergeRequest.query.filter_by(status=FeedbackMergeRequest.STATUS_AUTHOR_ACCEPTED_PENDING_ADMIN)),
        "identity_requests": _count(UserIdentity.query.filter_by(status=UserIdentity.PENDING)),
    }
    recent_logs = AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc()).limit(8).all()
    return jsonify({
        "metrics": _overview_metrics(),
        "pending": pending,
        "recent_activity": [log.to_dict() for log in recent_logs],
    }), 200


@admin_bp.route("/audit-logs", methods=["GET"])
@jwt_required()
def audit_logs():
    admin_user, error = _admin_guard()
    if error:
        return error

    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error

    query = AdminAuditLog.query
    action = request.args.get("action")
    target_type = request.args.get("target_type")
    actor_user_id = request.args.get("actor_user_id")
    if action:
        query = query.filter(AdminAuditLog.action == action)
    if target_type:
        query = query.filter(AdminAuditLog.target_type == target_type)
    if actor_user_id:
        try:
            query = query.filter(AdminAuditLog.actor_user_id == int(actor_user_id))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid actor_user_id"}), 400

    pagination = query.order_by(AdminAuditLog.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    return jsonify({
        "logs": [log.to_dict() for log in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
    }), 200


@admin_bp.route("/users", methods=["GET"])
@jwt_required()
def list_users():
    admin_user, error = _admin_guard()
    if error:
        return error

    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error

    query = User.query.outerjoin(UserRole)
    search = request.args.get("search")
    role = request.args.get("role")
    deleted = request.args.get("deleted")
    email_verified = request.args.get("email_verified")

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(User.username.ilike(pattern), User.email.ilike(pattern)))
    if role:
        query = query.filter(UserRole.name == role)
    if deleted in {"true", "false"}:
        query = query.filter(User.is_deleted == (deleted == "true"))
    if email_verified in {"true", "false"}:
        query = query.filter(User.email_verified == (email_verified == "true"))

    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    roles = UserRole.query.order_by(UserRole.name.asc()).all()
    return jsonify({
        "users": [_user_summary(user) for user in pagination.items],
        "roles": [{"id": role.id, "name": role.name, "description": role.description} for role in roles],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
        "counts": {
            "total": _count(User.query),
            "deleted": _count(User.query.filter_by(is_deleted=True)),
            "email_verified": _count(User.query.filter_by(email_verified=True)),
        },
    }), 200


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@jwt_required()
def update_user_role(user_id):
    admin_user, error = _admin_guard()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    role_name = payload.get("role_name")
    role = UserRole.query.filter_by(name=role_name).first()
    if role is None:
        return jsonify({"error": "Invalid role_name"}), 400

    target = User.query.get(user_id)
    if target is None:
        return jsonify({"error": "User not found"}), 404

    old_role = target.get_role_name()
    target.role_id = role.id
    _log_admin_action(
        admin_user,
        "user.role_update",
        "user",
        target.id,
        target.username,
        payload.get("note"),
        {"old_role": old_role, "new_role": role.name},
    )
    db.session.commit()
    return jsonify({"user": _user_summary(target)}), 200


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@jwt_required()
def delete_user(user_id):
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    target = User.query.get(user_id)
    if target is None:
        return jsonify({"error": "User not found"}), 404
    target.is_deleted = True
    target.deleted_at = datetime.now(timezone.utc)
    _log_admin_action(admin_user, "user.delete", "user", target.id, target.username, payload.get("note"))
    db.session.commit()
    return jsonify({"user": _user_summary(target)}), 200


@admin_bp.route("/users/<int:user_id>/restore", methods=["POST"])
@jwt_required()
def restore_user(user_id):
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    target = User.query.get(user_id)
    if target is None:
        return jsonify({"error": "User not found"}), 404
    target.is_deleted = False
    target.deleted_at = None
    _log_admin_action(admin_user, "user.restore", "user", target.id, target.username, payload.get("note"))
    db.session.commit()
    return jsonify({"user": _user_summary(target)}), 200


@admin_bp.route("/content/summary", methods=["GET"])
@jwt_required()
def content_summary():
    admin_user, error = _admin_guard()
    if error:
        return error
    return jsonify(_content_summary()), 200


@admin_bp.route("/content/posts", methods=["GET"])
@jwt_required()
def list_posts():
    admin_user, error = _admin_guard()
    if error:
        return error
    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error
    query = Post.query
    search = request.args.get("search")
    deleted = request.args.get("deleted")
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(Post.title.ilike(pattern), Post.content.ilike(pattern)))
    if deleted in {"true", "false"}:
        query = query.filter(Post.is_deleted == (deleted == "true"))
    pagination = query.order_by(Post.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "posts": [_post_summary(post) for post in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
    }), 200


@admin_bp.route("/content/comments", methods=["GET"])
@jwt_required()
def list_comments():
    admin_user, error = _admin_guard()
    if error:
        return error
    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error
    query = Comment.query
    search = request.args.get("search")
    deleted = request.args.get("deleted")
    if search:
        query = query.filter(Comment.content.ilike(f"%{search.strip()}%"))
    if deleted in {"true", "false"}:
        query = query.filter(Comment.is_deleted == (deleted == "true"))
    pagination = query.order_by(Comment.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "comments": [_comment_summary(comment) for comment in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
    }), 200


def _soft_delete_record(model, record_id, deleted, action_name, target_type, serializer):
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    record = model.query.get(record_id)
    if record is None:
        return jsonify({"error": f"{target_type.title()} not found"}), 404
    record.is_deleted = deleted
    record.deleted_at = datetime.now(timezone.utc) if deleted else None
    target_label = getattr(record, "title", None) or getattr(record, "content", None)
    _log_admin_action(
        admin_user,
        action_name,
        target_type,
        record.id,
        (target_label or "")[:255],
        payload.get("note"),
    )
    db.session.commit()
    return jsonify({target_type: serializer(record)}), 200


@admin_bp.route("/content/posts/<int:post_id>/delete", methods=["POST"])
@jwt_required()
def delete_post(post_id):
    return _soft_delete_record(Post, post_id, True, "content.post_delete", "post", _post_summary)


@admin_bp.route("/content/posts/<int:post_id>/restore", methods=["POST"])
@jwt_required()
def restore_post(post_id):
    return _soft_delete_record(Post, post_id, False, "content.post_restore", "post", _post_summary)


@admin_bp.route("/content/comments/<int:comment_id>/delete", methods=["POST"])
@jwt_required()
def delete_comment(comment_id):
    return _soft_delete_record(Comment, comment_id, True, "content.comment_delete", "comment", _comment_summary)


@admin_bp.route("/content/comments/<int:comment_id>/restore", methods=["POST"])
@jwt_required()
def restore_comment(comment_id):
    return _soft_delete_record(Comment, comment_id, False, "content.comment_restore", "comment", _comment_summary)
