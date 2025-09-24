from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required
from app.services.project_interview_service import project_interview_service
import logging

logger = logging.getLogger(__name__)

project_interview_bp = Blueprint('project_interview', __name__, url_prefix='/project-interview')

@project_interview_bp.route('/start', methods=['POST'])
@jwt_required()
def start_interview():
    """Start project interview with initial description"""
    try:
        data = request.get_json()
        if not data or not data.get('initial_description'):
            return jsonify({
                "success": False,
                "message": "Initial description is required"
            }), 400

        initial_description = data['initial_description'].strip()
        if not initial_description:
            return jsonify({
                "success": False,
                "message": "Initial description cannot be empty"
            }), 400

        result = project_interview_service.start_interview(initial_description)

        if result.get('success'):
            # Store interview session data (you might want to use Redis or database for persistence)
            return jsonify({
                "success": True,
                "data": {
                    "question": result['question'],
                    "options": result['options'],
                    "round": result['round'],
                    "interview_id": f"interview_{get_jwt_identity()}_{hash(initial_description) % 10000}"
                }
            }), 200
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.error(f"Error in start_interview endpoint: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error"
        }), 500

@project_interview_bp.route('/answer', methods=['POST'])
@jwt_required()
def submit_answer():
    """Submit answer to current question and get next question"""
    try:
        data = request.get_json()
        required_fields = ['interview_history', 'current_answer', 'round']
        if not data or not all(field in data for field in required_fields):
            return jsonify({
                "success": False,
                "message": "interview_history, current_answer, and round are required"
            }), 400

        interview_history = data['interview_history']
        current_answer = data['current_answer'].strip()
        round_number = data['round']
        initial_description = data.get('initial_description', '')  # Get initial description

        if not current_answer:
            return jsonify({
                "success": False,
                "message": "Answer cannot be empty"
            }), 400

        # Add current answer to history
        if interview_history and len(interview_history) > 0:
            interview_history[-1]['answer'] = current_answer

        next_round = round_number + 1
        result = project_interview_service.continue_interview(interview_history, next_round, initial_description)

        if result.get('success'):
            if result.get('completed'):
                # Interview completed, return synthesized description
                return jsonify({
                    "success": True,
                    "completed": True,
                    "data": {
                        "description": result['description']
                    }
                }), 200
            else:
                # Continue interview
                return jsonify({
                    "success": True,
                    "completed": False,
                    "data": {
                        "question": result['question'],
                        "options": result['options'],
                        "round": result['round']
                    }
                }), 200
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.error(f"Error in submit_answer endpoint: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error"
        }), 500

@project_interview_bp.route('/synthesize', methods=['POST'])
@jwt_required()
def synthesize_description():
    """Force synthesize description from current interview history"""
    try:
        data = request.get_json()
        if not data or not data.get('interview_history'):
            return jsonify({
                "success": False,
                "message": "interview_history is required"
            }), 400

        interview_history = data['interview_history']
        initial_description = data.get('initial_description', '')  # Get initial description

        result = project_interview_service.synthesize_description(interview_history, initial_description)

        if result.get('success'):
            return jsonify({
                "success": True,
                "data": {
                    "description": result['description']
                }
            }), 200
        else:
            return jsonify(result), 500

    except Exception as e:
        logger.error(f"Error in synthesize_description endpoint: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error"
        }), 500