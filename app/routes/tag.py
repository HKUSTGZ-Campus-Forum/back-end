from flask import Blueprint, request, jsonify
from app.models.tag import Tag, TagType, post_tags
from app.models.post import Post
from app.extensions import db#, limiter
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc, asc
from datetime import datetime, timezone, timedelta
import bleach

bp = Blueprint('tag', __name__, url_prefix='/tags')

@bp.route('', methods=['GET'])
# @limiter.limit("60 per minute")
def get_tags():
    """List all tags with optional filtering and sorting"""
    # Get filter parameters
    tag_type = request.args.get('type')
    search_query = request.args.get('q', '')
    sort_by = request.args.get('sort_by', 'name')
    sort_order = request.args.get('sort_order', 'asc')
    
    # Start building the query
    query = Tag.query
    
    # Apply filters
    if tag_type:
        if tag_type not in [TagType.SYSTEM, TagType.USER]:
            return jsonify({"error": f"Invalid tag type: {tag_type}"}), 400
        query = query.filter(Tag.tag_type == tag_type)
    
    # Apply search if provided
    if search_query:
        safe_query = "%" + search_query.replace('%', r'\%').replace('_', r'\_') + "%"
        query = query.filter(Tag.name.ilike(safe_query, escape='\\'))
    
    # Apply sorting
    valid_sort_fields = {
        'name': Tag.name,
        'created_at': Tag.created_at,
    }
    
    if sort_by == 'usage':
        # For usage sorting, we need to count posts per tag
        # This is more complex and requires a subquery
        from sqlalchemy import select, func
        from app.models.tag import post_tags
        
        # Count posts per tag
        subq = select([
            post_tags.c.tag_id,
            func.count(post_tags.c.post_id).label('post_count')
        ]).group_by(post_tags.c.tag_id).alias('tag_usage')
        
        # Join with our main query
        query = query.outerjoin(subq, Tag.id == subq.c.tag_id)
        
        # Order by post count
        if sort_order == 'desc':
            query = query.order_by(desc(func.coalesce(subq.c.post_count, 0)))
        else:
            query = query.order_by(asc(func.coalesce(subq.c.post_count, 0)))
    elif sort_by in valid_sort_fields:
        sort_field = valid_sort_fields[sort_by]
        if sort_order == 'desc':
            query = query.order_by(desc(sort_field))
        else:
            query = query.order_by(asc(sort_field))
    else:
        return jsonify({"error": f"Invalid sort_by field: {sort_by}"}), 400
    
    # Execute query and format response
    tags = query.all()
    
    # Format the response
    result = []
    for tag in tags:
        tag_dict = {
            "tag_id": tag.id,
            "name": tag.name,
            "tag_type": tag.tag_type,
            "description": tag.description,
            "isImportant": tag.tag_type == TagType.SYSTEM,
            "tagcolor": "#3498db",  # Default color, you might want to add this to your model
            "created_at": tag.created_at.isoformat()
        }
        result.append(tag_dict)
    
    return jsonify(result), 200

@bp.route('', methods=['POST'])
@jwt_required()
# @limiter.limit("20 per minute")
def create_tag():
    """Create a new tag"""
    try:
        data = request.get_json() or {}
        
        # Validate required fields
        if not data.get('name'):
            return jsonify({"error": "Tag name is required"}), 400
        
        # Sanitize inputs
        name = bleach.clean(data['name']).strip()
        description = bleach.clean(data.get('description', ''))
        tag_type = data.get('tag_type', TagType.USER)
        
        # Validate tag type
        if tag_type not in [TagType.SYSTEM, TagType.USER]:
            return jsonify({"error": f"Invalid tag type: {tag_type}"}), 400
        
        # Check if tag already exists
        if Tag.query.filter(func.lower(Tag.name) == func.lower(name)).first():
            return jsonify({"error": "Tag with this name already exists"}), 400
        
        # Create new tag
        tag = Tag(
            name=name,
            tag_type=tag_type,
            description=description
        )
        
        db.session.add(tag)
        db.session.commit()
        
        # Format response
        response = {
            "tag_id": tag.id,
            "name": tag.name,
            "tag_type": tag.tag_type,
            "description": tag.description,
            "isImportant": tag.tag_type == TagType.SYSTEM,
            "tagcolor": "#3498db",  # Default color
            "created_at": tag.created_at.isoformat()
        }
        
        return jsonify(response), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:tag_id>', methods=['GET'])
