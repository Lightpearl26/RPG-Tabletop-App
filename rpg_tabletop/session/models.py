# -*- coding: utf-8 -*-

"""
Runtime state records of a session: tokens, active map state, combat turn and journal events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from rpg_tabletop.assets.map import (
    CameraState,
    InitialTokenPlacement,
    MapAsset,
    MapElement,
    Vec2,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime_to_str(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _datetime_from_str(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# =============================================================================
# Session map state
# =============================================================================


@dataclass(slots=True)
class SessionToken:
    """Runtime token state, independent from MapAsset.initial_tokens."""

    entity_id: str
    pos: Vec2 = field(default_factory=Vec2)
    size_cells: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    hidden: bool = False
    disclosed_fields: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)

    @classmethod
    def from_initial(cls, token: InitialTokenPlacement) -> SessionToken:
        return cls(
            entity_id=token.entity_id,
            pos=Vec2(token.pos.x, token.pos.y),
            size_cells=token.size_cells,
            rotation=token.rotation,
            opacity=token.opacity,
            hidden=token.hidden,
            disclosed_fields=[],
            id=token.id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "pos": self.pos.to_list(),
            "size_cells": self.size_cells,
            "rotation": self.rotation,
            "opacity": self.opacity,
            "hidden": self.hidden,
            "disclosed_fields": list(self.disclosed_fields),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionToken:
        return cls(
            entity_id=str(data["entity_id"]),
            pos=Vec2.from_value(data.get("pos", [0, 0])),
            size_cells=float(data.get("size_cells", 1.0)),
            rotation=float(data.get("rotation", 0.0)),
            opacity=float(data.get("opacity", 1.0)),
            hidden=bool(data.get("hidden", False)),
            disclosed_fields=[str(item) for item in data.get("disclosed_fields", [])],
            id=str(data.get("id") or uuid4().hex),
        )


@dataclass(slots=True)
class SessionMapState:
    """
    Differential runtime state for one map.

    The permanent map background/grid/elements remain in MapAsset.  This object
    stores only runtime tokens, runtime-added elements and the current GM camera.
    """

    map_id: str
    tokens: list[SessionToken] = field(default_factory=list)
    elements: list[MapElement] = field(default_factory=list)
    gm_camera: CameraState = field(default_factory=CameraState)

    @classmethod
    def from_asset(cls, map_id: str, asset: MapAsset) -> SessionMapState:
        return cls(
            map_id=map_id,
            tokens=[SessionToken.from_initial(token) for token in asset.initial_tokens],
            elements=[],
            gm_camera=CameraState.from_dict(asset.initial_gm_camera.to_dict()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "tokens": [token.to_dict() for token in self.tokens],
            "elements": [element.to_dict() for element in self.elements],
            "gm_camera": self.gm_camera.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMapState:
        return cls(
            map_id=str(data["map_id"]),
            tokens=[SessionToken.from_dict(item) for item in data.get("tokens", [])],
            elements=[MapElement.from_dict(item) for item in data.get("elements", [])],
            gm_camera=CameraState.from_dict(data.get("gm_camera", {})),
        )

    def token_by_id(self, token_id: str) -> Optional[SessionToken]:
        return next((token for token in self.tokens if token.id == token_id), None)

    def token_for_entity(self, entity_id: str) -> Optional[SessionToken]:
        return next((token for token in self.tokens if token.entity_id == entity_id), None)


# =============================================================================
# Combat state
# =============================================================================


@dataclass(slots=True)
class TurnState:
    """Per-turn budgets. Both resources are refreshed when a turn starts."""

    entity_id: str
    movement_max: int
    movement_remaining: int
    stamina_max: int
    stamina_remaining: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "movement_max": self.movement_max,
            "movement_remaining": self.movement_remaining,
            "stamina_max": self.stamina_max,
            "stamina_remaining": self.stamina_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnState:
        movement_max = max(0, int(data.get("movement_max", 0)))
        stamina_max = max(0, int(data.get("stamina_max", 0)))
        return cls(
            entity_id=str(data.get("entity_id", "")),
            movement_max=movement_max,
            movement_remaining=max(0, min(movement_max, int(data.get("movement_remaining", movement_max)))),
            stamina_max=stamina_max,
            stamina_remaining=max(0, min(stamina_max, int(data.get("stamina_remaining", stamina_max)))),
        )


@dataclass(slots=True)
class CombatSession:
    """Snapshotable combat state, including the currently refreshed turn budgets."""

    participant_ids: list[str] = field(default_factory=list)
    initiative_order: list[str] = field(default_factory=list)
    round: int = 1
    turn_index: int = 0
    turn_state: Optional[TurnState] = None
    # d20 initiative of each participant (empty for combats started without rolls)
    initiative_rolls: dict[str, int] = field(default_factory=dict)

    @property
    def current_entity_id(self) -> Optional[str]:
        if not self.initiative_order:
            return None
        return self.initiative_order[self.turn_index % len(self.initiative_order)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_ids": list(self.participant_ids),
            "initiative_order": list(self.initiative_order),
            "round": self.round,
            "turn_index": self.turn_index,
            "turn_state": self.turn_state.to_dict() if self.turn_state is not None else None,
            "initiative_rolls": dict(self.initiative_rolls),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatSession:
        turn_state = data.get("turn_state")
        return cls(
            participant_ids=[str(item) for item in data.get("participant_ids", [])],
            initiative_order=[str(item) for item in data.get("initiative_order", [])],
            round=max(1, int(data.get("round", 1))),
            turn_index=max(0, int(data.get("turn_index", 0))),
            turn_state=TurnState.from_dict(turn_state) if turn_state else None,
            initiative_rolls={
                str(key): int(value) for key, value in (data.get("initiative_rolls") or {}).items()
            },
        )


# =============================================================================
# Events / journal
# =============================================================================


class SessionEventType(str, Enum):
    MAP_CHANGED = "map_changed"
    TOKEN_ADDED = "token_added"
    TOKEN_MOVED = "token_moved"
    TOKEN_REMOVED = "token_removed"
    MAP_ELEMENT_ADDED = "map_element_added"
    MAP_ELEMENT_UPDATED = "map_element_updated"
    MAP_ELEMENT_REMOVED = "map_element_removed"
    CAMERA_CHANGED = "camera_changed"
    TOKEN_DISCLOSURE_CHANGED = "token_disclosure_changed"

    ENTITY_CREATED = "entity_created"
    ENTITY_REMOVED = "entity_removed"

    EFFECT_APPLIED = "effect_applied"
    EFFECT_UPDATED = "effect_updated"
    EFFECT_REMOVED = "effect_removed"
    SPELL_EVENT_ADDED = "spell_event_added"
    SPELL_EVENT_UPDATED = "spell_event_updated"
    SPELL_EVENT_REMOVED = "spell_event_removed"

    # COMBAT_SET is retained for backward compatibility with V1 sessions.
    COMBAT_SET = "combat_set"
    COMBAT_STARTED = "combat_started"
    COMBAT_ENDED = "combat_ended"
    COMBAT_PARTICIPANT_ADDED = "combat_participant_added"
    COMBAT_PARTICIPANT_REMOVED = "combat_participant_removed"
    TURN_STARTED = "turn_started"
    TURN_ENDED = "turn_ended"
    ACTION_STAMINA_SPENT = "action_stamina_spent"
    STAT_CHECK = "stat_check"
    NOTE = "note"
    ASSET_UPDATED = "asset_updated"
    # GM character sheet: stats modifier, inventory and known spells
    ENTITY_EDITED = "entity_edited"
    # Entity changes written to the campaign files at session save
    ASSETS_SAVED = "assets_saved"
    # Combat: d100 of a strike / shoot (journal only), then the resolved result
    ATTACK_ROLLED = "attack_rolled"
    ACTION_RESOLVED = "action_resolved"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """One ordered, deterministic runtime mutation and its human journal text."""

    seq: int
    timestamp: datetime
    type: SessionEventType
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    source_id: Optional[str] = None
    target_ids: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "timestamp": _datetime_to_str(self.timestamp),
            "type": self.type.value,
            "message": self.message,
            "source_id": self.source_id,
            "target_ids": list(self.target_ids),
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEvent:
        return cls(
            id=str(data.get("id") or uuid4().hex),
            seq=int(data["seq"]),
            timestamp=_datetime_from_str(str(data["timestamp"])),
            type=SessionEventType(data["type"]),
            message=str(data.get("message", "")),
            source_id=(str(data["source_id"]) if data.get("source_id") is not None else None),
            target_ids=tuple(str(item) for item in data.get("target_ids", [])),
            data=dict(data.get("data", {})),
        )
