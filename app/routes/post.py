from flask import Blueprint, request, jsonify, abort
from app.models.post import Post
from app.models.tag import Tag
from app.models.reaction import Reaction
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc, asc, text
from datetime import datetime, timedelta
import json
import bleach

bp = Blueprint('post', __name__, url_prefix='/posts')

@bp.route('', methods=['GET'])
def get_posts():
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    
    # Get filter parameters
    user_id = request.args.get('user_id', type=int)
    sort_by = request.args.get('sort_by', 'created_at')
    sort_order = request.args.get('sort_order', 'desc')
    date_param = request.args.get('date')
    
    # Start building the query
    query = Post.query
    
    # Apply filters
    if user_id:
        query = query.filter(Post.user_id == user_id)
    
    # Date filtering
    if date_param:
        date_parts = date_param.split(',')
        if len(date_parts) == 1:
            # Single date
            try:
                target_date = datetime.strptime(date_parts[0], '%Y-%m-%d').date()
                query = query.filter(
                    func.date(Post.created_at) == target_date
                )
            except ValueError:
                return jsonify({"error": "Invalid date format"}), 400
        elif len(date_parts) == 2:
            # Date range
            try:
                start_date = datetime.strptime(date_parts[0], '%Y-%m-%d').date()
                end_date = datetime.strptime(date_parts[1], '%Y-%m-%d').date()
                query = query.filter(
                    func.date(Post.created_at) >= start_date,
                    func.date(Post.created_at) <= end_date
                )
            except ValueError:
                return jsonify({"error": "Invalid date format"}), 400
    
    # Apply sorting
    valid_sort_fields = {
        'created_at': Post.created_at,
        'updated_at': Post.updated_at,
        'view_count': Post.view_count,
        'comment_count': Post.comment_count,
        'reaction_count': Post.reaction_count
    }
    
    if sort_by not in valid_sort_fields:
        return jsonify({"error": f"Invalid sort_by field: {sort_by}"}), 400
    
    sort_field = valid_sort_fields[sort_by]
    if sort_order == 'desc':
        query = query.order_by(desc(sort_field))
    else:
        query = query.order_by(asc(sort_field))
    
    # Paginate results
    paginated_posts = query.paginate(page=page, per_page=limit, error_out=False)
    
    # Prepare response
    response = {
        "posts": [post.to_dict() for post in paginated_posts.items],
        "total_count": paginated_posts.total,
        "total_pages": paginated_posts.pages,
        "current_page": page
    }
    
    return jsonify(response), 200

@bp.route('', methods=['POST'])
@jwt_required()
def create_post():
    data = request.get_json() or {}
    
    # Validation
    required_fields = ['title', 'content']
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Use the authenticated user's ID
    user_id = get_jwt_identity()
    
    # Create new post
    post = Post(
        user_id=user_id,
        title=data['title'],
        content=data['content'],
        embedding=data.get('embedding')
    )
    
    db.session.add(post)
    db.session.commit()
    
    return jsonify(post.to_dict()), 201

@bp.route('/<int:post_id>', methods=['GET'])
def get_post(post_id):
    post = Post.query.get_or_404(post_id)
    user_id = request.args.get('user_id', type=int)
    
    # Increment view count
    post.increment_view()
    db.session.commit()
    
    # Get tags
    tags = [{"tag_name": tag.name, "isImportant": tag.tag_type == "system", "tagcolor": "#3498db"} for tag in post.tags]
    
    # Get reactions with counts
    reactions_query = db.session.query(
        Reaction.emoji,
        func.count(Reaction.id).label('count')
    ).filter(
        Reaction.post_id == post_id,
        Reaction.comment_id == None
    ).group_by(Reaction.emoji).all()
    
    reactions = []
    for emoji, count in reactions_query:
        emoji_obj = {
            "emoji": {
                "emoji_id": 0,  # Placeholder, would come from an actual emoji table
                "emoji_code": emoji,
                "description": f"Emoji {emoji}",
                "image_url": f"/static/emojis/{emoji}.png",  # Placeholder URL
                "display_order": 0
            },
            "emoji_count": count
        }
        reactions.append(emoji_obj)
    
    # Get comments with reactions
    comments_list = []
    for comment in post.comments:
        comment_reactions = db.session.query(
            Reaction.emoji,
            func.count(Reaction.id).label('count')
        ).filter(
            Reaction.comment_id == comment.id
        ).group_by(Reaction.emoji).all()
        
        comment_reactions_list = []
        for emoji, count in comment_reactions:
            emoji_obj = {
                "emoji": {
                    "emoji_id": 0,
                    "emoji_code": emoji,
                    "description": f"Emoji {emoji}",
                    "image_url": f"/static/emojis/{emoji}.png",
                    "display_order": 0
                },
                "emoji_count": count
            }
            comment_reactions_list.append(emoji_obj)
        
        comments_list.append({
            "content": comment.content,
            "parent_comment_id": comment.parent_comment_id,
            "time": comment.created_at.isoformat(),
            "reaction_list": comment_reactions_list,
            "post_id": post.id
        })
    
    # Get user's reaction if user_id is provided
    user_choice = None
    if user_id:
        user_reaction = Reaction.query.filter_by(
            post_id=post_id,
            user_id=user_id,
            comment_id=None
        ).first()
        if user_reaction:
            user_choice = user_reaction.emoji
    
    post_detail = {
        "id": post.id,
        "title": post.title,
        "content": post.content,
        "reaction_list": reactions,
        "view_count": post.views_count,
        "background_url": "",  # Placeholder for background image
        "time": post.created_at.isoformat(),
        "tags": tags,
        "comments_list": comments_list
    }
    
    return jsonify(post_detail), 200

