from decimal import Decimal
import re

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.academic_map import UserAcademicProfile, UserCourseRecord
from app.models.course_domain import UserCourseAttempt, UserCourseState
from app.services.academic_map_service import build_academic_map_summary, _record_from_domain_state
from app.services.academic_major_metadata import normalize_target_majors
from app.services.course_catalog_matcher import enrich_import_row_with_catalog, find_course_by_normalized_code
from app.services.course_domain import derive_user_course_state, find_offering, grade_points_for_letter
from app.services.course_history_importer import parse_course_history_text
from app.utils.academic_map_import_text import clean_copied_status_text

bp = Blueprint("academic_map", __name__, url_prefix="/academic-map")

VALID_STATUSES = {
    UserCourseRecord.STATUS_COMPLETED,
    UserCourseRecord.STATUS_IN_PROGRESS,
    UserCourseRecord.STATUS_PLANNED,
    UserCourseRecord.STATUS_INTERESTED,
    UserCourseRecord.STATUS_NOT_INTERESTED,
}

STRONG_STATUSES = {
    UserCourseRecord.STATUS_COMPLETED,
    UserCourseRecord.STATUS_IN_PROGRESS,
    UserCourseRecord.STATUS_PLANNED,
}


def _compact_course_code(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def _current_user_id() -> int:
    return int(get_jwt_identity())


def _semester_id_from_term_label(term_label: str | None) -> str | None:
    if not term_label:
        return None
    match = re.search(r"\b(20\d{2})-\d{2}\s+(Spring|Summer|Fall|Winter)\b", term_label, re.IGNORECASE)
    if not match:
        return None
    suffix = {
        "fall": "10",
        "winter": "20",
        "spring": "30",
        "summer": "40",
    }.get(match.group(2).lower())
    if not suffix:
        return None
    return f"{int(match.group(1)) % 100:02d}{suffix}"


def _upsert_course_state(user_id: int, course_id: int, status: str, source: str) -> UserCourseState:
    state = UserCourseState.query.filter_by(user_id=user_id, course_id=course_id).first()
    if state is None:
        state = UserCourseState(user_id=user_id, course_id=course_id, status=status, source=source)
        db.session.add(state)
    elif state.status not in {"completed", "in_progress"} or status in {"completed", "in_progress"}:
        state.status = status
        state.source = source
    return state


def _upsert_derived_course_state(user_id: int, course_id: int) -> None:
    state_data = derive_user_course_state(user_id, course_id)
    state = UserCourseState.query.filter_by(user_id=user_id, course_id=course_id).first()
    if state is None:
        state = UserCourseState(user_id=user_id, course_id=course_id)
        db.session.add(state)
    for key, value in state_data.items():
        setattr(state, key, value)


def _sync_domain_record(user_id: int, matched_course, row: dict, status: str, *, keep_grades: bool) -> None:
    if matched_course is None:
        return

    if status in {UserCourseRecord.STATUS_PLANNED, UserCourseRecord.STATUS_INTERESTED}:
        _upsert_course_state(user_id, matched_course.id, "interested", "import")
        return

    if status == UserCourseRecord.STATUS_NOT_INTERESTED:
        UserCourseAttempt.query.filter_by(user_id=user_id, course_id=matched_course.id).delete(synchronize_session=False)
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=matched_course.id).first()
        if state is not None:
            db.session.delete(state)
        return

    if status not in {UserCourseRecord.STATUS_COMPLETED, UserCourseRecord.STATUS_IN_PROGRESS}:
        return

    semester_id = row.get("term_code") or _semester_id_from_term_label(row.get("term_label"))
    offering = find_offering(matched_course, semester_id) if semester_id else None
    if offering is None:
        return

    attempt_status = "completed" if status == UserCourseRecord.STATUS_COMPLETED else "in_progress"
    attempt = UserCourseAttempt.query.filter_by(
        user_id=user_id,
        course_id=matched_course.id,
        offering_id=offering.id,
    ).first()
    if attempt is None:
        attempt = UserCourseAttempt(
            user_id=user_id,
            course_id=matched_course.id,
            offering_id=offering.id,
            status=attempt_status,
            source="transcript_import",
        )
        db.session.add(attempt)
    attempt.status = attempt_status
    attempt.term_label = row.get("term_label")
    attempt.raw_payload = row
    if attempt_status == "completed" and keep_grades and row.get("grade"):
        attempt.grade_letter = row.get("grade")
        attempt.grade_points = grade_points_for_letter(row.get("grade"))
    else:
        attempt.grade_letter = None
        attempt.grade_points = None
    db.session.flush()
    _upsert_derived_course_state(user_id, matched_course.id)


