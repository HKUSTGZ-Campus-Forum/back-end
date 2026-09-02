import html
import re

from sqlalchemy import desc, func, or_

from app.models.comment import Comment
from app.models.course import Course
from app.models.gugu_message import GuguMessage
from app.models.post import Post
from app.models.tag import Tag


MAX_QUERY_CHARS = 160
MAX_SNIPPET_CHARS = 220

SITE_NAVIGATION = [
    {
        "title": "Forum",
        "summary": "Browse campus posts, course discussions, tags, comments, reactions, and file attachments.",
        "path": "/forum",
    },
    {
        "title": "Community",
        "summary": "Switch between forum posts, feedback collaboration, and campus activity discussions.",
        "path": "/community",
    },
    {
        "title": "Courses",
        "summary": "Search courses, inspect course details, open prerequisite maps, and read course discussions.",
        "path": "/courses",
    },
    {
        "title": "Scheduling Assistant",
        "summary": "Build a timetable from course offerings, save plans, and inspect anonymous planning interest.",
        "path": "/scheduler",
    },
    {
        "title": "Feedback",
        "summary": "Submit campus forum feedback, discuss proposals, and follow public feedback status.",
        "path": "/feedback",
    },
    {
        "title": "Team Matching",
        "summary": "Create or join campus collaboration projects and course-project teams.",
        "path": "/matching",
    },
    {
        "title": "Identity Settings",
        "summary": "Apply for and manage verified campus display identities.",
        "path": "/settings/identity",
    },
]


def build_agent_context(message, user_id=None):
    query = _normalize_query(message)
    terms = _extract_terms(query)
    sections = [
        {
            "name": "site_navigation",
            "title": "Site navigation",
            "items": SITE_NAVIGATION,
        }
    ]

    if terms:
        sections.extend(
            [
                _course_section(terms),
                _post_section(terms),
                _comment_section(terms),
                _gugu_section(terms),
                _tag_section(terms),
            ]
        )

    sections = [
        section
        for section in sections
        if section.get("name") == "site_navigation" or section.get("items")
    ]
    return {
        "query": query,
        "terms": terms,
        "sections": sections,
        "limits": {
            "max_query_chars": MAX_QUERY_CHARS,
            "max_snippet_chars": MAX_SNIPPET_CHARS,
        },
    }


def _normalize_query(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_QUERY_CHARS]


def _extract_terms(query):
    if not query:
        return []

    candidates = []
    candidates.extend(
        re.findall(r"\b[A-Za-z]{2,5}\s*\d{3,5}[A-Za-z]?\b", query)
    )
    candidates.extend(re.findall(r"[A-Za-z0-9_+\-]{2,}", query))
    if re.search(r"[\u3400-\u9fff]", query) and len(query) >= 2:
        candidates.append(query)

    terms = []
    seen = set()
    for candidate in candidates:
        term = re.sub(r"\s+", " ", candidate).strip()
        if len(term) < 2:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= 6:
            break
    return terms


def _like_conditions(columns, terms):
    conditions = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        for column in columns:
            conditions.append(column.ilike(pattern, escape="\\"))
    return conditions


def _snippet(value, limit=MAX_SNIPPET_CHARS):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _course_label(course):
    title = course.canonical_title or course.name
    code = course.display_code or course.code
    return f"{code} · {title}" if title else str(code)


def _course_path(course):
    code = course.normalized_code or course.code
    compact = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return f"/courses/{compact}" if compact else "/courses"


def _course_section(terms):
    query = Course.query.filter(Course.is_deleted == False, Course.is_active == True)
    conditions = _like_conditions(
        [
            Course.code,
            Course.normalized_code,
            Course.display_code,
            Course.canonical_title,
            Course.name,
            Course.description,
        ],
        terms,
    )
    courses = (
        query.filter(or_(*conditions))
        .order_by(Course.code.asc())
        .limit(5)
        .all()
        if conditions
        else []
    )
    return {
        "name": "courses",
        "title": "Course search results",
        "items": [
            {
                "title": _course_label(course),
                "summary": _snippet(course.description or course.course_title_abbr or ""),
                "path": _course_path(course),
            }
            for course in courses
        ],
    }


def _post_section(terms):
    query = Post.query.filter(Post.is_deleted == False)
    conditions = _like_conditions([Post.title, Post.content], terms)
    posts = (
        query.filter(or_(*conditions))
        .order_by(desc(Post.created_at), desc(Post.id))
        .limit(4)
        .all()
        if conditions
        else []
    )
    return {
        "name": "posts",
        "title": "Public forum posts",
        "items": [
            {
                "title": post.title,
                "summary": _snippet(post.content),
                "path": f"/forum/posts/{post.id}",
            }
            for post in posts
        ],
    }


def _comment_section(terms):
    conditions = _like_conditions([Comment.content], terms)
    comments = (
        Comment.query.join(Post, Comment.post_id == Post.id)
        .filter(
            Comment.is_deleted == False,
            Post.is_deleted == False,
            or_(*conditions),
        )
        .order_by(desc(Comment.created_at), desc(Comment.id))
        .limit(3)
        .all()
        if conditions
        else []
    )
    return {
        "name": "comments",
        "title": "Public comments",
        "items": [
            {
                "title": f"Comment on {comment.post.title if comment.post else 'post'}",
                "summary": _snippet(comment.content),
                "path": f"/forum/posts/{comment.post_id}",
            }
            for comment in comments
        ],
    }


def _gugu_section(terms):
    conditions = _like_conditions([GuguMessage.content], terms)
    messages = (
        GuguMessage.query.filter(GuguMessage.is_deleted == False, or_(*conditions))
        .order_by(desc(GuguMessage.created_at), desc(GuguMessage.id))
        .limit(3)
        .all()
        if conditions
        else []
    )
    return {
        "name": "gugu",
        "title": "Public Gugu wall messages",
        "items": [
            {
                "title": f"Gugu #{message.id}",
                "summary": _snippet(message.content),
                "path": "/community",
            }
            for message in messages
        ],
    }


def _tag_section(terms):
    conditions = _like_conditions([Tag.name, Tag.description], terms)
    tags = (
        Tag.query.filter(or_(*conditions))
        .outerjoin(Tag.posts)
        .group_by(Tag.id)
        .order_by(func.count(Post.id).desc(), Tag.name.asc())
        .limit(6)
        .all()
        if conditions
        else []
    )
    return {
        "name": "tags",
        "title": "Forum tags",
        "items": [
            {
                "title": tag.name,
                "summary": _snippet(tag.description),
                "path": f"/forum?tag={tag.name}",
            }
            for tag in tags
        ],
    }
