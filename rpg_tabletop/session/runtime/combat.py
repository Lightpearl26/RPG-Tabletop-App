# -*- coding: utf-8 -*-

"""SessionRuntime combat: initiative, turns, movement and action budgets."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from rpg_tabletop.core.rules import (
    SHOOT_STAMINA_COST,
    STRIKE_STAMINA_COST,
    movement_cells_from_agi,
)
from rpg_tabletop.session.initiative import check_roll, insertion_index
from rpg_tabletop.session.initiative import initiative_order as sort_by_initiative
from rpg_tabletop.session.models import (
    CombatSession,
    SessionEvent,
    SessionEventType,
    TurnState,
)


class CombatMixin:
    """Combat, turns and budgets. Mixed into SessionRuntime."""

    # ------------------------------------------------------------- combat/log

    def _turn_state_for(self, entity_id: str) -> TurnState:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        movement = movement_cells_from_agi(entity.agi)
        stamina = max(0, int(entity.stamina))
        return TurnState(
            entity_id=str(entity_id),
            movement_max=movement,
            movement_remaining=movement,
            stamina_max=stamina,
            stamina_remaining=stamina,
        )

    def _initiative_helpers(self) -> tuple[Any, Any]:
        def agility(entity_id: str) -> int:
            entity = self.entities.get(entity_id)
            return int(entity.get_stat("agi")) if entity is not None else 0

        def name(entity_id: str) -> str:
            entity = self.entities.get(entity_id)
            return entity.name if entity is not None else entity_id

        return agility, name

    def start_combat_with_initiative(self, rolls: dict[str, int]) -> SessionEvent:
        """
        Start a combat from d20 results: the lowest acts first; ties go to the
        higher AGI, then to the name.
        """
        rolls = {str(entity_id): check_roll(value) for entity_id, value in rolls.items()}
        order = sort_by_initiative(rolls, *self._initiative_helpers())
        return self.start_combat(order, initiative_order=order, initiative_rolls=rolls)

    def start_combat(
        self,
        participant_ids: Sequence[str],
        *,
        initiative_order: Optional[Sequence[str]] = None,
        initiative_rolls: Optional[dict[str, int]] = None,
    ) -> SessionEvent:
        if self.combat is not None:
            raise RuntimeError("A combat is already active.")
        participants = [str(item) for item in participant_ids]
        if not participants:
            raise ValueError("Combat requires at least one participant.")
        if len(set(participants)) != len(participants):
            raise ValueError("Combat participants must be unique.")
        unknown = [item for item in participants if item not in self.entities]
        if unknown:
            raise KeyError(f"Unknown combat participant(s): {', '.join(unknown)}")

        order = [str(item) for item in (initiative_order or participants)]
        if set(order) != set(participants) or len(order) != len(participants):
            raise ValueError("Initiative order must contain every participant exactly once.")

        current_id = order[0]
        rolls = {str(key): int(value) for key, value in (initiative_rolls or {}).items()}
        combat = CombatSession(
            participant_ids=participants,
            initiative_order=order,
            round=1,
            turn_index=0,
            turn_state=self._turn_state_for(current_id),
            initiative_rolls={key: value for key, value in rolls.items() if key in participants},
        )
        message = f"Combat started - turn: {self.entities[current_id].name}"
        if combat.initiative_rolls:
            message += " - initiative: " + ", ".join(
                f"{self.entities[entity_id].name} {combat.initiative_rolls[entity_id]}"
                for entity_id in order if entity_id in combat.initiative_rolls
            )
        return self._dispatch(
            SessionEventType.COMBAT_STARTED,
            {"combat": combat.to_dict()},
            message=message,
            source_id=current_id,
        )

    def add_combat_participant(self, entity_id: str, roll: int) -> SessionEvent:
        """
        Bring an entity into the running combat at the place its d20 gives it.

        Placed after the current turn, it plays this round; before, next round.
        """
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        entity_id = str(entity_id)
        entity = self._require_entity(entity_id)
        if entity_id in self.combat.participant_ids:
            raise ValueError(f"{entity.name} already takes part in the combat.")
        roll = check_roll(roll)
        updated = CombatSession.from_dict(self.combat.to_dict())
        index = insertion_index(
            updated.initiative_order, updated.initiative_rolls, entity_id, roll,
            *self._initiative_helpers(),
        )
        updated.initiative_order.insert(index, entity_id)
        updated.participant_ids.append(entity_id)
        updated.initiative_rolls[entity_id] = roll
        if index <= updated.turn_index:
            updated.turn_index += 1  # the current entity keeps its turn
        return self._dispatch(
            SessionEventType.COMBAT_PARTICIPANT_ADDED,
            {"combat": updated.to_dict(), "entity_id": entity_id, "roll": roll},
            message=f"{entity.name} joins the combat (initiative {roll})",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def remove_combat_participant(self, entity_id: str) -> SessionEvent:
        """Take an entity out of the running combat (not the one whose turn it is)."""
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        entity_id = str(entity_id)
        if entity_id not in self.combat.participant_ids:
            raise KeyError(f"Not in the combat: {entity_id}")
        if entity_id == self.combat.current_entity_id:
            raise ValueError("End this entity's turn before taking it out of the combat.")
        if len(self.combat.initiative_order) <= 1:
            raise ValueError("End the combat instead: it would have no participant left.")
        updated = CombatSession.from_dict(self.combat.to_dict())
        index = updated.initiative_order.index(entity_id)
        updated.initiative_order.remove(entity_id)
        updated.participant_ids.remove(entity_id)
        updated.initiative_rolls.pop(entity_id, None)
        if index < updated.turn_index:
            updated.turn_index -= 1
        entity = self.entities.get(entity_id)
        return self._dispatch(
            SessionEventType.COMBAT_PARTICIPANT_REMOVED,
            {"combat": updated.to_dict(), "entity_id": entity_id},
            message=f"{entity.name if entity else entity_id} leaves the combat",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def end_combat(self) -> SessionEvent:
        if self.combat is None:
            raise RuntimeError("No combat is active.")
        previous = self.combat.to_dict()
        return self._dispatch(
            SessionEventType.COMBAT_ENDED,
            {"combat_before": previous},
            message="Combat ended",
        )

    def next_turn(self) -> tuple[SessionEvent, SessionEvent]:
        if self.combat is None or not self.combat.initiative_order:
            raise RuntimeError("No combat is active.")
        current_id = self.combat.current_entity_id
        ended = self._dispatch(
            SessionEventType.TURN_ENDED,
            {
                "entity_id": current_id,
                "round": self.combat.round,
                "turn_index": self.combat.turn_index,
            },
            message=(
                f"Turn ended: {self.entities[current_id].name}"
                if current_id in self.entities else "Turn ended"
            ),
            source_id=current_id,
        )

        updated = CombatSession.from_dict(self.combat.to_dict())
        next_index = updated.turn_index + 1
        if next_index >= len(updated.initiative_order):
            next_index = 0
            updated.round += 1
        updated.turn_index = next_index
        next_id = updated.current_entity_id
        if next_id is None:
            raise RuntimeError("Combat initiative order is empty.")
        updated.turn_state = self._turn_state_for(next_id)

        started = self._dispatch(
            SessionEventType.TURN_STARTED,
            {"combat": updated.to_dict()},
            message=f"Round {updated.round} - turn: {self.entities[next_id].name}",
            source_id=next_id,
        )
        return ended, started

    def action_stamina_remaining(self, entity_id: str) -> int:
        if self.combat is None or self.combat.turn_state is None:
            return 0
        if self.combat.current_entity_id != str(entity_id):
            return 0
        return self.combat.turn_state.stamina_remaining

    def movement_remaining(self, entity_id: str) -> int:
        if self.combat is None or self.combat.turn_state is None:
            return 0
        if self.combat.current_entity_id != str(entity_id):
            return 0
        return self.combat.turn_state.movement_remaining

    def spend_action_stamina(
        self,
        entity_id: str,
        cost: int,
        *,
        action: str,
    ) -> SessionEvent:
        entity_id = str(entity_id)
        if not self.player_permissions(entity_id).can_combat_actions:
            raise PermissionError("Combat actions are only available during the entity's turn.")
        if self.combat is None or self.combat.turn_state is None:
            raise RuntimeError("Combat turn state is not initialized.")
        cost = max(0, int(cost))
        before = self.combat.turn_state.stamina_remaining
        if cost > before:
            raise ValueError(
                f"Not enough stamina for {action}: {cost} required, {before} remaining."
            )
        after = before - cost
        return self._dispatch(
            SessionEventType.ACTION_STAMINA_SPENT,
            {
                "entity_id": entity_id,
                "action": str(action),
                "cost": cost,
                "before": before,
                "after": after,
            },
            message=f"{self.entities[entity_id].name} uses {cost} stamina for {action}",
            source_id=entity_id,
            target_ids=(entity_id,),
        )

    def spend_strike_stamina(self, entity_id: str) -> SessionEvent:
        return self.spend_action_stamina(
            entity_id, STRIKE_STAMINA_COST, action="strike"
        )

    def spend_shoot_stamina(self, entity_id: str) -> SessionEvent:
        return self.spend_action_stamina(
            entity_id, SHOOT_STAMINA_COST, action="shoot"
        )

    def spend_spell_stamina(self, entity_id: str, spell_name: str) -> SessionEvent:
        entity = self.entities.get(str(entity_id))
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        spell = entity.get_spell(spell_name)
        if spell is None:
            raise KeyError(f"Entity {entity.name!r} does not know spell {spell_name!r}.")
        return self.spend_action_stamina(
            entity_id, int(spell.cost), action=f"spell:{spell.name}"
        )

    def available_actions(self, entity_id: str) -> dict[str, Any]:
        entity_id = str(entity_id)
        entity = self.entities.get(entity_id)
        if entity is None:
            raise KeyError(f"Unknown entity: {entity_id}")
        permissions = self.player_permissions(entity_id)
        stamina = self.action_stamina_remaining(entity_id)
        return {
            "combat_actions_enabled": permissions.can_combat_actions,
            "stamina_remaining": stamina,
            "strike": {
                "cost": STRIKE_STAMINA_COST,
                "available": permissions.can_combat_actions and stamina >= STRIKE_STAMINA_COST,
            },
            "shoot": {
                "cost": SHOOT_STAMINA_COST,
                "available": permissions.can_combat_actions and stamina >= SHOOT_STAMINA_COST,
            },
            "spells": {
                name: {
                    "cost": int(spell.cost),
                    "available": permissions.can_combat_actions and stamina >= int(spell.cost),
                }
                for name, spell in entity.spells.items()
            },
        }

    def set_combat(self, combat: Optional[CombatSession]) -> SessionEvent:
        """Legacy low-level setter kept for V1 callers; prefer start_combat/end_combat."""
        return self._dispatch(
            SessionEventType.COMBAT_SET,
            {"combat": combat.to_dict() if combat is not None else None},
            message="Combat state changed",
        )
