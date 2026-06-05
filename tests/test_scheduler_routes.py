import pytest
from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_section import SchedulerSection
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.scheduler_cart import SchedulerUserCourseCart, SchedulerUserBundleCart
from app.models.user import User
from app.models.user_role import UserRole
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_courses(app):
    with app.app_context():
        c1 = Course(code="TEST1001", name="Test English I", credits=3, subject="UCUG", catalog_number="1001",
                    course_title_abbr="Test Engl I")
        c2 = Course(code="TEST1010", name="Test Intro to AI", credits=4, subject="AIAA", catalog_number="1010",
                    course_title_abbr="Test Intro AI")
        db.session.add_all([c1, c2])
        db.session.flush()

        # Use unique section_ids per course since (semester_id, section_id) is the composite PK
        s1 = SchedulerSection(semester_id="2530", section_id="TEST1001-L01", course_id=c1.id,
                              name="L01", bundle=1, layer=0, quota=30, section_type="L", is_main=True)
        s2 = SchedulerSection(semester_id="2530", section_id="TEST1010-L01", course_id=c2.id,
                              name="L01", bundle=1, layer=0, quota=50, section_type="L", is_main=True)
        l1 = SchedulerLecture(semester_id="2530", section_id="TEST1001-L01", day=1, start_time=900, end_time=1030,
                              room="Room 101", instructor="Dr. Smith")
        db.session.add_all([s1, s2, l1])
        db.session.commit()


@pytest.fixture
def auth_headers(app):
    with app.app_context():
        role = UserRole.query.filter_by(name='user').first()
        if not role:
            role = UserRole(name='user', description='Regular user')
            db.session.add(role)
            db.session.flush()
        user = User(username="testuser_routes", email="test_routes@hkust-gz.edu.cn",
                    email_verified=True, role_id=role.id)
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id))
    return {"Authorization": f"Bearer {token}"}


def test_list_semesters(client, seed_courses):
    resp = client.get('/scheduler/semesters')
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]['id'] == '2530'
    assert data[0]['name'] == '2025-26 Spring'


def test_list_semesters_includes_25_26_summer_label(client, app):
    with app.app_context():
        course = Course(code="SUMR1001", name="Summer Course", credits=3)
        db.session.add(course)
        db.session.flush()
        db.session.add(SchedulerSection(
            semester_id="2540",
            section_id="SUMR-L01",
            course_id=course.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=30,
            section_type="L",
            is_main=True,
        ))
        db.session.commit()

    resp = client.get('/scheduler/semesters')
    assert resp.status_code == 200
    summer = next(item for item in resp.get_json() if item['id'] == '2540')
    assert summer['name'] == '2025-26 Summer'
    assert summer['name_zh'] == '25-26夏'


def test_search_courses(client, seed_courses):
    resp = client.get('/scheduler/courses/search?query=English&semester=2530')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] >= 1
    assert data['items'][0]['course_code'] == 'TEST1001'


def test_search_courses_no_semester(client, seed_courses):
    resp = client.get('/scheduler/courses/search?query=TEST1010')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] >= 1
    assert data['items'][0]['course_code'] == 'TEST1010'


def test_search_courses_empty(client, seed_courses):
    resp = client.get('/scheduler/courses/search?query=NONEXISTENT')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 0
    assert data['items'] == []


def test_list_subjects_filters_to_current_semester_offerings(client, app):
    with app.app_context():
        dled = Course(code="DLED9910", name="English Communication I", credits=3, subject="DLED")
        aiaa = Course(code="AIAA9910", name="Python Programming", credits=3, subject="AIAA")
        moes = Course(code="MOES9910", name="Dormant MOES", credits=3, subject="MOES")
        db.session.add_all([dled, aiaa, moes])
        db.session.flush()
        db.session.add_all([
            SchedulerSection(semester_id="2540", section_id="DLED-L01", course_id=dled.id,
                             name="L01", bundle=1, layer=0, quota=40, section_type="L", is_main=True),
            SchedulerSection(semester_id="2540", section_id="AIAA-L01", course_id=aiaa.id,
                             name="L01", bundle=1, layer=0, quota=40, section_type="L", is_main=True),
            SchedulerSection(semester_id="2530", section_id="MOES-L01", course_id=moes.id,
                             name="L01", bundle=1, layer=0, quota=40, section_type="L", is_main=True),
        ])
        db.session.commit()

    resp = client.get('/scheduler/subjects?semester=2540')

    assert resp.status_code == 200
    assert resp.get_json() == [
        {'subject': 'AIAA', 'course_count': 1},
        {'subject': 'DLED', 'course_count': 1},
    ]


def test_get_course_detail(client, seed_courses):
    resp = client.get('/scheduler/courses/TEST1001?semester=2530')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['course_code'] == 'TEST1001'
    assert data['course_title'] == 'Test English I'
    assert data['credit'] == 3
    assert len(data['sections']) == 1
    assert data['sections'][0]['lectures'][0]['instructor'] == 'Dr. Smith'


def test_get_course_detail_not_found(client, seed_courses):
    resp = client.get('/scheduler/courses/NONEXIST?semester=2530')
    assert resp.status_code == 404


