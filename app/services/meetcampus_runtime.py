"""Authoritative event-driven simulation runtime shared by every MeetCampus resident."""

from __future__ import annotations

import hashlib
import heapq
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import or_

from app.extensions import db
from app.models.meetcampus import (
    MeetCampusActivityDefinition,
    MeetCampusActivityParticipant,
    MeetCampusActivitySession,
    MeetCampusCommand,
    MeetCampusDecision,
    MeetCampusEvent,
    MeetCampusJourney,
    MeetCampusMemory,
    MeetCampusObservation,
    MeetCampusRelationship,
    MeetCampusResident,
    MeetCampusResidentPlan,
    MeetCampusResidentState,
    MeetCampusScene,
    MeetCampusSceneConnection,
    MeetCampusStory,
    MeetCampusWorld,
)
from app.services.meetcampus_ai import narrate_homecoming, select_intent


DEFAULT_NEEDS = {
    "energy": 72,
    "hunger": 28,
    "focus": 58,
    "social": 52,
    "novelty": 48,
    "stress": 24,
}
CAMPUS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _stable_int(*parts: object, modulo: int = 2_147_483_647) -> int:
    raw = ":".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:12], 16) % modulo


def _world() -> MeetCampusWorld:
    world = db.session.get(
        MeetCampusWorld,
        current_app.config.get("MEETCAMPUS_WORLD_ID", "mc-world-campus-v1"),
    )
    if world is None:
        raise RuntimeError("meetcampus_not_initialized")
    return world


def _needs(state: MeetCampusResidentState) -> dict[str, int]:
    values = dict(DEFAULT_NEEDS)
    for key, value in dict(state.needs or {}).items():
        if key in values:
            try:
                values[key] = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                pass
    return values


def _save_needs(state: MeetCampusResidentState, values: dict[str, int]) -> None:
    state.needs = {key: max(0, min(100, int(value))) for key, value in values.items()}


def _apply_effects(state: MeetCampusResidentState, effects: dict[str, Any]) -> None:
    values = _needs(state)
    for key, delta in effects.items():
        if key in values:
            try:
                values[key] += int(delta)
            except (TypeError, ValueError):
                continue
    values["hunger"] = min(100, values["hunger"] + 2)
    _save_needs(state, values)


def _scene_map_point(scene: MeetCampusScene, scenes_by_id: dict[str, MeetCampusScene]) -> dict[str, float]:
    parent = scenes_by_id.get(scene.parent_scene_id or "")
    if parent and parent.slug != "campus":
        jitter = (_stable_int(scene.id, modulo=9) - 4) * 0.7
        return {"x": max(2.0, min(98.0, parent.map_x + jitter)), "y": max(2.0, min(98.0, parent.map_y - jitter))}
    return {"x": float(scene.map_x), "y": float(scene.map_y)}


def _route(
    from_scene_id: str,
    to_scene_id: str,
    connections: list[MeetCampusSceneConnection],
    scenes_by_id: dict[str, MeetCampusScene],
) -> dict[str, Any] | None:
    if from_scene_id == to_scene_id:
        return {"scene_ids": [from_scene_id], "minutes": 0, "path": [_scene_map_point(scenes_by_id[from_scene_id], scenes_by_id)]}
    graph: dict[str, list[tuple[str, int]]] = {}
    for connection in connections:
        graph.setdefault(connection.from_scene_id, []).append((connection.to_scene_id, connection.travel_minutes))
    queue: list[tuple[int, str, list[str]]] = [(0, from_scene_id, [from_scene_id])]
    best: dict[str, int] = {from_scene_id: 0}
    while queue:
        minutes, current, route_ids = heapq.heappop(queue)
        if current == to_scene_id:
            points = [_scene_map_point(scenes_by_id[scene_id], scenes_by_id) for scene_id in route_ids]
            return {"scene_ids": route_ids, "minutes": minutes, "path": points}
        if minutes != best.get(current):
            continue
        for neighbour, edge_minutes in graph.get(current, []):
            total = minutes + edge_minutes
            if total < best.get(neighbour, 1_000_000):
                best[neighbour] = total
                heapq.heappush(queue, (total, neighbour, [*route_ids, neighbour]))
    return None


def _active_journey(resident_id: str) -> MeetCampusJourney | None:
    return MeetCampusJourney.query.filter_by(resident_id=resident_id, status="traveling").order_by(
        MeetCampusJourney.depart_at.desc()
    ).first()


def _active_participation(resident_id: str) -> tuple[MeetCampusActivityParticipant, MeetCampusActivitySession] | None:
    return db.session.query(MeetCampusActivityParticipant, MeetCampusActivitySession).join(
        MeetCampusActivitySession,
        MeetCampusActivitySession.id == MeetCampusActivityParticipant.session_id,
    ).filter(
        MeetCampusActivityParticipant.resident_id == resident_id,
        MeetCampusActivityParticipant.status.in_(("accepted", "invited")),
        MeetCampusActivitySession.status.in_(("forming", "active")),
    ).order_by(MeetCampusActivitySession.created_at.desc()).first()


