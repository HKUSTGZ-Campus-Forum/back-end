from datetime import datetime, timedelta, timezone

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
from app.services.admin_audit_service import log_admin_action
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


def _group_counts(model, column):
    rows = (
        db.session.query(column, func.count(model.id))
        .group_by(column)
        .all()
    )
    return {str(key or "unknown"): int(count) for key, count in rows}


def _status_counts(model, column, statuses):
    return {status: model.query.filter(column == status).count() for status in statuses}


def _audit_group_counts(column, limit=12):
    rows = (
        db.session.query(column, func.count(AdminAuditLog.id))
        .group_by(column)
        .order_by(func.count(AdminAuditLog.id).desc())
        .limit(limit)
        .all()
    )
    return {str(key or "unknown"): int(count) for key, count in rows}


def _user_counts():
    total = _count(User.query)
    deleted = _count(User.query.filter_by(is_deleted=True))
    email_verified = _count(User.query.filter_by(email_verified=True))
    role_rows = (
        db.session.query(UserRole.name, func.count(User.id))
        .outerjoin(User, User.role_id == UserRole.id)
        .group_by(UserRole.name)
        .all()
    )
    roles = {str(name or "unknown"): int(count) for name, count in role_rows}
    return {
        "total": total,
        "active": _count(User.query.filter_by(is_deleted=False)),
        "deleted": deleted,
        "email_verified": email_verified,
        "email_unverified": max(total - email_verified, 0),
        "roles": roles,
        "status": {
            "active": max(total - deleted, 0),
            "deleted": deleted,
        },
        "email": {
            "verified": email_verified,
            "unverified": max(total - email_verified, 0),
        },
    }


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


def _gugu_summary(message):
    return {
        "id": message.id,
        "author_id": message.author_id,
        "author": message.author.username if message.author else None,
        "content": message.content,
        "reply_to_message_id": message.reply_to_message_id,
        "is_deleted": message.is_deleted,
        "deleted_at": message.deleted_at.isoformat() if message.deleted_at else None,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _file_summary(file_record):
    return {
        "id": file_record.id,
        "user_id": file_record.user_id,
        "owner": file_record.owner.username if file_record.owner else None,
        "original_filename": file_record.original_filename,
        "file_size": file_record.file_size,
        "mime_type": file_record.mime_type,
        "status": file_record.status,
        "file_type": file_record.file_type,
        "entity_type": file_record.entity_type,
        "entity_id": file_record.entity_id,
        "is_deleted": file_record.is_deleted,
        "deleted_at": file_record.deleted_at.isoformat() if file_record.deleted_at else None,
        "created_at": file_record.created_at.isoformat() if file_record.created_at else None,
        "updated_at": file_record.updated_at.isoformat() if file_record.updated_at else None,
    }


def _content_summary():
    file_status = _group_counts(File, File.status)
    file_types = _group_counts(File, File.file_type)
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
            "active": _count(File.query.filter_by(is_deleted=False)),
            "status": file_status,
            "file_type": file_types,
        },
        "gugu": {
            "messages": _count(GuguMessage.query),
            "active": _count(GuguMessage.query.filter_by(is_deleted=False)),
            "deleted": _count(GuguMessage.query.filter_by(is_deleted=True)),
        },
    }


def _courses_summary():
    total = _count(Course.query)
    active = _count(Course.query.filter_by(is_active=True, is_deleted=False))
    deleted = _count(Course.query.filter_by(is_deleted=True))
    return {
        "courses": total,
        "active_courses": active,
        "offerings": _count(CourseOffering.query),
        "sections": _count(CourseSection.query),
        "meetings": _count(CourseMeeting.query),
        "deleted_courses": deleted,
        "inactive_courses": max(total - active - deleted, 0),
        "course_status": {
            "active": active,
            "inactive": max(total - active - deleted, 0),
            "deleted": deleted,
        },
    }


