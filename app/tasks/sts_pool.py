from apscheduler.schedulers.background import BackgroundScheduler
from app.extensions import db
from app.services.file_service import OSSService
from flask import current_app

# Unified scheduler for all background tasks
unified_scheduler = BackgroundScheduler(daemon=True)

def init_pool_maintenance(app):
    """
    Initializes and starts the unified background task scheduler.

    IMPORTANT: This is the central entry point for ALL background tasks.

    Current Tasks:
    1. STS Pool Maintenance (every 15 minutes) - Existing functionality
    2. Embedding Maintenance (every 60 minutes) - New auto-recovery system

    Adding New Tasks:
    To add a new background task, follow this pattern:
    1. Create your task module in app/tasks/your_task.py
    2. Import and call your init function here
    3. Use the same unified_scheduler instance
    4. Follow the app context wrapper pattern (_your_task_job)

    Example:
    ```python
    try:
        from app.tasks.your_task import init_your_task
        init_your_task(app, unified_scheduler)
    except Exception as e:
        app.logger.warning(f"Could not initialize your task: {e}")
    ```
    """
    global unified_scheduler

    if not app.config.get('ENABLE_BACKGROUND_TASKS', True):
        app.logger.info("Background task scheduler disabled by configuration.")
        return

    if not unified_scheduler.running:
        # Add STS pool maintenance job
        unified_scheduler.add_job(
            id='sts_pool_maintenance',
            func=_maintain_pool_job,
            args=[app],
            trigger='interval',
            minutes=15,  # Consider making this configurable
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60
        )

        unified_scheduler.add_job(
            id='stale_upload_cleanup',
            func=_cleanup_stale_uploads_job,
            args=[app],
            trigger='interval',
            hours=1,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        # Initialize embedding maintenance (will add its job to the same scheduler)
        try:
            from app.tasks.embedding_maintenance import init_embedding_maintenance
            init_embedding_maintenance(app, unified_scheduler)
        except Exception as e:
            app.logger.warning(f"Could not initialize embedding maintenance: {e}")

        try:
            from app.tasks.course_catalog_sync import init_course_catalog_sync
            init_course_catalog_sync(app, unified_scheduler)
        except Exception as e:
            app.logger.warning(f"Could not initialize official course catalog sync: {e}")

        unified_scheduler.start()
        app.logger.info("Unified background task scheduler started with STS and embedding maintenance.")
    else:
        app.logger.info("Unified scheduler already running.")

def _maintain_pool_job(app):
    """Wrapper function to run the maintenance task within app context."""
    with app.app_context():
        try:
            current_app.logger.info("Running STS pool maintenance task...")
            OSSService.maintain_pool()
            db.session.commit() # Commit once after all operations in maintain_pool are done
            current_app.logger.info("STS pool maintenance task finished.")
        except Exception as e:
            current_app.logger.error(f"Error during STS pool maintenance: {e}", exc_info=True)
            db.session.rollback() # Rollback on error


def _cleanup_stale_uploads_job(app):
    """Delete abandoned, unbound upload objects after their recovery window."""
    with app.app_context():
        try:
            cleaned = OSSService.cleanup_stale_unbound_uploads(max_age_hours=24)
            if cleaned:
                current_app.logger.info(f"Cleaned {cleaned} stale upload objects.")
        except Exception as error:
            db.session.rollback()
            current_app.logger.error(
                f"Error during stale upload cleanup: {error}",
                exc_info=True,
            )

# Optional: Add a shutdown hook for the scheduler if needed
# def shutdown_scheduler():
#     if scheduler.running:
#         scheduler.shutdown()
#         print("STS pool maintenance scheduler shut down.")

# You might register shutdown_scheduler using app.teardown_appcontext or atexit
