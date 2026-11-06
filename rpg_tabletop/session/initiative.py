# -*- coding: utf-8 -*-

"""
Combat initiative: a d20 per participant, lowest result acts first.

`InitiativeRoster` is what the GM builds before (or during) a combat: entities
are added one at a time, each one gets a d20 (rolled automatically for
non-player entities, asked to the player otherwise) and the combat order is the
ascending order of the results. Ties go to the higher AGI, then to the name.

Nothing here touches the network or the GUI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping, Optional

INITIATIVE_DIE = 20


class RollSource(str, Enum):
    """Who produced an initiative result."""

    AUTO = "auto"      # rolled by the app for a non-player entity
    PLAYER = "player"  # sent by the player
    GM = "gm"          # typed or rerolled by the GM


@dataclass
class InitiativeEntry:
    entity_id: str
    roll: Optional[int] = None
    source: Optional[RollSource] = None
    # Player asked to roll and not answered yet
    waiting_for: Optional[str] = None

    @property
    def rolled(self) -> bool:
        return self.roll is not None


def roll_d20(rng: Optional[random.Random] = None) -> int:
    return (rng or random).randint(1, INITIATIVE_DIE)


def check_roll(value: object) -> int:
    """
    Validate an initiative result.

    Raises:
        ValueError: If it is not an integer from 1 to 20.
    """
    try:
        roll = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("The initiative must be a whole number.") from error
    if not 1 <= roll <= INITIATIVE_DIE or str(value).strip() not in (str(roll), f"{roll}.0"):
        raise ValueError(f"The initiative must be between 1 and {INITIATIVE_DIE}.")
    return roll


def initiative_key(
    entity_id: str,
    roll: int,
    agility: Callable[[str], int],
    name: Callable[[str], str],
) -> tuple[int, int, str, str]:
    """Sort key: lowest roll first, then highest AGI, then name."""
    return int(roll), -int(agility(entity_id)), name(entity_id).casefold(), entity_id


def initiative_order(
    rolls: Mapping[str, int],
    agility: Callable[[str], int],
    name: Callable[[str], str],
) -> list[str]:
    """Entity ids in turn order."""
    return sorted(rolls, key=lambda entity_id: initiative_key(entity_id, rolls[entity_id], agility, name))


def insertion_index(
    order: list[str],
    rolls: Mapping[str, int],
    entity_id: str,
    roll: int,
    agility: Callable[[str], int],
    name: Callable[[str], str],
) -> int:
    """
    Where a newcomer goes in a running combat order.

    Participants without a known roll (combat started before rolls existed)
    are kept in place: the newcomer goes before the first one it beats.
    """
    key = initiative_key(entity_id, roll, agility, name)
    for index, current in enumerate(order):
        if current in rolls and key < initiative_key(current, rolls[current], agility, name):
            return index
    return len(order)


class InitiativeRoster:
    """The GM's list of combatants and their d20."""

    def __init__(self) -> None:
        self.entries: list[InitiativeEntry] = []
        # Once rolling started, every newcomer rolls on arrival
        self.rolling = False

    # ---------------------------------------------------------------- content
    def __contains__(self, entity_id: object) -> bool:
        return any(entry.entity_id == entity_id for entry in self.entries)

    def entry(self, entity_id: str) -> Optional[InitiativeEntry]:
        return next((entry for entry in self.entries if entry.entity_id == entity_id), None)

    def ids(self) -> list[str]:
        return [entry.entity_id for entry in self.entries]

    def add(self, entity_id: str) -> InitiativeEntry:
        if entity_id in self:
            raise ValueError("This entity is already in the initiative.")
        entry = InitiativeEntry(str(entity_id))
        self.entries.append(entry)
        return entry

    def remove(self, entity_id: str) -> Optional[InitiativeEntry]:
        entry = self.entry(entity_id)
        if entry is not None:
            self.entries.remove(entry)
        return entry

    def keep_only(self, entity_ids: Iterable[str]) -> None:
        """Drop the entries of entities no longer in the session."""
        alive = set(entity_ids)
        self.entries = [entry for entry in self.entries if entry.entity_id in alive]

    def clear(self) -> None:
        self.entries.clear()
        self.rolling = False

    # ------------------------------------------------------------------ rolls
    def set_roll(self, entity_id: str, value: object, source: RollSource) -> InitiativeEntry:
        entry = self.entry(entity_id)
        if entry is None:
            raise KeyError(f"Not in the initiative: {entity_id}")
        entry.roll = check_roll(value)
        entry.source = RollSource(source)
        entry.waiting_for = None
        return entry

    def wait_for(self, entity_id: str, username: str) -> None:
        entry = self.entry(entity_id)
        if entry is None:
            raise KeyError(f"Not in the initiative: {entity_id}")
        entry.roll, entry.source, entry.waiting_for = None, None, str(username)

    def reset_rolls(self) -> list[tuple[str, str]]:
        """Forget every result; returns (entity id, username) of the requests withdrawn."""
        waiting = [(entry.entity_id, str(entry.waiting_for)) for entry in self.entries if entry.waiting_for]
        for entry in self.entries:
            entry.roll, entry.source, entry.waiting_for = None, None, None
        self.rolling = False
        return waiting

    def missing(self) -> list[str]:
        return [entry.entity_id for entry in self.entries if not entry.rolled]

    @property
    def complete(self) -> bool:
        return bool(self.entries) and not self.missing()

    def rolls(self) -> dict[str, int]:
        return {entry.entity_id: int(entry.roll) for entry in self.entries if entry.roll is not None}

    def order(self, agility: Callable[[str], int], name: Callable[[str], str]) -> list[str]:
        """Rolled entities in turn order, then the ones still waiting, as added."""
        rolled = initiative_order(self.rolls(), agility, name)
        return rolled + self.missing()
