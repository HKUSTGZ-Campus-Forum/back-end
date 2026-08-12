"""Container liveness and dependency readiness probes."""

import logging

from flask import Blueprint, current_app, jsonify
from redis import Redis
from sqlalchemy import text

from app.extensions import db


logger = logging.getLogger(__name__)
bp = Blueprint('health', __name__)


def _json_response(payload, status):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _check_postgresql():
    with db.engine.connect() as connection:
        connection.execute(text('SELECT 1'))


def _check_redis():
    redis_url = current_app.config.get('REDIS_URL')
    if not redis_url:
        raise RuntimeError('REDIS_URL is not configured')
    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError('Redis ping did not succeed')
    finally:
        close = getattr(client, 'close', None)
        if close is not None:
            close()
        else:
            client.connection_pool.disconnect()


@bp.get('/healthz')
def liveness():
    return _json_response({'status': 'ok'}, 200)


@bp.get('/readyz')
def readiness():
    checks = {}
    for name, check in (
        ('postgresql', _check_postgresql),
        ('redis', _check_redis),
    ):
        try:
            check()
            checks[name] = {'status': 'ok'}
        except Exception:
            logger.warning('Readiness check failed for %s', name, exc_info=True)
            checks[name] = {'status': 'unavailable'}

    ready = all(item['status'] == 'ok' for item in checks.values())
    return _json_response(
        {'status': 'ready' if ready else 'unavailable', 'checks': checks},
        200 if ready else 503,
    )