def _row_from_record(record: UserCourseRecord) -> dict:
    return {
        "course_code": record.course_code,
        "matched_course_code": record.course_code,
        "course_title": record.course_title,
        "term_label": record.term_label,
        "term_code": record.term_code,
        "units": float(record.units) if record.units is not None else None,
        "status": record.status,
        "grade": record.grade,
    }


def _delete_domain_record_for_legacy_record(user_id: int, record: UserCourseRecord) -> None:
    matched_course = find_course_by_normalized_code(record.course_code)
    if matched_course is None:
        return

    semester_id = record.term_code or _semester_id_from_term_label(record.term_label)
    offering = find_offering(matched_course, semester_id) if semester_id else None
    if offering is not None:
        UserCourseAttempt.query.filter_by(
            user_id=user_id,
            course_id=matched_course.id,
            offering_id=offering.id,
        ).delete(synchronize_session=False)

    remaining_legacy_records = UserCourseRecord.query.filter(
        UserCourseRecord.user_id == user_id,
        UserCourseRecord.id != record.id,
    ).all()
    has_remaining_same_course = any(
        _compact_course_code(item.course_code) == _compact_course_code(record.course_code)
        for item in remaining_legacy_records
    )
    remaining_attempt_count = UserCourseAttempt.query.filter_by(
        user_id=user_id,
        course_id=matched_course.id,
    ).count()

    if has_remaining_same_course or remaining_attempt_count:
        _upsert_derived_course_state(user_id, matched_course.id)
        return

    state = UserCourseState.query.filter_by(user_id=user_id, course_id=matched_course.id).first()
    if state is not None:
        db.session.delete(state)


@bp.route("/profile", methods=["GET"])
@jwt_required()
def get_profile():
    profile = UserAcademicProfile.get_or_create_for_user(_current_user_id())
    db.session.commit()
    return jsonify({"profile": profile.to_dict()}), 200


