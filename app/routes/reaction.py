from flask import Blueprint, request, jsonify
from app.models.reaction import Reaction
from app.models.reaction_emoji import ReactionEmoji
from app.models.post import Post
from app.models.comment import Comment
from app.extensions import db#, limiter
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc
from datetime import datetime, timezone

bp = Blueprint('reaction', __name__, url_prefix='/reactions')

# Get all available emojis
@bp.route('/emojis', methods=['GET'])
# @limiter.limit("60 per minute")
def get_emojis():
    """Get all available reaction emojis"""
    # Get active emojis ordered by display_order
    emojis = ReactionEmoji.query.filter_by(is_active=True).order_by(ReactionEmoji.display_order).all()
    
    # Format response
    result = [emoji.to_dict() for emoji in emojis]
    
    return jsonify(result), 200

# Get a specific emoji
@bp.route('/emojis/<int:emoji_id>', methods=['GET'])
# @limiter.limit("60 per minute")
def get_emoji(emoji_id):
    """Get a specific emoji by ID"""
    emoji = ReactionEmoji.query.get_or_404(emoji_id)
    
    return jsonify(emoji.to_dict()), 200

# Get reactions for a post
@bp.route('/posts/<int:post_id>/reactions', methods=['GET'])
# @limiter.limit("60 per minute")
def get_post_reactions(post_id):
    """Get all reactions for a specific post"""
    # Check if post exists
    post = Post.query.get_or_404(post_id)
    
    # Get user_id from query params if provided
    user_id = request.args.get('user_id', type=int)
    
    # Get reaction counts grouped by emoji
    reaction_counts = db.session.query(
        ReactionEmoji,
        func.count(Reaction.id).label('count')
    ).join(
        Reaction, Reaction.emoji_id == ReactionEmoji.id
    ).filter(
        Reaction.post_id == post_id
    ).group_by(
        ReactionEmoji.id
    ).order_by(
        desc('count'),
        ReactionEmoji.display_order
    ).all()
    
    # Format reaction counts
    reactions = []
    for emoji, count in reaction_counts:
        reactions.append({
            "emoji": emoji.to_dict(),
            "count": count
        })
    
    # Get user's reactions if user_id is provided
    user_reactions = []
    if user_id:
        user_emojis = db.session.query(
            ReactionEmoji
        ).join(
            Reaction, Reaction.emoji_id == ReactionEmoji.id
        ).filter(
            Reaction.post_id == post_id,
            Reaction.user_id == user_id
        ).all()
        
        user_reactions = [emoji.to_dict() for emoji in user_emojis]
    
    # Format response
    response = {
        "reactions": reactions,
        "user_reactions": user_reactions
    }
    
    return jsonify(response), 200

