from flask import Blueprint, request, jsonify, abort
from app.models.post import Post
from app.models.comment import Comment
from app.models.user import User
from app.models.reaction import Reaction
from app.extensions import db#, limiter
from app.services.notification_service import NotificationService
from app.services.content_moderation_service import content_moderation
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc, asc
from datetime import datetime, timezone
import bleach

bp = Blueprint('comment', __name__, url_prefix='/comments')

# Get a list of comments with pagination and filtering
@bp.route('', methods=['GET'])
# @limiter.limit("60 per minute")
def get_comments():
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    
    # Get filter parameters
    user_id = request.args.get('user_id', type=int)
    post_id = request.args.get('post_id', type=int)
    
    # Start building the query
    query = Comment.query.filter_by(is_deleted=False)
    
    # Apply filters
    if user_id:
        query = query.filter(Comment.user_id == user_id)
    if post_id:
        query = query.filter(Comment.post_id == post_id)
    
    # Order by creation date (newest first)
    query = query.order_by(desc(Comment.created_at))
    
    # Paginate results
    paginated_comments = query.paginate(page=page, per_page=limit, error_out=False)
    
    # Prepare response
    response = {
        "comments": [comment.to_dict() for comment in paginated_comments.items],
        "total_count": paginated_comments.total,
        "total_pages": paginated_comments.pages,
        "current_page": page
    }
    
    return jsonify(response), 200

