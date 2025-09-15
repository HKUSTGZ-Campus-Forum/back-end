"""
Cache management routes for admin/debugging purposes
"""
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.cache_service import CacheService
from app.models.user import User

cache_bp = Blueprint('cache', __name__)

@cache_bp.route('/api/admin/cache/stats', methods=['GET'])
@jwt_required()
def get_cache_stats():
    """Get cache statistics - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        
        stats = CacheService.get_cache_stats()
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to get cache stats: {e}")
        return jsonify({'error': 'Failed to get cache stats'}), 500

@cache_bp.route('/api/admin/cache/clear', methods=['POST'])
@jwt_required()
def clear_cache():
    """Clear file URL cache - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        
        data = request.get_json()
        file_id = data.get('file_id') if data else None
        
        cleared_count = CacheService.clear_file_url_cache(file_id)
        
        return jsonify({
            'success': True,
            'message': f'Cleared {cleared_count} cache entries',
            'cleared_count': cleared_count
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to clear cache: {e}")
        return jsonify({'error': 'Failed to clear cache'}), 500

@cache_bp.route('/api/admin/cache/warm', methods=['POST'])
@jwt_required()
def warm_cache():
    """Warm up file URL cache - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        
        data = request.get_json()
        file_type = data.get('file_type') if data else None
        file_ids = data.get('file_ids') if data else None
        
        cached_count = CacheService.warm_file_url_cache(file_ids, file_type)
        
        return jsonify({
            'success': True,
            'message': f'Cached {cached_count} file URLs',
            'cached_count': cached_count
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to warm cache: {e}")
        return jsonify({'error': 'Failed to warm cache'}), 500

@cache_bp.route('/api/admin/cache/refresh', methods=['POST'])
@jwt_required()
def refresh_cache():
    """Refresh expiring URLs - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        
        refreshed_count = CacheService.refresh_expired_urls()
        
        return jsonify({
            'success': True,
            'message': f'Refreshed {refreshed_count} expiring URLs',
            'refreshed_count': refreshed_count
        })
        
    except Exception as e:
        current_app.logger.error(f"Failed to refresh cache: {e}")
        return jsonify({'error': 'Failed to refresh cache'}), 500

# ===== MATCHING CACHE ENDPOINTS =====

@cache_bp.route('/api/admin/cache/matching/stats', methods=['GET'])
@jwt_required()
def get_matching_cache_stats():
    """Get matching cache performance statistics - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403

        from app.services.matching_cache_service import MatchingCacheService
        stats = MatchingCacheService.get_cache_stats()

        return jsonify({
            'success': True,
            'matching_cache_stats': stats
        })

    except Exception as e:
        current_app.logger.error(f"Failed to get matching cache stats: {e}")
        return jsonify({'error': 'Failed to get matching cache stats'}), 500

@cache_bp.route('/api/admin/cache/matching/warm/<int:user_id>', methods=['POST'])
@jwt_required()
def warm_matching_cache(user_id):
    """Proactively warm cache for a user - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403

        from app.services.matching_cache_service import MatchingCacheService
        success = MatchingCacheService.warm_cache_for_user(user_id)

        return jsonify({
            'success': success,
            'message': f'Cache warming {"completed" if success else "failed"} for user {user_id}'
        })

    except Exception as e:
        current_app.logger.error(f"Failed to warm matching cache: {e}")
        return jsonify({'error': 'Failed to warm matching cache'}), 500

@cache_bp.route('/api/admin/cache/matching/clear', methods=['POST'])
@jwt_required()
def clear_matching_cache():
    """Clear all matching caches - admin only"""
    try:
        current_user = User.query.get(get_jwt_identity())
        if not current_user or current_user.role.name != 'admin':
            return jsonify({'error': 'Admin access required'}), 403

        from app.services.matching_cache_service import MatchingCacheService
        success = MatchingCacheService.clear_all_matching_cache()

        return jsonify({
            'success': success,
            'message': 'Matching cache cleared'
        })

    except Exception as e:
        current_app.logger.error(f"Failed to clear matching cache: {e}")
        return jsonify({'error': 'Failed to clear matching cache'}), 500

@cache_bp.route('/api/cache/matching/invalidate/profile/<int:profile_id>', methods=['POST'])
@jwt_required()
def invalidate_profile_cache(profile_id):
    """Invalidate cache for a specific profile - user can invalidate their own"""
    try:
        current_user_id = get_jwt_identity()

        # Check if user owns the profile or is admin
        from app.models.user_profile import UserProfile
        profile = UserProfile.query.get(profile_id)
        if not profile:
            return jsonify({'error': 'Profile not found'}), 404

        current_user = User.query.get(current_user_id)
        if profile.user_id != current_user_id and (not current_user or current_user.role.name != 'admin'):
            return jsonify({'error': 'Permission denied'}), 403

        from app.services.matching_cache_service import MatchingCacheService
        invalidated = MatchingCacheService.invalidate_compatibility_for_profile(profile_id)

        return jsonify({
            'success': True,
            'message': f'Invalidated {invalidated} cache entries for profile {profile_id}',
            'invalidated_count': invalidated
        })

    except Exception as e:
        current_app.logger.error(f"Failed to invalidate profile cache: {e}")
        return jsonify({'error': 'Failed to invalidate profile cache'}), 500