#!/usr/bin/env python3
"""
Temporary Identity Management Script
==================================

This script provides temporary utilities to manage user identities and post display identities
during development. It will be replaced by proper admin interfaces later.

Usage:
    python temp_identity_manager.py --help
    python temp_identity_manager.py add-identity --user-id 1 --identity-type professor
    python temp_identity_manager.py set-post-identity --post-id 1 --identity-id 1
    python temp_identity_manager.py list-users
    python temp_identity_manager.py list-identities
    python temp_identity_manager.py list-posts

Requirements:
    - Run from the backend directory
    - Database must be accessible with current configuration
"""

import os
import sys
import argparse
from datetime import datetime, timezone

# Add the app directory to Python path
sys.path.insert(0, os.path.abspath('.'))

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.identity_type import IdentityType
from app.models.user_identity import UserIdentity
from app.models.post import Post

def init_app():
    """Initialize Flask app and database connection"""
    app = create_app()
    app.app_context().push()
    return app

def list_users():
    """List all users with their basic info"""
    users = User.query.all()
    print("\n=== Users ===")
    print(f"{'ID':<5} {'Username':<20} {'Role':<15} {'Active Identities'}")
    print("-" * 80)
    
    for user in users:
        active_identities = UserIdentity.get_user_active_identities(user.id)
        identity_names = [ai.identity_type.name for ai in active_identities if ai.identity_type]
        identity_str = ", ".join(identity_names) if identity_names else "None"
        
        print(f"{user.id:<5} {user.username:<20} {user.role_name or 'N/A':<15} {identity_str}")

def list_identity_types():
    """List all available identity types"""
    identity_types = IdentityType.get_active_types()
    print("\n=== Identity Types ===")
    print(f"{'ID':<5} {'Name':<15} {'Display Name':<20} {'Color':<10}")
    print("-" * 60)
    
    for it in identity_types:
        print(f"{it.id:<5} {it.name:<15} {it.display_name:<20} {it.color:<10}")

def list_user_identities(user_id=None):
    """List user identities, optionally filtered by user_id"""
    if user_id:
        identities = UserIdentity.query.filter_by(user_id=user_id).all()
        print(f"\n=== User {user_id} Identities ===")
    else:
        identities = UserIdentity.query.all()
        print("\n=== All User Identities ===")
    
    print(f"{'ID':<5} {'User ID':<8} {'User':<15} {'Type':<15} {'Status':<10} {'Active':<8}")
    print("-" * 80)
    
    for identity in identities:
        username = identity.user.username if identity.user else "N/A"
        type_name = identity.identity_type.name if identity.identity_type else "N/A"
        is_active = "Yes" if identity.is_active() else "No"
        
        print(f"{identity.id:<5} {identity.user_id:<8} {username:<15} {type_name:<15} {identity.status:<10} {is_active:<8}")

def list_posts():
    """List recent posts with their display identity info"""
    posts = Post.query.filter_by(is_deleted=False).order_by(Post.created_at.desc()).limit(20).all()
    print("\n=== Recent Posts (Last 20) ===")
    print(f"{'ID':<5} {'Title':<30} {'Author':<15} {'Display Identity':<20}")
    print("-" * 80)
    
    for post in posts:
        title = (post.title[:27] + "...") if len(post.title) > 30 else post.title
        author = post.user.username if post.user else "N/A"
        
        display_identity = "None"
        if post.display_identity and post.display_identity.identity_type:
            display_identity = f"{post.display_identity.identity_type.display_name}"
            if not post.display_identity.is_active():
                display_identity += " (Inactive)"
        
        print(f"{post.id:<5} {title:<30} {author:<15} {display_identity:<20}")

def add_user_identity(user_id, identity_type_name, auto_approve=True):
    """Add an identity to a user"""
    # Find user
    user = User.query.get(user_id)
    if not user:
        print(f"❌ User with ID {user_id} not found")
        return False
    
    # Find identity type
    identity_type = IdentityType.get_by_name(identity_type_name)
    if not identity_type:
        print(f"❌ Identity type '{identity_type_name}' not found")
        print("Available types:")
        list_identity_types()
        return False
    
    # Check if user already has this identity
    existing = UserIdentity.query.filter_by(
        user_id=user_id,
        identity_type_id=identity_type.id
    ).first()
    
    if existing:
        print(f"❌ User {user.username} already has {identity_type.display_name} identity (Status: {existing.status})")
        return False
    
    # Create new identity
    new_identity = UserIdentity(
        user_id=user_id,
        identity_type_id=identity_type.id,
        status=UserIdentity.PENDING if not auto_approve else UserIdentity.APPROVED
    )
    
    if auto_approve:
        new_identity.verified_at = datetime.now(timezone.utc)
        new_identity.notes = "Auto-approved by temp script"
    
    try:
        db.session.add(new_identity)
        db.session.commit()
        
        status_text = "approved" if auto_approve else "pending approval"
        print(f"✅ Added {identity_type.display_name} identity to user {user.username} ({status_text})")
        print(f"   Identity ID: {new_identity.id}")
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error adding identity: {e}")
        return False

