"""Recruitment routes with one attempt except for verified test accounts."""

import hashlib
import logging
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import cache, db
from app.models.recruitment_attempt import RecruitmentAttempt
from app.models.user import User
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
        and current_app.config.get('RECRUITMENT_AGENT_API_KEY')
        and current_app.config.get('RECRUITMENT_AGENT_BASE_URL')
        and current_app.config.get('RECRUITMENT_AGENT_MODEL')
    )


def _attempt_key(identity):
    digest = hashlib.sha256(str(identity).encode('utf-8')).hexdigest()
    return f'recruitment:attempt:v1:{digest}'


def _running_key(identity):
    digest = hashlib.sha256(str(identity).encode('utf-8')).hexdigest()
    return f'recruitment:running:v1:{digest}'


def _normalized_email_allowlist(value):
    if isinstance(value, str):
        value = value.split(',')
    return {
        str(email).strip().casefold()
        for email in (value or ())
        if str(email).strip()
    }


def _active_user(identity):
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return None

    user = db.session.get(User, user_id)
    if not user or user.is_deleted or not user.email:
        return None
    return user


def _has_allowlisted_email(identity, config_key):
    user = _active_user(identity)
    if not user or not user.email_verified:
        return False

    allowlist = _normalized_email_allowlist(
        current_app.config.get(config_key)
    )
    return user.email.strip().casefold() in allowlist


def _has_unlimited_attempts(identity):
    return _has_allowlisted_email(identity, 'RECRUITMENT_UNLIMITED_EMAILS')


def _is_recruitment_admin(identity):
    return _has_allowlisted_email(identity, 'RECRUITMENT_ADMIN_EMAILS')


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _utc_datetime():
    return datetime.now(timezone.utc)


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
    identity = get_jwt_identity()
    receipt = cache.get(_attempt_key(identity))
    unlimited_attempts = _has_unlimited_attempts(identity)
    return _response({
        'success': True,
        'data': {
            'attempted': receipt is not None,
            'attempt': _public_attempt(receipt),
            'unlimited_attempts': unlimited_attempts,
            'can_view_admin': _is_recruitment_admin(identity),
        },
    })


def _admin_pagination_args():
    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(100, max(1, int(request.args.get('per_page', 50))))
    except (TypeError, ValueError):
        return None, None
    return page, per_page


def _leaderboard(limit=100):
    ordered = (
        RecruitmentAttempt.query
        .options(joinedload(RecruitmentAttempt.user))
        .filter(RecruitmentAttempt.state == 'complete')
        .order_by(
            RecruitmentAttempt.score.desc(),
            RecruitmentAttempt.tool_calls.asc(),
            RecruitmentAttempt.duration_ms.asc().nullslast(),
            RecruitmentAttempt.completed_at.asc().nullslast(),
            RecruitmentAttempt.id.asc(),
        )
        .all()
    )
    best_by_account = []
    seen_accounts = set()
    for attempt in ordered:
        account_key = (
            f'user:{attempt.user_id}'
            if attempt.user_id is not None
            else f'email:{attempt.email_snapshot.casefold()}'
        )
        if account_key in seen_accounts:
            continue
        seen_accounts.add(account_key)
        best_by_account.append(attempt)
        if len(best_by_account) >= limit:
            break

    return [
        {
            'rank': rank,
            'attempt_id': attempt.id,
            'user_id': attempt.user_id,
            'username': (
                attempt.user.username
                if attempt.user and not attempt.user.is_deleted
                else attempt.username_snapshot
            ),
            'email': (
                attempt.user.email
                if attempt.user and attempt.user.email
                else attempt.email_snapshot
            ),
            'score': attempt.score,
            'tool_calls': attempt.tool_calls,
            'duration_ms': attempt.duration_ms,
            'completed_at': (
                attempt.completed_at.isoformat()
                if attempt.completed_at
                else None
            ),
        }
        for rank, attempt in enumerate(best_by_account, start=1)
    ]


