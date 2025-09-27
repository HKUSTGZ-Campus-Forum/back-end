# app/services/embedding_service.py
"""
Centralized Embedding Service
Provides a unified interface for all embedding-related operations across the application.
Designed to be extensible for future embedding use cases beyond matching.
"""
import os
import logging
import hashlib
import json
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timezone
from flask import current_app
from openai import OpenAI

logger = logging.getLogger(__name__)

class EmbeddingService:
    """
    Centralized service for embedding generation and management.

    IMPORTANT: This service is designed for extensibility beyond just matching.
    As the platform grows, new AI/ML features can easily be added by:
    1. Adding new use cases to model_configs
    2. Calling generate_embedding() with the new use_case parameter
    3. Implementing feature-specific logic that uses the embeddings

    Features:
    - Text embedding generation with intelligent caching (7-day TTL)
    - Multiple embedding model support with per-use-case configuration
    - Extensible for different use cases (matching, search, recommendations, etc.)
    - Performance monitoring and comprehensive error handling
    - Graceful degradation when external services are unavailable

    Current Use Cases:
    - "matching": User/project compatibility and matching
    - "search": Semantic search and content discovery
    - "content": Content analysis, classification, moderation

    Future Use Cases (easy to add):
    - "recommendation": Personalized content recommendations
    - "classification": Automatic content categorization
    - "similarity": Content similarity and duplicate detection
    - "analytics": User behavior analysis and insights
    """

    def __init__(self):
        self.emb_client = None
        self._initialized = False

        # Configuration - can be extended for different models/dimensions
        self.default_model = "text-embedding-v4"
        self.default_dimensions = 1024

        # Model configurations for different use cases
        self.model_configs = {
            "matching": {
                "model": "text-embedding-v4",
                "dimensions": 1024,
                "description": "For user/project matching and compatibility"
            },
            "search": {
                "model": "text-embedding-v4",
                "dimensions": 1024,
                "description": "For semantic search functionality"
            },
            "content": {
                "model": "text-embedding-v4",
                "dimensions": 1024,
                "description": "For content analysis and classification"
            }
            # Future: Add more specialized configurations as needed
            # "recommendation": {...},
            # "similarity": {...},
            # "classification": {...}
        }

    def _ensure_initialized(self):
        """Initialize embedding client if not already done"""
        if self._initialized:
            return

        try:
            # Get config from current app context
            dashscope_key = current_app.config.get('DASHSCOPE_API_KEY')

            if not dashscope_key:
                logger.warning("DASHSCOPE_API_KEY not found in config - embedding generation will fail")
                return

            # Initialize embedding client (DashScope)
            try:
                self.emb_client = OpenAI(
                    api_key=dashscope_key,
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                )
                logger.info("DashScope embedding client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize DashScope client: {e}")

            self._initialized = True

        except RuntimeError:
            # Not in app context - use environment variables as fallback
            logger.warning("No Flask app context - falling back to environment variables")
            dashscope_key = os.getenv("DASHSCOPE_API_KEY")

            if dashscope_key:
                try:
                    self.emb_client = OpenAI(
                        api_key=dashscope_key,
                        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                    )
                    logger.info("DashScope embedding client initialized (fallback)")
                except Exception as e:
                    logger.error(f"Failed to initialize DashScope client: {e}")

            self._initialized = True

    def generate_embedding(self, text: str, use_case: str = "matching", cache_enabled: bool = True) -> Optional[List[float]]:
        """
        Generate embedding for text with configurable use case

        Args:
            text: Text to embed
            use_case: Use case identifier (matching, search, content, etc.)
            cache_enabled: Whether to use caching

        Returns:
            List of floats representing the embedding, or None if failed
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding generation")
            return None

        # Get configuration for the use case
        config = self.model_configs.get(use_case, self.model_configs["matching"])

        # Generate cache key
        text_hash = hashlib.md5(f"{text}:{use_case}:{config['model']}".encode()).hexdigest()

        # Check cache first if enabled
        if cache_enabled:
            try:
                from app.extensions import cache
                cache_key = f"embed:{use_case}:{text_hash}"
                cached_embedding = cache.get(cache_key)

                if cached_embedding:
                    logger.info(f"💾 Embedding cache HIT for {use_case} text hash {text_hash}")
                    return json.loads(cached_embedding)
            except Exception as e:
                logger.debug(f"Cache lookup failed, proceeding without cache: {e}")

        logger.info(f"Generating {use_case} embedding for text {text[:50]}...")
        self._ensure_initialized()

        if not self.emb_client:
            logger.warning("No embedding client available - skipping embedding generation")
            return None

        try:
            response = self.emb_client.embeddings.create(
                model=config["model"],
                input=[text],
                dimensions=config["dimensions"],
                encoding_format="float"
            )
            embedding = response.data[0].embedding

            # Cache the result for 7 days if enabled
            if cache_enabled:
                try:
                    from app.extensions import cache
                    cache_key = f"embed:{use_case}:{text_hash}"
                    cache.set(cache_key, json.dumps(embedding), timeout=7*24*3600)
                    logger.info(f"💾 Cached {use_case} embedding for text hash {text_hash}")
                except Exception as e:
                    logger.debug(f"Cache save failed, proceeding without cache: {e}")

            logger.info(f"Generated {use_case} embedding for text {text[:50]}...")
            return embedding
        except Exception as e:
            logger.error(f"Error generating {use_case} embedding: {e}")
            return None

    def get_model_info(self, use_case: str = None) -> Dict:
        """Get information about available models and configurations"""
        if use_case:
            return self.model_configs.get(use_case, {})
        return self.model_configs

    def validate_embedding_compatibility(self, embedding1: List[float], embedding2: List[float]) -> bool:
        """Validate that two embeddings are compatible (same dimensions)"""
        return len(embedding1) == len(embedding2)

    def get_embedding_stats(self) -> Dict:
        """Get statistics about embedding usage (for monitoring)"""
        try:
            from app.extensions import cache
            # This could be expanded to track more detailed statistics
            stats = {
                "client_initialized": self._initialized,
                "available_use_cases": list(self.model_configs.keys()),
                "default_model": self.default_model,
                "default_dimensions": self.default_dimensions
            }
            return stats
        except Exception as e:
            logger.error(f"Error getting embedding stats: {e}")
            return {}

# Global instance
embedding_service = EmbeddingService()