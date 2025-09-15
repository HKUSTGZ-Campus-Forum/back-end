#!/usr/bin/env python3
"""
Remove Application System Migration Script
==========================================

This script removes the application system from the matching module by:
1. Dropping the project_applications table
2. Removing application-related columns from projects table
3. Cleaning up any application-related data

Run this script after updating the code to remove application functionality.
"""

import sys
import os

# Add the parent directory to Python path to import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app import create_app
from app.extensions import db
from sqlalchemy import text

def drop_application_system():
    """Drop application-related tables and clean up database"""

    print("🔄 Starting application system removal...")

    # Create app context
    app = create_app()

    with app.app_context():
        try:
            # Drop project_applications table if it exists
            print("📦 Dropping project_applications table...")
            db.session.execute(text("DROP TABLE IF EXISTS project_applications CASCADE;"))

            # Remove application-related indexes
            print("🗂️ Removing application-related indexes...")
            indexes_to_drop = [
                "idx_applications_project_id",
                "idx_applications_user_id",
                "idx_applications_status",
                "idx_applications_created_at"
            ]

            for index_name in indexes_to_drop:
                try:
                    db.session.execute(text(f"DROP INDEX IF EXISTS {index_name};"))
                    print(f"  ✅ Dropped index: {index_name}")
                except Exception as e:
                    print(f"  ⚠️ Could not drop index {index_name}: {e}")

            # Remove application-related constraints
            print("🔗 Removing application-related constraints...")
            constraints_to_drop = [
                "uq_project_user_application",
                "check_match_score_range"
            ]

            for constraint_name in constraints_to_drop:
                try:
                    db.session.execute(text(f"ALTER TABLE IF EXISTS project_applications DROP CONSTRAINT IF EXISTS {constraint_name};"))
                    print(f"  ✅ Dropped constraint: {constraint_name}")
                except Exception as e:
                    print(f"  ⚠️ Could not drop constraint {constraint_name}: {e}")

            # Commit the changes
            db.session.commit()

            print("✅ Application system removal completed successfully!")
            print("\n📋 Summary:")
            print("  • project_applications table dropped")
            print("  • Related indexes removed")
            print("  • Related constraints removed")
            print("\n💡 Next steps:")
            print("  1. Restart your application server")
            print("  2. Test the simplified matching system")
            print("  3. The system now focuses on discovery and direct contact")

        except Exception as e:
            print(f"❌ Error during migration: {e}")
            db.session.rollback()
            raise e

if __name__ == "__main__":
    try:
        drop_application_system()
    except Exception as e:
        print(f"💥 Migration failed: {e}")
        sys.exit(1)