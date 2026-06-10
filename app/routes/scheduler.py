from flask import Blueprint, request, jsonify
from app.models.course import Course
from app.models.course_domain import (
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, or_
from app.services.course_domain import current_catalog_version, find_offering, normalize_course_code

bp = Blueprint('scheduler', __name__, url_prefix='/scheduler')

SEMESTER_META = {
    '2430': {'name': '2024-25 Spring', 'name_zh': '24-25春'},
    '2440': {'name': '2024-25 Summer', 'name_zh': '24-25夏'},
    '2510': {'name': '2025-26 Fall', 'name_zh': '25-26秋'},
    '2530': {'name': '2025-26 Spring', 'name_zh': '25-26春'},
    '2540': {'name': '2025-26 Summer', 'name_zh': '25-26夏'},
}


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({'error': 'Invalid JSON body'}), 400)
    return data, None


def _find_course_by_code(code):
    normalized = normalize_course_code(code)
    if not normalized:
        return None

    normalized_match = Course.query.filter(
        Course.normalized_code == normalized,
        Course.is_deleted == False,
    ).first()
    if normalized_match:
        return normalized_match

    candidates = Course.query.filter(
        func.upper(func.replace(Course.code, " ", "")) == normalized,
        Course.is_deleted == False,
    ).all()
    if not candidates:
        return None

    def rank(course):
        domain_sections = (
            db.session.query(CourseSection.id)
            .join(CourseOffering)
            .filter(CourseOffering.course_id == course.id)
            .first()
            is not None
        )
        return (
            normalize_course_code(course.code) == normalized,
            domain_sections,
            course.updated_at or course.created_at,
            course.id,
        )

    return max(candidates, key=rank)


def _course_title(course):
    version = current_catalog_version(course)
    return (version.title if version else None) or course.canonical_title or course.name


def _course_credit(course):
    version = current_catalog_version(course)
    return (version.credits if version else None) or course.credits


def _course_title_abbr(course):
    version = current_catalog_version(course)
    return (version.title_abbr if version else None) or course.course_title_abbr


def _course_requirement(course, field_name):
    version = current_catalog_version(course)
    version_field = {
        "pre_requirement": "pre_requirement_raw",
        "co_requirement": "co_requirement_raw",
        "exclusion": "exclusion_raw",
    }.get(field_name)
    if version and version_field:
        value = getattr(version, version_field, None)
        if value is not None:
            return value
    return getattr(course, field_name)


def _course_flag(course, field_name):
    version = current_catalog_version(course)
    if version:
        return getattr(version, field_name)
    return getattr(course, field_name)


def _domain_sections_for_offering(offering):
    if not offering:
        return []
    return (
        CourseSection.query
        .filter_by(offering_id=offering.id)
        .order_by(CourseSection.layer, CourseSection.bundle, CourseSection.source_section_id)
        .all()
    )


def _meetings_for_section(section_id):
    return (
        CourseMeeting.query
        .filter_by(section_id=section_id)
        .order_by(CourseMeeting.day, CourseMeeting.start_time, CourseMeeting.end_time, CourseMeeting.id)
        .all()
    )


# --- Semester & Course Search ---

@bp.route('/semesters', methods=['GET'])
def list_semesters():
    """List semesters that have section data."""
    domain_rows = (
        db.session.query(CourseOffering.semester_id, func.count(CourseSection.id))
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .group_by(CourseOffering.semester_id)
        .all()
    )
    counts = {sid: count for sid, count in domain_rows}

    result = []
    for sid, count in counts.items():
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
        normalized_like = f'%{normalize_course_code(query)}%'
        q = q.filter(or_(
            Course.code.ilike(like),
            Course.normalized_code.ilike(normalized_like),
            Course.display_code.ilike(like),
            Course.name.ilike(like),
            Course.canonical_title.ilike(like),
        ))

    if semester:
        course_ids = [
            course_id for (course_id,) in (
                db.session.query(CourseOffering.course_id)
                .filter(CourseOffering.semester_id == semester)
                .distinct()
                .all()
            )
        ]
        if course_ids:
            q = q.filter(Course.id.in_(course_ids))
        else:
            q = q.filter(False)

    total = q.count()
    items = q.order_by(Course.code).offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': [{
            'course_code': c.code,
            'course_title': _course_title(c),
            'credit': _course_credit(c),
            'subject': c.subject,
        } for c in items],
    })


