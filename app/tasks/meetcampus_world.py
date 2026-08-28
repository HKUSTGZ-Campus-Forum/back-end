"""Single-leader scheduled advancement for the MeetCampus world."""

import secrets

from redis import Redis

from app.extensions import db
from app.services.meetcampus_service import advance_world


LOCK_KEY = "meetcampus:world-tick:leader"
_RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def run_meetcampus_world_tick(app) -> dict:
    """Advance one bounded batch while holding the Redis leader lease."""
    with app.app_context():
        redis_client = Redis.from_url(app.config["REDIS_URL"], decode_responses=True)
        token = secrets.token_urlsafe(24)
        acquired = redis_client.set(LOCK_KEY, token, nx=True, ex=55)
        if not acquired:
            redis_client.close()
            return {"status": "leader_busy", "advancedResidents": 0, "events": 0}
        try:
            result = advance_world()
            db.session.commit()
            app.logger.info("MeetCampus world tick: %s", result)
            return result
        except Exception:
            db.session.rollback()
            app.logger.exception("MeetCampus world tick failed")
            raise
        finally:
            try:
                redis_client.eval(_RELEASE_LOCK, 1, LOCK_KEY, token)
            finally:
                redis_client.close()


def init_meetcampus_world(app, scheduler) -> None:
    if not app.config.get("MEETCAMPUS_WORLD_ENABLED", True):
        app.logger.info("MeetCampus world scheduler disabled by configuration.")
        return
    scheduler.add_job(
        id="meetcampus_world_tick",
        func=run_meetcampus_world_tick,
        args=[app],
        trigger="interval",
        seconds=int(app.config["MEETCAMPUS_WORLD_TICK_SECONDS"]),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
    )
