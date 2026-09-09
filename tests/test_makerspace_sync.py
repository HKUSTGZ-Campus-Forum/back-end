"""Exercise the actual approval/exchange HTTP boundary using synthetic data."""
from datetime import timedelta
import pytest

from tests.test_makerspace import app, headers, ready_build
from app.extensions import db
from app.models.makerspace import MakerWorker, now
from app.models.makerspace_sync import MakerSyncGrant, MakerSyncReceipt, MakerSyncAudit
from app.services import makerspace_sync as sync


@pytest.fixture(autouse=True)
def adapter_contract(monkeypatch):
    original = sync.runtime_request
    def call(grant, operation, body):
        if operation == 'contract':
            return {'resources': {'groups': {'fields': {'title': 'string', 'members': 'integer'},
                'record_scope': 'Public synthetic groups only', 'directions': ['export', 'import']}}}
        return original(grant, operation, body)
    monkeypatch.setattr(sync, 'runtime_request', call)


def setup(client, direction='export'):
    space, item = ready_build(client)
    item.review_status, item.publication_status, item.public_runtime_port = 'approved', 'published', 20001
    space.status, space.published_deployment_id = 'published', item.id
    db.session.get(MakerWorker, 'school-makerspace').capabilities = {'runtime': 'runsc', 'closed_runtime': 'v1'}
    db.session.commit()
    policy = {'direction': direction, 'resource': 'groups', 'fields': {'title': 'string', 'members': 'integer'},
              'client_name': 'Synthetic mini program', 'external_origin': 'https://example.org',
              'purpose': 'Show approved synthetic groups', 'record_scope': 'Public synthetic groups only',
              'retention_days': 7, 'deletion_policy': 'Remove mirrored data on revocation',
              'conflict_policy': 'School owns membership decisions', 'expires_at': (now() + timedelta(days=1)).isoformat()}
    catalog = client.get('/makerspace/campus-tool/sync/catalog', headers=headers()).json
    policy.update(deployment_id=item.id, contract_digest=catalog['contract_digest'])
    response = client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy)
    assert response.status_code == 201, response.json
    return space, item, response.json, policy


def approve(client, grant):
    response = client.post(f"/makerspace/admin/sync/{grant['id']}/review", headers=headers('reviewer'),
                           json={'decision': 'approve', 'policy_digest': grant['policy_digest'], 'note': 'Reviewed synthetic scope'})
    assert response.status_code == 200, response.json
    response = client.post(f"/makerspace/sync/{grant['id']}/credential", headers=headers())
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json['token']}


def test_review_and_direction_are_independent(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client)
    path = f"/makerspace/exchange/{grant['id']}"
    assert client.post(path, json={'direction': 'export'}).status_code == 401
    assert client.post(f"/makerspace/sync/{grant['id']}/credential", headers=headers()).status_code == 403
    assert client.get('/makerspace/admin/sync', headers=headers()).status_code == 403
    assert client.get('/makerspace/campus-tool/sync', headers=headers('stranger')).status_code == 404
    token = approve(client, grant)
    assert client.post(path, headers=token, json={'direction': 'import'}).status_code == 403
    monkeypatch.setattr(sync, 'adapter', lambda *args: {'records': [{'id': 'g1', 'version': 1, 'deleted': False, 'data': {'title': 'Study', 'members': 2}}], 'next_cursor': '1'})
    result = client.post(path, headers=token, json={'direction': 'export'})
    assert result.status_code == 200
    assert result.json['records'][0]['data']['members'] == 2
    assert MakerSyncGrant.query.one().token_hash not in str(client.get('/makerspace/campus-tool/sync', headers=headers()).json)
    assert MakerSyncAudit.query.filter_by(action='exported').one().record_count == 1
    assert 'Study' not in str(client.get(f"/makerspace/sync/{grant['id']}/audit", headers=headers()).json)


def test_fields_types_and_replay_are_enforced(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client, 'import')
    token, calls = approve(client, grant), []
    def adapter(*args):
        calls.append(args)
        return {'record_id': 'external:1', 'version': 1, 'private_email': 'must not return'}
    monkeypatch.setattr(sync, 'adapter', adapter)
    data = {'direction': 'import', 'event_id': 'event-1', 'record_id': 'external:1', 'expected_version': 0, 'data': {'title': 'Study', 'members': 2}}
    path = f"/makerspace/exchange/{grant['id']}"
    assert client.post(path, headers=token, json=data | {'data': data['data'] | {'email': 'secret'}}).status_code == 422
    assert client.post(path, headers=token, json=data | {'data': {'title': 'Study', 'members': True}}).status_code == 422
    first = client.post(path, headers=token, json=data)
    assert first.status_code == 200
    assert 'private_email' not in first.json
    assert client.post(path, headers=token, json=data).json == first.json
    assert len(calls) == 1 and MakerSyncReceipt.query.count() == 1
    assert client.post(path, headers=token, json=data | {'data': {'title': 'Different', 'members': 2}}).status_code == 409


