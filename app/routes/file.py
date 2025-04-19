from flask import Blueprint, request, jsonify, current_app, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.file_service import OSSService
from app.models.user import User
from app.models.file import File

bp = Blueprint('file', __name__, url_prefix='/files')

@bp.route('/upload', methods=['POST'])
@jwt_required()
def generate_upload_url():
    user_id = get_jwt_identity()
    data = request.get_json()

    if not data or 'filename' not in data:
        return jsonify({"error": "Filename required"}), 400

    # Get categorization parameters from request body
    filename = data['filename']
    file_type = data.get('file_type', File.GENERAL) # Default to general
    entity_type = data.get('entity_type')
    entity_id = data.get('entity_id')

    # Basic validation for file_type (optional but good practice)
    allowed_file_types = [File.AVATAR, File.POST_IMAGE, File.COMMENT_ATTACHMENT, File.GENERAL]
    if file_type not in allowed_file_types:
        return jsonify({"error": f"Invalid file_type. Allowed types: {', '.join(allowed_file_types)}"}), 400

    # Validate entity_id if entity_type is provided (optional)
    if entity_type and entity_id is None:
         return jsonify({"error": "entity_id is required when entity_type is provided"}), 400
    if entity_type and not isinstance(entity_id, int):
         try:
             entity_id = int(entity_id)
         except (ValueError, TypeError):
             return jsonify({"error": "entity_id must be an integer"}), 400


    # Check if user exists and is not deleted
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        current_app.logger.warning(f"Upload attempt by non-existent or inactive user ID: {user_id}")
        return jsonify({"error": "User not found or inactive"}), 404

    try:
        # Generate callback URL dynamically
        # Ensure SERVER_NAME is configured in Flask for url_for to generate external URLs correctly
        # Or construct it manually if needed: callback_url = f"{request.host_url.rstrip('/')}{url_for('file.oss_callback')}"
        callback_url = url_for('file.oss_callback', _external=True)

        # Generate signed URL with callback and categorization
        signed_url, object_name, file_id = OSSService.generate_signed_url(
            user_id=user_id,
            filename=filename,
            file_type=file_type,
            entity_type=entity_type,
            entity_id=entity_id,
            callback_url=callback_url
        )

        return jsonify({
            "signed_url": signed_url,
            "object_name": object_name,
            "file_id": file_id,
            "file_type": file_type, # Return type for frontend context
            "url": f"{current_app.config.get('OSS_PUBLIC_URL', '')}/{object_name}", # Provide the final public URL
            "expiration_seconds": current_app.config['OSS_TOKEN_DURATION'] # Indicate URL validity duration
        }), 200
    except Exception as e:
        current_app.logger.error(f"OSS URL Generation Error for user {user_id}: {str(e)}", exc_info=True)
        return jsonify({"error": "Failed to generate upload URL. Please try again later."}), 500

# --- Callback Verification Helper (Optional but Recommended) ---
# def verify_oss_callback_signature(request):
#     """Verifies the signature of the callback request from OSS."""
#     try:
#         pub_key_url_base64 = request.headers.get('x-oss-pub-key-url')
#         authorization_base64 = request.headers.get('Authorization')
#         request_uri = request.path # Or request.full_path depending on what OSS signs
#         # OSS typically signs path + query string if present
#         if request.query_string:
#             request_uri += '?' + request.query_string.decode('utf-8')
#
#         if not pub_key_url_base64 or not authorization_base64:
#             current_app.logger.warning("Callback missing signature headers.")
#             return False
#
#         # 1. Decode public key URL
#         pub_key_url = base64.b64decode(pub_key_url_base64).decode('utf-8')
#
#         # 2. Verify public key URL hostname (important security step)
#         parsed_url = urlparse(pub_key_url)
#         if not parsed_url.hostname or not parsed_url.hostname.endswith('.aliyuncs.com'):
#              current_app.logger.error(f"Invalid public key URL hostname: {parsed_url.hostname}")
#              return False
#
#         # 3. Fetch the public key (cache this result in production)
#         # response = requests.get(pub_key_url)
#         # response.raise_for_status()
#         # pub_key = response.text
#         # For testing, use a known key:
#         # pub_key = """-----BEGIN PUBLIC KEY-----
#         # MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA... (Your OSS Public Key) ...AQAB
#         # -----END PUBLIC KEY-----"""
#         # This needs a proper implementation fetching the key securely.
#         # Placeholder: Assume key is fetched and valid for now.
#         pub_key = "YOUR_OSS_PUBLIC_KEY_PEM_FORMAT" # Replace with actual key fetching/storage
#         if "YOUR_OSS_PUBLIC_KEY" in pub_key: # Simple check if placeholder is still there
#              current_app.logger.warning("OSS Callback verification skipped: Public key not configured.")
#              return True # Skip verification if key isn't set up
#
#         # 4. Decode the signature
#         signature = base64.b64decode(authorization_base64)
#
#         # 5. Construct the string to sign
#         # Format: HTTP-Method + "\n" + Content-MD5 + "\n" + Content-Type + "\n" + Date + "\n" + CanonicalizedOSSHeaders + CanonicalizedResource
#         # For callbacks, it's simpler: The request body itself might be signed, or specific parts.
#         # OSS documentation specifies signing the URI path + query string + callback body.
#         # Let's assume it signs: POST\n\n{Content-Type}\n{Date}\n{CanonicalizedHeaders}{CanonicalizedResource}
#         # Or more likely for callbacks: PathAndQuery + '\n' + RequestBody
#         # This needs careful checking against OSS documentation for callbacks.
#         # Assuming Path + Query + Body for now:
#         string_to_sign = f"{request_uri}\n{request.get_data(as_text=True)}"
#
#         # 6. Verify the signature
#         from cryptography.hazmat.primitives import hashes
#         from cryptography.hazmat.primitives.asymmetric import padding
#         from cryptography.hazmat.primitives import serialization
#         from cryptography.hazmat.backends import default_backend
#
#         public_key_obj = serialization.load_pem_public_key(pub_key.encode('utf-8'), backend=default_backend())
#
#         try:
#             public_key_obj.verify(
#                 signature,
#                 string_to_sign.encode('utf-8'),
#                 padding.PKCS1v15(), # Or padding.PSS depending on OSS config
#                 hashes.SHA1() # Or SHA256 depending on OSS config
#             )
#             current_app.logger.info("OSS Callback signature verified successfully.")
#             return True
#         except Exception as e: # Be specific with exception type if possible
#             current_app.logger.error(f"OSS Callback signature verification failed: {e}")
#             return False
#
#     except Exception as e:
#         current_app.logger.error(f"Error during callback verification setup: {e}")
#         return False # Fail verification on any error