def _daily_plan(resident: MeetCampusResident, now: datetime) -> MeetCampusResidentPlan:
    local_date = _aware(now).astimezone(CAMPUS_TIMEZONE).date()
    plan = MeetCampusResidentPlan.query.filter_by(resident_id=resident.id, plan_date=local_date).one_or_none()
    if plan is not None:
        return plan
    schedule = list(resident.schedule or [])
    anchors = dict((resident.persona or {}).get("ownerAnchors") or {})
    plan = MeetCampusResidentPlan(
        id=str(uuid.uuid4()),
        resident_id=resident.id,
        plan_date=local_date,
        goals=[
            {"kind": "routine", "text": "follow a sustainable campus rhythm"},
            {"kind": "owner_anchor", "preferredPlaces": list(anchors.get("preferredPlaces") or [])},
        ],
        items=[{"period": item.get("period"), "scene": item.get("scene")} for item in schedule],
        source="resident_profile",
    )
    db.session.add(plan)
    return plan


def _relevant_memories(resident: MeetCampusResident, tokens: set[str]) -> list[dict[str, Any]]:
    rows = MeetCampusMemory.query.filter_by(resident_id=resident.id).filter(
        MeetCampusMemory.superseded_by_id.is_(None)
    ).order_by(MeetCampusMemory.created_at.desc()).limit(30).all()
    scored: list[tuple[float, MeetCampusMemory]] = []
    now = utc_now()
    for index, memory in enumerate(rows):
        text = f"{memory.content_zh} {memory.content_en}".casefold()
        relevance = sum(1 for token in tokens if token and token.casefold() in text)
        age_hours = max(0.0, (now - _aware(memory.created_at)).total_seconds() / 3600)
        score = float(memory.salience) * 2 + relevance * 5 + max(0, 8 - age_hours / 12) - index * 0.05
        scored.append((score, memory))
    return [{
        "id": memory.id,
        "kind": memory.kind,
        "content_zh": memory.content_zh,
        "content_en": memory.content_en,
        "salience": memory.salience,
        "source_event_ids": list(memory.source_event_ids or []),
    } for _score, memory in sorted(scored, key=lambda item: item[0], reverse=True)[:6]]


def _incoming_invitation(resident_id: str) -> tuple[MeetCampusActivityParticipant, MeetCampusActivitySession] | None:
    return db.session.query(MeetCampusActivityParticipant, MeetCampusActivitySession).join(
        MeetCampusActivitySession,
        MeetCampusActivitySession.id == MeetCampusActivityParticipant.session_id,
    ).filter(
        MeetCampusActivityParticipant.resident_id == resident_id,
        MeetCampusActivityParticipant.status == "invited",
        MeetCampusActivitySession.status == "forming",
    ).order_by(MeetCampusActivityParticipant.invited_at).first()


def _observation_payload(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    world: MeetCampusWorld,
    now: datetime,
) -> dict[str, Any]:
    scene = db.session.get(MeetCampusScene, state.scene_id)
    nearby_rows = db.session.query(MeetCampusResident, MeetCampusResidentState).join(
        MeetCampusResidentState,
        MeetCampusResidentState.resident_id == MeetCampusResident.id,
    ).filter(
        MeetCampusResidentState.scene_id == state.scene_id,
        MeetCampusResident.id != resident.id,
        MeetCampusResident.is_active.is_(True),
    ).order_by(MeetCampusResident.id).all()
    nearby = []
    for other, other_state in nearby_rows:
        if _active_journey(other.id) is not None:
            continue
        nearby.append({
            "id": other.id,
            "name": {"zh": other.name_zh, "en": other.name_en},
            "activity": other_state.activity,
            "interests": list((other.persona or {}).get("interests") or []),
        })
    activities = MeetCampusActivityDefinition.query.filter_by(scene_id=state.scene_id, is_active=True).order_by(
        MeetCampusActivityDefinition.slug
    ).all()
    invitation = _incoming_invitation(resident.id)
    invitation_payload = None
    if invitation:
        participant, session = invitation
        definition = db.session.get(MeetCampusActivityDefinition, session.activity_definition_id)
        host = db.session.get(MeetCampusResident, session.host_resident_id)
        invitation_payload = {
            "participant_id": participant.id,
            "session_id": session.id,
            "activity": definition.slug if definition else "unknown",
            "activity_name": {"zh": definition.name_zh, "en": definition.name_en} if definition else {},
            "host": {"id": host.id, "name": {"zh": host.name_zh, "en": host.name_en}} if host else None,
            "expires_at": iso(session.ends_at),
        }
    plan = _daily_plan(resident, now)
    return {
        "observed_at": iso(now),
        "world_state_version": world.state_version,
        "current_scene": {
            "id": scene.id if scene else state.scene_id,
            "slug": scene.slug if scene else "unknown",
            "name": {"zh": scene.name_zh, "en": scene.name_en} if scene else {},
        },
        "available_activities": [{
            "id": item.id,
            "slug": item.slug,
            "name": {"zh": item.name_zh, "en": item.name_en},
            "participants": {"min": item.min_participants, "max": item.max_participants},
            "duration_minutes": [item.duration_min_minutes, item.duration_max_minutes],
            "tags": list(item.tags or []),
        } for item in activities],
        "nearby_residents": nearby,
        "incoming_invitation": invitation_payload,
        "needs": _needs(state),
        "active_goal": dict(state.active_goal or {}),
        "plan": {"goals": list(plan.goals or []), "items": list(plan.items or [])},
    }


def _candidate(
    candidate_id: str,
    kind: str,
    zh: str,
    en: str,
    *,
    base_score: float,
    **payload: Any,
) -> dict[str, Any]:
    return {"id": candidate_id, "kind": kind, "label": {"zh": zh, "en": en}, "base_score": base_score, **payload}


