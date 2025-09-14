from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity, jwt_required
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.models.project_application import ProjectApplication
from app.extensions import db
from app.services.matching_service import matching_service
import logging

logger = logging.getLogger(__name__)

matching_bp = Blueprint('matching', __name__)

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

        if project.user_id != current_user_id:
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

@matching_bp.route('/applications', methods=['POST'])
@jwt_required()
def apply_to_project():
    """Apply to a project"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()

        if not data or not data.get('project_id'):
            return jsonify({"success": False, "message": "Project ID is required"}), 400

        project_id = int(data['project_id'])
        project = Project.query.get(project_id)

        if not project or project.is_deleted:
            return jsonify({"success": False, "message": "Project not found"}), 404

        if not project.can_user_apply(current_user_id):
            if project.user_id == current_user_id:
                return jsonify({"success": False, "message": "You cannot apply to your own project"}), 400
            elif not project.is_recruiting():
                return jsonify({"success": False, "message": "This project is not currently recruiting"}), 400
            else:
                return jsonify({"success": False, "message": "You have already applied to this project"}), 400

        # Create application
        application = ProjectApplication(
            project_id=project_id,
            user_id=current_user_id,
            application_message=data.get('message', '').strip() if data.get('message') else None,
            proposed_role=data.get('proposed_role', '').strip() if data.get('proposed_role') else None
        )

        # Calculate match score if user has profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()
        if profile and profile.is_complete():
            # Use matching service to calculate compatibility
            compatibility = matching_service._calculate_compatibility_score(profile, project)
            application.set_match_score(
                compatibility.get('total_score', 0.5),
                compatibility.get('reasons', [])
            )

        db.session.add(application)

        # Increment project interest count
        project.increment_interest()

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Application submitted successfully",
            "application": application.to_dict(include_project=True, current_user_id=current_user_id)
        }), 201

    except Exception as e:
        logger.error(f"Error applying to project: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to submit application"}), 500

@matching_bp.route('/applications', methods=['GET'])
@jwt_required()
def get_my_applications():
    """Get current user's project applications"""
    try:
        current_user_id = get_jwt_identity()

        # Query parameters
        status = request.args.get('status', '').strip()
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 50)

        # Build query
        applications_query = ProjectApplication.get_for_user(current_user_id, status if status else None)
        applications_query = applications_query.order_by(ProjectApplication.created_at.desc())

        # Pagination
        offset = (page - 1) * limit
        applications = applications_query.offset(offset).limit(limit).all()
        total = applications_query.count()

        return jsonify({
            "success": True,
            "applications": [app.to_dict(include_project=True, current_user_id=current_user_id) for app in applications],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting user applications: {e}")
        return jsonify({"success": False, "message": "Failed to get applications"}), 500

@matching_bp.route('/applications/<int:application_id>', methods=['PUT'])
@jwt_required()
def update_application(application_id):
    """Update an application (accept/reject by project creator, or withdraw by applicant)"""
    try:
        current_user_id = get_jwt_identity()
        application = ProjectApplication.query.get(application_id)

        if not application:
            return jsonify({"success": False, "message": "Application not found"}), 404

        if not application.can_be_modified_by_user(current_user_id):
            return jsonify({"success": False, "message": "Permission denied"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        action = data.get('action', '').lower()
        message = data.get('message', '').strip() if data.get('message') else None

        if action == 'accept' and application.project.user_id == current_user_id:
            application.accept(message)
            response_message = "Application accepted successfully"
        elif action == 'reject' and application.project.user_id == current_user_id:
            application.reject(message)
            response_message = "Application rejected"
        elif action == 'withdraw' and application.user_id == current_user_id:
            application.withdraw()
            response_message = "Application withdrawn"
        else:
            return jsonify({"success": False, "message": "Invalid action"}), 400

        db.session.commit()

        return jsonify({
            "success": True,
            "message": response_message,
            "application": application.to_dict(include_project=True, include_user=True, current_user_id=current_user_id)
        }), 200

    except Exception as e:
        logger.error(f"Error updating application {application_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to update application"}), 500

@matching_bp.route('/applications/received', methods=['GET'])
@jwt_required()
def get_received_applications():
    """Get applications received for current user's projects"""
    try:
        current_user_id = get_jwt_identity()

        # Query parameters
        status = request.args.get('status', '').strip()
        project_id = request.args.get('project_id', '').strip()
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 50)

        # Build base query
        applications_query = ProjectApplication.query.join(Project).filter(
            Project.user_id == current_user_id,
            Project.is_deleted == False
        )

        # Filter by status
        if status:
            applications_query = applications_query.filter(ProjectApplication.status == status)

        # Filter by specific project
        if project_id:
            applications_query = applications_query.filter(ProjectApplication.project_id == int(project_id))

        # Order by created date (newest first)
        applications_query = applications_query.order_by(ProjectApplication.created_at.desc())

        # Pagination
        offset = (page - 1) * limit
        applications = applications_query.offset(offset).limit(limit).all()
        total = applications_query.count()

        return jsonify({
            "success": True,
            "applications": [app.to_dict(include_user=True, include_project=True, current_user_id=current_user_id) for app in applications],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting received applications: {e}")
        return jsonify({"success": False, "message": "Failed to get applications"}), 500

@matching_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def get_matching_dashboard():
    """Get dashboard data for matching system"""
    try:
        current_user_id = get_jwt_identity()

        # Get user profile
        profile = UserProfile.query.filter_by(user_id=current_user_id).first()

        # Get user's applications
        recent_applications = ProjectApplication.get_for_user(current_user_id)\
                                               .order_by(ProjectApplication.created_at.desc())\
                                               .limit(5).all()

        # Get applications received (if user has projects)
        received_applications = ProjectApplication.get_pending_for_creator(current_user_id).limit(5).all()

        # Get user's projects
        user_projects = Project.query.filter_by(user_id=current_user_id, is_deleted=False)\
                                   .order_by(Project.created_at.desc())\
                                   .limit(5).all()

        # Quick stats
        stats = {
            "applications_sent": ProjectApplication.get_for_user(current_user_id).count(),
            "applications_pending": ProjectApplication.get_for_user(current_user_id, 'pending').count(),
            "applications_accepted": ProjectApplication.get_for_user(current_user_id, 'accepted').count(),
            "applications_received": ProjectApplication.get_pending_for_creator(current_user_id).count(),
            "projects_created": len(user_projects)
        }

        return jsonify({
            "success": True,
            "profile": profile.to_dict() if profile else None,
            "recent_applications": [app.to_dict(include_project=True, current_user_id=current_user_id) for app in recent_applications],
            "received_applications": [app.to_dict(include_user=True, include_project=True, current_user_id=current_user_id) for app in received_applications],
            "user_projects": [project.to_dict(current_user_id=current_user_id) for project in user_projects],
            "stats": stats
        }), 200

    except Exception as e:
        logger.error(f"Error getting dashboard data: {e}")
        return jsonify({"success": False, "message": "Failed to get dashboard data"}), 500