"""
【已废弃应用内自动执行】历史上用于从「25-26 秋」复制学期标签到「25-26 春」。

秋课与春课并不相同，不应再依赖此逻辑；应用启动时也不再运行等价操作。

若你仍需要一次性手动从秋季标签批量生成春季标签，可在本地/运维环境执行（慎用）::

    python -m app.scripts.clone_semester_25_26_spring_from_fall
    python -m app.scripts.clone_semester_25_26_spring_from_fall --dry-run
"""

from __future__ import annotations

import argparse

from app import create_app
from app.models.course import Course
from app.models.tag import Tag
from app.utils.semester import parse_semester_tag

SOURCE_YEAR = "2025"
SOURCE_SEASON = "fall"
TARGET_TAG_SUFFIX = "2025spring"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone 25-26 fall course tags to 25-26 spring (manual only).")
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