@bp.route("/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    data = request.get_json() or {}
    profile = UserAcademicProfile.get_or_create_for_user(_current_user_id())
    if "cohort" in data:
        profile.cohort = str(data["cohort"]).strip() or None
    if "target_majors" in data:
        majors = data["target_majors"] if isinstance(data["target_majors"], list) else []
        profile.target_majors = normalize_target_majors(majors)
    if "grade_policy" in data and data["grade_policy"] in ["keep_private", "drop_grades"]:
        profile.grade_policy = data["grade_policy"]
    db.session.commit()
    return jsonify({"profile": profile.to_dict()}), 200


@bp.route("/summary", methods=["GET"])
@jwt_required()
def get_summary():
    summary = build_academic_map_summary(_current_user_id())
    db.session.commit()
    return jsonify(summary), 200


@bp.route("/courses/<course_code>/interest", methods=["PUT"])
@jwt_required()
def mark_course_interested(course_code):
    user_id = _current_user_id()
    normalized_code = _compact_course_code(course_code)
    if not normalized_code:
        return jsonify({"error": "Course code is required."}), 400

    matched_course = find_course_by_normalized_code(normalized_code)
    if matched_course is None:
        return jsonify({"error": "Course not found."}), 404

    state_data = derive_user_course_state(user_id, matched_course.id)
    if state_data["status"] in {"completed", "in_progress"}:
        state = UserCourseState.query.filter_by(user_id=user_id, course_id=matched_course.id).first()
        record = _record_from_domain_state(state) if state else None
        return jsonify({
            "error": "Course already has a stronger academic status.",
            "record": record.to_dict(include_grade=True) if record else None,
        }), 409

    state = _upsert_course_state(user_id, matched_course.id, "interested", "manual")
    db.session.flush()
    record = _record_from_domain_state(state)
    db.session.commit()
    return jsonify({"record": record.to_dict(include_grade=True) if record else None}), 200


@bp.route("/courses/<course_code>/interest", methods=["DELETE"])
@jwt_required()
def cancel_course_interested(course_code):
    user_id = _current_user_id()
    normalized_code = _compact_course_code(course_code)
    if not normalized_code:
        return jsonify({"deleted": 0}), 200

    deleted = 0
    matched_course = find_course_by_normalized_code(normalized_code)
    if matched_course is not None:
        state = UserCourseState.query.filter_by(
            user_id=user_id,
            course_id=matched_course.id,
            status="interested",
        ).first()
        if state is not None:
            db.session.delete(state)
            deleted += 1

    records = UserCourseRecord.query.filter_by(user_id=user_id, status=UserCourseRecord.STATUS_INTERESTED).all()
    for record in records:
        if _compact_course_code(record.course_code) == normalized_code:
            db.session.delete(record)
            deleted += 1

    db.session.commit()
    return jsonify({"deleted": deleted}), 200


@bp.route("/import/parse", methods=["POST"])
@jwt_required()
def parse_import():
    data = request.get_json() or {}
    rows = [enrich_import_row_with_catalog(row) for row in parse_course_history_text(data.get("text", ""))]
    return jsonify({"rows": rows, "count": len(rows)}), 200


@bp.route("/records/bulk", methods=["POST"])
@jwt_required()
def save_records_bulk():
    user_id = _current_user_id()
    data = request.get_json() or {}
    keep_grades = bool(data.get("keep_grades"))
    rows = data.get("records") if isinstance(data.get("records"), list) else []
    saved = []

    for row in rows:
        course_code = str(row.get("course_code", "")).strip().upper()
        if not course_code:
            continue
        matched_course = find_course_by_normalized_code(row.get("matched_course_code") or course_code)
        status = row.get("status") if row.get("status") in VALID_STATUSES else UserCourseRecord.STATUS_COMPLETED
        units = row.get("units")
        resolved_units = units if units is not None else (matched_course.credits if matched_course else None)
        resolved_code = matched_course.code if matched_course else course_code
        record = UserCourseRecord(
            user_id=user_id,
            course_id=matched_course.id if matched_course else None,
            course_code=resolved_code,
            course_title=matched_course.name if matched_course else clean_copied_status_text(row.get("course_title")),
            term_label=row.get("term_label"),
            term_code=row.get("term_code"),
            units=Decimal(str(resolved_units)) if resolved_units is not None else None,
            status=status,
            grade=row.get("grade") if keep_grades else None,
            keep_grade=keep_grades and bool(row.get("grade")),
            import_source=UserCourseRecord.SOURCE_PASTE,
            needs_review=bool(row.get("needs_review")) or matched_course is None,
            review_reason=row.get("review_reason") or (None if matched_course else "Course was not matched in the course database."),
            raw_payload=row,
        )
        db.session.add(record)
        saved.append(record)
        _sync_domain_record(user_id, matched_course, row, status, keep_grades=keep_grades)

    db.session.commit()
    return jsonify({"records": [record.to_dict(include_grade=True) for record in saved]}), 200


@bp.route("/records/<int:record_id>", methods=["PUT"])
@jwt_required()
def update_record(record_id):
    user_id = _current_user_id()
    record = UserCourseRecord.query.filter_by(id=record_id, user_id=user_id).first_or_404()
    data = request.get_json() or {}
    if data.get("status") in VALID_STATUSES:
        record.status = data["status"]
    if "term_label" in data:
        record.term_label = data["term_label"]
    if "term_code" in data:
        record.term_code = data["term_code"]
    if "units" in data:
        record.units = Decimal(str(data["units"])) if data["units"] is not None else None
    if "keep_grade" in data:
        record.keep_grade = bool(data["keep_grade"])
        if not record.keep_grade:
            record.grade = None
    if "grade" in data:
        record.grade = data["grade"] if record.keep_grade else None
    record.needs_review = bool(data.get("needs_review", record.needs_review))
    matched_course = find_course_by_normalized_code(record.course_code)
    if matched_course is not None:
        record.course_id = matched_course.id
        record.course_code = matched_course.code
        _sync_domain_record(user_id, matched_course, _row_from_record(record), record.status, keep_grades=record.keep_grade)
    db.session.commit()
    return jsonify({"record": record.to_dict(include_grade=True)}), 200


@bp.route("/records/<int:record_id>", methods=["DELETE"])
@jwt_required()
def delete_record(record_id):
    user_id = _current_user_id()
    record = UserCourseRecord.query.filter_by(id=record_id, user_id=user_id).first_or_404()
    _delete_domain_record_for_legacy_record(user_id, record)
    db.session.delete(record)
    db.session.commit()
    return "", 204


@bp.route("/records", methods=["DELETE"])
@jwt_required()
def clear_records():
    user_id = _current_user_id()
    deleted_records = UserCourseRecord.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    UserCourseAttempt.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    UserCourseState.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    profile = UserAcademicProfile.get_or_create_for_user(user_id)
    profile.cohort = None
    profile.target_majors = []
    profile.grade_policy = "keep_private"
    db.session.commit()
    return jsonify({"deleted_records": deleted_records, "profile": profile.to_dict()}), 200


@bp.route("/grades", methods=["DELETE"])
@jwt_required()
def delete_grades():
    user_id = _current_user_id()
    records = UserCourseRecord.query.filter_by(user_id=user_id).all()
    cleared = 0
    for record in records:
        if record.grade or record.keep_grade:
            record.grade = None
            record.keep_grade = False
            cleared += 1
    for attempt in UserCourseAttempt.query.filter_by(user_id=user_id).all():
        if attempt.grade_letter or attempt.grade_points is not None:
            attempt.grade_letter = None
            attempt.grade_points = None
            cleared += 1
    for state in UserCourseState.query.filter_by(user_id=user_id).all():
        state.best_grade_letter = None
        state.best_grade_points = None
    db.session.commit()
    return jsonify({"cleared_count": cleared}), 200
