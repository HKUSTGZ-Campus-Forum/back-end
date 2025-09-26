# app/tasks/embedding_maintenance.py
"""
Embedding Maintenance Tasks using APScheduler

Integrates with the existing task system to provide:
- Auto-recovery for missing embeddings
- Validation of existing embeddings
- Performance monitoring
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from sqlalchemy import and_
from flask import current_app
from apscheduler.schedulers.background import BackgroundScheduler
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.services.matching_service import matching_service
from app.extensions import db

logger = logging.getLogger(__name__)

# Global scheduler instance (shared with other tasks)
embedding_scheduler: Optional[BackgroundScheduler] = None

# Global stats tracking
embedding_stats = {
    "last_run": None,
    "total_profiles_processed": 0,
    "total_projects_processed": 0,
    "total_profiles_fixed": 0,
    "total_projects_fixed": 0,
    "total_errors": 0,
    "last_run_stats": {}
}

def init_embedding_maintenance(app, scheduler: BackgroundScheduler = None):
    """Initialize embedding maintenance tasks with existing scheduler"""
    global embedding_scheduler

    if scheduler:
        embedding_scheduler = scheduler
    else:
        # Fallback to creating new scheduler if none provided
        embedding_scheduler = BackgroundScheduler(daemon=True)

    # Get configuration from app config
    interval_minutes = app.config.get('EMBEDDING_MAINTENANCE_INTERVAL_MINUTES', 60)

    # Add embedding maintenance job
    if not embedding_scheduler.running:
        embedding_scheduler.add_job(
            id='embedding_maintenance',
            func=_embedding_maintenance_job,
            args=[app],
            trigger='interval',
            minutes=interval_minutes,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300  # 5 minutes grace period
        )

        if not scheduler:  # Only start if we created our own scheduler
            embedding_scheduler.start()

        app.logger.info(f"Embedding maintenance scheduled to run every {interval_minutes} minutes")
    else:
        # Add job to existing running scheduler
        try:
            embedding_scheduler.add_job(
                id='embedding_maintenance',
                func=_embedding_maintenance_job,
                args=[app],
                trigger='interval',
                minutes=interval_minutes,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300
            )
            app.logger.info(f"Added embedding maintenance to existing scheduler")
        except Exception as e:
            app.logger.warning(f"Could not add embedding maintenance job: {e}")

def _embedding_maintenance_job(app):
    """Wrapper function to run embedding maintenance within app context"""
    global embedding_stats

    with app.app_context():
        start_time = datetime.now(timezone.utc)
        run_stats = {
            "start_time": start_time,
            "profiles_processed": 0,
            "projects_processed": 0,
            "profiles_fixed": 0,
            "projects_fixed": 0,
            "errors": 0,
            "duration_seconds": 0
        }

        try:
            current_app.logger.info("🔧 Starting embedding maintenance task...")

            # Get configuration
            batch_size = current_app.config.get('EMBEDDING_MAINTENANCE_BATCH_SIZE', 50)
            max_time_minutes = current_app.config.get('EMBEDDING_MAINTENANCE_MAX_TIME_MINUTES', 30)
            max_end_time = start_time + timedelta(minutes=max_time_minutes)

            # Fix missing profile embeddings
            if datetime.now(timezone.utc) < max_end_time:
                profile_stats = _fix_missing_profile_embeddings(batch_size, max_end_time)
                run_stats["profiles_processed"] += profile_stats["processed"]
                run_stats["profiles_fixed"] += profile_stats["fixed"]
                run_stats["errors"] += profile_stats["errors"]

            # Fix missing project embeddings
            if datetime.now(timezone.utc) < max_end_time:
                project_stats = _fix_missing_project_embeddings(batch_size, max_end_time)
                run_stats["projects_processed"] += project_stats["processed"]
                run_stats["projects_fixed"] += project_stats["fixed"]
                run_stats["errors"] += project_stats["errors"]

            # Update global stats
            end_time = datetime.now(timezone.utc)
            run_stats["duration_seconds"] = (end_time - start_time).total_seconds()

            embedding_stats.update({
                "last_run": end_time,
                "total_profiles_processed": embedding_stats["total_profiles_processed"] + run_stats["profiles_processed"],
                "total_projects_processed": embedding_stats["total_projects_processed"] + run_stats["projects_processed"],
                "total_profiles_fixed": embedding_stats["total_profiles_fixed"] + run_stats["profiles_fixed"],
                "total_projects_fixed": embedding_stats["total_projects_fixed"] + run_stats["projects_fixed"],
                "total_errors": embedding_stats["total_errors"] + run_stats["errors"],
                "last_run_stats": run_stats
            })

            if run_stats["profiles_fixed"] > 0 or run_stats["projects_fixed"] > 0:
                current_app.logger.info(
                    f"✅ Embedding maintenance completed: "
                    f"profiles_fixed={run_stats['profiles_fixed']}, "
                    f"projects_fixed={run_stats['projects_fixed']}, "
                    f"duration={run_stats['duration_seconds']:.2f}s"
                )
            else:
                current_app.logger.info("✅ Embedding maintenance completed: no missing embeddings found")

        except Exception as e:
            run_stats["errors"] += 1
            embedding_stats["total_errors"] += 1
            current_app.logger.error(f"❌ Error during embedding maintenance: {e}", exc_info=True)
            db.session.rollback()

def _fix_missing_profile_embeddings(batch_size: int, max_end_time: datetime) -> Dict:
    """Fix profiles that are missing embeddings"""
    stats = {"processed": 0, "fixed": 0, "errors": 0}

    try:
        # Find active, complete profiles without embeddings
        profiles_without_embeddings = UserProfile.query.filter(
            and_(
                UserProfile.is_active == True,
                UserProfile.embedding.is_(None)
            )
        ).limit(batch_size).all()

        current_app.logger.info(f"Found {len(profiles_without_embeddings)} profiles without embeddings")

        for profile in profiles_without_embeddings:
            # Check time limit
            if datetime.now(timezone.utc) >= max_end_time:
                current_app.logger.info("Time limit reached, stopping profile processing")
                break

            try:
                stats["processed"] += 1

                # Only fix complete profiles
                if not profile.is_complete():
                    continue

                current_app.logger.info(f"🔧 Auto-fixing embedding for profile {profile.id}")

                # Generate embedding
                success = matching_service.update_profile_embedding(profile.id)

                if success:
                    db.session.commit()
                    stats["fixed"] += 1
                    current_app.logger.info(f"✅ Fixed embedding for profile {profile.id}")
                else:
                    stats["errors"] += 1
                    current_app.logger.warning(f"❌ Failed to fix embedding for profile {profile.id}")

            except Exception as e:
                stats["errors"] += 1
                current_app.logger.error(f"❌ Error processing profile {profile.id}: {e}")
                db.session.rollback()

    except Exception as e:
        current_app.logger.error(f"Error in profile embedding fix task: {e}")
        stats["errors"] += 1

    return stats

def _fix_missing_project_embeddings(batch_size: int, max_end_time: datetime) -> Dict:
    """Fix projects that are missing embeddings"""
    stats = {"processed": 0, "fixed": 0, "errors": 0}

    try:
        # Find non-deleted projects without embeddings
        projects_without_embeddings = Project.query.filter(
            and_(
                Project.is_deleted == False,
                Project.embedding.is_(None)
            )
        ).limit(batch_size).all()

        current_app.logger.info(f"Found {len(projects_without_embeddings)} projects without embeddings")

        for project in projects_without_embeddings:
            # Check time limit
            if datetime.now(timezone.utc) >= max_end_time:
                current_app.logger.info("Time limit reached, stopping project processing")
                break

            try:
                stats["processed"] += 1

                # Only fix projects with content
                text_repr = project.get_text_representation()
                if not text_repr.strip():
                    continue

                current_app.logger.info(f"🔧 Auto-fixing embedding for project {project.id}")

                # Generate embedding
                success = matching_service.update_project_embedding(project.id)

                if success:
                    db.session.commit()
                    stats["fixed"] += 1
                    current_app.logger.info(f"✅ Fixed embedding for project {project.id}")
                else:
                    stats["errors"] += 1
                    current_app.logger.warning(f"❌ Failed to fix embedding for project {project.id}")

            except Exception as e:
                stats["errors"] += 1
                current_app.logger.error(f"❌ Error processing project {project.id}: {e}")
                db.session.rollback()

    except Exception as e:
        current_app.logger.error(f"Error in project embedding fix task: {e}")
        stats["errors"] += 1

    return stats

def get_embedding_maintenance_stats() -> Dict:
    """Get current statistics for embedding maintenance"""
    return embedding_stats.copy()

def force_run_embedding_maintenance(app, target_type: str = "all", batch_size: int = 100) -> Dict:
    """Force run embedding maintenance task on demand"""
    with app.app_context():
        current_app.logger.info(f"🚀 Force running embedding maintenance for: {target_type}")

        max_end_time = datetime.now(timezone.utc) + timedelta(hours=1)  # 1 hour max
        results = {}

        if target_type in ['profiles', 'all']:
            results['profiles'] = _fix_missing_profile_embeddings(batch_size, max_end_time)

        if target_type in ['projects', 'all']:
            results['projects'] = _fix_missing_project_embeddings(batch_size, max_end_time)

        return results

def get_embedding_scheduler_status() -> Dict:
    """Get status of the embedding maintenance scheduler"""
    global embedding_scheduler

    if not embedding_scheduler:
        return {"scheduler_running": False, "jobs": []}

    jobs = []
    for job in embedding_scheduler.get_jobs():
        if job.id == 'embedding_maintenance':
            jobs.append({
                "id": job.id,
                "name": job.name or job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            })

    return {
        "scheduler_running": embedding_scheduler.running,
        "jobs": jobs,
        "stats": get_embedding_maintenance_stats()
    }