from flask import Blueprint, request, jsonify
from app.models.course import Course
from app.models.tag import Tag, TagType
from app.models.post import Post
from app.models.user import User
from app.models.user_role import UserRole
from app.extensions import db
from app.utils.semester import (
    parse_semester_tag, format_semester_tag, get_semester_display_name,
    format_academic_year_semester_display,
    normalize_semester_code, sort_semesters, is_valid_semester_format,
    find_matching_semester_tag, format_offering_display_tag,
    normalize_offering_identifier, is_offering_not_newer
)
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user
from sqlalchemy import func, desc, asc
from functools import wraps
import bleach

bp = Blueprint('course', __name__, url_prefix='/courses')
COURSE_REVIEW_TAG = "course-review"

def admin_required(fn):
    """Decorator to ensure the user has admin privileges"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user or not current_user.role or current_user.role.name != UserRole.ADMIN:
            return jsonify({"error": "Admin privileges required"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _build_semester_info(year, semester_code, language='zh'):
    return {
        "code": f"{year}{semester_code}",
        "display_name": format_academic_year_semester_display(year, semester_code, language),
        "year": year,
        "season": semester_code,
        "season_display": get_semester_display_name(semester_code, language),
        "offering_tag": format_offering_display_tag(year, semester_code),
    }


def _get_course_semester_entries(course, language='zh'):
    course_tags = Course.get_course_tags(code=course.code)
    semester_entries = {}

    for tag in course_tags:
        parsed = parse_semester_tag(tag.name)
        if not parsed:
            continue

        course_code, year, semester_code = parsed
        if course_code != course.code:
            continue

        key = f"{year}{semester_code}"
        if key not in semester_entries:
            semester_entries[key] = _build_semester_info(year, semester_code, language)

    sorted_codes = sort_semesters(list(semester_entries.keys()))
    return [semester_entries[code] for code in sorted_codes if code in semester_entries]


def _get_visible_offering_tags(course, current_offering_tag):
    normalized_current = normalize_offering_identifier(current_offering_tag)
    if not normalized_current:
        return []

    semester_entries = _get_course_semester_entries(course, language='zh')
    if not any(
        entry["year"] == normalized_current[0] and entry["season"] == normalized_current[1]
        for entry in semester_entries
    ):
        return []

    visible_offering_tags = []
    for entry in semester_entries:
        candidate = (entry["year"], entry["season"])
        if is_offering_not_newer(candidate, normalized_current):
            visible_offering_tags.append(entry["offering_tag"])

    return visible_offering_tags

@bp.route('/filters', methods=['GET'])
def get_course_filters():
    """Get available filter options for courses"""
    try:
        # Get distinct semesters from course data (based on term field)
        # Assuming terms like "2430", "2440", "2410" represent different semesters
        courses = Course.query.filter_by(is_deleted=False).all()
        
        # Extract semester/term data from courses
        semester_data = {}
        course_types = set()
        
        for course in courses:
            # Extract course type from course code (prefix before space or number)
            code_parts = course.code.split()
            if code_parts:
                course_type = code_parts[0]  # e.g., "BSBE", "AIAA"
                course_types.add(course_type)
        
        # Get semesters from existing course tags
        course_tags = Course.get_course_tags()
        
        for tag in course_tags:
            parsed = parse_semester_tag(tag.name)
            if parsed:
                _, year, semester_code = parsed
                semester_key = f"{year}{semester_code}"
                if semester_key not in semester_data:
                    semester_data[semester_key] = _build_semester_info(year, semester_code, 'zh')
        
        # Sort semesters (latest first)
        sorted_semester_codes = sort_semesters(list(semester_data.keys()))
        sorted_semesters = [
            semester_data[semester_code]
            for semester_code in sorted_semester_codes
            if semester_code in semester_data
        ]
        
        # Format course types
        formatted_course_types = [
            {"code": ct, "name": ct} for ct in sorted(course_types)
        ]
        
        return jsonify({
            "semesters": sorted_semesters,
            "course_types": formatted_course_types
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('', methods=['GET'])
def get_courses():
    """List all courses with optional filtering and sorting"""
    # Get filter parameters
    search_query = request.args.get('q', '')
    sort_by = request.args.get('sort_by', 'code')
    sort_order = request.args.get('sort_order', 'asc')
    semester_filter = request.args.get('semester', '')
    course_type_filter = request.args.get('course_type', '')
    
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
    
    # Apply course type filter if provided
    if course_type_filter:
        query = query.filter(Course.code.like(f"{course_type_filter}%"))
    
    # Apply semester filter if provided
    if semester_filter:
        normalized_filter = normalize_offering_identifier(semester_filter)
        normalized_filter_code = (
            f"{normalized_filter[0]}{normalized_filter[1]}"
            if normalized_filter else semester_filter
        )

        # Get all course codes that have tags for this semester
        course_tags = Course.get_course_tags()
        valid_course_codes = []
        
        for tag in course_tags:
            parsed = parse_semester_tag(tag.name)
            if parsed:
                course_code, year, semester_code = parsed
                semester_key = f"{year}{semester_code}"
                if semester_key == normalized_filter_code:
                    valid_course_codes.append(course_code)
        
        if valid_course_codes:
            query = query.filter(Course.code.in_(valid_course_codes))
        else:
            # If no courses found for this semester, return empty results
            return jsonify([]), 200
    
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
    
    # Query posts with this course tag
    if semester:
        # Specific semester requested - look for exact match
        tag_filter = f"{course.code}-{semester}"
        posts = Post.query.join(
            Post.tags
        ).filter(
            Tag.name == tag_filter,
            Tag.tag_type.has(TagType.sql_course_type_name_match()),
            Post.is_deleted == False
        ).paginate(page=page, per_page=limit, error_out=False)
    else:
        # No semester specified - get all posts for this course
        # Include both: plain course code tags AND course-semester tags
        posts = Post.query.join(
            Post.tags
        ).filter(
            db.or_(
                Tag.name == course.code,  # Posts without semester
                Tag.name.like(f"{course.code}-%")  # Posts with any semester
            ),
            Tag.tag_type.has(TagType.sql_course_type_name_match()),
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


@bp.route('/<int:course_id>/discussions', methods=['GET'])
def get_course_discussions(course_id):
    """Get discussion posts for a course up to and including a given offering."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    offering = (request.args.get('offering') or '').strip()

    if not offering:
        return jsonify({"error": "offering is required"}), 400

    course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
    visible_offering_tags = _get_visible_offering_tags(course, offering)
    if not visible_offering_tags:
        return jsonify({
            "error": f"Course {course.code} was not offered in {offering}"
        }), 400

    query = Post.query.filter(Post.is_deleted == False)
    query = query.filter(Post.tags.any(Tag.name == course.code))
    query = query.filter(Post.tags.any(Tag.name.in_(visible_offering_tags)))
    query = query.filter(~Post.tags.any(Tag.name == COURSE_REVIEW_TAG))
    query = query.order_by(desc(Post.created_at))

    paginated_posts = query.paginate(page=page, per_page=limit, error_out=False)

    return jsonify({
        "posts": [post.to_dict(include_tags=True, include_author=True) for post in paginated_posts.items],
        "total_count": paginated_posts.total,
        "total_pages": paginated_posts.pages,
        "current_page": page,
        "visible_offering_tags": visible_offering_tags,
        "course": course.to_dict(),
    }), 200

