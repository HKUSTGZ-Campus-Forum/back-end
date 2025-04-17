from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.file_service import OSSService
from app.models.user import User

bp = Blueprint('file', __name__, url_prefix='/files')

@bp.route('/upload', methods=['POST'])
@jwt_required()
def generate_upload_url():
    user_id = get_jwt_identity()
    data = request.get_json()
    
    if not data or 'filename' not in data:
        return jsonify({"error": "Filename required"}), 400
    
    # Check if user exists and is not deleted
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found or inactive"}), 404
    
    try:
        signed_url, object_name = OSSService.generate_signed_url(user_id, data['filename'])
        return jsonify({
            "signed_url": signed_url,
            "object_name": object_name,
            "expiration": current_app.config['OSS_TOKEN_DURATION']
        }), 200
    except Exception as e:
        current_app.logger.error(f"OSS Error: {str(e)}")
        return jsonify({"error": "Failed to generate upload URL"}), 500