def _candidates(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    observation: dict[str, Any],
    scenes: list[MeetCampusScene],
    connections: list[MeetCampusSceneConnection],
    now: datetime,
) -> list[dict[str, Any]]:
    invitation = observation.get("incoming_invitation")
    if invitation:
        return [
            _candidate(f"accept:{invitation['participant_id']}", "accept_invitation", "接受邀请", "Accept the invitation", base_score=58, participant_id=invitation["participant_id"], session_id=invitation["session_id"]),
            _candidate(f"decline:{invitation['participant_id']}", "decline_invitation", "礼貌拒绝", "Decline politely", base_score=42, participant_id=invitation["participant_id"], session_id=invitation["session_id"]),
        ]
    result: list[dict[str, Any]] = []
    nearby = list(observation.get("nearby_residents") or [])
    definitions = MeetCampusActivityDefinition.query.filter_by(scene_id=state.scene_id, is_active=True).order_by(
        MeetCampusActivityDefinition.slug
    ).all()
    goal_text = str((state.active_goal or {}).get("text") or "").casefold()
    for definition in definitions:
        occupied = db.session.query(MeetCampusActivityParticipant).join(
            MeetCampusActivitySession,
            MeetCampusActivitySession.id == MeetCampusActivityParticipant.session_id,
        ).filter(
            MeetCampusActivitySession.activity_definition_id == definition.id,
            MeetCampusActivitySession.status.in_(("forming", "active")),
            MeetCampusActivityParticipant.status.in_(("invited", "accepted")),
        ).count()
        if occupied >= definition.capacity:
            continue
        command_boost = 28 if goal_text and any(token.casefold() in goal_text for token in (definition.slug, definition.name_zh, definition.name_en)) else 0
        if definition.min_participants <= 1:
            result.append(_candidate(
                f"activity:{definition.id}", "start_activity", definition.name_zh, definition.name_en,
                base_score=48 + command_boost, activity_id=definition.id, activity_slug=definition.slug,
                tags=list(definition.tags or []), effects=dict(definition.effects or {}),
            ))
        if definition.max_participants >= 2:
            for other in nearby[:5]:
                result.append(_candidate(
                    f"invite:{definition.id}:{other['id']}", "invite_activity",
                    f"邀请{other['name']['zh']}一起{definition.name_zh}",
                    f"Invite {other['name']['en']} to {definition.name_en}",
                    base_score=44 + command_boost, activity_id=definition.id, activity_slug=definition.slug,
                    target_resident_id=other["id"], tags=list(definition.tags or []),
                ))
    scenes_by_id = {scene.id: scene for scene in scenes}
    goal = dict(state.active_goal or {})
    target_id = goal.get("targetSceneId")
    destination_ids: list[str] = []
    if target_id:
        destination_ids.append(str(target_id))
    local_hour = _aware(now).astimezone(CAMPUS_TIMEZONE).hour
    period = "morning" if local_hour < 12 else "afternoon" if local_hour < 18 else "evening"
    scheduled_slug = next((item.get("scene") for item in resident.schedule or [] if item.get("period") == period), None)
    scheduled = next((scene for scene in scenes if scene.slug == scheduled_slug), None)
    if scheduled:
        destination_ids.append(scheduled.id)
    destination_ids.extend(scene.id for scene in scenes if scene.parent_scene_id and scene.id != state.scene_id)
    for destination_id in dict.fromkeys(destination_ids):
        destination = scenes_by_id.get(destination_id)
        route = _route(state.scene_id, destination_id, connections, scenes_by_id) if destination else None
        if not destination or not route or route["minutes"] <= 0:
            continue
        command_priority = bool(target_id and destination_id == str(target_id))
        result.append(_candidate(
            f"travel:{destination_id}", "travel", f"去{destination.name_zh}", f"Go to {destination.name_en}",
            base_score=92 if command_priority else 38,
            scene_id=destination.id, scene_slug=destination.slug,
            travel_minutes=route["minutes"], route_scene_ids=route["scene_ids"], path=route["path"],
            command_id=goal.get("commandId") if command_priority else None,
        ))
    result.append(_candidate("wait:observe", "wait", "先看看周围", "Observe for a while", base_score=24))
    return result


def _fallback_choice(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    candidates: list[dict[str, Any]],
    now: datetime,
) -> tuple[dict[str, Any], str, str]:
    needs = _needs(state)
    persona = dict(resident.persona or {})
    interests = " ".join(str(value).casefold() for value in persona.get("interests") or [])
    social_style = str(persona.get("social_style") or (persona.get("ownerAnchors") or {}).get("socialPace") or "balanced")
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        score = float(candidate.get("base_score", 0))
        kind = candidate.get("kind")
        tags = [str(tag).casefold() for tag in candidate.get("tags") or []]
        if any(tag in interests or interests in tag for tag in tags if tag):
            score += 14
        if kind == "start_activity":
            effects = candidate.get("effects") or {}
            if needs["energy"] < 35 and int(effects.get("energy", 0)) > 0:
                score += 22
            if needs["hunger"] > 65 and int(effects.get("hunger", 0)) < 0:
                score += 28
        if kind == "invite_activity":
            score += (needs["social"] - 50) * 0.35
            if social_style in {"slow_warmup", "quiet", "measured"}:
                score -= 9
        if kind == "accept_invitation":
            score += (needs["social"] - 50) * 0.4
            if needs["energy"] < 25:
                score -= 24
        if kind == "decline_invitation" and needs["energy"] < 25:
            score += 25
        score += _stable_int(resident.id, candidate["id"], now.strftime("%Y%m%d%H"), modulo=1000) / 1000
        scored.append((score, candidate))
    selected = max(scored, key=lambda item: item[0])[1]
    return selected, str(selected["label"]["zh"]), str(selected["label"]["en"])


