# -*- coding: utf-8 -*-

"""
Resolution of combat actions, once the GM validated the dice.

Strike and shoot take two steps:
    1. `roll_attack`: the attacker's and the defender's d100 decide how many
       damage dice the attack deals (Entity.strike / Entity.shoot rules); the
       turn stamina is spent, hit or miss.
    2. `apply_attack_damage`: the damage dice are added up, increased by the
       attacker's damage amplification, then reduced by the target's damage
       reduction and absorbed by its shields; what is left becomes a permanent
       "Damage" effect on the target's HP.

A spell takes one step, `cast_spell`: its turn cost is spent and a spell event
is applied with the d100 the GM validated for its formulas.

Replay never rolls nor evaluates a formula again: each resolution records the
resulting runtime state of the entities it changed (ACTION_RESOLVED).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Optional

from rpg_tabletop.assets.spell import DiceAttack, DiceRatio, Formula, Spell
from rpg_tabletop.core.dice import Dice
from .entity import Entity, StatsModifier
from .spell_effect import EffectType, SpellEffect
from .spell_event import SpellEvent, share_effects

if TYPE_CHECKING:
    from .session import SessionEvent, SessionRuntime


ATTACK_KINDS = ("strike", "shoot")
KIND_LABELS = {"strike": "Strike", "shoot": "Shoot"}


class ActionError(ValueError):
    """The action cannot be resolved as asked."""


# =============================================================================
# Dice
# =============================================================================


def parse_expression(expression: str) -> tuple[int, int]:
    """'3d8' -> (3, 8)."""
    try:
        count, _d, size = str(expression).lower().partition("d")
        return max(0, int(count)), max(0, int(size))
    except ValueError as error:
        raise ActionError(f"Invalid dice expression: {expression}") from error


def check_dice_values(expression: str, values: Iterable[Any]) -> list[int]:
    """
    Validate the result of `expression`: one value per die, each from 1 to its size.

    Raises:
        ActionError: If the values do not match.
    """
    count, size = parse_expression(expression)
    try:
        checked = [int(value) for value in values]
    except (TypeError, ValueError) as error:
        raise ActionError("Dice values must be whole numbers.") from error
    if len(checked) != count or any(not 1 <= value <= size for value in checked):
        raise ActionError(f"{expression} needs {count} value(s) from 1 to {size}.")
    return checked


def damage_expression(attacker: Entity, target: Entity, kind: str, attack: int, defense: int) -> str:
    """Damage dice of a strike or a shot ("0d4" when it misses)."""
    attack_die = Dice("1d100", [int(attack)])
    defense_die = Dice("1d100", [int(defense)])
    if kind == "strike":
        return attacker.strike(target, attack_die, defense_die)
    if kind == "shoot":
        return attacker.shoot(target, attack_die, defense_die)
    raise ActionError(f"Unknown attack: {kind}")


def is_hit(expression: str) -> bool:
    count, size = parse_expression(expression)
    return count > 0 and size > 0


def _formula_terms(formula: Optional[Formula], found: set[tuple[str, str]]) -> None:
    if formula is None:
        return
    if not formula.compilated:
        try:
            formula.compile()
        except (ValueError, SyntaxError):
            return
    for kind, args in formula.placeholders:
        if kind is Formula:
            _formula_terms(args, found)
        elif kind is DiceRatio:
            who, _dot, stat = str(args[0]).partition(".")
            found.add((who, stat))
        elif kind is DiceAttack:
            for reference in (args[0], args[2]):
                who, _dot, stat = str(reference).partition(".")
                found.add((who, stat))


def spell_dice_terms(spell: Spell) -> list[tuple[str, str]]:
    """(who, stat) of every d100 the spell's formulas roll: who is "user" or "target"."""
    found: set[tuple[str, str]] = set()
    for effect in spell.effects:
        _formula_terms(effect.formula, found)
    _formula_terms(spell.trigger_condition, found)
    return sorted(term for term in found if term[0] in ("user", "target") and term[1])


def spell_dice_keys(spell: Spell, caster_id: str, target_ids: Iterable[str]) -> list[tuple[str, str, str]]:
    """
    (key, who, stat) of the d100 to propose for a cast: "user:<stat>" for the
    caster, "target:<entity id>:<stat>" for each target.
    """
    keys: list[tuple[str, str, str]] = []
    for who, stat in spell_dice_terms(spell):
        if who == "user":
            keys.append((f"user:{stat}", caster_id, stat))
        else:
            for target_id in dict.fromkeys(target_ids):
                keys.append((f"target:{target_id}:{stat}", target_id, stat))
    return keys


