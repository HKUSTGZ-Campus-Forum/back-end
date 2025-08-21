# app/routes/oauth.py
from flask import Blueprint, request, jsonify, render_template_string, redirect, url_for, session
from flask_jwt_extended import jwt_required, get_jwt_identity, get_current_user
from app.extensions import db
from app.models.oauth_client import OAuthClient
from app.models.oauth_token import OAuthToken
from app.models.oauth_authorization_code import OAuthAuthorizationCode
from app.models.user import User
from datetime import datetime, timezone, timedelta
import secrets
import string
import json
from urllib.parse import urlencode, parse_qs, urlparse

oauth_bp = Blueprint('oauth', __name__)

# OAuth2 Authorization Server Endpoints

@oauth_bp.route('/oauth/authorize', methods=['GET', 'POST'])
@jwt_required()
def authorize():
    """OAuth2 Authorization Endpoint
    
    Handles authorization requests from OAuth clients.
    GET: Display consent screen
    POST: Process user consent and generate authorization code
    """
    
    # Extract OAuth2 parameters
    response_type = request.values.get('response_type')
    client_id = request.values.get('client_id')
    redirect_uri = request.values.get('redirect_uri')
    scope = request.values.get('scope', '')
    state = request.values.get('state')
    code_challenge = request.values.get('code_challenge')
    code_challenge_method = request.values.get('code_challenge_method', 'plain')
    
    # Validate required parameters
    if not all([response_type, client_id, redirect_uri]):
        return jsonify({
            'error': 'invalid_request',
            'error_description': 'Missing required parameters'
        }), 400
    
    # Check if response_type is supported
    if response_type != 'code':
        return jsonify({
            'error': 'unsupported_response_type',
            'error_description': 'Only "code" response type is supported'
        }), 400
    
    # Validate client
    client = OAuthClient.query.filter_by(client_id=client_id, is_active=True).first()
    if not client:
        return jsonify({
            'error': 'invalid_client',
            'error_description': 'Client not found or inactive'
        }), 400
    
    # Validate redirect URI
    if not client.check_redirect_uri(redirect_uri):
        return jsonify({
            'error': 'invalid_redirect_uri',
            'error_description': 'Redirect URI not allowed for this client'
        }), 400
    
    # Validate response type
    if not client.check_response_type(response_type):
        return jsonify({
            'error': 'unauthorized_client',
            'error_description': 'Client not authorized for this response type'
        }), 400
    
    # Get current user
    user = get_current_user()
    if not user:
        return jsonify({
            'error': 'access_denied',
            'error_description': 'User not authenticated'
        }), 401
    
    # Filter scopes to only allowed ones
    allowed_scope = client.get_allowed_scope(scope)
    
    if request.method == 'GET':
        # Display consent screen
        consent_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authorize {{ client.client_name }}</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 500px; margin: 50px auto; padding: 20px; }
                .app-info { text-align: center; margin-bottom: 30px; }
                .app-name { font-size: 24px; font-weight: bold; margin-bottom: 10px; }
                .user-info { background: #f5f5f5; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
                .scopes { margin: 20px 0; }
                .scope-item { margin: 10px 0; padding: 10px; background: #f9f9f9; border-radius: 4px; }
                .buttons { text-align: center; margin-top: 30px; }
                .btn { padding: 12px 24px; margin: 0 10px; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; }
                .btn-primary { background: #007bff; color: white; }
                .btn-secondary { background: #6c757d; color: white; }
                .btn:hover { opacity: 0.9; }
            </style>
        </head>
        <body>
            <div class="app-info">
                <div class="app-name">{{ client.client_name }}</div>
                <p>{{ client.client_description or 'An application' }} wants to access your account</p>
            </div>
            
            <div class="user-info">
                <strong>Signed in as:</strong> {{ user.username }} ({{ user.email or 'No email' }})
            </div>
            
            <div class="scopes">
                <strong>Requested permissions:</strong>
                {% for scope_name in scopes %}
                <div class="scope-item">
                    {% if scope_name == 'profile' %}
                        📋 <strong>Profile</strong> - Access your basic profile information (username, avatar)
                    {% elif scope_name == 'email' %}
                        📧 <strong>Email</strong> - Access your email address
                    {% elif scope_name == 'courses' %}
                        🎓 <strong>Courses</strong> - Access your course enrollment data
                    {% else %}
                        🔧 <strong>{{ scope_name }}</strong> - Custom permission
                    {% endif %}
                </div>
                {% endfor %}
            </div>
            
            <form method="POST">
                <input type="hidden" name="response_type" value="{{ response_type }}">
                <input type="hidden" name="client_id" value="{{ client_id }}">
                <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
                <input type="hidden" name="scope" value="{{ allowed_scope }}">
                <input type="hidden" name="state" value="{{ state }}">
                <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
                <input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
                
                <div class="buttons">
                    <button type="submit" name="action" value="allow" class="btn btn-primary">Allow</button>
                    <button type="submit" name="action" value="deny" class="btn btn-secondary">Deny</button>
                </div>
            </form>
        </body>
        </html>
        """
        
        scopes = allowed_scope.split(' ') if allowed_scope else []
        return render_template_string(
            consent_html,
            client=client,
            user=user,
            scopes=scopes,
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            allowed_scope=allowed_scope,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method
        )
    
    elif request.method == 'POST':
        # Process user consent
        action = request.form.get('action')
        
        if action != 'allow':
            # User denied access
            error_params = {
                'error': 'access_denied',
                'error_description': 'User denied access'
            }
            if state:
                error_params['state'] = state
                
            redirect_url = f"{redirect_uri}?{urlencode(error_params)}"
            return redirect(redirect_url)
        
        # User approved access - generate authorization code
        auth_code = OAuthAuthorizationCode(
            user_id=user.id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=allowed_scope,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method
        )
        
        db.session.add(auth_code)
        db.session.commit()
        
        # Redirect back to client with authorization code
        success_params = {
            'code': auth_code.code
        }
        if state:
            success_params['state'] = state
            
        redirect_url = f"{redirect_uri}?{urlencode(success_params)}"
        return redirect(redirect_url)


@oauth_bp.route('/oauth/token', methods=['POST'])
def token():
    """OAuth2 Token Endpoint
    
    Exchanges authorization codes for access tokens.
    """
    
    # Extract token request parameters
    grant_type = request.form.get('grant_type')
    code = request.form.get('code')
    redirect_uri = request.form.get('redirect_uri')
    client_id = request.form.get('client_id')
    client_secret = request.form.get('client_secret')
    code_verifier = request.form.get('code_verifier')  # PKCE
    
    # Validate grant type
    if grant_type != 'authorization_code':
        return jsonify({
            'error': 'unsupported_grant_type',
            'error_description': 'Only "authorization_code" grant type is supported'
        }), 400
    
    # Validate required parameters
    if not all([code, redirect_uri, client_id]):
        return jsonify({
            'error': 'invalid_request',
            'error_description': 'Missing required parameters'
        }), 400
    
    # Validate client credentials
    client = OAuthClient.query.filter_by(client_id=client_id, is_active=True).first()
    if not client:
        return jsonify({
            'error': 'invalid_client',
            'error_description': 'Client authentication failed'
        }), 401
    
    # Check client secret
    if client.client_secret != client_secret:
        return jsonify({
            'error': 'invalid_client',
            'error_description': 'Client authentication failed'
        }), 401
    
    # Validate grant type is allowed for this client
    if not client.check_grant_type(grant_type):
        return jsonify({
            'error': 'unauthorized_client',
            'error_description': 'Client not authorized for this grant type'
        }), 400
    
    # Find and validate authorization code
    auth_code = OAuthAuthorizationCode.query.filter_by(
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri
    ).first()
    
    if not auth_code:
        return jsonify({
            'error': 'invalid_grant',
            'error_description': 'Authorization code not found'
        }), 400
    
    if not auth_code.is_valid():
        return jsonify({
            'error': 'invalid_grant',
            'error_description': 'Authorization code expired or already used'
        }), 400
    
    # Verify PKCE if required
    if auth_code.code_challenge:
        if not code_verifier:
            return jsonify({
                'error': 'invalid_request',
                'error_description': 'Code verifier required for PKCE'
            }), 400
        
        if not auth_code.verify_code_challenge(code_verifier):
            return jsonify({
                'error': 'invalid_grant',
                'error_description': 'Code verifier verification failed'
            }), 400
    
    # Mark authorization code as used
    auth_code.use()
    
    # Generate access token
    access_token = generate_token()
    refresh_token = generate_token()
    
    # Create token record
    token_record = OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=auth_code.user_id,
        client_id=client_id,
        scope=auth_code.scope,
        expires_in=3600  # 1 hour
    )
    
    db.session.add(token_record)
    db.session.commit()
    
    # Return token response
    response = {
        'access_token': access_token,
        'token_type': 'Bearer',
        'expires_in': 3600,
        'scope': auth_code.scope
    }
    
    if refresh_token:
        response['refresh_token'] = refresh_token
    
    return jsonify(response)


@oauth_bp.route('/oauth/userinfo', methods=['GET', 'POST'])
def userinfo():
    """OAuth2 UserInfo Endpoint
    
    Returns user information for valid access tokens.
    """
    
    # Extract access token from Authorization header
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({
            'error': 'invalid_token',
            'error_description': 'Missing or invalid authorization header'
        }), 401
    
    access_token = auth_header[7:]  # Remove 'Bearer ' prefix
    
    # Find and validate token
    token_record = OAuthToken.query.filter_by(access_token=access_token).first()
    if not token_record or not token_record.is_valid():
        return jsonify({
            'error': 'invalid_token',
            'error_description': 'Access token expired or invalid'
        }), 401
    
    # Get user information
    user = User.query.get(token_record.user_id)
    if not user:
        return jsonify({
            'error': 'invalid_token',
            'error_description': 'User not found'
        }), 401
    
    # Build user info response based on granted scopes
    scopes = token_record.get_scope()
    userinfo = {'sub': str(user.id)}
    
    if 'profile' in scopes:
        userinfo.update({
            'username': user.username,
            'picture': user.avatar_url,
            'role': user.get_role_name()
        })
    
    if 'email' in scopes:
        userinfo.update({
            'email': user.email,
            'email_verified': user.email_verified
        })
    
    # Future: Add course data if 'courses' scope is requested
    if 'courses' in scopes:
        userinfo['courses'] = []  # Placeholder for course enrollment data
    
    return jsonify(userinfo)


@oauth_bp.route('/oauth/revoke', methods=['POST'])
def revoke():
    """OAuth2 Token Revocation Endpoint
    
    Revokes access or refresh tokens.
    """
    
    token = request.form.get('token')
    token_type_hint = request.form.get('token_type_hint')  # Optional hint
    client_id = request.form.get('client_id')
    client_secret = request.form.get('client_secret')
    
    if not token:
        return jsonify({
            'error': 'invalid_request',
            'error_description': 'Missing token parameter'
        }), 400
    
    # Find token (could be access token or refresh token)
    token_record = OAuthToken.query.filter(
        (OAuthToken.access_token == token) | (OAuthToken.refresh_token == token)
    ).first()
    
    if token_record:
        # Validate client if provided
        if client_id:
            if token_record.client_id != client_id:
                return jsonify({
                    'error': 'invalid_client',
                    'error_description': 'Token does not belong to this client'
                }), 400
        
        # Revoke the token
        token_record.revoke()
        db.session.commit()
    
    # RFC 7009: Return 200 even if token was not found (for security)
    return '', 200


def generate_token(length=40):
    """Generate a secure random token"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


# Client Management Endpoints (for admin use)

@oauth_bp.route('/oauth/clients', methods=['GET', 'POST'])
@jwt_required()
def manage_clients():
    """Manage OAuth clients (admin only)"""
    
    user = get_current_user()
    if not user or not user.is_admin():
        return jsonify({'error': 'Insufficient permissions'}), 403
    
    if request.method == 'GET':
        clients = OAuthClient.query.filter_by(is_active=True).all()
        return jsonify({
            'clients': [client.to_dict() for client in clients]
        })
    
    elif request.method == 'POST':
        data = request.get_json()
        
        # Generate client credentials
        client_id = generate_token(20)
        client_secret = generate_token(40)
        
        client = OAuthClient(
            client_id=client_id,
            client_secret=client_secret,
            client_name=data.get('client_name'),
            client_description=data.get('client_description'),
            client_uri=data.get('client_uri'),
            scope=data.get('scope', 'profile email'),
            response_types='code',
            grant_types='authorization_code'
        )
        
        # Set redirect URIs
        redirect_uris = data.get('redirect_uris', [])
        client.set_redirect_uris(redirect_uris)
        
        db.session.add(client)
        db.session.commit()
        
        return jsonify({
            'client_id': client_id,
            'client_secret': client_secret,
            'message': 'OAuth client created successfully'
        }), 201