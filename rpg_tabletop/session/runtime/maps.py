# -*- coding: utf-8 -*-

"""SessionRuntime map API: active map, tokens, map elements and the GM camera."""

from __future__ import annotations

from typing import Optional, Sequence

from rpg_tabletop.assets.map import (
    CameraState,
    MapAsset,
    MapElement,
    Vec2,
)
from rpg_tabletop.core.geometry import wall_between
from rpg_tabletop.core.rules import (
    DISCLOSABLE_FIELDS,
    chebyshev_distance,
)
from rpg_tabletop.session.models import (
    SessionEvent,
    SessionEventType,
    SessionMapState,
    SessionToken,
)
from rpg_tabletop.session.targeting import map_context


class MapMixin:
    """Active map, tokens and map elements. Mixed into SessionRuntime."""

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
