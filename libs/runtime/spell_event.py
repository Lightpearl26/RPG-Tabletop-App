#-*- coding: utf-8 -*-

"""
Spell event module of the RPG Tabletop App.

This module defines the SpellEvent class and related functionality for handling spell events in
the RPG Tabletop App.
"""

# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable, Optional, TYPE_CHECKING
from uuid import UUID, uuid4

# import local modules
from .spell_effect import SpellEffect
from ..assets.spell import RuntimePolicy, EffectType
from ..assets.campaign import get_current_campaign

if TYPE_CHECKING:
    from .entity import Entity
    from ..assets.spell import Spell


# SpellEvent dataclass
@dataclass
class SpellEvent:
    """
    Class representing a spell event in the RPG Tabletop App.

    Attributes:
        id (UUID): The unique identifier of the spell event.
        spell_id (str): The unique identifier of the spell associated with the event.
        caster_id (str): The unique identifier of the caster of the spell.
        targets_id (list[str]): The list of unique identifiers of the targets of the spell.
        effects (list[SpellEffect]): The list of spell effects associated with the event.
        runtime_policy (RuntimePolicy): The runtime policy governing the spell event.
        nb_cast (int): The number of times the spell has been cast.
        finished (bool): Indicates whether the spell event has finished.
    """
    # Unique identifier of the spell associated with the event
    spell_id: str

    # Unique identifier of the caster of the spell
    caster_id: str

    # List of unique identifiers of the targets of the spell
    targets_id: list[str]

    # List of spell effects associated with the event
    effects: list[SpellEffect]

    # Runtime policy governing the spell event
    runtime_policy: RuntimePolicy

    # Number of times the spell has been cast
    nb_cast: int = field(default=0)

    # Indicates whether the spell event has finished
    finished: bool = field(default=False)

    # Unique identifier of the spell event
    id: UUID = field(default_factory=uuid4)

    # methods
    def to_dict(self) -> dict:
        """
        Convert the instance to a JSON-serializable dictionary.
        """
        return {
            "spell_id": self.spell_id,
            "caster_id": str(self.caster_id),
            "targets_id": [str(target_id) for target_id in self.targets_id],
            "effects": [effect.to_dict() for effect in self.effects],
            "runtime_policy": RuntimePolicy(self.runtime_policy).value,
            "nb_cast": int(self.nb_cast),
            "finished": bool(self.finished),
            "id": str(self.id),
        }

    @classmethod
    def from_dict(cls, data: dict, effect_cache: Optional[dict] = None) -> SpellEvent:
        """
        Create a SpellEvent instance from a dictionary.

        Args:
            data (dict): A dictionary containing the attributes of the SpellEvent.
            effect_cache (dict): Effects already restored, by `str(effect.id)`. An effect
                                 found there is shared instead of duplicated, so the
                                 event and its target keep one instance.
        """
        effect_cache = {} if effect_cache is None else effect_cache
        effects = []
        for effect_data in data.get("effects", []):
            effect_id = str(effect_data.get("id"))
            effect = effect_cache.get(effect_id)
            if effect is None:
                effect = SpellEffect.from_dict(effect_data)
                effect_cache[str(effect.id)] = effect
            effects.append(effect)
        event_id = data.get("id")
        return cls(
            spell_id=str(data["spell_id"]),
            caster_id=str(data["caster_id"]),
            targets_id=[str(target_id) for target_id in data.get("targets_id", [])],
            effects=effects,
            runtime_policy=RuntimePolicy(
                getattr(data["runtime_policy"], "value", data["runtime_policy"])
            ),
            nb_cast=int(data.get("nb_cast", 0)),
            finished=bool(data.get("finished", False)),
            id=(event_id if isinstance(event_id, UUID) else UUID(str(event_id)))
            if event_id else uuid4(),
        )

    def apply(
        self,
        new_targets: list[str],
        user_dices: Optional[dict[str, int]]=None,
        targets_dices: Optional[dict[str, dict[str, int]]]=None,
    ) -> None:
        """
        Apply the spell event to the new targets.
        
        Args:
            new_targets (list[str]): The list of unique identifiers of the new targets for the spell
                                     event.
            user_dices (Optional[dict[str, int]]): The dice used by the user for the spell event.
            targets_dices (Optional[dict[str, dict[str, int]]]): The dice used by the targets for
                                                                 defense.
        """
        self._tick_effects()
        if self.finished:
            self._try_cleanup()
            return # Do not apply the spell event if it has already finished
        spell = get_current_campaign().spells.get(self.spell_id)
        if not spell:
            return # Do not apply the spell event if the spell does not exist
        caster = get_current_campaign().entities.get(self.caster_id)
        if not caster:
            return # Do not apply the spell event if the caster does not exist
        self.targets_id = new_targets

        if self.runtime_policy in (RuntimePolicy.MAINTAIN, RuntimePolicy.REFRESH):
            self._purge_removed_targets()

        if self.runtime_policy == RuntimePolicy.INSTANT:
            if self.nb_cast == 0:
                if not self._pay_cost(caster, spell.cost):
                    return # Do not apply the spell event if the caster cannot pay the cost
                self._create_effects_for_targets(caster, spell, user_dices, targets_dices)
                self.nb_cast += 1
                self.finished = True

        elif self.runtime_policy == RuntimePolicy.MAINTAIN:
            if self.nb_cast >= spell.delay:
                self.finished = True

            else:
                if not self._pay_cost(caster, spell.cost):
                    self.stop()
                    return

                self._create_effects_for_targets(
                    caster,
                    spell,
                    user_dices,
                    targets_dices,
                    only_new_targets=True,
                )

                self.nb_cast += 1

                if self.nb_cast >= spell.delay:
                    self.finished = True

        elif self.runtime_policy == RuntimePolicy.REFRESH:
            if self.nb_cast >= spell.delay:
                self.finished = True

            else:
                if not self._pay_cost(caster, spell.cost):
                    self.stop()
                    return

                self.purge()

                self._create_effects_for_targets(
                    caster,
                    spell,
                    user_dices,
                    targets_dices,
                )

                self.nb_cast += 1

                if self.nb_cast >= spell.delay:
                    self.finished = True

        elif self.runtime_policy == RuntimePolicy.DELAY:
            # Pay the cost only when starting the delayed cast.
            if self.nb_cast == 0:
                if not self._pay_cost(caster, spell.cost):
                    return

            # Trigger the spell after the requested delay.
            if self.nb_cast >= spell.delay:
                self._create_effects_for_targets(
                    caster,
                    spell,
                    user_dices,
                    targets_dices,
                )

                self.finished = True

            self.nb_cast += 1

        elif self.runtime_policy == RuntimePolicy.PASSIV:
            # Cost is paid only once when the passive starts.
            if self.nb_cast == 0:
                if not self._pay_cost(caster, spell.cost):
                    return

            if self.nb_cast < spell.delay:
                self._create_effects_for_targets(
                    caster,
                    spell,
                    user_dices,
                    targets_dices,
                )

                self.nb_cast += 1

                if self.nb_cast >= spell.delay:
                    self.finished = True

        elif self.runtime_policy == RuntimePolicy.TOGGLE:
            # First activation
            if self.nb_cast == 0:
                if not self._pay_cost(caster, spell.cost):
                    return

            self._create_effects_for_targets(
                caster,
                spell,
                user_dices,
                targets_dices,
                only_new_targets=True,
            )

            self.nb_cast += 1

        elif self.runtime_policy == RuntimePolicy.TRIGGER:
            # Arm the trigger and pay its cost only once.
            if self.nb_cast == 0:
                if not self._pay_cost(caster, spell.cost):
                    return

            triggered = False

            for target_id in self.targets_id:
                target = get_current_campaign().entities.get(target_id)

                if not target:
                    continue

                target_dice = (
                    targets_dices.get(target_id)
                    if targets_dices
                    else None
                )
                assert spell.trigger_condition is not None
                if spell.trigger_condition.evaluate(
                    caster,
                    target,
                    user_dices,
                    target_dice,
                ):
                    triggered = True
                    break

            if triggered:
                self._create_effects_for_targets(
                    caster,
                    spell,
                    user_dices,
                    targets_dices,
                )

                self.finished = True

            self.nb_cast += 1

        else:
            raise ValueError(f"Unknown runtime policy: {self.runtime_policy}")

        self._try_cleanup()

    def _try_cleanup(self) -> None:
        """
        Remove this event from the caster once it no longer needs to be updated.
        """
        if not self.finished:
            return

        # Temporary effects still require this event to be ticked.
        if any(effect.duration > 0 for effect in self.effects):
            return

        caster = get_current_campaign().entities.get(self.caster_id)

        if not caster:
            return

        caster.spell_events = [
            event
            for event in caster.spell_events
            if event.id != self.id
        ]

    def _tick_effects(self) -> None:
        """
        Tick all effects created by this spell event and remove expired ones.
        """
        for effect in self.effects[:]:
            effect.tick()

            if effect.is_expired():
                self._remove_effect(effect)

    def _remove_effect(self, effect: SpellEffect) -> None:
        """
        Remove a spell effect from both the event and its target.
        """
        target = get_current_campaign().entities.get(effect.target_id)

        if target:
            target.spell_effects = [
                active_effect
                for active_effect in target.spell_effects
                if active_effect.id != effect.id
            ]

        if effect in self.effects:
            self.effects.remove(effect)

    def _pay_cost(self, caster: Entity, cost: int) -> bool:
        """
        Attempt to pay the cost for the spell.

        Args:
            caster (Entity): The entity attempting to pay the cost.
            cost (int): The cost to be paid.

        Returns:
            bool: True if the cost was successfully paid, False otherwise.
        """
        if caster.stamina < cost:
            return False
        caster.stats_modifier.stamina -= cost
        return True

    def _purge_removed_targets(self) -> None:
        """
        Remove effects linked to targets that are no longer targeted.
        """
        for effect in self.effects[:]:
            if effect.target_id in self.targets_id:
                continue

            self._remove_effect(effect)

    def _get_effect_duration(self, spell: Spell) -> int:
        """
        Get the initial duration of an effect created by this event.
        """
        if self.runtime_policy in (
            RuntimePolicy.INSTANT,
            RuntimePolicy.DELAY,
            RuntimePolicy.PASSIV,
            RuntimePolicy.TOGGLE,
            RuntimePolicy.TRIGGER
        ):
            return -1

        return max(0, int(spell.delay) - self.nb_cast)

    def _create_effects_for_targets(
        self,
        caster: Entity,
        spell: Spell,
        user_dices: Optional[dict[str, int]]=None,
        targets_dices: Optional[dict[str, dict[str, int]]]=None,
        only_new_targets: bool = False,
    ) -> None:
        """
        Create effects for the targets of the spell.

        Args:
            caster (Entity): The entity casting the spell.
            spell (Spell): The spell being cast.
            user_dices (Optional[dict[str, int]]): The dice rolls of the caster.
            targets_dices (Optional[dict[str, dict[str, int]]]): The dice rolls of the targets.
        """
        existing_target_ids = {effect.target_id for effect in self.effects}
        for target_id in self.targets_id:
            if only_new_targets and target_id in existing_target_ids:
                continue
            target = get_current_campaign().entities.get(target_id)
            if not target:
                continue
            target_dice = (
                targets_dices.get(target_id)
                if targets_dices and target_id in targets_dices
                else None
            )
            for effect_def in spell.effects:
                delta = int(effect_def.formula.evaluate(caster, target, user_dices, target_dice))
                if delta < 0:
                    delta = 0
                op = -1 if effect_def.operator == "malus" else 1
                destination_id = self.caster_id if effect_def.target[0] == "user" else target_id
                destination = caster if effect_def.target[0] == "user" else target
                effect_type = EffectType(effect_def.effect_type)
                target_stat = effect_def.target[1]

                # We verify if its damage effect and apply damage_amp if it is
                if effect_type == EffectType.DAMAGE:
                    final_delta = delta * (100 + caster.damage_amplification) // 100
                else:
                    final_delta = op * delta

                # Damages should pass the apply_damage method
                if effect_type == EffectType.DAMAGE:
                    residual_damage, _ = destination.apply_damage(target_stat, int(final_delta))
                    final_delta = -int(residual_damage)

                # We don't register null effects
                if final_delta == 0:
                    continue

                spell_effect = SpellEffect(
                    effect_def=effect_def,
                    effect_type=effect_type,
                    target_id=destination_id,
                    target_stat=target_stat,
                    delta=final_delta,
                    event_id=self.id,
                    duration=self._get_effect_duration(spell)
                )
                self.effects.append(spell_effect)
                destination.spell_effects.append(spell_effect)

    def purge(self) -> None:
        """
        Remove all effects created by this spell event.
        """
        for effect in self.effects[:]:
            self._remove_effect(effect)

    def stop(self) -> None:
        """
        Stop the spell event and purge its effects.
        """
        self.finished = True
        self.purge()


def share_effects(entities: Iterable[Entity]) -> None:
    """
    Make every reference to one effect id point at the same SpellEffect instance.

    An effect lives both in its target's `spell_effects` and in the `effects` of the
    event that created it. Entities restored one by one (from their own asset file)
    get separate copies; ticking the event would then miss the target's copy.
    The target's instance wins.
    """
    entities = list(entities)
    shared: dict[str, SpellEffect] = {}
    for entity in entities:
        unique: list[SpellEffect] = []
        for effect in entity.spell_effects:
            key = str(effect.id)
            if key not in shared:
                shared[key] = effect
            if all(str(other.id) != key for other in unique):
                unique.append(shared[key])
        entity.spell_effects = unique
    for entity in entities:
        for spell_event in entity.spell_events:
            spell_event.effects = [
                shared.setdefault(str(effect.id), effect) for effect in spell_event.effects
            ]
