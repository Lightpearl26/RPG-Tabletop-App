# -*- coding: utf-8 -*-

"""
SessionRuntime: the live state engine of a game session (entities, active map,
combat, actions), recorded into a Session journal.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from json import loads
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.item import Item
from rpg_tabletop.assets.map import (
    CameraState,
    MapAsset,
    MapElement,
    Vec2,
)
from rpg_tabletop.assets.references import canonical_name
from rpg_tabletop.assets.spell import TARGET_STATS, EffectType, Spell
from rpg_tabletop.core.dice import DiceCheck
from rpg_tabletop.core.geometry import wall_between
from rpg_tabletop.core.rules import (
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

from .actions import apply_runtime_states
from .entity import Entity, StatsModifier
from .initiative import check_roll, insertion_index
from .initiative import initiative_order as sort_by_initiative
from .journal import DEFAULT_SNAPSHOT_INTERVAL, Session, _apply_map_local_event
from .live_sync import apply_asset_update, build_asset_update
from .models import (
    CombatSession,
    SessionEvent,
    SessionEventType,
    SessionMapState,
    SessionToken,
    TurnState,
    _utc_now,
)
from .snapshot import (
    SessionSnapshot,
    _effect_cache_from_entities,
    _restore_entities,
    _spell_to_dict,
    entity_to_snapshot,
)
from .spell_effect import SpellEffect
from .spell_event import SpellEvent, share_effects
from .targeting import map_context, sees
from .writeback import plan_entity_writeback, write_planned_assets

STATS_MODIFIER_NAMES = tuple(item.name for item in fields(StatsModifier))


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
        # Entity fields edited from the GM character sheet and not yet written to
        # the campaign files: {entity_id: {"items", "stats_modifier.str", ...}}.
        # Kept in memory only: reopening a session never rewrites the campaign.
        self.asset_changes: dict[str, set[str]] = {}

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
        # Entities loaded one by one from their files hold separate effect copies
        share_effects(entity_dict.values())
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

        if event.type is SessionEventType.ENTITY_EDITED:
            self._apply_entity_edit(data)
            return

        if event.type is SessionEventType.ACTION_RESOLVED:
            apply_runtime_states(self, data)
            return

        if event.type is SessionEventType.ATTACK_ROLLED:
            return  # the stamina is a separate ACTION_STAMINA_SPENT event

        if event.type is SessionEventType.ASSETS_SAVED:
            for entity_id, info in data.get("entities", {}).items():
                entity = self.entities.get(str(entity_id))
                if entity is not None and "authored_modifier" in info:
                    entity.authored_modifier = StatsModifier.from_dict(info["authored_modifier"])
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
            share_effects(self.entities.values())
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
            SessionEventType.COMBAT_PARTICIPANT_ADDED,
            SessionEventType.COMBAT_PARTICIPANT_REMOVED,
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
            current.origin = updated.origin
            return

        if event.type is SessionEventType.EFFECT_REMOVED:
            self._remove_effects_everywhere((data["effect_id"],))
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
            self._remove_effects_everywhere(data.get("removed_effect_ids", ()))
            return

        if event.type is SessionEventType.SPELL_EVENT_REMOVED:
            spell_event_id = str(data["spell_event_id"])
            caster.spell_events = [
                spell_event
                for spell_event in caster.spell_events
                if str(spell_event.id) != spell_event_id
            ]
            self._remove_effects_everywhere(data.get("removed_effect_ids", ()))

    def _remove_effects_everywhere(self, effect_ids: Iterable[str]) -> None:
        """Drop effects from their targets and from the events that created them."""
        removed = {str(effect_id) for effect_id in effect_ids}
        if not removed:
            return
        for entity in self.entities.values():
            entity.spell_effects = [
                effect for effect in entity.spell_effects if str(effect.id) not in removed
            ]
            for spell_event in entity.spell_events:
                spell_event.effects = [
                    effect for effect in spell_event.effects if str(effect.id) not in removed
                ]

    def _apply_entity_edit(self, data: dict[str, Any]) -> None:
        """Replay one ENTITY_EDITED event. Definitions come from the event only."""
        entity = self.entities.get(str(data["entity_id"]))
        if entity is None:
            return
        kind = data["field"]

        if kind == "stats_modifier":
            setattr(entity.stats_modifier, str(data["stat"]), int(data["after"]))

        elif kind == "item":
            name = str(data["name"])
            quantity = int(data["after"])
            if quantity <= 0:
                entity.items.items.pop(name, None)
                entity.items._item_cache.pop(name, None)
            else:
                entity.items.items[name] = quantity
                if data.get("snapshot"):
                    entity.items._item_cache[name] = Item.from_dict(data["snapshot"])

        elif kind == "spell":
            name = str(data["name"])
            if data["action"] == "add":
                spell = Spell.from_dict(data["snapshot"])
                entity.spells[name] = spell
                self.campaign.spells.register(spell.name, spell)
            else:
                entity.spells.pop(name, None)

        else:
            raise ValueError(f"Unknown entity edit: {kind!r}")

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
        """The active map's asset, read again only when its file changed."""
        if self.active_map_id is None:
            raise RuntimeError("No map is currently active.")
        map_dir = self.campaign.map_path(self.active_map_id)
        try:
            stamp = (self.active_map_id, (map_dir / "map.json").stat().st_mtime_ns)
        except OSError:
            stamp = None
        cached = getattr(self, "_asset_cache", None)
        if stamp is not None and cached is not None and cached[0] == stamp:
            return cached[1]
        asset = MapAsset.load(map_dir)
        self._asset_cache = (stamp, asset)
        return asset

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

        context = map_context(self)
        visible: list[SessionToken] = []
        for token in state.tokens:
            if token.entity_id == observer_entity_id:
                visible.append(token)
                continue
            if token.hidden:
                continue
            # PER radius, and walls hide what is behind them
            if sees(self, context, observer, token):
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
        context = map_context(self)
        if wall_between(context.grid, context.walls, from_cell, to_cell):
            raise ValueError("A wall is in the way: move around it.")

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

    # ------------------------------------------------------- GM character sheet
    #
    # Every edit is one journal event, so replay and time travel reproduce it.
    # The edited fields are also remembered in `asset_changes` and written to the
    # entity file by `write_entity_assets` at the next session save.

    def _require_entity(self, entity_id: str) -> Entity:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        return entity

    def _mark_asset_change(self, entity_id: str, *keys: str) -> None:
        self.asset_changes.setdefault(str(entity_id), set()).update(keys)

    def _effect_owner(self, effect_id: str) -> Optional[tuple[str, SpellEvent]]:
        """Return (caster id, spell event) of the event holding an effect, if any."""
        for caster_id, entity in self.entities.items():
            for spell_event in entity.spell_events:
                if any(str(effect.id) == str(effect_id) for effect in spell_event.effects):
                    return caster_id, spell_event
        return None

    def find_effect(self, effect_id: str) -> SpellEffect:
        """Return the live instance of an effect, on its target or in its event."""
        cache = _effect_cache_from_entities(self.entities.values())
        effect = cache.get(str(effect_id))
        if effect is None:
            raise KeyError(f"Unknown effect: {effect_id}")
        return effect

    def find_spell_event(self, caster_id: str, spell_event_id: str) -> SpellEvent:
        caster = self._require_entity(caster_id)
        for spell_event in caster.spell_events:
            if str(spell_event.id) == str(spell_event_id):
                return spell_event
        raise KeyError(f"Unknown spell event: {spell_event_id}")

    def _edit_entity(self, entity: Entity, data: dict[str, Any], message: str) -> SessionEvent:
        entity_id = str(entity.id)
        return self._dispatch(
            SessionEventType.ENTITY_EDITED,
            {"entity_id": entity_id, **data},
            message=message,
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def set_stat_modifier(self, entity_id: str, stat: str, value: int) -> Optional[SessionEvent]:
        """Set one field of the entity's runtime stats modifier."""
        entity = self._require_entity(entity_id)
        stat = str(stat)
        if stat not in STATS_MODIFIER_NAMES:
            raise ValueError(f"Unknown stat: {stat}")
        before, after = int(getattr(entity.stats_modifier, stat)), int(value)
        if before == after:
            return None
        event = self._edit_entity(
            entity,
            {"field": "stats_modifier", "stat": stat, "before": before, "after": after},
            f"{entity.name}: {stat.upper()} modifier {before:+d} -> {after:+d}",
        )
        self._mark_asset_change(entity_id, f"stats_modifier.{stat}")
        return event

    def set_item_quantity(
        self,
        entity_id: str,
        item_name: str,
        quantity: int,
    ) -> Optional[SessionEvent]:
        """Add, change the quantity of, or remove (quantity 0) an inventory item."""
        entity = self._require_entity(entity_id)
        key = canonical_name(str(item_name))
        name = next(
            (current for current in entity.items.items if canonical_name(current) == key),
            str(item_name).strip(),
        )
        before, after = int(entity.items.items.get(name, 0)), max(0, int(quantity))
        if before == after:
            return None

        snapshot = None
        if after > 0 and name not in entity.items._item_cache:
            try:
                item = Item.from_name(name)
            except (OSError, ValueError, KeyError) as error:
                raise ValueError(f"Item not found in the campaign: {name}") from error
            snapshot = {
                "name": item.name,
                "description": item.description,
                "modifier": [list(entry) for entry in item.modifier],
            }
        if before == 0:
            message = f"{entity.name}: item added, {name} x{after}"
        elif after == 0:
            message = f"{entity.name}: item removed, {name}"
        else:
            message = f"{entity.name}: {name} x{before} -> x{after}"
        event = self._edit_entity(
            entity,
            {"field": "item", "name": name, "before": before, "after": after, "snapshot": snapshot},
            message,
        )
        self._mark_asset_change(entity_id, "items")
        return event

    def add_spell(self, entity_id: str, spell_name: str) -> SessionEvent:
        """Teach a campaign spell to the entity."""
        entity = self._require_entity(entity_id)
        key = canonical_name(str(spell_name))
        if any(canonical_name(name) == key for name in entity.spells):
            raise ValueError(f"{entity.name} already knows {spell_name}.")
        spell = Spell.from_name(str(spell_name))
        if spell is None:
            raise ValueError(f"Spell not found in the campaign: {spell_name}")
        event = self._edit_entity(
            entity,
            {"field": "spell", "action": "add", "name": spell.name, "snapshot": _spell_to_dict(spell)},
            f"{entity.name}: spell learned, {spell.name}",
        )
        self._mark_asset_change(entity_id, "spells")
        return event

    def remove_spell(self, entity_id: str, spell_name: str) -> SessionEvent:
        """Forget a spell. Its running spell events are left untouched."""
        entity = self._require_entity(entity_id)
        spell = entity.spells.get(str(spell_name))
        if spell is None:
            raise KeyError(f"{entity.name} does not know {spell_name}.")
        event = self._edit_entity(
            entity,
            {
                "field": "spell",
                "action": "remove",
                "name": str(spell_name),
                "snapshot": _spell_to_dict(spell),
            },
            f"{entity.name}: spell forgotten, {spell_name}",
        )
        self._mark_asset_change(entity_id, "spells")
        return event

    def _mark_effect_change(self, effect: SpellEffect) -> None:
        self._mark_asset_change(effect.target_id, "spell_effects")
        owner = self._effect_owner(str(effect.id))
        if owner is not None:
            self._mark_asset_change(owner[0], "spell_events")

    def add_manual_effect(
        self,
        entity_id: str,
        effect_type: EffectType | str,
        stat: str,
        delta: int,
        duration: int = -1,
        origin: str = "",
    ) -> SessionEvent:
        """
        Apply an effect decided by the GM, outside any spell.

        `origin` describes its cause in a few words ("Fallen rock").

        Durations are counted down by spell events only, so a manual effect keeps
        its duration until the GM changes or removes it.
        """
        entity = self._require_entity(entity_id)
        if stat not in TARGET_STATS:
            raise ValueError(f"Unknown stat: {stat}")
        if int(duration) == 0:
            raise ValueError("A duration of 0 means the effect has expired.")
        effect = SpellEffect(
            effect_type=EffectType(effect_type),
            effect_def=None,
            target_id=str(entity.id),
            target_stat=stat,  # type: ignore[arg-type]
            delta=int(delta),
            duration=int(duration),
            event_id=None,
            origin=str(origin).strip(),
        )
        event = self.apply_resolved_effect(
            effect,
            origin={"gm": True},
            message=f"{entity.name}: GM effect {effect.effect_type.value} {stat.upper()} {int(delta):+d}"
            + (f" ({effect.origin})" if effect.origin else ""),
        )
        self._mark_effect_change(effect)
        return event

    def edit_effect(
        self,
        effect_id: str,
        *,
        effect_type: EffectType | str | None = None,
        stat: Optional[str] = None,
        delta: Optional[int] = None,
        duration: Optional[int] = None,
        origin: Optional[str] = None,
    ) -> Optional[SessionEvent]:
        """Change an active effect in place (spell effects included)."""
        current = self.find_effect(effect_id)
        updated = SpellEffect.from_dict(current.to_dict())
        if effect_type is not None:
            updated.effect_type = EffectType(effect_type)
        if stat is not None:
            if stat not in TARGET_STATS:
                raise ValueError(f"Unknown stat: {stat}")
            updated.target_stat = stat  # type: ignore[assignment]
        if delta is not None:
            updated.delta = int(delta)
        if duration is not None:
            if int(duration) == 0:
                raise ValueError("A duration of 0 means the effect has expired.")
            updated.duration = int(duration)
        if origin is not None:
            updated.origin = str(origin).strip()
        if updated.to_dict() == current.to_dict():
            return None
        target = self.entities.get(current.target_id)
        event = self.update_effect(
            updated,
            message=f"{target.name if target else current.target_id}: effect changed "
            f"({updated.effect_type.value} {str(updated.target_stat).upper()} {updated.delta:+d})",
        )
        self._mark_effect_change(current)
        return event

    def delete_effect(self, effect_id: str) -> SessionEvent:
        """Remove an active effect from its target and from its spell event."""
        effect = self.find_effect(effect_id)
        self._mark_effect_change(effect)
        target = self.entities.get(effect.target_id)
        return self.remove_effect(
            str(effect.id),
            target_id=effect.target_id,
            message=f"{target.name if target else effect.target_id}: effect removed "
            f"({effect.effect_type.value} {str(effect.target_stat).upper()} {effect.delta:+d})",
        )

    def edit_spell_event(
        self,
        caster_id: str,
        spell_event_id: str,
        *,
        nb_cast: Optional[int] = None,
        finished: Optional[bool] = None,
    ) -> Optional[SessionEvent]:
        """Change the progression of a spell event."""
        current = self.find_spell_event(caster_id, spell_event_id)
        data = current.to_dict()
        if nb_cast is not None:
            data["nb_cast"] = max(0, int(nb_cast))
        if finished is not None:
            data["finished"] = bool(finished)
        if data == current.to_dict():
            return None
        event = self._dispatch(
            SessionEventType.SPELL_EVENT_UPDATED,
            {"caster_id": str(caster_id), "spell_event": data},
            source_id=str(caster_id),
            target_ids=tuple(current.targets_id),
            message=f"Spell event changed: {current.spell_id} "
            f"(casts {data['nb_cast']}, {'finished' if data['finished'] else 'running'})",
        )
        self._mark_asset_change(caster_id, "spell_events")
        return event

    def stop_spell_event(self, caster_id: str, spell_event_id: str) -> SessionEvent:
        """Finish a spell event and remove the effects it created."""
        current = self.find_spell_event(caster_id, spell_event_id)
        data = current.to_dict()
        removed = [effect["id"] for effect in data["effects"]]
        data["finished"] = True
        data["effects"] = []
        for effect in current.effects:
            self._mark_asset_change(effect.target_id, "spell_effects")
        event = self._dispatch(
            SessionEventType.SPELL_EVENT_UPDATED,
            {"caster_id": str(caster_id), "spell_event": data, "removed_effect_ids": removed},
            source_id=str(caster_id),
            target_ids=tuple(current.targets_id),
            message=f"Spell event stopped: {current.spell_id} ({len(removed)} effect(s) removed)",
        )
        self._mark_asset_change(caster_id, "spell_events")
        return event

    def delete_spell_event(self, caster_id: str, spell_event_id: str) -> SessionEvent:
        """Remove a spell event and the effects it created."""
        current = self.find_spell_event(caster_id, spell_event_id)
        removed = [str(effect.id) for effect in current.effects]
        for effect in current.effects:
            self._mark_asset_change(effect.target_id, "spell_effects")
        event = self._dispatch(
            SessionEventType.SPELL_EVENT_REMOVED,
            {
                "caster_id": str(caster_id),
                "spell_event_id": str(current.id),
                "spell_event": current.to_dict(),
                "removed_effect_ids": removed,
            },
            source_id=str(caster_id),
            target_ids=tuple(current.targets_id),
            message=f"Spell event removed: {current.spell_id} ({len(removed)} effect(s) removed)",
        )
        self._mark_asset_change(caster_id, "spell_events")
        return event

    # -------------------------------------------------------- campaign assets

    def write_entity_assets(self) -> Optional[SessionEvent]:
        """
        Write the fields edited from the GM sheet to the entity files.

        Entities that only exist in the session (no file with their id) are left
        out and keep their pending changes. The written fields become the new
        reference of the live sync (`authored_modifier`), through an ASSETS_SAVED
        event so that replay keeps it too.

        Returns:
            Optional[SessionEvent]: The ASSETS_SAVED event, None when nothing was written.

        Raises:
            OSError: If a file cannot be written. Nothing is changed then.
        """
        plan = plan_entity_writeback(self.campaign, self.entities, self.asset_changes)
        if not plan.files:
            return None
        write_planned_assets(plan)
        for entity_id in plan.entities:
            self.asset_changes.pop(entity_id, None)
        names = ", ".join(sorted(info["name"] for info in plan.entities.values()))
        return self._dispatch(
            SessionEventType.ASSETS_SAVED,
            {"entities": plan.entities},
            message=f"Campaign entities updated: {names}",
            target_ids=tuple(plan.entities),
        )

    def entities_without_asset(self) -> list[str]:
        """Ids of the entities with pending sheet changes but no campaign file."""
        return [
            entity_id
            for entity_id in plan_entity_writeback(
                self.campaign, self.entities, self.asset_changes
            ).skipped
        ]

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

    def _initiative_helpers(self) -> tuple[Any, Any]:
        def agility(entity_id: str) -> int:
            entity = self.entities.get(entity_id)
            return int(entity.get_stat("agi")) if entity is not None else 0

        def name(entity_id: str) -> str:
            entity = self.entities.get(entity_id)
            return entity.name if entity is not None else entity_id

        return agility, name

    def start_combat_with_initiative(self, rolls: dict[str, int]) -> SessionEvent:
        """
        Start a combat from d20 results: the lowest acts first; ties go to the
        higher AGI, then to the name.
        """
        rolls = {str(entity_id): check_roll(value) for entity_id, value in rolls.items()}
        order = sort_by_initiative(rolls, *self._initiative_helpers())
        return self.start_combat(order, initiative_order=order, initiative_rolls=rolls)

    def start_combat(
        self,
        participant_ids: Sequence[str],
        *,
        initiative_order: Optional[Sequence[str]] = None,
        initiative_rolls: Optional[dict[str, int]] = None,
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
        rolls = {str(key): int(value) for key, value in (initiative_rolls or {}).items()}
        combat = CombatSession(
            participant_ids=participants,
            initiative_order=order,
            round=1,
            turn_index=0,
            turn_state=self._turn_state_for(current_id),
            initiative_rolls={key: value for key, value in rolls.items() if key in participants},
        )
        message = f"Combat started - turn: {self.entities[current_id].name}"
        if combat.initiative_rolls:
            message += " - initiative: " + ", ".join(
                f"{self.entities[entity_id].name} {combat.initiative_rolls[entity_id]}"
                for entity_id in order if entity_id in combat.initiative_rolls
            )
        return self._dispatch(
            SessionEventType.COMBAT_STARTED,
            {"combat": combat.to_dict()},
            message=message,
            source_id=current_id,
        )

    def add_combat_participant(self, entity_id: str, roll: int) -> SessionEvent:
        """
        Bring an entity into the running combat at the place its d20 gives it.

        Placed after the current turn, it plays this round; before, next round.
        """
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        entity_id = str(entity_id)
        entity = self._require_entity(entity_id)
        if entity_id in self.combat.participant_ids:
            raise ValueError(f"{entity.name} already takes part in the combat.")
        roll = check_roll(roll)
        updated = CombatSession.from_dict(self.combat.to_dict())
        index = insertion_index(
            updated.initiative_order, updated.initiative_rolls, entity_id, roll,
            *self._initiative_helpers(),
        )
        updated.initiative_order.insert(index, entity_id)
        updated.participant_ids.append(entity_id)
        updated.initiative_rolls[entity_id] = roll
        if index <= updated.turn_index:
            updated.turn_index += 1  # the current entity keeps its turn
        return self._dispatch(
            SessionEventType.COMBAT_PARTICIPANT_ADDED,
            {"combat": updated.to_dict(), "entity_id": entity_id, "roll": roll},
            message=f"{entity.name} joins the combat (initiative {roll})",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def remove_combat_participant(self, entity_id: str) -> SessionEvent:
        """Take an entity out of the running combat (not the one whose turn it is)."""
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        entity_id = str(entity_id)
        if entity_id not in self.combat.participant_ids:
            raise KeyError(f"Not in the combat: {entity_id}")
        if entity_id == self.combat.current_entity_id:
            raise ValueError("End this entity's turn before taking it out of the combat.")
        if len(self.combat.initiative_order) <= 1:
            raise ValueError("End the combat instead: it would have no participant left.")
        updated = CombatSession.from_dict(self.combat.to_dict())
        index = updated.initiative_order.index(entity_id)
        updated.initiative_order.remove(entity_id)
        updated.participant_ids.remove(entity_id)
        updated.initiative_rolls.pop(entity_id, None)
        if index < updated.turn_index:
            updated.turn_index -= 1
        entity = self.entities.get(entity_id)
        return self._dispatch(
            SessionEventType.COMBAT_PARTICIPANT_REMOVED,
            {"combat": updated.to_dict(), "entity_id": entity_id},
            message=f"{entity.name if entity else entity_id} leaves the combat",
            source_id=entity_id,
            target_ids=(entity_id,),
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
