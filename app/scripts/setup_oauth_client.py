# app/scripts/setup_oauth_client.py
"""
Script to register CoursePlan.search as an OAuth client
Run this after database migration to set up OAuth integration
"""

from app.extensions import db
from app.models.oauth_client import OAuthClient
import secrets
import string

def generate_client_credentials():
    """Generate secure client ID and secret"""
    def generate_token(length=40):
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    client_id = generate_token(20)
    client_secret = generate_token(40)
    return client_id, client_secret

def setup_courseplan_oauth_client():
    """Register CoursePlan.search as OAuth client"""
    
    # Check if client already exists
    existing_client = OAuthClient.query.filter_by(client_name='CoursePlan.search').first()
    if existing_client:
        print(f"OAuth client already exists!")
        print(f"Client ID: {existing_client.client_id}")
        print("Client secret is hidden for security. Check database if needed.")
        return existing_client.client_id, "[HIDDEN]"
    
    # Generate credentials
    client_id, client_secret = generate_client_credentials()
    
    # Create OAuth client record
    client = OAuthClient(
        client_id=client_id,
        client_secret=client_secret,
        client_name='CoursePlan.search',
        client_description='HKUST(GZ) course scheduling and planning tool',
        client_uri='https://scheduler.unikorn.axfff.com',
        scope='profile email courses',
        response_types='code',
        grant_types='authorization_code'
    )
    
    # Set allowed redirect URIs for CoursePlan.search
    redirect_uris = [
        'https://scheduler.unikorn.axfff.com/api/auth/callback/campus-forum',  # Production
        'http://localhost:3000/api/auth/callback/campus-forum',  # Development
        'http://127.0.0.1:3000/api/auth/callback/campus-forum'  # Alternative dev
    ]
    client.set_redirect_uris(redirect_uris)
    
    # Save to database
    db.session.add(client)
    db.session.commit()
    
    print("✅ OAuth client registered successfully!")
    print(f"Client ID: {client_id}")
    print(f"Client Secret: {client_secret}")
    print("\n🔑 Save these credentials securely!")
    print("Add to CoursePlan.search environment variables:")
    print(f"CAMPUS_FORUM_CLIENT_ID={client_id}")
    print(f"CAMPUS_FORUM_CLIENT_SECRET={client_secret}")
    
    return client_id, client_secret

if __name__ == '__main__':
    from app import create_app
    
    app = create_app()
    with app.app_context():
        setup_courseplan_oauth_client()