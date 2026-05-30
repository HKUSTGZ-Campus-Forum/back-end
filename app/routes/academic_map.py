from decimal import Decimal

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models.academic_map import UserAcademicProfile, UserCourseRecord
from app.services.academic_map_service import build_academic_map_summary
from app.services.academic_major_metadata import normalize_target_majors
from app.services.course_catalog_matcher import enrich_import_row_with_catalog, find_course_by_normalized_code
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


def _current_user_id() -> int:
    return int(get_jwt_identity())


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
    if "units" in data:
        record.units = Decimal(str(data["units"])) if data["units"] is not None else None
    if "grade" in data:
        record.grade = data["grade"] if record.keep_grade else None
    if "keep_grade" in data:
        record.keep_grade = bool(data["keep_grade"])
        if not record.keep_grade:
            record.grade = None
    record.needs_review = bool(data.get("needs_review", record.needs_review))
    db.session.commit()
    return jsonify({"record": record.to_dict(include_grade=True)}), 200


@bp.route("/records/<int:record_id>", methods=["DELETE"])
@jwt_required()
def delete_record(record_id):
    user_id = _current_user_id()
    record = UserCourseRecord.query.filter_by(id=record_id, user_id=user_id).first_or_404()
    db.session.delete(record)
    db.session.commit()
    return "", 204


@bp.route("/records", methods=["DELETE"])
@jwt_required()
def clear_records():
    user_id = _current_user_id()
    deleted_records = UserCourseRecord.query.filter_by(user_id=user_id).delete(synchronize_session=False)
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
    db.session.commit()
    return jsonify({"cleared_count": cleared}), 200
