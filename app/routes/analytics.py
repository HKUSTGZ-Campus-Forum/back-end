from flask import Blueprint, request, jsonify
from app.models.post import Post
from app.models.comment import Comment
from app.models.reaction import Reaction
from app.models.user import User
from app.models.tag import Tag, TagType
from app.extensions import db
from sqlalchemy import func, desc, and_, or_
from datetime import datetime, timedelta, timezone
import json

bp = Blueprint('analytics', __name__, url_prefix='/analytics')

# Note: Analytics endpoints are made public to attract new users
# TODO: Consider adding rate limiting for security

def calculate_hot_score(post):
    """
    Calculate a hot score for posts based on engagement metrics.
    Formula: (reactions * 3 + comments * 5 + views * 0.1 + recency_boost) / age_hours^0.6
    Enhanced with stronger recency boost for posts under 6 hours old.
    """
    age_hours = max(0.1, (datetime.now(timezone.utc) - post.created_at).total_seconds() / 3600)
    
    # Weight different engagement types
    reaction_score = (post.reaction_count or 0) * 3
    comment_score = (post.comment_count or 0) * 5
    view_score = (post.view_count or 0) * 0.1
    
    # Enhanced recency boost for very recent posts
    recency_boost = 0
    if age_hours <= 1:
        recency_boost = 50  # Big boost for posts under 1 hour
    elif age_hours <= 3:
        recency_boost = 25  # Medium boost for posts under 3 hours
    elif age_hours <= 6:
        recency_boost = 10  # Small boost for posts under 6 hours
    
    # Reduced time decay factor (from 0.8 to 0.6 for stronger recency preference)
    time_factor = age_hours ** 0.6
    
    return (reaction_score + comment_score + view_score + recency_boost) / time_factor

@bp.route('/daily-summary', methods=['GET'])
def get_daily_summary():
    """
    Generate a comprehensive daily summary for social media sharing.
    Includes hot posts, engagement metrics, and trending topics.
    """
    # Parse date parameter (default to today)
    date_str = request.args.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
    else:
        target_date = datetime.now(timezone.utc).date()
    
    # Date range for the target day
    start_of_day = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_of_day = start_of_day + timedelta(days=1)
    
    # Get posts from the target day
    daily_posts = Post.query.filter(
        and_(
            Post.created_at >= start_of_day,
            Post.created_at < end_of_day,
            Post.is_deleted == False
        )
    ).all()
    
    # Calculate hot scores and get top posts
    posts_with_scores = []
    for post in daily_posts:
        score = calculate_hot_score(post)
        posts_with_scores.append((post, score))
    
    # Sort by hot score and get top posts
    posts_with_scores.sort(key=lambda x: x[1], reverse=True)
    hot_posts = posts_with_scores[:5]  # Top 5 hot posts
    
    # Get all-time hot posts (for comparison and main page)
    all_posts = Post.query.filter(Post.is_deleted == False).all()
    all_posts_with_scores = [(post, calculate_hot_score(post)) for post in all_posts]
    all_posts_with_scores.sort(key=lambda x: x[1], reverse=True)
    trending_posts = all_posts_with_scores[:10]  # Top 10 trending posts
    
    # Daily engagement metrics
    daily_comments = Comment.query.filter(
        and_(
            Comment.created_at >= start_of_day,
            Comment.created_at < end_of_day,
            Comment.is_deleted == False
        )
    ).count()
    
    daily_reactions = Reaction.query.filter(
        and_(
            Reaction.created_at >= start_of_day,
            Reaction.created_at < end_of_day
        )
    ).count()
    
    # New user registrations
    new_users = User.query.filter(
        and_(
            User.created_at >= start_of_day,
            User.created_at < end_of_day
        )
    ).count()
    
    # Total active users (posted, commented, or reacted today)
    active_user_ids = set()
    
    # Users who posted today
    for post in daily_posts:
        active_user_ids.add(post.user_id)
    
    # Users who commented today
    daily_comment_authors = db.session.query(Comment.user_id).filter(
        and_(
            Comment.created_at >= start_of_day,
            Comment.created_at < end_of_day,
            Comment.is_deleted == False
        )
    ).distinct().all()
    
    for (user_id,) in daily_comment_authors:
        active_user_ids.add(user_id)
    
    # Users who reacted today
    daily_reaction_authors = db.session.query(Reaction.user_id).filter(
        and_(
            Reaction.created_at >= start_of_day,
            Reaction.created_at < end_of_day
        )
    ).distinct().all()
    
    for (user_id,) in daily_reaction_authors:
        active_user_ids.add(user_id)
    
    # Trending topics (most used tags today)
    trending_tags = db.session.query(
        Tag.name,
        func.count(Tag.id).label('usage_count')
    ).join(
        Post.tags
    ).filter(
        and_(
            Post.created_at >= start_of_day,
            Post.created_at < end_of_day,
            Post.is_deleted == False
        )
    ).group_by(Tag.id, Tag.name).order_by(desc('usage_count')).limit(5).all()
    
    # Format hot posts for response
    def format_post_summary(post, score=None):
        # Get base post data including author information
        post_data = post.to_dict(include_content=False, include_tags=False, include_files=False)
        
        # Add summary-specific fields
        content_preview = post.content[:200] + "..." if len(post.content) > 200 else post.content
        post_data.update({
            "content": content_preview,
            "author_id": post.user_id,  # Keep legacy field name
            "hot_score": round(score, 2) if score else None,
            "tags": [{"name": tag.name, "type": tag.tag_type.name} for tag in post.tags]
        })
        
        return post_data
    
    # Generate social media friendly summary text
    summary_stats = {
        "posts_today": len(daily_posts),
        "comments_today": daily_comments,
        "reactions_today": daily_reactions,
        "new_users": new_users,
        "active_users": len(active_user_ids)
    }
    
    # Create engaging summary text for social media
    social_text = generate_social_summary(target_date, summary_stats, hot_posts, trending_tags)
    
    response = {
        "date": target_date.isoformat(),
        "summary_stats": summary_stats,
        "hot_posts_today": [format_post_summary(post, score) for post, score in hot_posts],
        "trending_posts": [format_post_summary(post, score) for post, score in trending_posts],
        "trending_topics": [{"name": tag_name, "count": count} for tag_name, count in trending_tags],
        "social_media_text": social_text,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }
    
    return jsonify(response), 200

