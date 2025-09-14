from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity, jwt_required, get_jwt
from app.models.project import Project
from app.models.project_application import ProjectApplication
from app.models.user_profile import UserProfile
from app.extensions import db
from app.services.matching_service import matching_service
import logging

logger = logging.getLogger(__name__)

project_bp = Blueprint('project', __name__, url_prefix='/projects')

@project_bp.route('/', methods=['GET'])
def get_projects():
    """Get projects (public endpoint)"""
    try:
        # Query parameters
        status = request.args.get('status', 'recruiting')
        project_type = request.args.get('type', '').strip()
        difficulty = request.args.get('difficulty', '').strip()
        query = request.args.get('q', '').strip()
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 20)), 50)

        # Build query
        projects_query = Project.get_active_projects()

        # Filter by status
        if status:
            projects_query = projects_query.filter(Project.status == status)

        # Filter by type
        if project_type:
            projects_query = projects_query.filter(Project.project_type == project_type)

        # Filter by difficulty
        if difficulty and difficulty in ['beginner', 'intermediate', 'advanced']:
            projects_query = projects_query.filter(Project.difficulty_level == difficulty)

        # Text search
        if query:
            projects_query = projects_query.filter(
                db.or_(
                    Project.title.ilike(f'%{query}%'),
                    Project.description.ilike(f'%{query}%')
                )
            )

        # Order by created date (newest first)
        projects_query = projects_query.order_by(Project.created_at.desc())

        # Pagination
        offset = (page - 1) * limit
        projects = projects_query.offset(offset).limit(limit).all()
        total = projects_query.count()

        # Get current user ID if authenticated
        current_user_id = None
        try:
            current_user_id = get_jwt_identity()
        except:
            pass

        return jsonify({
            "success": True,
            "projects": [project.to_dict(include_creator=True, current_user_id=current_user_id) for project in projects],
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        return jsonify({"success": False, "message": "Failed to get projects"}), 500

@project_bp.route('/', methods=['POST'])
@jwt_required()
def create_project():
    """Create a new project"""
    try:
        current_user_id = get_jwt_identity()
        data = request.get_json()

        if not data or not data.get('title') or not data.get('description'):
            return jsonify({"success": False, "message": "Title and description are required"}), 400

        # Create project
        project = Project(
            user_id=current_user_id,
            title=data['title'].strip(),
            description=data['description'].strip(),
            goal=data.get('goal', '').strip() if data.get('goal') else None,
            required_skills=data.get('required_skills', []) if isinstance(data.get('required_skills'), list) else [],
            preferred_skills=data.get('preferred_skills', []) if isinstance(data.get('preferred_skills'), list) else [],
            project_type=data.get('project_type', '').strip() if data.get('project_type') else None,
            difficulty_level=data.get('difficulty_level', 'intermediate'),
            duration_estimate=data.get('duration_estimate', '').strip() if data.get('duration_estimate') else None,
            team_size_min=max(1, int(data.get('team_size_min', 1))),
            team_size_max=max(1, int(data.get('team_size_max', 5))),
            looking_for_roles=data.get('looking_for_roles', []) if isinstance(data.get('looking_for_roles'), list) else [],
            collaboration_method=data.get('collaboration_method', '').strip() if data.get('collaboration_method') else None,
            meeting_frequency=data.get('meeting_frequency', '').strip() if data.get('meeting_frequency') else None,
            communication_tools=data.get('communication_tools', []) if isinstance(data.get('communication_tools'), list) else []
        )

        # Validate difficulty level
        if project.difficulty_level not in ['beginner', 'intermediate', 'advanced']:
            project.difficulty_level = 'intermediate'

        # Validate team size
        if project.team_size_min > project.team_size_max:
            project.team_size_min = project.team_size_max

        db.session.add(project)
        db.session.flush()  # Flush to get the ID assigned

        # Update embedding before committing
        embedding_success = matching_service.update_project_embedding(project.id)
        if not embedding_success:
            logger.warning(f"Failed to update embedding for project {project.id}")

        # Commit everything together
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Project created successfully",
            "project": project.to_dict(include_creator=True, current_user_id=current_user_id),
            "embedding_updated": embedding_success
        }), 201

    except Exception as e:
        logger.error(f"Error creating project: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to create project"}), 500

@project_bp.route('/<int:project_id>', methods=['GET'])
def get_project(project_id):
    """Get a specific project (public endpoint)"""
    try:
        project = Project.query.get(project_id)
        if not project or project.is_deleted:
            return jsonify({"success": False, "message": "Project not found"}), 404

        # Increment view count
        project.increment_view()
        db.session.commit()

        # Get current user ID if authenticated
        current_user_id = None
        try:
            current_user_id = get_jwt_identity()
        except:
            pass

        # Include applications if user is project creator
        include_applications = current_user_id == project.user_id

        return jsonify({
            "success": True,
            "project": project.to_dict(
                include_creator=True,
                include_applications=include_applications,
                current_user_id=current_user_id
            )
        }), 200

    except Exception as e:
        logger.error(f"Error getting project {project_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get project"}), 500

