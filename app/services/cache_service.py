"""
Cache management utilities for file URLs and other cached data
"""
from flask import current_app
from app.extensions import cache
from app.models.file import File
import re


class CacheService:
    """Service for managing Redis cache operations"""
    
    @staticmethod
    def clear_file_url_cache(file_id=None):
        """
        Clear cached file URLs
        
        Args:
            file_id (int, optional): Specific file ID to clear. If None, clears all file URL caches.
        
        Returns:
            int: Number of cache entries cleared
        """
        prefix = current_app.config.get('FILE_URL_CACHE_KEY_PREFIX', 'file_url:')
        
        if file_id:
            # Clear specific file cache
            cache_key = f"{prefix}{file_id}"
            result = cache.delete(cache_key)
            current_app.logger.info(f"Cleared cache for file {file_id}: {result}")
            return 1 if result else 0
        else:
            # Clear all file URL caches (pattern-based deletion)
            # Note: This requires Redis SCAN operation, which might be expensive
            try:
                # Get all keys matching the pattern
                redis_client = cache.cache._write_client  # Access underlying Redis client
                pattern = f"{prefix}*"
                keys = list(redis_client.scan_iter(match=pattern))
                
                if keys:
                    deleted_count = redis_client.delete(*keys)
                    current_app.logger.info(f"Cleared {deleted_count} file URL cache entries")
                    return deleted_count
                else:
                    current_app.logger.info("No file URL cache entries to clear")
                    return 0
                    
            except Exception as e:
                current_app.logger.error(f"Failed to clear file URL caches: {e}")
                return 0
    
    @staticmethod
    def warm_file_url_cache(file_ids=None, file_type=None):
        """
        Pre-generate and cache URLs for files
        
        Args:
            file_ids (list, optional): List of file IDs to warm up
            file_type (str, optional): File type to warm up (e.g., 'avatar', 'post_image')
        
        Returns:
            int: Number of URLs cached
        """
        try:
            if file_ids:
                files = File.query.filter(File.id.in_(file_ids)).all()
            elif file_type:
                files = File.query.filter_by(file_type=file_type).all()
            else:
                # Warm up most recent 100 files
                files = File.query.order_by(File.created_at.desc()).limit(100).all()
            
            cached_count = 0
            for file in files:
                try:
                    # Accessing .url property will generate and cache the URL
                    url = file.url
                    if url:
                        cached_count += 1
                        current_app.logger.debug(f"Warmed up cache for file {file.id}")
                except Exception as e:
                    current_app.logger.warning(f"Failed to warm up cache for file {file.id}: {e}")
            
            current_app.logger.info(f"Warmed up cache for {cached_count} files")
            return cached_count
            
        except Exception as e:
            current_app.logger.error(f"Failed to warm up file URL cache: {e}")
            return 0
    
    @staticmethod
    def get_cache_stats():
        """
        Get cache statistics
        
        Returns:
            dict: Cache statistics
        """
        try:
            redis_client = cache.cache._write_client
            info = redis_client.info()
            
            # Count file URL cache entries
            prefix = current_app.config.get('FILE_URL_CACHE_KEY_PREFIX', 'file_url:')
            pattern = f"{prefix}*"
            file_url_keys = list(redis_client.scan_iter(match=pattern, count=1000))
            
            return {
                'redis_memory_used': info.get('used_memory_human', 'N/A'),
                'redis_connected_clients': info.get('connected_clients', 0),
                'redis_total_commands_processed': info.get('total_commands_processed', 0),
                'file_url_cache_entries': len(file_url_keys),
                'cache_hit_ratio': info.get('keyspace_hit_ratio', 'N/A')
            }
        except Exception as e:
            current_app.logger.error(f"Failed to get cache stats: {e}")
            return {'error': str(e)}
    
    @staticmethod
    def refresh_expired_urls():
        """
        Find and refresh URLs that are close to expiring
        This can be called periodically via a background task
        
        Returns:
            int: Number of URLs refreshed
        """
        try:
            prefix = current_app.config.get('FILE_URL_CACHE_KEY_PREFIX', 'file_url:')
            redis_client = cache.cache._write_client
            pattern = f"{prefix}*"
            
            refreshed_count = 0
            
            # Get all cached file URL keys
            for key in redis_client.scan_iter(match=pattern, count=100):
                try:
                    # Get TTL for the key
                    ttl = redis_client.ttl(key)
                    
                    # If TTL is less than 5 minutes (300 seconds), refresh the URL
                    if 0 < ttl < 300:
                        # Extract file ID from cache key
                        file_id = key.decode('utf-8').replace(prefix, '')
                        if file_id.isdigit():
                            file = File.query.get(int(file_id))
                            if file:
                                # Clear the cache entry and regenerate
                                cache.delete(key.decode('utf-8'))
                                new_url = file.url  # This will regenerate and cache
                                if new_url:
                                    refreshed_count += 1
                                    current_app.logger.debug(f"Refreshed URL for file {file_id}")
                                    
                except Exception as e:
                    current_app.logger.warning(f"Failed to refresh URL for key {key}: {e}")
            
            if refreshed_count > 0:
                current_app.logger.info(f"Refreshed {refreshed_count} expiring URLs")
            
            return refreshed_count
            
        except Exception as e:
            current_app.logger.error(f"Failed to refresh expired URLs: {e}")
            return 0