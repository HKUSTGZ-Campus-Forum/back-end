from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity, jwt_required, get_jwt
from app.models.user_profile import UserProfile
from app.models.user import User
from app.extensions import db
from app.services.matching_service import matching_service
import logging

logger = logging.getLogger(__name__)

profile_bp = Blueprint('profile', __name__, url_prefix='/profiles')

@profile_bp.route('', methods=['GET'])
@jwt_required()
def get_current_user_profile():
    """Get current user's profile"""
    try:
        current_user_id = get_jwt_identity()
        profile = UserProfile.get_or_create_for_user(current_user_id)
        return jsonify({
            "success": True,
            "profile": profile.to_dict()
        }), 200
    except Exception as e:
        logger.error(f"Error getting profile: {e}")
        return jsonify({"success": False, "message": "Failed to get profile"}), 500

@profile_bp.route('', methods=['POST'])
@jwt_required()
def create_or_update_profile():
    """Create or update current user's profile"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()

        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        # Get or create profile
        profile = UserProfile.get_or_create_for_user(current_user_id)

        # Update profile fields
        if 'bio' in data:
            profile.bio = data['bio']
        if 'skills' in data:
            profile.skills = data['skills'] if isinstance(data['skills'], list) else []
        if 'interests' in data:
            profile.interests = data['interests'] if isinstance(data['interests'], list) else []
        if 'experience_level' in data:
            if data['experience_level'] in ['beginner', 'intermediate', 'advanced', 'expert']:
                profile.experience_level = data['experience_level']
        if 'preferred_roles' in data:
            profile.preferred_roles = data['preferred_roles'] if isinstance(data['preferred_roles'], list) else []
        if 'availability' in data:
            profile.availability = data['availability']
        if 'contact_preferences' in data:
            profile.contact_preferences = data['contact_preferences'] if isinstance(data['contact_preferences'], dict) else {}
        if 'is_active' in data:
            profile.is_active = bool(data['is_active'])

        # Save to database
        db.session.commit()

        # Update embedding if profile has meaningful content
        if profile.is_complete():
            success = matching_service.update_profile_embedding(profile.id)
            if not success:
                logger.warning(f"Failed to update embedding for profile {profile.id}")

        return jsonify({
            "success": True,
            "message": "Profile updated successfully",
            "profile": profile.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to update profile"}), 500

@profile_bp.route('/<int:profile_id>', methods=['GET'])
@jwt_required()
def get_profile_by_id(profile_id):
    """Get a specific user profile by ID"""
    try:
        profile = UserProfile.query.get(profile_id)
        if not profile:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        # Only show active profiles to others
        current_user_id = get_jwt_identity()
        if profile.user_id != current_user_id and not profile.is_active:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        return jsonify({
            "success": True,
            "profile": profile.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error getting profile {profile_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get profile"}), 500

@profile_bp.route('/user/<int:user_id>', methods=['GET'])
@jwt_required()
def get_profile_by_user_id(user_id):
    """Get user profile by user ID"""
    try:
        profile = UserProfile.query.filter_by(user_id=user_id).first()
        if not profile:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        # Only show active profiles to others
        current_user_id = get_jwt_identity()
        if user_id != current_user_id and not profile.is_active:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        return jsonify({
            "success": True,
            "profile": profile.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error getting profile for user {user_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get profile"}), 500

@profile_bp.route('/search', methods=['GET'])
@jwt_required()
def search_profiles():
    """Search user profiles"""
    try:
        # Query parameters
        query = request.args.get('q', '').strip()
        skills = request.args.get('skills', '').strip()
        experience_level = request.args.get('experience_level', '').strip()
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 50)

        # Build query
        profiles_query = UserProfile.query.filter_by(is_active=True)

        # Filter by experience level
        if experience_level and experience_level in ['beginner', 'intermediate', 'advanced', 'expert']:
            profiles_query = profiles_query.filter(UserProfile.experience_level == experience_level)

        # Text search in bio
        if query:
            profiles_query = profiles_query.filter(UserProfile.bio.ilike(f'%{query}%'))

        # Filter by skills (simplified - could be enhanced with better matching)
        if skills:
            skill_list = [s.strip().lower() for s in skills.split(',')]
            for skill in skill_list:
                profiles_query = profiles_query.filter(
                    UserProfile.skills.op('::text')().__contains__(skill)
                )

        # Pagination
        offset = (page - 1) * limit
        profiles = profiles_query.offset(offset).limit(limit).all()
        total = profiles_query.count()

        return jsonify({
            "success": True,
            "profiles": [profile.to_dict() for profile in profiles],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }), 200

    except Exception as e:
        logger.error(f"Error searching profiles: {e}")
        return jsonify({"success": False, "message": "Search failed"}), 500

@profile_bp.route('/refresh-embedding', methods=['POST'])
@jwt_required()
def refresh_profile_embedding():
    """Refresh embedding for current user's profile"""
    try:
        current_user_id = get_jwt_identity()
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()

        if not profile:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        if not profile.is_complete():
            return jsonify({"success": False, "message": "Profile is not complete enough for embedding"}), 400

        success = matching_service.update_profile_embedding(profile.id)

        if success:
            return jsonify({
                "success": True,
                "message": "Embedding updated successfully"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update embedding"
            }), 500

    except Exception as e:
        logger.error(f"Error refreshing embedding: {e}")
        return jsonify({"success": False, "message": "Failed to refresh embedding"}), 500

@profile_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_profile_stats():
    """Get profile statistics"""
    try:
        # Basic stats
        total_profiles = UserProfile.query.count()
        active_profiles = UserProfile.query.filter_by(is_active=True).count()
        complete_profiles = UserProfile.query.filter(
            UserProfile.bio.isnot(None),
            UserProfile.skills.isnot(None),
            UserProfile.experience_level.isnot(None)
        ).count()

        # Experience level distribution
        experience_stats = {}
        for level in ['beginner', 'intermediate', 'advanced', 'expert']:
            count = UserProfile.query.filter_by(experience_level=level, is_active=True).count()
            experience_stats[level] = count

        return jsonify({
            "success": True,
            "stats": {
                "total_profiles": total_profiles,
                "active_profiles": active_profiles,
                "complete_profiles": complete_profiles,
                "completion_rate": (complete_profiles / total_profiles * 100) if total_profiles > 0 else 0,
                "experience_distribution": experience_stats
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting profile stats: {e}")
        return jsonify({"success": False, "message": "Failed to get stats"}), 500