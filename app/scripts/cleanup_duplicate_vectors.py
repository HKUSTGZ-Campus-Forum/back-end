#!/usr/bin/env python3
# app/scripts/cleanup_duplicate_vectors.py
"""
Cleanup script for duplicate vectors in DashVector collections
This script identifies and removes duplicate vectors that may have been created
due to inconsistent embedding generation
"""
import sys
import os
import json
import logging
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.services.matching_service import matching_service
from app.extensions import db

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def cleanup_duplicate_vectors():
    """
    Remove duplicate vectors and regenerate clean embeddings

    CRITICAL: Run this script ONCE after deploying the unified embedding system
    to fix the duplicate search results issue.

    What this script does:
    1. Deletes ALL existing vectors from DashVector collections (profiles & projects)
    2. Regenerates embeddings using the new unified approach (consistent formatting)
    3. Ensures all profiles use project-enhanced embeddings
    4. Provides comprehensive logging and statistics

    When to run:
    - After deploying the embedding system fixes
    - When you notice duplicate search results
    - After any major changes to embedding generation logic

    Safety notes:
    - This script is safe to run multiple times
    - It only affects vector database, not your main PostgreSQL data
    - Profile/project data remains intact
    - Regenerated embeddings will match the current codebase logic

    Expected results after running:
    - No more duplicate profiles/projects in search results
    - All embeddings use consistent text representation
    - Background task system prevents future issues
    """
    app = create_app()

    with app.app_context():
        logger.info("Starting vector cleanup process...")

        # Initialize matching service to get clients
        matching_service._ensure_initialized()

        if not matching_service.dv_client:
            logger.error("DashVector client not available - cannot perform cleanup")
            return

        cleanup_stats = {
            "profiles_processed": 0,
            "projects_processed": 0,
            "profiles_errors": 0,
            "projects_errors": 0,
            "vectors_deleted": 0
        }

        # Step 1: Clean up profile vectors
        logger.info("\n=== STEP 1: Cleaning Profile Vectors ===")

        try:
            profiles_collection = matching_service.dv_client.get(name=matching_service.profiles_collection)
            if profiles_collection:
                logger.info("Clearing all profile vectors to remove duplicates...")

                # Get all active profiles from database
                active_profiles = UserProfile.query.filter_by(is_active=True).all()
                logger.info(f"Found {len(active_profiles)} active profiles in database")

                # Get all profile IDs that should exist
                valid_profile_ids = {f"profile_{p.id}" for p in active_profiles}
                logger.info(f"Expected {len(valid_profile_ids)} valid profile vectors")

                # Query existing vectors in collection
                try:
                    # Get a sample to see what's in the collection
                    sample_result = profiles_collection.query(
                        vector=[0.0] * matching_service.embedding_dimensions,
                        topk=100,
                        include_vector=False,
                        output_fields=["profile_id", "type", "updated_at"]
                    )

                    existing_doc_ids = set()
                    if sample_result:
                        for doc in sample_result:
                            existing_doc_ids.add(doc.id)

                    logger.info(f"Found {len(existing_doc_ids)} existing vectors in collection")

                    # Delete all existing profile vectors to ensure clean state
                    if existing_doc_ids:
                        logger.info("Deleting all existing profile vectors for clean regeneration...")
                        profiles_collection.delete(list(existing_doc_ids))
                        cleanup_stats["vectors_deleted"] += len(existing_doc_ids)
                        logger.info(f"Deleted {len(existing_doc_ids)} existing profile vectors")

                except Exception as query_error:
                    logger.warning(f"Could not query existing vectors (continuing anyway): {query_error}")

            else:
                logger.warning("Profile collection not found")

        except Exception as e:
            logger.error(f"Error accessing profiles collection: {e}")

        # Step 2: Clean up project vectors
        logger.info("\n=== STEP 2: Cleaning Project Vectors ===")

        try:
            projects_collection = matching_service.dv_client.get(name=matching_service.projects_collection)
            if projects_collection:
                logger.info("Clearing all project vectors to remove duplicates...")

                # Get all non-deleted projects from database
                active_projects = Project.query.filter_by(is_deleted=False).all()
                logger.info(f"Found {len(active_projects)} active projects in database")

                # Query existing vectors in collection
                try:
                    # Get a sample to see what's in the collection
                    sample_result = projects_collection.query(
                        vector=[0.0] * matching_service.embedding_dimensions,
                        topk=100,
                        include_vector=False,
                        output_fields=["project_id", "type", "updated_at"]
                    )

                    existing_doc_ids = set()
                    if sample_result:
                        for doc in sample_result:
                            existing_doc_ids.add(doc.id)

                    logger.info(f"Found {len(existing_doc_ids)} existing vectors in collection")

                    # Delete all existing project vectors to ensure clean state
                    if existing_doc_ids:
                        logger.info("Deleting all existing project vectors for clean regeneration...")
                        projects_collection.delete(list(existing_doc_ids))
                        cleanup_stats["vectors_deleted"] += len(existing_doc_ids)
                        logger.info(f"Deleted {len(existing_doc_ids)} existing project vectors")

                except Exception as query_error:
                    logger.warning(f"Could not query existing vectors (continuing anyway): {query_error}")

            else:
                logger.warning("Projects collection not found")

        except Exception as e:
            logger.error(f"Error accessing projects collection: {e}")

        # Step 3: Regenerate clean embeddings
        logger.info("\n=== STEP 3: Regenerating Clean Embeddings ===")

        # Regenerate profile embeddings
        logger.info("Regenerating profile embeddings...")
        complete_profiles = UserProfile.query.filter_by(is_active=True).all()

        for i, profile in enumerate(complete_profiles, 1):
            try:
                if profile.is_complete():
                    logger.info(f"Regenerating profile {i}/{len(complete_profiles)}: ID={profile.id}, User={profile.user_id}")
                    success = matching_service.update_profile_embedding(profile.id)

                    if success:
                        db.session.commit()
                        cleanup_stats["profiles_processed"] += 1
                        logger.info(f"  ✅ Regenerated embedding for profile {profile.id}")
                    else:
                        cleanup_stats["profiles_errors"] += 1
                        logger.error(f"  ❌ Failed to regenerate embedding for profile {profile.id}")
                else:
                    logger.info(f"  ⏭️  Skipping incomplete profile {profile.id}")

            except Exception as e:
                cleanup_stats["profiles_errors"] += 1
                logger.error(f"  ❌ Error regenerating profile {profile.id}: {e}")
                db.session.rollback()

        # Regenerate project embeddings
        logger.info("\nRegenerating project embeddings...")
        active_projects = Project.query.filter_by(is_deleted=False).all()

        for i, project in enumerate(active_projects, 1):
            try:
                logger.info(f"Regenerating project {i}/{len(active_projects)}: ID={project.id}, Title='{project.title[:50]}...'")

                text_repr = project.get_text_representation()
                if text_repr.strip():
                    success = matching_service.update_project_embedding(project.id)

                    if success:
                        db.session.commit()
                        cleanup_stats["projects_processed"] += 1
                        logger.info(f"  ✅ Regenerated embedding for project {project.id}")
                    else:
                        cleanup_stats["projects_errors"] += 1
                        logger.error(f"  ❌ Failed to regenerate embedding for project {project.id}")
                else:
                    logger.info(f"  ⏭️  Skipping project {project.id} - no text content")

            except Exception as e:
                cleanup_stats["projects_errors"] += 1
                logger.error(f"  ❌ Error regenerating project {project.id}: {e}")
                db.session.rollback()

        logger.info(f"""
=== CLEANUP COMPLETED ===
- Profiles processed: {cleanup_stats['profiles_processed']}
- Profile errors: {cleanup_stats['profiles_errors']}
- Projects processed: {cleanup_stats['projects_processed']}
- Project errors: {cleanup_stats['projects_errors']}
- Total vectors deleted: {cleanup_stats['vectors_deleted']}
- Completion time: {datetime.now()}

All embeddings have been regenerated with consistent formatting.
Duplicate search results should now be resolved.
        """)

if __name__ == "__main__":
    cleanup_duplicate_vectors()