@bp.route('/callback', methods=['POST'])
def oss_callback():
    """Handle OSS callback after successful upload"""
    # --- Add Verification Step ---
    # if not verify_oss_callback_signature(request):
    #     current_app.logger.error("OSS Callback verification failed.")
    #     # OSS expects a JSON response even on failure, but the status code matters.
    #     # Returning 400 might cause OSS to retry or log an error.
    #     return jsonify({"Status": "Error", "Message": "Callback verification failed"}), 400

    try:
        # Get callback data from FORM POST
        # Ensure keys match exactly what OSS sends in callbackBody
        object_name = request.form.get('object_name')
        file_id = request.form.get('file_id')
        file_size = request.form.get('size')
        mime_type = request.form.get('mimeType')

        if not file_id:
            current_app.logger.error("Callback received without file_id.")
            return jsonify({"Status": "Error", "Message": "Missing file_id"}), 400 # Return error JSON for OSS

        if not object_name:
             current_app.logger.error(f"Callback for file_id {file_id} received without object_name.")
             return jsonify({"Status": "Error", "Message": "Missing object_name"}), 400

        # Update file status
        file_record = OSSService.update_file_status(
            file_id=int(file_id),
            status='uploaded',
            object_name=object_name, # Pass for verification
            file_size=file_size,     # Pass raw value
            mime_type=mime_type
        )

        if not file_record:
            # Error already logged in service method
            return jsonify({"Status": "Error", "Message": "Failed to update file record"}), 500 # Internal error

        # Return success JSON response required by OSS
        # The content can be customized if the client needs info from the callback.
        return jsonify({
            "Status": "OK",
            # Optionally include file info if needed by any client polling this
            # "file": file_record.to_dict()
        }), 200

    except Exception as e:
        # Log the exception details
        file_id_log = request.form.get('file_id', 'unknown')
        current_app.logger.error(f"Callback processing error for file_id {file_id_log}: {str(e)}", exc_info=True)
        # Return error JSON response to OSS
        return jsonify({"Status": "Error", "Message": "Internal server error processing callback"}), 500


@bp.route('/<int:file_id>', methods=['DELETE'])
@jwt_required()
def delete_file_route(file_id):
    """Soft delete a file record"""
    user_id = get_jwt_identity()

    # Check if user exists and is not deleted
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found or inactive"}), 404

    deleted_file = OSSService.delete_file(file_id, user_id)

    if not deleted_file:
        # Could be not found, or already deleted, or DB error
        # Check if it exists at all first
        exists = File.query.filter_by(id=file_id, user_id=user_id).first()
        if not exists:
             return jsonify({"error": "File not found or you don't have permission"}), 404
        else:
             # Assume DB error if it exists but deletion failed
             return jsonify({"error": "Failed to delete file record"}), 500

    return jsonify({"message": "File marked for deletion"}), 200


@bp.route('', methods=['GET'])
@jwt_required()
def get_user_files():
    """Get files uploaded by the current user, with optional filters"""
    user_id = get_jwt_identity()

    # Check if user exists and is not deleted
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found or inactive"}), 404

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)

    # Get filter parameters
    file_type = request.args.get('file_type')
    entity_type = request.args.get('entity_type')
    entity_id = request.args.get('entity_id', type=int)
    status = request.args.get('status')

    # Query files, always filter by user_id and not deleted
    query = File.query.filter_by(
        user_id=user_id,
        is_deleted=False
    )

    # Apply optional filters
    if file_type:
        query = query.filter(File.file_type == file_type)
    if entity_type:
        query = query.filter(File.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(File.entity_id == entity_id)
    if status:
        query = query.filter(File.status == status)


    # Order by creation date
    query = query.order_by(File.created_at.desc())

    # Paginate results
    try:
        pagination = query.paginate(page=page, per_page=limit, error_out=False) # error_out=False prevents 404 on empty page
    except Exception as e:
         current_app.logger.error(f"Pagination error for user {user_id} files: {e}")
         return jsonify({"error": "Invalid pagination parameters"}), 400


    return jsonify({
        "files": [file.to_dict() for file in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page
    }), 200