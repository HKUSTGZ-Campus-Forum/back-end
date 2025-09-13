#!/usr/bin/env python3
"""
Database migration script to create teammate matching tables.

This script creates the necessary tables for the teammate matching system:
- user_profiles: Extended user profiles with skills, interests, and embeddings
- projects: Project ideas and proposals
- project_applications: Applications and matches between users and projects

Usage: python create_matching_tables.py
"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app import create_app
from app.extensions import db
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.models.project_application import ProjectApplication

def create_tables():
    """Create all the new tables"""
    print("Creating teammate matching tables...")

    try:
        # Create the tables
        UserProfile.__table__.create(db.engine, checkfirst=True)
        print("✓ Created user_profiles table")

        Project.__table__.create(db.engine, checkfirst=True)
        print("✓ Created projects table")

        ProjectApplication.__table__.create(db.engine, checkfirst=True)
        print("✓ Created project_applications table")

        print("\nAll tables created successfully!")
        print("\nTable structure:")
        print("- user_profiles: Stores extended user profiles with skills, interests, and embeddings")
        print("- projects: Stores project ideas and proposals")
        print("- project_applications: Stores applications and matches between users and projects")

    except Exception as e:
        print(f"Error creating tables: {e}")
        return False

    return True

def check_existing_tables():
    """Check which tables already exist"""
    print("Checking existing tables...")

    inspector = db.inspect(db.engine)
    existing_tables = inspector.get_table_names()

    tables_to_create = ['user_profiles', 'projects', 'project_applications']

    for table in tables_to_create:
        if table in existing_tables:
            print(f"⚠ Table '{table}' already exists")
        else:
            print(f"• Table '{table}' will be created")

    return existing_tables

def verify_tables():
    """Verify that tables were created correctly"""
    print("\nVerifying table creation...")

    inspector = db.inspect(db.engine)
    tables_to_check = ['user_profiles', 'projects', 'project_applications']

    all_exist = True
    for table in tables_to_check:
        if table in inspector.get_table_names():
            columns = [col['name'] for col in inspector.get_columns(table)]
            print(f"✓ Table '{table}' exists with {len(columns)} columns")
        else:
            print(f"✗ Table '{table}' does not exist")
            all_exist = False

    return all_exist

def main():
    """Main migration function"""
    print("Teammate Matching System - Database Migration")
    print("=" * 50)

    # Create Flask app context
    app = create_app()

    with app.app_context():
        # Check current state
        existing_tables = check_existing_tables()

        print("\nProceeding with table creation...")
        input("Press Enter to continue or Ctrl+C to abort...")

        # Create tables
        success = create_tables()

        if success:
            # Verify creation
            if verify_tables():
                print("\n🎉 Migration completed successfully!")
                print("\nNext steps:")
                print("1. Update any existing users with profile data")
                print("2. Configure DashScope and DashVector API keys")
                print("3. Test the matching endpoints")
            else:
                print("\n❌ Migration partially failed - some tables may not have been created correctly")
                sys.exit(1)
        else:
            print("\n❌ Migration failed")
            sys.exit(1)

if __name__ == "__main__":
    main()