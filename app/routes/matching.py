from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.orm import joinedload
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.extensions import db
from app.services.matching_service import matching_service
import logging

logger = logging.getLogger(__name__)

matching_bp = Blueprint('matching', __name__, url_prefix='/matching')

@matching_bp.route('/projects', methods=['GET'])
@jwt_required()
def get_project_recommendations():
    """Get project recommendations for current user"""
    try:
        current_user_id = get_jwt_identity()

        # Check if user has a profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()
        if not profile:
            return jsonify({
                "success": False,
                "message": "Please complete your profile first to get recommendations",
                "profile_required": True
            }), 400

        if not profile.is_complete():
            return jsonify({
                "success": False,
                "message": "Please complete your profile (bio, skills, experience level) to get better recommendations",
                "profile_incomplete": True,
                "profile": profile.to_dict()
            }), 400

        limit = min(int(request.args.get('limit', 10)), 20)
        matches = matching_service.find_project_matches(current_user_id, limit)

        return jsonify({
            "success": True,
            "matches": matches,
            "profile": profile.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error getting project recommendations: {e}")
        return jsonify({"success": False, "message": "Failed to get recommendations"}), 500

@matching_bp.route('/teammates/<int:project_id>', methods=['GET'])
@jwt_required()
def get_teammate_recommendations(project_id):
    """Get teammate recommendations for a specific project"""
    try:
        current_user_id = get_jwt_identity()

        # Verify project exists and user owns it
        project = Project.query.get(project_id)
        if not project or project.is_deleted:
            return jsonify({"success": False, "message": "Project not found"}), 404

        if str(project.user_id) != str(current_user_id):
            return jsonify({"success": False, "message": "Permission denied"}), 403

        limit = min(int(request.args.get('limit', 10)), 20)
        matches = matching_service.find_teammate_matches(project_id, limit)

        return jsonify({
            "success": True,
            "matches": matches,
            "project": {
                "id": project.id,
                "title": project.title,
                "status": project.status,
                "current_team_size": project.get_current_team_size(),
                "team_size_max": project.team_size_max
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting teammate recommendations for project {project_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get recommendations"}), 500





@matching_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def get_matching_dashboard():
    """Get dashboard data for matching system"""
    try:
        current_user_id = get_jwt_identity()

        # Get user profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()

        # Get user's projects
        user_projects = Project.query.filter_by(user_id=current_user_id, is_deleted=False)\
                                   .order_by(Project.created_at.desc())\
                                   .limit(5).all()

        # Quick stats
        stats = {
            "projects_created": len(user_projects),
            "profile_complete": profile.is_complete() if profile else False
        }

        return jsonify({
            "success": True,
            "profile": profile.to_dict() if profile else None,
            "user_projects": [project.to_dict(current_user_id=current_user_id) for project in user_projects],
            "stats": stats
        }), 200

    except Exception as e:
        logger.error(f"Error getting dashboard data: {e}")
        return jsonify({"success": False, "message": "Failed to get dashboard data"}), 500

@matching_bp.route('/contact-visibility/<int:target_user_id>', methods=['GET'])
@jwt_required()
def check_contact_visibility(target_user_id):
    """Check if current user can see contact info of target user"""
    try:
        current_user_id = get_jwt_identity()

        # Users can always see their own contact info
        if current_user_id == target_user_id:
            return jsonify({
                "success": True,
                "can_see_contact": True,
                "reason": "own_profile"
            }), 200

        # In simplified system, allow contact visibility for matched users
        # This could be enhanced with specific rules later
        return jsonify({
            "success": True,
            "can_see_contact": True,
            "reason": "matching_enabled"
        }), 200

    except Exception as e:
        logger.error(f"Error checking contact visibility for user {target_user_id}: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to check contact visibility",
            "can_see_contact": False
        }), 500