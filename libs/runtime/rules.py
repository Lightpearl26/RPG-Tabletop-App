# -*- coding: utf-8 -*-

"""Pure runtime rules shared by the GM runtime and the player runtime."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any


STRIKE_STAMINA_COST = 60
SHOOT_STAMINA_COST = 60

BASE_VISION_CELLS = 5
PERCEPTION_POINTS_PER_VISION_CELL = 10

BASE_MOVEMENT_CELLS = 3
AGILITY_POINTS_PER_MOVEMENT_CELL = 15


def vision_cells_from_per(perception: int) -> int:
    """Player field-of-view radius in grid cells (legacy V1 rule)."""
    return BASE_VISION_CELLS + max(0, int(perception)) // PERCEPTION_POINTS_PER_VISION_CELL


def movement_cells_from_agi(agility: int) -> int:
    """Movement budget refreshed at the beginning of each combat turn."""
    return BASE_MOVEMENT_CELLS + max(0, int(agility)) // AGILITY_POINTS_PER_MOVEMENT_CELL


def chebyshev_distance(
    first: tuple[int, int],
    second: tuple[int, int],
) -> int:
    """8-neighbour grid distance: orthogonal and diagonal steps both cost 1."""
    return max(abs(second[0] - first[0]), abs(second[1] - first[1]))


def grid_cell(
    point: Any,
    *,
    cell_size: int,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> tuple[int, int]:
    """Convert a map point (with x/y attributes) into integer grid coordinates."""
    if cell_size <= 0:
        raise ValueError("Grid cell size must be positive.")
    size = float(cell_size)
    return (
        floor((float(point.x) - float(offset_x)) / size),
        floor((float(point.y) - float(offset_y)) / size),
    )


@dataclass(frozen=True, slots=True)
class RuntimePermissions:
    """Player capabilities for the current runtime context."""

    can_move: bool
    movement_limited: bool
    can_inspect_visible_tokens: bool
    can_stat_check: bool
    can_combat_actions: bool

    @property
    def in_active_turn(self) -> bool:
        return self.can_combat_actions
