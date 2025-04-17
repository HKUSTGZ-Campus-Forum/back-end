from apscheduler.schedulers.background import BackgroundScheduler
from app.extensions import db
from app.services.file_service import OSSService
from flask import current_app # Import current_app if needed within maintain_pool directly, though app_context is better

scheduler = BackgroundScheduler(daemon=True) # Set daemon=True so it exits when main thread exits

def init_pool_maintenance(app):
    """Initializes and starts the STS token pool maintenance scheduler."""
    if not scheduler.running:
        # Add the job with the app instance passed as an argument
        # The actual function `_maintain_pool_job` will run within the app context
        scheduler.add_job(
            id='sts_pool_maintenance',
            func=_maintain_pool_job, # Target the wrapper function
            args=[app], # Pass the app instance
            trigger='interval',
            minutes=15, # Consider making this configurable
            max_instances=1,
            coalesce=True, # Prevent job from running multiple times if scheduler was down
            misfire_grace_time=60 # Allow 60 seconds grace period if job misfires
        )
        scheduler.start()
        app.logger.info("STS pool maintenance scheduler started.")

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