def _relationship_for(a_id: str, b_id: str) -> MeetCampusRelationship:
    resident_a_id, resident_b_id = sorted((a_id, b_id))
    relationship = MeetCampusRelationship.query.filter_by(
        resident_a_id=resident_a_id, resident_b_id=resident_b_id
    ).one_or_none()
    if relationship is None:
        a = db.session.get(MeetCampusResident, resident_a_id)
        b = db.session.get(MeetCampusResident, resident_b_id)
        shared = sorted(set((a.persona or {}).get("interests") or []) & set((b.persona or {}).get("interests") or [])) if a and b else []
        relationship = MeetCampusRelationship(
            id=str(uuid.uuid4()), resident_a_id=resident_a_id, resident_b_id=resident_b_id,
            familiarity=0, trust=0, warmth=0, shared_interests=shared,
            summary_zh="还没有真正相处过", summary_en="They have not spent time together yet",
        )
        db.session.add(relationship)
    return relationship


def _add_memory(resident_id: str, event: MeetCampusEvent, zh: str, en: str, salience: int) -> None:
    db.session.add(MeetCampusMemory(
        id=str(uuid.uuid4()), resident_id=resident_id, kind="episodic",
        content_zh=zh, content_en=en, source_event_ids=[event.id], source="lived_event",
        salience=salience, confidence=1.0,
    ))


def _create_event(
    *, world: MeetCampusWorld, scene_id: str, actor_id: str, kind: str,
    summary_zh: str, summary_en: str, participant_ids: list[str], payload: dict[str, Any],
    importance: int, occurred_at: datetime, idempotency_key: str,
) -> MeetCampusEvent | None:
    if MeetCampusEvent.query.filter_by(idempotency_key=idempotency_key).one_or_none():
        return None
    event = MeetCampusEvent(
        id=str(uuid.uuid4()), world_id=world.id, scene_id=scene_id, actor_resident_id=actor_id,
        kind=kind, summary_zh=summary_zh, summary_en=summary_en,
        participant_resident_ids=list(dict.fromkeys(participant_ids)), payload=payload,
        importance=max(1, min(10, importance)), idempotency_key=idempotency_key, occurred_at=occurred_at,
    )
    db.session.add(event)
    db.session.flush()
    return event


def _complete_journeys(world: MeetCampusWorld, now: datetime) -> int:
    journeys = MeetCampusJourney.query.filter(
        MeetCampusJourney.world_id == world.id,
        MeetCampusJourney.status == "traveling",
        MeetCampusJourney.arrive_at <= now,
    ).order_by(MeetCampusJourney.arrive_at).all()
    for journey in journeys:
        state = db.session.get(MeetCampusResidentState, journey.resident_id)
        destination = db.session.get(MeetCampusScene, journey.to_scene_id)
        resident = db.session.get(MeetCampusResident, journey.resident_id)
        journey.status = "arrived"
        journey.completed_at = now
        if not state or not destination or not resident:
            continue
        state.scene_id = destination.id
        state.position_x = 50
        state.position_y = 50
        state.activity = "arrived"
        state.activity_started_at = journey.arrive_at
        state.next_decision_at = now
        event = _create_event(
            world=world, scene_id=destination.id, actor_id=resident.id, kind="arrived",
            summary_zh=f"沿着校园路线抵达了{destination.name_zh}。",
            summary_en=f"Arrived at {destination.name_en} after walking through campus.",
            participant_ids=[resident.id],
            payload={"journeyId": journey.id, "fromSceneId": journey.from_scene_id, "toSceneId": journey.to_scene_id, "departAt": iso(journey.depart_at), "arriveAt": iso(journey.arrive_at), "routeSceneIds": list(journey.route_scene_ids or [])},
            importance=3, occurred_at=journey.arrive_at, idempotency_key=f"journey:{journey.id}:arrived",
        )
        if event:
            _add_memory(resident.id, event, event.summary_zh, event.summary_en, 3)
        if journey.command_id:
            command = db.session.get(MeetCampusCommand, journey.command_id)
            if command:
                command.status = "completed"
                command.outcome = {"journeyId": journey.id, "sceneId": destination.id, "arrivedAt": iso(journey.arrive_at)}
                command.resolved_at = now
    return len(journeys)


def _activity_duration(definition: MeetCampusActivityDefinition, session_id: str) -> int:
    span = max(0, definition.duration_max_minutes - definition.duration_min_minutes)
    return definition.duration_min_minutes + _stable_int(session_id, modulo=span + 1)


def _start_session(session: MeetCampusActivitySession, definition: MeetCampusActivityDefinition, now: datetime) -> None:
    duration = _activity_duration(definition, session.id)
    session.status = "active"
    session.starts_at = now
    session.ends_at = now + timedelta(minutes=duration)
    participants = MeetCampusActivityParticipant.query.filter_by(session_id=session.id, status="accepted").all()
    for participant in participants:
        state = db.session.get(MeetCampusResidentState, participant.resident_id)
        if state:
            state.activity = f"activity:{definition.slug}"
            state.activity_started_at = now
            state.next_decision_at = session.ends_at