@bp.route('/subjects', methods=['GET'])
def list_subjects():
    """List course subjects that have course-domain sections in a semester."""
    semester = request.args.get('semester', '').strip()
    q = (
        db.session.query(
            func.upper(Course.subject).label('subject'),
            func.count(func.distinct(Course.id)).label('course_count'),
        )
        .join(CourseOffering, CourseOffering.course_id == Course.id)
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .filter(Course.is_deleted == False)
        .filter(Course.subject.isnot(None))
        .filter(func.trim(Course.subject) != '')
    )
    if semester:
        q = q.filter(CourseOffering.semester_id == semester)

    rows = q.group_by(func.upper(Course.subject)).order_by(func.upper(Course.subject)).all()
    return jsonify([
        {'subject': subject, 'course_count': course_count}
        for subject, course_count in rows
    ])


@bp.route('/courses/<code>', methods=['GET'])
def get_course_detail(code):
    """Get course detail with sections and lectures for a semester."""
    semester = request.args.get('semester', '').strip()
    course = _find_course_by_code(code)
    if not course:
        return jsonify({'error': 'Course not found'}), 404

    domain_offerings = CourseOffering.query.filter_by(course_id=course.id)
    if semester:
        domain_offerings = domain_offerings.filter_by(semester_id=semester)
    domain_offerings = domain_offerings.order_by(CourseOffering.semester_id, CourseOffering.id).all()
    domain_sections = []
    for offering in domain_offerings:
        domain_sections.extend(_domain_sections_for_offering(offering))

    if domain_sections:
        section_data = []
        for s in domain_sections:
            meetings = _meetings_for_section(s.id)
            section_data.append({
                'semester_id': s.offering.semester_id,
                'section_id': s.source_section_id,
                'name': s.name,
                'bundle': s.bundle,
                'layer': s.layer,
                'quota': s.quota,
                'section_type': s.section_type,
                'is_main': s.is_main,
                'lectures': [{
                    'day': m.day,
                    'start_time': m.start_time,
                    'end_time': m.end_time,
                    'room': m.room,
                    'instructor': m.instructor_text,
                } for m in meetings],
            })

        return jsonify({
            'course_code': course.code,
            'course_title': _course_title(course),
            'course_title_abbr': _course_title_abbr(course),
            'credit': _course_credit(course),
            'subject': course.subject,
            'catalog_number': course.catalog_number,
            'course_desc': (current_catalog_version(course).description if current_catalog_version(course) else None) or course.description,
            'pre_requirement': _course_requirement(course, "pre_requirement"),
            'co_requirement': _course_requirement(course, "co_requirement"),
            'exclusion': _course_requirement(course, "exclusion"),
            'pg_course': _course_flag(course, "pg_course"),
            'klms_course': _course_flag(course, "klms_course"),
            'sections': section_data,
        })

    return jsonify({
        'course_code': course.code,
        'course_title': _course_title(course),
        'course_title_abbr': _course_title_abbr(course),
        'credit': _course_credit(course),
        'subject': course.subject,
        'catalog_number': course.catalog_number,
        'course_desc': (current_catalog_version(course).description if current_catalog_version(course) else None) or course.description,
        'pre_requirement': _course_requirement(course, "pre_requirement"),
        'co_requirement': _course_requirement(course, "co_requirement"),
        'exclusion': _course_requirement(course, "exclusion"),
        'pg_course': _course_flag(course, "pg_course"),
        'klms_course': _course_flag(course, "klms_course"),
        'sections': [],
    })


