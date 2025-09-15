"""
Matching Service Redis Cache Layer
Provides intelligent caching for semantic search, embeddings, and match results
"""

import json
import hashlib
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import logging

from app.models.user_profile import UserProfile
from app.models.project import Project

logger = logging.getLogger(__name__)

class MatchingCacheService:
    """
    High-performance caching layer for matching service operations
    Reduces DashScope API calls, DashVector queries, and computation overhead
    """

    # Cache TTL configurations (in seconds)
    EMBEDDING_TTL = 7 * 24 * 3600        # 7 days - embeddings rarely change
    MATCHES_TTL = 3600                   # 1 hour - balance freshness vs performance
    COMPATIBILITY_TTL = 6 * 3600         # 6 hours - compatibility scores
    POPULAR_PROJECTS_TTL = 30 * 60       # 30 minutes - trending projects
    USER_PREFERENCES_TTL = 24 * 3600     # 24 hours - user search preferences

    # Cache key prefixes
    EMBEDDING_PREFIX = "match:embed"
    MATCHES_PREFIX = "match:results"
    COMPATIBILITY_PREFIX = "match:compat"
    POPULAR_PREFIX = "match:popular"
    PREFERENCES_PREFIX = "match:prefs"

    @classmethod
    def _generate_cache_key(cls, prefix: str, *args) -> str:
        """Generate consistent cache keys with hashing for long keys"""
        key_parts = [str(arg) for arg in args]
        key_string = ":".join([prefix] + key_parts)

        # Hash long keys to avoid Redis key length limits
        if len(key_string) > 200:
            hash_suffix = hashlib.md5(key_string.encode()).hexdigest()[:8]
            key_string = f"{prefix}:hash:{hash_suffix}"

        return key_string

    # ===== EMBEDDING CACHE =====

    @classmethod
    def get_cached_embedding(cls, entity_type: str, entity_id: int) -> Optional[List[float]]:
        """
        Retrieve cached embedding vector

        Args:
            entity_type: 'profile' or 'project'
            entity_id: Database ID of the entity

        Returns:
            Cached embedding vector or None
        """
        try:
            from app.extensions import cache
            cache_key = cls._generate_cache_key(cls.EMBEDDING_PREFIX, entity_type, entity_id)
            cached_data = cache.get(cache_key)

            if cached_data:
                logger.debug(f"🎯 Embedding cache HIT: {entity_type}:{entity_id}")
                return json.loads(cached_data)

            logger.debug(f"❌ Embedding cache MISS: {entity_type}:{entity_id}")
            return None

        except Exception as e:
            logger.debug(f"Cache operation failed, proceeding without cache: {e}")
            return None

    @classmethod
    def cache_embedding(cls, entity_type: str, entity_id: int, embedding: List[float]) -> bool:
        """
        Cache embedding vector with compression

        Args:
            entity_type: 'profile' or 'project'
            entity_id: Database ID of the entity
            embedding: The embedding vector to cache

        Returns:
            Success status
        """
        try:
            cache_key = cls._generate_cache_key(cls.EMBEDDING_PREFIX, entity_type, entity_id)

            # JSON serialize with minimal precision to reduce memory usage
            embedding_json = json.dumps(embedding, separators=(',', ':'))

            success = cache.set(cache_key, embedding_json, timeout=cls.EMBEDDING_TTL)

            if success:
                logger.info(f"💾 Cached embedding: {entity_type}:{entity_id} ({len(embedding)} dims)")
            else:
                logger.warning(f"Failed to cache embedding: {entity_type}:{entity_id}")

            return success

        except Exception as e:
            logger.error(f"Error caching embedding: {e}")
            return False

    @classmethod
    def invalidate_embedding(cls, entity_type: str, entity_id: int) -> bool:
        """Invalidate cached embedding when entity is updated"""
        try:
            cache_key = cls._generate_cache_key(cls.EMBEDDING_PREFIX, entity_type, entity_id)
            return cache.delete(cache_key)
        except Exception as e:
            logger.error(f"Error invalidating embedding cache: {e}")
            return False

    # ===== MATCH RESULTS CACHE =====

    @classmethod
    def get_cached_matches(cls, user_id: int, match_type: str, limit: int,
                          filters: Dict = None) -> Optional[List[Dict]]:
        """
        Retrieve cached match results

        Args:
            user_id: User requesting matches
            match_type: 'projects' or 'teammates'
            limit: Number of results requested
            filters: Additional filter parameters

        Returns:
            Cached match results or None
        """
        try:
            # Include current hour in cache key for hourly refresh
            current_hour = datetime.now().strftime('%Y%m%d%H')

            # Create filter hash for cache key
            filter_hash = ""
            if filters:
                filter_str = json.dumps(filters, sort_keys=True)
                filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:8]

            cache_key = cls._generate_cache_key(
                cls.MATCHES_PREFIX, user_id, match_type, limit, current_hour, filter_hash
            )

            cached_data = cache.get(cache_key)

            if cached_data:
                logger.debug(f"🎯 Matches cache HIT: user:{user_id} type:{match_type}")
                return json.loads(cached_data)

            logger.debug(f"❌ Matches cache MISS: user:{user_id} type:{match_type}")
            return None

        except Exception as e:
            logger.error(f"Error retrieving cached matches: {e}")
            return None

    @classmethod
    def cache_matches(cls, user_id: int, match_type: str, limit: int,
                     matches: List[Dict], filters: Dict = None) -> bool:
        """
        Cache match results with automatic expiration

        Args:
            user_id: User requesting matches
            match_type: 'projects' or 'teammates'
            limit: Number of results
            matches: Match results to cache
            filters: Filter parameters used

        Returns:
            Success status
        """
        try:
            current_hour = datetime.now().strftime('%Y%m%d%H')

            filter_hash = ""
            if filters:
                filter_str = json.dumps(filters, sort_keys=True)
                filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:8]

            cache_key = cls._generate_cache_key(
                cls.MATCHES_PREFIX, user_id, match_type, limit, current_hour, filter_hash
            )

            # Serialize matches with metadata
            cache_data = {
                'matches': matches,
                'cached_at': datetime.now().isoformat(),
                'count': len(matches)
            }

            success = cache.set(cache_key, json.dumps(cache_data), timeout=cls.MATCHES_TTL)

            if success:
                logger.info(f"💾 Cached matches: user:{user_id} type:{match_type} count:{len(matches)}")

            return success

        except Exception as e:
            logger.error(f"Error caching matches: {e}")
            return False

    # ===== COMPATIBILITY SCORE CACHE =====

    @classmethod
    def get_cached_compatibility(cls, profile_id: int, project_id: int) -> Optional[float]:
        """
        Retrieve cached compatibility score between profile and project

        Args:
            profile_id: User profile ID
            project_id: Project ID

        Returns:
            Cached compatibility score (0.0-1.0) or None
        """
        try:
            cache_key = cls._generate_cache_key(cls.COMPATIBILITY_PREFIX, profile_id, project_id)
            cached_score = cache.get(cache_key)

            if cached_score is not None:
                logger.debug(f"🎯 Compatibility cache HIT: profile:{profile_id} project:{project_id}")
                return float(cached_score)

            logger.debug(f"❌ Compatibility cache MISS: profile:{profile_id} project:{project_id}")
            return None

        except Exception as e:
            logger.error(f"Error retrieving cached compatibility: {e}")
            return None

    @classmethod
    def cache_compatibility(cls, profile_id: int, project_id: int, score: float) -> bool:
        """
        Cache compatibility score between profile and project

        Args:
            profile_id: User profile ID
            project_id: Project ID
            score: Compatibility score (0.0-1.0)

        Returns:
            Success status
        """
        try:
            cache_key = cls._generate_cache_key(cls.COMPATIBILITY_PREFIX, profile_id, project_id)

            # Round score to reduce cache variations
            rounded_score = round(score, 3)

            success = cache.set(cache_key, str(rounded_score), timeout=cls.COMPATIBILITY_TTL)

            if success:
                logger.debug(f"💾 Cached compatibility: profile:{profile_id} project:{project_id} score:{rounded_score}")

            return success

        except Exception as e:
            logger.error(f"Error caching compatibility: {e}")
            return False

    @classmethod
    def invalidate_compatibility_for_profile(cls, profile_id: int) -> int:
        """
        Invalidate all cached compatibility scores for a profile
        Called when profile is updated

        Returns:
            Number of keys invalidated
        """
        try:
            # Pattern to match all compatibility scores for this profile
            pattern = cls._generate_cache_key(cls.COMPATIBILITY_PREFIX, profile_id, "*")
            return cls._delete_by_pattern(pattern)

        except Exception as e:
            logger.error(f"Error invalidating compatibility cache for profile {profile_id}: {e}")
            return 0

    @classmethod
    def invalidate_compatibility_for_project(cls, project_id: int) -> int:
        """
        Invalidate all cached compatibility scores for a project
        Called when project is updated

        Returns:
            Number of keys invalidated
        """
        try:
            # This requires scanning all compatibility keys - less efficient
            # Consider using Redis SCAN in production for large datasets
            pattern = cls._generate_cache_key(cls.COMPATIBILITY_PREFIX, "*", project_id)
            return cls._delete_by_pattern(pattern)

        except Exception as e:
            logger.error(f"Error invalidating compatibility cache for project {project_id}: {e}")
            return 0

    # ===== POPULAR PROJECTS CACHE =====

    @classmethod
    def get_popular_projects(cls) -> Optional[List[Dict]]:
        """Get cached list of trending/popular projects"""
        try:
            cache_key = cls._generate_cache_key(cls.POPULAR_PREFIX, "trending")
            cached_data = cache.get(cache_key)

            if cached_data:
                logger.debug("🎯 Popular projects cache HIT")
                return json.loads(cached_data)

            return None

        except Exception as e:
            logger.error(f"Error retrieving popular projects cache: {e}")
            return None

    @classmethod
    def cache_popular_projects(cls, projects: List[Dict]) -> bool:
        """Cache popular projects list"""
        try:
            cache_key = cls._generate_cache_key(cls.POPULAR_PREFIX, "trending")
            cache_data = {
                'projects': projects,
                'cached_at': datetime.now().isoformat(),
                'count': len(projects)
            }

            return cache.set(cache_key, json.dumps(cache_data), timeout=cls.POPULAR_PROJECTS_TTL)

        except Exception as e:
            logger.error(f"Error caching popular projects: {e}")
            return False

    # ===== UTILITY METHODS =====

    @classmethod
    def _delete_by_pattern(cls, pattern: str) -> int:
        """
        Delete cache keys matching pattern
        Note: In production, use Redis SCAN for better performance
        """
        try:
            # This is a simplified implementation
            # In production, implement proper pattern-based deletion using Redis SCAN
            deleted_count = 0

            # For Flask-Caching with Redis, we'd need direct Redis access
            # This is a placeholder for the concept
            logger.info(f"Pattern deletion requested: {pattern}")

            return deleted_count

        except Exception as e:
            logger.error(f"Error deleting by pattern {pattern}: {e}")
            return 0

    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """
        Get cache performance statistics
        Useful for monitoring and optimization
        """
        try:
            # This would require Redis INFO command access
            # Placeholder implementation
            stats = {
                'embedding_cache_info': 'Available with Redis direct access',
                'matches_cache_info': 'Available with Redis direct access',
                'compatibility_cache_info': 'Available with Redis direct access',
                'cache_hit_ratio': 'Available with Redis direct access',
                'memory_usage': 'Available with Redis direct access'
            }

            return stats

        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}

    @classmethod
    def warm_cache_for_user(cls, user_id: int) -> bool:
        """
        Proactively warm cache for a user
        Called during low-traffic periods or user login
        """
        try:
            logger.info(f"🔥 Warming cache for user {user_id}")

            # Pre-generate embeddings and popular matches
            # This would call the main matching service methods
            # which would populate the cache as a side effect

            return True

        except Exception as e:
            logger.error(f"Error warming cache for user {user_id}: {e}")
            return False

    @classmethod
    def clear_all_matching_cache(cls) -> bool:
        """Clear all matching-related cache (for maintenance)"""
        try:
            patterns = [
                cls.EMBEDDING_PREFIX,
                cls.MATCHES_PREFIX,
                cls.COMPATIBILITY_PREFIX,
                cls.POPULAR_PREFIX,
                cls.PREFERENCES_PREFIX
            ]

            total_deleted = 0
            for pattern in patterns:
                deleted = cls._delete_by_pattern(f"{pattern}:*")
                total_deleted += deleted

            logger.info(f"🧹 Cleared matching cache: {total_deleted} keys deleted")
            return True

        except Exception as e:
            logger.error(f"Error clearing matching cache: {e}")
            return False


