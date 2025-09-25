#!/usr/bin/env python3
"""
Backfill embeddings for existing projects
Run this once to generate embeddings for projects created before the embedding system
"""

import sys
import os
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models.project import Project
from app.services.matching_service import matching_service
from app.extensions import db
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def backfill_project_embeddings():
    """Generate embeddings for all existing projects that don't have them"""
    app = create_app()

    with app.app_context():
        logger.info("Starting project embedding backfill...")

        # Find projects without embeddings that are not deleted and recruiting
        projects_without_embeddings = Project.query.filter(
            Project.embedding.is_(None),
            Project.is_deleted == False,
            Project.status == Project.STATUS_RECRUITING
        ).all()

        total_projects = len(projects_without_embeddings)
        logger.info(f"Found {total_projects} recruiting projects without embeddings")

        if total_projects == 0:
            logger.info("No projects need embedding generation")
            return

        success_count = 0
        error_count = 0

        for i, project in enumerate(projects_without_embeddings, 1):
            try:
                logger.info(f"Processing project {i}/{total_projects}: ID={project.id}, Title='{project.title[:50]}...'")

                # Check if project has enough content for embedding
                text_repr = project.get_text_representation()
                if not text_repr.strip():
                    logger.info(f"  Skipping project {project.id} - no text content")
                    continue

                # Generate embedding
                success = matching_service.update_project_embedding(project.id)

                if success:
                    # Commit the embedding update
                    db.session.commit()
                    success_count += 1
                    logger.info(f"  ✅ Generated embedding for project {project.id}")
                else:
                    error_count += 1
                    logger.error(f"  ❌ Failed to generate embedding for project {project.id}")

            except Exception as e:
                error_count += 1
                logger.error(f"  ❌ Error processing project {project.id}: {e}")
                db.session.rollback()

        # Also process non-recruiting projects (completed, active, etc.) that might still be searchable
        logger.info("\nProcessing non-recruiting projects...")

        other_projects = Project.query.filter(
            Project.embedding.is_(None),
            Project.is_deleted == False,
            Project.status != Project.STATUS_RECRUITING
        ).all()

        other_total = len(other_projects)
        logger.info(f"Found {other_total} non-recruiting projects without embeddings")

        for i, project in enumerate(other_projects, 1):
            try:
                logger.info(f"Processing non-recruiting project {i}/{other_total}: ID={project.id}, Status={project.status}")

                text_repr = project.get_text_representation()
                if not text_repr.strip():
                    logger.info(f"  Skipping project {project.id} - no text content")
                    continue

                success = matching_service.update_project_embedding(project.id)

                if success:
                    db.session.commit()
                    success_count += 1
                    logger.info(f"  ✅ Generated embedding for project {project.id}")
                else:
                    error_count += 1
                    logger.error(f"  ❌ Failed to generate embedding for project {project.id}")

            except Exception as e:
                error_count += 1
                logger.error(f"  ❌ Error processing project {project.id}: {e}")
                db.session.rollback()

        logger.info(f"""
Project embedding backfill completed:
- Total projects processed: {total_projects + other_total}
- Successfully generated: {success_count}
- Errors: {error_count}
- Completion time: {datetime.now()}
        """)

if __name__ == "__main__":
    backfill_project_embeddings()