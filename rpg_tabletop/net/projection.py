# -*- coding: utf-8 -*-
"""Player-safe projections of SessionRuntime.

Never send SessionRuntime state directly to a player.  Every player-facing sync
must be built through this module so hidden tokens and undisclosed entity fields
stay server-side.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from rpg_tabletop.core.geometry import vision_square
from rpg_tabletop.core.rules import PLAYER_STAT_NAMES, RESOURCE_BAR_STATS
from rpg_tabletop.core.stats import PRIVATE_STATS
from rpg_tabletop.session.models import SessionToken
from rpg_tabletop.session.runtime import SessionRuntime
from rpg_tabletop.session.targeting import targeting_options
from rpg_tabletop.assets.item import Item


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
    # Name, cost, targeting, policy, delay and which stats each effect touches:
    # never the formulas.
    return {name: spell.to_public_dict() for name, spell in entity.spells.items()}


def _effect_view(effect: dict[str, Any], spell_names: dict[str, str]) -> dict[str, Any]:
    """
    One active effect as the player sees it: no formula (`effect_def`), and the
    spell it comes from only when the player cast it.
    """
    data = {key: value for key, value in effect.items() if key != "effect_def"}
    event_id = effect.get("event_id")
    data["source"] = spell_names.get(str(event_id), "Sort") if event_id else ""
    return data


def _event_view(spell_event, spell_names: dict[str, str]) -> dict[str, Any]:
    data = spell_event.to_dict()
    effects = data.get("effects")
    if isinstance(effects, list):
        data["effects"] = [
            _effect_view(effect, spell_names) for effect in effects
            if isinstance(effect, dict) and effect.get("target_stat") not in PRIVATE_STATS
        ]
    return data


def _own_entity_view(runtime: SessionRuntime, entity_id: str) -> dict[str, Any]:
    entity = runtime.entities[str(entity_id)]
    stats = {name: int(entity.get_stat(name)) for name in STAT_NAMES}
    stats_max = {name: int(entity.get_stat_max(name)) for name in RESOURCE_BAR_STATS}
    # Spells the player cast: their effects may say which spell they come from
    spell_names = {str(event.id): event.spell_id for event in entity.spell_events}
    return {
        "id": str(entity.id),
        "name": entity.name,
        "character": entity.character.name,
        "level": int(entity.character.stats.level),
        "stats": stats,
        "stats_max": stats_max,
        # [base, modifier, items, effects] of each stat, as the GM sheet shows it
        "stats_detail": {name: list(entity.stat_breakdown(name)) for name in STAT_NAMES},
        "damage_reduction": int(entity.damage_reduction),
        "damage_amplification": int(entity.damage_amplification),
        "items": _items_view(entity),
        "spells": _spells_view(entity),
        "spell_effects": [
            _effect_view(effect.to_dict(), spell_names) for effect in entity.spell_effects
            if effect.target_stat not in PRIVATE_STATS
        ],
        "spell_events": [_event_view(event, spell_names) for event in entity.spell_events],
    }


# path -> (mtime_ns, size, image id): a sync is built after every change and for
# every player, so a picture is hashed again only when its file changes.
_TOKEN_IMAGES: dict[str, tuple[int, int, str]] = {}


def token_image_id(path: Any) -> str | None:
    """SHA-256 of a token picture (what the player asks for), None without one."""
    if not path:
        return None
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _TOKEN_IMAGES.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    try:
        image_id = sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    _TOKEN_IMAGES[key] = (stat.st_mtime_ns, stat.st_size, image_id)
    return image_id


def compiled_version(compiled) -> str:
    """Changes whenever any tile may have changed (pixels, tiling, area)."""
    return (
        f"{compiled.source_hash}:{compiled.tile_size}:{compiled.tile_format}:"
        f"{compiled.width}x{compiled.height}:{compiled.origin_x},{compiled.origin_y}"
    )


def player_view_box(runtime: SessionRuntime, entity_id: str):
    """
    What a player may see of the GM's current map.

    Returns False when the entity has no token on it (nothing visible), None
    when there is no field of view (no grid: the whole map), otherwise the
    (left, top, right, bottom) square of the field of view in pixels.
    """
    state = runtime.active_map
    if state is None or runtime.active_map_id is None:
        return False
    own_token = state.token_for_entity(str(entity_id))
    if own_token is None:
        return False
    grid = runtime._active_map_asset().grid
    radius = runtime.vision_radius_cells(entity_id)
    if not grid.enabled or grid.cell_size <= 0 or radius <= 0:
        return None
    return vision_square(
        float(grid.cell_size), float(grid.offset_x), float(grid.offset_y),
        own_token.pos.x, own_token.pos.y, int(radius),
    )


def element_visible(element, box) -> bool:
    if box is None:
        return True
    if box is False:
        return False
    bounds = element.bounds()
    return (
        bounds.x < box[2] and bounds.x + bounds.width > box[0]
        and bounds.y < box[3] and bounds.y + bounds.height > box[1]
    )


def _token_view(runtime: SessionRuntime, observer_id: str, token: SessionToken) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    try:
        summary = runtime.public_entity_view(observer_id, token.id)
    except (KeyError, PermissionError):
        summary = {}
    entity = runtime.entities[str(token.entity_id)]
    return {
        "id": token.id,
        "entity_id": token.entity_id,
        "pos": token.pos.to_list(),
        "size_cells": token.size_cells,
        "rotation": token.rotation,
        "opacity": token.opacity,
        "self": token.entity_id == observer_id,
        "public": summary,
        # The picture itself is downloaded on demand: ASSET_REQUEST "token:<id>"
        "image_id": token_image_id(entity.token),
    }


def _map_view(runtime: SessionRuntime, entity_id: str) -> dict[str, Any] | None:
    """
    The structure of the GM's current map, never the tiles themselves.

    The player works out which tiles its field of view needs, takes those it
    has from its cache and asks the server for the others (net.asset_service).
    """
    if runtime.active_map_id is None or runtime.active_map is None:
        return None

    asset = runtime._active_map_asset()  # projection is trusted server-side code
    box = player_view_box(runtime, entity_id)
    structure = {
        "id": asset.id,
        "name": asset.name,
        "size": [asset.width, asset.height],
        "origin": [asset.origin_x, asset.origin_y],
        "grid": asset.grid.to_dict(),
        "compiled": None,
    }
    if box is False:
        # The GM's current map is the only one that counts. A player whose
        # entity has no token on it sees nothing of it. The token keeps its
        # position on the other maps, stored in the session journal.
        return {
            "map_id": runtime.active_map_id, "present": False, "asset": structure,
            "fov": None, "tokens": [], "elements": [],
        }

    compiled = asset.compiled
    if compiled is not None:
        structure["compiled"] = {
            "version": compiled_version(compiled),
            "size": [compiled.width, compiled.height],
            "origin": [compiled.origin_x, compiled.origin_y],
            "tile_size": compiled.tile_size,
            "format": compiled.tile_format,
        }
    own_token = runtime.active_map.token_for_entity(entity_id)
    visible_tokens = runtime.visible_tokens_for(entity_id)
    return {
        "map_id": runtime.active_map_id,
        "present": True,
        "asset": structure,
        "fov": {
            "radius_cells": runtime.vision_radius_cells(entity_id),
            "origin": own_token.pos.to_list(),
            "metric": "chebyshev",
        },
        "tokens": [_token_view(runtime, entity_id, token) for token in visible_tokens],
        # Annotations of the session, only those inside the field of view
        "elements": [
            element.to_dict() for element in runtime.active_map.elements
            if getattr(element, "visible", True) and element_visible(element, box)
        ],
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

    def safe_entry(target_id: str, _: int) -> dict[str, Any]:
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
        # Tokens each action may target now (only tokens this player sees)
        "targeting": targeting_options(runtime, entity_id).to_dict(),
    }