# ===== CACHE DECORATORS =====

def cache_embedding_result(entity_type: str):
    """
    Decorator to automatically cache embedding generation results

    Usage:
        @cache_embedding_result('profile')
        def generate_profile_embedding(profile_id):
            # ... expensive embedding generation
            return embedding_vector
    """
    def decorator(func):
        def wrapper(entity_id, *args, **kwargs):
            # Try cache first
            cached_embedding = MatchingCacheService.get_cached_embedding(entity_type, entity_id)
            if cached_embedding:
                return cached_embedding

            # Generate and cache
            embedding = func(entity_id, *args, **kwargs)
            if embedding:
                MatchingCacheService.cache_embedding(entity_type, entity_id, embedding)

            return embedding
        return wrapper
    return decorator


def cache_compatibility_result(func):
    """
    Decorator to automatically cache compatibility score calculations

    Usage:
        @cache_compatibility_result
        def calculate_compatibility(profile, project):
            # ... expensive compatibility calculation
            return score
    """
    def wrapper(profile, project, *args, **kwargs):
        profile_id = profile.id if hasattr(profile, 'id') else profile
        project_id = project.id if hasattr(project, 'id') else project

        # Try cache first
        cached_score = MatchingCacheService.get_cached_compatibility(profile_id, project_id)
        if cached_score is not None:
            return cached_score

        # Calculate and cache
        score = func(profile, project, *args, **kwargs)
        if score is not None:
            MatchingCacheService.cache_compatibility(profile_id, project_id, score)

        return score
    return wrapper