# @limiter.limit("60 per minute")
def get_tag(tag_id):
    """Get a specific tag by ID"""
    tag = Tag.query.get_or_404(tag_id)
    
    # Count posts using this tag
    post_count = db.session.query(func.count(Post.id)).join(
        post_tags, Post.id == post_tags.c.post_id
    ).filter(post_tags.c.tag_id == tag_id).scalar()
    
    # Format response
    response = {
        "tag_id": tag.id,
        "name": tag.name,
        "tag_type": tag.tag_type,
        "description": tag.description,
        "isImportant": tag.tag_type == TagType.SYSTEM,
        "tagcolor": "#3498db",  # Default color
        "created_at": tag.created_at.isoformat(),
        "post_count": post_count
    }
    
    return jsonify(response), 200

@bp.route('/<int:tag_id>', methods=['PUT'])
@jwt_required()
# @limiter.limit("20 per minute")
def update_tag(tag_id):
    """Update an existing tag"""
    try:
        tag = Tag.query.get_or_404(tag_id)
        data = request.get_json() or {}
        
        # Update fields if provided
        if 'name' in data:
            name = bleach.clean(data['name']).strip()
            # Check if new name already exists (excluding current tag)
            existing_tag = Tag.query.filter(
                func.lower(Tag.name) == func.lower(name),
                Tag.id != tag_id
            ).first()
            if existing_tag:
                return jsonify({"error": "Tag with this name already exists"}), 400
            tag.name = name
            
        if 'description' in data:
            tag.description = bleach.clean(data['description'])
            
        if 'tag_type' in data:
            tag_type = data['tag_type']
            if tag_type not in [TagType.SYSTEM, TagType.USER]:
                return jsonify({"error": f"Invalid tag type: {tag_type}"}), 400
            tag.tag_type = tag_type
        
        db.session.commit()
        
        # Format response
        response = {
            "tag_id": tag.id,
            "name": tag.name,
            "tag_type": tag.tag_type,
            "description": tag.description,
            "isImportant": tag.tag_type == TagType.SYSTEM,
            "tagcolor": "#3498db",  # Default color
            "created_at": tag.created_at.isoformat()
        }
        
        return jsonify(response), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/<int:tag_id>', methods=['DELETE'])
@jwt_required()
# @limiter.limit("10 per minute")
def delete_tag(tag_id):
    """Delete a tag"""
    try:
        tag = Tag.query.get_or_404(tag_id)
        
        # Check if it's a system tag (optional protection)
        if tag.tag_type == TagType.SYSTEM:
            return jsonify({"error": "System tags cannot be deleted"}), 403
        
        # Delete the tag
        db.session.delete(tag)
        db.session.commit()
        
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/popular', methods=['GET'])
# @limiter.limit("60 per minute")
def get_popular_tags():
    """Get most popular tags based on usage"""
    limit = request.args.get('limit', 10, type=int)
    
    # Query to get tags with their post counts
    popular_tags = db.session.query(
        Tag,
        func.count(post_tags.c.post_id).label('post_count')
    ).join(
        post_tags, Tag.id == post_tags.c.tag_id
    ).group_by(Tag.id).order_by(desc('post_count')).limit(limit).all()
    
    # Format response
    result = []
    for tag, post_count in popular_tags:
        tag_dict = {
            "tag": {
                "tag_id": tag.id,
                "name": tag.name,
                "tag_type": tag.tag_type,
                "description": tag.description,
                "isImportant": tag.tag_type == TagType.SYSTEM,
                "tagcolor": "#3498db",  # Default color
                "created_at": tag.created_at.isoformat()
            },
            "post_count": post_count
        }
        result.append(tag_dict)
    
    return jsonify(result), 200

@bp.route('/<int:tag_id>/posts', methods=['GET'])
# @limiter.limit("60 per minute")
def get_tag_posts(tag_id):
    """Get posts with a specific tag"""
    # Check if tag exists
    tag = Tag.query.get_or_404(tag_id)
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    
    # Query posts with this tag
    posts = Post.query.join(
        post_tags, Post.id == post_tags.c.post_id
    ).filter(
        post_tags.c.tag_id == tag_id
    ).paginate(page=page, per_page=limit, error_out=False)
    
    # Format response
    response = {
        "posts": [post.to_dict() for post in posts.items],
        "total_count": posts.total,
        "total_pages": posts.pages,
        "current_page": page,
        "tag": {
            "tag_id": tag.id,
            "name": tag.name,
            "tag_type": tag.tag_type,
            "description": tag.description
        }
    }
    
    return jsonify(response), 200