# Add a reaction to a post
@bp.route('/posts/<int:post_id>/reactions', methods=['POST'])
@jwt_required()
# @limiter.limit("30 per minute")
def add_post_reaction(post_id):
    """Add a reaction to a post"""
    try:
        # Check if post exists
        post = Post.query.get_or_404(post_id)
        
        # Get current user
        user_id = get_jwt_identity()
        
        # Get request data
        data = request.get_json() or {}
        
        # Validate required fields
        if not data.get('emoji_id'):
            return jsonify({"error": "emoji_id is required"}), 400
        
        # Check if emoji exists
        emoji_id = data['emoji_id']
        emoji = ReactionEmoji.query.get_or_404(emoji_id)
        
        if not emoji.is_active:
            return jsonify({"error": "This emoji is not active"}), 400
        
        # Check if reaction already exists
        existing_reaction = Reaction.query.filter_by(
            user_id=user_id,
            post_id=post_id,
            emoji_id=emoji_id
        ).first()
        
        if existing_reaction:
            return jsonify({"error": "You have already reacted with this emoji"}), 400
        
        # Create new reaction
        reaction = Reaction(
            user_id=user_id,
            post_id=post_id,
            emoji_id=emoji_id
        )
        
        db.session.add(reaction)
        
        # Update post reaction count
        post.reaction_count = Reaction.query.filter_by(post_id=post_id).count() + 1
        
        db.session.commit()
        
        return jsonify(reaction.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Remove a reaction from a post
@bp.route('/posts/<int:post_id>/reactions', methods=['DELETE'])
@jwt_required()
# @limiter.limit("30 per minute")
def remove_post_reaction(post_id):
    """Remove a reaction from a post"""
    try:
        # Check if post exists
        post = Post.query.get_or_404(post_id)
        
        # Get current user
        user_id = get_jwt_identity()
        
        # Get emoji_id from query params
        emoji_id = request.args.get('emoji_id', type=int)
        if not emoji_id:
            return jsonify({"error": "emoji_id query parameter is required"}), 400
        
        # Find the reaction
        reaction = Reaction.query.filter_by(
            user_id=user_id,
            post_id=post_id,
            emoji_id=emoji_id
        ).first()
        
        if not reaction:
            return jsonify({"error": "Reaction not found"}), 404
        
        # Delete the reaction
        db.session.delete(reaction)
        
        # Update post reaction count
        post.reaction_count = Reaction.query.filter_by(post_id=post_id).count() - 1
        
        db.session.commit()
        
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get reactions for a comment
@bp.route('/comments/<int:comment_id>/reactions', methods=['GET'])
# @limiter.limit("60 per minute")
def get_comment_reactions(comment_id):
    """Get all reactions for a specific comment"""
    # Check if comment exists
    comment = Comment.query.get_or_404(comment_id)
    
    # Get user_id from query params if provided
    user_id = request.args.get('user_id', type=int)
    
    # Get reaction counts grouped by emoji
    reaction_counts = db.session.query(
        ReactionEmoji,
        func.count(Reaction.id).label('count')
    ).join(
        Reaction, Reaction.emoji_id == ReactionEmoji.id
    ).filter(
        Reaction.comment_id == comment_id
    ).group_by(
        ReactionEmoji.id
    ).order_by(
        desc('count'),
        ReactionEmoji.display_order
    ).all()
    
    # Format reaction counts
    reactions = []
    for emoji, count in reaction_counts:
        reactions.append({
            "emoji": emoji.to_dict(),
            "count": count
        })
    
    # Get user's reactions if user_id is provided
    user_reactions = []
    if user_id:
        user_emojis = db.session.query(
            ReactionEmoji
        ).join(
            Reaction, Reaction.emoji_id == ReactionEmoji.id
        ).filter(
            Reaction.comment_id == comment_id,
            Reaction.user_id == user_id
        ).all()
        
        user_reactions = [emoji.to_dict() for emoji in user_emojis]
    
    # Format response
    response = {
        "reactions": reactions,
        "user_reactions": user_reactions
    }
    
    return jsonify(response), 200

# Add a reaction to a comment
@bp.route('/comments/<int:comment_id>/reactions', methods=['POST'])
@jwt_required()
# @limiter.limit("30 per minute")
def add_comment_reaction(comment_id):
    """Add a reaction to a comment"""
    try:
        # Check if comment exists
        comment = Comment.query.get_or_404(comment_id)
        
        # Get current user
        user_id = get_jwt_identity()
        
        # Get request data
        data = request.get_json() or {}
        
        # Validate required fields
        if not data.get('emoji_id'):
            return jsonify({"error": "emoji_id is required"}), 400
        
        # Check if emoji exists
        emoji_id = data['emoji_id']
        emoji = ReactionEmoji.query.get_or_404(emoji_id)
        
        if not emoji.is_active:
            return jsonify({"error": "This emoji is not active"}), 400
        
        # Check if reaction already exists
        existing_reaction = Reaction.query.filter_by(
            user_id=user_id,
            comment_id=comment_id,
            emoji_id=emoji_id
        ).first()
        
        if existing_reaction:
            return jsonify({"error": "You have already reacted with this emoji"}), 400
        
        # Create new reaction
        reaction = Reaction(
            user_id=user_id,
            comment_id=comment_id,
            emoji_id=emoji_id
        )
        
        db.session.add(reaction)
        db.session.commit()
        
        return jsonify(reaction.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Remove a reaction from a comment
@bp.route('/comments/<int:comment_id>/reactions', methods=['DELETE'])
@jwt_required()
# @limiter.limit("30 per minute")
def remove_comment_reaction(comment_id):
    """Remove a reaction from a comment"""
    try:
        # Check if comment exists
        comment = Comment.query.get_or_404(comment_id)
        
        # Get current user
        user_id = get_jwt_identity()
        
        # Get emoji_id from query params
        emoji_id = request.args.get('emoji_id', type=int)
        if not emoji_id:
            return jsonify({"error": "emoji_id query parameter is required"}), 400
        
        # Find the reaction
        reaction = Reaction.query.filter_by(
            user_id=user_id,
            comment_id=comment_id,
            emoji_id=emoji_id
        ).first()
        
        if not reaction:
            return jsonify({"error": "Reaction not found"}), 404
        
        # Delete the reaction
        db.session.delete(reaction)
        db.session.commit()
        
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get user's reactions
@bp.route('/users/<int:user_id>', methods=['GET'])
# @limiter.limit("30 per minute")
def get_user_reactions(user_id):
    """Get all reactions by a specific user"""
    # Get filter parameters
    target_type = request.args.get('target_type')  # 'post' or 'comment'
    
    # Start building the query
    query = Reaction.query.filter_by(user_id=user_id)
    
    # Apply filters
    if target_type == 'post':
        query = query.filter(Reaction.post_id != None)
    elif target_type == 'comment':
        query = query.filter(Reaction.comment_id != None)
    
    # Execute query
    reactions = query.order_by(desc(Reaction.created_at)).all()
    
    # Format response
    result = [reaction.to_dict() for reaction in reactions]
    
    return jsonify(result), 200