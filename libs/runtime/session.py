# -*- coding: utf-8 -*-

"""
Session/runtime state engine for RPG Tabletop App.

A Session is the persistent history of a runtime:
    - immutable ordered events (the journal),
    - periodic full snapshots of the runtime state,
    - no permanent in-memory cache for inactive maps.

Only the active map exists as a SessionMapState in SessionRuntime.  When leaving
that map, MAP_CHANGED stores its complete runtime state in the event.  Loading a
map later reconstructs it from the campaign MapAsset plus the most recent map
checkpoint and the following map-local events.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from json import dumps, loads
from math import isfinite
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable, Optional, Sequence
from uuid import UUID, uuid4

from ..assets.campaign import Campaign
from ..assets.character import Character
from ..assets.item import Inventory, Item
from ..assets.map import (
    CameraState,
    InitialTokenPlacement,
    MapAsset,
    MapElement,
    Vec2,
)
from ..assets.spell import Spell
from .entity import Entity, StatsModifier
from .spell_effect import SpellEffect
from .spell_event import SpellEvent
from ..dice import DiceCheck
from .rules import (
    DISCLOSABLE_FIELDS,
    PLAYER_STAT_NAMES,
    RESOURCE_BAR_STATS,
    SHOOT_STAMINA_COST,
    STRIKE_STAMINA_COST,
    RuntimePermissions,
    chebyshev_distance,
    grid_cell,
    movement_cells_from_agi,
    vision_cells_from_per,
)
from .live_sync import apply_asset_update, build_asset_update


SESSION_FORMAT_VERSION = 2
SESSION_FILE = "session.json"
DEFAULT_SNAPSHOT_INTERVAL = 250


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
    TURN_STARTED = "turn_started"
    TURN_ENDED = "turn_ended"
    ACTION_STAMINA_SPENT = "action_stamina_spent"
    STAT_CHECK = "stat_check"
    NOTE = "note"
    ASSET_UPDATED = "asset_updated"


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


# =============================================================================
# Entity snapshots
# =============================================================================


def _character_to_dict(character: Character) -> dict[str, Any]:
    return {
        "name": character.name,
        "stats": {
            item.name: getattr(character.stats, item.name)
            for item in fields(character.stats)
        },
    }


def _spell_to_dict(spell: Spell) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": spell.name,
        "description": spell.description,
        "cost": spell.cost,
        "targeting": spell.targeting,
        "runtime_policy": spell.runtime_policy.value,
        "effects": [
            {
                "target": ".".join(effect.target),
                "operator": effect.operator,
                "formula": effect.formula.expression,
                "effect_type": effect.effect_type.value,
            }
            for effect in spell.effects
        ],
    }
    if isinstance(spell.delay, (int, float)) and isfinite(float(spell.delay)):
        data["delay"] = spell.delay
    if spell.trigger_condition is not None:
        data["trigger_condition"] = spell.trigger_condition.expression
    return data


def entity_to_snapshot(entity: Entity) -> dict[str, Any]:
    """
    Full runtime snapshot of an entity plus the definitions required to restore it.

    Entity.to_dict() stores the mutable runtime state.  The embedded character and
    spell snapshots make a historical session independent from later edits to those
    campaign resources.
    """

    data = entity.to_dict()
    data["id"] = str(entity.id)
    data["authored_modifier"] = entity.authored_modifier.to_dict()
    data["character_snapshot"] = _character_to_dict(entity.character)
    data["spell_snapshots"] = {
        name: _spell_to_dict(spell)
        for name, spell in entity.spells.items()
    }
    data["item_snapshots"] = {}
    for item_name in entity.items.items:
        item = entity.items._item_cache.get(item_name)
        if item is None:
            try:
                item = Item.from_name(item_name)
            except FileNotFoundError:
                continue
        data["item_snapshots"][item_name] = {
            "name": item.name,
            "description": item.description,
            "modifier": item.modifier,
        }
    return data


def _restore_entities(
    snapshots: dict[str, dict[str, Any]],
    campaign: Campaign,
    *,
    clear_registry: bool = True,
) -> dict[str, Entity]:
    """Restore an entity graph while preserving shared SpellEffect instances."""

    if clear_registry:
        campaign.entities.clear()
    restored: dict[str, Entity] = {}
    effect_cache: dict[str, SpellEffect] = {}

    # Pass 1: construct/register every entity and its frozen resource definitions.
    for entity_id, raw in snapshots.items():
        character_data = raw.get("character_snapshot")
        character = (
            Character.from_dict(character_data)
            if character_data
            else Character.from_name(str(raw["character"]))
        )

        spells: dict[str, Spell] = {}
        spell_snapshots = raw.get("spell_snapshots", {})
        for spell_name in raw.get("spells", []):
            spell_data = spell_snapshots.get(spell_name)
            spell = Spell.from_dict(spell_data) if spell_data else Spell.from_name(spell_name)
            if spell is not None:
                spells[spell_name] = spell
                campaign.spells.register(spell.name, spell)

        inventory = Inventory.from_list(raw.get("items", raw.get("inventory", [])))
        for item_name, item_data in raw.get("item_snapshots", {}).items():
            inventory._item_cache[str(item_name)] = Item.from_dict(item_data)

        modifier = raw.get("stat_modifier", raw.get("stat_modifiers", {}))
        entity = Entity(
            name=str(raw["name"]),
            character=character,
            spells=spells,
            items=inventory,
            id=UUID(str(raw.get("id") or entity_id)),
            stats_modifier=StatsModifier.from_dict(modifier),
            authored_modifier=StatsModifier.from_dict(raw.get("authored_modifier", modifier)),
        )
        restored[str(entity.id)] = entity
        campaign.entities.register(str(entity.id), entity)

    # Pass 2: active effects. UUID cache keeps event.effects and target effects shared.
    for entity_id, raw in snapshots.items():
        entity = restored[str(raw.get("id") or entity_id)]
        for effect_data in raw.get("spell_effects", []):
            effect_id = str(effect_data["id"])
            effect = effect_cache.get(effect_id)
            if effect is None:
                effect = SpellEffect.from_dict(effect_data)
                effect_cache[effect_id] = effect
            entity.spell_effects.append(effect)

    # Pass 3: spell events.
    for entity_id, raw in snapshots.items():
        entity = restored[str(raw.get("id") or entity_id)]
        for event_data in raw.get("spell_events", []):
            event = SpellEvent.from_dict(event_data, effect_cache=effect_cache)
            entity.spell_events.append(event)

            if campaign.spells.get(event.spell_id) is None:
                spell_data = raw.get("spell_snapshots", {}).get(event.spell_id)
                spell = Spell.from_dict(spell_data) if spell_data else Spell.from_name(event.spell_id)
                if spell is not None:
                    campaign.spells.register(spell.name, spell)

    return restored


def _effect_cache_from_entities(entities: Iterable[Entity]) -> dict[str, SpellEffect]:
    cache: dict[str, SpellEffect] = {}
    for entity in entities:
        for effect in entity.spell_effects:
            cache[str(effect.id)] = effect
        for spell_event in entity.spell_events:
            for effect in spell_event.effects:
                cache[str(effect.id)] = effect
    return cache


# =============================================================================
# Snapshots
# =============================================================================


@dataclass(slots=True)
class SessionSnapshot:
    """Complete reconstructible runtime state at one event sequence."""

    seq: int
    timestamp: datetime
    entities: dict[str, dict[str, Any]]
    active_map_id: Optional[str]
    active_map_state: Optional[SessionMapState]
    combat: Optional[CombatSession]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": _datetime_to_str(self.timestamp),
            "entities": self.entities,
            "active_map_id": self.active_map_id,
            "active_map_state": (
                self.active_map_state.to_dict()
                if self.active_map_state is not None
                else None
            ),
            "combat": self.combat.to_dict() if self.combat is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionSnapshot:
        map_state = data.get("active_map_state")
        combat = data.get("combat")
        return cls(
            seq=int(data["seq"]),
            timestamp=_datetime_from_str(str(data["timestamp"])),
            entities={str(key): dict(value) for key, value in data.get("entities", {}).items()},
            active_map_id=(
                str(data["active_map_id"])
                if data.get("active_map_id") is not None
                else None
            ),
            active_map_state=(SessionMapState.from_dict(map_state) if map_state else None),
            combat=CombatSession.from_dict(combat) if combat else None,
        )


# =============================================================================
# Session persistence
# =============================================================================


@dataclass(slots=True)
class Session:
    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    events: list[SessionEvent] = field(default_factory=list)
    snapshots: list[SessionSnapshot] = field(default_factory=list)
    current_seq: int = 0
    snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    version: int = SESSION_FORMAT_VERSION

    @property
    def initial_snapshot(self) -> SessionSnapshot:
        if not self.snapshots:
            raise RuntimeError("The session has no initial snapshot.")
        return min(self.snapshots, key=lambda snapshot: snapshot.seq)

    def event(self, seq: int) -> Optional[SessionEvent]:
        return next((event for event in self.events if event.seq == seq), None)

    def events_between(self, start_exclusive: int, end_inclusive: int) -> list[SessionEvent]:
        return [
            event
            for event in self.events
            if start_exclusive < event.seq <= end_inclusive
        ]

    def snapshot_before_or_at(self, seq: int) -> SessionSnapshot:
        candidates = [snapshot for snapshot in self.snapshots if snapshot.seq <= seq]
        if not candidates:
            raise RuntimeError(f"No snapshot exists before event #{seq}.")
        return max(candidates, key=lambda snapshot: snapshot.seq)

    def seq_at(self, timestamp: datetime) -> int:
        """Return the last event sequence at or before a timestamp."""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        seq = 0
        for event in self.events:
            if event.timestamp <= timestamp:
                seq = event.seq
            else:
                break
        return seq

    def latest_map_checkpoint(
        self,
        map_id: str,
        before_or_at_seq: int,
    ) -> Optional[SessionEvent]:
        """Latest MAP_CHANGED event that stored the state of `map_id`."""
        for event in reversed(self.events):
            if event.seq > before_or_at_seq:
                continue
            if event.type is not SessionEventType.MAP_CHANGED:
                continue
            if event.data.get("from_map_id") == map_id and event.data.get("from_map_state"):
                return event
        return None

    def map_state_at(
        self,
        map_id: str,
        seq: int,
        campaign: Campaign,
    ) -> SessionMapState:
        """
        Reconstruct one map at a sequence without keeping inactive maps in memory.
        """
        checkpoint = self.latest_map_checkpoint(map_id, seq)
        if checkpoint is not None:
            state = SessionMapState.from_dict(checkpoint.data["from_map_state"])
            start_seq = checkpoint.seq
        else:
            asset = MapAsset.load(campaign.map_path(map_id))
            state = SessionMapState.from_asset(map_id, asset)
            start_seq = 0

        for event in self.events_between(start_seq, seq):
            _apply_map_local_event(state, event)
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "name": self.name,
            "created_at": _datetime_to_str(self.created_at),
            "updated_at": _datetime_to_str(self.updated_at),
            "current_seq": self.current_seq,
            "snapshot_interval": self.snapshot_interval,
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        version = int(data.get("version", 1))
        if version > SESSION_FORMAT_VERSION:
            raise ValueError(
                f"Session format {version} is newer than this app understands."
            )
        session = cls(
            version=version,
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name", "New Session")),
            created_at=_datetime_from_str(str(data.get("created_at") or _datetime_to_str(_utc_now()))),
            updated_at=_datetime_from_str(str(data.get("updated_at") or _datetime_to_str(_utc_now()))),
            current_seq=int(data.get("current_seq", 0)),
            snapshot_interval=max(1, int(data.get("snapshot_interval", DEFAULT_SNAPSHOT_INTERVAL))),
            snapshots=[SessionSnapshot.from_dict(item) for item in data.get("snapshots", [])],
            events=[SessionEvent.from_dict(item) for item in data.get("events", [])],
        )
        session.events.sort(key=lambda event: event.seq)
        session.snapshots.sort(key=lambda snapshot: snapshot.seq)
        return session

    def save(self, session_dir: Path) -> Path:
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / SESSION_FILE
        self.updated_at = _utc_now()

        tmp_name: Optional[str] = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                tmp_name = stream.name
                stream.write(dumps(self.to_dict(), ensure_ascii=False, indent=4))
                stream.write("\n")
            replace(tmp_name, path)
        finally:
            if tmp_name is not None:
                Path(tmp_name).unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, session_dir: Path) -> Session:
        path = Path(session_dir) / SESSION_FILE
        if not path.is_file():
            raise FileNotFoundError(f"Session file not found: {path}")
        return cls.from_dict(loads(path.read_text(encoding="utf-8")))


# =============================================================================
# Map-local replay
# =============================================================================


def _apply_map_local_event(state: SessionMapState, event: SessionEvent) -> None:
    data = event.data
    if data.get("map_id") != state.map_id:
        return

    if event.type is SessionEventType.TOKEN_ADDED:
        token = SessionToken.from_dict(data["token"])
        if state.token_by_id(token.id) is None:
            state.tokens.append(token)

    elif event.type is SessionEventType.TOKEN_MOVED:
        token = state.token_by_id(str(data["token_id"]))
        if token is not None:
            token.pos = Vec2.from_value(data["to"])

    elif event.type is SessionEventType.TOKEN_REMOVED:
        token_id = str(data["token"]["id"])
        state.tokens = [token for token in state.tokens if token.id != token_id]

    elif event.type is SessionEventType.MAP_ELEMENT_ADDED:
        element = MapElement.from_dict(data["element"])
        if not any(current.id == element.id for current in state.elements):
            state.elements.append(element)

    elif event.type is SessionEventType.MAP_ELEMENT_UPDATED:
        updated = MapElement.from_dict(data["after"])
        state.elements = [
            updated if current.id == updated.id else current
            for current in state.elements
        ]

    elif event.type is SessionEventType.MAP_ELEMENT_REMOVED:
        element_id = str(data["element"]["id"])
        state.elements = [
            element for element in state.elements if element.id != element_id
        ]

    elif event.type is SessionEventType.CAMERA_CHANGED:
        state.gm_camera = CameraState.from_dict(data["after"])

    elif event.type is SessionEventType.TOKEN_DISCLOSURE_CHANGED:
        token = state.token_by_id(str(data["token_id"]))
        if token is not None:
            token.disclosed_fields = [str(item) for item in data.get("after", [])]


# =============================================================================
# Runtime
# =============================================================================


class SessionRuntime:
    """
    Mutable live state of one Session.

    Only active_map is loaded.  All changes should enter through this object so
    that the event log remains sufficient to reconstruct the runtime.
    """

    def __init__(
        self,
        session: Session,
        campaign: Campaign,
        *,
        entities: Optional[dict[str, Entity]] = None,
        active_map_id: Optional[str] = None,
        active_map: Optional[SessionMapState] = None,
        combat: Optional[CombatSession] = None,
        seq: Optional[int] = None,
        recording: bool = True,
    ) -> None:
        self.session = session
        self.campaign = campaign
        self.entities: dict[str, Entity] = entities or {}
        self.active_map_id = active_map_id
        self.active_map = active_map
        self.combat = combat
        self.seq = session.current_seq if seq is None else seq
        self.recording = recording

        # V1 sessions did not persist per-turn budgets. When one is loaded,
        # initialize the current turn from the Entity's current AGI/Stamina.
        if (
            self.combat is not None
            and self.combat.turn_state is None
            and self.combat.current_entity_id in self.entities
        ):
            self.combat.turn_state = self._turn_state_for(self.combat.current_entity_id)

    # ------------------------------------------------------------------ setup

    @classmethod
    def start_new(
        cls,
        name: str,
        campaign: Campaign,
        *,
        entities: Optional[Iterable[Entity]] = None,
        initial_map_id: Optional[str] = None,
        snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL,
    ) -> SessionRuntime:
        """
        Start a session from the current campaign state.

        If `entities` is omitted, every entity JSON in the campaign is loaded.
        """
        if entities is None:
            loaded: list[Entity] = []
            campaign.entities.clear()
            for path in sorted(campaign.entity_path.glob("*.json")):
                raw = loads(path.read_text(encoding="utf-8"))
                entity_name = str(raw.get("name") or path.stem)
                entity = Entity.from_name(entity_name)
                if entity:
                    loaded.append(entity)
            entities = loaded

        entity_dict = {str(entity.id): entity for entity in entities}
        campaign.entities.clear()
        for entity_id, entity in entity_dict.items():
            campaign.entities.register(entity_id, entity)
            for spell in entity.spells.values():
                campaign.spells.register(spell.name, spell)

        active_map = None
        if initial_map_id is not None:
            asset = MapAsset.load(campaign.map_path(initial_map_id))
            active_map = SessionMapState.from_asset(initial_map_id, asset)

        session = Session(
            name=name,
            snapshot_interval=max(1, int(snapshot_interval)),
        )
        runtime = cls(
            session,
            campaign,
            entities=entity_dict,
            active_map_id=initial_map_id,
            active_map=active_map,
            combat=None,
            seq=0,
        )
        session.snapshots.append(runtime._capture_snapshot(seq=0))
        return runtime

    @classmethod
    def load(cls, session: Session, campaign: Campaign) -> SessionRuntime:
        return cls.reconstruct(session, campaign, session.current_seq, recording=True)

    @classmethod
    def reconstruct(
        cls,
        session: Session,
        campaign: Campaign,
        seq: int,
        *,
        recording: bool = False,
    ) -> SessionRuntime:
        """Rebuild a runtime at any historical sequence."""
        seq = max(0, min(int(seq), session.current_seq))
        snapshot = session.snapshot_before_or_at(seq)
        entities = _restore_entities(snapshot.entities, campaign)
        active_map = (
            SessionMapState.from_dict(snapshot.active_map_state.to_dict())
            if snapshot.active_map_state is not None
            else None
        )
        combat = (
            CombatSession.from_dict(snapshot.combat.to_dict())
            if snapshot.combat is not None
            else None
        )

        runtime = cls(
            session,
            campaign,
            entities=entities,
            active_map_id=snapshot.active_map_id,
            active_map=active_map,
            combat=combat,
            seq=snapshot.seq,
            recording=recording,
        )
        for event in session.events_between(snapshot.seq, seq):
            runtime._apply_event(event)
            runtime.seq = event.seq
        return runtime

    @classmethod
    def reconstruct_at(
        cls,
        session: Session,
        campaign: Campaign,
        timestamp: datetime,
    ) -> SessionRuntime:
        return cls.reconstruct(session, campaign, session.seq_at(timestamp), recording=False)

    # --------------------------------------------------------------- snapshots

    def _capture_snapshot(self, *, seq: Optional[int] = None) -> SessionSnapshot:
        return SessionSnapshot(
            seq=self.seq if seq is None else seq,
            timestamp=_utc_now(),
            entities={
                entity_id: entity_to_snapshot(entity)
                for entity_id, entity in self.entities.items()
            },
            active_map_id=self.active_map_id,
            active_map_state=(
                SessionMapState.from_dict(self.active_map.to_dict())
                if self.active_map is not None
                else None
            ),
            combat=(
                CombatSession.from_dict(self.combat.to_dict())
                if self.combat is not None
                else None
            ),
        )

    def create_snapshot(self, *, force: bool = False) -> SessionSnapshot:
        existing = next(
            (snapshot for snapshot in self.session.snapshots if snapshot.seq == self.seq),
            None,
        )
        if existing is not None:
            return existing

        if not force and self.seq % self.session.snapshot_interval != 0:
            raise ValueError("Current sequence is not a snapshot boundary.")

        snapshot = self._capture_snapshot()
        self.session.snapshots.append(snapshot)
        self.session.snapshots.sort(key=lambda item: item.seq)
        return snapshot

    # ----------------------------------------------------------------- events

    def _dispatch(
        self,
        event_type: SessionEventType,
        data: Optional[dict[str, Any]] = None,
        *,
        message: str = "",
        source_id: Optional[str] = None,
        target_ids: Iterable[str] = (),
    ) -> SessionEvent:
        if not self.recording:
            raise RuntimeError("Historical runtimes are read-only.")
        if self.seq != self.session.current_seq:
            raise RuntimeError(
                "Cannot append events from a historical runtime. Restore/branch explicitly first."
            )

        next_seq = self.session.current_seq + 1
        event = SessionEvent(
            seq=next_seq,
            timestamp=_utc_now(),
            type=event_type,
            data=dict(data or {}),
            message=message,
            source_id=source_id,
            target_ids=tuple(str(item) for item in target_ids),
        )
        self._apply_event(event)
        self.session.events.append(event)
        self.session.current_seq = next_seq
        self.session.updated_at = event.timestamp
        self.seq = next_seq

        if self.seq % self.session.snapshot_interval == 0:
            self.create_snapshot(force=True)
        return event

    def _apply_event(self, event: SessionEvent) -> None:
        data = event.data

        if event.type is SessionEventType.MAP_CHANGED:
            to_map_id = data.get("to_map_id")
            self.active_map_id = str(to_map_id) if to_map_id is not None else None
            self.active_map = (
                self.session.map_state_at(self.active_map_id, event.seq - 1, self.campaign)
                if self.active_map_id is not None
                else None
            )
            return

        if event.type in {
            SessionEventType.TOKEN_ADDED,
            SessionEventType.TOKEN_MOVED,
            SessionEventType.TOKEN_REMOVED,
            SessionEventType.MAP_ELEMENT_ADDED,
            SessionEventType.MAP_ELEMENT_UPDATED,
            SessionEventType.MAP_ELEMENT_REMOVED,
            SessionEventType.CAMERA_CHANGED,
            SessionEventType.TOKEN_DISCLOSURE_CHANGED,
        }:
            if self.active_map is not None:
                _apply_map_local_event(self.active_map, event)
            if (
                event.type is SessionEventType.TOKEN_MOVED
                and data.get("player_move")
                and self.combat is not None
                and self.combat.turn_state is not None
                and data.get("movement_after") is not None
            ):
                self.combat.turn_state.movement_remaining = max(
                    0, int(data["movement_after"])
                )
            return

        if event.type is SessionEventType.ASSET_UPDATED:
            apply_asset_update(self, data)
            return

        if event.type is SessionEventType.ENTITY_CREATED:
            raw = dict(data["entity"])
            restored = _restore_entities(
                {str(raw["id"]): raw},
                self.campaign,
                clear_registry=False,
            )
            created = next(iter(restored.values()))
            self.entities[str(created.id)] = created
            for entity_id, entity in self.entities.items():
                self.campaign.entities.register(entity_id, entity)
            return

        if event.type is SessionEventType.ENTITY_REMOVED:
            entity_id = str(data["entity_id"])
            self.entities.pop(entity_id, None)
            self.campaign.entities.unregister(entity_id)
            return

        if event.type in {
            SessionEventType.EFFECT_APPLIED,
            SessionEventType.EFFECT_UPDATED,
            SessionEventType.EFFECT_REMOVED,
            SessionEventType.SPELL_EVENT_ADDED,
            SessionEventType.SPELL_EVENT_UPDATED,
            SessionEventType.SPELL_EVENT_REMOVED,
        }:
            self._apply_entity_runtime_event(event)
            return

        if event.type in {
            SessionEventType.COMBAT_SET,
            SessionEventType.COMBAT_STARTED,
            SessionEventType.TURN_STARTED,
        }:
            combat = data.get("combat")
            self.combat = CombatSession.from_dict(combat) if combat else None
            return

        if event.type is SessionEventType.COMBAT_ENDED:
            self.combat = None
            return

        if event.type is SessionEventType.ACTION_STAMINA_SPENT:
            if self.combat is not None and self.combat.turn_state is not None:
                self.combat.turn_state.stamina_remaining = max(
                    0, int(data.get("after", self.combat.turn_state.stamina_remaining))
                )
            return

        # A player TOKEN_MOVED event also stores the post-move turn budget.
        if event.type is SessionEventType.TURN_ENDED:
            return

        # STAT_CHECK and NOTE deliberately have no state mutation.

    def _apply_entity_runtime_event(self, event: SessionEvent) -> None:
        data = event.data
        effect_cache = _effect_cache_from_entities(self.entities.values())

        if event.type is SessionEventType.EFFECT_APPLIED:
            raw = data["effect"]
            target = self.entities.get(str(raw["target_id"]))
            if target is None:
                return
            effect_id = str(raw["id"])
            if any(str(effect.id) == effect_id for effect in target.spell_effects):
                return
            effect = effect_cache.get(effect_id)
            if effect is None:
                effect = SpellEffect.from_dict(raw)
            target.spell_effects.append(effect)
            return

        if event.type is SessionEventType.EFFECT_UPDATED:
            raw = data["effect"]
            effect_id = str(raw["id"])
            updated = SpellEffect.from_dict(raw)
            current = effect_cache.get(effect_id)
            if current is None:
                return
            current.effect_type = updated.effect_type
            current.effect_def = updated.effect_def
            current.target_id = updated.target_id
            current.target_stat = updated.target_stat
            current.delta = updated.delta
            current.duration = updated.duration
            current.event_id = updated.event_id
            return

        if event.type is SessionEventType.EFFECT_REMOVED:
            effect_id = str(data["effect_id"])
            for entity in self.entities.values():
                entity.spell_effects = [
                    effect for effect in entity.spell_effects
                    if str(effect.id) != effect_id
                ]
                for spell_event in entity.spell_events:
                    spell_event.effects = [
                        effect for effect in spell_event.effects
                        if str(effect.id) != effect_id
                    ]
            return

        caster_id = str(data["caster_id"])
        caster = self.entities.get(caster_id)
        if caster is None:
            return

        if event.type is SessionEventType.SPELL_EVENT_ADDED:
            raw = data["spell_event"]
            spell_event = SpellEvent.from_dict(raw, effect_cache=effect_cache)
            if not any(existing.id == spell_event.id for existing in caster.spell_events):
                caster.spell_events.append(spell_event)
            return

        if event.type is SessionEventType.SPELL_EVENT_UPDATED:
            raw = data["spell_event"]
            updated = SpellEvent.from_dict(raw, effect_cache=effect_cache)
            for index, existing in enumerate(caster.spell_events):
                if existing.id == updated.id:
                    caster.spell_events[index] = updated
                    break
            return

        if event.type is SessionEventType.SPELL_EVENT_REMOVED:
            spell_event_id = str(data["spell_event_id"])
            caster.spell_events = [
                spell_event
                for spell_event in caster.spell_events
                if str(spell_event.id) != spell_event_id
            ]

    # ------------------------------------------------------------- player rules

    def player_permissions(self, entity_id: str) -> RuntimePermissions:
        """Return capabilities for a player-controlled entity in the current context."""
        entity_id = str(entity_id)
        if entity_id not in self.entities:
            raise KeyError(f"Unknown entity: {entity_id}")

        if self.combat is None:
            return RuntimePermissions(
                can_move=True,
                movement_limited=False,
                can_inspect_visible_tokens=True,
                can_stat_check=True,
                can_combat_actions=False,
            )

        is_turn = self.combat.current_entity_id == entity_id
        return RuntimePermissions(
            can_move=is_turn,
            movement_limited=is_turn,
            can_inspect_visible_tokens=True,
            can_stat_check=True,
            can_combat_actions=is_turn,
        )

    def vision_radius_cells(self, entity_id: str) -> int:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        return vision_cells_from_per(entity.per)

    def _active_map_asset(self) -> MapAsset:
        if self.active_map_id is None:
            raise RuntimeError("No map is currently active.")
        return MapAsset.load(self.campaign.map_path(self.active_map_id))

    def _grid_cell_for_pos(self, pos: Vec2) -> tuple[int, int]:
        asset = self._active_map_asset()
        grid = asset.grid
        if not grid.enabled or grid.cell_size <= 0:
            raise RuntimeError("Player movement/vision requires an enabled map grid.")
        return grid_cell(
            pos,
            cell_size=grid.cell_size,
            offset_x=grid.offset_x,
            offset_y=grid.offset_y,
        )

    def visible_tokens_for(self, observer_entity_id: str) -> list[SessionToken]:
        """Tokens visible inside the PER radius. Hidden tokens stay GM-only."""
        state = self._require_active_map()
        observer_entity_id = str(observer_entity_id)
        observer = state.token_for_entity(observer_entity_id)
        if observer is None:
            return []

        origin = self._grid_cell_for_pos(observer.pos)
        radius = self.vision_radius_cells(observer_entity_id)
        visible: list[SessionToken] = []
        for token in state.tokens:
            if token.entity_id == observer_entity_id:
                visible.append(token)
                continue
            if token.hidden:
                continue
            if chebyshev_distance(origin, self._grid_cell_for_pos(token.pos)) <= radius:
                visible.append(token)
        return visible

    def can_inspect_token(self, observer_entity_id: str, token_id: str) -> bool:
        return any(
            token.id == str(token_id)
            for token in self.visible_tokens_for(str(observer_entity_id))
        )

    def public_entity_view(
        self,
        observer_entity_id: str,
        token_id: str,
    ) -> dict[str, Any]:
        """Build the short right-click sheet using only fields disclosed by the GM."""
        state = self._require_active_map()
        token = state.token_by_id(str(token_id))
        if token is None:
            raise KeyError(f"Unknown token: {token_id}")
        if not self.can_inspect_token(observer_entity_id, token.id):
            raise PermissionError("This token is not visible to the player.")

        entity = self.entities.get(token.entity_id)
        if entity is None:
            raise KeyError(f"Unknown entity: {token.entity_id}")

        # An entity bound to the observer is "shared" with them: every field a
        # player may know is visible without any GM disclosure. Private stats
        # are never part of DISCLOSABLE_FIELDS, so they can never leak here.
        if token.entity_id == str(observer_entity_id):
            names: Sequence[str] = DISCLOSABLE_FIELDS
        else:
            names = [name for name in token.disclosed_fields if name in DISCLOSABLE_FIELDS]

        result: dict[str, Any] = {"entity_id": token.entity_id, "token_id": token.id}
        for name in names:
            if name == "name":
                result["name"] = entity.name
            elif name in PLAYER_STAT_NAMES:
                result[name] = entity.get_stat(name)
                if name in RESOURCE_BAR_STATS:
                    result[f"{name}_max"] = entity.get_stat_max(name)
        return result

    def stat_check(self, entity_id: str, stat: str) -> SessionEvent:
        """Roll a free stat test. It is allowed in and out of combat, on or off turn."""
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        if not hasattr(entity.character.stats, stat):
            raise ValueError(f"Unknown stat: {stat}")

        check = DiceCheck.resolve(entity, stat)
        return self._dispatch(
            SessionEventType.STAT_CHECK,
            {
                "stat": stat,
                "stat_value": entity.get_stat(stat),
                "dice": check.dice.cmd,
                "values": list(check.dice.dice_values),
                "total": check.dice.total,
                "success": check.success,
                "critical_success": check.dice.critical_success,
                "critical_failure": check.dice.critical_failure,
            },
            message=(
                f"{entity.name} tests {stat.upper()}: {check.dice.total} "
                f"({'success' if check.success else 'failure'})"
            ),
            source_id=str(entity_id),
            target_ids=(str(entity_id),),
        )

    # --------------------------------------------------------------- map API

    def change_map(self, map_id: Optional[str]) -> SessionEvent:
        if map_id == self.active_map_id:
            raise ValueError("This map is already active.")
        if map_id is not None:
            # Validate before recording the event.
            MapAsset.load(self.campaign.map_path(map_id))

        from_state = self.active_map.to_dict() if self.active_map is not None else None
        return self._dispatch(
            SessionEventType.MAP_CHANGED,
            {
                "from_map_id": self.active_map_id,
                "to_map_id": map_id,
                "from_map_state": from_state,
            },
            message=(
                f"Map changed: {self.active_map_id or 'none'} -> {map_id or 'none'}"
            ),
        )

    def _require_active_map(self) -> SessionMapState:
        if self.active_map is None or self.active_map_id is None:
            raise RuntimeError("No map is currently active.")
        return self.active_map

    def add_token(self, token: SessionToken) -> SessionEvent:
        state = self._require_active_map()
        if state.token_by_id(token.id) is not None:
            raise ValueError(f"Token {token.id!r} already exists on this map.")
        return self._dispatch(
            SessionEventType.TOKEN_ADDED,
            {"map_id": state.map_id, "token": token.to_dict()},
            message=f"Token added for entity {token.entity_id}",
            target_ids=(token.entity_id,),
        )

    def move_token(self, token_id: str, pos: Vec2) -> SessionEvent:
        state = self._require_active_map()
        token = state.token_by_id(token_id)
        if token is None:
            raise KeyError(f"Unknown token: {token_id}")
        before = token.pos.to_list()
        return self._dispatch(
            SessionEventType.TOKEN_MOVED,
            {
                "map_id": state.map_id,
                "token_id": token.id,
                "entity_id": token.entity_id,
                "from": before,
                "to": pos.to_list(),
            },
            message=f"Token moved for entity {token.entity_id}",
            target_ids=(token.entity_id,),
        )

    def move_player_token(self, entity_id: str, pos: Vec2) -> SessionEvent:
        """Player movement with turn ownership and Chebyshev movement-budget validation."""
        entity_id = str(entity_id)
        permissions = self.player_permissions(entity_id)
        if not permissions.can_move:
            raise PermissionError("The player cannot move outside their combat turn.")

        state = self._require_active_map()
        token = state.token_for_entity(entity_id)
        if token is None:
            raise KeyError(f"No token for entity {entity_id!r} on the active map.")

        asset = self._active_map_asset()
        target = asset.grid.cell_center(pos) if asset.grid.enabled else Vec2(pos.x, pos.y)
        from_cell = self._grid_cell_for_pos(token.pos)
        to_cell = self._grid_cell_for_pos(target)
        cost = chebyshev_distance(from_cell, to_cell)
        if cost <= 0:
            raise ValueError("The token is already in that grid cell.")

        movement_before: Optional[int] = None
        movement_after: Optional[int] = None
        if permissions.movement_limited:
            if self.combat is None or self.combat.turn_state is None:
                raise RuntimeError("Combat turn state is not initialized.")
            turn = self.combat.turn_state
            if turn.entity_id != entity_id:
                raise PermissionError("This is not the player's combat turn.")
            movement_before = turn.movement_remaining
            if cost > movement_before:
                raise ValueError(
                    f"Not enough movement: {cost} cells required, "
                    f"{movement_before} remaining."
                )
            movement_after = movement_before - cost

        return self._dispatch(
            SessionEventType.TOKEN_MOVED,
            {
                "map_id": state.map_id,
                "token_id": token.id,
                "entity_id": token.entity_id,
                "from": token.pos.to_list(),
                "to": target.to_list(),
                "player_move": True,
                "distance_cells": cost,
                "movement_before": movement_before,
                "movement_after": movement_after,
            },
            message=f"{self.entities[entity_id].name} moves {cost} cell(s)",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def set_token_disclosure(
        self,
        token_id: str,
        token_fields: Sequence[str],
    ) -> SessionEvent:
        state = self._require_active_map()
        token = state.token_by_id(str(token_id))
        if token is None:
            raise KeyError(f"Unknown token: {token_id}")
        allowed = set(DISCLOSABLE_FIELDS)
        normalized = []
        for item in token_fields:
            name = str(item).strip().lower()
            if name and name not in normalized:
                if name not in allowed:
                    raise ValueError(f"Unsupported disclosed field: {name}")
                normalized.append(name)
        return self._dispatch(
            SessionEventType.TOKEN_DISCLOSURE_CHANGED,
            {
                "map_id": state.map_id,
                "token_id": token.id,
                "before": list(token.disclosed_fields),
                "after": normalized,
            },
            message=f"Public token information changed for {token.entity_id}",
            target_ids=(token.entity_id,),
        )

    def remove_token(self, token_id: str) -> SessionEvent:
        state = self._require_active_map()
        token = state.token_by_id(token_id)
        if token is None:
            raise KeyError(f"Unknown token: {token_id}")
        return self._dispatch(
            SessionEventType.TOKEN_REMOVED,
            {"map_id": state.map_id, "token": token.to_dict()},
            message=f"Token removed for entity {token.entity_id}",
            target_ids=(token.entity_id,),
        )

    def add_map_element(self, element: MapElement) -> SessionEvent:
        state = self._require_active_map()
        if any(current.id == element.id for current in state.elements):
            raise ValueError(f"Map element {element.id!r} already exists.")
        return self._dispatch(
            SessionEventType.MAP_ELEMENT_ADDED,
            {"map_id": state.map_id, "element": element.to_dict()},
            message="Runtime map element added",
        )

    def update_map_element(self, element: MapElement) -> SessionEvent:
        state = self._require_active_map()
        current = next((item for item in state.elements if item.id == element.id), None)
        if current is None:
            raise KeyError(f"Unknown runtime map element: {element.id}")
        return self._dispatch(
            SessionEventType.MAP_ELEMENT_UPDATED,
            {
                "map_id": state.map_id,
                "before": current.to_dict(),
                "after": element.to_dict(),
            },
            message="Runtime map element updated",
        )

    def remove_map_element(self, element_id: str) -> SessionEvent:
        state = self._require_active_map()
        element = next((item for item in state.elements if item.id == element_id), None)
        if element is None:
            raise KeyError(f"Unknown runtime map element: {element_id}")
        return self._dispatch(
            SessionEventType.MAP_ELEMENT_REMOVED,
            {"map_id": state.map_id, "element": element.to_dict()},
            message="Runtime map element removed",
        )

    def set_gm_camera(self, camera: CameraState) -> SessionEvent:
        state = self._require_active_map()
        return self._dispatch(
            SessionEventType.CAMERA_CHANGED,
            {
                "map_id": state.map_id,
                "before": state.gm_camera.to_dict(),
                "after": camera.to_dict(),
            },
            message="GM camera changed",
        )

    # ------------------------------------------------------------ entity API

    def add_entity(self, entity: Entity) -> SessionEvent:
        entity_id = str(entity.id)
        if entity_id in self.entities:
            raise ValueError(f"Entity {entity_id!r} is already in the session.")
        return self._dispatch(
            SessionEventType.ENTITY_CREATED,
            {"entity": entity_to_snapshot(entity)},
            message=f"Entity added: {entity.name}",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def remove_entity(self, entity_id: str) -> SessionEvent:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        return self._dispatch(
            SessionEventType.ENTITY_REMOVED,
            {
                "entity_id": str(entity_id),
                "entity": entity_to_snapshot(entity),
            },
            message=f"Entity removed: {entity.name}",
            source_id=str(entity_id),
            target_ids=(str(entity_id),),
        )

    def sync_asset(self, kind: str, name: str) -> Optional[SessionEvent]:
        """Record the saved campaign asset `name` (kind: character, item, spell, entity)."""
        update = build_asset_update(self, kind, name)
        if update is None:
            return None
        return self._dispatch(
            SessionEventType.ASSET_UPDATED,
            update.data,
            message=update.message,
            target_ids=update.entity_ids,
        )

    def apply_resolved_effect(
        self,
        effect: SpellEffect,
        *,
        source_id: Optional[str] = None,
        origin: Optional[dict[str, Any]] = None,
        message: str = "",
    ) -> SessionEvent:
        """
        Apply an already-resolved SpellEffect.

        The effect delta must already be final. Replay never rerolls dice or
        reevaluates a spell formula.
        """
        if effect.target_id not in self.entities:
            raise KeyError(f"Unknown target entity: {effect.target_id}")
        return self._dispatch(
            SessionEventType.EFFECT_APPLIED,
            {
                "effect": effect.to_dict(),
                "origin": dict(origin or {}),
            },
            message=message,
            source_id=source_id,
            target_ids=(effect.target_id,),
        )

    def update_effect(self, effect: SpellEffect, *, message: str = "") -> SessionEvent:
        return self._dispatch(
            SessionEventType.EFFECT_UPDATED,
            {"effect": effect.to_dict()},
            message=message,
            target_ids=(effect.target_id,),
        )

    def remove_effect(
        self,
        effect_id: str,
        *,
        target_id: Optional[str] = None,
        message: str = "",
    ) -> SessionEvent:
        return self._dispatch(
            SessionEventType.EFFECT_REMOVED,
            {"effect_id": effect_id, "target_id": target_id},
            message=message,
            target_ids=((target_id,) if target_id is not None else ()),
        )

    def add_spell_event(self, caster_id: str, spell_event: SpellEvent) -> SessionEvent:
        return self._dispatch(
            SessionEventType.SPELL_EVENT_ADDED,
            {"caster_id": caster_id, "spell_event": spell_event.to_dict()},
            source_id=caster_id,
            target_ids=spell_event.targets_id,
            message=f"Spell event started: {spell_event.spell_id}",
        )

    def update_spell_event(self, caster_id: str, spell_event: SpellEvent) -> SessionEvent:
        return self._dispatch(
            SessionEventType.SPELL_EVENT_UPDATED,
            {"caster_id": caster_id, "spell_event": spell_event.to_dict()},
            source_id=caster_id,
            target_ids=spell_event.targets_id,
            message=f"Spell event updated: {spell_event.spell_id}",
        )

    def remove_spell_event(self, caster_id: str, spell_event_id: str) -> SessionEvent:
        return self._dispatch(
            SessionEventType.SPELL_EVENT_REMOVED,
            {"caster_id": caster_id, "spell_event_id": spell_event_id},
            source_id=caster_id,
            message="Spell event removed",
        )

    # ------------------------------------------------------------- combat/log

    def _turn_state_for(self, entity_id: str) -> TurnState:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        movement = movement_cells_from_agi(entity.agi)
        stamina = max(0, int(entity.stamina))
        return TurnState(
            entity_id=str(entity_id),
            movement_max=movement,
            movement_remaining=movement,
            stamina_max=stamina,
            stamina_remaining=stamina,
        )

    def start_combat(
        self,
        participant_ids: Sequence[str],
        *,
        initiative_order: Optional[Sequence[str]] = None,
    ) -> SessionEvent:
        if self.combat is not None:
            raise RuntimeError("A combat is already active.")
        participants = [str(item) for item in participant_ids]
        if not participants:
            raise ValueError("Combat requires at least one participant.")
        if len(set(participants)) != len(participants):
            raise ValueError("Combat participants must be unique.")
        unknown = [item for item in participants if item not in self.entities]
        if unknown:
            raise KeyError(f"Unknown combat participant(s): {', '.join(unknown)}")

        order = [str(item) for item in (initiative_order or participants)]
        if set(order) != set(participants) or len(order) != len(participants):
            raise ValueError("Initiative order must contain every participant exactly once.")

        current_id = order[0]
        combat = CombatSession(
            participant_ids=participants,
            initiative_order=order,
            round=1,
            turn_index=0,
            turn_state=self._turn_state_for(current_id),
        )
        return self._dispatch(
            SessionEventType.COMBAT_STARTED,
            {"combat": combat.to_dict()},
            message=f"Combat started - turn: {self.entities[current_id].name}",
            source_id=current_id,
        )

    def end_combat(self) -> SessionEvent:
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        previous = self.combat.to_dict()
        return self._dispatch(
            SessionEventType.COMBAT_ENDED,
            {"combat_before": previous},
            message="Combat ended",
        )

    def next_turn(self) -> tuple[SessionEvent, SessionEvent]:
        if self.combat is None or not self.combat.initiative_order:
            raise RuntimeError("No combat is active.")
        current_id = self.combat.current_entity_id
        ended = self._dispatch(
            SessionEventType.TURN_ENDED,
            {
                "entity_id": current_id,
                "round": self.combat.round,
                "turn_index": self.combat.turn_index,
            },
            message=(
                f"Turn ended: {self.entities[current_id].name}"
                if current_id in self.entities else "Turn ended"
            ),
            source_id=current_id,
        )

        updated = CombatSession.from_dict(self.combat.to_dict())
        next_index = updated.turn_index + 1
        if next_index >= len(updated.initiative_order):
            next_index = 0
            updated.round += 1
        updated.turn_index = next_index
        next_id = updated.current_entity_id
        if next_id is None:
            raise RuntimeError("Combat initiative order is empty.")
        updated.turn_state = self._turn_state_for(next_id)

        started = self._dispatch(
            SessionEventType.TURN_STARTED,
            {"combat": updated.to_dict()},
            message=f"Round {updated.round} - turn: {self.entities[next_id].name}",
            source_id=next_id,
        )
        return ended, started

    def action_stamina_remaining(self, entity_id: str) -> int:
        if self.combat is None or self.combat.turn_state is None:
            return 0
        if self.combat.current_entity_id != str(entity_id):
            return 0
        return self.combat.turn_state.stamina_remaining

    def movement_remaining(self, entity_id: str) -> int:
        if self.combat is None or self.combat.turn_state is None:
            return 0
        if self.combat.current_entity_id != str(entity_id):
            return 0
        return self.combat.turn_state.movement_remaining

    def spend_action_stamina(
        self,
        entity_id: str,
        cost: int,
        *,
        action: str,
    ) -> SessionEvent:
        entity_id = str(entity_id)
        if not self.player_permissions(entity_id).can_combat_actions:
            raise PermissionError("Combat actions are only available during the entity's turn.")
        if self.combat is None or self.combat.turn_state is None:
            raise RuntimeError("Combat turn state is not initialized.")
        cost = max(0, int(cost))
        before = self.combat.turn_state.stamina_remaining
        if cost > before:
            raise ValueError(
                f"Not enough stamina for {action}: {cost} required, {before} remaining."
            )
        after = before - cost
        return self._dispatch(
            SessionEventType.ACTION_STAMINA_SPENT,
            {
                "entity_id": entity_id,
                "action": str(action),
                "cost": cost,
                "before": before,
                "after": after,
            },
            message=f"{self.entities[entity_id].name} uses {cost} stamina for {action}",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def spend_strike_stamina(self, entity_id: str) -> SessionEvent:
        return self.spend_action_stamina(
            entity_id, STRIKE_STAMINA_COST, action="strike"
        )

    def spend_shoot_stamina(self, entity_id: str) -> SessionEvent:
        return self.spend_action_stamina(
            entity_id, SHOOT_STAMINA_COST, action="shoot"
        )

    def spend_spell_stamina(self, entity_id: str, spell_name: str) -> SessionEvent:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        spell = entity.get_spell(spell_name)
        if spell is None:
            raise KeyError(f"Entity {entity.name!r} does not know spell {spell_name!r}.")
        return self.spend_action_stamina(
            entity_id, int(spell.cost), action=f"spell:{spell.name}"
        )

    def available_actions(self, entity_id: str) -> dict[str, Any]:
        entity_id = str(entity_id)
        entity = self.entities.get(entity_id)
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        permissions = self.player_permissions(entity_id)
        stamina = self.action_stamina_remaining(entity_id)
        return {
            "combat_actions_enabled": permissions.can_combat_actions,
            "stamina_remaining": stamina,
            "strike": {
                "cost": STRIKE_STAMINA_COST,
                "available": permissions.can_combat_actions and stamina >= STRIKE_STAMINA_COST,
            },
            "shoot": {
                "cost": SHOOT_STAMINA_COST,
                "available": permissions.can_combat_actions and stamina >= SHOOT_STAMINA_COST,
            },
            "spells": {
                name: {
                    "cost": int(spell.cost),
                    "available": permissions.can_combat_actions and stamina >= int(spell.cost),
                }
                for name, spell in entity.spells.items()
            },
        }

    def set_combat(self, combat: Optional[CombatSession]) -> SessionEvent:
        """Legacy low-level setter kept for V1 callers; prefer start_combat/end_combat."""
        return self._dispatch(
            SessionEventType.COMBAT_SET,
            {"combat": combat.to_dict() if combat is not None else None},
            message="Combat state changed",
        )

    def add_note(self, message: str, *, source_id: Optional[str] = None) -> SessionEvent:
        return self._dispatch(
            SessionEventType.NOTE,
            {},
            message=message,
            source_id=source_id,
        )

    # --------------------------------------------------------------- save/load

    def save(self, session_dir: Optional[Path] = None) -> Path:
        """Force a current checkpoint, then atomically persist the Session."""
        self.create_snapshot(force=True)
        if session_dir is None:
            session_dir = self.campaign.session_path(self.session.name)
        return self.session.save(session_dir)