@bp.route('/hot-posts', methods=['GET'])
def get_hot_posts():
    """
    Get current hot posts for real-time display on main page.
    """
    limit = request.args.get('limit', 10, type=int)
    time_window = request.args.get('hours', 24, type=int)  # Default 24 hours
    
    # Calculate cutoff time
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=time_window)
    
    # Get posts within time window
    recent_posts = Post.query.filter(
        and_(
            Post.created_at >= cutoff_time,
            Post.is_deleted == False
        )
    ).all()
    
    # Calculate hot scores
    posts_with_scores = []
    for post in recent_posts:
        score = calculate_hot_score(post)
        posts_with_scores.append((post, score))
    
    # Sort and limit
    posts_with_scores.sort(key=lambda x: x[1], reverse=True)
    hot_posts = posts_with_scores[:limit]
    
    # Format response
    def format_hot_post(post, score):
        # Get base post data including author information
        post_data = post.to_dict(include_content=False, include_tags=False, include_files=False)
        
        # Add hot post specific fields
        content_preview = post.content[:150] + "..." if len(post.content) > 150 else post.content
        post_data.update({
            "content": content_preview,
            "author_id": post.user_id,  # Keep legacy field name
            "hot_score": round(score, 2),
            "tags": [{"name": tag.name, "type": tag.tag_type.name} for tag in post.tags[:3]]  # Limit tags
        })
        
        return post_data
    
    response = {
        "hot_posts": [format_hot_post(post, score) for post, score in hot_posts],
        "time_window_hours": time_window,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }
    
    return jsonify(response), 200

def generate_social_summary(date, stats, hot_posts, trending_tags):
    """
    Generate engaging social media text for the daily summary.
    """
    date_str = date.strftime("%Y年%m月%d日")
    
    # Base summary
    summary_parts = [
        f"🔥 {date_str} 校园论坛日报 🔥",
        ""
    ]
    
    # Activity stats
    if stats["posts_today"] > 0:
        summary_parts.append(f"📝 今日新帖: {stats['posts_today']} 篇")
    
    if stats["comments_today"] > 0:
        summary_parts.append(f"💬 互动评论: {stats['comments_today']} 条")
    
    if stats["reactions_today"] > 0:
        summary_parts.append(f"❤️ 点赞反应: {stats['reactions_today']} 次")
    
    if stats["active_users"] > 0:
        summary_parts.append(f"👥 活跃用户: {stats['active_users']} 人")
    
    if stats["new_users"] > 0:
        summary_parts.append(f"🆕 新用户: {stats['new_users']} 人")
    
    # Hot posts highlight
    if hot_posts:
        summary_parts.extend(["", "🌟 今日热帖:"])
        for i, (post, score) in enumerate(hot_posts[:3], 1):
            title = post.title[:30] + "..." if len(post.title) > 30 else post.title
            summary_parts.append(f"{i}. {title}")
    
    # Trending topics
    if trending_tags:
        summary_parts.extend(["", "🔥 热门话题:"])
        tag_names = [f"#{tag_name}" for tag_name, _ in trending_tags[:3]]
        summary_parts.append(" ".join(tag_names))
    
    # Call to action
    summary_parts.extend([
        "",
        "💡 加入讨论，分享你的观点！",
        "🎓 #校园论坛 #学生交流 #大学生活"
    ])
    
    return "\n".join(summary_parts)