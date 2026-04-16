from flask import Blueprint, request, jsonify, abort, current_app
from app.models.post import Post, serialize_post_tag
from app.models.tag import Tag, TagType
from app.models.course import Course
from app.models.reaction import Reaction
from app.extensions import db
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func, desc, asc, text
from datetime import datetime, timedelta
import json
import bleach
from app.services.content_moderation_service import content_moderation

bp = Blueprint('post', __name__, url_prefix='/posts')

MAX_POST_TAG_COUNT = 5
MAX_POST_TAG_LENGTH = 50
SYSTEM_REVIEW_TAG = "course-review"

def normalize_post_tags(raw_tag_names):
    if raw_tag_names is None:
        return []
    if not isinstance(raw_tag_names, list):
        raise ValueError("Tags must be provided as an array of strings.")

    tag_names = []
    seen_tag_names = set()

    for raw_tag_name in raw_tag_names:
        if not isinstance(raw_tag_name, str):
            raise ValueError("Each tag must be a string.")

        normalized_tag_name = " ".join(raw_tag_name.strip().split())
        if not normalized_tag_name:
            continue
        if len(normalized_tag_name) > MAX_POST_TAG_LENGTH:
            raise ValueError(f"Each tag must be {MAX_POST_TAG_LENGTH} characters or fewer.")

        normalized_key = normalized_tag_name.casefold()
        if normalized_key in seen_tag_names:
            continue

        seen_tag_names.add(normalized_key)
        tag_names.append(normalized_tag_name)

    if len(tag_names) > MAX_POST_TAG_COUNT:
        raise ValueError(f"A post can have at most {MAX_POST_TAG_COUNT} tags.")

    return tag_names


def _get_or_create_tag_type(type_name):
    tag_type = TagType.query.filter_by(name=type_name).first()
    if not tag_type:
        tag_type = TagType(name=type_name)
        db.session.add(tag_type)
        db.session.flush()
    return tag_type


def _get_or_create_system_tag(tag_name, description=None):
    existing_tag = Tag.query.filter_by(name=tag_name).first()
    if existing_tag:
        return existing_tag

    system_type = _get_or_create_tag_type(TagType.SYSTEM)
    tag = Tag(
        name=tag_name,
        tag_type_id=system_type.id,
        description=description or f"System tag: {tag_name}",
    )
    db.session.add(tag)
    db.session.flush()
    return tag

