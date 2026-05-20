from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from app.models.user import User


def get_authenticated_user():
    user_id = get_jwt_identity()
    if user_id is None:
        return None

    try:
        return User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None


def require_admin_user():
    user = get_authenticated_user()

    if not user or user.is_deleted:
        return None, (jsonify({"error": "Authenticated user not found"}), 401)

    if not user.is_admin():
        return None, (jsonify({"error": "Admin access required"}), 403)

    return user, None
