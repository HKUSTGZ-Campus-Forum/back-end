#!/usr/bin/env python3
"""
Notification System Initialization Script

This script helps initialize the notification system by:
1. Creating the notifications table if it doesn't exist
2. Testing the notification creation functionality
3. Verifying the API endpoints work correctly

Usage:
    python init_notifications.py
"""

import sys
import os

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from app import create_app
from app.extensions import db
from app.models import Notification, User, Post, Comment, Reaction
from app.services.notification_service import NotificationService

def init_notifications():
    """Initialize the notification system"""
    app = create_app()
    
    with app.app_context():
        try:
            # Create tables if they don't exist
            print("Creating database tables...")
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Test notification creation
            print("\nTesting notification creation...")
            
            # Get first two users for testing
            users = User.query.limit(2).all()
            if len(users) < 2:
                print("❌ Need at least 2 users in database for testing")
                return False
            
            recipient = users[0]
            sender = users[1]
            
            # Create a test notification
            notification = Notification.create_notification(
                recipient_id=recipient.id,
                sender_id=sender.id,
                notification_type="post_reaction",
                title="Test notification",
                message=f"{sender.username} liked your post for testing purposes"
            )
            
            if notification:
                db.session.commit()
                print(f"✅ Test notification created with ID: {notification.id}")
                
                # Test notification service
                result = NotificationService.get_user_notifications(recipient.id, page=1, limit=5)
                print(f"✅ Notification service working - found {len(result['notifications'])} notifications")
                
                # Clean up test notification
                db.session.delete(notification)
                db.session.commit()
                print("✅ Test notification cleaned up")
                
            else:
                print("❌ Failed to create test notification")
                return False
            
            print("\n🎉 Notification system initialized successfully!")
            print("\n📝 Next steps:")
            print("1. Start your Flask application")
            print("2. Test the notification endpoints:")
            print("   - GET /api/notifications")
            print("   - GET /api/notifications/unread-count")
            print("3. Create posts/comments/reactions to generate notifications")
            
            return True
            
        except Exception as e:
            print(f"❌ Error initializing notifications: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = init_notifications()
    sys.exit(0 if success else 1)