import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from libs.assets.campaign import Campaign, Registry
from libs.assets.character import Character, Stats
from libs.assets.item import Inventory
from libs.assets.spell import Effect, EffectType, Formula, Spell, TargetStat
from libs.dice import Dice, DiceAttack, DiceCheck, DiceRatio
from libs.runtime.entity import Entity
from libs.runtime.spell_effect import SpellEffect
from libs.runtime.spell_event import SpellEvent


class StatSource:
    def __init__(self, **stats: int) -> None:
        self.stats = stats

    def get_stat(self, stat_name: str) -> int:
        return self.stats.get(stat_name, 0)


class RegistryTests(unittest.TestCase):
    def test_register_get_require_and_unregister(self) -> None:
        registry: Registry[str] = Registry()
        registry.register("hero", "Amphora")

        self.assertEqual(registry.get("hero"), "Amphora")
        self.assertEqual(registry.require("hero"), "Amphora")
        self.assertIn("hero", registry)

        registry.unregister("hero")

        self.assertIsNone(registry.get("hero"))
        with self.assertRaises(KeyError):
            registry.require("hero")


class DiceTests(unittest.TestCase):
    def test_roll_uses_requested_number_of_dice(self) -> None:
        with patch("libs.dice.randint", side_effect=[2, 6]) as randint:
            roll = Dice.roll("2d6")

        self.assertEqual(roll.dice_values, [2, 6])
        self.assertEqual(roll.total, 8)
        self.assertEqual(randint.call_count, 2)
        self.assertTrue(roll.critical_failure)

    def test_invalid_command_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            Dice.roll("invalid")

    def test_dice_check_and_ratio_use_supplied_roll(self) -> None:
        entity = StatSource(dex=80, wis=50)
        check = DiceCheck(entity, "wis", Dice("1d100", [40]))
        ratio = DiceRatio("dex", 50).resolve(entity, Dice("1d100", [30]))

        self.assertTrue(check.success)
        self.assertEqual(ratio, 25)

    def test_attack_never_returns_negative_damage(self) -> None:
        attack = DiceAttack.from_stats("str", "con", 100, 100)
        user = StatSource(str=20)
        target = StatSource(con=80)

        damage = attack.resolve(
            user,
            target,
            Dice("1d100", [10]),
            Dice("1d100", [10]),
        )

        self.assertEqual(damage, 0)


class AssetAndFormulaTests(unittest.TestCase):
    def test_stats_calculate_hp_and_level(self) -> None:
        stats = Stats(
            str=50,
            dex=50,
            int=50,
            con=50,
            wis=50,
            agi=50,
            per=50,
            cha=50,
            luc=50,
            sur=50,
        )

        self.assertEqual(stats.hp, 20)
        self.assertEqual(stats.level, 0)

    def test_inventory_add_remove_and_round_trip(self) -> None:
        inventory = Inventory.from_list([("Potion", 2)])
        inventory.add_item("Potion")
        inventory.remove_item("Potion", 2)

        self.assertEqual(inventory.to_list(), [("Potion", 1)])

        inventory.remove_item("Potion")
        self.assertEqual(inventory.to_list(), [])

    def test_formula_respects_arithmetic_precedence(self) -> None:
        formula = Formula("2 + 3 * 4")

        self.assertEqual(formula.evaluate(None, None), 14)

    def test_formula_comparison_returns_boolean_result(self) -> None:
        user = StatSource(hp=25)
        target = StatSource()

        self.assertFalse(Formula("user.hp <= 20").evaluate(user, target))
        self.assertTrue(Formula("user.hp > 20").evaluate(user, target))

    def test_formula_supports_boolean_operators(self) -> None:
        user = StatSource(hp=25)
        target = StatSource()

        self.assertTrue(
            Formula("(user.hp > 20) & (user.hp < 30)").evaluate(user, target)
        )
        self.assertTrue(Formula("!(user.hp < 20)").evaluate(user, target))

    def test_formula_validate_accepts_what_the_game_can_evaluate(self) -> None:
        for expression in (
            "10",
            "user.str",
            "10 + diceratio(user.int, 10)",
            "diceattack(user.dex, 20, target.con, 20) * 2",
            "user.hp <= 20",
            "(user.hp > 20) & (user.hp < 30)",
            "!(user.hp < 20)",
            "1 / 0",
            "2 + 3 * 4",
        ):
            with self.subTest(expression):
                Formula(expression).validate()

    def test_formula_validate_keeps_the_formula_evaluable(self) -> None:
        formula = Formula("2 + 3 * 4")

        formula.validate()
        formula.validate()

        self.assertEqual(formula.evaluate(None, None), 14)

    def test_formula_validate_explains_what_is_wrong(self) -> None:
        cases = {
            "": "The formula is empty.",
            "   ": "The formula is empty.",
            "user.a.b": "'user.a.b' must look like user.stat or target.stat.",
            "foe.hp": "Unknown scope 'foe' in 'foe.hp': use user or target.",
            "user.mana": "Unknown stat 'mana' in 'user.mana'.",
            "foo(1)": "Unknown function 'foo'.",
            "diceratio(user.int)": "diceratio takes 2 arguments.",
            "diceattack(user.dex, 20, target.con)": "diceattack takes 4 arguments.",
            "diceratio(user.int, ten)": "The percentage 'ten' must be a whole number.",
            "(user.int + ": "Unmatched opening parenthesis.",
            "10 +) 3": "Unmatched closing parenthesis.",
            "5 +": "The operators do not form a valid expression.",
            "user.hp >": "The operators do not form a valid expression.",
            "3 3": "'3 3' must look like user.stat or target.stat.",
            "user.hp ?": "Unknown stat 'hp ?' in 'user.hp ?'.",
        }
        for expression, message in cases.items():
            with self.subTest(expression):
                with self.assertRaises(ValueError) as caught:
                    Formula(expression).validate()

                self.assertEqual(str(caught.exception), message)


