"""Directional, version-bound exchange. No arbitrary URL or database access."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
from urllib.parse import urlsplit

from flask import current_app
import requests

from app.extensions import db
from app.models.makerspace import MakerDeployment, MakerSpace, MakerWorker, now
from app.models.makerspace_sync import MakerSyncGrant, MakerSyncReceipt, MakerSyncAudit
from app.services import makerspace_service as maker

NAME = re.compile(r'[a-z][a-z0-9_]{0,47}\Z')
KEY = re.compile(r'[A-Za-z0-9_.:-]{1,64}\Z')
TYPES = {'string', 'integer', 'number', 'boolean'}
MAX_BYTES = 65536


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def log(grant, action, actor=None, count=0):
    db.session.add(MakerSyncAudit(grant_id=grant.id, actor_id=actor.id if actor else None, action=action, record_count=count))


def runtime_ready():
    worker = db.session.get(MakerWorker, current_app.config.get('MAKERSPACE_WORKER_ID', 'school-makerspace'))
    return bool(worker and maker.aware(worker.last_seen_at) > now() - timedelta(seconds=90)
                and worker.capabilities.get('closed_runtime') == 'v1')


def published(space):
    item = db.session.get(MakerDeployment, space.published_deployment_id) if space.published_deployment_id else None
    if (space.kind != 'hosted' or space.status != 'published' or not item or item.space_id != space.id
            or item.review_status != 'approved' or item.publication_status != 'published'
            or item.status != 'ready' or not item.public_runtime_port):
        raise maker.MakerError('sync_published_sandbox_required', 409)
    return item


def normalize_contract(value):
    """Only return bounded schema declarations, never arbitrary adapter payloads."""
    resources = value.get('resources') if isinstance(value, dict) else None
    if not isinstance(resources, dict) or len(resources) > 24:
        raise maker.MakerError('sync_invalid_contract', 422)
    clean = {}
    for name, resource in resources.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(resource, dict):
            raise maker.MakerError('sync_invalid_contract', 422)
        fields, directions, scope = resource.get('fields'), resource.get('directions'), resource.get('record_scope')
        if (not isinstance(fields, dict) or not 1 <= len(fields) <= 24
                or any(not isinstance(key, str) or not NAME.fullmatch(key) or not isinstance(kind, str) or kind not in TYPES for key, kind in fields.items())
                or not isinstance(directions, list) or not directions or len(directions) > 2
                or any(not isinstance(direction, str) or direction not in ('export', 'import') for direction in directions)
                or len(set(directions)) != len(directions)
                or not isinstance(scope, str) or not scope.strip() or len(scope) > 1000):
            raise maker.MakerError('sync_invalid_contract', 422)
        clean[name] = {'fields': dict(sorted(fields.items())), 'directions': sorted(directions), 'record_scope': scope}
    return {'resources': dict(sorted(clean.items()))}


def catalog(space):
    item = published(space)
    if not runtime_ready():
        raise maker.MakerError('sync_runtime_unavailable', 503)
    contract = normalize_contract(runtime_request(item, 'contract', {}))
    return {**contract, 'deployment_id': item.id, 'source_sha': item.source_sha,
            'artifact_digest': item.artifact_digest, 'contract_digest': digest(contract)}


def validate_selection(policy, contract):
    resource = contract['resources'].get(policy['resource'])
    if (not resource or policy['direction'] not in resource['directions']
            or policy['record_scope'] != resource['record_scope']
            or any(resource['fields'].get(name) != kind for name, kind in policy['fields'].items())):
        raise maker.MakerError('sync_invalid_contract', 422)


def parse_policy(data, item):
    try:
        expires = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
        if expires.tzinfo is None:
            raise ValueError()
        expires = expires.astimezone(timezone.utc)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise maker.MakerError('sync_invalid_expiry') from None
    if not now() + timedelta(minutes=5) < expires <= now() + timedelta(days=90):
        raise maker.MakerError('sync_invalid_expiry')
    direction, resource, fields = data.get('direction'), data.get('resource'), data.get('fields')
    if direction not in ('export', 'import') or not isinstance(resource, str) or not NAME.fullmatch(resource):
        raise maker.MakerError('sync_invalid_contract')
    if (not isinstance(fields, dict) or not 1 <= len(fields) <= 24
            or any(not NAME.fullmatch(key) or (not isinstance(value, str) or value not in TYPES) for key, value in fields.items())):
        raise maker.MakerError('sync_invalid_fields')
    origin = maker.text(data, 'external_origin', 253)
    try:
        url = urlsplit(origin)
        port = url.port
    except ValueError:
        raise maker.MakerError('sync_invalid_origin') from None
    if url.scheme != 'https' or not url.hostname or url.username or url.password or port not in (None, 443) or url.path not in ('', '/') or url.query or url.fragment:
        raise maker.MakerError('sync_invalid_origin')
    retention = data.get('retention_days')
    if type(retention) is not int or not 1 <= retention <= 90:
        raise maker.MakerError('sync_invalid_retention')
    return {
        'version': 1, 'direction': direction, 'resource': resource, 'fields': fields,
        'client_name': maker.text(data, 'client_name', 100), 'external_origin': origin.rstrip('/'),
        'purpose': maker.text(data, 'purpose', 2000), 'record_scope': maker.text(data, 'record_scope', 1000),
        'retention_days': retention, 'deletion_policy': maker.text(data, 'deletion_policy', 2000),
        'conflict_policy': maker.text(data, 'conflict_policy', 1000),
        'expires_at': expires.isoformat(), 'source_sha': item.source_sha,
        'artifact_digest': item.artifact_digest, 'deployment_id': item.id,
    }, expires


def create(space, user, data):
    item = published(space)
    policy, expires = parse_policy(data, item)
    contract = catalog(space)
    if data.get('deployment_id') != item.id or data.get('contract_digest') != contract['contract_digest']:
        raise maker.MakerError('sync_catalog_changed', 409)
    validate_selection(policy, contract)
    policy['contract_digest'] = contract['contract_digest']
    if MakerSyncGrant.query.filter_by(space_id=space.id, status='pending').count() >= 20:
        raise maker.MakerError('sync_request_limit', 429)
    grant = MakerSyncGrant(space_id=space.id, deployment_id=item.id, requested_by=user.id,
                           policy=policy, policy_digest=digest(policy), expires_at=expires)
    db.session.add(grant)
    db.session.flush()
    log(grant, 'requested', user)
    return grant


def effective_status(grant):
    if grant.status != 'approved':
        return grant.status
    if maker.aware(grant.expires_at) <= now():
        return 'expired'
    space = grant.space
    if space.status != 'published' or space.kind != 'hosted' or space.published_deployment_id != grant.deployment_id:
        return 'inactive'
    return 'approved'


def payload(grant):
    return {'id': grant.id, 'slug': grant.space.slug, 'policy': grant.policy, 'policy_digest': grant.policy_digest,
            'status': effective_status(grant), 'review_note': grant.review_note,
            'requested_by': grant.requested_by, 'reviewed_by': grant.reviewed_by,
            'created_at': maker.aware(grant.created_at).isoformat(),
            'reviewed_at': maker.aware(grant.reviewed_at).isoformat() if grant.reviewed_at else None,
            'credential_issued': bool(grant.token_hash),
            'gateway_path': f'/api/makerspace/exchange/{grant.id}'}


def review(grant, user, data):
    if user.id in (grant.requested_by, grant.space.owner_id):
        raise maker.MakerError('independent_review_required', 403)
    if grant.status != 'pending' or data.get('policy_digest') != grant.policy_digest:
        raise maker.MakerError('sync_review_changed', 409)
    decision = data.get('decision')
    if decision not in ('approve', 'reject'):
        raise maker.MakerError('sync_invalid_decision')
    note = maker.text(data, 'note', 2000)
    if decision == 'approve':
        item = published(grant.space)
        if item.id != grant.deployment_id or maker.aware(grant.expires_at) <= now():
            raise maker.MakerError('sync_review_changed', 409)
        if not runtime_ready():
            raise maker.MakerError('sync_runtime_unavailable', 503)
        contract = normalize_contract(adapter(grant, 'contract', {}))
        if grant.policy.get('contract_digest') and grant.policy['contract_digest'] != digest(contract):
            raise maker.MakerError('sync_catalog_changed', 409)
        validate_selection(grant.policy, contract)
    grant.status = 'approved' if decision == 'approve' else 'rejected'
    grant.reviewed_by, grant.reviewed_at, grant.review_note = user.id, now(), note
    log(grant, grant.status, user)


def revoke(grant, user):
    if grant.status not in ('revoked', 'rejected'):
        grant.status, grant.token_hash = 'revoked', None
        log(grant, 'revoked', user)


def rotate(grant, user):
    if effective_status(grant) != 'approved':
        raise maker.MakerError('sync_not_approved', 403)
    secret = 'msx_' + secrets.token_urlsafe(40)
    grant.token_hash, grant.token_rotated_at = maker.hash_value(secret), now()
    log(grant, 'credential_rotated', user)
    return secret


def locked_grant(identifier):
    reference = db.session.get(MakerSyncGrant, identifier)
    if not reference:
        raise maker.MakerError('not_found', 404)
    MakerSpace.query.filter_by(id=reference.space_id).with_for_update().populate_existing().one()
    grant = MakerSyncGrant.query.filter_by(id=identifier).with_for_update().populate_existing().first()
    if not grant:
        raise maker.MakerError('not_found', 404)
    return grant


def authenticate(identifier, authorization):
    grant = locked_grant(identifier)
    secret = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
    if not grant.token_hash or not secret or not hmac.compare_digest(grant.token_hash, maker.hash_value(secret)):
        raise maker.MakerError('sync_invalid_credential', 401)
    # Owner account disablement revokes delegated access as well.
    if not maker.can_manage(grant.space, grant.space.owner) or effective_status(grant) != 'approved':
        raise maker.MakerError('sync_not_approved', 403)
    item = published(grant.space)
    if item.source_sha != grant.policy['source_sha'] or item.artifact_digest != grant.policy['artifact_digest']:
        raise maker.MakerError('sync_review_changed', 409)
    if not runtime_ready():
        raise maker.MakerError('sync_runtime_unavailable', 503)
    if not grant.window_start or maker.aware(grant.window_start) < now() - timedelta(minutes=1):
        grant.window_start, grant.window_count = now(), 0
    if grant.window_count >= 60:
        raise maker.MakerError('sync_rate_limited', 429)
    grant.window_count += 1
    return grant


def validate_record(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise maker.MakerError('sync_field_violation', 422)
    for key, kind in fields.items():
        entry = value[key]
        valid = ((kind == 'string' and isinstance(entry, str) and len(entry) <= 2000)
                 or (kind == 'integer' and type(entry) is int and abs(entry) <= 2**53 - 1)
                 or (kind == 'number' and type(entry) in (int, float) and abs(entry) <= 2**53 - 1 and math.isfinite(entry))
                 or (kind == 'boolean' and type(entry) is bool))
        if not valid:
            raise maker.MakerError('sync_field_violation', 422)
    return value


def adapter(grant, operation, body):
    """Fixed server-selected loopback port; no client URL, redirects, auth or cookies."""
    item = published(grant.space)
    envelope = {'grant_id': grant.id, 'policy_digest': grant.policy_digest, 'resource': grant.policy['resource'],
                'fields': grant.policy['fields'], 'record_scope': grant.policy['record_scope'],
                'retention_days': grant.policy['retention_days'], 'expires_at': grant.policy['expires_at'], **body}
    return runtime_request(item, operation, envelope)


def runtime_request(item, operation, envelope):
    # Only the published server-selected listener is used. Never accept a URL,
    # caller credentials or response redirects from a creator/client.
    port = item.public_runtime_port
    if type(port) is not int or not 20000 <= port < 20100:
        raise maker.MakerError('sync_runtime_unavailable', 503)
    client = requests.Session()
    client.trust_env = False
    try:
        with client.post(f'http://127.0.0.1:{port}/__unikorn/sync/{operation}', json=envelope,
                         headers={'X-Unikorn-Sync': 'v1'}, timeout=(2, 8), allow_redirects=False, stream=True) as response:
            if response.status_code == 409:
                raise maker.MakerError('sync_version_conflict', 409)
            if response.status_code != 200:
                raise maker.MakerError('sync_adapter_rejected', 502)
            content = bytearray()
            for chunk in response.iter_content(8192):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise maker.MakerError('sync_response_too_large', 502)
            try:
                value = json.loads(content)
            except (ValueError, UnicodeError):
                raise maker.MakerError('sync_invalid_response', 502) from None
            if not isinstance(value, dict):
                raise maker.MakerError('sync_invalid_response', 502)
            return value
    except requests.RequestException:
        raise maker.MakerError('sync_runtime_unavailable', 503) from None
    finally:
        client.close()


def exchange(grant, data):
    direction = data.get('direction')
    if direction != grant.policy['direction']:
        raise maker.MakerError('sync_direction_denied', 403)
    if direction == 'export':
        if set(data) - {'direction', 'cursor', 'limit'}:
            raise maker.MakerError('sync_invalid_contract')
        cursor, limit = data.get('cursor', ''), data.get('limit', 50)
        if not isinstance(cursor, str) or len(cursor) > 256 or type(limit) is not int or not 1 <= limit <= 50:
            raise maker.MakerError('sync_invalid_contract')
        result = adapter(grant, 'export', {'cursor': cursor, 'limit': limit})
        records, next_cursor = result.get('records'), result.get('next_cursor')
        if not isinstance(records, list) or len(records) > limit or not isinstance(next_cursor, str) or len(next_cursor) > 256:
            raise maker.MakerError('sync_invalid_response', 502)
        clean = []
        for record in records:
            if not isinstance(record, dict) or set(record) != {'id', 'version', 'deleted', 'data'} or not isinstance(record['id'], str) or not KEY.fullmatch(record['id']) or type(record['version']) is not int or not 1 <= record['version'] <= 2**53 - 1 or type(record['deleted']) is not bool:
                raise maker.MakerError('sync_invalid_response', 502)
            validate_record(record['data'], {} if record['deleted'] else grant.policy['fields'])
            clean.append(record)
        log(grant, 'exported', count=len(clean))
        return {'records': clean, 'next_cursor': next_cursor}
    required = {'direction', 'event_id', 'record_id', 'expected_version', 'data'}
    if not required <= set(data) or set(data) - required - {'deleted'}:
        raise maker.MakerError('sync_invalid_contract')
    if type(data.get('deleted', False)) is not bool:
        raise maker.MakerError('sync_invalid_contract')
    for key in ('event_id', 'record_id'):
        if not isinstance(data[key], str) or not KEY.fullmatch(data[key]):
            raise maker.MakerError('sync_invalid_contract')
    if type(data['expected_version']) is not int or not 0 <= data['expected_version'] <= 2**53 - 1:
        raise maker.MakerError('sync_invalid_contract')
    validate_record(data['data'], {} if data.get('deleted') else grant.policy['fields'])
    value_hash = digest(data)
    receipt = db.session.get(MakerSyncReceipt, (grant.id, data['event_id']))
    if receipt:
        if receipt.payload_hash != value_hash:
            raise maker.MakerError('sync_event_conflict', 409)
        log(grant, 'duplicate')
        return receipt.result
    result = adapter(grant, 'import', {key: value for key, value in data.items() if key != 'direction'})
    if result.get('record_id') != data['record_id'] or type(result.get('version')) is not int or not data['expected_version'] < result['version'] <= 2**53 - 1:
        raise maker.MakerError('sync_invalid_response', 502)
    # Never return creator-controlled extra fields to the external service.
    clean = {'event_id': data['event_id'], 'record_id': data['record_id'], 'version': result['version'], 'status': 'applied'}
    db.session.add(MakerSyncReceipt(grant_id=grant.id, event_id=data['event_id'], payload_hash=value_hash, result=clean))
    log(grant, 'imported', count=1)
    return clean