def test_get_empty_cart(client, auth_headers):
    resp = client.get('/scheduler/cart/2530', headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_add_to_cart(client, auth_headers, seed_courses):
    resp = client.post('/scheduler/cart/2530/add',
                       json={'course_code': 'TEST1001'},
                       headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['course_code'] == 'TEST1001'
    assert data['enabled'] is False


def test_add_to_cart_course_not_found(client, auth_headers, seed_courses):
    resp = client.post('/scheduler/cart/2530/add',
                       json={'course_code': 'NOPE9999'},
                       headers=auth_headers)
    assert resp.status_code == 404


def test_add_to_cart_duplicate(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)
    resp = client.post('/scheduler/cart/2530/add',
                       json={'course_code': 'TEST1001'},
                       headers=auth_headers)
    assert resp.status_code == 409


def test_get_cart_after_add(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)
    resp = client.get('/scheduler/cart/2530', headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]['course_code'] == 'TEST1001'


def test_remove_from_cart(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)
    resp = client.delete('/scheduler/cart/2530/remove/TEST1001', headers=auth_headers)
    assert resp.status_code == 200
    resp = client.get('/scheduler/cart/2530', headers=auth_headers)
    assert resp.get_json() == []


def test_remove_from_cart_not_found(client, auth_headers, seed_courses):
    resp = client.delete('/scheduler/cart/2530/remove/TEST1001', headers=auth_headers)
    assert resp.status_code == 404


def test_toggle_course_enabled(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)
    resp = client.put('/scheduler/cart/2530/course/TEST1001/toggle',
                      json={'enabled': True},
                      headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()['enabled'] is True


def test_toggle_course_enabled_not_in_cart(client, auth_headers, seed_courses):
    resp = client.put('/scheduler/cart/2530/course/TEST1001/toggle',
                      json={'enabled': True},
                      headers=auth_headers)
    assert resp.status_code == 404


def test_toggle_cart_requires_auth(client, seed_courses):
    resp = client.get('/scheduler/cart/2530')
    assert resp.status_code == 401


def test_get_map_components(client, seed_courses):
    resp = client.get('/scheduler/map/components')
    assert resp.status_code == 200


def test_get_map_lines(client, seed_courses):
    resp = client.get('/scheduler/map/lines')
    assert resp.status_code == 200


def test_get_map_courses(client, seed_courses):
    resp = client.get('/scheduler/map/courses')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) >= 2
    codes = [c['course_code'] for c in data]
    assert 'TEST1001' in codes
    assert 'TEST1010' in codes


def test_add_multiple_courses_to_cart(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1010'},
                headers=auth_headers)
    resp = client.get('/scheduler/cart/2530', headers=auth_headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    codes = sorted([item['course_code'] for item in data])
    assert codes == ['TEST1001', 'TEST1010']


def test_add_to_cart_rejects_missing_json(client, auth_headers, seed_courses):
    resp = client.post('/scheduler/cart/2530/add', headers=auth_headers)

    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Invalid JSON body'}


def test_add_to_cart_rejects_non_string_course_code(client, auth_headers, seed_courses):
    resp = client.post('/scheduler/cart/2530/add',
                       json={'course_code': 123},
                       headers=auth_headers)

    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Invalid course code'}


def test_toggle_course_rejects_missing_json(client, auth_headers, seed_courses):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)

    resp = client.put('/scheduler/cart/2530/course/TEST1001/toggle',
                      headers=auth_headers)

    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Invalid JSON body'}


def test_add_to_cart_rejects_course_without_sections(client, auth_headers, app):
    with app.app_context():
        db.session.add(Course(code='EMPTY1001', name='No Sections', credits=3))
        db.session.commit()

    resp = client.post('/scheduler/cart/2530/add',
                       json={'course_code': 'EMPTY1001'},
                       headers=auth_headers)

    assert resp.status_code == 422
    assert resp.get_json() == {'error': 'Course has no sections for semester'}


def test_cart_serialization_orders_layers_bundles_and_sections(client, auth_headers, app):
    with app.app_context():
        course = Course(code='SORT1001', name='Sort Me', credits=3)
        db.session.add(course)
        db.session.flush()
        db.session.add_all([
            SchedulerSection(semester_id='2530', section_id='SORT-T02', course_id=course.id,
                             name='T02', bundle=2, layer=1, quota=10, section_type='T', is_main=False),
            SchedulerSection(semester_id='2530', section_id='SORT-L02', course_id=course.id,
                             name='L02', bundle=2, layer=0, quota=10, section_type='L', is_main=True),
            SchedulerSection(semester_id='2530', section_id='SORT-L01', course_id=course.id,
                             name='L01', bundle=1, layer=0, quota=10, section_type='L', is_main=True),
        ])
        db.session.commit()

    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'SORT1001'},
                headers=auth_headers)
    data = client.get('/scheduler/cart/2530', headers=auth_headers).get_json()[0]

    assert list(data['layers']) == ['0', '1']
    assert [bundle['id'] for bundle in data['layers']['0']] == [1, 2]
    assert data['layers']['0'][0]['sections'][0]['section_id'] == 'SORT-L01'


def test_cart_is_isolated_between_users(client, auth_headers, seed_courses, app):
    client.post('/scheduler/cart/2530/add',
                json={'course_code': 'TEST1001'},
                headers=auth_headers)

    with app.app_context():
        role = UserRole.query.filter_by(name='user').first()
        other = User(username='other_scheduler_user', email='other_scheduler@hkust-gz.edu.cn',
                     email_verified=True, role_id=role.id)
        other.set_password('password123')
        db.session.add(other)
        db.session.commit()
        token = create_access_token(identity=str(other.id))

    other_headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/scheduler/cart/2530', headers=other_headers).get_json() == []
