#!/usr/bin/env python3
"""
Backfill embeddings for existing user profiles
Run this once to generate embeddings for profiles created before the embedding system
"""

import sys
import os
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models.user_profile import UserProfile
from app.services.matching_service import matching_service
from app.extensions import db
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def backfill_profile_embeddings():
    """Generate embeddings for all existing profiles that don't have them"""
    app = create_app()

    with app.app_context():
        logger.info("Starting profile embedding backfill...")

        # Find profiles without embeddings
        profiles_without_embeddings = UserProfile.query.filter(
            UserProfile.embedding.is_(None),
            UserProfile.is_active == True
        ).all()

        total_profiles = len(profiles_without_embeddings)
        logger.info(f"Found {total_profiles} profiles without embeddings")

        if total_profiles == 0:
            logger.info("No profiles need embedding generation")
            return

        success_count = 0
        error_count = 0

        for i, profile in enumerate(profiles_without_embeddings, 1):
            try:
                logger.info(f"Processing profile {i}/{total_profiles}: ID={profile.id}, User={profile.user_id}")

                # Check if profile has enough content for embedding
                if not profile.is_complete():
                    logger.info(f"  Skipping incomplete profile {profile.id}")
                    continue

                # Generate embedding (always includes projects for consistency)
                success = matching_service.update_profile_embedding(profile.id)

                if success:
                    # Commit the embedding update
                    db.session.commit()
                    success_count += 1
                    logger.info(f"  ✅ Generated embedding for profile {profile.id}")
                else:
                    error_count += 1
                    logger.error(f"  ❌ Failed to generate embedding for profile {profile.id}")

            except Exception as e:
                error_count += 1
                logger.error(f"  ❌ Error processing profile {profile.id}: {e}")
                db.session.rollback()

        logger.info(f"""
Embedding backfill completed:
- Total profiles processed: {total_profiles}
- Successfully generated: {success_count}
- Errors: {error_count}
- Completion time: {datetime.now()}
        """)

if __name__ == "__main__":
    backfill_profile_embeddings()