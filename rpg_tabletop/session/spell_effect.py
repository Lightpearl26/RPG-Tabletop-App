#-*- coding: utf-8 -*-

"""
Spell effect module of the RPG Tabletop App.

This module defines the SpellEffect class and related functionality for handling spell effects in
the RPG Tabletop App. it also provides an Enum of every effect type.
"""

# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4

# import local modules
from rpg_tabletop.assets.spell import Effect, TargetStat, EffectType


def _uuid_or_none(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


# SpellEffect dataclass
@dataclass
class SpellEffect:
    """
    Class representing a spell effect in the RPG Tabletop App.

    Attributes:
        effect_id (UUID): The unique identifier of the effect.
        effect_type (EffectType): The type of the effect.
        effect_def (Optional[Effect]): The definition of the effect, None for an effect the
                                       GM added by hand.
        target_id (str): The unique identifier of the target affected by the effect.
        target_stat (TargetStat): The target stat affected by the effect.
        delta (int): The magnitude of the effect.
        duration (int): The duration of the effect in turns.
        event_id (Optional[UUID]): The spell event that created the effect, None for an
                                   effect the GM added by hand.
        origin (str): Free description of where the effect comes from ("Fallen rock"),
                      written by the GM. Empty when the spell event says it all.
    """
    # Type of the Effect
    effect_type: EffectType

    # Definition of the Effect (None when the GM added the effect by hand)
    effect_def: Optional[Effect]

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

    # Spell event that created the effect (None when the GM added it by hand)
    event_id: Optional[UUID]

    # Unique identifier of the spell effect instance
    id: UUID = field(default_factory=uuid4)

    # Free description of the cause, written by the GM
    origin: str = ""

    # Spell and caster that created the effect ("" for a GM or weapon effect);
    # kept when the spell event itself is gone
    spell_id: str = ""
    caster_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the instance to a JSON-serializable dictionary.
        """
        return {
            "id": str(self.id),
            "effect_type": EffectType(self.effect_type).value,
            "effect_def": self.effect_def.to_dict() if self.effect_def is not None else None,
            "target_id": str(self.target_id),
            "target_stat": str(self.target_stat),
            "delta": int(self.delta),
            "duration": int(self.duration),
            "event_id": str(self.event_id) if self.event_id is not None else None,
            "origin": str(self.origin),
            "spell_id": str(self.spell_id),
            "caster_id": str(self.caster_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpellEffect:
        """
        Create a SpellEffect instance from a dictionary.

        Args:
            data (dict): A dictionary made by `to_dict`. Live values (enums, UUIDs,
                         Effect objects) are accepted too.
        """
        effect_def = data.get("effect_def")
        if isinstance(effect_def, dict):
            effect_def = Effect.from_dict(effect_def)
        elif not isinstance(effect_def, Effect):
            effect_def = None
        return cls(
            effect_type=EffectType(getattr(data["effect_type"], "value", data["effect_type"])),
            effect_def=effect_def,
            target_id=str(data["target_id"]),
            target_stat=str(data["target_stat"]),  # type: ignore[arg-type]
            delta=int(data.get("delta", 0)),
            duration=int(data.get("duration", -1)),
            event_id=_uuid_or_none(data.get("event_id")),
            id=_uuid_or_none(data.get("id")) or uuid4(),
            origin=str(data.get("origin") or ""),
            spell_id=str(data.get("spell_id") or ""),
            caster_id=str(data.get("caster_id") or ""),
        )

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