@bp.route('/<int:post_id>', methods=['PUT'])
@jwt_required()
def update_post(post_id):
    post = Post.query.get_or_404(post_id)
    current_user_id = get_jwt_identity()
    
    # Check permissions
    if post.user_id != current_user_id:
        return jsonify({"error": "You don't have permission to update this post"}), 403
    
    data = request.get_json() or {}
    
    # Update post fields
    if 'title' in data:
        post.title = data['title']
    if 'content' in data:
        post.content = data['content']
    if 'embedding' in data:
        post.embedding = data['embedding']
    
    post.updated_at = datetime.now()
    db.session.commit()
    
    return jsonify(post.to_dict()), 200

@bp.route('/<int:post_id>', methods=['DELETE'])
@jwt_required()
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    current_user_id = get_jwt_identity()
    
    # Check permissions
    if post.user_id != current_user_id:
        return jsonify({"error": "You don't have permission to delete this post"}), 403
    
    db.session.delete(post)
    db.session.commit()
    
    return "", 204

@bp.route('/hot', methods=['GET'])
def get_hot_posts():
    user_id = request.args.get('user_id', type=int)
    limit = request.args.get('limit', 10, type=int)
    algorithm = request.args.get('algorithm', 'trending')
    
    # Base query
    query = Post.query
    
    # Different algorithms for hot posts
    if algorithm == 'trending':
        # Example trending algorithm: weight recent activity higher
        # (3 * reaction_count + 2 * comment_count + views_count) / days_since_post
        three_days_ago = datetime.now() - timedelta(days=3)
        query = query.filter(Post.created_at >= three_days_ago)
        # This is a simplified version - a real trending algorithm would be more complex
        query = query.order_by(desc(
            (3 * Post.reaction_count + 2 * Post.comment_count + Post.view_count)
        ))
    elif algorithm == 'most_viewed':
        query = query.order_by(desc(Post.view_count))
    elif algorithm == 'most_reacted':
        query = query.order_by(desc(Post.reaction_count))
    elif algorithm == 'most_commented':
        query = query.order_by(desc(Post.comment_count))
    else:
        return jsonify({"error": f"Invalid algorithm: {algorithm}"}), 400
    
    # Get the hot posts
    hot_posts = query.limit(limit).all()
    
    # Format the response
    result = []
    for post in hot_posts:
        # Get most frequent reaction for this post
        most_frequent_reaction = db.session.query(
            Reaction.emoji,
            func.count(Reaction.id).label('count')
        ).filter(
            Reaction.post_id == post.id,
            Reaction.comment_id == None
        ).group_by(Reaction.emoji).order_by(desc('count')).first()
        
        # Get user's reaction if user_id is provided
        user_choice = None
        if user_id:
            user_reaction = Reaction.query.filter_by(
                post_id=post.id,
                user_id=user_id,
                comment_id=None
            ).first()
            if user_reaction:
                user_choice = user_reaction.emoji
        
        # Get tags
        tags = [{"tag_name": tag.name, "isImportant": tag.tag_type == "system", "tagcolor": "#3498db"} for tag in post.tags]
        
        # Create content preview (first 150 chars)
        content_preview = post.content[:150] + "..." if len(post.content) > 150 else post.content
        
        hot_post = {
            "postID": post.id,
            "title": post.title,
            "content_preview": content_preview,
            "comment_count": post.comment_count,
            "click_count": post.views_count,
            "time": post.created_at.isoformat(),
            "background_url": "",  # Placeholder for background image
            "tags": tags,
            "user_choice": user_choice
        }
        
        # Add most frequent reaction if it exists
        if most_frequent_reaction:
            emoji, count = most_frequent_reaction
            hot_post["most_frequent_reaction_count"] = count
            hot_post["most_frequent_reaction"] = {
                "emoji_id": 0,  # Placeholder
                "emoji_code": emoji,
                "description": f"Emoji {emoji}",
                "image_url": f"/static/emojis/{emoji}.png",  # Placeholder URL
                "display_order": 0
            }
        
        result.append(hot_post)
    
    return jsonify(result), 200

@bp.route('/search', methods=['GET'])
def search_posts():
    query_string = bleach.clean(request.args.get('q', ''))
    if not query_string:
        return jsonify({"error": "Search query is required"}), 400

    safe_query = f"%{query_string.replace('%', r'\%').replace('_', r'\_')}%"
    
    search_type = request.args.get('search_type', 'keyword')
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    
    # Basic search query
    if search_type == 'keyword':
        # Simple keyword search in title and content
        search_query = Post.query.filter(
            (Post.title.ilike(safe_query, escape='\\')) | 
            (Post.content.ilike(safe_query, escape='\\'))
        )
    elif search_type == 'semantic':
        # For semantic search, normally you would:
        # 1. Convert the query to an embedding using same model as post embeddings
        # 2. Find posts with similar embeddings
        # This is a simplified placeholder - actual semantic search would be more complex
        return jsonify({"error": "Semantic search not implemented yet"}), 501
    else:
        return jsonify({"error": f"Invalid search_type: {search_type}"}), 400
    
    # Paginate results
    paginated_posts = search_query.paginate(page=page, per_page=limit, error_out=False)
    
    # Prepare response
    response = {
        "posts": [post.to_dict() for post in paginated_posts.items],
        "total_count": paginated_posts.total,
        "total_pages": paginated_posts.pages,
        "current_page": page
    }
    
    return jsonify(response), 200
