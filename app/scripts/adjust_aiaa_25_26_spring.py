"""
「25-26 春」AIAA 课表同步（学期标签后缀 ``2025spring``）。

这次修改表达的是：

- 以「25-26 秋」AIAA 开课集合为基线；
- 从春季集合里移除：2290、3102、3111、3225、4220、5030、5031、5072、5088、6102；
- 再补上 ``NEW_COURSES`` 中新增/修正的课程；
- 最终把数据库中的「25-26 春」AIAA tag 同步到这个目标状态。

与早期“先 clone 春季，再额外打补丁”的方案相比，这个脚本是自包含的：
即使目标环境里从未手动跑过 spring clone，它也能根据 25-26 秋自动推导春季 AIAA 集合。
另外它会兼容 ``AIAA2711`` / ``AIAA 2711`` 这类历史 code 变体，确保课程行与 spring tag 对齐，
避免前端按 semester 过滤时因为 code 不一致而“看起来没改”。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, func, select

from app.extensions import db
from app.models.course import Course
from app.models.tag import Tag, TagType, post_tags
from app.utils.semester import parse_semester_tag

SOURCE_YEAR = "2025"
SOURCE_SEASON = "fall"
TARGET_SPRING_YEAR = "2025"
TARGET_SPRING_SEASON = "spring"
TAG_SUFFIX = "2025spring"

REMOVE_AIAA_NUMBERS = frozenset({2290, 3102, 3111, 3225, 4220, 5030, 5031, 5072, 5088, 6102})

NEW_COURSES: tuple[tuple[str, str, int], ...] = (
    ("AIAA 2711", "Mathematics for AI", 3),
    ("AIAA 3053", "Reinforcement Learning: Principles and Methods", 3),
    ("AIAA 3201", "Introduction to Computer Vision", 3),
    ("AIAA 4051", "Introduction to Natural Language Processing", 3),
    ("AIAA 4641", "Social Information Network Analysis and Engineering", 3),
    ("AIAA 4711", "Introduction to Advanced Algorithmic Techniques", 3),
    ("AIAA 5026", "Computer Vision and Its Applications", 3),
    ("AIAA 5033", "AI Security and Privacy", 3),
    ("AIAA 5048", "Multimodal Artificial Intelligence", 3),
    ("AIAA 5050", "Machine Consciousness", 3),
    ("AIAA 6091A", "Independent Study", 3),
    ("AIAA 6091B", "Independent Study", 1),
    ("AIAA 6101", "Artificial Intelligence Seminar I", 0),
    ("AIAA 6990", "MPhil Thesis Research", 0),
    ("AIAA 7990", "Doctoral Thesis Research", 0),
)

_AIAA_CODE_RE = re.compile(r"^AIAA\s*([0-9]+[A-Z]?)$", re.IGNORECASE)


@dataclass
class DesiredCourse:
    logical_code: str
    preferred_code: str
    name: str
    credits: int


def _normalize_aiaa_code(code: str | None) -> str | None:
    if not code or not isinstance(code, str):
        return None
    match = _AIAA_CODE_RE.match(code.strip().upper())
    if not match:
        return None
    return f"AIAA{match.group(1)}"


def _parse_aiaa_catalog_number(code: str) -> int | None:
    logical = _normalize_aiaa_code(code)
    if not logical:
        return None
    suffix = logical[4:]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _iter_aiaa_course_tags(tags: Iterable[Tag], *, year: str, season: str):
    for tag in tags:
        parsed = parse_semester_tag(tag.name)
        if not parsed:
            continue
        code, parsed_year, parsed_season = parsed
        logical = _normalize_aiaa_code(code)
        if not logical:
            continue
        if parsed_year == year and parsed_season == season:
            yield tag, code, logical


def _unlink_tag_from_posts(tag_id: int) -> int:
    result = db.session.execute(delete(post_tags).where(post_tags.c.tag_id == tag_id))
    return result.rowcount or 0


def _retarget_post_links(old_tag_id: int, new_tag_id: int) -> int:
    if old_tag_id == new_tag_id:
        return 0

    moved = 0
    post_ids = [
        post_id
        for (post_id,) in db.session.execute(
            select(post_tags.c.post_id).where(post_tags.c.tag_id == old_tag_id)
        ).all()
    ]

    for post_id in post_ids:
        exists = db.session.execute(
            select(post_tags.c.post_id).where(
                post_tags.c.post_id == post_id,
                post_tags.c.tag_id == new_tag_id,
            )
        ).first()
        if exists:
            continue
        db.session.execute(post_tags.insert().values(post_id=post_id, tag_id=new_tag_id))
        moved += 1

    db.session.execute(delete(post_tags).where(post_tags.c.tag_id == old_tag_id))
    return moved


def _query_courses_by_logical_code(logical_code: str) -> list[Course]:
    return (
        Course.query.filter(
            func.replace(func.upper(Course.code), " ", "") == logical_code
        )
        .order_by(Course.is_deleted.asc(), Course.id.asc())
        .all()
    )


def _resolve_course_for_target(
    spec: DesiredCourse,
    *,
    prefer_exact_code: str | None = None,
) -> Course:
    candidates = _query_courses_by_logical_code(spec.logical_code)

    preferred_codes = [code for code in (prefer_exact_code, spec.preferred_code) if code]

    for preferred_code in preferred_codes:
        for course in candidates:
            if course.code == preferred_code:
                course.is_deleted = False
                course.is_active = True
                course.name = spec.name
                course.credits = spec.credits
                return course

    for course in candidates:
        if not course.is_deleted:
            course.is_active = True
            course.name = spec.name
            course.credits = spec.credits
            return course

    if candidates:
        course = candidates[0]
        course.is_deleted = False
        course.deleted_at = None
        course.is_active = True
        course.name = spec.name
        course.credits = spec.credits
        return course

    course = Course(
        code=spec.preferred_code,
        name=spec.name,
        credits=spec.credits,
        is_active=True,
        is_deleted=False,
    )
    db.session.add(course)
    db.session.flush()
    return course


def _build_desired_courses(all_course_tags: list[Tag], log) -> dict[str, DesiredCourse]:
    desired: dict[str, DesiredCourse] = {}

    for _tag, code, logical in _iter_aiaa_course_tags(
        all_course_tags, year=SOURCE_YEAR, season=SOURCE_SEASON
    ):
        number = _parse_aiaa_catalog_number(code)
        if number is not None and number in REMOVE_AIAA_NUMBERS:
            continue

        fall_course = Course.query.filter_by(code=code, is_deleted=False).first()
        if not fall_course:
            candidates = _query_courses_by_logical_code(logical)
            fall_course = next((course for course in candidates if not course.is_deleted), None)

        if not fall_course:
            log(f"[skip] no course row found for fall AIAA tag {code}-{SOURCE_YEAR}{SOURCE_SEASON}")
            continue

        desired[logical] = DesiredCourse(
            logical_code=logical,
            preferred_code=fall_course.code,
            name=fall_course.name,
            credits=fall_course.credits,
        )

    for code, name, credits in NEW_COURSES:
        logical = _normalize_aiaa_code(code)
        if not logical:
            continue
        base_code = desired[logical].preferred_code if logical in desired else code
        desired[logical] = DesiredCourse(
            logical_code=logical,
            preferred_code=base_code,
            name=name,
            credits=credits,
        )

    return desired


def apply_aiaa_25_26_spring_adjustments(*, dry_run: bool = False, verbose: bool = False) -> None:
    """幂等：同步 AIAA 25-26 春季课程集合。需在 Flask app_context 内调用。"""
    log = print if verbose else (lambda *_a, **_k: None)

    course_type = TagType.get_course_type()
    if not course_type:
        log("No COURSE tag type in DB; skip AIAA spring adjustment.")
        return

    all_course_tags = Tag.query.filter(Tag.tag_type_id == course_type.id).all()
    desired_courses = _build_desired_courses(all_course_tags, log)

    spring_tags_by_logical: dict[str, list[Tag]] = {}
    for tag, code, logical in _iter_aiaa_course_tags(
        all_course_tags, year=TARGET_SPRING_YEAR, season=TARGET_SPRING_SEASON
    ):
        spring_tags_by_logical.setdefault(logical, []).append(tag)

    desired_keys = set(desired_courses.keys())
    tags_to_remove: list[Tag] = []
    for logical, tags in spring_tags_by_logical.items():
        if logical in desired_keys:
            continue
        tags_to_remove.extend(tags)

    log(f"AIAA {TAG_SUFFIX} target course count: {len(desired_courses)}")
    log(f"Existing spring AIAA tags: {sum(len(tags) for tags in spring_tags_by_logical.values())}")
    log(f"Spring tags to delete because not in final set: {len(tags_to_remove)}")
    for tag in sorted(tags_to_remove, key=lambda item: item.name):
        log(f"  - {tag.name}")

    if dry_run:
        for logical, spec in sorted(desired_courses.items()):
            log(f"  keep/create: {logical} -> {spec.preferred_code}-{TAG_SUFFIX}")
        return

    removed_post_links = 0
    for tag in tags_to_remove:
        removed_post_links += _unlink_tag_from_posts(tag.id)
        db.session.delete(tag)

    aligned_tags = 0
    retargeted_post_links = 0
    created_tags = 0
    created_courses = 0

    for logical, spec in sorted(desired_courses.items()):
        spring_tags = spring_tags_by_logical.get(logical, [])
        prefer_exact_code = None
        if spring_tags:
            prefer_exact_code = parse_semester_tag(spring_tags[0].name)[0]

        existing_candidates = _query_courses_by_logical_code(logical)
        had_any_course = bool(existing_candidates)
        course = _resolve_course_for_target(spec, prefer_exact_code=prefer_exact_code)
        if not had_any_course:
            created_courses += 1

        desired_tag_name = f"{course.code}-{TAG_SUFFIX}"
        target_tag = Tag.query.filter_by(name=desired_tag_name).first()
        if not target_tag:
            target_tag = Tag(
                name=desired_tag_name,
                tag_type_id=course_type.id,
                description=f"Tag for {course.name} ({TAG_SUFFIX})",
            )
            db.session.add(target_tag)
            db.session.flush()
            created_tags += 1

        for tag in spring_tags:
            if tag.id == target_tag.id:
                continue
            retargeted_post_links += _retarget_post_links(tag.id, target_tag.id)
            db.session.delete(tag)
            aligned_tags += 1

        log(f"[sync] {logical} -> {desired_tag_name}")

    db.session.commit()
    log(
        "Done. "
        f"created_courses={created_courses}, created_tags={created_tags}, "
        f"deleted_tags={len(tags_to_remove)}, aligned_tags={aligned_tags}, "
        f"unlinked_post_tags={removed_post_links}, retargeted_post_tags={retargeted_post_links}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync AIAA courses for 25-26 Spring.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; no writes.")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        apply_aiaa_25_26_spring_adjustments(dry_run=args.dry_run, verbose=True)


if __name__ == "__main__":
    main()
