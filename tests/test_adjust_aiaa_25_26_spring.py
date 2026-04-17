import pytest
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.post import Post
from app.models.tag import Tag, TagType, post_tags
from app.models.user import User
from app.models.user_role import UserRole
from app.scripts.adjust_aiaa_25_26_spring import apply_aiaa_25_26_spring_adjustments


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["JWT_SECRET_KEY"] = "test-secret"

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


def _get_or_create_course_type() -> TagType:
    course_type = TagType.get_course_type()
    if course_type:
        return course_type
    course_type = TagType(name=TagType.COURSE)
    db.session.add(course_type)
    db.session.flush()
    return course_type


def _create_course(code: str, name: str, credits: int) -> Course:
    course = Course(code=code, name=name, credits=credits, is_active=True, is_deleted=False)
    db.session.add(course)
    db.session.flush()
    return course


def _create_course_tag(name: str) -> Tag:
    course_type = _get_or_create_course_type()
    tag = Tag(name=name, tag_type_id=course_type.id, description=f"Tag {name}")
    db.session.add(tag)
    db.session.flush()
    return tag


def _create_user() -> User:
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if not role:
        role = UserRole(name=UserRole.USER)
        db.session.add(role)
        db.session.flush()

    user = User(username="tester", email="tester@example.com", role_id=role.id)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    return user


def test_apply_aiaa_adjustments_syncs_spring_from_fall_and_normalizes_tags(app):
    with app.app_context():
        _create_course("AIAA2205", "Intro to AI", 3)
        _create_course_tag("AIAA2205-2025fall")

        _create_course("AIAA2290", "Removed Course", 3)
        _create_course_tag("AIAA2290-2025fall")
        _create_course_tag("AIAA2290-2025spring")

        _create_course("AIAA9999", "Stale Spring Course", 2)
        _create_course_tag("AIAA9999-2025spring")

        addition_course = _create_course("AIAA5033", "Old Name", 1)
        spaced_spring_tag = _create_course_tag("AIAA 5033-2025spring")

        user = _create_user()
        post = Post(user_id=user.id, title="review", content="content")
        db.session.add(post)
        db.session.flush()
        db.session.execute(post_tags.insert().values(post_id=post.id, tag_id=spaced_spring_tag.id))
        db.session.commit()

        apply_aiaa_25_26_spring_adjustments(dry_run=False, verbose=False)

        spring_tag_names = {tag.name for tag in Tag.query.order_by(Tag.name).all()}
        assert "AIAA2205-2025spring" in spring_tag_names
        assert "AIAA2290-2025spring" not in spring_tag_names
        assert "AIAA9999-2025spring" not in spring_tag_names
        assert "AIAA5033-2025spring" in spring_tag_names
        assert "AIAA 5033-2025spring" not in spring_tag_names

        refreshed_course = Course.query.filter_by(code=addition_course.code).first()
        assert refreshed_course is not None
        assert refreshed_course.name == "AI Security and Privacy"
        assert refreshed_course.credits == 3

        linked_tag_ids = {
            tag_id
            for (tag_id,) in db.session.execute(
                select(post_tags.c.tag_id).where(post_tags.c.post_id == post.id)
            ).all()
        }
        compact_spring_tag = Tag.query.filter_by(name="AIAA5033-2025spring").first()
        assert compact_spring_tag is not None
        assert linked_tag_ids == {compact_spring_tag.id}