@bp.route('/<int:course_id>/semesters', methods=['GET'])
def get_course_semesters(course_id):
    """Get all available semesters for a course based on existing tags"""
    course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
    language = request.args.get('lang', 'zh')  # Default to Chinese
    sorted_semester_data = _get_course_semester_entries(course, language)
    
    return jsonify({
        "course_id": course_id,
        "course_code": course.code,
        "course_name": course.name,
        "semesters": sorted_semester_data,
        "language": language
    }), 200

@bp.route('/<int:course_id>/semesters/validate', methods=['POST'])
@jwt_required()
def validate_course_semester(course_id):
    """Validate if a course-semester combination exists and can be commented on"""
    data = request.get_json() or {}
    semester_input = data.get('semester', '').strip()
    
    if not semester_input:
        return jsonify({"error": "Semester is required"}), 400
    
    course = Course.query.filter_by(id=course_id, is_deleted=False).first_or_404()
    
    normalized_offering = normalize_offering_identifier(semester_input)
    if normalized_offering:
        year, semester_code = normalized_offering
    else:
        # Parse the semester input to extract year and season
        # Handle various formats: "2024fall", "2024秋", "24Fall", etc.
        parsed = parse_semester_tag(f"{course.code}-{semester_input}")
        if not parsed:
            return jsonify({
                "valid": False,
                "error": "Invalid semester format"
            }), 400
        _, year, semester_code = parsed

    if not year or not semester_code:
        return jsonify({
            "valid": False,
            "error": "Invalid semester format"
        }), 400
    
    # Check if this specific course-semester combination exists in tags
    all_course_tags = Course.get_course_tags(code=course.code)
    matching_tag = find_matching_semester_tag(course.code, year, semester_code, all_course_tags)
    
    if not matching_tag:
        available_semesters = [tag.name.split('-', 1)[1] for tag in all_course_tags if '-' in tag.name]
        suggested_offerings = [
            format_offering_display_tag(*normalized[:2])
            for normalized in [
                parse_semester_tag(tag.name)[1:]
                for tag in all_course_tags
                if parse_semester_tag(tag.name)
            ]
        ]
        return jsonify({
            "valid": False,
            "error": (
                f"Course {course.code} was not offered in "
                f"{format_academic_year_semester_display(year, semester_code, 'zh')}"
            ),
            "suggested_semesters": available_semesters,
            "suggested_offerings": list(dict.fromkeys(suggested_offerings)),
        }), 400
    
    return jsonify({
        "valid": True,
        "normalized_semester": f"{year}{semester_code}",
        "display_name": format_academic_year_semester_display(year, semester_code, 'zh'),
        "offering_tag": format_offering_display_tag(year, semester_code),
        "tag_name": matching_tag.name,
        "matched_tag_id": matching_tag.id
    }), 200
