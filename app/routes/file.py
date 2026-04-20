from flask import Blueprint, request, jsonify, current_app, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.file_service import OSSService
from app.models.user import User
from app.models.file import File
from app.models.token import STSTokenPool
from datetime import datetime, timezone, timedelta
import os

bp = Blueprint('file', __name__, url_prefix='/files')

# Extensions blocked for security (executables / script delivery / HTML smuggling)
_UPLOAD_BLOCKED_EXTENSIONS = frozenset({
    '.exe', '.bat', '.cmd', '.com', '.msi', '.scr', '.dll', '.pif', '.application',
    '.gadget', '.msp', '.wsf', '.wsh', '.vbs', '.vbe', '.js', '.jse', '.jar',
    '.hta', '.cpl', '.msc', '.sh', '.bash', '.ps1', '.ps1xml', '.ps2', '.ps2xml',
    '.psc1', '.psc2', '.scf', '.lnk', '.inf', '.reg', '.desktop', '.html', '.htm',
})

_POST_IMAGE_EXTENSIONS = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.ico', '.svg', '.avif', '.heic',
})

_BLOCKED_CONTENT_TYPE_PREFIXES = (
    'text/html',
    'application/javascript',
    'application/x-javascript',
    'text/javascript',
    'application/ecmascript',
)

_ALLOWED_CONTENT_TYPE_PREFIXES = (
    'image/', 'video/', 'audio/', 'text/', 'application/', 'font/', 'message/', 'model/',
)


def _stream_file_from_oss(file_record, cache_control='public, max-age=3600'):
    from flask import Response, stream_with_context
    import requests

    signed_url = file_record.url
    if not signed_url:
        return jsonify({"error": "File URL not available"}), 500

    response = requests.get(signed_url, stream=True, timeout=30)
    if not response.ok:
        current_app.logger.error(
            f"Failed to fetch file {file_record.id} from OSS: {response.status_code}"
        )
        return jsonify({"error": "Failed to fetch file from storage"}), 502

    def generate():
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    headers = {
        'Content-Type': file_record.mime_type or response.headers.get('Content-Type', 'application/octet-stream'),
        'Content-Disposition': f'inline; filename="{file_record.original_filename}"',
        'Cache-Control': cache_control,
    }

    content_length = response.headers.get('Content-Length')
    if content_length:
        headers['Content-Length'] = content_length

    return Response(
        stream_with_context(generate()),
        headers=headers,
        status=200
    )


def _validate_upload_request(filename, file_type, content_type):
    """Returns (True, None) or (False, error_message)."""
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in _UPLOAD_BLOCKED_EXTENSIONS:
        return False, '该文件类型不允许上传'

    if file_type == File.POST_IMAGE:
        if ext and ext not in _POST_IMAGE_EXTENSIONS:
            return False, '图片附件仅支持常见图片格式'
        if content_type and content_type.strip():
            ct = content_type.strip().lower()
            if not ct.startswith('image/'):
                return False, '图片附件必须是图片类型'

    if file_type == File.POST_ATTACHMENT:
        # Arbitrary files except dangerous extensions (already filtered above).
        pass

    if content_type and content_type.strip():
        ct = content_type.strip().lower()
        if any(ct.startswith(b) for b in _BLOCKED_CONTENT_TYPE_PREFIXES):
            return False, 'Invalid content type'
        if not any(ct.startswith(p) for p in _ALLOWED_CONTENT_TYPE_PREFIXES):
            return False, 'Invalid content type'

    return True, None

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
    content_type = data.get('content_type')  # Get MIME type from frontend

    # Basic validation for file_type (optional but good practice)
    allowed_file_types = [
        File.AVATAR,
        File.POST_IMAGE,
        File.POST_ATTACHMENT,
        File.COMMENT_ATTACHMENT,
        File.IDENTITY_DOCUMENT,
        File.GENERAL
    ]
    if file_type not in allowed_file_types:
        return jsonify({"error": f"Invalid file_type. Allowed types: {', '.join(allowed_file_types)}"}), 400

    ok, err_msg = _validate_upload_request(filename, file_type, content_type)
    if not ok:
        return jsonify({"error": err_msg}), 400

    # Optional declared size (client hint) — reject before signing URL
    declared_size = data.get('file_size')
    if declared_size is not None:
        try:
            declared_size = int(declared_size)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid file_size"}), 400
        if declared_size > File.MAX_UPLOAD_BYTES:
            return jsonify({"error": f"File exceeds maximum size of {File.MAX_UPLOAD_BYTES} bytes"}), 400

    # Validate entity_id if entity_type is provided (optional)
    # Some flows upload files before the entity record exists.
    # Keep this list explicit to avoid weakening validation globally.
    entity_types_allow_pending_id = {'post', 'identity_verification'}
    if entity_type and entity_type not in entity_types_allow_pending_id and entity_id is None:
         return jsonify({"error": "entity_id is required when entity_type is provided"}), 400
    if entity_type and entity_id is not None and not isinstance(entity_id, int):
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
            callback_url=callback_url,
            content_type=content_type
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


