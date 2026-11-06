# -*- coding: utf-8 -*-

"""SessionRuntime player rules: permissions, vision, visible tokens, public views and stat checks."""

from __future__ import annotations

from typing import Any, Sequence

from rpg_tabletop.assets.map import (
    MapAsset,
    Vec2,
)
from rpg_tabletop.core.dice import DiceCheck
from rpg_tabletop.core.rules import (
    DISCLOSABLE_FIELDS,
    PLAYER_STAT_NAMES,
    RESOURCE_BAR_STATS,
    RuntimePermissions,
    grid_cell,
    vision_cells_from_per,
)
from rpg_tabletop.session.models import (
    SessionEvent,
    SessionEventType,
    SessionToken,
)
from rpg_tabletop.session.targeting import map_context, sees


class PlayerRulesMixin:
    """What a player may see and do. Mixed into SessionRuntime."""

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