@pytest.mark.parametrize('change', ['revoke', 'expiry', 'deployment', 'suspended', 'owner_deleted', 'network'])
def test_access_stops_when_authority_changes(app, monkeypatch, change):
    client = app.test_client()
    space, _, grant, _ = setup(client)
    token = approve(client, grant)
    stored = MakerSyncGrant.query.one()
    if change == 'revoke':
        assert client.post(f"/makerspace/sync/{grant['id']}/revoke", headers=headers('reviewer')).status_code == 200
    elif change == 'expiry': stored.expires_at = now() - timedelta(seconds=1)
    elif change == 'deployment': space.published_deployment_id = None
    elif change == 'suspended': space.status = 'suspended'
    elif change == 'owner_deleted': space.owner.is_deleted = True
    elif change == 'network': db.session.get(MakerWorker, 'school-makerspace').capabilities = {'runtime': 'runsc'}
    db.session.commit()
    monkeypatch.setattr(sync, 'adapter', lambda *args: pytest.fail('must not reach business data'))
    assert client.post(f"/makerspace/exchange/{grant['id']}", headers=token, json={'direction': 'export'}).status_code in (401, 403, 503)


def test_review_requires_current_digest_and_independent_admin(app):
    client = app.test_client()
    space, _, grant, _ = setup(client)
    path = f"/makerspace/admin/sync/{grant['id']}/review"
    data = {'decision': 'approve', 'policy_digest': 'wrong', 'note': 'Reviewed'}
    assert client.post(path, headers=headers('reviewer'), json=data).status_code == 409
    space.owner.role_id = __import__('app.models.user_role', fromlist=['UserRole']).UserRole.query.filter_by(name='admin').one().id
    db.session.commit()
    data['policy_digest'] = grant['policy_digest']
    assert client.post(path, headers=headers(), json=data).status_code == 403
    assert client.post(path, headers=headers('reviewer'), json=data).status_code == 200
    assert client.post(path, headers=headers('reviewer'), json=data).status_code == 409


def test_export_rejects_extra_fields_and_nested_data(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client)
    token = approve(client, grant)
    record = {'id': 'g1', 'version': 1, 'deleted': False, 'data': {'title': 'Study', 'members': 2, 'email': 'private'}}
    monkeypatch.setattr(sync, 'adapter', lambda *args: {'records': [record], 'next_cursor': ''})
    result = client.post(f"/makerspace/exchange/{grant['id']}", headers=token, json={'direction': 'export'})
    assert result.status_code == 422
    assert 'private' not in str(result.json)


def test_rotation_revokes_old_token_and_stranger_cannot_read_audit(app):
    client = app.test_client()
    _, _, grant, _ = setup(client)
    token = approve(client, grant)
    assert client.post(f"/makerspace/sync/{grant['id']}/credential", headers=headers('stranger')).status_code == 404
    assert client.get(f"/makerspace/sync/{grant['id']}/audit", headers=headers('stranger')).status_code == 404
    assert client.post(f"/makerspace/sync/{grant['id']}/credential", headers=headers()).status_code == 200
    assert client.post(f"/makerspace/exchange/{grant['id']}", headers=token, json={'direction': 'export'}).status_code == 401


def test_failed_requests_count_toward_rate_limit(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client)
    token = approve(client, grant)
    stored = MakerSyncGrant.query.one()
    stored.window_start, stored.window_count = now(), 59
    db.session.commit()
    path = f"/makerspace/exchange/{grant['id']}"
    assert client.post(path, headers=token, json={'direction': 'import'}).status_code == 403
    assert client.post(path, headers=token, json={'direction': 'export'}).status_code == 429


def test_browser_cannot_call_internal_exchange_adapter(app):
    client = app.test_client()
    space, item, _, _ = setup(client)
    response = client.post('/makerspace/campus-tool/launch', headers=headers(), json={})
    path = response.json['url'].removeprefix('/api')
    assert client.post(path + '__unikorn/sync/import', json={}).status_code == 404


def test_review_rejects_unsupported_adapter_contract(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client)
    monkeypatch.setattr(sync, 'adapter', lambda *args: {'resources': {}})
    response = client.post(f"/makerspace/admin/sync/{grant['id']}/review", headers=headers('reviewer'),
        json={'decision': 'approve', 'policy_digest': grant['policy_digest'], 'note': 'Reviewed'})
    assert response.status_code == 409
    assert MakerSyncGrant.query.one().status == 'pending'


