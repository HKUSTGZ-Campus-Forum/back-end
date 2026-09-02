from datetime import datetime, timezone
import re
import time

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import cache, db
from app.models.agent_chat import AgentConversation, AgentMessage
from app.services.agent_chat_service import (
    AgentResponseError,
    AgentUnavailableError,
    agent_chat_service,
)


bp = Blueprint("agent", __name__, url_prefix="/agent")


def utcnow():
    return datetime.now(timezone.utc)


def current_user_id():
    try:
        return int(get_jwt_identity())
    except (TypeError, ValueError):
        return None


def private_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    return response


def serialize_message(message):
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def serialize_conversation(conversation, include_messages=False):
    payload = {
        "id": conversation.public_id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat()
        if conversation.created_at
        else None,
        "updated_at": conversation.updated_at.isoformat()
        if conversation.updated_at
        else None,
        "last_message_at": conversation.last_message_at.isoformat()
        if conversation.last_message_at
        else None,
        "message_count": len(conversation.messages),
    }
    if include_messages:
        payload["messages"] = [
            serialize_message(message) for message in conversation.messages
        ]
    return payload


def conversation_for_user(public_id, user_id):
    return AgentConversation.query.filter_by(
        public_id=public_id,
        user_id=user_id,
        is_deleted=False,
    ).first()


def conversation_title(content):
    compact = re.sub(r"\s+", " ", content).strip()
    if len(compact) <= 48:
        return compact
    return f"{compact[:47]}..."


def rate_limit_allows(user_id):
    limit = int(current_app.config.get("AGENT_REQUESTS_PER_MINUTE", 10))
    if limit <= 0:
        return True

    bucket = int(time.time() // 60)
    key = f"agent:rate:{user_id}:{bucket}"
    try:
        if cache.add(key, 1, timeout=70):
            count = 1
        else:
            count = cache.inc(key)
        return int(count or 0) <= limit
    except Exception:
        current_app.logger.warning("Agent rate-limit cache unavailable")
        return True


@bp.route("/status", methods=["GET"])
@jwt_required()
def get_status():
    return private_json(agent_chat_service.status())


@bp.route("/conversations", methods=["GET"])
@jwt_required()
def list_conversations():
    user_id = current_user_id()
    limit = min(50, max(1, request.args.get("limit", 30, type=int) or 30))
    conversations = (
        AgentConversation.query.filter_by(user_id=user_id, is_deleted=False)
        .order_by(AgentConversation.last_message_at.desc())
        .limit(limit)
        .all()
    )
    return private_json(
        {"conversations": [serialize_conversation(item) for item in conversations]}
    )


@bp.route("/conversations/<string:public_id>", methods=["GET"])
@jwt_required()
def get_conversation(public_id):
    conversation = conversation_for_user(public_id, current_user_id())
    if conversation is None:
        return private_json(
            {"error": "Conversation not found", "code": "conversation_not_found"},
            404,
        )
    return private_json(serialize_conversation(conversation, include_messages=True))


@bp.route("/conversations/<string:public_id>", methods=["DELETE"])
@jwt_required()
def delete_conversation(public_id):
    conversation = conversation_for_user(public_id, current_user_id())
    if conversation is None:
        return private_json(
            {"error": "Conversation not found", "code": "conversation_not_found"},
            404,
        )
    conversation.is_deleted = True
    conversation.deleted_at = utcnow()
    conversation.updated_at = utcnow()
    db.session.commit()
    response = current_app.response_class(status=204)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/chat", methods=["POST"])
@jwt_required()
def send_message():
    user_id = current_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return private_json(
            {"error": "A JSON object is required", "code": "invalid_json"},
            400,
        )

    content = str(payload.get("message") or "").strip()
    max_message_chars = int(current_app.config.get("AGENT_MAX_MESSAGE_CHARS", 4000))
    if not content:
        return private_json(
            {"error": "Message is required", "code": "message_required"},
            400,
        )
    if len(content) > max_message_chars:
        return private_json(
            {
                "error": "Message is too long",
                "code": "message_too_long",
                "max_chars": max_message_chars,
            },
            400,
        )
    if not agent_chat_service.status()["enabled"]:
        return private_json(
            {"error": "Assistant is not configured", "code": "agent_unavailable"},
            503,
        )
    if not rate_limit_allows(user_id):
        return private_json(
            {"error": "Too many assistant requests", "code": "rate_limited"},
            429,
        )

    public_id = str(payload.get("conversation_id") or "").strip()
    if public_id:
        conversation = conversation_for_user(public_id, user_id)
        if conversation is None:
            return private_json(
                {
                    "error": "Conversation not found",
                    "code": "conversation_not_found",
                },
                404,
            )
    else:
        conversation = AgentConversation(
            user_id=user_id,
            title=conversation_title(content),
        )
        db.session.add(conversation)
        db.session.flush()

    now = utcnow()
    user_message = AgentMessage(
        conversation_id=conversation.id,
        role=AgentMessage.ROLE_USER,
        content=content,
        created_at=now,
    )
    conversation.last_message_at = now
    conversation.updated_at = now
    db.session.add(user_message)
    db.session.commit()

    context_limit = max(2, int(current_app.config.get("AGENT_CONTEXT_MESSAGES", 20)))
    context = (
        AgentMessage.query.filter_by(conversation_id=conversation.id)
        .order_by(AgentMessage.id.desc())
        .limit(context_limit)
        .all()
    )
    context.reverse()

    try:
        reply = agent_chat_service.create_reply(
            [{"role": item.role, "content": item.content} for item in context]
        )
    except AgentUnavailableError:
        return private_json(
            {"error": "Assistant is not configured", "code": "agent_unavailable"},
            503,
        )
    except AgentResponseError:
        return private_json(
            {
                "error": "Assistant response failed",
                "code": "agent_request_failed",
                "conversation": serialize_conversation(conversation),
                "user_message": serialize_message(user_message),
            },
            502,
        )

    assistant_now = utcnow()
    assistant_message = AgentMessage(
        conversation_id=conversation.id,
        role=AgentMessage.ROLE_ASSISTANT,
        content=reply["content"],
        input_tokens=reply.get("input_tokens"),
        output_tokens=reply.get("output_tokens"),
        created_at=assistant_now,
    )
    conversation.last_message_at = assistant_now
    conversation.updated_at = assistant_now
    db.session.add(assistant_message)
    db.session.commit()

    return private_json(
        {
            "conversation": serialize_conversation(conversation),
            "user_message": serialize_message(user_message),
            "assistant_message": serialize_message(assistant_message),
        },
        201,
    )
