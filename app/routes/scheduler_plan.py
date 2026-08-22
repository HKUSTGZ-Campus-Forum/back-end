from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.scheduler_plan import SchedulerPlan
from app.services.content_moderation_service import content_moderation
from app.services.scheduler_plans import (
    PlanConflictError,
    PlanValidationError,
    apply_plan_to_workspace,
    can_view_plan,
    clone_plan,
    create_plan,
    public_plan_query,
    serialize_plan,
    update_plan,
)


bp = Blueprint("scheduler_plans", __name__, url_prefix="/scheduler/plans")


def _current_user_id() -> Optional[int]:
    identity = get_jwt_identity()
    if identity is None:
        return None
    try:
        return int(identity)
    except (TypeError, ValueError):
        return None


def _json_body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise PlanValidationError("A JSON object is required", "invalid_json")
    return payload


def _plan_by_public_id(public_id: str) -> SchedulerPlan | None:
    return SchedulerPlan.query.filter_by(public_id=public_id, is_deleted=False).first()


def _error_response(error: Exception, status: int):
    return jsonify({
        "error": str(error),
        "code": getattr(error, "code", "scheduler_plan_error"),
    }), status


def _moderate_plan_metadata(user_id: int, payload: dict) -> None:
    if "name" not in payload and "description" not in payload:
        return
    result = content_moderation.moderate_post(
        title=str(payload.get("name", "")),
        content=str(payload.get("description", "")),
        data_id=f"scheduler_plan_{user_id}",
    )
    if not result["is_safe"]:
        current_app.logger.warning(
            "Content moderation blocked scheduler plan metadata for user %s: %s",
            user_id,
            result["reason"],
        )
        raise PlanValidationError(
            "Plan name or description does not meet community guidelines",
            "content_moderation_rejected",
        )


@bp.route("", methods=["POST"])
@jwt_required()
def create_saved_plan():
    user_id = _current_user_id()
    try:
        payload = _json_body()
        _moderate_plan_metadata(user_id, payload)
        plan = create_plan(user_id, payload)
    except PlanValidationError as exc:
        db.session.rollback()
        return _error_response(exc, 400)
    response = jsonify(serialize_plan(plan, viewer_id=user_id))
    response.status_code = 201
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/mine", methods=["GET"])
@jwt_required()
def list_my_plans():
    user_id = _current_user_id()
    semester_id = request.args.get("semester_id", "").strip()
    query = SchedulerPlan.query.filter_by(owner_id=user_id, is_deleted=False)
    if semester_id:
        query = query.filter(SchedulerPlan.semester_id == semester_id)
    plans = query.order_by(SchedulerPlan.updated_at.desc()).all()
    response = jsonify({"plans": [serialize_plan(plan, viewer_id=user_id) for plan in plans]})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/shared", methods=["GET"])
@jwt_required(optional=True)
def list_shared_plans():
    viewer_id = _current_user_id()
    page = max(1, request.args.get("page", 1, type=int))
    page_size = min(24, max(1, request.args.get("page_size", 12, type=int)))
    pagination = public_plan_query(
        semester_id=request.args.get("semester_id", "").strip(),
        course_code=request.args.get("course_code", "").strip(),
    ).paginate(page=page, per_page=page_size, error_out=False)
    response = jsonify({
        "plans": [serialize_plan(plan, viewer_id=viewer_id) for plan in pagination.items],
        "page": page,
        "page_size": page_size,
        "total": pagination.total,
        "total_pages": pagination.pages,
    })
    response.headers["Cache-Control"] = (
        "private, no-store" if viewer_id is not None else "public, max-age=60"
    )
    response.headers["Vary"] = "Authorization"
    return response


@bp.route("/<string:public_id>", methods=["GET"])
@jwt_required(optional=True)
def get_saved_plan(public_id: str):
    viewer_id = _current_user_id()
    plan = _plan_by_public_id(public_id)
    if not can_view_plan(plan, viewer_id):
        return jsonify({"error": "Plan not found", "code": "plan_not_found"}), 404
    response = jsonify(serialize_plan(plan, viewer_id=viewer_id))
    response.headers["Cache-Control"] = (
        "private, no-store" if viewer_id == plan.owner_id else "public, max-age=60"
    )
    response.headers["Vary"] = "Authorization"
    return response


@bp.route("/<string:public_id>", methods=["PATCH"])
@jwt_required()
def update_saved_plan(public_id: str):
    user_id = _current_user_id()
    plan = _plan_by_public_id(public_id)
    if plan is None or plan.owner_id != user_id:
        return jsonify({"error": "Plan not found", "code": "plan_not_found"}), 404
    try:
        payload = _json_body()
        _moderate_plan_metadata(user_id, payload)
        plan = update_plan(plan, payload)
    except PlanValidationError as exc:
        db.session.rollback()
        return _error_response(exc, 400)
    except PlanConflictError as exc:
        db.session.rollback()
        return _error_response(exc, 409)
    response = jsonify(serialize_plan(plan, viewer_id=user_id))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/<string:public_id>", methods=["DELETE"])
@jwt_required()
def delete_saved_plan(public_id: str):
    user_id = _current_user_id()
    plan = _plan_by_public_id(public_id)
    if plan is None or plan.owner_id != user_id:
        return jsonify({"error": "Plan not found", "code": "plan_not_found"}), 404
    plan.is_deleted = True
    plan.deleted_at = datetime.now(timezone.utc)
    plan.visibility = SchedulerPlan.VISIBILITY_PRIVATE
    plan.published_at = None
    plan.content_version += 1
    db.session.commit()
    return "", 204


@bp.route("/<string:public_id>/clone", methods=["POST"])
@jwt_required()
def clone_saved_plan(public_id: str):
    user_id = _current_user_id()
    source = _plan_by_public_id(public_id)
    if not can_view_plan(source, user_id):
        return jsonify({"error": "Plan not found", "code": "plan_not_found"}), 404
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise PlanValidationError("A JSON object is required", "invalid_json")
        clone = clone_plan(source, user_id, payload.get("name"))
    except PlanValidationError as exc:
        db.session.rollback()
        return _error_response(exc, 400)
    response = jsonify(serialize_plan(clone, viewer_id=user_id))
    response.status_code = 201
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/<string:public_id>/apply", methods=["POST"])
@jwt_required()
def apply_saved_plan(public_id: str):
    user_id = _current_user_id()
    plan = _plan_by_public_id(public_id)
    if not can_view_plan(plan, user_id):
        return jsonify({"error": "Plan not found", "code": "plan_not_found"}), 404
    try:
        apply_plan_to_workspace(plan, user_id)
    except PlanConflictError as exc:
        db.session.rollback()
        return _error_response(exc, 409)
    response = jsonify({
        "ok": True,
        "plan": serialize_plan(plan, viewer_id=user_id),
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response
