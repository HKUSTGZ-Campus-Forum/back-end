"""
「25-26 春」UFUG 课表调整（学期标签后缀 ``2025spring``）。

- 从该学期移除：UFUG 1102 / 1105 / 1801 / 2101（兼容代码带或不带空格、多种可解析的春季后缀）。
- 确保存在并打上 ``2025spring`` 标签：UFUG 1403、2106、2602。

默认由 ``app/__init__.py`` 在应用启动时幂等调用（``apply_ufug_25_26_spring_adjustments``），
自部署无需在容器内执行 ``python -m``。

若本地仍要试跑或查看计划，可在已配置数据库的环境下执行::

    python -m app.scripts.adjust_ufug_25_26_spring --dry-run
    python -m app.scripts.adjust_ufug_25_26_spring
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

REMOVE_UFUG_NUMBERS = frozenset({1102, 1105, 1801, 2101})

NEW_COURSES: tuple[tuple[str, str, int], ...] = (
    ("UFUG 1403", "Introduction to Biotechnology", 3),
    ("UFUG 2106", "Discrete Mathematics", 3),
    ("UFUG 2602", "Data Structure and Algorithm Design", 3),
)


def _parse_ufug_number(code: str) -> int | None:
    s = code.replace(" ", "").strip().upper()
    m = re.match(r"^UFUG(\d+)$", s)
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


def apply_ufug_25_26_spring_adjustments(*, dry_run: bool = False, verbose: bool = False) -> None:
    """
    幂等：执行 UFUG 25-26 春季调整。需在 Flask app_context 内调用。

    :param dry_run: 为 True 时不写入数据库。
    :param verbose: 为 True 时打印计划/结果（CLI）；启动时传 False 保持安静。
    """
    log = print if verbose else (lambda *_a, **_k: None)

    course_type = TagType.get_course_type()
    if not course_type:
        log("No COURSE tag type in DB; skip UFUG spring adjustment.")
        return

    all_course_tags = Tag.query.filter(Tag.tag_type_id == course_type.id).all()

    to_remove: list[Tag] = []
    for tag, code in _iter_spring_25_course_tags(all_course_tags):
        n = _parse_ufug_number(code)
        if n is not None and n in REMOVE_UFUG_NUMBERS:
            to_remove.append(tag)

    log(f"Spring {TARGET_SPRING_YEAR} tags to remove: {len(to_remove)}")
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
    parser = argparse.ArgumentParser(description="Adjust UFUG courses for 25-26 Spring.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; no writes.")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        apply_ufug_25_26_spring_adjustments(dry_run=args.dry_run, verbose=True)


if __name__ == "__main__":
    main()
