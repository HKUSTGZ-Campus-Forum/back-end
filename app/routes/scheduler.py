import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, request, jsonify
from app.models.course import Course
from app.models.course_domain import (
    CourseMeeting,
    CourseOffering,
    CourseSection,
    SisnSyncRun,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.user import User
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, or_
from app.services.course_domain import current_catalog_version, find_offering, normalize_course_code
from app.services.scheduler_popularity import (
    POPULARITY_HISTORY_END_AT,
    POPULARITY_HISTORY_SEMESTER,
    build_popularity_history,
    build_popularity_snapshot,
    is_canonical_popularity_user,
    is_eligible_popularity_user,
    normalize_popularity_course_codes,
    popularity_state,
    record_popularity_transition,
)
from app.services.sisn_push_auth import (
    SisnPushAuthenticationError,
    verify_push_request,
)
from app.services.sisn_sync import (
    SisnSyncBlocked,
    SisnSyncDuplicateRequest,
    SisnSyncGuards,
    run_sisn_sync,
)

bp = Blueprint('scheduler', __name__, url_prefix='/scheduler')

SEMESTER_META = {
    '2430': {'name': '2024-25 Spring', 'name_zh': '24-25春'},
    '2440': {'name': '2024-25 Summer', 'name_zh': '24-25夏'},
    '2510': {'name': '2025-26 Fall', 'name_zh': '25-26秋'},
    '2530': {'name': '2025-26 Spring', 'name_zh': '25-26春'},
    '2540': {'name': '2025-26 Summer', 'name_zh': '25-26夏'},
    '2610': {'name': '2026-27 Fall', 'name_zh': '26-27秋'},
}


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({'error': 'Invalid JSON body'}), 400)
    return data, None


class _EnvelopeClient:
    def __init__(self, envelope):
        self.envelope = envelope

    def fetch_class_quota(self, *, term):
        return self.envelope


def _sisn_guards_from_config():
    return SisnSyncGuards(
        min_source_courses=current_app.config['SISN_SYNC_MIN_SOURCE_COURSES'],
        max_source_courses=current_app.config['SISN_SYNC_MAX_SOURCE_COURSES'],
        min_source_classes=current_app.config['SISN_SYNC_MIN_SOURCE_CLASSES'],
        max_source_classes=current_app.config['SISN_SYNC_MAX_SOURCE_CLASSES'],
        min_source_schedules=current_app.config['SISN_SYNC_MIN_SOURCE_SCHEDULES'],
        max_source_schedules=current_app.config['SISN_SYNC_MAX_SOURCE_SCHEDULES'],
        min_candidate_sections=current_app.config['SISN_SYNC_MIN_CANDIDATE_SECTIONS'],
        max_fallback_main_classes=current_app.config['SISN_SYNC_MAX_FALLBACK_MAIN_CLASSES'],
        max_missing_baseline_classes=current_app.config['SISN_SYNC_MAX_MISSING_BASELINE_CLASSES'],
        max_omitted_unscheduled_classes=current_app.config['SISN_SYNC_MAX_OMITTED_UNSCHEDULED_CLASSES'],
    )


def _requested_enabled(data, default):
    if 'enabled' not in data:
        return default, None
    enabled = data['enabled']
    if not isinstance(enabled, bool):
        return None, (jsonify({'error': 'enabled must be a boolean'}), 400)
    return enabled, None


def _offered_offering(course, semester):
    offering = find_offering(course, semester) if course else None
    if offering is None or offering.status != 'offered':
        return None
    return offering


def _request_user():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None
    return db.session.get(User, user_id)


def _eligible_popularity_contributor(user_id):
    user = db.session.get(User, user_id)
    return is_eligible_popularity_user(user) and is_canonical_popularity_user(user)


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
    if version is not None and version.credits is not None:
        return version.credits
    return course.credits


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


def _course_source_metadata(course):
    version = current_catalog_version(course)
    if version and isinstance(version.source_metadata, dict):
        return version.source_metadata
    return {}


