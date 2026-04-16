"""
Backfill historical course reviews so they are organized by offering.

Rules:
- Any historical post that already looks course-related (plain course tag or
  legacy ``courseCode-2025fall`` tag) is treated as a course review.
- Add a standalone display offering tag such as ``25-26Fall``.
- Add the plain course code tag if it is missing.
- Add the hidden system tag ``course-review`` so offering review pages can
  distinguish reviews from discussion posts.
- Prefer explicit legacy semester tags; fall back to created_at windows:
  Feb-Jul -> previous academic year's Spring
  Aug-Jan -> current academic year's Fall (Jan belongs to the previous fall).

Usage:
    python -m app.scripts.backfill_course_review_offerings --dry-run
    python -m app.scripts.backfill_course_review_offerings
"""

from __future__ import annotations

import argparse

from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.post import Post
from app.models.tag import Tag, TagType
from app.utils.semester import (
    format_offering_display_tag,
    infer_offering_from_datetime,
    parse_offering_display_tag,
    parse_semester_tag,
)

SYSTEM_REVIEW_TAG = "course-review"


def _get_or_create_tag_type(type_name: str) -> TagType:
    tag_type = TagType.query.filter_by(name=type_name).first()
    if not tag_type:
        tag_type = TagType(name=type_name)
        db.session.add(tag_type)
        db.session.flush()
    return tag_type


def _get_or_create_tag(tag_name: str, type_name: str, description: str) -> Tag:
    existing = Tag.query.filter_by(name=tag_name).first()
    if existing:
        return existing

    tag_type = _get_or_create_tag_type(type_name)
    tag = Tag(name=tag_name, tag_type_id=tag_type.id, description=description)
    db.session.add(tag)
    db.session.flush()
    return tag


def _resolve_course_code(post: Post) -> str | None:
    for tag in post.tags:
        if Course.query.filter_by(code=tag.name, is_deleted=False).first():
            return tag.name

    for tag in post.tags:
        parsed = parse_semester_tag(tag.name)
        if parsed:
            course_code, _, _ = parsed
            return course_code

    return None


def _resolve_offering_tag(post: Post) -> str:
    for tag in post.tags:
        if parse_offering_display_tag(tag.name):
            return tag.name

    for tag in post.tags:
        parsed = parse_semester_tag(tag.name)
        if parsed:
            _, year, semester_code = parsed
            return format_offering_display_tag(year, semester_code)

    year, semester_code = infer_offering_from_datetime(post.created_at)
    return format_offering_display_tag(year, semester_code)


def _looks_course_related(post: Post) -> bool:
    if any(tag.name == SYSTEM_REVIEW_TAG for tag in post.tags):
        return True

    if any(Course.query.filter_by(code=tag.name, is_deleted=False).first() for tag in post.tags):
        return True

    return any(parse_semester_tag(tag.name) for tag in post.tags)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill offering tags for historical course reviews.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without committing them.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        changed_posts = 0

        for post in Post.query.filter_by(is_deleted=False).all():
            if not _looks_course_related(post):
                continue

            course_code = _resolve_course_code(post)
            if not course_code:
                continue

            offering_tag = _resolve_offering_tag(post)

            desired_tags = [
                _get_or_create_tag(course_code, TagType.COURSE, f"Course: {course_code}"),
                _get_or_create_tag(offering_tag, TagType.SYSTEM, f"Offering tag: {offering_tag}"),
                _get_or_create_tag(SYSTEM_REVIEW_TAG, TagType.SYSTEM, "System tag for course review posts"),
            ]

            added_any = False
            for desired_tag in desired_tags:
                if desired_tag not in post.tags:
                    post.tags.append(desired_tag)
                    added_any = True

            if added_any:
                changed_posts += 1
                print(f"[update] post {post.id}: + {course_code}, {offering_tag}, {SYSTEM_REVIEW_TAG}")

        if args.dry_run:
            db.session.rollback()
            print(f"Dry run complete. Would update {changed_posts} post(s).")
            return

        db.session.commit()
        print(f"Done. Updated {changed_posts} post(s).")


if __name__ == "__main__":
    main()
