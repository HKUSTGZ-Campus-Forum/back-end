from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.user import User
from app.services.meetcampus_service import build_bootstrap_payload, can_access_meetcampus


bp = Blueprint("meetcampus", __name__, url_prefix="/meetcampus")


@bp.get("/bootstrap")
@jwt_required()
def bootstrap():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"code": "invalid_identity", "message": "Invalid user identity"}), 401

    user = db.session.get(User, user_id)
    if not can_access_meetcampus(user):
        response = jsonify({
            "code": "meetcampus_beta_required",
            "message": "MeetCampus is currently limited to invited private-beta accounts.",
        })
        response.status_code = 403
        response.headers["Cache-Control"] = "no-store"
        return response

    response = jsonify(build_bootstrap_payload())
    response.headers["Cache-Control"] = "private, no-store"
    return response