@bp.route('/post/<int:post_id>', methods=['GET'])
# @limiter.limit("60 per minute")
def get_post_tags(post_id):
    """Get all tags for a specific post"""
    # Check if post exists
    post = Post.query.get_or_404(post_id)
    
    # Get tags for the post
    tags = Tag.query.join(
        post_tags, Tag.id == post_tags.c.tag_id
    ).filter(
        post_tags.c.post_id == post_id
    ).all()
    
    # Format response
    result = []
    for tag in tags:
        tag_dict = {
            "tag_id": tag.id,
            "name": tag.name,
            "tag_type": tag.tag_type,
            "description": tag.description,
            "isImportant": tag.tag_type == TagType.SYSTEM,
            "tagcolor": "#3498db",  # Default color
            "created_at": tag.created_at.isoformat()
        }
        result.append(tag_dict)
    
    return jsonify(result), 200

@bp.route('/trending', methods=['GET'])
# @limiter.limit("60 per minute")
def get_trending_tags():
    """Get trending tags based on usage in a specific time period"""
    # Get parameters
    period = request.args.get('period', 'week')
    limit = request.args.get('limit', 10, type=int)
    
    # Determine time range based on period
    now = datetime.now(timezone.utc)
    if period == 'day':
        start_date = now - timedelta(days=1)
    elif period == 'week':
        start_date = now - timedelta(weeks=1)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    else:  # all_time
        start_date = datetime(1970, 1, 1, tzinfo=timezone.utc)
    
    # Query to get trending tags
    # This query counts tag usage in posts created within the specified time period
    trending_tags = db.session.query(
        Tag,
        func.count(post_tags.c.post_id).label('post_count')
    ).join(
        post_tags, Tag.id == post_tags.c.tag_id
    ).join(
        Post, Post.id == post_tags.c.post_id
    ).filter(
        Post.created_at >= start_date,
        Post.is_deleted == False
    ).group_by(Tag.id).order_by(desc('post_count')).limit(limit).all()
    
    # Format response
    result = []
    for tag, post_count in trending_tags:
        tag_dict = {
            "tag": {
                "tag_id": tag.id,
                "name": tag.name,
                "tag_type": tag.tag_type,
                "description": tag.description,
                "isImportant": tag.tag_type == TagType.SYSTEM,
                "tagcolor": "#3498db",  # Default color
                "created_at": tag.created_at.isoformat()
            },
            "post_count": post_count
        }
        result.append(tag_dict)
    
    return jsonify(result), 200

# Add endpoint for managing tags on a post
@bp.route('/post/<int:post_id>/tags', methods=['POST'])
@jwt_required()
# @limiter.limit("20 per minute")
def add_tags_to_post(post_id):
    """Add tags to a post"""
    try:
        # Check if post exists
        post = Post.query.get_or_404(post_id)
        
        # Check if user has permission to modify this post
        current_user_id = get_jwt_identity()
        if post.user_id != current_user_id:
            return jsonify({"error": "You don't have permission to modify this post's tags"}), 403
        
        data = request.get_json() or {}
        
        # Validate input
        if not data.get('tag_ids') or not isinstance(data.get('tag_ids'), list):
            return jsonify({"error": "tag_ids must be a non-empty list"}), 400
        
        # Get tags
        tags = Tag.query.filter(Tag.id.in_(data['tag_ids'])).all()
        found_tag_ids = [tag.id for tag in tags]
        
        # Check if all requested tags exist
        missing_tag_ids = [tag_id for tag_id in data['tag_ids'] if tag_id not in found_tag_ids]
        if missing_tag_ids:
            return jsonify({"error": f"Tags with IDs {missing_tag_ids} not found"}), 404
        
        # Add tags to post
        for tag in tags:
            if tag not in post.tags:
                post.tags.append(tag)
        
        db.session.commit()
        
        # Get updated tags for the post
        updated_tags = [
            {
                "tag_id": tag.id,
                "name": tag.name,
                "tag_type": tag.tag_type,
                "description": tag.description,
                "isImportant": tag.tag_type == TagType.SYSTEM,
                "tagcolor": "#3498db"  # Default color
            } for tag in post.tags
        ]
        
        return jsonify(updated_tags), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@bp.route('/post/<int:post_id>/tags/<int:tag_id>', methods=['DELETE'])
@jwt_required()
# @limiter.limit("20 per minute")
def remove_tag_from_post(post_id, tag_id):
    """Remove a tag from a post"""
    try:
        # Check if post exists
        post = Post.query.get_or_404(post_id)
        
        # Check if user has permission to modify this post
        current_user_id = get_jwt_identity()
        if post.user_id != current_user_id:
            return jsonify({"error": "You don't have permission to modify this post's tags"}), 403
        
        # Check if tag exists
        tag = Tag.query.get_or_404(tag_id)
        
        # Remove tag from post
        if tag in post.tags:
            post.tags.remove(tag)
            db.session.commit()
        
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500