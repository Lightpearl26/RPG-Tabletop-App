#-*- coding: utf-8 -*-

"""
This module provides a dice object for simulating dice rolls in tabletop RPG games.
"""

# import built-in modules
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from random import randint


if TYPE_CHECKING:
    from .runtime.entity import Entity


# Create a Dice class for simulating dice rolls
class Dice:
    """
    A class representing a dice for simulating dice rolls in tabletop RPG games.

    Arguments
    ---------
    cmd : str
        The dice command string (e.g., "2d6" for rolling two six-sided dice).
    dice_values : list[int]
        The list of individual dice values resulting from the roll.
    """
    def __init__(self, cmd: str, dice_values: list[int]):
        self.cmd = cmd
        self.dice_type = int(cmd.split('d')[1])
        self.num_dice = int(cmd.split('d')[0])
        self.dice_values = dice_values

    # properties
    @property
    def total(self) -> int:
        """
        Returns the total sum of all dice values.
        """
        return sum(self.dice_values)

    @property
    def critical_success(self) -> bool:
        """
        Returns True if any dice of the patch is a critical success
        (i.e., has 5% or less of the dice type value).
        """
        return any(value <= self.dice_type * 0.05 for value in self.dice_values)

    @property
    def critical_failure(self) -> bool:
        """
        Returns True if any dice of the patch is a critical failure
        (i.e., has 95% or more of the dice type value).
        """
        return any(value >= self.dice_type * 0.95 for value in self.dice_values)

    @property
    def critical(self) -> bool:
        """
        Returns True if any dice of the patch is critical (either success or failure).
        """
        return self.critical_success or self.critical_failure

    # class methods
    @classmethod
    def roll(cls, cmd: str) -> Dice:
        """
        Rolls the dice based on the given command string and returns a Dice object.

        Arguments
        ---------
        cmd : str
            The dice command string (e.g., "2d6" for rolling two six-sided dice).

        Returns
        -------
        Dice
            A Dice object representing the result of the roll.
        """
        try:
            num_dice = int(cmd.split('d')[0])
            dice_type = int(cmd.split('d')[1])
            dice_values = [randint(1, dice_type) for _ in range(num_dice)]
            return cls(cmd, dice_values)
        except (IndexError, ValueError) as e:
            raise ValueError(f"Invalid dice command: {cmd}") from e


# Create a DiceCheck class for handling a stat check dice roll
class DiceCheck:
    """
    A class representing a dice check for handling stat check dice rolls in tabletop RPG games.

    Arguments
    ---------
    entity : Entity
        The entity performing the dice check.
    stat : str
        The stat being checked (e.g., "str", "dex").
    dice : Dice
        The Dice object representing the result of the roll.
    """
    def __init__(self, entity: Entity, stat: str, dice: Dice):
        self.entity = entity
        self.stat = stat
        self.dice = dice

    # properties
    @property
    def success(self) -> bool:
        """
        Returns True if the dice check is successful based on the entity's stat and the dice roll.
        """
        stat_value = self.entity.get_stat(self.stat)
        return (not self.dice.critical_failure) and (self.dice.total <= stat_value)

    # class methods
    @classmethod
    def resolve(cls, entity: Entity, stat: str) -> DiceCheck:
        """
        Resolves a dice check for the given entity and stat by rolling
        the appropriate dice and returning a DiceCheck object.
        """
        dice = Dice.roll("1d100")
        return cls(entity, stat, dice)


# Create a DiceRatio class for handling ratio-based dice rolls
class DiceRatio:
    """
    A class representing a ratio-based dice roll.

    Arguments
    ---------
    stat : str
        The stat being used for the ratio-based dice roll (e.g., "str", "dex").
    ratio : int
        The ratio value to be applied to the stat. (in percentage)
    """
    def __init__(self, stat: str, ratio: int):
        self.stat = stat
        self.ratio = ratio

    # methods
    def resolve(self, entity: Entity, dice: Optional[Dice] = None) -> int:
        """
        Resolves the ratio-based dice roll for the given entity and
        returns the result as an integer.

        Arguments
        ---------
        entity : Entity
            The entity performing the ratio-based dice roll.
        dice : Optional[Dice]
            The Dice object representing the result of the roll.
            If None, a new "1d100" roll will be used.

        Returns
        -------
        int
            The result of the ratio-based dice roll.
        """
        if dice is None:
            dice = Dice.roll("1d100")
        stat_value = entity.get_stat(self.stat)
        result = (stat_value - dice.total) * self.ratio // 100
        return int(result)


# Create a DiceAttack class for handling attack dice rolls
class DiceAttack:
    """
    A class representing an attack dice roll between a user and a target.

    Arguments
    ---------
    user_ratio : DiceRatio
        The DiceRatio object representing the user's attack ratio.
    target_ratio : DiceRatio
        The DiceRatio object representing the target's defense ratio.
    """
    def __init__(self, user_ratio: DiceRatio, target_ratio: DiceRatio):
        self.user_ratio = user_ratio
        self.target_ratio = target_ratio

    # methods
    def resolve(
        self,
        user: Entity,
        target: Entity,
        user_dice: Optional[Dice]=None,
        target_dice: Optional[Dice]=None
    ) -> int:
        """
        Resolves the attack dice roll between the user and the target.

        Arguments
        ---------
        user : Entity
            The entity performing the attack.
        target : Entity
            The entity being attacked.
        user_dice : Optional[Dice]
            The Dice object representing the user's attack roll.
            If None, a new "1d100" roll will be used.
        target_dice : Optional[Dice]
            The Dice object representing the target's defense roll.
            If None, a new "1d100" roll will be used.

        Returns
        -------
        int
            The result of the attack after applying the user's attack ratio
            and the target's defense ratio.
        """
        if user_dice is None:
            user_dice = Dice.roll("1d100")
        if target_dice is None:
            target_dice = Dice.roll("1d100")
        user_result = self.user_ratio.resolve(user, user_dice)
        target_result = self.target_ratio.resolve(target, target_dice)
        return max(user_result - target_result, 0)

    # class methods
    @classmethod
    def from_stats(
        cls,
        user_stat: str,
        target_stat: str,
        user_ratio: int,
        target_ratio: int
    ) -> DiceAttack:
        """
        Creates a DiceAttack instance from the given user and target stats and ratios.

        Arguments
        ---------
        user_stat : str
            The name of the user's stat to be used for the attack ratio.
        target_stat : str
            The name of the target's stat to be used for the defense ratio.
        user_ratio : int
            The attack ratio value for the user.
        target_ratio : int
            The defense ratio value for the target.

        Returns
        -------
        DiceAttack
            A new DiceAttack instance with the specified user and target ratios.
        """
        user = DiceRatio(user_stat, user_ratio)
        target = DiceRatio(target_stat, target_ratio)
        return cls(user, target)
