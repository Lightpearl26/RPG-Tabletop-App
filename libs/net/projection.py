# -*- coding: utf-8 -*-
"""Player-safe projections of SessionRuntime.

Never send SessionRuntime state directly to a player.  Every player-facing sync
must be built through this module so hidden tokens and undisclosed entity fields
stay server-side.
"""

from __future__ import annotations

from typing import Any

from ..runtime.rules import PLAYER_STAT_NAMES, PRIVATE_STATS, RESOURCE_BAR_STATS
from ..runtime.session import SessionRuntime, SessionToken
from ..assets.item import Item


STAT_NAMES = PLAYER_STAT_NAMES  # mental_health / drug_health are GM-only


def _items_view(entity) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, quantity in entity.items.items.items():
        item = entity.items._item_cache.get(name)
        if item is None:
            try:
                item = Item.from_name(name)
            except (OSError, KeyError, ValueError):
                item = None
        modifiers: list[list[Any]] = []
        description = ""
        if item is not None:
            description = item.description
            for entry in item.modifier:
                try:
                    stat, amount = entry
                    if str(stat) not in PRIVATE_STATS:  # GM-only stats never leak
                        modifiers.append([str(stat), int(amount)])
                except (TypeError, ValueError):
                    continue
        result.append({
            "name": name,
            "quantity": int(quantity),
            "description": description,
            "modifier": modifiers,
        })
    return result


def _spells_view(entity) -> dict[str, Any]:
    return {
        name: {
            "cost": int(spell.cost),
            "targeting": str(spell.targeting),
            "description": spell.description,
            "policy": spell.runtime_policy.value,
        }
        for name, spell in entity.spells.items()
    }

def _event_view(spell_event) -> dict[str, Any]:
    data = spell_event.to_dict()
    effects = data.get("effects")
    if isinstance(effects, list):
        data["effects"] = [
            effect for effect in effects
            if not (isinstance(effect, dict) and effect.get("target_stat") in PRIVATE_STATS)
        ]
    return data


def _own_entity_view(runtime: SessionRuntime, entity_id: str) -> dict[str, Any]:
    entity = runtime.entities[str(entity_id)]
    stats = {name: int(entity.get_stat(name)) for name in STAT_NAMES}
    stats_max = {name: int(entity.get_stat_max(name)) for name in RESOURCE_BAR_STATS}
    return {
        "id": str(entity.id),
        "name": entity.name,
        "character": entity.character.name,
        "stats": stats,
        "stats_max": stats_max,
        "items": _items_view(entity),
        "spells": _spells_view(entity),
        "spell_effects": [
            effect.to_dict() for effect in entity.spell_effects
            if effect.target_stat not in PRIVATE_STATS
        ],
        "spell_events": [_event_view(event) for event in entity.spell_events],
    }


def _token_view(runtime: SessionRuntime, observer_id: str, token: SessionToken) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    try:
        summary = runtime.public_entity_view(observer_id, token.id)
    except (KeyError, PermissionError):
        summary = {}
    entity = runtime.entities[str(token.entity_id)]
    if entity.token:
        with entity.token.open("rb") as file:
            raw = file.read().hex()
    else:
        raw = None
    return {
        "id": token.id,
        "entity_id": token.entity_id,
        "pos": token.pos.to_list(),
        "size_cells": token.size_cells,
        "rotation": token.rotation,
        "opacity": token.opacity,
        "self": token.entity_id == observer_id,
        "public": summary,
        "image": raw
    }


def _map_view(runtime: SessionRuntime, entity_id: str) -> dict[str, Any] | None:
    if runtime.active_map_id is None or runtime.active_map is None:
        return None

    asset = runtime._active_map_asset()  # projection is trusted server-side code
    own_token = runtime.active_map.token_for_entity(entity_id)
    fov_origin = own_token.pos.to_list() if own_token is not None else None

    compiled = asset.compiled.to_dict() if asset.compiled is not None else None
    visible_tokens = runtime.visible_tokens_for(entity_id)
    return {
        "map_id": runtime.active_map_id,
        "asset": {
            "id": asset.id,
            "name": asset.name,
            "size": [asset.width, asset.height],
            "grid": asset.grid.to_dict(),
            "compiled": compiled,
        },
        "fov": {
            "radius_cells": runtime.vision_radius_cells(entity_id),
            "origin": fov_origin,
            "metric": "chebyshev",
        },
        "tokens": [_token_view(runtime, entity_id, token) for token in visible_tokens],
        # V2 currently treats runtime-added map elements as player-visible.
        # Add a visibility policy here later if GM-only runtime elements are needed.
        "elements": [element.to_dict() for element in runtime.active_map.elements],
    }


