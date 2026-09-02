"""Public configuration and authenticated one-attempt recruitment routes."""

import hashlib
import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import cache
from app.services.recruitment_agent_service import (
    PROMPT_LIMIT,
    count_recruitment_prompt_characters,
    normalize_recruitment_prompt,
    run_recruitment_agent,
)


logger = logging.getLogger(__name__)
bp = Blueprint('recruitment', __name__, url_prefix='/recruitment')


def _response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _is_enabled():
    return bool(
        current_app.config.get('RECRUITMENT_CHALLENGE_ENABLED')
        and current_app.config.get('DASHSCOPE_API_KEY')
    )


def _attempt_key(identity):
    digest = hashlib.sha256(str(identity).encode('utf-8')).hexdigest()
    return f'recruitment:attempt:v1:{digest}'


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _public_attempt(receipt):
    if not isinstance(receipt, dict):
        return None
    allowed = {
        'state', 'success', 'score', 'tool_calls', 'events', 'agent_message',
        'duration_ms', 'model', 'started_at', 'completed_at', 'error',
    }
    return {key: value for key, value in receipt.items() if key in allowed}


@bp.get('/config')
def get_recruitment_config():
    return _response({
        'success': True,
        'data': {
            'enabled': _is_enabled(),
            'prompt_limit': PROMPT_LIMIT,
            'attempt_limit': 1,
            'max_tool_calls': current_app.config['RECRUITMENT_AGENT_MAX_TOOL_CALLS'],
            'max_rounds': current_app.config['RECRUITMENT_AGENT_MAX_ROUNDS'],
        },
    })


@bp.get('/status')
@jwt_required()
def get_recruitment_status():
    receipt = cache.get(_attempt_key(get_jwt_identity()))
    return _response({
        'success': True,
        'data': {
            'attempted': receipt is not None,
            'attempt': _public_attempt(receipt),
        },
    })


@bp.post('/run')
@jwt_required()
def run_recruitment_challenge():
    if not _is_enabled():
        return _response({
            'success': False,
            'error': 'challenge_unavailable',
        }, 503)

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    prompt = normalize_recruitment_prompt(body.get('prompt'))
    character_count = count_recruitment_prompt_characters(prompt)
    if character_count == 0:
        return _response({'success': False, 'error': 'prompt_required'}, 400)
    if character_count > PROMPT_LIMIT:
        return _response({
            'success': False,
            'error': 'prompt_too_long',
            'prompt_limit': PROMPT_LIMIT,
        }, 400)

    key = _attempt_key(get_jwt_identity())
    ttl = current_app.config['RECRUITMENT_ATTEMPT_TTL_SECONDS']
    started_at = _utc_now()
    running_receipt = {'state': 'running', 'started_at': started_at}
    if not cache.add(key, running_receipt, timeout=ttl):
        return _response({
            'success': False,
            'error': 'attempt_already_used',
            'data': {'attempt': _public_attempt(cache.get(key))},
        }, 409)

    try:
        result = run_recruitment_agent(prompt)
        receipt = {
            **result,
            'started_at': started_at,
            'completed_at': _utc_now(),
        }
        cache.set(key, receipt, timeout=ttl)
        return _response({'success': True, 'data': {'attempt': _public_attempt(receipt)}})
    except Exception:
        logger.exception('Recruitment agent run failed')
        receipt = {
            'state': 'failed',
            'error': 'agent_failed',
            'started_at': started_at,
            'completed_at': _utc_now(),
        }
        cache.set(key, receipt, timeout=ttl)
        return _response({
            'success': False,
            'error': 'agent_failed',
            'data': {'attempt': _public_attempt(receipt)},
        }, 502)