def set_post_display_identity(post_id, identity_id):
    """Set a post's display identity"""
    # Find post
    post = Post.query.get(post_id)
    if not post:
        print(f"❌ Post with ID {post_id} not found")
        return False
    
    # Find identity (can be None to clear)
    identity = None
    if identity_id is not None:
        identity = UserIdentity.query.get(identity_id)
        if not identity:
            print(f"❌ User identity with ID {identity_id} not found")
            return False
        
        # Check if identity is active
        if not identity.is_active():
            print(f"⚠️  Warning: Identity {identity_id} is not active (status: {identity.status})")
            print("   Post will still be updated, but identity may not display properly")
    
    # Update post
    try:
        old_identity_id = post.display_identity_id
        post.display_identity_id = identity_id
        db.session.commit()
        
        if identity_id is None:
            print(f"✅ Cleared display identity for post '{post.title}'")
        else:
            identity_name = identity.identity_type.display_name if identity.identity_type else "Unknown"
            print(f"✅ Set display identity for post '{post.title}' to {identity_name}")
            print(f"   Identity ID: {identity_id}, User: {identity.user.username if identity.user else 'Unknown'}")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error updating post: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(
        description="Temporary Identity Management Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all users
  python temp_identity_manager.py list-users
  
  # Add professor identity to user ID 1
  python temp_identity_manager.py add-identity --user-id 1 --identity-type professor
  
  # Set post ID 5 to display identity ID 2
  python temp_identity_manager.py set-post-identity --post-id 5 --identity-id 2
  
  # Clear post display identity
  python temp_identity_manager.py set-post-identity --post-id 5 --identity-id none
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # List commands
    subparsers.add_parser('list-users', help='List all users')
    subparsers.add_parser('list-identity-types', help='List available identity types')
    list_identities_parser = subparsers.add_parser('list-identities', help='List user identities')
    list_identities_parser.add_argument('--user-id', type=int, help='Filter by user ID')
    subparsers.add_parser('list-posts', help='List recent posts')
    
    # Add identity command
    add_identity_parser = subparsers.add_parser('add-identity', help='Add identity to user')
    add_identity_parser.add_argument('--user-id', type=int, required=True, help='User ID')
    add_identity_parser.add_argument('--identity-type', required=True, 
                                   choices=['professor', 'staff', 'officer', 'student_leader'],
                                   help='Identity type')
    add_identity_parser.add_argument('--no-auto-approve', action='store_true', 
                                   help='Don\'t auto-approve (leave as pending)')
    
    # Set post identity command
    set_post_parser = subparsers.add_parser('set-post-identity', help='Set post display identity')
    set_post_parser.add_argument('--post-id', type=int, required=True, help='Post ID')
    set_post_parser.add_argument('--identity-id', required=True, 
                                help='Identity ID (use "none" to clear)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize app
    try:
        app = init_app()
        print("✅ Connected to database")
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        print("   Make sure you're running from the backend directory and database is accessible")
        return
    
    # Execute command
    try:
        if args.command == 'list-users':
            list_users()
        
        elif args.command == 'list-identity-types':
            list_identity_types()
        
        elif args.command == 'list-identities':
            list_user_identities(args.user_id if hasattr(args, 'user_id') else None)
        
        elif args.command == 'list-posts':
            list_posts()
        
        elif args.command == 'add-identity':
            auto_approve = not args.no_auto_approve
            add_user_identity(args.user_id, args.identity_type, auto_approve)
        
        elif args.command == 'set-post-identity':
            identity_id = None if args.identity_id.lower() == 'none' else int(args.identity_id)
            set_post_display_identity(args.post_id, identity_id)
    
    except Exception as e:
        print(f"❌ Error executing command: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()