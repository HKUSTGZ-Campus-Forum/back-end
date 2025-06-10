from flask import Blueprint, request, jsonify
from app.models.course import Course
from app.models.tag import Tag, TagType
from app.models.post import Post
from app.models.user import User
from app.models.user_role import UserRole
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user
from sqlalchemy import func, desc, asc
from functools import wraps
import bleach

bp = Blueprint('course', __name__, url_prefix='/courses')

def admin_required(fn):
    """Decorator to ensure the user has admin privileges"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user or not current_user.role or current_user.role.name != UserRole.ADMIN:
            return jsonify({"error": "Admin privileges required"}), 403
        return fn(*args, **kwargs)
    return wrapper

@bp.route('', methods=['GET'])
def get_courses():
    """List all courses with optional filtering and sorting"""
    # Get filter parameters
    search_query = request.args.get('q', '')
    sort_by = request.args.get('sort_by', 'code')
    sort_order = request.args.get('sort_order', 'asc')
    
    # Start building the query
    query = Course.query.filter_by(is_deleted=False)
    
    # Apply search if provided
    if search_query:
        safe_query = "%" + search_query.replace('%', r'\%').replace('_', r'\_') + "%"
        query = query.filter(
            (Course.code.ilike(safe_query, escape='\\')) |
            (Course.name.ilike(safe_query, escape='\\')) |
            (Course.description.ilike(safe_query, escape='\\'))
        )
    
    # Apply sorting
    valid_sort_fields = {
        'code': Course.code,
        'name': Course.name,
        'created_at': Course.created_at,
    }
    
    if sort_by in valid_sort_fields:
        sort_field = valid_sort_fields[sort_by]
        if sort_order == 'desc':
            query = query.order_by(desc(sort_field))
        else:
            query = query.order_by(asc(sort_field))
    else:
        return jsonify({"error": f"Invalid sort_by field: {sort_by}"}), 400
    
    # Execute query and format response
    courses = query.all()
    return jsonify([course.to_dict() for course in courses]), 200

@bp.route('', methods=['POST'])
@jwt_required()
@admin_required
def create_course():
    """Create a new course (admin only)"""
    try:
        data = request.get_json() or {}
        
        # Validate required fields
        required_fields = ['code', 'name', 'instructor_id', 'credits']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"{field} is required"}), 400
        
        # Sanitize inputs
        code = bleach.clean(data['code']).strip().upper()
        name = bleach.clean(data['name']).strip()
        description = bleach.clean(data.get('description', ''))
        
        # Check if course code already exists
        if Course.query.filter_by(code=code, is_deleted=False).first():
            return jsonify({"error": "Course with this code already exists"}), 400
        
        # Create new course
        course = Course(
            code=code,
            name=name,
            description=description,
            instructor_id=data['instructor_id'],
            credits=data['credits'],
            capacity=data.get('capacity')
        )
        
        db.session.add(course)
        db.session.commit()
        
        return jsonify(course.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:course_id>', methods=['GET'])
def get_course(course_id):
    """Get a specific course by ID"""
    course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
    return jsonify(course.to_dict()), 200

@bp.route('/<int:course_id>', methods=['PUT'])
@jwt_required()
@admin_required
def update_course(course_id):
    """Update an existing course (admin only)"""
    try:
        course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
        data = request.get_json() or {}
        
        # Update fields if provided
        if 'code' in data:
            code = bleach.clean(data['code']).strip().upper()
            # Check if new code already exists
            existing_course = Course.query.filter(
                Course.code == code,
                Course.id != course_id,
                Course.is_deleted == False
            ).first()
            if existing_course:
                return jsonify({"error": "Course with this code already exists"}), 400
            course.code = code
            
        if 'name' in data:
            course.name = bleach.clean(data['name']).strip()
            
        if 'description' in data:
            course.description = bleach.clean(data['description'])
            
        if 'instructor_id' in data:
            course.instructor_id = data['instructor_id']
            
        if 'credits' in data:
            course.credits = data['credits']
            
        if 'capacity' in data:
            course.capacity = data['capacity']
            
        if 'is_active' in data:
            course.is_active = data['is_active']
        
        db.session.commit()
        return jsonify(course.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:course_id>', methods=['DELETE'])
@jwt_required()
@admin_required
def delete_course(course_id):
    """Soft delete a course (admin only)"""
    try:
        course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
        course.is_deleted = True
        course.deleted_at = func.now()
        db.session.commit()
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:course_id>/semester/<semester>', methods=['POST'])
@jwt_required()
@admin_required
def add_semester(course_id, semester):
    """Add a new semester tag for a course (admin only)"""
    try:
        course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
        tag = course.create_semester_tag(semester)
        return jsonify(tag.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:course_id>/posts', methods=['GET'])
def get_course_posts(course_id):
    """Get all posts for a course"""
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    semester = request.args.get('semester')
    
    # Build tag filter
    course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
    tag_filter = f"{course.code}-{semester}" if semester else f"{course.code}-%"
    
    # Query posts with this course tag
    posts = Post.query.join(
        Post.tags
    ).filter(
        Tag.name.like(tag_filter),
        Tag.tag_type.has(name=TagType.COURSE),
        Post.is_deleted == False
    ).paginate(page=page, per_page=limit, error_out=False)
    
    # Format response
    response = {
        "posts": [post.to_dict(include_tags=True) for post in posts.items],
        "total_count": posts.total,
        "total_pages": posts.pages,
        "current_page": page,
        "course": course.to_dict()
    }
    
    return jsonify(response), 200 