# -*- coding: utf-8 -*-

"""SessionRuntime entity API: entities in the session, resolved effects and spell events."""

from __future__ import annotations

from typing import Any, Optional

from rpg_tabletop.session.entity import Entity
from rpg_tabletop.session.live_sync import build_asset_update
from rpg_tabletop.session.models import (
    SessionEvent,
    SessionEventType,
)
from rpg_tabletop.session.snapshot import entity_to_snapshot
from rpg_tabletop.session.spell_effect import SpellEffect
from rpg_tabletop.session.spell_event import SpellEvent


class EntityMixin:
    """Entities, effects and spell events of the session. Mixed into SessionRuntime."""

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
