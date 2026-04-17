"""
「25-26 春」UCUG 课表调整（学期标签后缀 ``2025spring``）。

- 从该学期移除：UCUG 1050 / 1051 / 1070 / 1502（兼容 ``UCUG1050`` / ``UCUG 1050`` 等写法）。
- 确保存在并打上 ``2025spring`` 标签：1052A/I/S、1053、1501、1503、1604、1808、1903、2500 等（见 ``NEW_COURSES``）。

由 ``app/__init__.py`` 在启动时幂等调用；也可本地::

    python -m app.scripts.adjust_ucug_25_26_spring --dry-run
    python -m app.scripts.adjust_ucug_25_26_spring
"""

from __future__ import annotations

import argparse
import re
from typing import Iterable

from sqlalchemy import delete

from app.extensions import db
from app.models.course import Course
from app.models.tag import Tag, TagType, post_tags
from app.utils.semester import parse_semester_tag

TARGET_SPRING_YEAR = "2025"
TARGET_SPRING_SEASON = "spring"
TAG_SUFFIX = "2025spring"

REMOVE_UCUG_NUMBERS = frozenset({1050, 1051, 1070, 1502})

NEW_COURSES: tuple[tuple[str, str, int], ...] = (
    ("UCUG 1052A", "Academic English for University Studies", 3),
    ("UCUG 1052I", "Academic English for University Studies", 3),
    ("UCUG 1052S", "Academic English for University Studies", 3),
    ("UCUG 1053", "Advanced Academic English for University Studies", 3),
    ("UCUG 1501", "Introduction to Photography and Digital Imaging", 3),
    ("UCUG 1503", "Introduction to Experimental Animation", 3),
    ("UCUG 1604", "History of Chinese Civilization: From Antiquity to Late Imperial", 3),
    ("UCUG 1808", "Sport in Society: Cultures, Media and Politics", 3),
    ("UCUG 1903", "Signals and Information Technology", 3),
    ("UCUG 2500", "Music of China", 3),
)


def _parse_ucug_catalog_number(code: str) -> int | None:
    """仅匹配课号为纯数字的 UCUG（如 1050）；不含 1052A 这类带字母后缀。"""
    s = code.replace(" ", "").strip().upper()
    m = re.match(r"^UCUG(\d+)$", s)
    if not m:
        return None
    return int(m.group(1))


def _iter_spring_25_course_tags(tags: Iterable[Tag]):
    for tag in tags:
        parsed = parse_semester_tag(tag.name)
        if not parsed:
            continue
        code, year, season = parsed
        if year == TARGET_SPRING_YEAR and season == TARGET_SPRING_SEASON:
            yield tag, code


def _unlink_tag_from_posts(tag_id: int) -> int:
    r = db.session.execute(delete(post_tags).where(post_tags.c.tag_id == tag_id))
    return r.rowcount or 0


def apply_ucug_25_26_spring_adjustments(*, dry_run: bool = False, verbose: bool = False) -> None:
    """
    幂等：执行 UCUG 25-26 春季调整。需在 Flask app_context 内调用。
    """
    log = print if verbose else (lambda *_a, **_k: None)

    course_type = TagType.get_course_type()
    if not course_type:
        log("No COURSE tag type in DB; skip UCUG spring adjustment.")
        return

    all_course_tags = Tag.query.filter(Tag.tag_type_id == course_type.id).all()

    to_remove: list[Tag] = []
    for tag, code in _iter_spring_25_course_tags(all_course_tags):
        n = _parse_ucug_catalog_number(code)
        if n is not None and n in REMOVE_UCUG_NUMBERS:
            to_remove.append(tag)

    log(f"Spring {TARGET_SPRING_YEAR} UCUG tags to remove: {len(to_remove)}")
    for t in sorted(to_remove, key=lambda x: x.name):
        log(f"  - {t.name}")

    log("Courses to upsert + ensure tag " + TAG_SUFFIX + ":")
    for code, name, units in NEW_COURSES:
        log(f"  + {code} | {name} | {units} units")

    if dry_run:
        return

    unlinked = 0
    for tag in to_remove:
        unlinked += _unlink_tag_from_posts(tag.id)
        db.session.delete(tag)
    if to_remove:
        log(f"Removed {len(to_remove)} semester tag(s); unlinked {unlinked} post_tag row(s).")

    for code, name, credits in NEW_COURSES:
        course = Course.query.filter_by(code=code, is_deleted=False).first()
        if course:
            course.name = name
            course.credits = credits
            course.is_active = True
        else:
            course = Course(
                code=code,
                name=name,
                credits=credits,
                is_active=True,
                is_deleted=False,
            )
            db.session.add(course)
            db.session.flush()

        tag_name = f"{course.code}-{TAG_SUFFIX}"
        if Tag.query.filter_by(name=tag_name).first():
            log(f"[skip] tag exists: {tag_name}")
            continue
        db.session.add(
            Tag(
                name=tag_name,
                tag_type_id=course_type.id,
                description=f"Tag for {course.name} ({TAG_SUFFIX})",
            )
        )
        log(f"[ok] {tag_name}")

    db.session.commit()
    log("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Adjust UCUG courses for 25-26 Spring.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; no writes.")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        apply_ucug_25_26_spring_adjustments(dry_run=args.dry_run, verbose=True)


if __name__ == "__main__":
    main()