# --- Cart CRUD ---

def _serialize_cart_item(cart_item):
    """Serialize a cart item with course details and bundles."""
    return _serialize_domain_cart_item(cart_item) if isinstance(cart_item, UserOfferingCart) else None


def _serialize_domain_cart_item(cart_item):
    offering = cart_item.offering
    course = offering.course
    selections = UserSectionSelection.query.filter_by(
        user_id=cart_item.user_id,
        offering_id=offering.id,
    ).all()
    selection_map = {selection.section_id: selection.enabled for selection in selections}

    section_groups = {}
    for section in _domain_sections_for_offering(offering):
        key = (section.bundle, section.layer)
        if key not in section_groups:
            section_groups[key] = {
                'id': section.bundle,
                'layer': section.layer,
                'enabled': True,
                'sections': [],
                '_enabled_values': [],
            }
        enabled = selection_map.get(section.id, True)
        section_groups[key]['_enabled_values'].append(enabled)
        meetings = _meetings_for_section(section.id)
        section_groups[key]['sections'].append({
            'section_id': section.source_section_id,
            'name': section.name,
            'section_type': section.section_type,
            'is_main': section.is_main,
            'quota': section.quota,
            'lectures': [{
                'day': meeting.day,
                'start_time': meeting.start_time,
                'end_time': meeting.end_time,
                'room': meeting.room,
                'instructor': meeting.instructor_text,
            } for meeting in meetings],
        })

    layers = {}
    for group in sorted(section_groups.values(), key=lambda item: (item['layer'], item['id'])):
        group['enabled'] = all(group.pop('_enabled_values') or [True])
        layer = group['layer']
        layers.setdefault(layer, []).append(group)

    return {
        'course_code': course.code,
        'course_title': _course_title(course),
        'credit': _course_credit(course),
        'subject': course.subject,
        'pg_course': _course_flag(course, "pg_course"),
        'klms_course': _course_flag(course, "klms_course"),
        'enabled': cart_item.enabled,
        'layers': layers,
    }


@bp.route('/cart/<semester>', methods=['GET'])
@jwt_required()
def get_cart(semester):
    """Get user's cart for a semester."""
    user_id = int(get_jwt_identity())
    domain_items = (
        UserOfferingCart.query
        .join(CourseOffering)
        .filter(UserOfferingCart.user_id == user_id, CourseOffering.semester_id == semester)
        .all()
    )
    result = []
    for item in domain_items:
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
    data, error = _json_body()
    if error:
        return error
    raw_course_code = data.get('course_code')
    if not isinstance(raw_course_code, str) or not raw_course_code.strip():
        return jsonify({'error': 'Invalid course code'}), 400
    course_code = raw_course_code.strip().upper()

    course = _find_course_by_code(course_code)
    if not course:
        return jsonify({'error': 'Course not found'}), 404

    offering = find_offering(course, semester)
    if offering:
        existing = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        if existing:
            return jsonify({'error': 'Course already in cart'}), 409

        sections = _domain_sections_for_offering(offering)
        if not sections:
            return jsonify({'error': 'Course has no sections for semester'}), 422

        cart = UserOfferingCart(user_id=user_id, offering_id=offering.id, enabled=False)
        db.session.add(cart)
        for section in sections:
            db.session.add(UserSectionSelection(
                user_id=user_id,
                offering_id=offering.id,
                section_id=section.id,
                enabled=True,
                source="cart",
            ))
        db.session.commit()
        return jsonify(_serialize_cart_item(cart))

    return jsonify({'error': 'Course has no sections for semester'}), 422


