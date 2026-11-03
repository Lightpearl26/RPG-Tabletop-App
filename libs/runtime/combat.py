# -*- coding: utf-8 -*-

"""Combat/runtime rules shared by the GM and player applications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


STRIKE_STAMINA_COST = 60
SHOOT_STAMINA_COST = 60

# Gameplay balance lives here rather than in the UI.
AGI_PER_MOVEMENT_CELL = 10.0
BASE_VISION_CELLS = 5.0
PER_PER_VISION_CELL = 10


class CombatAction(str, Enum):
    STRIKE = "strike"
    SHOOT = "shoot"
    SPELL = "spell"


@dataclass(slots=True)
class TurnState:
    """Resources available to the entity whose combat turn is active."""

    entity_id: str
    movement_max: float
    movement_remaining: float
    stamina_max: int
    stamina_remaining: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "movement_max": self.movement_max,
            "movement_remaining": self.movement_remaining,
            "stamina_max": self.stamina_max,
            "stamina_remaining": self.stamina_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnState:
        movement_max = max(0.0, float(data.get("movement_max", 0.0)))
        stamina_max = max(0, int(data.get("stamina_max", 0)))
        return cls(
            entity_id=str(data["entity_id"]),
            movement_max=movement_max,
            movement_remaining=max(
                0.0,
                min(movement_max, float(data.get("movement_remaining", movement_max))),
            ),
            stamina_max=stamina_max,
            stamina_remaining=max(
                0,
                min(stamina_max, int(data.get("stamina_remaining", stamina_max))),
            ),
        )


@dataclass(slots=True)
class CombatSession:
    """Snapshotable state of an active combat."""

    participant_ids: list[str]
    initiative_order: list[str]
    round: int = 1
    turn_index: int = 0
    turn: Optional[TurnState] = None

    @property
    def current_entity_id(self) -> Optional[str]:
        if not self.initiative_order:
            return None
        return self.initiative_order[self.turn_index % len(self.initiative_order)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_ids": list(self.participant_ids),
            "initiative_order": list(self.initiative_order),
            "round": self.round,
            "turn_index": self.turn_index,
            "turn": self.turn.to_dict() if self.turn is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatSession:
        raw_turn = data.get("turn")
        return cls(
            participant_ids=[str(item) for item in data.get("participant_ids", [])],
            initiative_order=[str(item) for item in data.get("initiative_order", [])],
            round=max(1, int(data.get("round", 1))),
            turn_index=max(0, int(data.get("turn_index", 0))),
            turn=TurnState.from_dict(raw_turn) if raw_turn else None,
        )


@dataclass(frozen=True, slots=True)
class RuntimePermissions:
    """What a player-controlled entity may currently do."""

    can_move: bool
    movement_limited: bool
    can_inspect_visible_tokens: bool
    can_stat_check: bool
    can_combat_actions: bool


def movement_budget_from_agi(agi: int | float) -> float:
    """Combat movement budget in grid cells for one turn."""
    return max(0.0, float(agi)) / AGI_PER_MOVEMENT_CELL


def vision_radius_from_per(per: int | float) -> float:
    """Player field-of-view radius in grid cells."""
    return BASE_VISION_CELLS + max(0, int(per)) // PER_PER_VISION_CELL


def action_stamina_cost(
    action: CombatAction | str,
    *,
    spell_cost: Optional[int] = None,
) -> int:
    action = CombatAction(action)
    if action is CombatAction.STRIKE:
        return STRIKE_STAMINA_COST
    if action is CombatAction.SHOOT:
        return SHOOT_STAMINA_COST
    if spell_cost is None:
        raise ValueError("spell_cost is required for a spell action.")
    return max(0, int(spell_cost))


def permissions_for(
    combat: Optional[CombatSession],
    entity_id: str,
) -> RuntimePermissions:
    """Apply the agreed out-of-combat / off-turn / own-turn rules."""
    if combat is None:
        return RuntimePermissions(
            can_move=True,
            movement_limited=False,
            can_inspect_visible_tokens=True,
            can_stat_check=True,
            can_combat_actions=False,
        )

    own_turn = combat.current_entity_id == str(entity_id)
    return RuntimePermissions(
        can_move=own_turn,
        movement_limited=own_turn,
        can_inspect_visible_tokens=True,
        can_stat_check=True,
        can_combat_actions=own_turn,
    )
