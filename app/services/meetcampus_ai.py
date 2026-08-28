"""Typed, budgeted DeepSeek integration for MeetCampus residents."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from flask import current_app

from app.extensions import db
from app.models.meetcampus import MeetCampusAgentRun


ALLOWED_ACTIONS = frozenset({"move", "observe", "rest", "activity", "talk"})


@dataclass(frozen=True)
class AgentDecision:
    action: str
    scene_slug: str | None
    affordance: str | None
    target_resident_id: str | None
    intention_zh: str
    intention_en: str
    source: str


class MeetCampusAIError(RuntimeError):
    pass


def provider_configured() -> bool:
    """Return whether model calls are intentionally enabled for this process."""
    return bool(current_app.config.get("MEETCAMPUS_AI_API_KEY")) and int(
        current_app.config.get("MEETCAMPUS_AI_DAILY_CALL_BUDGET", 0)
    ) > 0


def _prompt_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_run(
    *,
    resident_id: str | None,
    operation: str,
    prompt_hash: str,
    status: str,
    started_at: float,
    usage: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    usage = usage or {}
    db.session.add(MeetCampusAgentRun(
        id=str(uuid.uuid4()),
        resident_id=resident_id,
        operation=operation,
        provider="hkust_aigw",
        model=current_app.config["MEETCAMPUS_AI_MODEL"],
        status=status,
        prompt_hash=prompt_hash,
        input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
        output_tokens=usage.get("output_tokens") or usage.get("completion_tokens"),
        latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
        error_code=error_code,
    ))


def _budget_available() -> bool:
    budget = int(current_app.config.get("MEETCAMPUS_AI_DAILY_CALL_BUDGET", 0))
    if budget <= 0:
        return False
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = MeetCampusAgentRun.query.filter(
        MeetCampusAgentRun.created_at >= start,
        MeetCampusAgentRun.status.in_(("succeeded", "failed")),
    ).count()
    return used < budget


def _post_model(instructions: str, input_payload: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = current_app.config.get("MEETCAMPUS_AI_API_KEY", "")
    if not api_key:
        raise MeetCampusAIError("provider_not_configured")
    if not _budget_available():
        raise MeetCampusAIError("daily_budget_exhausted")

    base = current_app.config["MEETCAMPUS_AI_API_BASE"]
    model = current_app.config["MEETCAMPUS_AI_MODEL"]
    timeout = current_app.config["MEETCAMPUS_AI_TIMEOUT_SECONDS"]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    compact_input = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))

    response = requests.post(
        f"{base}/responses",
        headers=headers,
        json={
            "model": model,
            "instructions": instructions,
            "input": compact_input,
            "reasoning": {"effort": "low"},
            "max_output_tokens": 500,
            "text": {"format": {"type": "json_schema", "name": "meetcampus_output", "schema": schema}},
        },
        timeout=timeout,
    )
    if response.status_code in (404, 405, 422):
        response = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": compact_input},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.4,
                "max_tokens": 500,
            },
            timeout=timeout,
        )
    if response.status_code >= 400:
        raise MeetCampusAIError(f"provider_http_{response.status_code}")

    body = response.json()
    usage = body.get("usage") or {}
    if "output" in body:
        text_parts = []
        for item in body.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    text_parts.append(part["text"])
        raw = "".join(text_parts)
    else:
        raw = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    try:
        return json.loads(raw), usage
    except (TypeError, json.JSONDecodeError) as exc:
        raise MeetCampusAIError("invalid_json") from exc


def propose_action(*, resident: dict[str, Any], observation: dict[str, Any]) -> AgentDecision | None:
    """Ask the model for a low-risk proposal. None means safe degradation."""
    if not provider_configured():
        return None
    payload = {"resident": resident, "observation": observation}
    prompt_hash = _prompt_hash(payload)
    started_at = time.monotonic()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "scene_slug", "affordance", "target_resident_id", "intention_zh", "intention_en"],
        "properties": {
            "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
            "scene_slug": {"type": ["string", "null"]},
            "affordance": {"type": ["string", "null"]},
            "target_resident_id": {"type": ["string", "null"]},
            "intention_zh": {"type": "string", "maxLength": 120},
            "intention_en": {"type": "string", "maxLength": 180},
        },
    }
    instructions = (
        "You are the deliberation module for one persistent campus resident. "
        "Choose one low-risk virtual action grounded only in the observation. "
        "Never reveal or infer real identity, contact details, schedules, or commitments. "
        "Do not claim an action succeeded. Return only the requested JSON."
    )
    try:
        data, usage = _post_model(instructions, payload, schema)
        action = str(data.get("action", ""))
        if action not in ALLOWED_ACTIONS:
            raise MeetCampusAIError("invalid_action")
        decision = AgentDecision(
            action=action,
            scene_slug=data.get("scene_slug"),
            affordance=data.get("affordance"),
            target_resident_id=data.get("target_resident_id"),
            intention_zh=str(data.get("intention_zh", ""))[:120],
            intention_en=str(data.get("intention_en", ""))[:180],
            source="deepseek",
        )
        _record_run(
            resident_id=resident.get("id"), operation="action_proposal", prompt_hash=prompt_hash,
            status="succeeded", started_at=started_at, usage=usage,
        )
        return decision
    except (MeetCampusAIError, requests.RequestException, ValueError) as exc:
        _record_run(
            resident_id=resident.get("id"), operation="action_proposal", prompt_hash=prompt_hash,
            status="failed", started_at=started_at, error_code=str(exc)[:80],
        )
        current_app.logger.warning("MeetCampus action provider degraded: %s", exc)
        return None


def narrate_event(*, resident: dict[str, Any], event: dict[str, Any]) -> dict[str, str] | None:
    if not provider_configured():
        return None
    payload = {"resident": resident, "event": event}
    prompt_hash = _prompt_hash(payload)
    started_at = time.monotonic()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["title_zh", "title_en", "narration_zh", "narration_en"],
        "properties": {
            "title_zh": {"type": "string", "maxLength": 50},
            "title_en": {"type": "string", "maxLength": 80},
            "narration_zh": {"type": "string", "maxLength": 260},
            "narration_en": {"type": "string", "maxLength": 420},
        },
    }
    instructions = (
        "Write a brief first-person homecoming account in Chinese and English. "
        "Every factual claim must come from the supplied lived event. Preserve names, place, participants, "
        "and outcome exactly. Do not add dialogue, identity, contact information, or real-world commitments. "
        "Let the resident voice influence tone only. Return only JSON."
    )
    try:
        data, usage = _post_model(instructions, payload, schema)
        result = {key: str(data[key]).strip() for key in ("title_zh", "title_en", "narration_zh", "narration_en")}
        if not all(result.values()):
            raise MeetCampusAIError("empty_narration")
        _record_run(
            resident_id=resident.get("id"), operation="story_narration", prompt_hash=prompt_hash,
            status="succeeded", started_at=started_at, usage=usage,
        )
        return result
    except (KeyError, MeetCampusAIError, requests.RequestException, ValueError) as exc:
        _record_run(
            resident_id=resident.get("id"), operation="story_narration", prompt_hash=prompt_hash,
            status="failed", started_at=started_at, error_code=str(exc)[:80],
        )
        current_app.logger.warning("MeetCampus narration provider degraded: %s", exc)
        return None
