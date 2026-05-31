from flask import Blueprint, request, jsonify
from app.models.course import Course
from app.models.scheduler_section import SchedulerSection
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.scheduler_cart import SchedulerUserCourseCart, SchedulerUserBundleCart
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, or_

bp = Blueprint('scheduler', __name__, url_prefix='/scheduler')

SEMESTER_META = {
    '2430': {'name': '2024-25 Spring', 'name_zh': '24-25春'},
    '2440': {'name': '2024-25 Summer', 'name_zh': '24-25夏'},
    '2510': {'name': '2025-26 Fall', 'name_zh': '25-26秋'},
    '2530': {'name': '2025-26 Spring', 'name_zh': '25-26春'},
}


# --- Semester & Course Search ---

@bp.route('/semesters', methods=['GET'])
def list_semesters():
    """List semesters that have section data."""
    rows = (
        db.session.query(SchedulerSection.semester_id, func.count())
        .group_by(SchedulerSection.semester_id)
        .all()
    )
    result = []
    for sid, count in rows:
        meta = SEMESTER_META.get(sid, {})
        result.append({
            'id': sid,
            'name': meta.get('name', sid),
            'name_zh': meta.get('name_zh', sid),
            'section_count': count,
        })
    result.sort(key=lambda x: x['id'], reverse=True)
    return jsonify(result)


@bp.route('/courses/search', methods=['GET'])
def search_courses():
    """Search courses by query string, optionally filtered by semester."""
    query = request.args.get('query', '').strip()
    semester = request.args.get('semester', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    page_size = min(50, max(1, request.args.get('pageSize', 8, type=int)))

    q = Course.query.filter(Course.is_deleted == False)

    if query:
        like = f'%{query}%'
        q = q.filter(or_(Course.code.ilike(like), Course.name.ilike(like)))

    if semester:
        course_ids = (
            db.session.query(SchedulerSection.course_id)
            .filter(SchedulerSection.semester_id == semester)
            .distinct()
            .scalar_subquery()
        )
        q = q.filter(Course.id.in_(course_ids))

    total = q.count()
    items = q.order_by(Course.code).offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': [{
            'course_code': c.code,
            'course_title': c.name,
            'credit': c.credits,
            'subject': c.subject,
        } for c in items],
    })


@bp.route('/courses/<code>', methods=['GET'])
def get_course_detail(code):
    """Get course detail with sections and lectures for a semester."""
    semester = request.args.get('semester', '').strip()
    course = Course.query.filter_by(code=code.upper(), is_deleted=False).first()
    if not course:
        return jsonify({'error': 'Course not found'}), 404

    sections_query = SchedulerSection.query.filter_by(course_id=course.id)
    if semester:
        sections_query = sections_query.filter_by(semester_id=semester)
    sections = sections_query.all()

    section_data = []
    for s in sections:
        lectures = SchedulerLecture.query.filter_by(
            semester_id=s.semester_id, section_id=s.section_id
        ).all()
        section_data.append({
            'semester_id': s.semester_id,
            'section_id': s.section_id,
            'name': s.name,
            'bundle': s.bundle,
            'layer': s.layer,
            'quota': s.quota,
            'section_type': s.section_type,
            'is_main': s.is_main,
            'lectures': [{
                'day': l.day,
                'start_time': l.start_time,
                'end_time': l.end_time,
                'room': l.room,
                'instructor': l.instructor,
            } for l in lectures],
        })

    return jsonify({
        'course_code': course.code,
        'course_title': course.name,
        'course_title_abbr': course.course_title_abbr,
        'credit': course.credits,
        'subject': course.subject,
        'catalog_number': course.catalog_number,
        'course_desc': course.description,
        'pre_requirement': course.pre_requirement,
        'co_requirement': course.co_requirement,
        'exclusion': course.exclusion,
        'pg_course': course.pg_course,
        'klms_course': course.klms_course,
        'sections': section_data,
    })


# --- Cart CRUD ---

def _serialize_cart_item(cart_item):
    """Serialize a cart item with course details and bundles."""
    course = Course.query.filter_by(code=cart_item.course_code).first()
    if not course:
        return None

    sections = SchedulerSection.query.filter_by(
        course_id=course.id, semester_id=cart_item.semester_id
    ).all()

    bundles = SchedulerUserBundleCart.query.filter_by(
        user_id=cart_item.user_id,
        semester_id=cart_item.semester_id,
        course_code=cart_item.course_code,
    ).all()
    bundle_map = {(b.id, b.layer): b.enabled for b in bundles}

    section_groups = {}
    for s in sections:
        key = (s.bundle, s.layer)
        if key not in section_groups:
            section_groups[key] = {
                'id': s.bundle,
                'layer': s.layer,
                'enabled': bundle_map.get(key, True),
                'sections': [],
            }
        lectures = SchedulerLecture.query.filter_by(
            semester_id=s.semester_id, section_id=s.section_id
        ).all()
        section_groups[key]['sections'].append({
            'section_id': s.section_id,
            'name': s.name,
            'section_type': s.section_type,
            'is_main': s.is_main,
            'quota': s.quota,
            'lectures': [{
                'day': l.day,
                'start_time': l.start_time,
                'end_time': l.end_time,
                'room': l.room,
                'instructor': l.instructor,
            } for l in lectures],
        })

    # Group bundles by layer
    layers = {}
    for bg in section_groups.values():
        layer = bg['layer']
        if layer not in layers:
            layers[layer] = []
        layers[layer].append(bg)

    return {
        'course_code': course.code,
        'course_title': course.name,
        'credit': course.credits,
        'subject': course.subject,
        'pg_course': course.pg_course,
        'klms_course': course.klms_course,
        'enabled': cart_item.enabled,
        'layers': layers,
    }