@project_bp.route('/<int:project_id>', methods=['PUT'])
@jwt_required()
def update_project(project_id):
    """Update a project"""
    try:
        current_user_id = get_jwt_identity()
        project = Project.query.get(project_id)

        if not project or project.is_deleted:
            return jsonify({"success": False, "message": "Project not found"}), 404

        if project.user_id != current_user_id:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        # Update fields
        if 'title' in data and data['title'].strip():
            project.title = data['title'].strip()
        if 'description' in data and data['description'].strip():
            project.description = data['description'].strip()
        if 'goal' in data:
            project.goal = data['goal'].strip() if data['goal'] else None
        if 'required_skills' in data:
            project.required_skills = data['required_skills'] if isinstance(data['required_skills'], list) else []
        if 'preferred_skills' in data:
            project.preferred_skills = data['preferred_skills'] if isinstance(data['preferred_skills'], list) else []
        if 'project_type' in data:
            project.project_type = data['project_type'].strip() if data['project_type'] else None
        if 'difficulty_level' in data:
            if data['difficulty_level'] in ['beginner', 'intermediate', 'advanced']:
                project.difficulty_level = data['difficulty_level']
        if 'duration_estimate' in data:
            project.duration_estimate = data['duration_estimate'].strip() if data['duration_estimate'] else None
        if 'team_size_min' in data:
            project.team_size_min = max(1, int(data['team_size_min']))
        if 'team_size_max' in data:
            project.team_size_max = max(1, int(data['team_size_max']))
        if 'looking_for_roles' in data:
            project.looking_for_roles = data['looking_for_roles'] if isinstance(data['looking_for_roles'], list) else []
        if 'status' in data:
            if data['status'] in [Project.STATUS_RECRUITING, Project.STATUS_ACTIVE, Project.STATUS_COMPLETED, Project.STATUS_CANCELLED]:
                project.status = data['status']
        if 'collaboration_method' in data:
            project.collaboration_method = data['collaboration_method'].strip() if data['collaboration_method'] else None
        if 'meeting_frequency' in data:
            project.meeting_frequency = data['meeting_frequency'].strip() if data['meeting_frequency'] else None
        if 'communication_tools' in data:
            project.communication_tools = data['communication_tools'] if isinstance(data['communication_tools'], list) else []

        # Validate team size
        if project.team_size_min > project.team_size_max:
            project.team_size_min = project.team_size_max

        db.session.commit()

        # Update embedding
        matching_service.update_project_embedding(project.id)

        return jsonify({
            "success": True,
            "message": "Project updated successfully",
            "project": project.to_dict(include_creator=True, current_user_id=current_user_id)
        }), 200

    except Exception as e:
        logger.error(f"Error updating project {project_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to update project"}), 500

@project_bp.route('/<int:project_id>', methods=['DELETE'])
@jwt_required()
def delete_project(project_id):
    """Delete a project (soft delete)"""
    try:
        current_user_id = get_jwt_identity()
        project = Project.query.get(project_id)

        if not project or project.is_deleted:
            return jsonify({"success": False, "message": "Project not found"}), 404

        if project.user_id != current_user_id:
            return jsonify({"success": False, "message": "Permission denied"}), 403

        # Soft delete
        from datetime import datetime, timezone
        project.is_deleted = True
        project.deleted_at = datetime.now(timezone.utc)

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Project deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {e}")
        db.session.rollback()
        return jsonify({"success": False, "message": "Failed to delete project"}), 500

@project_bp.route('/<int:project_id>/match', methods=['GET'])
@jwt_required()
def get_project_matches(project_id):
    """Get teammate matches for a project"""
    try:
        current_user_id = get_jwt_identity()
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
                "status": project.status
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting matches for project {project_id}: {e}")
        return jsonify({"success": False, "message": "Failed to get matches"}), 500

@project_bp.route('/my', methods=['GET'])
@jwt_required()
def get_my_projects():
    """Get current user's projects"""
    try:
        current_user_id = get_jwt_identity()
        projects = Project.query.filter_by(user_id=current_user_id, is_deleted=False)\
                                .order_by(Project.created_at.desc()).all()

        return jsonify({
            "success": True,
            "projects": [project.to_dict(include_creator=True, include_applications=True, current_user_id=current_user_id) for project in projects]
        }), 200

    except Exception as e:
        logger.error(f"Error getting user projects: {e}")
        return jsonify({"success": False, "message": "Failed to get projects"}), 500

@project_bp.route('/stats', methods=['GET'])
def get_project_stats():
    """Get project statistics (public endpoint)"""
    try:
        total_projects = Project.query.filter_by(is_deleted=False).count()
        recruiting_projects = Project.get_recruiting_projects().count()

        # Project type distribution
        type_stats = {}
        types = db.session.query(Project.project_type).filter_by(is_deleted=False).distinct().all()
        for (project_type,) in types:
            if project_type:
                count = Project.query.filter_by(project_type=project_type, is_deleted=False).count()
                type_stats[project_type] = count

        # Difficulty distribution
        difficulty_stats = {}
        for level in ['beginner', 'intermediate', 'advanced']:
            count = Project.query.filter_by(difficulty_level=level, is_deleted=False).count()
            difficulty_stats[level] = count

        return jsonify({
            "success": True,
            "stats": {
                "total_projects": total_projects,
                "recruiting_projects": recruiting_projects,
                "type_distribution": type_stats,
                "difficulty_distribution": difficulty_stats
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting project stats: {e}")
        return jsonify({"success": False, "message": "Failed to get stats"}), 500