# -*- coding: utf-8 -*-

"""SessionRuntime edits from the GM character sheet, and their write-back to the campaign files."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Optional

from rpg_tabletop.assets.item import Item
from rpg_tabletop.assets.references import canonical_name
from rpg_tabletop.assets.spell import TARGET_STATS, EffectType, Spell
from rpg_tabletop.session.entity import Entity, StatsModifier
from rpg_tabletop.session.models import (
    SessionEvent,
    SessionEventType,
)
from rpg_tabletop.session.snapshot import (
    _effect_cache_from_entities,
    _spell_to_dict,
)
from rpg_tabletop.session.spell_effect import SpellEffect
from rpg_tabletop.session.spell_event import SpellEvent
from rpg_tabletop.session.writeback import plan_entity_writeback, write_planned_assets

STATS_MODIFIER_NAMES = tuple(item.name for item in fields(StatsModifier))


class SheetEditMixin:
    """GM character sheet edits and campaign write-back. Mixed into SessionRuntime."""

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
