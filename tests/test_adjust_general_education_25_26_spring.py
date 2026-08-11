import pytest

from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.tag import Tag, TagType
from app.scripts.adjust_ucug_25_26_spring import (
    NEW_COURSES as UCUG_NEW_COURSES,
    apply_ucug_25_26_spring_adjustments,
)
from app.scripts.adjust_ufug_25_26_spring import (
    NEW_COURSES as UFUG_NEW_COURSES,
    apply_ufug_25_26_spring_adjustments,
)
from app.services.course_domain import normalize_course_code


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = "test-secret"
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False


@pytest.fixture
def app():
    app = create_app(TestConfig)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.mark.parametrize(
    ("apply_adjustments", "course_specs", "existing_code"),
    (
        (apply_ufug_25_26_spring_adjustments, UFUG_NEW_COURSES, "UFUG1403"),
        (apply_ucug_25_26_spring_adjustments, UCUG_NEW_COURSES, "UCUG1052A"),
    ),
)
def test_adjustments_reuse_compact_courses_and_create_canonical_rows_and_tags(
    app,
    apply_adjustments,
    course_specs,
    existing_code,
):
    with app.app_context():
        course_type = TagType(name=TagType.COURSE)
        existing_course = Course(
            code=existing_code,
            normalized_code=existing_code,
            name="Old title",
            credits=1,
            is_active=True,
            is_deleted=False,
        )
        db.session.add_all([course_type, existing_course])
        db.session.commit()
        existing_course_id = existing_course.id

        apply_adjustments(dry_run=False, verbose=False)
        apply_adjustments(dry_run=False, verbose=False)

        matching_existing = [
            course
            for course in Course.query.all()
            if normalize_course_code(course.code) == existing_code
        ]
        assert [course.id for course in matching_existing] == [existing_course_id]

        for source_code, _name, _credits in course_specs:
            canonical_code = normalize_course_code(source_code)
            course = Course.query.filter_by(code=canonical_code).one()
            assert course.normalized_code == canonical_code
            assert Tag.query.filter_by(name=f"{canonical_code}-2025spring").one()
            assert Tag.query.filter_by(name=f"{source_code}-2025spring").first() is None