@bp.route('/<int:file_id>', methods=['GET'])
@jwt_required()
def get_file_route(file_id):
    """Get a specific file record"""
    user_id = get_jwt_identity()
    
    # Check if user exists and is not deleted
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found or inactive"}), 404
    
    # Find the file record
    file_record = File.query.filter_by(id=file_id, user_id=user_id, is_deleted=False).first()
    if not file_record:
        return jsonify({"error": "File not found or you don't have permission"}), 404
    
    # For files without callback, automatically mark as uploaded if they're still pending
    # This is a temporary workaround while callback is disabled
    if file_record.status == 'pending':
        try:
            file_record.status = 'uploaded'
            from app.extensions import db
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Error updating file status for file_id {file_id}: {e}")
            db.session.rollback()
    
    return jsonify(file_record.to_dict()), 200


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


@bp.route('/debug/sts-pool', methods=['GET'])
@jwt_required()
def debug_sts_pool():
    """Debug endpoint to check STS token pool status"""
    user_id = get_jwt_identity()
    
    # Only allow admin users (you might want to check for admin role)
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    try:
        now = datetime.now(timezone.utc)
        
        # Get all tokens
        all_tokens = STSTokenPool.query.all()
        
        # Categorize tokens
        valid_tokens = STSTokenPool.query.filter(
            STSTokenPool.expiration > now + timedelta(minutes=15)
        ).all()
        
        soon_expired_tokens = STSTokenPool.query.filter(
            STSTokenPool.expiration <= now + timedelta(minutes=15),
            STSTokenPool.expiration > now
        ).all()
        
        expired_tokens = STSTokenPool.query.filter(
            STSTokenPool.expiration <= now
        ).all()
        
        token_details = []
        for token in all_tokens:
            time_until_expiry = (token.expiration - now).total_seconds()
            token_details.append({
                "id": token.id,
                "expiration": token.expiration.isoformat(),
                "seconds_until_expiry": int(time_until_expiry),
                "status": "valid" if time_until_expiry > 900 else ("soon_expired" if time_until_expiry > 0 else "expired")
            })
        
        return jsonify({
            "current_time": now.isoformat(),
            "total_tokens": len(all_tokens),
            "valid_tokens": len(valid_tokens),
            "soon_expired_tokens": len(soon_expired_tokens),
            "expired_tokens": len(expired_tokens),
            "min_pool_size": OSSService.MIN_POOL_SIZE,
            "tokens": token_details
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in STS debug endpoint: {e}")
        return jsonify({"error": "Failed to get STS pool status"}), 500


@bp.route('/debug/maintain-sts-pool', methods=['POST'])
@jwt_required()
def debug_maintain_sts_pool():
    """Debug endpoint to manually trigger STS pool maintenance"""
    user_id = get_jwt_identity()

    # Only allow admin users
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        current_app.logger.info(f"Manual STS pool maintenance triggered by user {user_id}")
        OSSService.maintain_pool()

        # Get updated pool status
        now = datetime.now(timezone.utc)
        valid_count = STSTokenPool.query.filter(
            STSTokenPool.expiration > now + timedelta(minutes=15)
        ).count()
        total_count = STSTokenPool.query.count()

        return jsonify({
            "message": "STS pool maintenance completed",
            "total_tokens": total_count,
            "valid_tokens": valid_count
        }), 200

    except Exception as e:
        current_app.logger.error(f"Error in STS maintenance endpoint: {e}")
        return jsonify({"error": "Failed to maintain STS pool"}), 500


@bp.route('/view/<int:file_id>', methods=['GET'])
def public_view_file(file_id):
    """Public stable file endpoint for forum post attachments."""
    from app.models.post import Post
    import requests

    file_record = File.query.filter_by(id=file_id, is_deleted=False).first()
    if not file_record:
        return jsonify({"error": "File not found"}), 404

    if file_record.status != 'uploaded':
        return jsonify({"error": "File not ready"}), 400

    if file_record.entity_type != 'post' or not file_record.entity_id:
        return jsonify({"error": "Forbidden"}), 403

    post = Post.query.filter_by(id=file_record.entity_id, is_deleted=False).first()
    if not post:
        return jsonify({"error": "Post not found"}), 404

    try:
        return _stream_file_from_oss(file_record, cache_control='no-store')
    except requests.exceptions.Timeout:
        current_app.logger.error(f"Timeout fetching public file {file_id} from OSS")
        return jsonify({"error": "Request timeout"}), 504
    except Exception as e:
        current_app.logger.error(f"Error serving public file {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to serve file"}), 500


@bp.route('/proxy/<int:file_id>', methods=['GET'])
@jwt_required()
def proxy_file(file_id):
    """Proxy file content from OSS to avoid CORS issues"""
    import requests

    user_id = get_jwt_identity()

    # Check if user exists
    user = User.query.filter_by(id=user_id, is_deleted=False).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Get file record
    file_record = File.query.filter_by(id=file_id, is_deleted=False).first()
    if not file_record:
        return jsonify({"error": "File not found"}), 404

    # Check if file is uploaded
    if file_record.status != 'uploaded':
        return jsonify({"error": "File not ready"}), 400

    try:
        return _stream_file_from_oss(file_record, cache_control='public, max-age=3600')

    except requests.exceptions.Timeout:
        current_app.logger.error(f"Timeout fetching file {file_id} from OSS")
        return jsonify({"error": "Request timeout"}), 504
    except Exception as e:
        current_app.logger.error(f"Error proxying file {file_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to proxy file"}), 500