@bp.get('/admin/overview')
@jwt_required()
def get_recruitment_admin_overview():
    identity = get_jwt_identity()
    if not _is_recruitment_admin(identity):
        return _response({'success': False, 'error': 'admin_required'}, 403)

    page, per_page = _admin_pagination_args()
    if page is None or per_page is None:
        return _response({'success': False, 'error': 'invalid_pagination'}, 400)

    query = (
        RecruitmentAttempt.query
        .options(joinedload(RecruitmentAttempt.user))
        .order_by(
            RecruitmentAttempt.started_at.desc(),
            RecruitmentAttempt.id.desc(),
        )
    )
    total = query.count()
    attempts = (
        query.offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    completed_query = RecruitmentAttempt.query.filter(
        RecruitmentAttempt.state == 'complete'
    )
    participants = (
        db.session.query(func.count(func.distinct(RecruitmentAttempt.user_id)))
        .filter(RecruitmentAttempt.user_id.isnot(None))
        .scalar()
        or 0
    )
    average_score = (
        db.session.query(func.avg(RecruitmentAttempt.score))
        .filter(RecruitmentAttempt.state == 'complete')
        .scalar()
    )

    return _response({
        'success': True,
        'data': {
            'summary': {
                'attempts': total,
                'participants': int(participants),
                'completed': completed_query.count(),
                'perfect_scores': completed_query.filter(
                    RecruitmentAttempt.score == 100
                ).count(),
                'average_score': (
                    round(float(average_score), 1)
                    if average_score is not None
                    else 0
                ),
            },
            'leaderboard': _leaderboard(),
            'attempts': [attempt.to_admin_dict() for attempt in attempts],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': max(1, (total + per_page - 1) // per_page),
            },
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

    identity = get_jwt_identity()
    user = _active_user(identity)
    if not user:
        return _response({
            'success': False,
            'error': 'account_required',
        }, 403)
    key = _attempt_key(identity)
    unlimited_attempts = _has_unlimited_attempts(identity)
    ttl = current_app.config['RECRUITMENT_ATTEMPT_TTL_SECONDS']
    started_at = _utc_now()
    running_receipt = {'state': 'running', 'started_at': started_at}
    claim_key = _running_key(identity) if unlimited_attempts else key
    claim_ttl = (
        max(
            300,
            current_app.config['RECRUITMENT_AGENT_MAX_ROUNDS']
            * current_app.config['RECRUITMENT_AGENT_TIMEOUT_SECONDS']
            + 60,
        )
        if unlimited_attempts
        else ttl
    )
    if not cache.add(claim_key, running_receipt, timeout=claim_ttl):
        return _response({
            'success': False,
            'error': (
                'attempt_in_progress'
                if unlimited_attempts
                else 'attempt_already_used'
            ),
            'data': {
                'attempt': _public_attempt(cache.get(key)),
                'unlimited_attempts': unlimited_attempts,
            },
        }, 409)

    attempt_record = RecruitmentAttempt(
        user_id=user.id,
        username_snapshot=user.username,
        email_snapshot=user.email,
        prompt=prompt,
        state='running',
        success=False,
        score=0,
        tool_calls=0,
        feedback=[],
        agent_message='',
        started_at=_utc_datetime(),
    )
    try:
        db.session.add(attempt_record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        cache.delete(claim_key)
        logger.exception('Unable to persist recruitment attempt before run')
        return _response({
            'success': False,
            'error': 'history_unavailable',
        }, 503)

    try:
        result = run_recruitment_agent(prompt)
        receipt = {
            **result,
            'started_at': started_at,
            'completed_at': _utc_now(),
        }
        attempt_record.state = 'complete'
        attempt_record.success = bool(result.get('success'))
        attempt_record.score = int(result.get('score') or 0)
        attempt_record.tool_calls = int(result.get('tool_calls') or 0)
        attempt_record.feedback = list(result.get('events') or [])
        attempt_record.agent_message = str(result.get('agent_message') or '')[:500]
        attempt_record.duration_ms = result.get('duration_ms')
        attempt_record.model = str(result.get('model') or '')[:100] or None
        attempt_record.completed_at = _utc_datetime()
        db.session.commit()
        cache.set(key, receipt, timeout=ttl)
        return _response({
            'success': True,
            'data': {
                'attempt': _public_attempt(receipt),
                'unlimited_attempts': unlimited_attempts,
            },
        })
    except Exception:
        logger.exception('Recruitment agent run failed')
        db.session.rollback()
        receipt = {
            'state': 'failed',
            'error': 'agent_failed',
            'started_at': started_at,
            'completed_at': _utc_now(),
        }
        try:
            persisted_attempt = db.session.get(
                RecruitmentAttempt,
                attempt_record.id,
            )
            if persisted_attempt:
                persisted_attempt.state = 'failed'
                persisted_attempt.error = 'agent_failed'
                persisted_attempt.completed_at = _utc_datetime()
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception('Unable to persist failed recruitment attempt')
        cache.set(key, receipt, timeout=ttl)
        return _response({
            'success': False,
            'error': 'agent_failed',
            'data': {
                'attempt': _public_attempt(receipt),
                'unlimited_attempts': unlimited_attempts,
            },
        }, 502)
    finally:
        if unlimited_attempts:
            cache.delete(claim_key)
