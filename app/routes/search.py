from flask import Blueprint, request, jsonify
from app.models.post import Post
from app.models.comment import Comment
from app.models.user import User
from app.models.tag import Tag, TagType
from app.models.course import Course
from app.extensions import db
from sqlalchemy import func, desc, and_, or_
from datetime import datetime, timezone
import re

bp = Blueprint('search', __name__, url_prefix='/search')

# Note: Search endpoints are public to allow guest access
# TODO: Consider adding rate limiting for security

@bp.route('/posts', methods=['GET'])
def search_posts():
    """
    Search for posts by title, content, author username, and tags.
    Supports pagination and sorting options.
    """
    # Get search parameters
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 50)  # Max 50 results per page
    sort_by = request.args.get('sort', 'relevance')  # relevance, date, popularity
    
    # Validate query
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters long"}), 400
    
    if len(query) > 100:
        return jsonify({"error": "Search query too long"}), 400
    
    # Build base query for non-deleted posts
    base_query = Post.query.filter(Post.is_deleted == False)
    
    # Search logic
    search_filters = []
    
    # Split query into words for better matching
    search_words = [word.strip() for word in query.split() if word.strip()]
    
    for word in search_words:
        word_filters = [
            Post.title.ilike(f'%{word}%'),
            Post.content.ilike(f'%{word}%')
        ]
        search_filters.append(or_(*word_filters))
    
    # Apply search filters
    if search_filters:
        search_query = base_query.filter(and_(*search_filters))
    else:
        search_query = base_query.filter(
            or_(
                Post.title.ilike(f'%{query}%'),
                Post.content.ilike(f'%{query}%')
            )
        )
    
    # Add author search
    author_search = base_query.join(User, Post.user_id == User.id).filter(
        User.username.ilike(f'%{query}%')
    )
    
    # Add tag search
    tag_search = base_query.join(Post.tags).filter(
        Tag.name.ilike(f'%{query}%')
    )
    
    # Combine all searches
    combined_query = search_query.union(author_search).union(tag_search)
    
    # Apply sorting
    if sort_by == 'date':
        combined_query = combined_query.order_by(desc(Post.created_at))
    elif sort_by == 'popularity':
        # Sort by engagement score (reactions + comments + views)
        combined_query = combined_query.order_by(
            desc(Post.reaction_count + Post.comment_count + Post.view_count * 0.1)
        )
    else:  # relevance (default)
        # Simple relevance: title matches first, then by date
        combined_query = combined_query.order_by(
            # Title matches get higher priority
            desc(func.case(
                (Post.title.ilike(f'%{query}%'), 2),
                else_=1
            )),
            desc(Post.created_at)
        )
    
    # Execute paginated query
    try:
        pagination = combined_query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        posts = pagination.items
        
        # Format results
        results = []
        for post in posts:
            post_data = post.to_dict(
                include_content=False,  # Use excerpt for search results
                include_tags=True,
                include_files=False,
                include_author=True
            )
            
            # Add search-specific fields
            content_excerpt = get_content_excerpt(post.content, query, max_length=200)
            title_highlight = highlight_text(post.title, query)
            
            post_data.update({
                "content_excerpt": content_excerpt,
                "title_highlighted": title_highlight,
                "match_score": calculate_match_score(post, query)
            })
            
            results.append(post_data)
        
        response = {
            "results": results,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev
            },
            "query": query,
            "sort_by": sort_by,
            "search_time": datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "Search failed", "details": str(e)}), 500

@bp.route('/users', methods=['GET'])
def search_users():
    """
    Search for users by username.
    """
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 50)
    
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters long"}), 400
    
    # Search users (exclude deleted users)
    users_query = User.query.filter(
        and_(
            User.username.ilike(f'%{query}%'),
            User.is_deleted == False
        )
    ).order_by(User.username)
    
    try:
        pagination = users_query.paginate(
            page=page,
            per_page=per_page,
            error_out=False
        )
        
        users = pagination.items
        
        # Format results
        results = []
        for user in users:
            user_data = user.to_dict(include_contact=False, include_last_active=True)
            user_data["username_highlighted"] = highlight_text(user.username, query)
            results.append(user_data)
        
        response = {
            "results": results,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev
            },
            "query": query,
            "search_time": datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "User search failed", "details": str(e)}), 500

@bp.route('/tags', methods=['GET'])
def search_tags():
    """
    Search for tags by name.
    """
    query = request.args.get('q', '').strip()
    limit = min(request.args.get('limit', 20, type=int), 50)
    
    if not query or len(query) < 1:
        return jsonify({"error": "Search query required"}), 400
    
    try:
        # Search tags and include post count
        tags_query = db.session.query(
            Tag.id,
            Tag.name,
            Tag.tag_type,
            func.count(Post.id).label('post_count')
        ).outerjoin(
            Post.tags
        ).filter(
            and_(
                Tag.name.ilike(f'%{query}%'),
                or_(Post.is_deleted == False, Post.id.is_(None))
            )
        ).group_by(
            Tag.id, Tag.name, Tag.tag_type
        ).order_by(
            desc('post_count'),
            Tag.name
        ).limit(limit)
        
        results = []
        for tag_id, tag_name, tag_type, post_count in tags_query.all():
            results.append({
                "id": tag_id,
                "name": tag_name,
                "type": tag_type.name if tag_type else "unknown",
                "post_count": post_count or 0,
                "name_highlighted": highlight_text(tag_name, query)
            })
        
        response = {
            "results": results,
            "query": query,
            "search_time": datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "Tag search failed", "details": str(e)}), 500