@bp.route('/cart/<semester>/remove/<code>', methods=['DELETE'])
@jwt_required()
def remove_from_cart(semester, code):
    """Remove a course from the cart."""
    user_id = int(get_jwt_identity())
    course = _find_course_by_code(code)
    offering = find_offering(course, semester) if course else None
    if offering:
        cart = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        if cart:
            UserSectionSelection.query.filter_by(user_id=user_id, offering_id=offering.id).delete()
            db.session.delete(cart)
            db.session.commit()
            return jsonify({'ok': True})

    return jsonify({'error': 'Not in cart'}), 404


@bp.route('/cart/<semester>/course/<code>/toggle', methods=['PUT'])
@jwt_required()
def toggle_course_enabled(semester, code):
    """Toggle course enabled state."""
    user_id = int(get_jwt_identity())
    data, error = _json_body()
    if error:
        return error
    course = _find_course_by_code(code)
    offering = find_offering(course, semester) if course else None
    if offering:
        cart = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        if not cart:
            return jsonify({'error': 'Not in cart'}), 404
        cart.enabled = data.get('enabled', not cart.enabled)
        db.session.commit()
        return jsonify({'course_code': course.code, 'enabled': cart.enabled})

    return jsonify({'error': 'Not in cart'}), 404


@bp.route('/cart/<semester>/bundle/<code>/<int:bundle_id>/<int:layer>/toggle', methods=['PUT'])
@jwt_required()
def toggle_bundle_enabled(semester, code, bundle_id, layer):
    """Toggle bundle enabled state."""
    user_id = int(get_jwt_identity())
    data, error = _json_body()
    if error:
        return error
    course = _find_course_by_code(code)
    offering = find_offering(course, semester) if course else None
    if offering:
        cart = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        sections = CourseSection.query.filter_by(
            offering_id=offering.id,
            bundle=bundle_id,
            layer=layer,
        ).all()
        if not cart or not sections:
            return jsonify({'error': 'Bundle not found'}), 404
        new_state = data.get('enabled')
        if new_state is None:
            existing = UserSectionSelection.query.filter_by(
                user_id=user_id,
                offering_id=offering.id,
                section_id=sections[0].id,
            ).first()
            new_state = not (existing.enabled if existing else True)
        for section in sections:
            selection = UserSectionSelection.query.filter_by(
                user_id=user_id,
                offering_id=offering.id,
                section_id=section.id,
            ).first()
            if selection is None:
                selection = UserSectionSelection(
                    user_id=user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    source="cart",
                )
                db.session.add(selection)
            selection.enabled = bool(new_state)
        db.session.commit()
        return jsonify({'id': bundle_id, 'layer': layer, 'enabled': bool(new_state)})

    return jsonify({'error': 'Bundle not found'}), 404


@bp.route('/cart/<semester>/layer/<code>/<int:layer>/toggle', methods=['PUT'])
@jwt_required()
def toggle_layer_enabled(semester, code, layer):
    """Toggle all bundles in a layer for a course."""
    user_id = int(get_jwt_identity())
    data, error = _json_body()
    if error:
        return error
    course = _find_course_by_code(code)
    offering = find_offering(course, semester) if course else None
    if offering:
        cart = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        sections = CourseSection.query.filter_by(offering_id=offering.id, layer=layer).all()
        if not cart or not sections:
            return jsonify({'error': 'No bundles found'}), 404
        existing = UserSectionSelection.query.filter_by(
            user_id=user_id,
            offering_id=offering.id,
            section_id=sections[0].id,
        ).first()
        new_state = data.get('enabled', not (existing.enabled if existing else True))
        for section in sections:
            selection = UserSectionSelection.query.filter_by(
                user_id=user_id,
                offering_id=offering.id,
                section_id=section.id,
            ).first()
            if selection is None:
                selection = UserSectionSelection(
                    user_id=user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    source="cart",
                )
                db.session.add(selection)
            selection.enabled = bool(new_state)
        db.session.commit()
        return jsonify({'ok': True, 'enabled': bool(new_state), 'count': len(sections)})

    return jsonify({'error': 'No bundles found'}), 404


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
