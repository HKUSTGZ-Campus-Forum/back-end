"""Entrypoint for the single APScheduler background-worker container."""

import logging
import signal
import threading

from app import create_app
from app.tasks.sts_pool import unified_scheduler


logger = logging.getLogger(__name__)


def run_worker():
    """Create the app, start its configured scheduler, and keep the process alive."""
    app = create_app()
    if not app.config.get('ENABLE_BACKGROUND_TASKS', False):
        raise RuntimeError(
            'Background worker requires ENABLE_BACKGROUND_TASKS=true.'
        )
    if not unified_scheduler.running:
        raise RuntimeError('Background task scheduler did not start.')

    stop_event = threading.Event()

    def _request_shutdown(signum, _frame):
        logger.info('Background worker received signal %s; shutting down.', signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    logger.info(
        'Background worker started with %s scheduled job(s).',
        len(unified_scheduler.get_jobs()),
    )
    try:
        while not stop_event.wait(timeout=30):
            if not unified_scheduler.running:
                raise RuntimeError('Background task scheduler stopped unexpectedly.')
    finally:
        if unified_scheduler.running:
            unified_scheduler.shutdown(wait=False)
        logger.info('Background worker stopped.')


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    run_worker()


if __name__ == '__main__':
    main()