@bp.route('/cart/<semester>', methods=['GET'])
@jwt_required()
def get_cart(semester):
    """Get user's cart for a semester."""
    user_id = int(get_jwt_identity())
    items = SchedulerUserCourseCart.query.filter_by(user_id=user_id, semester_id=semester).all()
    result = []
    for item in items:
        serialized = _serialize_cart_item(item)
        if serialized:
            result.append(serialized)
    result.sort(key=lambda x: x['course_code'])
    return jsonify(result)


@bp.route('/cart/<semester>/add', methods=['POST'])
@jwt_required()
def add_to_cart(semester):
    """Add a course to the cart."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    course_code = data.get('course_code', '').strip().upper()

    course = Course.query.filter_by(code=course_code, is_deleted=False).first()
    if not course:
        return jsonify({'error': 'Course not found'}), 404

    existing = SchedulerUserCourseCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=course_code
    ).first()
    if existing:
        return jsonify({'error': 'Course already in cart'}), 409

    cart = SchedulerUserCourseCart(
        user_id=user_id,
        semester_id=semester,
        course_code=course_code,
        enabled=False,
    )
    db.session.add(cart)

    # Auto-create bundle entries for all sections
    sections = SchedulerSection.query.filter_by(course_id=course.id, semester_id=semester).all()
    seen = set()
    for s in sections:
        key = (s.bundle, s.layer)
        if key not in seen:
            seen.add(key)
            bundle = SchedulerUserBundleCart(
                user_id=user_id,
                semester_id=semester,
                course_code=course_code,
                id=s.bundle,
                layer=s.layer,
                enabled=True,
            )
            db.session.add(bundle)

    db.session.commit()

    cart = SchedulerUserCourseCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=course_code
    ).first()
    return jsonify(_serialize_cart_item(cart))


@bp.route('/cart/<semester>/remove/<code>', methods=['DELETE'])
@jwt_required()
def remove_from_cart(semester, code):
    """Remove a course from the cart."""
    user_id = int(get_jwt_identity())
    cart = SchedulerUserCourseCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=code.upper()
    ).first()
    if not cart:
        return jsonify({'error': 'Not in cart'}), 404
    db.session.delete(cart)
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/cart/<semester>/course/<code>/toggle', methods=['PUT'])
@jwt_required()
def toggle_course_enabled(semester, code):
    """Toggle course enabled state."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    cart = SchedulerUserCourseCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=code.upper()
    ).first()
    if not cart:
        return jsonify({'error': 'Not in cart'}), 404
    cart.enabled = data.get('enabled', not cart.enabled)
    db.session.commit()
    return jsonify({'course_code': cart.course_code, 'enabled': cart.enabled})


@bp.route('/cart/<semester>/bundle/<code>/<int:bundle_id>/<int:layer>/toggle', methods=['PUT'])
@jwt_required()
def toggle_bundle_enabled(semester, code, bundle_id, layer):
    """Toggle bundle enabled state."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    bundle = SchedulerUserBundleCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=code.upper(),
        id=bundle_id, layer=layer
    ).first()
    if not bundle:
        return jsonify({'error': 'Bundle not found'}), 404
    bundle.enabled = data.get('enabled', not bundle.enabled)
    db.session.commit()
    return jsonify({'id': bundle.id, 'layer': bundle.layer, 'enabled': bundle.enabled})


@bp.route('/cart/<semester>/layer/<code>/<int:layer>/toggle', methods=['PUT'])
@jwt_required()
def toggle_layer_enabled(semester, code, layer):
    """Toggle all bundles in a layer for a course."""
    user_id = int(get_jwt_identity())
    data = request.get_json()
    bundles = SchedulerUserBundleCart.query.filter_by(
        user_id=user_id, semester_id=semester, course_code=code.upper(), layer=layer
    ).all()
    if not bundles:
        return jsonify({'error': 'No bundles found'}), 404
    new_state = data.get('enabled', not bundles[0].enabled)
    for b in bundles:
        b.enabled = new_state
    db.session.commit()
    return jsonify({'ok': True, 'enabled': new_state, 'count': len(bundles)})


# --- Map Data ---

@bp.route('/map/components', methods=['GET'])
def get_map_components():
    """Get all map components."""
    components = SchedulerMapComponent.query.all()
    return jsonify([{
        'id': c.id,
        'node_type': c.node_type,
        'x_coordinate': c.x_coordinate,
        'y_coordinate': c.y_coordinate,
        'category': c.category,
    } for c in components])


@bp.route('/map/lines', methods=['GET'])
def get_map_lines():
    """Get all map lines."""
    lines = SchedulerMapLine.query.all()
    return jsonify([{
        'id': l.id,
        'start_id': l.start_id,
        'end_id': l.end_id,
        'line_type': l.line_type,
        'x_coordinate': l.x_coordinate,
        'category': l.category,
    } for l in lines])


@bp.route('/map/courses', methods=['GET'])
def get_map_courses():
    """Get all courses with fields needed for the map."""
    courses = Course.query.filter(
        Course.is_deleted == False,
        Course.course_title_abbr.isnot(None)
    ).all()
    return jsonify([{
        'course_code': c.code,
        'course_title_abbr': c.course_title_abbr,
        'subject': c.subject,
    } for c in courses])
