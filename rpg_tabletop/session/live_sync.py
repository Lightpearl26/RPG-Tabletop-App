# -*- coding: utf-8 -*-

"""
Live propagation of edited campaign assets to a running session.

A session keeps frozen definitions of its characters, spells and items, so that
editing the campaign never silently changes a historical session. To make an
edit reach a live session anyway, the new definition is recorded in the journal
as an ASSET_UPDATED event:

    build_asset_update()  reads the saved file and compares it with the runtime
                          (disk access happens only here, when recording);
    apply_asset_update()  applies the definition stored in the event (used by
                          the replay, never touches the disk).

Rules:
    - Effects already applied keep the delta they were computed with; only the
      next casts use the new formulas.
    - Entity modifiers are synced as a delta against `Entity.authored_modifier`
      (what the file said last time), so runtime changes such as spent stamina
      are not overwritten.
    - Items and spells listed by an entity but missing on disk are ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from rpg_tabletop.assets.character import Character
from rpg_tabletop.assets.item import Inventory, Item
from rpg_tabletop.assets.references import canonical_name
from rpg_tabletop.assets.spell import Spell
from .entity import StatsModifier

if TYPE_CHECKING:
    from .session import SessionRuntime


# Resource type of the IDE -> kind of asset update
RESOURCE_KINDS = {
    "characters": "character",
    "items": "item",
    "spells": "spell",
    "entities": "entity",
}


@dataclass(frozen=True)
class AssetUpdate:
    """What to record for one edited asset."""

    data: dict[str, Any]
    message: str
    entity_ids: tuple[str, ...]


# =============================================================================
# Helpers
# =============================================================================


def _snapshotters():
    # Imported late: session.py imports this module.
    from .session import _character_to_dict, _spell_to_dict

    return _character_to_dict, _spell_to_dict


def _read(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def _item_dict(item: Item) -> dict[str, Any]:
    return {
        "name": item.name,
        "description": item.description,
        "modifier": [list(entry) for entry in item.modifier],
    }


# =============================================================================
# Recording
# =============================================================================


def _character_update(runtime: SessionRuntime, stem: str) -> Optional[AssetUpdate]:
    character_to_dict, _ = _snapshotters()
    key = canonical_name(stem)
    definition = character_to_dict(
        Character.from_dict(_read(runtime.campaign.character_path / f"{stem}.json"))
    )
    affected = tuple(
        entity_id
        for entity_id, entity in runtime.entities.items()
        if canonical_name(entity.character.name) == key
        and character_to_dict(entity.character) != definition
    )
    if not affected:
        return None
    return AssetUpdate(
        {"kind": "character", "name": stem, "definition": definition,
         "entity_ids": list(affected)},
        f"Character updated: {definition['name']}",
        affected,
    )


def _item_update(runtime: SessionRuntime, stem: str) -> Optional[AssetUpdate]:
    key = canonical_name(stem)
    definition = _item_dict(
        Item.from_dict(_read(runtime.campaign.item_path / f"{stem}.json"))
    )
    affected: list[str] = []
    for entity_id, entity in runtime.entities.items():
        for inventory_name in entity.items.items:
            if canonical_name(inventory_name) != key:
                continue
            cached = entity.items._item_cache.get(inventory_name)
            if cached is None or _item_dict(cached) != definition:
                affected.append(entity_id)
                break
    if not affected:
        return None
    return AssetUpdate(
        {"kind": "item", "name": stem, "definition": definition, "entity_ids": affected},
        f"Item updated: {definition['name']}",
        tuple(affected),
    )


def _spell_update(runtime: SessionRuntime, stem: str) -> Optional[AssetUpdate]:
    _, spell_to_dict = _snapshotters()
    key = canonical_name(stem)
    definition = spell_to_dict(
        Spell.from_dict(_read(runtime.campaign.spell_path / f"{stem}.json"))
    )
    affected = tuple(
        entity_id
        for entity_id, entity in runtime.entities.items()
        if any(
            canonical_name(name) == key and spell_to_dict(spell) != definition
            for name, spell in entity.spells.items()
        )
    )
    if not affected:
        return None
    return AssetUpdate(
        {"kind": "spell", "name": stem, "definition": definition,
         "entity_ids": list(affected)},
        f"Spell updated: {definition['name']}",
        affected,
    )


def _entity_update(runtime: SessionRuntime, stem: str) -> Optional[AssetUpdate]:
    character_to_dict, spell_to_dict = _snapshotters()
    campaign = runtime.campaign
    raw = _read(campaign.entity_path / f"{stem}.json")
    entity = runtime.entities.get(str(raw.get("id") or ""))
    if entity is None:
        return None  # not part of this session

    character_name = canonical_name(str(raw.get("character", "")))
    character = character_to_dict(
        Character.from_dict(_read(campaign.character_path / f"{character_name}.json"))
    )

    spells: dict[str, Any] = {}
    for spell_name in raw.get("spells", []) or []:
        path = campaign.spell_path / f"{canonical_name(str(spell_name))}.json"
        if path.is_file():
            spells[str(spell_name)] = spell_to_dict(Spell.from_dict(_read(path)))

    entries = raw.get("items", raw.get("inventory", [])) or []
    if isinstance(entries, dict):
        entries = list(entries.items())
    items: list[list[Any]] = []
    item_snapshots: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        name, quantity = str(entry[0]), int(entry[1])
        path = campaign.item_path / f"{canonical_name(name)}.json"
        if path.is_file():
            items.append([name, quantity])
            item_snapshots[name] = _item_dict(Item.from_dict(_read(path)))

    authored = StatsModifier.from_dict(
        raw.get("stats_modifier", raw.get("stat_modifier", {})) or {}
    ).to_dict()
    before = entity.authored_modifier.to_dict()
    delta = {stat: authored[stat] - before[stat] for stat in authored if authored[stat] != before[stat]}
    name = str(raw.get("name") or stem.replace("_", " "))

    unchanged = (
        not delta
        and name == entity.name
        and character == character_to_dict(entity.character)
        and spells == {n: spell_to_dict(s) for n, s in entity.spells.items()}
        and sorted(items) == sorted([n, q] for n, q in entity.items.items.items())
    )
    if unchanged:
        return None

    entity_id = str(entity.id)
    return AssetUpdate(
        {
            "kind": "entity",
            "name": stem,
            "entity_ids": [entity_id],
            "definition": {
                "name": name,
                "character": character,
                "spells": spells,
                "items": items,
                "item_snapshots": item_snapshots,
                "modifier_delta": delta,
                "authored_modifier": authored,
            },
        },
        f"Entity updated from the campaign: {name}",
        (entity_id,),
    )


_BUILDERS: dict[str, Callable[[SessionRuntime, str], Optional[AssetUpdate]]] = {
    "character": _character_update,
    "item": _item_update,
    "spell": _spell_update,
    "entity": _entity_update,
}


def build_asset_update(
    runtime: SessionRuntime,
    kind: str,
    stem: str,
) -> Optional[AssetUpdate]:
    """
    Read the saved asset `stem` and describe what changed in `runtime`.

    Returns:
        Optional[AssetUpdate]: None when nothing in the session is concerned or
        the session already holds this definition.

    Raises:
        OSError / ValueError / KeyError: If the saved file cannot be read or is invalid.
    """
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown asset kind: {kind!r}")
    return builder(runtime, str(stem))


# =============================================================================
# Replay
# =============================================================================


def apply_asset_update(runtime: SessionRuntime, data: dict[str, Any]) -> None:
    """
    Apply the definition stored in an ASSET_UPDATED event. Never reads the disk.
    """
    kind = data["kind"]
    definition = data["definition"]
    entities = [
        runtime.entities[entity_id]
        for entity_id in data.get("entity_ids", [])
        if entity_id in runtime.entities
    ]
    key = canonical_name(str(data.get("name", "")))

    if kind == "character":
        for entity in entities:
            entity.character = Character.from_dict(definition)

    elif kind == "item":
        for entity in entities:
            for inventory_name in list(entity.items.items):
                if canonical_name(inventory_name) == key:
                    entity.items._item_cache[inventory_name] = Item.from_dict(definition)

    elif kind == "spell":
        spell = Spell.from_dict(definition)
        for entity in entities:
            for spell_name in list(entity.spells):
                if canonical_name(spell_name) == key:
                    entity.spells[spell_name] = spell
        runtime.campaign.spells.register(spell.name, spell)

    elif kind == "entity":
        for entity in entities:
            entity.name = str(definition["name"])
            entity.character = Character.from_dict(definition["character"])
            entity.spells = {}
            for spell_name, snapshot in definition["spells"].items():
                spell = Spell.from_dict(snapshot)
                entity.spells[spell_name] = spell
                runtime.campaign.spells.register(spell.name, spell)
            inventory = Inventory.from_list([tuple(entry) for entry in definition["items"]])
            for item_name, snapshot in definition["item_snapshots"].items():
                inventory._item_cache[item_name] = Item.from_dict(snapshot)
            entity.items = inventory
            for stat, delta in definition["modifier_delta"].items():
                setattr(entity.stats_modifier, stat, getattr(entity.stats_modifier, stat, 0) + delta)
            entity.authored_modifier = StatsModifier.from_dict(definition["authored_modifier"])

    else:
        raise ValueError(f"Unknown asset kind: {kind!r}")