@bp.route('/courses', methods=['GET'])
def search_courses():
    """
    Search for courses by code or name.
    """
    query = request.args.get('q', '').strip()
    limit = min(request.args.get('limit', 20, type=int), 50)
    
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters long"}), 400
    
    try:
        # Search courses
        courses_query = Course.query.filter(
            or_(
                Course.code.ilike(f'%{query}%'),
                Course.name.ilike(f'%{query}%')
            )
        ).order_by(Course.code).limit(limit)
        
        results = []
        for course in courses_query.all():
            results.append({
                "id": course.id,
                "code": course.code,
                "name": course.name,
                "credits": course.credits,
                "code_highlighted": highlight_text(course.code, query),
                "name_highlighted": highlight_text(course.name, query)
            })
        
        response = {
            "results": results,
            "query": query,
            "search_time": datetime.now(timezone.utc).isoformat()
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "Course search failed", "details": str(e)}), 500

@bp.route('/global', methods=['GET'])
def global_search():
    """
    Global search across posts, users, tags, and courses.
    Returns top results from each category.
    """
    query = request.args.get('q', '').strip()
    
    if not query or len(query) < 2:
        return jsonify({"error": "Search query must be at least 2 characters long"}), 400
    
    try:
        results = {
            "posts": [],
            "users": [],
            "tags": [],
            "courses": []
        }
        
        # Search posts (top 5)
        posts_query = Post.query.filter(
            and_(
                Post.is_deleted == False,
                or_(
                    Post.title.ilike(f'%{query}%'),
                    Post.content.ilike(f'%{query}%')
                )
            )
        ).order_by(desc(Post.created_at)).limit(5)
        
        for post in posts_query.all():
            post_data = post.to_dict(include_content=False, include_tags=True, include_author=True)
            post_data["content_excerpt"] = get_content_excerpt(post.content, query, max_length=100)
            post_data["title_highlighted"] = highlight_text(post.title, query)
            results["posts"].append(post_data)
        
        # Search users (top 5)
        users_query = User.query.filter(
            and_(
                User.username.ilike(f'%{query}%'),
                User.is_deleted == False
            )
        ).order_by(User.username).limit(5)
        
        for user in users_query.all():
            user_data = user.to_dict()
            user_data["username_highlighted"] = highlight_text(user.username, query)
            results["users"].append(user_data)
        
        # Search tags (top 5)
        tags_query = Tag.query.filter(
            Tag.name.ilike(f'%{query}%')
        ).order_by(Tag.name).limit(5)
        
        for tag in tags_query.all():
            results["tags"].append({
                "id": tag.id,
                "name": tag.name,
                "type": tag.tag_type.name if tag.tag_type else "unknown",
                "name_highlighted": highlight_text(tag.name, query)
            })
        
        # Search courses (top 5)
        courses_query = Course.query.filter(
            or_(
                Course.code.ilike(f'%{query}%'),
                Course.name.ilike(f'%{query}%')
            )
        ).order_by(Course.code).limit(5)
        
        for course in courses_query.all():
            results["courses"].append({
                "id": course.id,
                "code": course.code,
                "name": course.name,
                "code_highlighted": highlight_text(course.code, query),
                "name_highlighted": highlight_text(course.name, query)
            })
        
        response = {
            "results": results,
            "query": query,
            "search_time": datetime.now(timezone.utc).isoformat(),
            "total_results": sum(len(category) for category in results.values())
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "Global search failed", "details": str(e)}), 500

# Helper functions

def get_content_excerpt(content, query, max_length=200):
    """
    Extract relevant excerpt from content based on search query.
    """
    if not content or not query:
        return content[:max_length] + "..." if len(content) > max_length else content
    
    # Find the first occurrence of the query (case insensitive)
    query_lower = query.lower()
    content_lower = content.lower()
    
    pos = content_lower.find(query_lower)
    if pos == -1:
        # Query not found, return beginning
        return content[:max_length] + "..." if len(content) > max_length else content
    
    # Calculate excerpt boundaries
    start = max(0, pos - max_length // 3)
    end = min(len(content), start + max_length)
    
    # Adjust start if we're near the end
    if end - start < max_length and start > 0:
        start = max(0, end - max_length)
    
    excerpt = content[start:end]
    
    # Add ellipsis if needed
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt = excerpt + "..."
    
    return excerpt

def highlight_text(text, query):
    """
    Add highlighting markers around query matches in text.
    Returns text with <mark></mark> tags around matches.
    """
    if not text or not query:
        return text
    
    # Escape special regex characters in query
    escaped_query = re.escape(query)
    
    # Use regex to find and replace matches (case insensitive)
    pattern = re.compile(escaped_query, re.IGNORECASE)
    highlighted = pattern.sub(lambda m: f"<mark>{m.group()}</mark>", text)
    
    return highlighted

def calculate_match_score(post, query):
    """
    Calculate a relevance score for a post based on the search query.
    Higher score = more relevant.
    """
    score = 0
    query_lower = query.lower()
    
    # Title matches are worth more
    if query_lower in post.title.lower():
        score += 10
        # Exact title match is worth even more
        if query_lower == post.title.lower():
            score += 20
    
    # Content matches
    content_matches = post.content.lower().count(query_lower)
    score += content_matches * 2
    
    # Author username match
    if hasattr(post, 'author') and post.author and query_lower in post.author.username.lower():
        score += 5
    
    # Tag matches
    for tag in post.tags:
        if query_lower in tag.name.lower():
            score += 3
    
    # Boost score based on post popularity
    popularity_boost = (post.reaction_count * 0.5 + 
                       post.comment_count * 1 + 
                       post.view_count * 0.01)
    score += min(popularity_boost, 10)  # Cap popularity boost at 10
    
    return round(score, 2)