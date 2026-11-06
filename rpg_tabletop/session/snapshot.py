# -*- coding: utf-8 -*-

"""
Serialization of entities into runtime snapshots, and their restoration.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from math import isfinite
from typing import Any, Iterable, Optional
from uuid import UUID

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.character import Character
from rpg_tabletop.assets.item import Inventory, Item
from rpg_tabletop.assets.spell import Spell

from .entity import Entity, StatsModifier
from .models import CombatSession, SessionMapState, _datetime_from_str, _datetime_to_str
from .spell_effect import SpellEffect
from .spell_event import SpellEvent

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
    data["area_size"] = int(spell.area_size)
    if spell.max_targets:
        data["max_targets"] = int(spell.max_targets)
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