def validate_and_get_tag(tag_name, allow_course_creation=False):
    """
    Validate and get an existing tag or create a new one.

    We intentionally do not enforce course/offering semantics here anymore.
    Tags remain free-form aside from basic normalization limits enforced earlier.
    
    Args:
        tag_name: The tag name to validate
        allow_course_creation: Retained for compatibility; no longer used to
            enforce semantic tag validation.
    
    Returns:
        Tag object if valid, raises ValueError if invalid
    """
    # Check if tag already exists
    existing_tag = Tag.query.filter_by(name=tag_name).first()
    if existing_tag:
        return existing_tag

    if tag_name == SYSTEM_REVIEW_TAG:
        return _get_or_create_system_tag(
            tag_name,
            description="System tag for course review posts"
        )
    
    # For non-course tags, optionally map real course codes to course tags.
    # If the course doesn't exist, treat it as a normal user tag instead.
    if tag_name.replace(' ', '').replace('-', '').isalnum() and len(tag_name.split()) <= 3:
        course = Course.query.filter_by(code=tag_name, is_deleted=False).first()
        if course:
            tag_type = _get_or_create_tag_type(TagType.COURSE)
            
            tag = Tag(
                name=tag_name,
                tag_type_id=tag_type.id,
                description=f"Course: {tag_name}"
            )
            db.session.add(tag)
            db.session.flush()
            return tag
    
    # For user tags, allow creation
    tag_type = _get_or_create_tag_type(TagType.USER)
    
    tag = Tag(
        name=tag_name,
        tag_type_id=tag_type.id,
        description=f"User tag: {tag_name}"
    )
    db.session.add(tag)
    db.session.flush()
    
    return tag

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
    tags_param = request.args.get('tags')  # Can be comma-separated list of tag names
    tag_match = request.args.get('tag_match', 'any').lower()
    exclude_tags_param = request.args.get('exclude_tags')
    
    # Start building the query
    query = Post.query.filter(Post.is_deleted == False)
    
    # Apply filters
    if user_id:
        query = query.filter(Post.user_id == user_id)
    
    # Tag filtering
    if tags_param:
        tag_names = [tag.strip() for tag in tags_param.split(',') if tag.strip()]
        if tag_names:
            if tag_match not in {'any', 'all'}:
                return jsonify({"error": "Invalid tag_match. Use 'any' or 'all'."}), 400

            if tag_match == 'all':
                for tag_name in tag_names:
                    query = query.filter(Post.tags.any(Tag.name == tag_name))
            else:
                # Filter posts that have ANY of the specified tags
                query = query.join(Post.tags).filter(Tag.name.in_(tag_names)).distinct()

    if exclude_tags_param:
        exclude_tag_names = [tag.strip() for tag in exclude_tags_param.split(',') if tag.strip()]
        for tag_name in exclude_tag_names:
            query = query.filter(~Post.tags.any(Tag.name == tag_name))
    
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
        "posts": [post.to_dict(include_tags=True, include_author=True) for post in paginated_posts.items],
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
    
    # Content moderation check
    moderation_result = content_moderation.moderate_post(
        title=data['title'],
        content=data['content'],
        data_id=f"post_{user_id}_{datetime.now().timestamp()}"
    )
    
    if not moderation_result['is_safe']:
        current_app.logger.warning(f"Content moderation blocked post from user {user_id}: {moderation_result['reason']}")
        return jsonify({
            "error": "Content moderation failed",
            "message": "Your content violates community guidelines and cannot be published.",
            "details": moderation_result['reason'],
            "risk_level": moderation_result['risk_level']
        }), 400
    
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
    
    # Create new post
    post = Post(
        user_id=user_id,
        title=data['title'],
        content=data['content'],
        display_identity_id=display_identity_id,
        embedding=data.get('embedding')
    )
    
    db.session.add(post)
    db.session.flush()  # Flush to get the post ID before commit
    
    # Link uploaded files to this post
    file_ids = data.get('file_ids', [])
    if file_ids:
        from app.models.file import File
        allowed_post_file_types = frozenset({File.POST_IMAGE, File.POST_ATTACHMENT})
        for file_id in file_ids:
            file_record = File.query.filter_by(
                id=file_id, 
                user_id=user_id,
                status='uploaded',
                is_deleted=False
            ).first()
            if not file_record:
                continue
            if file_record.file_type not in allowed_post_file_types:
                db.session.rollback()
                return jsonify({
                    "error": "Invalid attachment",
                    "message": "仅允许通过论坛上传接口添加的图片或附件。",
                }), 400
            if (
                file_record.file_size is not None
                and int(file_record.file_size) > File.MAX_UPLOAD_BYTES
            ):
                db.session.rollback()
                return jsonify({
                    "error": "Attachment too large",
                    "message": f"附件「{file_record.original_filename}」超过 10MB 限制。",
                }), 400
            file_record.entity_type = 'post'
            file_record.entity_id = post.id
    
    # Handle tags with validation
    try:
        tag_names = normalize_post_tags(data.get('tags', []))
    except ValueError as e:
        return jsonify({
            "error": "Tag validation failed",
            "message": str(e)
        }), 400

    tag_errors = []
    
    if tag_names:
        for tag_name in tag_names:
            try:
                tag = validate_and_get_tag(tag_name)
                # Add tag to post if not already linked
                if tag not in post.tags:
                    post.tags.append(tag)
            except ValueError as e:
                tag_errors.append(f"Tag '{tag_name}': {str(e)}")
            except Exception as e:
                current_app.logger.error(f"Unexpected error with tag '{tag_name}': {str(e)}")
                tag_errors.append(f"Tag '{tag_name}': Internal error")
    
    # If there are tag validation errors, rollback and return error
    if tag_errors:
        db.session.rollback()
        return jsonify({
            "error": "Tag validation failed",
            "message": "One or more tags are invalid.",
            "tag_errors": tag_errors
        }), 400
    
    db.session.commit()
    
    return jsonify(post.to_dict(include_tags=True, include_files=True)), 201

@bp.route('/<int:post_id>', methods=['GET'])
def get_post(post_id):
    post = Post.query.get_or_404(post_id)
    user_id = request.args.get('user_id', type=int)
    
    # Increment view count
    post.increment_view()
    db.session.commit()
    
    # Get tags
    tags = [serialize_post_tag(tag) for tag in post.tags]
    
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
        
        # Use comment.to_dict() to include author information
        comment_dict = comment.to_dict()
        comment_dict.update({
            "reaction_list": comment_reactions_list,
            "time": comment.created_at.isoformat(),  # Keep legacy field name
            "post_id": post.id
        })
        comments_list.append(comment_dict)
    
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
    
    # Get files associated with this post
    files = [file.to_dict() for file in post.files if file.status == 'uploaded']
    current_app.logger.info(f"Post {post_id} has {len(files)} files: {[f['original_filename'] for f in files]}")
    
    # Get base post data including author information
    post_detail = post.to_dict(include_content=True, include_tags=False, include_files=False)
    
    # Add additional fields specific to post detail view
    post_detail.update({
        "author_id": post.user_id,  # Keep legacy field name
        "reaction_list": reactions,
        "background_url": "",  # Placeholder for background image
        "time": post.created_at.isoformat(),  # Keep legacy field name
        "tags": tags,
        "comments_list": comments_list,
        "files": files  # Include files in response
    })
    
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
    if str(post.user_id) != str(current_user_id):
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
        tags = [serialize_post_tag(tag) for tag in post.tags]
        
        # Get base post data including author information
        hot_post = post.to_dict(include_content=False, include_tags=False, include_files=False)
        
        # Create content preview (first 150 chars)
        content_preview = post.content[:150] + "..." if len(post.content) > 150 else post.content
        
        # Add additional fields specific to hot posts view
        hot_post.update({
            "postID": post.id,  # Legacy field name
            "content_preview": content_preview,
            "click_count": post.view_count,  # Legacy field name
            "time": post.created_at.isoformat(),  # Legacy field name  
            "background_url": "",  # Placeholder for background image
            "tags": tags,
            "user_choice": user_choice
        })
        
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

    safe_query = "%" + query_string.replace('%', r'\%').replace('_', r'\_') + "%"
    
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