def _matching_summary():
    statuses = [
        Project.STATUS_RECRUITING,
        Project.STATUS_ACTIVE,
        Project.STATUS_COMPLETED,
        Project.STATUS_CANCELLED,
    ]
    return {
        "projects": _count(Project.query),
        "active_projects": _count(Project.query.filter_by(is_deleted=False)),
        "profiles": _count(UserProfile.query),
        "active_profiles": _count(UserProfile.query.filter_by(is_active=True)),
        "project_status": _status_counts(Project, Project.status, statuses),
        "project_types": _group_counts(Project, Project.project_type),
        "difficulty": _group_counts(Project, Project.difficulty_level),
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
            **_user_counts(),
            "admins": _count(User.query.join(UserRole).filter(UserRole.name == UserRole.ADMIN)),
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
        "courses": _courses_summary(),
        "academic_map": _academic_map_summary(),
        "matching": _matching_summary(),
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

    active = _count(ContestInfo.query.filter_by(is_active=True))
    return {
        "contests": _count(ContestInfo.query),
        "active_contests": active,
        "organizers": _count(ContestOrganizer.query),
        "submissions": _count(ContestSubmission.query),
        "contest_status": {
            "active": active,
            "inactive": _count(ContestInfo.query.filter_by(is_active=False)),
        },
        "submission_tracks": _group_counts(ContestSubmission, ContestSubmission.track),
    }


def _operations_summary():
    total_tokens = _count(STSTokenPool.query)
    valid_tokens = _count(STSTokenPool.query.filter(STSTokenPool.expiration > datetime.now(timezone.utc)))
    total_notifications = _count(Notification.query)
    unread_notifications = _count(Notification.query.filter_by(read=False))
    return {
        "files": _count(File.query),
        "sts_tokens": total_tokens,
        "valid_sts_tokens": valid_tokens,
        "oauth_clients": _count(OAuthClient.query),
        "oauth_tokens": _count(OAuthToken.query),
        "notifications": total_notifications,
        "unread_notifications": unread_notifications,
        "push_subscriptions": _count(PushSubscription.query),
        "sts_token_status": {
            "valid": valid_tokens,
            "expired": max(total_tokens - valid_tokens, 0),
        },
        "notification_status": {
            "unread": unread_notifications,
            "read": max(total_notifications - unread_notifications, 0),
        },
        "file_status": _group_counts(File, File.status),
    }


def _parse_trend_days():
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Invalid days"}), 400)
    if days not in {7, 30}:
        return None, (jsonify({"error": "days must be 7 or 30"}), 400)
    return days, None


def _date_bucket_counts(model, created_column, start_at):
    rows = (
        db.session.query(func.date(created_column), func.count(model.id))
        .filter(created_column >= start_at)
        .group_by(func.date(created_column))
        .all()
    )
    return {str(day): int(count) for day, count in rows}


def _overview_trends(days):
    from app.models.academic_map import UserCourseRecord

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    start_at = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    series = {
        "users": _date_bucket_counts(User, User.created_at, start_at),
        "posts": _date_bucket_counts(Post, Post.created_at, start_at),
        "comments": _date_bucket_counts(Comment, Comment.created_at, start_at),
        "feedbacks": _date_bucket_counts(Feedback, Feedback.created_at, start_at),
        "identity_requests": _date_bucket_counts(UserIdentity, UserIdentity.created_at, start_at),
        "course_records": _date_bucket_counts(UserCourseRecord, UserCourseRecord.created_at, start_at),
        "projects": _date_bucket_counts(Project, Project.created_at, start_at),
        "files": _date_bucket_counts(File, File.created_at, start_at),
    }

    rows = []
    for offset in range(days):
        day = (start_date + timedelta(days=offset)).isoformat()
        row = {"date": day}
        for key, counts in series.items():
            row[key] = counts.get(day, 0)
        rows.append(row)
    return {"days": days, "items": rows}


def _admin_summary_response(summary_builder):
    _admin_user, error = _admin_guard()
    if error:
        return error
    return jsonify(summary_builder()), 200


def _active_admin_count(exclude_user_id=None):
    query = User.query.join(UserRole).filter(
        UserRole.name == UserRole.ADMIN,
        User.is_deleted.is_(False),
    )
    if exclude_user_id is not None:
        query = query.filter(User.id != exclude_user_id)
    return query.count()


def _ensure_user_admin_mutation_allowed(admin_user, target, requested_role_name=None, deleting=False):
    if target.id == admin_user.id and deleting:
        return jsonify({"error": "Admins cannot delete their own account"}), 400

    target_is_admin = target.get_role_name() == UserRole.ADMIN
    demoting_admin = target_is_admin and requested_role_name and requested_role_name != UserRole.ADMIN
    deleting_admin = target_is_admin and deleting

    if deleting_admin and target.id != admin_user.id:
        return jsonify({"error": "Admin accounts cannot be deleted from this console"}), 400

    if (demoting_admin or deleting_admin) and _active_admin_count(exclude_user_id=target.id) == 0:
        return jsonify({"error": "At least one active admin is required"}), 400

    return None


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


@admin_bp.route("/overview/trends", methods=["GET"])
@jwt_required()
def overview_trends():
    admin_user, error = _admin_guard()
    if error:
        return error

    days, days_error = _parse_trend_days()
    if days_error:
        return days_error
    return jsonify(_overview_trends(days)), 200


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


@admin_bp.route("/audit-logs/summary", methods=["GET"])
@jwt_required()
def audit_logs_summary():
    admin_user, error = _admin_guard()
    if error:
        return error

    return jsonify({
        "total": _count(AdminAuditLog.query),
        "actions": _audit_group_counts(AdminAuditLog.action),
        "target_types": _audit_group_counts(AdminAuditLog.target_type),
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
        "counts": _user_counts(),
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
    safety_error = _ensure_user_admin_mutation_allowed(
        admin_user,
        target,
        requested_role_name=role.name,
    )
    if safety_error:
        return safety_error

    target.role_id = role.id
    log_admin_action(
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
    safety_error = _ensure_user_admin_mutation_allowed(admin_user, target, deleting=True)
    if safety_error:
        return safety_error
    target.is_deleted = True
    target.deleted_at = datetime.now(timezone.utc)
    log_admin_action(admin_user, "user.delete", "user", target.id, target.username, payload.get("note"))
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
    log_admin_action(admin_user, "user.restore", "user", target.id, target.username, payload.get("note"))
    db.session.commit()
    return jsonify({"user": _user_summary(target)}), 200


@admin_bp.route("/content/summary", methods=["GET"])
@jwt_required()
def content_summary():
    admin_user, error = _admin_guard()
    if error:
        return error
    return jsonify(_content_summary()), 200


@admin_bp.route("/courses/summary", methods=["GET"])
@jwt_required()
def courses_summary():
    return _admin_summary_response(_courses_summary)


@admin_bp.route("/matching/summary", methods=["GET"])
@jwt_required()
def matching_summary():
    return _admin_summary_response(_matching_summary)


@admin_bp.route("/contest/summary", methods=["GET"])
@jwt_required()
def contest_summary():
    return _admin_summary_response(_contest_summary)


@admin_bp.route("/operations/summary", methods=["GET"])
@jwt_required()
def operations_summary():
    return _admin_summary_response(_operations_summary)


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


@admin_bp.route("/content/gugu", methods=["GET"])
@jwt_required()
def list_gugu_messages():
    admin_user, error = _admin_guard()
    if error:
        return error
    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error
    query = GuguMessage.query
    search = request.args.get("search")
    deleted = request.args.get("deleted")
    if search:
        query = query.filter(GuguMessage.content.ilike(f"%{search.strip()}%"))
    if deleted in {"true", "false"}:
        query = query.filter(GuguMessage.is_deleted == (deleted == "true"))
    pagination = query.order_by(GuguMessage.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "gugu": [_gugu_summary(message) for message in pagination.items],
        "total": pagination.total,
        "page": page,
        "pages": pagination.pages,
        "per_page": per_page,
    }), 200


@admin_bp.route("/content/files", methods=["GET"])
@jwt_required()
def list_files():
    admin_user, error = _admin_guard()
    if error:
        return error
    page, per_page, pagination_error = _pagination_args()
    if pagination_error:
        return pagination_error
    query = File.query
    search = request.args.get("search")
    deleted = request.args.get("deleted")
    status = request.args.get("status")
    file_type = request.args.get("file_type")
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(File.original_filename.ilike(pattern), File.object_name.ilike(pattern)))
    if deleted in {"true", "false"}:
        query = query.filter(File.is_deleted == (deleted == "true"))
    if status:
        query = query.filter(File.status == status)
    if file_type:
        query = query.filter(File.file_type == file_type)
    pagination = query.order_by(File.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "files": [_file_summary(file_record) for file_record in pagination.items],
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
    log_admin_action(
        admin_user,
        action_name,
        target_type,
        record.id,
        (target_label or "")[:255],
        payload.get("note"),
    )
    db.session.commit()
    return jsonify({target_type: serializer(record)}), 200


def _set_gugu_deleted(message_id, deleted):
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    message = GuguMessage.query.get(message_id)
    if message is None:
        return jsonify({"error": "Gugu message not found"}), 404
    message.is_deleted = deleted
    message.deleted_at = datetime.now(timezone.utc) if deleted else None
    log_admin_action(
        admin_user,
        "content.gugu_delete" if deleted else "content.gugu_restore",
        "gugu_message",
        message.id,
        (message.content or "")[:255],
        payload.get("note"),
    )
    db.session.commit()
    return jsonify({"gugu": _gugu_summary(message)}), 200


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


@admin_bp.route("/content/gugu/<int:message_id>/delete", methods=["POST"])
@jwt_required()
def delete_gugu_message(message_id):
    return _set_gugu_deleted(message_id, True)


@admin_bp.route("/content/gugu/<int:message_id>/restore", methods=["POST"])
@jwt_required()
def restore_gugu_message(message_id):
    return _set_gugu_deleted(message_id, False)


@admin_bp.route("/content/files/<int:file_id>/delete", methods=["POST"])
@jwt_required()
def delete_file(file_id):
    return _soft_delete_record(File, file_id, True, "content.file_delete", "file", _file_summary)


@admin_bp.route("/content/files/<int:file_id>/restore", methods=["POST"])
@jwt_required()
def restore_file(file_id):
    return _soft_delete_record(File, file_id, False, "content.file_restore", "file", _file_summary)