# Create a new comment
@bp.route('', methods=['POST'])
@jwt_required()
# @limiter.limit("20 per minute")
def create_comment():
    try:
        data = request.get_json() or {}
        
        # Validate required fields
        if not data.get('content'):
            return jsonify({"error": "Comment content is required"}), 400
        if not data.get('post_id'):
            return jsonify({"error": "Post ID is required"}), 400
        
        # Sanitize content
        content = bleach.clean(data['content'])
        
        # Use authenticated user's ID
        user_id = get_jwt_identity()
        
        # Content moderation check
        moderation_result = content_moderation.moderate_comment(
            content=content,
            data_id=f"comment_{user_id}_{datetime.now().timestamp()}"
        )
        
        if not moderation_result['is_safe']:
            from flask import current_app
            current_app.logger.warning(f"Content moderation blocked comment from user {user_id}: {moderation_result['reason']}")
            return jsonify({
                "error": "Content moderation failed",
                "message": "Your comment violates community guidelines and cannot be published.",
                "details": moderation_result['reason'],
                "risk_level": moderation_result['risk_level']
            }), 400
        
        # Check if post exists
        post = Post.query.get_or_404(data['post_id'])
        
        # Check parent comment if provided
        parent_comment_id = data.get('parent_comment_id')
        if parent_comment_id:
            parent_comment = Comment.query.get_or_404(parent_comment_id)
            # Ensure parent comment belongs to the same post
            if parent_comment.post_id != post.id:
                return jsonify({"error": "Parent comment does not belong to the specified post"}), 400
        
        # Validate display_identity_id if provided
        display_identity_id = data.get('display_identity_id')
        if display_identity_id:
            from app.models.user_identity import UserIdentity
            identity = UserIdentity.query.filter_by(
                id=display_identity_id,
                user_id=user_id,
                status=UserIdentity.APPROVED
            ).first()
            if not identity:
                return jsonify({"error": "Invalid or unauthorized identity selected"}), 400
        
        # Create new comment
        comment = Comment(
            post_id=post.id,
            user_id=user_id,
            content=content,
            display_identity_id=display_identity_id,
            embedding=data.get('embedding'),
            parent_comment_id=parent_comment_id
        )
        
        # Update post comment count
        post.comment_count += 1
        
        db.session.add(comment)
        db.session.commit()
        
        # Create notifications for comment
        try:
            if parent_comment_id:
                # This is a reply to a comment
                NotificationService.create_comment_reply_notification(comment)
            else:
                # This is a comment on a post
                NotificationService.create_post_comment_notification(comment)
            db.session.commit()
        except Exception as e:
            # Log error but don't fail the comment creation
            print(f"Failed to create notification: {str(e)}")
        
        return jsonify(comment.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get a specific comment
@bp.route('/<int:comment_id>', methods=['GET'])
# @limiter.limit("60 per minute")
def get_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    include_replies = request.args.get('include_replies', 'false').lower() == 'true'
    
    result = comment.to_dict()
    
    if include_replies:
        result['replies'] = [reply.to_dict() for reply in comment.replies.filter_by(is_deleted=False)]
    
    return jsonify(result), 200

# Update a comment
@bp.route('/<int:comment_id>', methods=['PUT'])
@jwt_required()
# @limiter.limit("20 per minute")
def update_comment(comment_id):
    try:
        comment = Comment.query.get_or_404(comment_id)
        current_user_id = get_jwt_identity()
        
        # Check permissions
        if comment.user_id != current_user_id:
            return jsonify({"error": "You don't have permission to update this comment"}), 403
        
        data = request.get_json() or {}
        
        # Update comment fields
        if 'content' in data:
            comment.content = bleach.clean(data['content'])
        if 'embedding' in data:
            comment.embedding = data['embedding']
        
        comment.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        return jsonify(comment.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Delete a comment (soft delete)
@bp.route('/<int:comment_id>', methods=['DELETE'])
@jwt_required()
# @limiter.limit("10 per minute")
def delete_comment(comment_id):
    try:
        comment = Comment.query.get_or_404(comment_id)
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)

        # Check permissions (owner or moderator)
        if str(comment.user_id) != current_user_id and not user.is_moderator():
            return jsonify({"error": "You don't have permission to delete this comment"}), 403
        
        # Soft delete
        comment.is_deleted = True
        comment.deleted_at = datetime.now(timezone.utc)
        
        # Update post comment count
        post = Post.query.get(comment.post_id)
        if post and post.comment_count > 0:
            post.comment_count -= 1
        
        db.session.commit()
        
        return "", 204
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# Get comments for a specific post
@bp.route('/post/<int:post_id>', methods=['GET'])
# @limiter.limit("60 per minute")
def get_post_comments(post_id):
    # Check if post exists
    post = Post.query.get_or_404(post_id)
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    
    # Get sorting parameters
    sort_by = request.args.get('sort_by', 'created_at')
    sort_order = request.args.get('sort_order', 'asc')
    
    # Get filtering parameters
    include_replies = request.args.get('include_replies', 'true').lower() == 'true'
    top_level_only = request.args.get('top_level_only', 'false').lower() == 'true'
    
    # Start building the query
    query = Comment.query.filter_by(post_id=post_id, is_deleted=False)
    
    # Apply top-level filter if needed
    if top_level_only:
        query = query.filter(Comment.parent_comment_id == None)
    
    # Apply sorting
    valid_sort_fields = {
        'created_at': Comment.created_at,
        'updated_at': Comment.updated_at
    }
    
    if sort_by not in valid_sort_fields:
        return jsonify({"error": f"Invalid sort_by field: {sort_by}"}), 400
    
    sort_field = valid_sort_fields[sort_by]
    if sort_order == 'desc':
        query = query.order_by(desc(sort_field))
    else:
        query = query.order_by(asc(sort_field))
    
    # Paginate results
    paginated_comments = query.paginate(page=page, per_page=limit, error_out=False)
    
    # Prepare response
    comments_list = []
    for comment in paginated_comments.items:
        comment_dict = comment.to_dict()
        
        # Include replies if requested
        if include_replies:
            comment_dict['replies'] = [
                reply.to_dict() for reply in comment.replies.filter_by(is_deleted=False)
            ]
        
        comments_list.append(comment_dict)
    
    response = {
        "comments": comments_list,
        "total_count": paginated_comments.total,
        "total_pages": paginated_comments.pages,
        "current_page": page
    }
    
    return jsonify(response), 200