def _competitive_result(
    session: MeetCampusActivitySession,
    definition: MeetCampusActivityDefinition,
    resident_ids: list[str],
) -> dict[str, Any]:
    rng = random.Random(session.seed)
    skills = {}
    for resident_id in resident_ids:
        state = db.session.get(MeetCampusResidentState, resident_id)
        energy = _needs(state)["energy"] if state else 50
        skills[resident_id] = 35 + _stable_int(resident_id, (definition.outcome_rules or {}).get("skill", definition.slug), modulo=46) + (energy - 50) * 0.15
    ranked = sorted(resident_ids, key=lambda rid: skills[rid] + rng.uniform(-12, 12), reverse=True)
    if len(ranked) < 2:
        return {"kind": "completed", "participants": resident_ids}
    winner, runner_up = ranked[:2]
    losing_score = rng.randint(12, 19)
    return {
        "kind": "competitive",
        "winnerResidentId": winner,
        "score": {winner: 21, runner_up: losing_score},
        "skillDimension": (definition.outcome_rules or {}).get("skill", definition.slug),
    }


def _complete_sessions(world: MeetCampusWorld, now: datetime) -> int:
    sessions = MeetCampusActivitySession.query.filter(
        MeetCampusActivitySession.world_id == world.id,
        MeetCampusActivitySession.status == "active",
        MeetCampusActivitySession.ends_at <= now,
    ).order_by(MeetCampusActivitySession.ends_at).all()
    for session in sessions:
        definition = db.session.get(MeetCampusActivityDefinition, session.activity_definition_id)
        scene = db.session.get(MeetCampusScene, session.scene_id)
        participants = MeetCampusActivityParticipant.query.filter_by(session_id=session.id, status="accepted").order_by(
            MeetCampusActivityParticipant.invited_at
        ).all()
        resident_ids = [item.resident_id for item in participants]
        residents = {resident.id: resident for resident in MeetCampusResident.query.filter(MeetCampusResident.id.in_(resident_ids)).all()} if resident_ids else {}
        if not definition or not scene or not residents:
            session.status = "cancelled"
            session.completed_at = now
            continue
        rules = dict(definition.outcome_rules or {})
        result = _competitive_result(session, definition, resident_ids) if rules.get("kind") == "competitive" else {"kind": rules.get("kind", "completed"), "participants": resident_ids}
        session.result = result
        session.status = "completed"
        session.completed_at = now
        names_zh = "、".join(residents[rid].name_zh for rid in resident_ids)
        names_en = ", ".join(residents[rid].name_en for rid in resident_ids)
        if result.get("kind") == "competitive" and len(resident_ids) >= 2:
            winner_id = result["winnerResidentId"]
            loser_id = next(rid for rid in resident_ids if rid != winner_id)
            winner, loser = residents[winner_id], residents[loser_id]
            loser_score = result["score"][loser_id]
            summary_zh = f"{winner.name_zh}和{loser.name_zh}在{scene.name_zh}{definition.name_zh}，{winner.name_zh}以21比{loser_score}获胜。"
            summary_en = f"{winner.name_en} and {loser.name_en} did {definition.name_en} at {scene.name_en}; {winner.name_en} won 21–{loser_score}."
        elif len(resident_ids) > 1:
            summary_zh = f"{names_zh}在{scene.name_zh}一起完成了{definition.name_zh}。"
            summary_en = f"{names_en} completed {definition.name_en} together at {scene.name_en}."
        else:
            only = residents[resident_ids[0]]
            summary_zh = f"{only.name_zh}在{scene.name_zh}完成了{definition.name_zh}。"
            summary_en = f"{only.name_en} completed {definition.name_en} at {scene.name_en}."
        event = _create_event(
            world=world, scene_id=scene.id, actor_id=session.host_resident_id, kind="shared_activity" if len(resident_ids) > 1 else "activity_completed",
            summary_zh=summary_zh, summary_en=summary_en, participant_ids=resident_ids,
            payload={"activitySessionId": session.id, "activityDefinitionId": definition.id, "activity": definition.slug, "startsAt": iso(session.starts_at), "endsAt": iso(session.ends_at), "result": result, "factSource": "world_kernel"},
            importance=8 if len(resident_ids) > 1 else 4, occurred_at=session.ends_at or now,
            idempotency_key=f"activity-session:{session.id}:completed",
        )
        for participant in participants:
            participant.status = "completed"
            participant.outcome = result
            state = db.session.get(MeetCampusResidentState, participant.resident_id)
            if state:
                _apply_effects(state, dict(definition.effects or {}))
                state.activity = "reflecting_on_activity"
                state.activity_started_at = now
                state.next_decision_at = now + timedelta(minutes=3 + _stable_int(participant.resident_id, session.id, modulo=5))
            if event:
                _add_memory(participant.resident_id, event, summary_zh, summary_en, event.importance)
        if event and len(resident_ids) > 1:
            for index, resident_id in enumerate(resident_ids):
                for other_id in resident_ids[index + 1:]:
                    relationship = _relationship_for(resident_id, other_id)
                    relationship.familiarity = min(100, relationship.familiarity + 9)
                    relationship.trust = min(100, relationship.trust + 4)
                    relationship.warmth = min(100, relationship.warmth + (6 if result.get("kind") != "competitive" else 4))
                    relationship.summary_zh = f"在{scene.name_zh}一起{definition.name_zh}"
                    relationship.summary_en = f"Shared {definition.name_en} at {scene.name_en}"
                    relationship.last_event_id = event.id
    return len(sessions)


