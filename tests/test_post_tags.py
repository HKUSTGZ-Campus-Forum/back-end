import pytest

from app.models.post import serialize_post_tag
from app.models.tag import Tag, TagType
from app.routes.post import (
    MAX_POST_TAG_COUNT,
    MAX_POST_TAG_LENGTH,
    normalize_post_tags,
)


def test_normalize_post_tags_trims_collapses_spaces_and_dedupes():
    assert normalize_post_tags([
        " instant-discussion ",
        "study   group",
        "INSTANT-DISCUSSION",
        "",
        "   ",
    ]) == ["instant-discussion", "study group"]


def test_normalize_post_tags_rejects_too_many_tags():
    with pytest.raises(ValueError, match=f"at most {MAX_POST_TAG_COUNT} tags"):
        normalize_post_tags([f"tag-{i}" for i in range(MAX_POST_TAG_COUNT + 1)])


def test_normalize_post_tags_rejects_long_tag():
    with pytest.raises(ValueError, match=f"{MAX_POST_TAG_LENGTH} characters or fewer"):
        normalize_post_tags(["x" * (MAX_POST_TAG_LENGTH + 1)])


def test_serialize_post_tag_returns_frontend_friendly_fields():
    user_tag_type = TagType(name=TagType.USER)
    tag = Tag(id=7, name="instant-discussion", tag_type=user_tag_type)

    serialized = serialize_post_tag(tag)

    assert serialized["id"] == 7
    assert serialized["tag_id"] == 7
    assert serialized["name"] == "instant-discussion"
    assert serialized["tag_name"] == "instant-discussion"
    assert serialized["tag_type"] == TagType.USER
    assert serialized["isImportant"] is False
    assert serialized["tagcolor"] == "#3498db"
