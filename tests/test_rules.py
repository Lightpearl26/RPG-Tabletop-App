from types import SimpleNamespace

import pytest

from rpg_tabletop.core import rules, stats
from rpg_tabletop.session import initiative


def test_stat_lists():
    assert len(stats.CORE_STATS) == 10
    assert stats.PUBLIC_RESOURCE_STATS == ("hp", "stamina")
    assert rules.PLAYER_STAT_NAMES == ("hp", "stamina", *stats.CORE_STATS)
    assert not set(rules.PLAYER_STAT_NAMES) & stats.PRIVATE_STATS
    assert rules.DISCLOSABLE_FIELDS[0] == "name"


def test_movement_and_vision_budgets():
    assert rules.movement_cells_from_agi(0) == rules.BASE_MOVEMENT_CELLS
    assert rules.movement_cells_from_agi(-50) == rules.BASE_MOVEMENT_CELLS
    assert rules.movement_cells_from_agi(rules.AGILITY_POINTS_PER_MOVEMENT_CELL * 2) == (
        rules.BASE_MOVEMENT_CELLS + 2
    )
    assert rules.vision_cells_from_per(rules.PERCEPTION_POINTS_PER_VISION_CELL) == (
        rules.BASE_VISION_CELLS + 1
    )


def test_grid_helpers():
    assert rules.chebyshev_distance((0, 0), (3, -2)) == 3
    point = SimpleNamespace(x=105.0, y=-1.0)
    assert rules.grid_cell(point, cell_size=50) == (2, -1)
    assert rules.grid_cell(point, cell_size=50, offset_x=10, offset_y=-1) == (1, 0)
    with pytest.raises(ValueError):
        rules.grid_cell(point, cell_size=0)


def test_initiative_order_and_insertion():
    agility = {"a": 10, "b": 30, "c": 30}.get
    name = {"a": "Alban", "b": "Bérénice", "c": "Chloé"}.get
    rolls = {"a": 5, "b": 12, "c": 12}
    order = initiative.initiative_order(rolls, agility, name)
    assert order == ["a", "b", "c"]
    assert initiative.insertion_index(order, rolls, "d", 1, lambda _: 0, lambda _: "Dexter") == 0
    assert initiative.insertion_index(order, rolls, "d", 20, lambda _: 0, lambda _: "Dexter") == 3


@pytest.mark.parametrize("value", [0, 21, "abc", 2.5, None])
def test_check_roll_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        initiative.check_roll(value)
