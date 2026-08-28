from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from app.extensions import db
from app.models.file import File
from app.models.home_carousel_slide import HomeCarouselSlide
from app.services.admin_audit_service import log_admin_action
from app.utils.permissions import require_admin_user


public_bp = Blueprint("home_carousel", __name__, url_prefix="/home/carousel")
admin_bp = Blueprint("home_carousel_admin", __name__, url_prefix="/admin/carousel")


def _admin_guard():
    admin_user, error = require_admin_user()
    if error:
        return None, error
    return admin_user, None


def _clean_text(value, field, max_length, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_length:
        raise ValueError(f"{field} must not exceed {max_length} characters")
    return value or None


def _clean_href(value):
    href = _clean_text(value, "href", 2048)
    if href is None:
        return None
    if any(character.isspace() or ord(character) < 32 for character in href) or "\\" in href:
        raise ValueError("href is invalid")
    try:
        parsed = urlsplit(href)
    except ValueError:
        raise ValueError("href is invalid") from None
    if href.startswith("/") and not href.startswith("//"):
        if not parsed.scheme and not parsed.netloc:
            return href
    try:
        parsed_port = parsed.port
    except ValueError:
        raise ValueError("href is invalid") from None
    if (
        parsed.scheme == "https"
        and parsed.netloc
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and (parsed_port is None or 1 <= parsed_port <= 65535)
    ):
        return href
    raise ValueError("href must be a site path or an HTTPS URL")


def _clean_locale(value):
    if value not in HomeCarouselSlide.LOCALES:
        raise ValueError("locale must be one of zh, en, or all")
    return value


def _clean_bool(value, field):
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _clean_alt_text(payload, locale, current=None):
    current_zh = current.alt_text_zh if current else None
    current_en = current.alt_text_en if current else None
    alt_zh = _clean_text(
        payload.get("alt_text_zh", current_zh),
        "alt_text_zh",
        255,
        required=locale in {HomeCarouselSlide.LOCALE_ZH, HomeCarouselSlide.LOCALE_ALL},
    )
    alt_en = _clean_text(
        payload.get("alt_text_en", current_en),
        "alt_text_en",
        255,
        required=locale in {HomeCarouselSlide.LOCALE_EN, HomeCarouselSlide.LOCALE_ALL},
    )
    return alt_zh, alt_en


def _carousel_file(file_id):
    if isinstance(file_id, bool):
        raise ValueError("image_file_id must be an integer")
    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        raise ValueError("image_file_id must be an integer") from None
    file_record = File.query.filter_by(
        id=file_id,
        file_type=File.CAROUSEL_IMAGE,
        entity_type="home_carousel",
        status="uploaded",
        is_deleted=False,
    ).first()
    if file_record is None:
        raise ValueError("carousel image is not available")
    return file_record


def _slide_or_404(slide_id, include_deleted=False):
    query = HomeCarouselSlide.query.filter_by(id=slide_id)
    if not include_deleted:
        query = query.filter_by(is_deleted=False)
    return query.first()


@public_bp.get("")
def list_public_carousel():
    locale = request.args.get("locale", HomeCarouselSlide.LOCALE_ZH)
    if locale not in {HomeCarouselSlide.LOCALE_ZH, HomeCarouselSlide.LOCALE_EN}:
        return jsonify({"error": "locale must be zh or en"}), 400

    slides = (
        HomeCarouselSlide.query
        .filter(
            HomeCarouselSlide.is_deleted.is_(False),
            HomeCarouselSlide.is_active.is_(True),
            HomeCarouselSlide.locale.in_([locale, HomeCarouselSlide.LOCALE_ALL]),
        )
        .order_by(HomeCarouselSlide.sort_order.asc(), HomeCarouselSlide.id.asc())
        .all()
    )
    public_slides = [slide.to_public_dict(locale) for slide in slides if slide.image_url]
    response = jsonify({"locale": locale, "slides": public_slides})
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return response


@admin_bp.get("")
@jwt_required()
def list_admin_carousel():
    _admin_user, error = _admin_guard()
    if error:
        return error
    include_archived = request.args.get("include_archived", "true").lower() != "false"
    query = HomeCarouselSlide.query
    if not include_archived:
        query = query.filter(HomeCarouselSlide.is_deleted.is_(False))
    slides = query.order_by(
        HomeCarouselSlide.is_deleted.asc(),
        HomeCarouselSlide.sort_order.asc(),
        HomeCarouselSlide.id.asc(),
    ).all()
    return jsonify({"slides": [slide.to_admin_dict() for slide in slides]})


@admin_bp.post("")
@jwt_required()
def create_admin_carousel_slide():
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    try:
        locale = _clean_locale(payload.get("locale"))
        alt_zh, alt_en = _clean_alt_text(payload, locale)
        href = _clean_href(payload.get("href"))
        file_record = _carousel_file(payload.get("image_file_id"))
        is_active = _clean_bool(payload.get("is_active", True), "is_active")
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400

    next_order = (db.session.query(func.max(HomeCarouselSlide.sort_order)).scalar() or 0) + 10
    slide = HomeCarouselSlide(
        locale=locale,
        image_file_id=file_record.id,
        alt_text_zh=alt_zh,
        alt_text_en=alt_en,
        href=href,
        presentation_variant=HomeCarouselSlide.VARIANT_IMAGE,
        sort_order=next_order,
        is_active=is_active,
        created_by_user_id=admin_user.id,
        updated_by_user_id=admin_user.id,
    )
    db.session.add(slide)
    db.session.flush()
    file_record.entity_id = slide.id
    log_admin_action(
        admin_user,
        "carousel.create",
        "home_carousel_slide",
        slide.id,
        target_label=alt_zh or alt_en,
        metadata={"locale": locale, "image_file_id": file_record.id},
    )
    db.session.commit()
    return jsonify({"slide": slide.to_admin_dict()}), 201


@admin_bp.patch("/<int:slide_id>")
@jwt_required()
def update_admin_carousel_slide(slide_id):
    admin_user, error = _admin_guard()
    if error:
        return error
    slide = _slide_or_404(slide_id)
    if slide is None:
        return jsonify({"error": "Carousel slide not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        locale = _clean_locale(payload.get("locale", slide.locale))
        alt_zh, alt_en = _clean_alt_text(payload, locale, slide)
        href = _clean_href(payload.get("href", slide.href))
        file_record = None
        if "image_file_id" in payload and payload.get("image_file_id") != slide.image_file_id:
            file_record = _carousel_file(payload.get("image_file_id"))
        is_active = (
            _clean_bool(payload["is_active"], "is_active")
            if "is_active" in payload
            else slide.is_active
        )
    except ValueError as validation_error:
        return jsonify({"error": str(validation_error)}), 400

    before = slide.to_admin_dict()
    slide.locale = locale
    slide.alt_text_zh = alt_zh
    slide.alt_text_en = alt_en
    slide.href = href
    if file_record is not None:
        slide.image_file_id = file_record.id
        slide.image_path = None
        file_record.entity_id = slide.id
    slide.is_active = is_active
    slide.updated_by_user_id = admin_user.id
    log_admin_action(
        admin_user,
        "carousel.update",
        "home_carousel_slide",
        slide.id,
        target_label=alt_zh or alt_en,
        metadata={"before": before, "locale": locale},
    )
    db.session.commit()
    return jsonify({"slide": slide.to_admin_dict()})


@admin_bp.post("/reorder")
@jwt_required()
def reorder_admin_carousel_slides():
    admin_user, error = _admin_guard()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    ordered_ids = payload.get("ordered_ids")
    if not isinstance(ordered_ids, list) or not ordered_ids:
        return jsonify({"error": "ordered_ids must be a non-empty list"}), 400
    if any(isinstance(slide_id, bool) for slide_id in ordered_ids):
        return jsonify({"error": "ordered_ids must contain integers"}), 400
    try:
        ordered_ids = [int(slide_id) for slide_id in ordered_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "ordered_ids must contain integers"}), 400
    if len(set(ordered_ids)) != len(ordered_ids):
        return jsonify({"error": "ordered_ids must not contain duplicates"}), 400

    slides = HomeCarouselSlide.query.filter(HomeCarouselSlide.is_deleted.is_(False)).all()
    slides_by_id = {slide.id: slide for slide in slides}
    if set(ordered_ids) != set(slides_by_id):
        return jsonify({"error": "ordered_ids must include every non-archived slide"}), 400
    for index, slide_id in enumerate(ordered_ids, start=1):
        slide = slides_by_id[slide_id]
        slide.sort_order = index * 10
        slide.updated_by_user_id = admin_user.id
    log_admin_action(
        admin_user,
        "carousel.reorder",
        "home_carousel_slide",
        metadata={"ordered_ids": ordered_ids},
    )
    db.session.commit()
    ordered = [slides_by_id[slide_id].to_admin_dict() for slide_id in ordered_ids]
    return jsonify({"slides": ordered})


def _set_archived(slide_id, archived):
    admin_user, error = _admin_guard()
    if error:
        return error
    slide = _slide_or_404(slide_id, include_deleted=True)
    if slide is None:
        return jsonify({"error": "Carousel slide not found"}), 404
    if slide.is_deleted == archived:
        return jsonify({"slide": slide.to_admin_dict()})
    slide.is_deleted = archived
    slide.deleted_at = datetime.now(timezone.utc) if archived else None
    slide.deleted_by_user_id = admin_user.id if archived else None
    slide.updated_by_user_id = admin_user.id
    action = "carousel.archive" if archived else "carousel.restore"
    log_admin_action(
        admin_user,
        action,
        "home_carousel_slide",
        slide.id,
        target_label=slide.alt_text_zh or slide.alt_text_en,
    )
    db.session.commit()
    return jsonify({"slide": slide.to_admin_dict()})


@admin_bp.post("/<int:slide_id>/archive")
@jwt_required()
def archive_admin_carousel_slide(slide_id):
    return _set_archived(slide_id, True)


@admin_bp.post("/<int:slide_id>/restore")
@jwt_required()
def restore_admin_carousel_slide(slide_id):
    return _set_archived(slide_id, False)