def test_import_tombstone_cannot_carry_unapproved_data(app, monkeypatch):
    client = app.test_client()
    _, _, grant, _ = setup(client, 'import')
    token = approve(client, grant)
    monkeypatch.setattr(sync, 'adapter', lambda *args: {'record_id': 'external:1', 'version': 2})
    data = {'direction': 'import', 'event_id': 'delete-1', 'record_id': 'external:1',
            'expected_version': 1, 'deleted': True, 'data': {}}
    path = f"/makerspace/exchange/{grant['id']}"
    assert client.post(path, headers=token, json=data | {'data': {'email': 'private'}}).status_code == 422
    assert client.post(path, headers=token, json=data).status_code == 200


@pytest.mark.parametrize('bad', [{'fields': {'nested': []}}, {'external_origin': 'https://example.org:bad'}, {'fields': {}}, {'expires_at': 'never'}])
def test_malformed_contracts_are_rejected_without_500(app, bad):
    client = app.test_client()
    _, _, _, data = setup(client)
    assert client.post('/makerspace/campus-tool/sync', headers=headers(), json=data | bad).status_code == 400


def test_catalog_is_owner_only_read_only_and_bound_to_public_artifact(app):
    client = app.test_client()
    space, item, _, _ = setup(client)
    path = '/makerspace/campus-tool/sync/catalog'
    before = MakerSyncGrant.query.count()
    for identity in ('stranger', 'reviewer'):
        assert client.get(path, headers=headers(identity)).status_code == 404
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers())
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['deployment_id'] == item.id
    assert response.json['artifact_digest'] == item.artifact_digest
    assert response.json['resources']['groups']['fields'] == {'title': 'string', 'members': 'integer'}
    assert MakerSyncGrant.query.count() == before
    assert MakerSyncReceipt.query.count() == 0
    space.published_deployment_id = None
    db.session.commit()
    assert client.get(path, headers=headers()).status_code == 409


@pytest.mark.parametrize('change', [
    {'fields': {'email': 'string'}}, {'fields': {'title': 'integer'}},
    {'record_scope': 'all records'}, {'resource': 'private_users'},
])
def test_submission_revalidates_selected_schema(app, change):
    client = app.test_client()
    _, _, _, policy = setup(client)
    response = client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy | change)
    assert response.status_code == 422
    assert MakerSyncGrant.query.count() == 1


def test_submission_rejects_stale_deployment_and_contract(app, monkeypatch):
    client = app.test_client()
    _, _, _, policy = setup(client)
    for change in ({'deployment_id': 'stale'}, {'contract_digest': 'stale'}):
        assert client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy | change).status_code == 409
    monkeypatch.setattr(sync, 'runtime_request', lambda *args: {'resources': {'groups': {
        'fields': {'title': 'string'}, 'directions': ['export'], 'record_scope': policy['record_scope']}}})
    assert client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy).status_code == 409
    assert MakerSyncGrant.query.count() == 1


def test_catalog_strips_extra_payload_and_only_declared_direction_is_allowed(app, monkeypatch):
    client = app.test_client()
    _, _, _, policy = setup(client)
    monkeypatch.setattr(sync, 'runtime_request', lambda *args: {'private': 'do not return', 'resources': {'groups': {
        'fields': policy['fields'], 'directions': ['export'], 'record_scope': policy['record_scope'], 'rows': ['private']}}})
    catalog = client.get('/makerspace/campus-tool/sync/catalog', headers=headers()).json
    assert 'private' not in str(catalog) and 'rows' not in str(catalog)
    policy['contract_digest'] = catalog['contract_digest']
    assert client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy | {'direction': 'import'}).status_code == 422
    assert client.post('/makerspace/campus-tool/sync', headers=headers(), json=policy).status_code == 201


@pytest.mark.parametrize('contract', [None, {'resources': []}, {'resources': {'invalid/name': {}}},
    {'resources': {'groups': {'fields': {'bad': []}, 'directions': ['export'], 'record_scope': 'public'}}},
    {'resources': {'groups': {'fields': {'title': 'string'}, 'directions': [{}], 'record_scope': 'public'}}}])
def test_malformed_catalog_fails_closed(app, monkeypatch, contract):
    client = app.test_client()
    setup(client)
    monkeypatch.setattr(sync, 'runtime_request', lambda *args: contract)
    assert client.get('/makerspace/campus-tool/sync/catalog', headers=headers()).status_code == 422


def test_unavailable_or_empty_catalog_does_not_create_grants(app, monkeypatch):
    client = app.test_client()
    setup(client)
    monkeypatch.setattr(sync, 'runtime_request', lambda *args: {'resources': {}})
    assert client.get('/makerspace/campus-tool/sync/catalog', headers=headers()).json['resources'] == {}
    db.session.get(MakerWorker, 'school-makerspace').last_seen_at = now() - timedelta(minutes=5)
    db.session.commit()
    assert client.get('/makerspace/campus-tool/sync/catalog', headers=headers()).status_code == 503
    assert MakerSyncGrant.query.count() == 1
