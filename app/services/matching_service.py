import os
import logging
from typing import List, Dict, Optional, Tuple
from flask import current_app
from openai import OpenAI
import dashvector
from app.models.user_profile import UserProfile
from app.models.project import Project
from app.extensions import db
# Cache service imports - safely handle if cache service is not available
try:
    from app.services.matching_cache_service import MatchingCacheService, cache_embedding_result, cache_compatibility_result
    CACHE_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Cache service not available: {e}")
    CACHE_AVAILABLE = False
    MatchingCacheService = None

logger = logging.getLogger(__name__)

class MatchingService:
    """Service for matching users to projects using semantic embeddings and compatibility scoring"""

    def __init__(self):
        self.emb_client = None
        self.dv_client = None
        self._initialized = False

        # Configuration
        self.embedding_model = "text-embedding-v4"
        self.embedding_dimensions = 1024
        self.profiles_collection = "user_profiles"
        self.projects_collection = "projects"

    def _ensure_initialized(self):
        """Initialize clients if not already done"""
        if self._initialized:
            return

        try:
            # Get config from current app context
            dashscope_key = current_app.config.get('DASHSCOPE_API_KEY')
            dashvector_key = current_app.config.get('DASHVECTOR_API_KEY')
            dashvector_endpoint = current_app.config.get('DASHVECTOR_ENDPOINT')

            if not dashscope_key:
                logger.warning("DASHSCOPE_API_KEY not found in config - embedding generation will fail")
            if not dashvector_key:
                logger.warning("DASHVECTOR_API_KEY not found in config - vector search will be disabled")
            if not dashvector_endpoint:
                logger.warning("DASHVECTOR_ENDPOINT not found in config - vector search will be disabled")

            # Initialize embedding client (DashScope)
            if dashscope_key:
                try:
                    self.emb_client = OpenAI(
                        api_key=dashscope_key,
                        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                    )
                    logger.info("DashScope embedding client initialized")
                except Exception as e:
                    logger.error(f"Failed to initialize DashScope client: {e}")

            # Initialize vector database client (DashVector)
            if dashvector_key and dashvector_endpoint:
                try:
                    self.dv_client = dashvector.Client(
                        api_key=dashvector_key,
                        endpoint=dashvector_endpoint,
                    )
                    logger.info("DashVector client initialized")
                except Exception as e:
                    logger.error(f"Failed to initialize DashVector client: {e}")

            # Ensure collections exist (only if we have vector client)
            if self.dv_client:
                self._ensure_collections()
            else:
                logger.warning("Skipping vector collection setup - DashVector client not available")

            self._initialized = True

        except RuntimeError:
            # Not in app context - use environment variables as fallback
            logger.warning("No Flask app context - falling back to environment variables")
            dashscope_key = os.getenv("DASHSCOPE_API_KEY")
            dashvector_key = os.getenv("DASHVECTOR_API_KEY")
            dashvector_endpoint = os.getenv("DASHVECTOR_ENDPOINT")

            if dashscope_key:
                try:
                    self.emb_client = OpenAI(
                        api_key=dashscope_key,
                        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                    )
                    logger.info("DashScope embedding client initialized (fallback)")
                except Exception as e:
                    logger.error(f"Failed to initialize DashScope client: {e}")

            if dashvector_key and dashvector_endpoint:
                try:
                    self.dv_client = dashvector.Client(
                        api_key=dashvector_key,
                        endpoint=dashvector_endpoint,
                    )
                    logger.info("DashVector client initialized (fallback)")
                except Exception as e:
                    logger.error(f"Failed to initialize DashVector client: {e}")

            self._initialized = True

    def _ensure_collections(self):
        """Ensure DashVector collections exist"""
        try:
            existing = list(self.dv_client.list() or [])

            for collection_name in [self.profiles_collection, self.projects_collection]:
                if collection_name not in existing:
                    result = self.dv_client.create(
                        name=collection_name,
                        dimension=self.embedding_dimensions,
                        metric="cosine",
                        dtype=float,
                    )
                    if not result:
                        logger.error(f"Failed to create collection: {collection_name}")
        except Exception as e:
            logger.error(f"Error ensuring collections: {e}")

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using DashScope with caching"""
        # Create a simple hash for the text to use as cache key
        import hashlib
        import json
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]

        # Check cache first (graceful fallback if cache fails)
        try:
            from app.extensions import cache
            cache_key = f"embed:text:{text_hash}"
            cached_embedding = cache.get(cache_key)
            if cached_embedding:
                logger.info(f"🎯 Embedding cache HIT for text hash {text_hash}")
                return json.loads(cached_embedding)
        except Exception as e:
            logger.debug(f"Cache lookup failed, proceeding without cache: {e}")

        logger.info(f"Generating embedding for text {text[:50]}...")
        self._ensure_initialized()

        if not self.emb_client:
            logger.warning("No embedding client available - skipping embedding generation")
            return None

        try:
            response = self.emb_client.embeddings.create(
                model=self.embedding_model,
                input=[text],
                dimensions=self.embedding_dimensions,
                encoding_format="float"
            )
            embedding = response.data[0].embedding

            # Cache the result for 7 days (graceful fallback if cache fails)
            try:
                from app.extensions import cache
                cache_key = f"embed:text:{text_hash}"
                cache.set(cache_key, json.dumps(embedding), timeout=7*24*3600)
                logger.info(f"💾 Cached embedding for text hash {text_hash}")
            except Exception as e:
                logger.debug(f"Cache save failed, proceeding without cache: {e}")

            logger.info(f"Embedding for text {text[:50]}... generated")
            return embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None

    def update_profile_embedding(self, profile_id: int) -> bool:
        """Update embedding for a user profile"""
        try:
            profile = UserProfile.query.get(profile_id)
            if not profile:
                logger.warning(f"Profile {profile_id} not found")
                return False

            # Generate text representation and embedding
            text = profile.get_text_representation()
            if not text.strip():
                logger.warning(f"Empty text for profile {profile_id}")
                return False

            embedding = self.generate_embedding(text)
            if not embedding:
                logger.warning(f"Failed to generate embedding for profile {profile_id}")
                return False

            # Update database - use a separate transaction to avoid affecting main transaction
            try:
                profile.update_embedding(embedding)
                db.session.flush()  # Flush changes but don't commit
                logger.info(f"Updated embedding for profile {profile_id}")
            except Exception as db_error:
                logger.error(f"Database error updating profile {profile_id} embedding: {db_error}")
                # Don't rollback here - just return False to indicate failure
                return False

            # Update vector database (non-critical - don't fail if this fails)
            try:
                self._upsert_to_vector_db(
                    collection_name=self.profiles_collection,
                    doc_id=f"profile_{profile_id}",
                    vector=embedding,
                    metadata={"profile_id": profile_id, "text": text}
                )
                logger.info(f"Updated vector DB for profile {profile_id}")
            except Exception as vector_error:
                logger.warning(f"Vector DB error for profile {profile_id}: {vector_error}")

            return True
        except Exception as e:
            logger.error(f"Error updating profile embedding {profile_id}: {e}")
            return False

    def update_project_embedding(self, project_id: int) -> bool:
        """Update embedding for a project"""
        try:
            project = Project.query.get(project_id)
            if not project:
                logger.warning(f"Project {project_id} not found")
                return False

            # Generate text representation and embedding
            text = project.get_text_representation()
            if not text.strip():
                logger.warning(f"Empty text for project {project_id}")
                return False

            embedding = self.generate_embedding(text)
            if not embedding:
                logger.warning(f"Failed to generate embedding for project {project_id}")
                return False

            # Update database - use flush to avoid affecting main transaction
            try:
                project.update_embedding(embedding)
                db.session.flush()  # Flush changes but don't commit
                logger.info(f"Updated embedding for project {project_id}")
            except Exception as db_error:
                logger.error(f"Database error updating project {project_id} embedding: {db_error}")
                # Don't rollback here - just return False to indicate failure
                return False

            # Update vector database (non-critical - don't fail if this fails)
            try:
                self._upsert_to_vector_db(
                    collection_name=self.projects_collection,
                    doc_id=f"project_{project_id}",
                    vector=embedding,
                    metadata={"project_id": project_id, "text": text}
                )
                logger.info(f"Updated vector DB for project {project_id}")
            except Exception as vector_error:
                logger.warning(f"Vector DB error for project {project_id}: {vector_error}")

            return True
        except Exception as e:
            logger.error(f"Error updating project embedding {project_id}: {e}")
            return False

    def _upsert_to_vector_db(self, collection_name: str, doc_id: str, vector: List[float], metadata: Dict):
        """Upsert document to vector database"""
        try:
            collection = self.dv_client.get(name=collection_name)
            if not collection:
                logger.error(f"Collection {collection_name} not found")
                return False

            result = collection.upsert([(doc_id, vector, metadata)])
            return bool(result)
        except Exception as e:
            logger.error(f"Error upserting to vector DB: {e}")
            return False

    def find_project_matches(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Find projects matching a user's profile with caching"""
        try:
            import json
            from datetime import datetime

            # Check cache first (graceful fallback if cache fails)
            try:
                from app.extensions import cache
                # Include current hour for cache key to refresh hourly
                current_hour = datetime.now().strftime('%Y%m%d%H')
                cache_key = f"matches:projects:{user_id}:{limit}:{current_hour}"
                cached_matches = cache.get(cache_key)

                if cached_matches:
                    logger.info(f"🎯 Project matches cache HIT for user {user_id}")
                    return json.loads(cached_matches)
            except Exception as e:
                logger.debug(f"Cache lookup failed, proceeding without cache: {e}")

            # Get user profile
            profile = UserProfile.query.filter_by(user_id=user_id).first()
            if not profile or not profile.embedding:
                logger.warning(f"No profile or embedding for user {user_id}")
                return []

            # Search for similar projects
            similar_projects = self._vector_search(
                collection_name=self.projects_collection,
                query_vector=profile.embedding,
                limit=limit * 2  # Get more to filter
            )

            logger.info(f"Found {len(similar_projects)} similar projects from vector search for user {user_id}")

            # Get project objects and calculate compatibility scores
            matches = []
            for result in similar_projects:
                project_id = result.get("metadata", {}).get("project_id")
                if not project_id:
                    logger.debug(f"Skipping result with no project_id: {result}")
                    continue

                project = Project.query.get(project_id)
                if not project:
                    logger.debug(f"Project {project_id} not found in database")
                    continue

                if project.is_deleted:
                    logger.debug(f"Project {project_id} is deleted")
                    continue

                if not project.is_recruiting():
                    logger.debug(f"Project {project_id} ({project.title}) is not recruiting (status: {project.status})")
                    continue

                # Skip own projects
                if project.user_id == user_id:
                    logger.debug(f"Skipping own project {project_id} ({project.title})")
                    continue

                # Calculate compatibility score
                compatibility = self._calculate_compatibility_score(profile, project)
                similarity_score = result.get("score", 0.0)

                match_data = {
                    "project": project.to_dict(include_creator=True, current_user_id=user_id),
                    "similarity_score": similarity_score,
                    "compatibility_score": compatibility.get("total_score", 0.0),
                    "match_reasons": compatibility.get("reasons", []),
                    "combined_score": (similarity_score + compatibility.get("total_score", 0.0)) / 2
                }
                matches.append(match_data)
                logger.debug(f"Added match: project {project_id} ({project.title}) with combined score {match_data['combined_score']:.3f}")

            # Sort by combined score and return top matches
            matches.sort(key=lambda x: x["combined_score"], reverse=True)
            final_matches = matches[:limit]

            logger.info(f"Returning {len(final_matches)} project matches for user {user_id}")

            # Cache the results for 1 hour (graceful fallback if cache fails)
            try:
                from app.extensions import cache
                cache_key = f"matches:projects:{user_id}:{limit}:{current_hour}"
                cache.set(cache_key, json.dumps(final_matches), timeout=3600)
                logger.info(f"💾 Cached project matches for user {user_id} ({len(final_matches)} matches)")
            except Exception as e:
                logger.debug(f"Cache save failed, proceeding without cache: {e}")

            return final_matches

        except Exception as e:
            logger.error(f"Error finding project matches for user {user_id}: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return []

    def find_teammate_matches(self, project_id: int, limit: int = 10) -> List[Dict]:
        """Find users matching a project's requirements"""
        try:
            # Get project
            project = Project.query.get(project_id)
            if not project or not project.embedding:
                logger.warning(f"No project or embedding for project {project_id}")
                return []

            # Search for similar profiles
            similar_profiles = self._vector_search(
                collection_name=self.profiles_collection,
                query_vector=project.embedding,
                limit=limit * 2  # Get more to filter
            )

            # Get profile objects and calculate compatibility scores
            matches = []
            for result in similar_profiles:
                profile_id = result.get("metadata", {}).get("profile_id")
                if not profile_id:
                    continue

                profile = UserProfile.query.get(profile_id)
                if not profile or not profile.is_active:
                    continue

                # Skip project creator
                if profile.user_id == project.user_id:
                    continue

                # Skip own projects
                if profile.user_id == project.user_id:
                    continue

                # Calculate compatibility score
                compatibility = self._calculate_compatibility_score(profile, project)
                similarity_score = result.get("score", 0.0)

                match_data = {
                    "profile": profile.to_dict(),
                    "similarity_score": similarity_score,
                    "compatibility_score": compatibility.get("total_score", 0.0),
                    "match_reasons": compatibility.get("reasons", []),
                    "combined_score": (similarity_score + compatibility.get("total_score", 0.0)) / 2
                }
                matches.append(match_data)

            # Sort by combined score and return top matches
            matches.sort(key=lambda x: x["combined_score"], reverse=True)
            return matches[:limit]

        except Exception as e:
            logger.error(f"Error finding teammate matches for project {project_id}: {e}")
            return []

    def _vector_search(self, collection_name: str, query_vector: List[float], limit: int) -> List[Dict]:
        """Perform vector similarity search"""
        try:
            self._ensure_initialized()

            if not self.dv_client:
                logger.warning("Vector client not available - returning empty results")
                return []

            collection = self.dv_client.get(name=collection_name)
            if not collection:
                logger.warning(f"Collection {collection_name} not found - returning empty results")
                return []

            result = collection.query(
                vector=query_vector,
                topk=limit,
                include_vector=False,
                output_fields=["profile_id", "project_id", "text"]
            )

            matches = []
            if result:
                for doc in result:
                    match_data = {
                        "id": doc.id,
                        "score": getattr(doc, 'score', 0.0),  # Similarity score
                        "metadata": getattr(doc, 'fields', {}) or {}
                    }
                    matches.append(match_data)

            return matches
        except Exception as e:
            logger.error(f"Error in vector search: {e}")
            return []

    def _calculate_compatibility_score(self, profile: UserProfile, project: Project) -> Dict:
        """Calculate detailed compatibility score between profile and project with caching"""
        # Check cache first
        try:
            from app.extensions import cache
            import json

            cache_key = f"compat:{profile.id}:{project.id}"
            cached_score = cache.get(cache_key)

            if cached_score:
                logger.debug(f"🎯 Compatibility cache HIT: profile:{profile.id} project:{project.id}")
                return json.loads(cached_score)
        except Exception as e:
            logger.debug(f"Compatibility cache lookup failed: {e}")

        reasons = []
        scores = {}

        # Skills matching (30% weight)
        skill_score, skill_reasons = self._calculate_skill_match(profile, project)
        scores["skills"] = skill_score
        reasons.extend(skill_reasons)

        # Experience level matching (15% weight)
        exp_score, exp_reasons = self._calculate_experience_match(profile, project)
        scores["experience"] = exp_score
        reasons.extend(exp_reasons)

        # Role preferences matching (25% weight)
        role_score, role_reasons = self._calculate_role_match(profile, project)
        scores["roles"] = role_score
        reasons.extend(role_reasons)

        # Availability/preferences matching (15% weight)
        avail_score, avail_reasons = self._calculate_availability_match(profile, project)
        scores["availability"] = avail_score
        reasons.extend(avail_reasons)

        # Interest alignment (15% weight)
        interest_score, interest_reasons = self._calculate_interest_match(profile, project)
        scores["interests"] = interest_score
        reasons.extend(interest_reasons)

        # Calculate weighted total score
        total_score = (
            scores["skills"] * 0.30 +
            scores["experience"] * 0.15 +
            scores["roles"] * 0.25 +
            scores["availability"] * 0.15 +
            scores["interests"] * 0.15
        )

        result = {
            "total_score": total_score,
            "component_scores": scores,
            "reasons": reasons[:5]  # Top 5 reasons
        }

        # Cache the result for 6 hours
        try:
            from app.extensions import cache
            import json
            cache_key = f"compat:{profile.id}:{project.id}"
            cache.set(cache_key, json.dumps(result), timeout=6*3600)
            logger.debug(f"💾 Cached compatibility: profile:{profile.id} project:{project.id} score:{total_score:.3f}")
        except Exception as e:
            logger.debug(f"Compatibility cache save failed: {e}")

        return result

    def _calculate_skill_match(self, profile: UserProfile, project: Project) -> Tuple[float, List[str]]:
        """Calculate skills matching score"""
        if not profile.skills or not project.required_skills:
            return 0.3, []

        user_skills = set(skill.lower() for skill in profile.skills)
        required_skills = set(skill.lower() for skill in project.required_skills)
        preferred_skills = set(skill.lower() for skill in (project.preferred_skills or []))

        # Calculate matches
        required_matches = user_skills.intersection(required_skills)
        preferred_matches = user_skills.intersection(preferred_skills)

        # Calculate score
        required_score = len(required_matches) / len(required_skills) if required_skills else 0
        preferred_score = len(preferred_matches) / len(preferred_skills) if preferred_skills else 0

        # Weighted combination (required skills more important)
        score = required_score * 0.8 + preferred_score * 0.2

        # Generate reasons
        reasons = []
        if required_matches:
            reasons.append(f"Has required skills: {', '.join(list(required_matches)[:3])}")
        if preferred_matches:
            reasons.append(f"Has preferred skills: {', '.join(list(preferred_matches)[:2])}")

        return score, reasons

    def _calculate_experience_match(self, profile: UserProfile, project: Project) -> Tuple[float, List[str]]:
        """Calculate experience level matching"""
        if not profile.experience_level or not project.difficulty_level:
            return 0.5, []

        # Experience levels: beginner, intermediate, advanced, expert
        # Difficulty levels: beginner, intermediate, advanced
        exp_levels = {"beginner": 0, "intermediate": 1, "advanced": 2, "expert": 3}
        diff_levels = {"beginner": 0, "intermediate": 1, "advanced": 2}

        user_exp = exp_levels.get(profile.experience_level.lower(), 1)
        proj_diff = diff_levels.get(project.difficulty_level.lower(), 1)

        # Perfect match: user experience matches project difficulty
        # Good match: user experience is one level above project difficulty
        # Poor match: significant mismatch
        diff = abs(user_exp - proj_diff)

        if diff == 0:
            score = 1.0
            reason = f"Perfect experience match for {project.difficulty_level} project"
        elif diff == 1 and user_exp > proj_diff:
            score = 0.8
            reason = f"Good experience level for {project.difficulty_level} project"
        elif diff == 1:
            score = 0.6
            reason = f"Slightly challenging but manageable project"
        else:
            score = 0.3
            reason = f"Experience level mismatch"

        return score, [reason] if score > 0.5 else []

    def _calculate_role_match(self, profile: UserProfile, project: Project) -> Tuple[float, List[str]]:
        """Calculate role preferences matching"""
        if not profile.preferred_roles or not project.looking_for_roles:
            return 0.5, []

        user_roles = set(role.lower() for role in profile.preferred_roles)
        needed_roles = set(role.lower() for role in project.looking_for_roles)

        matches = user_roles.intersection(needed_roles)
        score = len(matches) / len(needed_roles) if needed_roles else 0

        reasons = []
        if matches:
            reasons.append(f"Wants to work as: {', '.join(list(matches)[:2])}")

        return score, reasons

    def _calculate_availability_match(self, profile: UserProfile, project: Project) -> Tuple[float, List[str]]:
        """Calculate availability/preferences matching"""
        score = 0.7  # Default decent score
        reasons = []

        # This could be enhanced with more detailed availability matching
        if profile.availability:
            reasons.append(f"Available: {profile.availability}")

        return score, reasons

    def _calculate_interest_match(self, profile: UserProfile, project: Project) -> Tuple[float, List[str]]:
        """Calculate interest alignment"""
        if not profile.interests:
            return 0.5, []

        # This is a simplified version - could be enhanced with semantic similarity
        user_interests = set(interest.lower() for interest in profile.interests)
        project_text = f"{project.title} {project.description} {project.project_type}".lower()

        matches = []
        for interest in user_interests:
            if interest in project_text:
                matches.append(interest)

        score = min(1.0, len(matches) * 0.3 + 0.4)  # Base score + interest bonuses

        reasons = []
        if matches:
            reasons.append(f"Matches interests: {', '.join(matches[:2])}")

        return score, reasons

# Global instance
matching_service = MatchingService()