from apscheduler.schedulers.background import BackgroundScheduler
from app.extensions import db
from app.services.file_service import OSSService

scheduler = BackgroundScheduler()

def init_pool_maintenance(app):
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(
            id='sts_pool_maintenance',
            func=maintain_pool,
            trigger='interval',
            minutes=15,
            max_instances=1
        )

def maintain_pool():
    with app.app_context():
        OSSService.maintain_pool()
        db.session.commit()