def _expire_invitations(world: MeetCampusWorld, now: datetime) -> int:
    sessions = MeetCampusActivitySession.query.filter(
        MeetCampusActivitySession.world_id == world.id,
        MeetCampusActivitySession.status == "forming",
        MeetCampusActivitySession.ends_at <= now,
    ).all()
    for session in sessions:
        session.status = "expired"
        session.completed_at = now
        participants = MeetCampusActivityParticipant.query.filter_by(session_id=session.id).all()
        for participant in participants:
            if participant.status == "invited":
                participant.status = "expired"
                participant.responded_at = now
            state = db.session.get(MeetCampusResidentState, participant.resident_id)
            if state:
                state.activity = "invitation_expired"
                state.next_decision_at = now
    return len(sessions)


def _execute_candidate(
    resident: MeetCampusResident,
    state: MeetCampusResidentState,
    candidate: dict[str, Any],
    intention: dict[str, str],
    decision: MeetCampusDecision,
    world: MeetCampusWorld,
    now: datetime,
) -> dict[str, Any]:
    kind = candidate["kind"]
    if kind == "travel":
        minutes = max(1, int(candidate["travel_minutes"]))
        journey = MeetCampusJourney(
            id=str(uuid.uuid4()), world_id=world.id, resident_id=resident.id,
            from_scene_id=state.scene_id, to_scene_id=candidate["scene_id"],
            route_scene_ids=list(candidate["route_scene_ids"]), path=list(candidate["path"]),
            status="traveling", intention=intention, decision_id=decision.id,
            command_id=candidate.get("command_id"), depart_at=now, arrive_at=now + timedelta(minutes=minutes),
        )
        db.session.add(journey)
        state.activity = "traveling"
        state.activity_started_at = now
        state.last_decision_at = now
        state.next_decision_at = journey.arrive_at
        if candidate.get("command_id"):
            command = db.session.get(MeetCampusCommand, candidate["command_id"])
            if command:
                command.status = "in_progress"
                command.outcome = {"journeyId": journey.id, "arriveAt": iso(journey.arrive_at)}
        return {"kind": "journey_started", "journeyId": journey.id, "arriveAt": iso(journey.arrive_at)}

    if kind in {"start_activity", "invite_activity"}:
        definition = db.session.get(MeetCampusActivityDefinition, candidate["activity_id"])
        if not definition or definition.scene_id != state.scene_id or not definition.is_active:
            raise ValueError("activity_unavailable")
        session = MeetCampusActivitySession(
            id=str(uuid.uuid4()), world_id=world.id, activity_definition_id=definition.id,
            scene_id=state.scene_id, host_resident_id=resident.id,
            status="forming", seed=_stable_int(resident.id, definition.id, now.isoformat()),
            intention=intention, result={}, ends_at=now + timedelta(minutes=8),
        )
        db.session.add(session)
        db.session.flush()
        db.session.add(MeetCampusActivityParticipant(
            id=str(uuid.uuid4()), session_id=session.id, resident_id=resident.id,
            role="host", status="accepted", response_reason=intention, responded_at=now,
        ))
        target_id = candidate.get("target_resident_id")
        if kind == "invite_activity" and target_id:
            target_state = db.session.get(MeetCampusResidentState, target_id)
            if not target_state or target_state.scene_id != state.scene_id or _active_journey(target_id):
                raise ValueError("invitee_unavailable")
            invitation = MeetCampusActivityParticipant(
                id=str(uuid.uuid4()), session_id=session.id, resident_id=target_id,
                role="participant", status="invited", response_reason={}, invited_at=now,
            )
            db.session.add(invitation)
            state.activity = f"waiting_for_invitation:{definition.slug}"
            state.next_decision_at = session.ends_at
            target_state.next_decision_at = now
            return {"kind": "invitation_sent", "sessionId": session.id, "targetResidentId": target_id, "expiresAt": iso(session.ends_at)}
        _start_session(session, definition, now)
        return {"kind": "activity_started", "sessionId": session.id, "endsAt": iso(session.ends_at)}

    if kind in {"accept_invitation", "decline_invitation"}:
        participant = db.session.get(MeetCampusActivityParticipant, candidate["participant_id"])
        session = db.session.get(MeetCampusActivitySession, candidate["session_id"])
        if not participant or not session or participant.resident_id != resident.id or participant.status != "invited" or session.status != "forming":
            raise ValueError("invitation_unavailable")
        participant.responded_at = now
        participant.response_reason = intention
        definition = db.session.get(MeetCampusActivityDefinition, session.activity_definition_id)
        host = db.session.get(MeetCampusResident, session.host_resident_id)
        if kind == "decline_invitation":
            participant.status = "declined"
            session.status = "cancelled"
            session.completed_at = now
            state.activity = "declined_invitation"
            state.next_decision_at = now + timedelta(minutes=5)
            host_state = db.session.get(MeetCampusResidentState, session.host_resident_id)
            if host_state:
                host_state.activity = "invitation_declined"
                host_state.next_decision_at = now + timedelta(minutes=4)
            scene = db.session.get(MeetCampusScene, session.scene_id)
            if definition and host and scene:
                event = _create_event(
                    world=world, scene_id=scene.id, actor_id=resident.id, kind="invitation_declined",
                    summary_zh=f"{resident.name_zh}礼貌拒绝了{host.name_zh}在{scene.name_zh}{definition.name_zh}的邀请。",
                    summary_en=f"{resident.name_en} politely declined {host.name_en}'s invitation to {definition.name_en} at {scene.name_en}.",
                    participant_ids=[resident.id, host.id], payload={"activitySessionId": session.id, "activity": definition.slug},
                    importance=3, occurred_at=now, idempotency_key=f"activity-session:{session.id}:declined",
                )
                if event:
                    _add_memory(resident.id, event, event.summary_zh, event.summary_en, 3)
                    _add_memory(host.id, event, event.summary_zh, event.summary_en, 3)
            return {"kind": "invitation_declined", "sessionId": session.id}
        participant.status = "accepted"
        if not definition:
            raise ValueError("activity_unavailable")
        _start_session(session, definition, now)
        return {"kind": "invitation_accepted", "sessionId": session.id, "endsAt": iso(session.ends_at)}

    state.activity = "observing"
    state.activity_started_at = now
    state.last_decision_at = now
    state.next_decision_at = now + timedelta(minutes=8 + _stable_int(resident.id, now.hour, modulo=8))
    return {"kind": "waiting", "until": iso(state.next_decision_at)}


