# app/routes/background_tasks.py
"""
Background Tasks API Routes

Provides endpoints for monitoring and controlling background tasks.
Useful for debugging, monitoring, and manual task execution.
"""
import logging
from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

background_tasks_bp = Blueprint('background_tasks', __name__, url_prefix='/background-tasks')

@background_tasks_bp.route('/status', methods=['GET'])
@jwt_required()
def get_background_tasks_status():
    """Get status of all background tasks and scheduler"""
    try:
        current_user_id = get_jwt_identity()

        # Get unified scheduler status
        try:
            from app.tasks.sts_pool import unified_scheduler
            from app.tasks.embedding_maintenance import get_embedding_scheduler_status

            scheduler_info = {
                "running": unified_scheduler.running if unified_scheduler else False,
                "jobs": []
            }

            if unified_scheduler:
                for job in unified_scheduler.get_jobs():
                    scheduler_info["jobs"].append({
                        "id": job.id,
                        "name": job.name or job.id,
                        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                        "trigger": str(job.trigger)
                    })

            embedding_status = get_embedding_scheduler_status()

        except Exception as e:
            logger.warning(f"Could not get scheduler status: {e}")
            scheduler_info = {"running": False, "jobs": [], "error": str(e)}
            embedding_status = {"stats": {}}

        return jsonify({
            "success": True,
            "scheduler": scheduler_info,
            "embedding_maintenance": embedding_status,
            "embedding_service_stats": embedding_service.get_embedding_stats()
        }), 200

    except Exception as e:
        logger.error(f"Error getting background tasks status: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get background tasks status"
        }), 500

# Note: Scheduler control endpoints removed as the unified scheduler
# is managed automatically. Individual task control can be added if needed.

@background_tasks_bp.route('/embedding-maintenance/run', methods=['POST'])
@jwt_required()
def run_embedding_maintenance():
    """Force run embedding maintenance task with custom parameters"""
    try:
        current_user_id = get_jwt_identity()

        # TODO: Add admin user check here
        # if not is_admin_user(current_user_id):
        #     return jsonify({"success": False, "message": "Admin access required"}), 403

        # Get parameters from request
        data = request.get_json() or {}
        target_type = data.get('target_type', 'all')  # 'profiles', 'projects', or 'all'
        batch_size = min(int(data.get('batch_size', 100)), 500)  # Max 500 for safety

        # Use unified task system
        from app.tasks.embedding_maintenance import force_run_embedding_maintenance
        from flask import current_app

        result = force_run_embedding_maintenance(
            app=current_app._get_current_object(),
            target_type=target_type,
            batch_size=batch_size
        )

        return jsonify({
            "success": True,
            "message": f"Embedding maintenance completed for {target_type}",
            "result": result
        }), 200

    except Exception as e:
        logger.error(f"Error running embedding maintenance: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to run embedding maintenance"
        }), 500

@background_tasks_bp.route('/embedding-service/stats', methods=['GET'])
@jwt_required()
def get_embedding_service_stats():
    """Get embedding service statistics"""
    try:
        current_user_id = get_jwt_identity()

        stats = embedding_service.get_embedding_stats()
        model_info = embedding_service.get_model_info()

        return jsonify({
            "success": True,
            "stats": stats,
            "model_configurations": model_info
        }), 200

    except Exception as e:
        logger.error(f"Error getting embedding service stats: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get embedding service stats"
        }), 500

@background_tasks_bp.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for background tasks (no auth required)"""
    try:
        # Check unified scheduler status
        try:
            from app.tasks.sts_pool import unified_scheduler
            scheduler_running = unified_scheduler.running if unified_scheduler else False
            job_count = len(unified_scheduler.get_jobs()) if unified_scheduler else 0
        except Exception:
            scheduler_running = False
            job_count = 0

        # Basic health indicators
        health_status = {
            "scheduler_running": scheduler_running,
            "jobs_registered": job_count,
            "embedding_service_initialized": embedding_service._initialized
        }

        # Overall health
        is_healthy = (
            health_status["scheduler_running"] and
            health_status["jobs_registered"] > 0 and
            health_status["embedding_service_initialized"]
        )

        return jsonify({
            "healthy": is_healthy,
            "status": health_status,
            "timestamp": None  # Could add timestamp if needed
        }), 200 if is_healthy else 503

    except Exception as e:
        logger.error(f"Error in background tasks health check: {e}")
        return jsonify({
            "healthy": False,
            "error": str(e)
        }), 503