def spell_dice_from_keys(values: dict[str, int]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Turn {"user:str": 40, "target:<id>:con": 12} into the dicts SpellEvent.apply reads."""
    user: dict[str, int] = {}
    targets: dict[str, dict[str, int]] = {}
    for key, value in values.items():
        parts = str(key).split(":")
        if len(parts) == 2 and parts[0] == "user":
            user[parts[1]] = int(value)
        elif len(parts) == 3 and parts[0] == "target":
            targets.setdefault(parts[1], {})[parts[2]] = int(value)
    return user, targets


# =============================================================================
# State recording
# =============================================================================


def runtime_state(entity: Entity) -> dict[str, Any]:
    """The part of an entity that combat changes."""
    return {
        "stats_modifier": entity.stats_modifier.to_dict(),
        "spell_effects": [effect.to_dict() for effect in entity.spell_effects],
        "spell_events": [event.to_dict() for event in entity.spell_events],
    }


def apply_runtime_states(runtime: SessionRuntime, data: dict[str, Any]) -> None:
    """Replay an ACTION_RESOLVED event: put back the recorded states."""
    states: dict[str, dict[str, Any]] = data.get("states", {})
    cache: dict[str, SpellEffect] = {}
    for entity_id, state in states.items():
        entity = runtime.entities.get(str(entity_id))
        if entity is None:
            continue
        entity.stats_modifier = StatsModifier.from_dict(state.get("stats_modifier", {}))
        effects = []
        for raw in state.get("spell_effects", []):
            effect = cache.get(str(raw.get("id"))) or SpellEffect.from_dict(raw)
            cache[str(effect.id)] = effect
            effects.append(effect)
        entity.spell_effects = effects
    for entity_id, state in states.items():
        entity = runtime.entities.get(str(entity_id))
        if entity is not None:
            entity.spell_events = [
                SpellEvent.from_dict(raw, cache) for raw in state.get("spell_events", [])
            ]
    runtime._remove_effects_everywhere(data.get("removed_effect_ids", ()))
    share_effects(runtime.entities.values())


@dataclass(frozen=True)
class AttackOutcome:
    hit: bool
    expression: str
    event: Any  # the ATTACK_ROLLED SessionEvent


class CombatResolver:
    """Applies validated combat actions to a SessionRuntime, as journal events."""

    def __init__(self, runtime: SessionRuntime) -> None:
        self.runtime = runtime

    # -------------------------------------------------------------- helpers
    def _entity(self, entity_id: str) -> Entity:
        entity = self.runtime.entities.get(str(entity_id))
        if entity is None:
            raise ActionError(f"Unknown entity: {entity_id}")
        return entity

    def _snapshot(self) -> tuple[dict[str, str], set[str]]:
        states = {
            entity_id: json.dumps(runtime_state(entity), sort_keys=True)
            for entity_id, entity in self.runtime.entities.items()
        }
        effect_ids = {
            str(effect.id)
            for entity in self.runtime.entities.values()
            for effect in entity.spell_effects
        }
        return states, effect_ids

    def _record(
        self,
        before: tuple[dict[str, str], set[str]],
        action: dict[str, Any],
        message: str,
        *,
        source_id: str,
        target_ids: Iterable[str],
    ) -> SessionEvent:
        from .session import SessionEventType

        states_before, effects_before = before
        changed = {}
        for entity_id, entity in self.runtime.entities.items():
            state = runtime_state(entity)
            if json.dumps(state, sort_keys=True) != states_before.get(entity_id):
                changed[entity_id] = state
        effects_after = {
            str(effect.id)
            for entity in self.runtime.entities.values()
            for effect in entity.spell_effects
        }
        removed = sorted(effects_before - effects_after)
        return self.runtime._dispatch(
            SessionEventType.ACTION_RESOLVED,
            {"action": action, "states": changed, "removed_effect_ids": removed},
            message=message,
            source_id=source_id,
            target_ids=tuple(target_ids),
        )

    # -------------------------------------------------------------- attacks
    def roll_attack(
        self,
        attacker_id: str,
        kind: str,
        target_id: str,
        attack: int,
        defense: int,
    ) -> AttackOutcome:
        """
        Step 1 of a strike or a shot: spend the turn stamina and find the damage dice.
        """
        from .session import SessionEventType

        if kind not in ATTACK_KINDS:
            raise ActionError(f"Unknown attack: {kind}")
        if not self.runtime.recording:
            raise RuntimeError("Historical runtimes are read-only.")
        attacker, target = self._entity(attacker_id), self._entity(target_id)
        check_dice_values("1d100", [attack])
        check_dice_values("1d100", [defense])
        expression = damage_expression(attacker, target, kind, attack, defense)
        hit = is_hit(expression)
        if kind == "strike":
            self.runtime.spend_strike_stamina(attacker_id)
        else:
            self.runtime.spend_shoot_stamina(attacker_id)
        verb = "strikes" if kind == "strike" else "shoots"
        event = self.runtime._dispatch(
            SessionEventType.ATTACK_ROLLED,
            {
                "kind": kind,
                "attacker_id": str(attacker_id),
                "target_id": str(target_id),
                "attack": int(attack),
                "defense": int(defense),
                "damage": expression,
                "hit": hit,
            },
            message=(
                f"{attacker.name} {verb} {target.name}: d100 {attack} vs {defense} -> "
                + (f"hit, {expression} damage" if hit else "miss")
            ),
            source_id=str(attacker_id),
            target_ids=(str(target_id),),
        )
        return AttackOutcome(hit, expression, event)

    def apply_attack_damage(
        self,
        attacker_id: str,
        kind: str,
        target_id: str,
        expression: str,
        values: Iterable[Any],
    ) -> SessionEvent:
        """Step 2 of a strike or a shot: deal the rolled damage."""
        attacker, target = self._entity(attacker_id), self._entity(target_id)
        rolled = check_dice_values(expression, values)
        before = self._snapshot()
        total = sum(rolled)
        amplified = total * (100 + int(attacker.damage_amplification)) // 100
        residual, consumed = target.apply_damage("hp", amplified)
        label = KIND_LABELS.get(kind, kind.title())
        if residual > 0:
            target.spell_effects.append(SpellEffect(
                effect_type=EffectType.DAMAGE,
                effect_def=None,
                target_id=str(target.id),
                target_stat="hp",
                delta=-int(residual),
                duration=-1,
                event_id=None,
                origin=f"{label} — {attacker.name}",
                caster_id=str(attacker.id),
            ))
        absorbed = amplified - residual
        details = f"{expression} [{', '.join(map(str, rolled))}] = {total}"
        if amplified != total:
            details += f", amplified to {amplified}"
        if absorbed:
            details += f", {absorbed} reduced or absorbed"
        return self._record(
            before,
            {
                "kind": kind,
                "attacker_id": str(attacker_id),
                "target_id": str(target_id),
                "damage": expression,
                "values": rolled,
                "total": total,
                "dealt": int(residual),
                "consumed_shields": [str(effect.id) for effect in consumed],
            },
            f"{target.name} takes {residual} damage from {attacker.name}'s {kind} ({details})",
            source_id=str(attacker_id),
            target_ids=(str(target_id),),
        )

    # --------------------------------------------------------------- spells
    def cast_spell(
        self,
        caster_id: str,
        spell_name: str,
        target_ids: Iterable[str],
        dice: dict[str, int],
    ) -> SessionEvent:
        """
        Cast a spell on its targets with the validated d100 of its formulas.

        The cost comes out of the turn stamina only.
        """
        if not self.runtime.recording:
            raise RuntimeError("Historical runtimes are read-only.")
        caster = self._entity(caster_id)
        spell = caster.get_spell(spell_name)
        if spell is None:
            raise ActionError(f"{caster.name} does not know {spell_name}.")
        targets = [self._entity(target_id) for target_id in dict.fromkeys(target_ids)]
        if not targets:
            raise ActionError("The spell has no target.")

        self.runtime.spend_spell_stamina(caster_id, spell_name)
        before = self._snapshot()
        campaign = self.runtime.campaign
        campaign.spells.register(spell.name, spell)
        for entity_id, entity in self.runtime.entities.items():
            campaign.entities.register(entity_id, entity)

        user_dice, target_dice = spell_dice_from_keys(dice)
        spell_event = SpellEvent(
            spell_id=spell.name,
            caster_id=str(caster.id),
            targets_id=[str(target.id) for target in targets],
            effects=[],
            runtime_policy=spell.runtime_policy,
        )
        # Registered before applying, so that a finished instant spell can clean
        # itself up; the turn stamina was already spent above.
        caster.spell_events.append(spell_event)
        spell_event.apply(
            [str(target.id) for target in targets],
            user_dices=user_dice or None,
            targets_dices=target_dice or None,
            pay=lambda _caster, _cost: True,
        )
        names = ", ".join(target.name for target in targets)
        return self._record(
            before,
            {
                "kind": "spell",
                "caster_id": str(caster_id),
                "spell": spell.name,
                "target_ids": [str(target.id) for target in targets],
                "dice": {str(key): int(value) for key, value in dice.items()},
                "effects": len(spell_event.effects),
            },
            f"{caster.name} casts {spell.name} on {names} ({len(spell_event.effects)} effect(s))",
            source_id=str(caster_id),
            target_ids=[str(target.id) for target in targets],
        )
