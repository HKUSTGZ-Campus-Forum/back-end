"""Private-beta HTTP boundary for the persistent MeetCampus world."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.user import User
from app.services.meetcampus_service import (
    MeetCampusDomainError,
    build_bootstrap_payload,
    can_access_meetcampus,
    complete_onboarding,
    correct_memory,
    create_bridge,
    create_command,
    mark_story_viewed,
    world_worker_status,
)


bp = Blueprint("meetcampus", __name__, url_prefix="/meetcampus")


def _response(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _authorized_user() -> User:
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError) as exc:
        raise MeetCampusDomainError("invalid_identity", "Invalid user identity.", 401) from exc
    user = db.session.get(User, user_id)
    if not can_access_meetcampus(user):
        raise MeetCampusDomainError(
            "meetcampus_beta_required",
            "MeetCampus is currently limited to invited private-beta accounts.",
            403,
        )
    return user


def _json_body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise MeetCampusDomainError("invalid_json", "A JSON object is required.")
    return payload


@bp.errorhandler(MeetCampusDomainError)
def _handle_domain_error(error: MeetCampusDomainError):
    db.session.rollback()
    return _response({"code": error.code, "message": error.message}, error.status)


@bp.get("/bootstrap")
@jwt_required()
def bootstrap():
    return _response(build_bootstrap_payload(_authorized_user()))


@bp.get("/snapshot")
@jwt_required()
def snapshot():
    payload = build_bootstrap_payload(_authorized_user())
    return _response({
        "snapshot": payload["snapshot"],
        "stories": payload["stories"],
        "relationships": payload["relationships"],
    })


@bp.post("/onboarding")
@jwt_required()
def onboarding():
    return _response(complete_onboarding(_authorized_user(), _json_body()))


@bp.post("/commands")
@jwt_required()
def commands():
    return _response(create_command(_authorized_user(), _json_body()), 201)


@bp.post("/stories/<string:story_id>/view")
@jwt_required()
def story_view(story_id: str):
    return _response(mark_story_viewed(_authorized_user(), story_id))


@bp.post("/stories/<string:story_id>/bridge")
@jwt_required()
def story_bridge(story_id: str):
    return _response(create_bridge(_authorized_user(), story_id), 201)


@bp.post("/memories/corrections")
@jwt_required()
def memory_correction():
    return _response(correct_memory(_authorized_user(), _json_body()), 201)


@bp.get("/worker/status")
@jwt_required()
def worker_status():
    _authorized_user()
    return _response(world_worker_status())