def _domain_sections_for_offering(offering):
    if not offering:
        return []
    return (
        CourseSection.query
        .filter_by(offering_id=offering.id, status="active")
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

@bp.route('/internal/sisn-ingest', methods=['POST'])
def ingest_sisn_snapshot():
    """Accept a fresh, Ed25519-signed snapshot from the school server."""
    if not current_app.config.get('SISN_PUSH_INGEST_ENABLED', False):
        return jsonify({'error': 'SISN ingest is disabled'}), 404

    maximum_body_size = current_app.config['SISN_PUSH_MAX_BODY_BYTES']
    if request.content_length is not None and request.content_length > maximum_body_size:
        return jsonify({'error': 'Request body is too large'}), 413
    raw_body = request.get_data(cache=False)
    if not raw_body or len(raw_body) > maximum_body_size:
        return jsonify({'error': 'Invalid request body'}), 400 if not raw_body else 413

    timestamp = request.headers.get('X-UniKorn-Timestamp', '')
    nonce = request.headers.get('X-UniKorn-Nonce', '')
    signature = request.headers.get('X-UniKorn-Signature', '')
    try:
        verify_push_request(
            public_key_path=Path(current_app.config['SISN_PUSH_PUBLIC_KEY_PATH']),
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            body=raw_body,
            max_age_seconds=current_app.config['SISN_PUSH_MAX_AGE_SECONDS'],
        )
    except SisnPushAuthenticationError:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({'error': 'Invalid JSON body'}), 400
    if not isinstance(payload, dict) or not isinstance(payload.get('envelope'), dict):
        return jsonify({'error': 'Invalid SISN envelope'}), 400

    configured_term = current_app.config['SISN_SYNC_TERM']
    term = payload.get('term')
    mode = payload.get('mode')
    if term != configured_term:
        return jsonify({'error': 'Unexpected SISN term'}), 400
    if mode not in {'dry-run', 'apply'}:
        return jsonify({'error': 'mode must be dry-run or apply'}), 400

    archive_value = current_app.config.get('SISN_SYNC_ARCHIVE_DIR', '')
    try:
        result = run_sisn_sync(
            client=_EnvelopeClient(payload['envelope']),
            term=term,
            baseline_path=Path(current_app.config['SISN_SYNC_BASELINE_PATH']).resolve(),
            mode=mode,
            guards=_sisn_guards_from_config(),
            archive_dir=Path(archive_value).resolve() if archive_value else None,
            archive_retention_files=current_app.config['SISN_SYNC_ARCHIVE_RETENTION_FILES'],
            request_id=f'push-{nonce}',
        )
    except SisnSyncDuplicateRequest:
        return jsonify({'error': 'Request already processed'}), 409
    except SisnSyncBlocked as exc:
        return jsonify({'error': 'SISN snapshot blocked', 'detail': str(exc)}), 422
    except Exception:
        current_app.logger.exception('SISN push ingest failed')
        failed_run = SisnSyncRun.query.filter_by(request_id=f'push-{nonce}').first()
        response = {'error': 'SISN ingest failed'}
        if failed_run is not None:
            response['code'] = failed_run.error_code
            response['detail'] = failed_run.error_message
        return jsonify(response), 500

    response = asdict(result)
    status_code = 422 if result.status == 'blocked' else 200
    return jsonify(response), status_code

@bp.route('/semesters', methods=['GET'])
def list_semesters():
    """List semesters that have section data."""
    domain_rows = (
        db.session.query(CourseOffering.semester_id, func.count(CourseSection.id))
        .join(CourseSection, CourseSection.offering_id == CourseOffering.id)
        .filter(CourseOffering.status == 'offered')
        .filter(CourseSection.status == 'active')
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
                .filter(
                    CourseOffering.semester_id == semester,
                    CourseOffering.status == 'offered',
                )
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
        .filter(CourseOffering.status == 'offered')
        .filter(CourseSection.status == 'active')
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
        source_metadata = _course_source_metadata(course)
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
                'enrol': s.enrol,
                'unfilled_capacity': s.avail,
                'wait': s.wait,
                'reserve_cap': s.reserve_cap or [],
                'consent_required': s.consent_required,
                'remarks': s.remarks,
                'section_type': s.section_type,
                'is_main': s.is_main,
                'lectures': [{
                    'day': m.day,
                    'start_time': m.start_time,
                    'end_time': m.end_time,
                    'room': m.room,
                    'instructor': m.instructor_text,
                    'facility_id': m.facility_id,
                    'date_ranges': m.date_ranges or [],
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
            'attributes': source_metadata.get('attributes', []),
            'previous_course_code': source_metadata.get('previous_course_code'),
            'sections': section_data,
        })

    source_metadata = _course_source_metadata(course)
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
        'attributes': source_metadata.get('attributes', []),
        'previous_course_code': source_metadata.get('previous_course_code'),
        'sections': [],
    })


