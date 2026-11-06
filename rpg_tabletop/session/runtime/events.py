# -*- coding: utf-8 -*-

"""SessionRuntime events: every change is dispatched as a journal event, then applied to the live state."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from rpg_tabletop.assets.item import Item
from rpg_tabletop.assets.spell import Spell
from rpg_tabletop.session.actions import apply_runtime_states
from rpg_tabletop.session.entity import StatsModifier
from rpg_tabletop.session.journal import _apply_map_local_event
from rpg_tabletop.session.live_sync import apply_asset_update
from rpg_tabletop.session.models import (
    CombatSession,
    SessionEvent,
    SessionEventType,
    _utc_now,
)
from rpg_tabletop.session.snapshot import (
    _effect_cache_from_entities,
    _restore_entities,
)
from rpg_tabletop.session.spell_effect import SpellEffect
from rpg_tabletop.session.spell_event import SpellEvent, share_effects


class EventMixin:
    """Event dispatch and application. Mixed into SessionRuntime."""

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
