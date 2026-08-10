import json

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.tag import Tag
from app.scripts.import_courses import (
    CourseImportValidationError,
    import_courses_from_file,
)


@compiles(JSONB, 'sqlite')
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return 'JSON'


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    CACHE_TYPE = 'SimpleCache'
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = 'test-secret'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _write_payload(tmp_path, rows):
    path = tmp_path / 'courses_2610.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    return path


def test_legacy_import_resolves_spaced_identity_and_normalizes_new_rows(app, tmp_path):
    path = _write_payload(tmp_path, [
        {'course_code': 'TEST1001', 'name': 'Updated title', 'unit': 3},
        {'course_code': 'NEWC 2001', 'name': 'New course', 'unit': 2},
    ])

    with app.app_context():
        legacy = Course(code='TEST 1001', name='Legacy title', credits=1)
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id
        before_count = Course.query.count()

        imported, skipped = import_courses_from_file(path, db.session)

        assert (imported, skipped) == (2, 0)
        assert Course.query.count() == before_count + 1
        resolved = db.session.get(Course, legacy_id)
        assert resolved.code == 'TEST 1001'
        assert resolved.normalized_code == 'TEST1001'
        assert resolved.name == 'Updated title'
        created = Course.query.filter_by(code='NEWC2001').one()
        assert created.normalized_code == 'NEWC2001'
        assert Tag.query.filter_by(name='NEWC2001-26Fall').one()


def test_legacy_import_rejects_ambiguous_existing_normalized_identity(app, tmp_path):
    path = _write_payload(tmp_path, [
        {'course_code': 'TEST1001', 'name': 'Imported title', 'unit': 3},
    ])

    with app.app_context():
        db.session.add_all([
            Course(
                code='TEST1001',
                normalized_code='TEST1001',
                name='Canonical',
                credits=3,
            ),
            Course(code='TEST 1001', name='Legacy duplicate', credits=3),
        ])
        db.session.commit()

        with pytest.raises(
            CourseImportValidationError,
            match='ambiguous existing course rows',
        ):
            import_courses_from_file(path, db.session)

        assert Course.query.filter_by(name='Imported title').count() == 0
        assert Course.query.filter(
            Course.code.in_(['TEST1001', 'TEST 1001'])
        ).count() == 2


def test_legacy_import_rejects_source_course_aliases_before_writing(app, tmp_path):
    path = _write_payload(tmp_path, [
        {'course_code': 'TEST1001', 'name': 'Canonical', 'unit': 3},
        {'course_code': 'TEST 1001', 'name': 'Alias', 'unit': 3},
    ])

    with app.app_context():
        before_count = Course.query.count()
        with pytest.raises(
            CourseImportValidationError,
            match='duplicate normalized course identity',
        ):
            import_courses_from_file(path, db.session)

        assert Course.query.count() == before_count


def test_legacy_import_rejects_inconsistent_existing_identity(app, tmp_path):
    path = _write_payload(tmp_path, [
        {'course_code': 'TEST1001', 'name': 'Imported title', 'unit': 3},
    ])

    with app.app_context():
        db.session.add(Course(
            code='WRNG1001',
            normalized_code='TEST1001',
            name='Inconsistent',
            credits=3,
        ))
        db.session.commit()

        with pytest.raises(
            CourseImportValidationError,
            match='normalization is inconsistent',
        ):
            import_courses_from_file(path, db.session)

        assert Course.query.filter_by(name='Imported title').count() == 0
        assert Course.query.filter_by(code='WRNG1001').count() == 1
