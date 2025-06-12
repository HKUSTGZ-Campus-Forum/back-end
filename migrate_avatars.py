#!/usr/bin/env python3
"""
Avatar migration script - run this after deploying the new code

This will:
1. Add the new profile_picture_file_id column to users table
2. The avatar_url property will automatically work for new uploads

Usage: python migrate_avatars.py
"""

from app import create_app
from app.extensions import db

def migrate_avatar_system():
    app = create_app()
    
    with app.app_context():
        try:
            # Add the new column to users table
            db.engine.execute("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS profile_picture_file_id INTEGER 
                REFERENCES files(id) ON DELETE SET NULL
            """)
            
            print("✅ Added profile_picture_file_id column to users table")
            
            # Create index for performance
            db.engine.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_profile_picture_file_id 
                ON users(profile_picture_file_id)
            """)
            
            print("✅ Created index for profile_picture_file_id")
            
            print("🎉 Avatar system migration completed successfully!")
            print("")
            print("📝 Notes:")
            print("- Existing users will show no avatar until they upload a new one")
            print("- New avatar uploads will use the File-based system and never expire")
            print("- The avatar_url property automatically generates fresh signed URLs")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            return False
            
    return True

if __name__ == "__main__":
    migrate_avatar_system()