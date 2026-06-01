import pytest
import app.scripts.migrate_scheduler_data as importer
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.scheduler_section import SchedulerSection
from app.scripts.migrate_scheduler_data import SnapshotValidationError, import_snapshot


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


def source_connection():
    engine = create_engine('sqlite:///:memory:')
    conn = engine.connect()
    for statement in [
        'CREATE TABLE course (course_code TEXT, course_title TEXT, course_title_abbr TEXT, '
        'course_desc TEXT, pre_requirement TEXT, co_requirement TEXT, exclusion TEXT, credit INTEGER, '
        'subject TEXT, catalog_number TEXT, pg_course BOOLEAN, klms_course BOOLEAN, vector TEXT)',
        'CREATE TABLE section (semester_id TEXT, section_id TEXT, course_code TEXT, name TEXT, '
        'bundle INTEGER, layer INTEGER, quota INTEGER, section_type TEXT, is_main BOOLEAN)',
        'CREATE TABLE lecture (semester_id TEXT, section_id TEXT, day INTEGER, start_time INTEGER, '
        'end_time INTEGER, room TEXT, instructor TEXT)',
        'CREATE TABLE map_component (id TEXT, node_type BOOLEAN, x_coordinate INTEGER, '
        'y_coordinate INTEGER, category INTEGER)',
        'CREATE TABLE map_line (start_id TEXT, end_id TEXT, line_type BOOLEAN, '
        'x_coordinate INTEGER, category INTEGER)',
    ]:
        conn.execute(text(statement))
    conn.execute(text(
        "INSERT INTO course VALUES "
        "('AIAA1001','AI Basics','AI','Intro',NULL,NULL,NULL,3,'AIAA','1001',0,0,'A')"
    ))
    conn.execute(text(
        "INSERT INTO section VALUES ('2530','L01','AIAA1001','L01',1,0,10,'L',1)"
    ))
    conn.execute(text(
        "INSERT INTO lecture VALUES ('2530','L01',1,900,1030,'R','I')"
    ))
    conn.execute(text(
        "INSERT INTO map_component VALUES ('AIAA1001',1,1,1,0)"
    ))
    conn.commit()
    return conn


def test_import_snapshot_is_repeatable(app):
    conn = source_connection()

    with app.app_context():
        first = import_snapshot(conn)
        second = import_snapshot(conn)

        assert first == {
            'courses': 1,
            'sections': 1,
            'lectures': 1,
            'map_components': 1,
            'map_lines': 0,
        }
        assert second == first
        assert Course.query.filter_by(code='AIAA1001').count() == 1
        assert SchedulerSection.query.count() == 1
        assert SchedulerLecture.query.count() == 1


def test_import_snapshot_rejects_orphan_lecture_without_mutating_destination(app):
    conn = source_connection()
    conn.execute(text(
        "INSERT INTO lecture VALUES ('2530','MISSING',1,900,1030,'R','I')"
    ))
    conn.commit()

    with app.app_context():
        db.session.add(Course(code='KEEP1001', name='Keep', credits=3))
        db.session.commit()

        with pytest.raises(SnapshotValidationError, match='lecture references missing section'):
            import_snapshot(conn)

        assert Course.query.filter_by(code='KEEP1001').one()
        assert Course.query.filter_by(code='AIAA1001').count() == 0
        assert SchedulerSection.query.count() == 0


def test_import_snapshot_rejects_orphan_map_line(app):
    conn = source_connection()
    conn.execute(text(
        "INSERT INTO map_line VALUES ('AIAA1001','MISSING',1,1,0)"
    ))
    conn.commit()

    with app.app_context():
        with pytest.raises(SnapshotValidationError, match='map line references missing component'):
            import_snapshot(conn)
        assert SchedulerMapComponent.query.count() == 0
        assert SchedulerMapLine.query.count() == 0


def test_import_snapshot_rolls_back_when_destination_validation_fails(app, monkeypatch):
    conn = source_connection()

    with app.app_context():
        keep = Course(code='KEEP1001', name='Keep', credits=3)
        db.session.add(keep)
        db.session.flush()
        db.session.add(SchedulerSection(
            semester_id='OLD',
            section_id='OLD-L01',
            course_id=keep.id,
            name='L01',
            bundle=1,
            layer=0,
            quota=10,
            section_type='L',
            is_main=True,
        ))
        db.session.commit()

        def fail_validation(_snapshot, _summary):
            raise SnapshotValidationError('forced destination failure')

        monkeypatch.setattr(importer, 'validate_destination', fail_validation)

        with pytest.raises(SnapshotValidationError, match='forced destination failure'):
            import_snapshot(conn)

        assert Course.query.filter_by(code='AIAA1001').count() == 0
        assert SchedulerSection.query.filter_by(semester_id='OLD', section_id='OLD-L01').one()
