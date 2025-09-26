from apscheduler.schedulers.background import BackgroundScheduler
from app.extensions import db
from app.services.file_service import OSSService
from flask import current_app

# Unified scheduler for all background tasks
unified_scheduler = BackgroundScheduler(daemon=True)

def init_pool_maintenance(app):
    """Initializes and starts the unified background task scheduler."""
    global unified_scheduler

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

        # Initialize embedding maintenance (will add its job to the same scheduler)
        try:
            from app.tasks.embedding_maintenance import init_embedding_maintenance
            init_embedding_maintenance(app, unified_scheduler)
        except Exception as e:
            app.logger.warning(f"Could not initialize embedding maintenance: {e}")

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

# Optional: Add a shutdown hook for the scheduler if needed
# def shutdown_scheduler():
#     if scheduler.running:
#         scheduler.shutdown()
#         print("STS pool maintenance scheduler shut down.")

# You might register shutdown_scheduler using app.teardown_appcontext or atexit