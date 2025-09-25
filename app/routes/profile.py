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
        if 'thrust' in data:
            profile.thrust = data['thrust'] if isinstance(data['thrust'], list) else []
        if 'experience_level' in data:
            if data['experience_level'] in ['beginner', 'intermediate', 'advanced', 'expert']:
                profile.experience_level = data['experience_level']
        if 'preferred_roles' in data:
            profile.preferred_roles = data['preferred_roles'] if isinstance(data['preferred_roles'], list) else []
        if 'availability' in data:
            profile.availability = data['availability']
        if 'contact_preferences' in data:
            profile.contact_preferences = data['contact_preferences'] if isinstance(data['contact_preferences'], dict) else {}
        if 'contact_methods' in data:
            profile.contact_methods = data['contact_methods'] if isinstance(data['contact_methods'], list) else []
        if 'is_active' in data:
            profile.is_active = bool(data['is_active'])

        # Flush changes to get ID assigned for new profiles
        db.session.flush()

        # Update embedding if profile has meaningful content (non-blocking)
        embedding_success = True
        if profile.is_complete():
            try:
                embedding_success = matching_service.update_profile_embedding(profile.id)
                if not embedding_success:
                    logger.warning(f"Failed to update embedding for profile {profile.id}")
            except Exception as e:
                logger.error(f"Error during embedding update for profile {profile.id}: {e}")
                embedding_success = False

        # Save to database after embedding update - proceed even if embedding fails
        logger.info(f"About to commit profile changes for user {current_user_id}")
        try:
            db.session.commit()
            logger.info(f"Successfully committed profile for user {current_user_id} with ID {profile.id}")

            # Verify the record exists immediately after commit
            verification = UserProfile.query.get(profile.id)
            if verification:
                logger.info(f"✅ Profile ID {profile.id} verified in database after commit")
            else:
                logger.error(f"❌ Profile ID {profile.id} NOT FOUND in database after commit!")

        except Exception as commit_error:
            logger.error(f"Failed to commit profile for user {current_user_id}: {commit_error}")
            db.session.rollback()
            raise

        return jsonify({
            "success": True,
            "message": "Profile updated successfully",
            "profile": profile.to_dict(),
            "embedding_updated": embedding_success if profile.is_complete() else None
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
@jwt_required(optional=True)
def get_profile_by_user_id(user_id):
    """Get user profile by user ID (public endpoint for viewing profiles)"""
    try:
        profile = UserProfile.query.filter_by(user_id=user_id).first()
        if not profile:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        # Only show active profiles
        if not profile.is_active:
            return jsonify({"success": False, "message": "Profile not found"}), 404

        # Get current user ID if authenticated (for checking if it's their own profile)
        current_user_id = None
        try:
            current_user_id = get_jwt_identity()
        except:
            pass

        # In the matching system, contact info should be visible to facilitate collaboration
        # Include contact info only if it's their own profile or if they choose to make it public
        include_contact = current_user_id == user_id
        profile_data = profile.to_dict()

        # For the matching system, show actual contact details if available
        # The system is designed for team collaboration, so contact visibility is important
        if not include_contact:
            # Show actual contact methods for the matching/collaboration system
            # If user has contact_methods (new format), use those
            if profile_data.get('contact_methods'):
                # contact_methods already contains actual values, keep them as-is
                pass
            elif profile_data.get('contact_preferences'):
                # Legacy format: convert contact_preferences to contact_methods with real values
                profile_data['contact_methods'] = []
                for method, value in profile_data['contact_preferences'].items():
                    if value and isinstance(value, str) and value != '***':  # Only show real values
                        profile_data['contact_methods'].append({
                            'method': method,
                            'value': value  # Show actual contact details for team collaboration
                        })

        return jsonify({
            "success": True,
            "profile": profile_data
        }), 200

    except Exception as e:
        logger.error(f"Error getting profile for user {user_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get profile"}), 500

@profile_bp.route('/', methods=['GET'])
@jwt_required(optional=True)
def search_profiles():
    """Search user profiles (public endpoint for discovery)"""
    try:
        # Query parameters
        query = request.args.get('q', '').strip()
        skills = request.args.get('skills', '').strip()
        experience_level = request.args.get('experience_level', '').strip()
        availability = request.args.get('availability', '').strip()
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 50)

        # Build query
        profiles_query = UserProfile.query.filter_by(is_active=True)

        # Filter by experience level
        if experience_level and experience_level in ['beginner', 'intermediate', 'advanced', 'expert']:
            profiles_query = profiles_query.filter(UserProfile.experience_level == experience_level)

        # Filter by availability
        if availability and availability in ['full-time', 'part-time', 'weekends', 'flexible']:
            profiles_query = profiles_query.filter(UserProfile.availability == availability)

        # Text search in bio, skills, and interests
        if query:
            search_term = f'%{query.lower()}%'
            profiles_query = profiles_query.filter(
                db.or_(
                    UserProfile.bio.ilike(search_term),
                    UserProfile.skills.op('::text').ilike(search_term),
                    UserProfile.interests.op('::text').ilike(search_term)
                )
            )

        # Filter by skills (simplified - could be enhanced with better matching)
        if skills:
            skill_list = [s.strip().lower() for s in skills.split(',')]
            for skill in skill_list:
                profiles_query = profiles_query.filter(
                    UserProfile.skills.op('::text')().__contains__(skill)
                )

        # Order by most recently updated and complete profiles first
        profiles_query = profiles_query.order_by(
            UserProfile.updated_at.desc()
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