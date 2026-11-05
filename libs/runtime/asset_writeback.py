# -*- coding: utf-8 -*-

"""
Write GM character sheet changes back to the campaign entity files.

During a session the GM edits entities through the character sheet; each edit is
a journal event and the edited fields are remembered in
`SessionRuntime.asset_changes`. At session save, those fields (and only those)
are copied into the entity file that carries the same id:

    stats_modifier.<stat>  -> "stats_modifier": {<stat>: value}
    items                  -> "items": [[name, quantity], ...]
    spells                 -> "spells": [name, ...]
    spell_effects          -> "spell_effects": [effect, ...]
    spell_events           -> "spell_events": [event, ...]

Every other key of the file is kept as it is, so edits made in the IDE to other
fields are not overwritten. Entities created during the session (no file with
their id) are reported as skipped. All files are written together: if one
fails, the others are put back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..assets.attachments import FileChange, apply_changes
from ..assets.campaign import Campaign
from ..assets.references import json_text

if TYPE_CHECKING:
    from .entity import Entity


LEGACY_MODIFIER_KEYS = ("stat_modifier", "stat_modifiers")


@dataclass
class WritebackPlan:
    """What a session save writes to the campaign."""

    # The new content of every entity file to write
    files: list[FileChange] = field(default_factory=list)
    # {entity_id: {"name", "file", "fields", "authored_modifier"}} for the written ones
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Ids of the entities with changes but no campaign file
    skipped: list[str] = field(default_factory=list)


def entity_files_by_id(campaign: Campaign) -> dict[str, Path]:
    """
    Map the id stored in each entity file to that file.
    """
    files: dict[str, Path] = {}
    directory = campaign.entity_path
    if not directory.is_dir():
        return files
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and raw.get("id"):
            files.setdefault(str(raw["id"]), path)
    return files


def _updated_file(entity: Entity, data: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    """Copy the edited fields of `entity` into the file content `data`."""
    stats = sorted(key.split(".", 1)[1] for key in keys if key.startswith("stats_modifier."))
    if stats:
        modifiers: dict[str, Any] = {}
        for legacy in LEGACY_MODIFIER_KEYS:
            if isinstance(data.get(legacy), dict):
                modifiers.update(data.pop(legacy))
        if isinstance(data.get("stats_modifier"), dict):
            modifiers.update(data["stats_modifier"])
        for stat in stats:
            modifiers[stat] = int(getattr(entity.stats_modifier, stat))
        data["stats_modifier"] = modifiers

    if "items" in keys:
        data.pop("inventory", None)
        data["items"] = [[name, int(quantity)] for name, quantity in entity.items.items.items()]
    if "spells" in keys:
        data["spells"] = list(entity.spells)
    if "spell_effects" in keys:
        data["spell_effects"] = [effect.to_dict() for effect in entity.spell_effects]
    if "spell_events" in keys:
        data["spell_events"] = [event.to_dict() for event in entity.spell_events]
    return data


def plan_entity_writeback(
    campaign: Campaign,
    entities: Mapping[str, Entity],
    changes: Mapping[str, set[str]],
) -> WritebackPlan:
    """
    Compute the entity files to write, without writing anything.

    Raises:
        OSError / ValueError: If an entity file cannot be read.
    """
    plan = WritebackPlan()
    pending = {entity_id: keys for entity_id, keys in changes.items() if keys}
    if not pending:
        return plan

    files = entity_files_by_id(campaign)
    for entity_id, keys in sorted(pending.items()):
        entity = entities.get(entity_id)
        if entity is None:
            continue  # removed from the session meanwhile
        path = files.get(entity_id)
        if path is None:
            plan.skipped.append(entity_id)
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name} must contain a JSON object.")
        data = _updated_file(entity, data, set(keys))

        authored = entity.authored_modifier.to_dict()
        for key in keys:
            if key.startswith("stats_modifier."):
                stat = key.split(".", 1)[1]
                authored[stat] = int(getattr(entity.stats_modifier, stat))

        plan.files.append(FileChange(path, json_text(data).encode("utf-8")))
        plan.entities[entity_id] = {
            "name": entity.name,
            "file": path.stem,
            "fields": sorted(keys),
            "authored_modifier": authored,
        }
    return plan


def write_planned_assets(plan: WritebackPlan) -> None:
    """
    Write every file of `plan`.

    Raises:
        OSError: If a file cannot be written. The files already written are restored.
    """
    apply_changes(plan.files)