def _advance_residents(world: MeetCampusWorld, now: datetime, max_residents: int) -> int:
    due = db.session.query(MeetCampusResident, MeetCampusResidentState).join(
        MeetCampusResidentState,
        MeetCampusResidentState.resident_id == MeetCampusResident.id,
    ).filter(
        MeetCampusResident.world_id == world.id,
        MeetCampusResident.is_active.is_(True),
        MeetCampusResidentState.next_decision_at <= now,
    ).order_by(MeetCampusResidentState.next_decision_at, MeetCampusResident.id).limit(max_residents).all()
    scenes = MeetCampusScene.query.filter_by(world_id=world.id, is_active=True).all()
    connections = MeetCampusSceneConnection.query.filter_by(world_id=world.id).all()
    advanced = 0
    for resident, state in due:
        if _active_journey(resident.id):
            continue
        participation = _active_participation(resident.id)
        if participation and participation[1].status == "active":
            continue
        pending = MeetCampusCommand.query.filter_by(resident_id=resident.id, status="pending").order_by(
            MeetCampusCommand.created_at
        ).first()
        if pending:
            state.active_goal = {"commandId": pending.id, "kind": pending.kind, "text": pending.text, **dict(pending.payload or {})}
        observation_payload = _observation_payload(resident, state, world, now)
        observation = MeetCampusObservation(
            id=str(uuid.uuid4()), resident_id=resident.id, world_state_version=world.state_version,
            payload=observation_payload, observed_at=now,
        )
        db.session.add(observation)
        candidates = _candidates(resident, state, observation_payload, scenes, connections, now)
        tokens = {str(item.get("activity_slug") or item.get("scene_slug") or "") for item in candidates}
        tokens.update(str(item["id"]) for item in observation_payload.get("nearby_residents") or [])
        memories = _relevant_memories(resident, tokens)
        model_choice = select_intent(
            resident={
                "id": resident.id,
                "name": {"zh": resident.name_zh, "en": resident.name_en},
                "persona": dict(resident.persona or {}),
                "voice": dict(resident.voice or {}),
            },
            observation=observation_payload,
            relevant_memories=memories,
            candidates=candidates,
        )
        if model_choice:
            selected = next(candidate for candidate in candidates if candidate["id"] == model_choice.candidate_id)
            intention = {"zh": model_choice.intention_zh or selected["label"]["zh"], "en": model_choice.intention_en or selected["label"]["en"]}
            source = model_choice.source
        else:
            selected, intention_zh, intention_en = _fallback_choice(resident, state, candidates, now)
            intention = {"zh": intention_zh, "en": intention_en}
            source = "resident_utility"
        decision = MeetCampusDecision(
            id=str(uuid.uuid4()), resident_id=resident.id, observation_id=observation.id,
            candidates=candidates, selected_intent={**selected, "intention": intention}, source=source,
            status="validated", validation={"accepted": True, "candidateGeneratedByWorld": True}, execution={},
            created_at=now,
        )
        db.session.add(decision)
        db.session.flush()
        try:
            execution = _execute_candidate(resident, state, selected, intention, decision, world, now)
            decision.status = "executed"
            decision.execution = execution
            decision.executed_at = now
        except ValueError as exc:
            decision.status = "rejected"
            decision.validation = {"accepted": False, "reason": str(exc)}
            state.activity = "reconsidering"
            state.next_decision_at = now + timedelta(minutes=3)
        state.last_decision_at = now
        advanced += 1
    return advanced


def advance_world(now: datetime | None = None, *, max_residents: int | None = None) -> dict[str, Any]:
    """Process due world events, then deliberate only for residents at a decision boundary."""
    if not current_app.config.get("MEETCAMPUS_WORLD_ENABLED", True):
        return {"status": "disabled", "advancedResidents": 0, "events": 0}
    now = now or utc_now()
    world = _world()
    before_events = MeetCampusEvent.query.filter_by(world_id=world.id).count()
    journeys = _complete_journeys(world, now)
    sessions = _complete_sessions(world, now)
    expired = _expire_invitations(world, now)
    limit = max_residents or int(current_app.config.get("MEETCAMPUS_MAX_DUE_RESIDENTS_PER_TICK", 8))
    residents = _advance_residents(world, now, limit)
    changed = journeys + sessions + expired + residents
    if changed:
        world.state_version += 1
    world.last_advanced_at = now
    db.session.commit()
    return {
        "status": "advanced",
        "advancedResidents": residents,
        "completedJourneys": journeys,
        "completedSessions": sessions,
        "expiredInvitations": expired,
        "events": MeetCampusEvent.query.filter_by(world_id=world.id).count() - before_events,
        "stateVersion": world.state_version,
        "lastAdvancedAt": iso(world.last_advanced_at),
    }