def effect_data(
    target: str, operator: str, formula: str, effect_type: str
) -> dict[str, str]:
    return {
        "target": target,
        "operator": operator,
        "formula": formula,
        "effect_type": effect_type,
    }


class SpellEventDamageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign = Campaign("Runtime Test", Path("unused"))
        patcher = patch(
            "libs.runtime.spell_event.get_current_campaign",
            return_value=self.campaign,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Constitution and wisdom of 100 give 30 hit points
        self.caster = self.entity("Caster")
        self.target = self.entity("Target")

    def entity(self, name: str) -> Entity:
        entity = Entity(name, Character(name, Stats(con=100, wis=100)))
        self.campaign.entities.register(str(entity.id), entity)
        return entity

    def add_effect(
        self, entity: Entity, effect_type: EffectType, stat: TargetStat, delta: int
    ) -> SpellEffect:
        definition = Effect(("target", stat), "bonus", Formula("0"), effect_type)
        effect = SpellEffect(
            effect_type=effect_type,
            effect_def=definition,
            target_id=str(entity.id),
            target_stat=stat,
            delta=delta,
            duration=-1,
            event_id=uuid4(),
        )
        entity.spell_effects.append(effect)
        return effect

    def cast(self, *effects: dict[str, str]) -> SpellEvent:
        spell = Spell.from_dict(
            {
                "name": "bolt",
                "cost": 0,
                "targeting": "single",
                "runtime_policy": "instant",
                "effects": list(effects),
            }
        )
        self.campaign.spells.register("bolt", spell)
        event = SpellEvent(
            spell_id="bolt",
            caster_id=str(self.caster.id),
            targets_id=[str(self.target.id)],
            effects=[],
            runtime_policy=spell.runtime_policy,
        )
        event.apply([str(self.target.id)])
        return event

    def test_damage_is_amplified_reduced_and_absorbed_by_shields(self) -> None:
        # The modifiers sit on luck so that they do not move hit points themselves
        self.add_effect(self.caster, EffectType.DAMAGE_AMPLIFICATION, "luc", 20)
        self.add_effect(self.target, EffectType.DAMAGE_REDUCTION, "luc", 50)
        shield = self.add_effect(self.target, EffectType.SHIELD, "hp", 10)
        self.assertEqual(self.target.hp, 40)

        event = self.cast(effect_data("target.hp", "malus", "50", "damage"))

        # 50 -> 60 with the amplification -> 30 with the reduction -> 20 after the shield
        self.assertEqual([effect.delta for effect in event.effects], [-20])
        self.assertNotIn(shield, self.target.spell_effects)
        self.assertEqual(self.target.hp, 10)

    def test_damage_to_another_stat_leaves_hit_point_shields_alone(self) -> None:
        shield = self.add_effect(self.target, EffectType.SHIELD, "hp", 5)

        self.cast(effect_data("target.stamina", "malus", "10", "damage"))

        self.assertIn(shield, self.target.spell_effects)
        self.assertEqual(shield.delta, 5)
        self.assertEqual(self.target.stamina, 90)
        self.assertEqual(self.target.hp, 35)

    def test_other_effect_types_ignore_the_damage_modifiers(self) -> None:
        self.add_effect(self.caster, EffectType.DAMAGE_AMPLIFICATION, "luc", 20)
        self.add_effect(self.target, EffectType.DAMAGE_REDUCTION, "luc", 50)

        self.cast(
            effect_data("target.hp", "malus", "10", "other"),
            effect_data("target.hp", "bonus", "5", "heal"),
        )

        self.assertEqual(self.target.hp, 25)

    def test_fully_reduced_damage_registers_no_effect(self) -> None:
        self.add_effect(self.target, EffectType.DAMAGE_REDUCTION, "luc", 100)

        event = self.cast(effect_data("target.hp", "malus", "50", "damage"))

        self.assertEqual(event.effects, [])
        self.assertEqual(self.target.hp, 30)