def _combat_view(runtime: SessionRuntime, entity_id: str) -> dict[str, Any]:
    """Expose only the three initiative entries that are safe for this player."""
    if runtime.combat is None:
        return {"active": False}

    combat = runtime.combat
    order = list(combat.initiative_order)
    if not order:
        return {"active": False}

    own_turn = combat.current_entity_id == entity_id
    current_index = max(0, min(len(order) - 1, int(combat.turn_index)))
    visible_tokens = []
    if runtime.active_map is not None:
        try:
            visible_tokens = runtime.visible_tokens_for(entity_id)
        except (KeyError, RuntimeError, ValueError):
            visible_tokens = []
    visible_by_entity = {token.entity_id: token for token in visible_tokens}

    def safe_entry(target_id: str, offset: int) -> dict[str, Any]:
        if target_id == entity_id:
            entity = runtime.entities.get(target_id)
            token = visible_by_entity.get(target_id)
            return {
                "id": token.id if token is not None else "self",
                "name": entity.name if entity is not None else "Toi",
                "visible": True,
                "self": True,
            }

        token = visible_by_entity.get(target_id)
        if token is None:
            return {"name": "???", "visible": False}

        try:
            public = runtime.public_entity_view(entity_id, token.id)
        except (KeyError, PermissionError):
            public = {}
        return {
            "id": token.id,
            "name": str(public.get("name") or "Token visible"),
            "visible": True,
            "self": False,
        }

    carousel = [
        safe_entry(order[(current_index + offset) % len(order)], offset)
        for offset in (-1, 0, 1)
    ]

    turn_state: dict[str, Any] | None = None
    if combat.turn_state is not None:
        raw_turn = combat.turn_state.to_dict()
        if own_turn:
            turn_state = raw_turn
            carousel[1].update({
                "stamina_remaining": raw_turn["stamina_remaining"],
                "stamina_max": raw_turn["stamina_max"],
                "movement_remaining": raw_turn["movement_remaining"],
                "movement_max": raw_turn["movement_max"],
            })
        elif carousel[1].get("visible"):
            current_token = visible_by_entity.get(combat.current_entity_id or "")
            public = {}
            if current_token is not None:
                try:
                    public = runtime.public_entity_view(entity_id, current_token.id)
                except (KeyError, PermissionError):
                    public = {}
            # Resource budgets are displayed off-turn only when the matching
            # stat was explicitly disclosed by the GM for this visible token.
            if "stamina" in public:
                carousel[1]["stamina_remaining"] = raw_turn["stamina_remaining"]
                carousel[1]["stamina_max"] = raw_turn["stamina_max"]
            if "agi" in public:
                carousel[1]["movement_remaining"] = raw_turn["movement_remaining"]
                carousel[1]["movement_max"] = raw_turn["movement_max"]

    return {
        "active": True,
        "round": combat.round,
        "turn_index": current_index,
        "own_turn": own_turn,
        "turn_state": turn_state,
        "carousel": carousel,
    }


def build_player_runtime_projection(
    runtime: SessionRuntime,
    entity_id: str,
) -> dict[str, Any]:
    """Build the complete player-visible state for one bound Entity."""
    entity_id = str(entity_id)
    if entity_id not in runtime.entities:
        raise KeyError(f"Unknown entity: {entity_id}")

    permissions = runtime.player_permissions(entity_id)
    return {
        "session": {
            "id": str(runtime.session.id),
            "name": runtime.session.name,
            "seq": runtime.session.current_seq,
        },
        "entity": _own_entity_view(runtime, entity_id),
        "permissions": {
            "can_move": permissions.can_move,
            "movement_limited": permissions.movement_limited,
            "can_inspect_visible_tokens": permissions.can_inspect_visible_tokens,
            "can_stat_check": permissions.can_stat_check,
            "can_combat_actions": permissions.can_combat_actions,
        },
        "combat": _combat_view(runtime, entity_id),
        "actions": runtime.available_actions(entity_id),
        "map": _map_view(runtime, entity_id),
    }
