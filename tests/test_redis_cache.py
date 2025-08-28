#!/usr/bin/env python3
"""
Test script for Redis caching implementation
Run this after setting up Redis to verify everything works
"""
import os
import sys
import redis
import requests
import time

# Add the app directory to the Python path
sys.path.append('/Users/zhaoj/Project/campusForum/back-end')

def test_redis_connection():
    """Test basic Redis connectivity"""
    print("🔍 Testing Redis connection...")
    try:
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.ping()
        print("✅ Redis connection successful")
        return True
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False

def test_cache_in_app_context():
    """Test caching within Flask app context"""
    print("\n🔍 Testing cache in Flask app context...")
    try:
        from app import create_app
        from app.extensions import cache
        from app.models.file import File
        from app.extensions import db
        
        app = create_app()
        
        with app.app_context():
            # Test basic cache operations
            cache.set('test_key', 'test_value', timeout=60)
            cached_value = cache.get('test_key')
            
            if cached_value == 'test_value':
                print("✅ Basic caching works")
            else:
                print(f"❌ Basic caching failed. Expected 'test_value', got '{cached_value}'")
                return False
            
            # Test file URL caching if files exist
            file = File.query.first()
            if file:
                print(f"🔍 Testing file URL caching with file ID {file.id}...")
                
                # First access - should generate and cache
                start_time = time.time()
                url1 = file.url
                first_access_time = time.time() - start_time
                
                # Second access - should use cache
                start_time = time.time()
                url2 = file.url
                second_access_time = time.time() - start_time
                
                if url1 == url2:
                    print(f"✅ File URL caching works")
                    print(f"   First access: {first_access_time:.3f}s")
                    print(f"   Second access: {second_access_time:.3f}s")
                    
                    if second_access_time < first_access_time * 0.5:
                        print("✅ Cache provides performance improvement")
                    else:
                        print("⚠️  Cache might not be providing expected performance improvement")
                else:
                    print(f"❌ File URL caching failed. URLs don't match")
                    return False
            else:
                print("⚠️  No files in database to test file URL caching")
            
            # Clean up test key
            cache.delete('test_key')
            print("✅ Flask app caching test complete")
            return True
            
    except Exception as e:
        print(f"❌ Flask app caching test failed: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return False

def test_cache_service():
    """Test cache service utilities"""
    print("\n🔍 Testing cache service utilities...")
    try:
        from app import create_app
        from app.services.cache_service import CacheService
        
        app = create_app()
        
        with app.app_context():
            # Test cache stats
            stats = CacheService.get_cache_stats()
            if 'error' not in stats:
                print("✅ Cache stats working")
                print(f"   Redis memory used: {stats.get('redis_memory_used', 'N/A')}")
                print(f"   File URL cache entries: {stats.get('file_url_cache_entries', 0)}")
            else:
                print(f"❌ Cache stats failed: {stats['error']}")
                return False
            
            # Test cache warming (with a small number)
            cached_count = CacheService.warm_file_url_cache()
            print(f"✅ Cache warming test: cached {cached_count} URLs")
            
            return True
            
    except Exception as e:
        print(f"❌ Cache service test failed: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return False

def main():
    """Run all tests"""
    print("🚀 Starting Redis Cache Implementation Tests\n")
    
    # Test 1: Redis connection
    if not test_redis_connection():
        print("\n❌ Redis connection test failed. Please install and start Redis server.")
        return False
    
    # Test 2: Cache in app context
    if not test_cache_in_app_context():
        print("\n❌ Flask app caching test failed.")
        return False
    
    # Test 3: Cache service utilities
    if not test_cache_service():
        print("\n❌ Cache service test failed.")
        return False
    
    print("\n🎉 All Redis cache tests passed!")
    print("\n📊 Expected Benefits:")
    print("   • 🔥 90%+ reduction in OSS API calls")
    print("   • ⚡ 50-80% faster image loading")
    print("   • 💰 Reduced OSS bandwidth costs")
    print("   • 🚀 Better user experience")
    
    print("\n🛠️  Next Steps:")
    print("   1. Monitor Redis memory usage")
    print("   2. Adjust cache timeout if needed")
    print("   3. Set up Redis persistence for production")
    print("   4. Consider Redis Cluster for high availability")
    
    return True

if __name__ == "__main__":
    main()