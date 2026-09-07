from datetime import timedelta
from uuid import uuid4

import pytest
from flask import Response

from app.extensions import db
from app.models.file import File
from app.models.makerspace import MakerLike, MakerFavorite, now
from app.models.user import User
from app.services.file_service import OSSService
from app.services import makerspace_service as service
from tests.test_makerspace import app, headers, setup_space, form
from tests.test_file_upload_flow import FakeBucket


def published(client):
    space, _ = setup_space(client)
    space.status = 'published'
    space.published_metadata = service.metadata(space)
    db.session.commit()
    return space


def cover(owner='author', **overrides):
    values = dict(user_id=User.query.filter_by(username=owner).one().id,
                  object_name=uuid4().hex, original_filename='cover.png',
                  status='uploaded', file_type=File.MAKER_COVER,
                  entity_type='makerspace', mime_type='image/png', file_size=128)
    values.update(overrides)
    record = File(**values)
    db.session.add(record)
    db.session.commit()
    return record


@pytest.mark.parametrize('kind,model', [('likes', MakerLike), ('favorites', MakerFavorite)])
def test_reactions_are_persistent_idempotent_and_viewer_specific(app, kind, model):
    client = app.test_client()
    published(client)
    path = '/makerspace/campus-tool/' + kind
    assert client.put(path).status_code == 401
    for _ in range(2):
        assert client.put(path, headers=headers()).status_code == 200
    assert model.query.count() == 1
    assert client.put(path, headers=headers('stranger')).status_code == 200
    assert model.query.count() == 2
    count, selected = kind + '_count', 'is_liked' if kind == 'likes' else 'is_favorited'
    assert client.get('/makerspace').json['spaces'][0][count] == 2
    assert client.get('/makerspace').json['spaces'][0][selected] is False
    assert client.get('/makerspace/campus-tool', headers=headers()).json[selected] is True
    for _ in range(2):
        result = client.delete(path, headers=headers()).json
        assert result[count] == 1 and result[selected] is False
    assert model.query.count() == 1


def test_private_or_inactive_users_cannot_react(app):
    client = app.test_client()
    space, _ = setup_space(client)
    for kind in ('likes', 'favorites'):
        assert client.put('/makerspace/campus-tool/' + kind, headers=headers()).status_code == 404
    space.status = 'published'
    space.owner.email_verified = False
    db.session.commit()
    assert client.put('/makerspace/campus-tool/likes', headers=headers()).status_code == 403


def test_profiles_never_expose_other_users_drafts_or_favorites(app):
    client = app.test_client()
    space = published(client)
    owner_id = space.owner_id
    assert client.post('/makerspace', json=form('private-tool'), headers=headers()).status_code == 201
    space.title_en = 'Unreviewed draft title'
    db.session.commit()
    path = f'/makerspace/users/{owner_id}'
    public = client.get(path).json['spaces']
    assert len(public) == 1 and public[0]['title_en'] == 'Campus tool'
    assert 'repository' not in public[0]
    assert len(client.get(path, headers=headers('reviewer')).json['spaces']) == 1
    own = client.get(path, headers=headers()).json['spaces']
    assert len(own) == 2
    assert any(item['title_en'] == 'Unreviewed draft title' for item in own)
    assert client.put('/makerspace/campus-tool/favorites', headers=headers()).status_code == 200
    assert client.get('/makerspace/favorites').status_code == 401
    assert len(client.get('/makerspace/favorites', headers=headers()).json['spaces']) == 1
    assert client.get('/makerspace/favorites', headers=headers('stranger')).json['spaces'] == []
    space.status = 'suspended'
    db.session.commit()
    assert client.get('/makerspace/favorites', headers=headers()).json['spaces'] == []
    assert client.get(path).json['spaces'] == []


def test_cover_requires_ownership_and_verified_safe_file(app, monkeypatch):
    client = app.test_client()
    space = published(client)
    path = '/makerspace/campus-tool/cover'
    good = cover()
    for user in ('stranger', 'reviewer'):
        assert client.put(path, json={'file_id': good.id}, headers=headers(user)).status_code == 404
    invalid = [True, '1', 999999, cover('stranger').id,
               cover(status='pending').id, cover(mime_type='image/svg+xml').id,
               cover(file_type=File.AVATAR).id, cover(file_size=File.MAX_MAKER_COVER_BYTES + 1).id]
    for file_id in invalid:
        assert client.put(path, json={'file_id': file_id}, headers=headers()).status_code == 400
    snapshot = dict(space.published_metadata)
    result = client.put(path, json={'file_id': good.id}, headers=headers())
    assert result.status_code == 200
    assert result.json['cover_url'] == f'/api/makerspace/campus-tool/cover?v={good.id}'
    assert space.published_metadata == snapshot
    monkeypatch.setattr('app.routes.file._stream_file_from_oss', lambda *a, **kw: Response(b'image', mimetype='image/png', headers={'Cache-Control': kw['cache_control']}))
    response = client.get(path)
    assert response.status_code == 200 and response.data == b'image'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'sandbox' in response.headers['Content-Security-Policy']
    space.status = 'draft'
    db.session.commit()
    assert client.get(path).status_code == 404
    assert client.get(path, headers=headers('stranger')).status_code == 404
    assert client.get(path, headers=headers('reviewer')).status_code == 404
    assert client.get(path, headers=headers()).status_code == 200
    assert client.get(f'/files/view/{good.id}').status_code == 403


def test_cleanup_and_direct_delete_preserve_current_cover(app, monkeypatch):
    client = app.test_client()
    published(client)
    bucket = FakeBucket()
    monkeypatch.setattr(OSSService, '_create_management_bucket', staticmethod(lambda: bucket))
    bound = cover(created_at=now() - timedelta(hours=25))
    detached = cover(created_at=now() - timedelta(hours=25))
    path = '/makerspace/campus-tool/cover'
    assert client.put(path, json={'file_id': bound.id}, headers=headers()).status_code == 200
    assert client.delete(f'/files/{bound.id}', headers=headers()).status_code == 409
    assert OSSService.cleanup_stale_unbound_uploads(max_age_hours=24) == 1
    assert detached.is_deleted and not bound.is_deleted
    assert bound.object_name not in bucket.deleted
    assert client.put(path, json={'file_id': None}, headers=headers()).json == {'cover_url': None}
    assert OSSService.cleanup_stale_unbound_uploads(max_age_hours=24) == 1
    assert bound.is_deleted


@pytest.mark.parametrize('size,mime,expected', [(128, 'image/webp', 200), (5 * 1024 * 1024 + 1, 'image/png', 422), (128, 'image/svg+xml', 422)])
def test_cover_completion_verifies_storage_metadata(app, monkeypatch, size, mime, expected):
    client = app.test_client()
    bucket = FakeBucket(size, mime)
    monkeypatch.setattr(OSSService, '_create_upload_bucket', staticmethod(lambda: bucket))
    monkeypatch.setattr(OSSService, '_create_management_bucket', staticmethod(lambda: bucket))
    record = cover(status='pending')
    assert client.post(f'/files/{record.id}/complete', headers=headers()).status_code == expected
    assert record.status == ('uploaded' if expected == 200 else 'error')
