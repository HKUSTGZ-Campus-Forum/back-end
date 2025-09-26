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

@matching_bp.route('/profile/refresh-embedding', methods=['POST'])
@jwt_required()
def refresh_profile_embedding():
    """Refresh user's profile embedding with project-enhanced mode"""
    try:
        current_user_id = get_jwt_identity()

        # Get optional parameter for including projects
        include_projects = request.json.get('include_projects', True) if request.json else True

        # Get user profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()
        if not profile:
            return jsonify({
                "success": False,
                "message": "Please create your profile first"
            }), 400

        # Update embedding with enhanced mode
        success = matching_service.update_profile_embedding(
            profile.id,
            include_projects=include_projects
        )

        if success:
            db.session.commit()
            return jsonify({
                "success": True,
                "message": "Profile embedding updated successfully",
                "enhanced_mode": include_projects
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update profile embedding"
            }), 500

    except Exception as e:
        logger.error(f"Error refreshing profile embedding: {e}")
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "Failed to refresh embedding"
        }), 500

@matching_bp.route('/teammates', methods=['GET'])
@jwt_required()
def get_profile_based_teammates():
    """Get teammate recommendations based on user's profile"""
    try:
        current_user_id = get_jwt_identity()

        # Check if user has a profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()
        if not profile:
            return jsonify({
                "success": False,
                "message": "Please complete your profile first to get teammate recommendations",
                "profile_required": True
            }), 400

        if not profile.is_complete():
            return jsonify({
                "success": False,
                "message": "Please complete your profile (bio, skills, experience level) to get better teammate recommendations",
                "profile_incomplete": True,
                "profile": profile.to_dict()
            }), 400

        limit = min(int(request.args.get('limit', 10)), 20)

        # Use the matching service to find compatible profiles
        from app.services.matching_service import matching_service

        # Get similar profiles using the profile's embedding directly
        if not profile.embedding:
            return jsonify({
                "success": False,
                "message": "Profile embedding not found. Please refresh your profile embedding.",
                "embedding_required": True
            }), 400

        # Search for similar profiles
        similar_profiles = matching_service._vector_search(
            collection_name=matching_service.profiles_collection,
            query_vector=profile.embedding,
            limit=limit * 2  # Get more to filter
        )

        # Process results similar to find_teammate_matches
        matches = []
        for result in similar_profiles:
            profile_id = result.get("metadata", {}).get("profile_id")
            if not profile_id:
                continue

            candidate_profile = UserProfile.query.get(profile_id)
            if not candidate_profile or not candidate_profile.is_active:
                continue

            # Skip own profile
            if candidate_profile.user_id == current_user_id:
                continue

            # Create a simple compatibility score based on similarity
            similarity_score = result.get("score", 0.0)

            match_data = {
                "profile": candidate_profile.to_dict(),
                "similarity_score": similarity_score,
                "compatibility_score": similarity_score,  # Use similarity as compatibility for now
                "match_reasons": [
                    f"Similar profile and interests",
                    f"Complementary skills and experience"
                ],
                "combined_score": similarity_score
            }
            matches.append(match_data)

        # Sort by similarity score and return top matches
        matches.sort(key=lambda x: x["combined_score"], reverse=True)
        final_matches = matches[:limit]

        logger.info(f"Returning {len(final_matches)} teammate recommendations for user {current_user_id}")

        return jsonify({
            "success": True,
            "matches": final_matches,
            "profile": profile.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error getting teammate recommendations: {e}")
        return jsonify({"success": False, "message": "Failed to get teammate recommendations"}), 500

# Unified Template-Based Search Endpoints

@matching_bp.route('/search-projects', methods=['GET'])
@jwt_required()
def search_projects_by_text():
    """Unified semantic search for projects using text input"""
    try:
        current_user_id = get_jwt_identity()

        # Get search text from query parameter
        search_text = request.args.get('q', '').strip()
        if not search_text:
            return jsonify({
                "success": False,
                "message": "Search text is required"
            }), 400

        limit = min(int(request.args.get('limit', 10)), 20)

        # Use the new text-based search method
        matches = matching_service.find_projects_by_text(search_text, current_user_id, limit)

        return jsonify({
            "success": True,
            "matches": matches,
            "search_text": search_text,
            "search_type": "semantic"
        }), 200

    except Exception as e:
        logger.error(f"Error searching projects by text: {e}")
        return jsonify({"success": False, "message": "Failed to search projects"}), 500

@matching_bp.route('/search-teammates', methods=['GET'])
@jwt_required()
def search_teammates_by_text():
    """Unified semantic search for teammates using text input"""
    try:
        current_user_id = get_jwt_identity()

        # Get search text from query parameter
        search_text = request.args.get('q', '').strip()
        if not search_text:
            return jsonify({
                "success": False,
                "message": "Search text is required"
            }), 400

        limit = min(int(request.args.get('limit', 10)), 20)

        # Use the new text-based search method
        matches = matching_service.find_teammates_by_text(search_text, current_user_id, limit)

        return jsonify({
            "success": True,
            "matches": matches,
            "search_text": search_text,
            "search_type": "semantic"
        }), 200

    except Exception as e:
        logger.error(f"Error searching teammates by text: {e}")
        return jsonify({"success": False, "message": "Failed to search teammates"}), 500

# Template Endpoints

@matching_bp.route('/templates/profile', methods=['GET'])
@jwt_required()
def get_profile_template():
    """Get user's profile as a search template"""
    try:
        current_user_id = get_jwt_identity()

        profile = UserProfile.query.filter_by(user_id=current_user_id).first()
        if not profile:
            return jsonify({
                "success": False,
                "message": "Profile not found"
            }), 404

        # Get the text representation that would be used for search
        template_text = profile.get_text_representation()

        return jsonify({
            "success": True,
            "template": {
                "type": "profile",
                "title": "My Profile",
                "text": template_text,
                "description": "Search based on your profile, skills, and interests"
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting profile template: {e}")
        return jsonify({"success": False, "message": "Failed to get profile template"}), 500

@matching_bp.route('/templates/projects', methods=['GET'])
@jwt_required()
def get_project_templates():
    """Get user's projects as search templates"""
    try:
        current_user_id = get_jwt_identity()

        # Get user's recent projects
        projects = Project.query.filter_by(user_id=current_user_id, is_deleted=False)\
                               .order_by(Project.created_at.desc())\
                               .limit(5).all()

        templates = []
        for project in projects:
            template_text = project.get_text_representation()
            templates.append({
                "type": "project",
                "id": project.id,
                "title": project.title,
                "text": template_text,
                "description": f"Search based on your project: {project.title}"
            })

        return jsonify({
            "success": True,
            "templates": templates
        }), 200

    except Exception as e:
        logger.error(f"Error getting project templates: {e}")
        return jsonify({"success": False, "message": "Failed to get project templates"}), 500