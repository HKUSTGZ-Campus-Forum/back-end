"""
为「25-26春」补学期标签：从已有「25-26秋」对应的课程标签复制一份。

说明：
- 界面上「25-26秋」来自标签解析为 calendar year=2025、season=fall（学期键 2025fall）。
- 「25-26春」对应标签 ``2025spring``（与「25-26秋」的学年起算年一致），与 format_academic_year_semester_display 一致。

本脚本不为课程表新增 courses 行，只为在 25-26 秋季已打标的每门课增加一条 COURSE 学期标签，
使筛选与发帖可选「25-26春」。

**生产环境无需跑本脚本**：`app/__init__.py` 在应用启动时会自动执行相同逻辑（幂等）。
本地调试可选用法（在 back-end 目录、已配置数据库环境变量）::

    python -m app.scripts.clone_semester_25_26_spring_from_fall
    python -m app.scripts.clone_semester_25_26_spring_from_fall --dry-run
"""

from __future__ import annotations

import argparse

from app import create_app
from app.models.course import Course
from app.models.tag import Tag
from app.utils.semester import parse_semester_tag

# 源：25-26 秋（与 /api/courses/filters 中 semester_key 2025fall 一致）
SOURCE_YEAR = "2025"
SOURCE_SEASON = "fall"
# 目标：25-26 春 → 标签后缀 2025spring（与 2025fall 同学年起算）
TARGET_TAG_SUFFIX = "2025spring"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone 25-26 fall course tags to 25-26 spring.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print how many tags would be created; no database writes.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        course_tags = Course.get_course_tags()
        seen_codes: set[str] = set()
        to_create: list[str] = []

        for tag in course_tags:
            parsed = parse_semester_tag(tag.name)
            if not parsed:
                continue
            code, year, season = parsed
            if year != SOURCE_YEAR or season != SOURCE_SEASON:
                continue
            if code in seen_codes:
                continue

            course = Course.query.filter_by(code=code, is_deleted=False).first()
            if not course:
                print(f"[skip] no active course row for code={code!r} (tag {tag.name!r})")
                continue

            new_name = f"{course.code}-{TARGET_TAG_SUFFIX}"
            if Tag.query.filter_by(name=new_name).first():
                print(f"[skip] already exists: {new_name}")
                seen_codes.add(code)
                continue

            seen_codes.add(code)
            to_create.append(course.code)

        print(f"Courses with {SOURCE_YEAR}{SOURCE_SEASON} tags: {len(seen_codes)}")
        print(f"New {TARGET_TAG_SUFFIX} tags to add: {len(to_create)}")

        if args.dry_run:
            for c in sorted(to_create):
                print(f"  would create: {c}-{TARGET_TAG_SUFFIX}")
            return

        created = 0
        for code in to_create:
            course = Course.query.filter_by(code=code, is_deleted=False).first()
            if not course:
                continue
            course.create_semester_tag(TARGET_TAG_SUFFIX)
            created += 1
            if created % 50 == 0:
                print(f"  ... created {created}")

        print(f"Done. Created {created} tag(s).")


if __name__ == "__main__":
    main()