# --- Popularity ---


def _parse_popularity_history_timestamp(name):
    raw_value = request.args.get(name, "").strip()
    if not raw_value:
        raise ValueError(f"{name} is required")
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
    if value.tzinfo is None:
        raise ValueError(f"{name} must include a timezone offset")
    return value.astimezone(timezone.utc)

@bp.route('/popularity/<semester>', methods=['GET'])
@jwt_required()
def get_popularity(semester):
    """Return anonymous current planner counts for courses in the viewer's cart."""
    viewer = _request_user()
    if viewer is None or viewer.is_deleted:
        return jsonify({'error': 'Authenticated user not found'}), 401
    if not is_eligible_popularity_user(viewer) or not is_canonical_popularity_user(viewer):
        return jsonify({'error': 'verified_institutional_account_required'}), 403

    try:
        course_codes = normalize_popularity_course_codes(
            request.args.getlist('course_codes'),
            limit=30,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    payload = build_popularity_snapshot(
        viewer_id=viewer.id,
        semester_id=str(semester).strip(),
        course_codes=course_codes,
    )
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Authorization'
    return response


@bp.route('/popularity/<semester>/history', methods=['GET'])
@jwt_required()
def get_popularity_history(semester):
    """Return anonymous sampled planner counts for one cart-scoped entity."""
    viewer = _request_user()
    if viewer is None or viewer.is_deleted:
        return jsonify({'error': 'Authenticated user not found'}), 401
    if not is_eligible_popularity_user(viewer) or not is_canonical_popularity_user(viewer):
        return jsonify({'error': 'verified_institutional_account_required'}), 403

    semester_id = str(semester).strip()
    if semester_id != POPULARITY_HISTORY_SEMESTER:
        return jsonify({'error': 'popularity_history_not_available'}), 404

    course_code = request.args.get('course_code', '').strip()
    section_id = request.args.get('section_id', '').strip() or None
    if not course_code:
        return jsonify({'error': 'course_code is required'}), 400
    if section_id is not None and len(section_id) > 32:
        return jsonify({'error': 'Invalid section_id'}), 400

    try:
        from_at = _parse_popularity_history_timestamp('from')
        to_at = min(_parse_popularity_history_timestamp('to'), POPULARITY_HISTORY_END_AT)
        if from_at > to_at:
            raise ValueError('from must be before or equal to to')
        payload = build_popularity_history(
            viewer_id=viewer.id,
            semester_id=semester_id,
            course_code=course_code,
            section_id=section_id,
            from_at=from_at,
            to_at=to_at,
            resolution=request.args.get('resolution', 'auto').strip() or 'auto',
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if payload is None:
        return jsonify({'error': 'popularity_history_scope_not_found'}), 404
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Authorization'
    return response


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
            'enrol': section.enrol,
            'unfilled_capacity': section.avail,
            'wait': section.wait,
            'reserve_cap': section.reserve_cap or [],
            'consent_required': section.consent_required,
            'remarks': section.remarks,
            'lectures': [{
                'day': meeting.day,
                'start_time': meeting.start_time,
                'end_time': meeting.end_time,
                'room': meeting.room,
                'instructor': meeting.instructor_text,
                'facility_id': meeting.facility_id,
                'date_ranges': meeting.date_ranges or [],
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
        .filter(
            UserOfferingCart.user_id == user_id,
            CourseOffering.semester_id == semester,
            CourseOffering.status == 'offered',
        )
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

    offering = _offered_offering(course, semester)
    if offering:
        existing = UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering.id).first()
        if existing:
            return jsonify({'error': 'Course already in cart'}), 409

        sections = _domain_sections_for_offering(offering)
        if not sections:
            return jsonify({'error': 'Course has no sections for semester'}), 422

        cart = UserOfferingCart(user_id=user_id, offering_id=offering.id, enabled=False)
        db.session.add(cart)
        contributor_is_eligible = _eligible_popularity_contributor(user_id)
        record_popularity_transition(
            contributor_is_eligible=contributor_is_eligible,
            offering_id=offering.id,
            from_state=None,
            to_state='looking',
            reason='cart_added',
        )
        for section in sections:
            db.session.add(UserSectionSelection(
                user_id=user_id,
                offering_id=offering.id,
                section_id=section.id,
                enabled=True,
                source="cart",
            ))
            record_popularity_transition(
                contributor_is_eligible=contributor_is_eligible,
                offering_id=offering.id,
                section=section,
                from_state=None,
                to_state='looking',
                reason='cart_added',
            )
        db.session.commit()
        return jsonify(_serialize_cart_item(cart))

    return jsonify({'error': 'Course has no sections for semester'}), 422


@bp.route('/cart/<semester>/remove/<code>', methods=['DELETE'])
@jwt_required()
def remove_from_cart(semester, code):
    """Remove a course from the cart."""
    user_id = int(get_jwt_identity())
    course = _find_course_by_code(code)
    offering = _offered_offering(course, semester)
    if offering:
        cart = (
            UserOfferingCart.query
            .filter_by(user_id=user_id, offering_id=offering.id)
            .with_for_update()
            .first()
        )
        if cart:
            selected = (
                UserSectionSelection.query
                .join(CourseSection, CourseSection.id == UserSectionSelection.section_id)
                .filter(
                    UserSectionSelection.user_id == user_id,
                    UserSectionSelection.offering_id == offering.id,
                    UserSectionSelection.enabled.is_(True),
                    CourseSection.offering_id == offering.id,
                    CourseSection.status == 'active',
                )
                .with_for_update()
                .all()
            )
            contributor_is_eligible = _eligible_popularity_contributor(user_id)
            state = popularity_state(cart.enabled)
            record_popularity_transition(
                contributor_is_eligible=contributor_is_eligible,
                offering_id=offering.id,
                from_state=state,
                to_state=None,
                reason='cart_removed',
            )
            for selection in selected:
                record_popularity_transition(
                    contributor_is_eligible=contributor_is_eligible,
                    offering_id=offering.id,
                    section=selection.section,
                    from_state=state,
                    to_state=None,
                    reason='cart_removed',
                )
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
    offering = _offered_offering(course, semester)
    if offering:
        cart = (
            UserOfferingCart.query
            .filter_by(user_id=user_id, offering_id=offering.id)
            .with_for_update()
            .first()
        )
        if not cart:
            return jsonify({'error': 'Not in cart'}), 404
        new_state, state_error = _requested_enabled(data, not cart.enabled)
        if state_error:
            return state_error
        old_state = bool(cart.enabled)
        if old_state != new_state:
            selected = (
                UserSectionSelection.query
                .join(CourseSection, CourseSection.id == UserSectionSelection.section_id)
                .filter(
                    UserSectionSelection.user_id == user_id,
                    UserSectionSelection.offering_id == offering.id,
                    UserSectionSelection.enabled.is_(True),
                    CourseSection.offering_id == offering.id,
                    CourseSection.status == 'active',
                )
                .with_for_update()
                .all()
            )
            contributor_is_eligible = _eligible_popularity_contributor(user_id)
            old_popularity = popularity_state(old_state)
            new_popularity = popularity_state(new_state)
            record_popularity_transition(
                contributor_is_eligible=contributor_is_eligible,
                offering_id=offering.id,
                from_state=old_popularity,
                to_state=new_popularity,
                reason='course_toggled',
            )
            for selection in selected:
                record_popularity_transition(
                    contributor_is_eligible=contributor_is_eligible,
                    offering_id=offering.id,
                    section=selection.section,
                    from_state=old_popularity,
                    to_state=new_popularity,
                    reason='course_toggled',
                )
        cart.enabled = new_state
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
    offering = _offered_offering(course, semester)
    if offering:
        cart = (
            UserOfferingCart.query
            .filter_by(user_id=user_id, offering_id=offering.id)
            .with_for_update()
            .first()
        )
        sections = CourseSection.query.filter_by(
            offering_id=offering.id,
            bundle=bundle_id,
            layer=layer,
            status='active',
        ).all()
        if not cart or not sections:
            return jsonify({'error': 'Bundle not found'}), 404
        existing_selections = (
            UserSectionSelection.query
            .filter(
                UserSectionSelection.user_id == user_id,
                UserSectionSelection.offering_id == offering.id,
                UserSectionSelection.section_id.in_([section.id for section in sections]),
            )
            .with_for_update()
            .all()
        )
        selection_map = {selection.section_id: selection for selection in existing_selections}
        first = selection_map.get(sections[0].id)
        implicit_state = not (first.enabled if first else True)
        new_state, state_error = _requested_enabled(data, implicit_state)
        if state_error:
            return state_error
        contributor_is_eligible = _eligible_popularity_contributor(user_id)
        selected_state = popularity_state(cart.enabled)
        for section in sections:
            selection = selection_map.get(section.id)
            was_enabled = bool(selection.enabled) if selection is not None else False
            if selection is None:
                selection = UserSectionSelection(
                    user_id=user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    source="cart",
                )
                db.session.add(selection)
            selection.enabled = new_state
            if was_enabled != new_state:
                record_popularity_transition(
                    contributor_is_eligible=contributor_is_eligible,
                    offering_id=offering.id,
                    section=section,
                    from_state=selected_state if was_enabled else None,
                    to_state=selected_state if new_state else None,
                    reason='bundle_toggled',
                )
        db.session.commit()
        return jsonify({'id': bundle_id, 'layer': layer, 'enabled': new_state})

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
    offering = _offered_offering(course, semester)
    if offering:
        cart = (
            UserOfferingCart.query
            .filter_by(user_id=user_id, offering_id=offering.id)
            .with_for_update()
            .first()
        )
        sections = CourseSection.query.filter_by(
            offering_id=offering.id,
            layer=layer,
            status='active',
        ).all()
        if not cart or not sections:
            return jsonify({'error': 'No bundles found'}), 404
        existing_selections = (
            UserSectionSelection.query
            .filter(
                UserSectionSelection.user_id == user_id,
                UserSectionSelection.offering_id == offering.id,
                UserSectionSelection.section_id.in_([section.id for section in sections]),
            )
            .with_for_update()
            .all()
        )
        selection_map = {selection.section_id: selection for selection in existing_selections}
        first = selection_map.get(sections[0].id)
        implicit_state = not (first.enabled if first else True)
        new_state, state_error = _requested_enabled(data, implicit_state)
        if state_error:
            return state_error
        contributor_is_eligible = _eligible_popularity_contributor(user_id)
        selected_state = popularity_state(cart.enabled)
        for section in sections:
            selection = selection_map.get(section.id)
            was_enabled = bool(selection.enabled) if selection is not None else False
            if selection is None:
                selection = UserSectionSelection(
                    user_id=user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    source="cart",
                )
                db.session.add(selection)
            selection.enabled = new_state
            if was_enabled != new_state:
                record_popularity_transition(
                    contributor_is_eligible=contributor_is_eligible,
                    offering_id=offering.id,
                    section=section,
                    from_state=selected_state if was_enabled else None,
                    to_state=selected_state if new_state else None,
                    reason='layer_toggled',
                )
        db.session.commit()
        return jsonify({'ok': True, 'enabled': new_state, 'count': len(sections)})

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
