import pytest
from app.extensions import db
from app.models.user import User
from app.models.post import Post
from app.models.makerspace import MakerFavorite
from tests.test_makerspace import app, headers, form
from tests.test_makerspace_social import published

DEFAULTS = dict(favorite_spaces=False, created_spaces=True, recent_posts=True)


def test_defaults_and_account_only_updates(app):
    client = app.test_client()
    owner = User.query.filter_by(username='author').one()
    assert client.get('/users/me/profile-visibility').status_code == 401
    assert client.get('/users/me/profile-visibility', headers=headers()).json == DEFAULTS
    assert client.get(f'/users/public/{owner.id}').json['profile_visibility'] == DEFAULTS
    changed = dict(favorite_spaces=True, created_spaces=False, recent_posts=False)
    result = client.put('/users/me/profile-visibility', json=changed, headers=headers())
    assert result.status_code == 200 and result.json == changed
    assert result.headers['Cache-Control'] == 'no-store'
    db.session.expire_all()
    assert client.get('/users/me/profile-visibility', headers=headers()).json == changed
    assert client.get('/users/me/profile-visibility', headers=headers('stranger')).json == DEFAULTS
    assert client.put('/users/me/profile-visibility', json=DEFAULTS).status_code == 401


@pytest.mark.parametrize('payload', [None, [], {}, {**DEFAULTS, 'recent_posts': 1}, {**DEFAULTS, 'favorite_spaces': 'true'}, {**DEFAULTS, 'user_id': 2}])
def test_invalid_settings_never_partially_save(app, payload):
    client = app.test_client()
    result = client.put('/users/me/profile-visibility', json=payload, headers=headers())
    assert result.status_code == 400
    assert client.get('/users/me/profile-visibility', headers=headers()).json == DEFAULTS


def test_profile_visibility_does_not_unpublish_work_or_expose_drafts(app):
    client = app.test_client()
    space = published(client)
    owner_id = space.owner_id
    client.post('/makerspace', json=form('draft-tool'), headers=headers())
    client.put('/users/me/profile-visibility', json={**DEFAULTS, 'created_spaces': False}, headers=headers())
    for auth in ({}, headers('stranger'), headers('reviewer')):
        response = client.get(f'/makerspace/users/{owner_id}', headers=auth)
        assert response.status_code == 403
        assert response.headers['Cache-Control'] == 'no-store'
    assert len(client.get(f'/makerspace/users/{owner_id}', headers=headers()).json['spaces']) == 2
    assert len(client.get('/makerspace').json['spaces']) == 1
    assert client.get('/makerspace/campus-tool').status_code == 200
    client.put('/users/me/profile-visibility', json=DEFAULTS, headers=headers())
    assert len(client.get(f'/makerspace/users/{owner_id}').json['spaces']) == 1


def test_favorites_require_opt_in_and_never_leak_collector_state(app):
    client = app.test_client()
    space = published(client)
    owner_id = space.owner_id
    client.put('/makerspace/campus-tool/favorites', headers=headers())
    path = f'/makerspace/users/{owner_id}/favorites'
    assert client.get(path).status_code == 403
    assert client.get(path, headers=headers('stranger')).status_code == 403
    assert len(client.get(path, headers=headers()).json['spaces']) == 1
    client.put('/users/me/profile-visibility', json={**DEFAULTS, 'favorite_spaces': True}, headers=headers())
    space.title_en = 'Unreviewed private title'
    db.session.commit()
    for auth in ({}, headers('stranger')):
        entry = client.get(path, headers=auth).json['spaces'][0]
        assert entry['title_en'] == 'Campus tool'
        assert entry['is_favorited'] is False
        assert 'repository' not in entry and 'deployments' not in entry
    space.status = 'suspended'
    db.session.commit()
    assert client.get(path).json['spaces'] == []
    assert MakerFavorite.query.count() == 1
    client.put('/users/me/profile-visibility', json=DEFAULTS, headers=headers())
    assert client.get(path).status_code == 403
    assert client.get('/makerspace/favorites').status_code == 401


def test_recent_posts_visibility_is_enforced_on_profile_endpoint(app):
    client = app.test_client()
    owner = User.query.filter_by(username='author').one()
    for number in range(12):
        db.session.add(Post(user_id=owner.id, title=f'Post {number}', content='Public content'))
    db.session.commit()
    path = f'/users/{owner.id}/profile-posts'
    assert len(client.get(path).json['posts']) == 10
    client.put('/users/me/profile-visibility', json={**DEFAULTS, 'recent_posts': False}, headers=headers())
    for auth in ({}, headers('stranger'), headers('reviewer')):
        assert client.get(path, headers=auth).status_code == 403
    assert len(client.get(path, headers=headers()).json['posts']) == 10
    assert client.get(f'/posts?user_id={owner.id}').json['total_count'] == 12
    post = Post.query.first()
    assert client.get(f'/posts/{post.id}').status_code == 200


def test_deleted_account_cannot_publish_preferences(app):
    client = app.test_client()
    auth = headers()
    user = User.query.filter_by(username='author').one()
    user.is_deleted = True
    db.session.commit()
    assert client.put('/users/me/profile-visibility', json={**DEFAULTS, 'favorite_spaces': True}, headers=auth).status_code == 401
    assert client.get(f'/makerspace/users/{user.id}/favorites').status_code == 404
    assert client.get(f'/users/{user.id}/profile-posts').status_code == 404