def compile_homecoming(owner_user_id: int, resident: MeetCampusResident) -> MeetCampusStory | None:
    """Compile unreported facts for any private-beta perspective without changing those facts."""
    stories = MeetCampusStory.query.filter_by(owner_user_id=owner_user_id, resident_id=resident.id).order_by(
        MeetCampusStory.created_at.desc()
    ).limit(30).all()
    reported = {event_id for story in stories for event_id in (story.event_ids or [])}
    events = MeetCampusEvent.query.filter(
        MeetCampusEvent.world_id == resident.world_id,
        MeetCampusEvent.importance >= 3,
        or_(
            MeetCampusEvent.actor_resident_id == resident.id,
            MeetCampusEvent.participant_resident_ids.contains([resident.id]),
        ),
    ).order_by(MeetCampusEvent.occurred_at.desc()).limit(20).all()
    pending = [event for event in reversed(events) if event.id not in reported][-5:]
    if not pending:
        return None
    facts = [{
        "id": event.id, "kind": event.kind, "summary_zh": event.summary_zh,
        "summary_en": event.summary_en, "occurred_at": iso(event.occurred_at),
        "scene_id": event.scene_id, "participant_resident_ids": list(event.participant_resident_ids or []),
        "payload": dict(event.payload or {}),
    } for event in pending]
    narration = narrate_homecoming(
        resident={"id": resident.id, "name": {"zh": resident.name_zh, "en": resident.name_en}, "persona": dict(resident.persona or {}), "voice": dict(resident.voice or {})},
        events=facts,
    )
    if narration is None:
        most_important = max(pending, key=lambda event: event.importance)
        title_zh = "我在校园里经历的新鲜事"
        title_en = "What happened while you were away"
        result = (most_important.payload or {}).get("result") or {}
        if result.get("kind") == "competitive":
            title_zh = "那场比赛，我记得很清楚"
            title_en = "I still remember that match"
        narration = {
            "title_zh": title_zh,
            "title_en": title_en,
            "narration_zh": "你不在的时候，" + "后来，".join(event.summary_zh.rstrip("。") for event in pending) + "。",
            "narration_en": "While you were away, " + " Then, ".join(event.summary_en.rstrip(".") for event in pending) + ".",
        }
    story = MeetCampusStory(
        id=str(uuid.uuid4()), owner_user_id=owner_user_id, resident_id=resident.id,
        title_zh=narration["title_zh"], title_en=narration["title_en"],
        narration_zh=narration["narration_zh"], narration_en=narration["narration_en"],
        event_ids=[event.id for event in pending],
        bridge_candidate=any(event.kind == "shared_activity" for event in pending),
        is_viewed=False,
    )
    db.session.add(story)
    db.session.commit()
    return story


def serialize_journey(journey: MeetCampusJourney | None) -> dict[str, Any] | None:
    if journey is None:
        return None
    return {
        "id": journey.id,
        "fromSceneId": journey.from_scene_id,
        "toSceneId": journey.to_scene_id,
        "routeSceneIds": list(journey.route_scene_ids or []),
        "path": list(journey.path or []),
        "status": journey.status,
        "intention": dict(journey.intention or {}),
        "departAt": iso(journey.depart_at),
        "arriveAt": iso(journey.arrive_at),
    }


def serialize_activity_state(resident_id: str) -> dict[str, Any] | None:
    participation = _active_participation(resident_id)
    if not participation:
        return None
    participant, session = participation
    definition = db.session.get(MeetCampusActivityDefinition, session.activity_definition_id)
    peers = MeetCampusActivityParticipant.query.filter_by(session_id=session.id).all()
    return {
        "sessionId": session.id,
        "status": session.status,
        "participantStatus": participant.status,
        "activityId": definition.id if definition else session.activity_definition_id,
        "activitySlug": definition.slug if definition else "unknown",
        "activityName": {"zh": definition.name_zh, "en": definition.name_en} if definition else {"zh": "活动", "en": "Activity"},
        "participantResidentIds": [item.resident_id for item in peers],
        "startsAt": iso(session.starts_at),
        "endsAt": iso(session.ends_at),
    }


def list_decision_traces(resident_id: str, limit: int = 12) -> list[dict[str, Any]]:
    decisions = MeetCampusDecision.query.filter_by(resident_id=resident_id).order_by(
        MeetCampusDecision.created_at.desc()
    ).limit(max(1, min(50, limit))).all()
    result = []
    for decision in decisions:
        observation = db.session.get(MeetCampusObservation, decision.observation_id)
        result.append({
            "id": decision.id,
            "createdAt": iso(decision.created_at),
            "source": decision.source,
            "status": decision.status,
            "observation": dict(observation.payload or {}) if observation else {},
            "candidates": list(decision.candidates or []),
            "selectedIntent": dict(decision.selected_intent or {}),
            "validation": dict(decision.validation or {}),
            "execution": dict(decision.execution or {}),
        })
    return result
