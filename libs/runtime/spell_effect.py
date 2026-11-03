#-*- coding: utf-8 -*-

"""
Spell effect module of the RPG Tabletop App.

This module defines the SpellEffect class and related functionality for handling spell effects in
the RPG Tabletop App. it also provides an Enum of every effect type.
"""

# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID, uuid4

# import local modules
from ..assets.spell import Effect, TargetStat, EffectType


# SpellEffect dataclass
@dataclass
class SpellEffect:
    """
    Class representing a spell effect in the RPG Tabletop App.

    Attributes:
        effect_id (UUID): The unique identifier of the effect.
        effect_type (EffectType): The type of the effect.
        effect_def (Effect): The definition of the effect.
        target_id (str): The unique identifier of the target affected by the effect.
        target_stat (TargetStat): The target stat affected by the effect.
        delta (int): The magnitude of the effect.
        duration (int): The duration of the effect in turns.
        event_id (UUID): The unique identifier of the spell event associated with the effect.
    """
    # Type of the Effect
    effect_type: EffectType

    # Definition of the Effect
    effect_def: Effect

    # ID of the affected entity
    target_id: str

    # Target stat affected by the effect
    target_stat: TargetStat

    # Magnitude of the effect
    delta: int

    # Duration of the effect in turns
    #
    # < 0 means the effect is permanent
    # 0 means the effect has expired
    # > 0 means the effect is temporary and still active
    duration: int

    # Unique identifier of the spell event associated with the effect
    event_id: UUID

    # Unique identifier of the spell effect instance
    id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict:
        """
        Convert the instance to a dictionnary
        """
        return {
            "effect_type": self.effect_type,
            "effect_def": self.effect_def,
            "target_id": self.target_id,
            "target_stat": self.target_stat,
            "delta": self.delta,
            "duration": self.duration,
            "event_id": self.event_id,
            "id": self.id
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpellEffect:
        """
        Create a SpellEffect instance from a dictionary.

        Args:
            data (dict): A dictionary containing the attributes of the SpellEffect.
        """
        return cls(**data)

    def is_expired(self) -> bool:
        """
        Check if the spell effect has expired based on its duration.

        Returns:
            bool: True if the effect has expired, False otherwise.
        """
        return self.duration == 0

    def is_permanent(self) -> bool:
        """
        Check if the spell effect is permanent (i.e., has an infinite duration).

        Returns:
            bool: True if the effect is permanent, False otherwise.
        """
        return self.duration < 0

    def tick(self) -> None:
        """
        Decrease the duration of the spell effect by one unit of time.

        If the duration reaches zero, the effect is considered expired.
        """
        if self.duration > 0:
            self.duration -= 1
