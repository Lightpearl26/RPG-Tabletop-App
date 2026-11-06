#-*- coding: utf-8 -*-

"""
Spell module for RPG Tabletop App.

This module defines the Spell class and related functionality for managing
spells and magic-related operations in the game.
"""

# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Any, TYPE_CHECKING, get_args
from enum import Enum
from ast import (
    Expression,
    BinOp,
    UnaryOp,
    Constant,
    Add,
    Sub,
    Mult,
    Div,
    USub,
    UAdd,
    parse,
    Compare,
    Eq,
    Gt,
    Lt,
    GtE,
    LtE,
    NotEq,
    BitAnd,
    BitOr,
    BitXor,
    Not
)
from math import isfinite
from re import match, sub
from json import load

# import local modules
from .campaign import get_current_campaign
from ..dice import Dice, DiceAttack, DiceRatio

if TYPE_CHECKING:
    from ..runtime.entity import Entity


# Define types and constants
TargetScope = Literal["user", "target"]
TargetStat = Literal[
    "str",
    "dex",
    "int",
    "con",
    "wis",
    "agi",
    "per",
    "cha",
    "luc",
    "sur",
    "hp",
    "stamina",
    "mental_health",
    "drug_health"
]
Targeting = Literal["single", "multi", "self", "area", "line"]
# Radius, in cells, of an area spell when its file does not say
DEFAULT_AREA_SIZE = 1
Operator = Literal["bonus", "malus"]
TARGET_SCOPES: tuple[str, ...] = get_args(TargetScope)
TARGET_STATS: tuple[str, ...] = get_args(TargetStat)

class RuntimePolicy(Enum):
    """
    Runtime policies for spell events in the RPG Tabletop App.
    """
    INSTANT = "instant"
    MAINTAIN = "maintain"
    REFRESH = "refresh"
    DELAY = "delay"
    PASSIV = "passiv"
    TOGGLE = "toggle"
    TRIGGER = "trigger"

class EffectType(Enum):
    """
    Enum representing the different types of spell effects in the RPG Tabletop App.

    Each member of this enum corresponds to a specific type of effect that a spell can have.
    """
    # Hit point effects
    DAMAGE = "damage"
    HEAL = "heal"
    SHIELD = "shield"

    # Damage modifiers
    DAMAGE_REDUCTION = "damage_reduction"
    DAMAGE_AMPLIFICATION = "damage_amplification"

    # Buffs and debuffs
    STAT_BUFF = "buff"
    STAT_DEBUFF = "debuff"

    # Crowd control effects
    CONTROL = "control"

    # Miscellaneous effects
    OTHER = "other"


# Define helper functions
def smart_split(expr: str) -> list[str]:
    """
    Split an expression into parts while respecting parentheses.

    Supported operators:
        +, -, *, /, ==, <, >, <=, >=

    Example:
        "10 + diceratio(user.int, 20) - (5 * 2)"
        ->
        ["10", "+", "diceratio(user.int, 20)", "-", "(5 * 2)"]

        "user.hp <= 20"
        ->
        ["user.hp", "<=", "20"]
    """
    parts = []
    buf = ""
    depth = 0
    i = 0

    while i < len(expr):
        c = expr[i]

        if c == "(":
            depth += 1
            buf += c

        elif c == ")":
            depth -= 1

            if depth < 0:
                raise ValueError("Unmatched closing parenthesis.")

            buf += c

        elif depth == 0:
            # Two-character comparison operators
            if expr[i:i + 2] in ("==", "<=", ">=", "!="):
                if buf.strip():
                    parts.append(buf.strip())

                parts.append(expr[i:i + 2])
                buf = ""
                i += 1

            # Single-character operators
            elif c in "+-*/<>!&|^":
                if buf.strip():
                    parts.append(buf.strip())

                parts.append(c)
                buf = ""

            else:
                buf += c

        else:
            buf += c

        i += 1

    if depth != 0:
        raise ValueError("Unmatched opening parenthesis.")

    if buf.strip():
        parts.append(buf.strip())

    return parts

def _safe_eval_expression(expr: str) -> int | float | bool:
    """
    Evaluate a simple arithmetic, comparison or logical expression safely.

    Allowed operations:
        Arithmetic:
            +, -, *, /

        Comparison:
            ==, !=, >, <, <=, >=

        Logical:
            &, |, ^, !
    """

    # Convert custom logical NOT syntax to Python syntax.
    # Preserve != comparison operator.
    expr = sub(r'!(?!=)', ' not ', expr).strip()

    tree = parse(expr, mode="eval")

    def _eval(node):
        if isinstance(node, Expression):
            return _eval(node.body)

        if isinstance(node, Constant) and isinstance(node.value, bool):
            return node.value

        if (
            isinstance(node, Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            return node.value

        # Unary operators
        if isinstance(node, UnaryOp) and isinstance(
            node.op,
            (USub, UAdd, Not),
        ):
            value = _eval(node.operand)

            if isinstance(node.op, USub):
                return -value

            if isinstance(node.op, UAdd):
                return value

            if isinstance(node.op, Not):
                return not bool(value)

        # Binary operators
        if isinstance(
            node,
            BinOp
        ) and isinstance(
            node.op,
            (Add, Sub, Mult, Div, BitAnd, BitOr, BitXor)
        ):
            left = _eval(node.left)
            right = _eval(node.right)

            if isinstance(node.op, Add):
                return left + right

            if isinstance(node.op, Sub):
                return left - right

            if isinstance(node.op, Mult):
                return left * right

            if isinstance(node.op, Div):
                if right == 0:
                    raise ZeroDivisionError("division by zero")

                return left / right

            if isinstance(node.op, BitAnd):
                return bool(left) and bool(right)

            if isinstance(node.op, BitOr):
                return bool(left) or bool(right)

            if isinstance(node.op, BitXor):
                return bool(left) ^ bool(right)

        # Comparison operators
        if (
            isinstance(node, Compare)
            and len(node.ops) == 1
            and len(node.comparators) == 1
        ):
            left = _eval(node.left)
            right = _eval(node.comparators[0])
            op = node.ops[0]

            if isinstance(op, Eq):
                return left == right

            if isinstance(op, NotEq):
                return left != right

            if isinstance(op, Gt):
                return left > right

            if isinstance(op, Lt):
                return left < right

            if isinstance(op, GtE):
                return left >= right

            if isinstance(op, LtE):
                return left <= right

        raise ValueError(
            f"Unsupported expression node: {type(node).__name__}"
        )

    return _eval(tree)


def _check_stat_reference(reference: str) -> None:
    """Raise ValueError unless `reference` looks like `user.stat` or `target.stat`."""
    scope, dot, stat = reference.partition(".")
    if not dot or "." in stat:
        raise ValueError(f"'{reference}' must look like user.stat or target.stat.")
    if scope not in TARGET_SCOPES:
        raise ValueError(f"Unknown scope '{scope}' in '{reference}': use user or target.")
    if stat not in TARGET_STATS:
        raise ValueError(f"Unknown stat '{stat}' in '{reference}'.")


def _check_value(text: str) -> None:
    """Raise ValueError unless `text` is a number or a stat reference."""
    try:
        if isfinite(float(text)):
            return
    except ValueError:
        pass

    call = match(r"(\w+)\s*\(", text)
    if call:
        name = call.group(1)
        argument_counts = {"diceratio": 2, "diceattack": 4}
        if name in argument_counts:
            raise ValueError(f"{name} takes {argument_counts[name]} arguments.")
        raise ValueError(f"Unknown function '{name}'.")
    _check_stat_reference(text)


def _check_dice_term(reference: str, percentage: str) -> None:
    """Raise ValueError unless the arguments describe a valid `stat, percentage` pair."""
    _check_stat_reference(reference)
    try:
        int(percentage)
    except ValueError as error:
        raise ValueError(f"The percentage '{percentage}' must be a whole number.") from error


# Define Formula object
@dataclass
class Formula:
    """
    Represents a formula for calculating spell effects in the RPG system.

    Attributes:
        expression (str): The arithmetic expression representing the spell formula.
        template (str): The template string for the spell formula, which may
                        include placeholders for dynamic values.
        placeholders (list[Any]): A list of placeholders used in the template string.
        compilated (bool): Indicates whether the formula has been compiled for evaluation.
    """
    expression: str
    template: str = ""
    placeholders: list[Any] = field(default_factory=list)
    compilated: bool = False

    # methods
    def compile(self) -> None:
        """
        Compile the formula for evaluation.

        This method processes the template and placeholders, preparing the formula
        for safe evaluation. After compilation, the `compilated` attribute is set to True.
        """
        # First we reset the formula template and placeholders
        self.template = ""
        self.placeholders = []

        # We initialize the counter for placeholders
        count = 0

        # We split the expression with the smart_split function
        parts = smart_split(self.expression)

        # Then we iterate through the parts to build the template and placeholders
        for part in parts:
            if part in (
                "+",
                "-",
                "*",
                "/",
                "==",
                "!=",
                "<",
                ">",
                "<=",
                ">=",
                "&",
                "|",
                "^",
                "!",
            ):
                # Operator detected , we just add it to the template
                self.template += f" {part} "
            else:
                # We detect a placeholder here
                if part.startswith("(") and part.endswith(")"):
                    # Here it is a sub formula in paretheses
                    subformula = Formula(part[1:-1])
                    subformula.compile()
                    self.placeholders.append((Formula, subformula))
                else:
                    # Here it is a simple placeholder
                    m = match(r"(\w+)\s*\(([^)]*)\)", part)
                    if m:
                        # We detect the placeholder is a function call
                        func_name = m.group(1)
                        func_args = [arg.strip() for arg in m.group(2).split(",")]
                        if func_name == "diceratio" and len(func_args) == 2:
                            self.placeholders.append((DiceRatio, func_args))
                        elif func_name == "diceattack" and len(func_args) == 4:
                            self.placeholders.append((DiceAttack, func_args))
                        else:
                            # Here we handle unknown function calls as generic placeholders
                            self.placeholders.append((str, part))
                    else:
                        # Here we handle simple placeholders that are not function calls
                        self.placeholders.append((str, part))
                self.template += f"{{{count}}}"
                count += 1

        # After processing all parts, we mark the formula as compiled
        self.compilated = True

    def validate(self) -> None:
        """
        Check that the expression can be evaluated with real stats.

        `evaluate` silently falls back to 0 on most mistakes, so this is the way to
        tell a deliberate 0 from a typo.

        Raises:
            ValueError: Describing the first problem found.
        """
        self.compile()
        if not self.placeholders:
            raise ValueError("The formula is empty.")

        for kind, value in self.placeholders:
            if kind is Formula:
                value.validate()
            elif kind is DiceRatio:
                _check_dice_term(value[0], value[1])
            elif kind is DiceAttack:
                _check_dice_term(value[0], value[1])
                _check_dice_term(value[2], value[3])
            else:
                _check_value(value)

        # A sample number stands in for every placeholder to check the operators around them
        sample = self.template.format(*["1"] * len(self.placeholders))
        try:
            _safe_eval_expression(sample)
        except ZeroDivisionError:
            pass
        except (SyntaxError, ValueError, TypeError) as error:
            raise ValueError("The operators do not form a valid expression.") from error

    def evaluate(
        self,
        user: Entity,
        target: Entity,
        user_dices: Optional[dict[str, int]]=None,
        target_dices: Optional[dict[str, int]]=None
    ) -> int | bool | float:
        """
        Evaluate the compiled formula with the given context.

        Args:
            user (Entity): The entity performing the action.
            target (Entity): The target entity of the action.
            user_dices (Optional[dict[str, int]]): The dice results for the user.
            target_dices (Optional[dict[str, int]]): The dice results for the target.

        Returns:
            int | bool | float: The result of the formula evaluation.
        """
        # First we ensure that the formula is compiled before evaluation.
        if not self.compilated:
            self.compile()

        # Then we evaluate the placeholders with the given context.
        values = []
        for cls, args in self.placeholders:
            if cls == str:
                try:
                    value = int(args)
                except ValueError:
                    try:
                        value = float(args)
                    except ValueError:
                        if "." in args:
                            who, stat = args.split(".")
                            if who == "user":
                                value = user.get_stat(stat)
                            elif who == "target":
                                value = target.get_stat(stat)
                            else:
                                value = 0  # Default value if the placeholder is not recognized
                        else:
                            value = 0  # Default value if the placeholder is not recognized
                values.append(value)
            elif cls == Formula:
                value = args.evaluate(user, target, user_dices, target_dices)
                values.append(value)
            elif cls == DiceRatio:
                who, stat = args[0].split(".")
                ratio = int(args[1])
                dice = None
                dice_value = None
                if who == "user" and user_dices:
                    dice_value = user_dices.get(stat)
                    dice = Dice("1d100", [dice_value]) if dice_value else None
                elif who == "target" and target_dices:
                    dice_value = target_dices.get(stat)
                    dice = Dice("1d100", [dice_value]) if dice_value else None
                if who == "user":
                    value = DiceRatio(stat, ratio).resolve(user, dice)
                elif who == "target":
                    value = DiceRatio(stat, ratio).resolve(target, dice)
                else:
                    value = 0  # Default value if the placeholder is not recognized
                values.append(value)
            elif cls == DiceAttack:
                atk_who, atk_stat = args[0].split(".")
                def_who, def_stat = args[2].split(".")
                atk_ratio = DiceRatio(atk_stat, int(args[1]))
                def_ratio = DiceRatio(def_stat, int(args[3]))

                if atk_who == "user":
                    atk_char = user
                    atk_dice = user_dices.get(atk_stat) if user_dices else None
                elif atk_who == "target":
                    atk_char = target
                    atk_dice = target_dices.get(atk_stat) if target_dices else None
                else:
                    atk_char = None
                    atk_dice = None

                if def_who == "user":
                    def_char = user
                    def_dice = user_dices.get(def_stat) if user_dices else None
                elif def_who == "target":
                    def_char = target
                    def_dice = target_dices.get(def_stat) if target_dices else None
                else:
                    def_char = None
                    def_dice = None
                if atk_dice is not None:
                    atk_dice = Dice("1d100", [atk_dice])
                if def_dice is not None:
                    def_dice = Dice("1d100", [def_dice])

                if atk_char and def_char:
                    value = DiceAttack(atk_ratio, def_ratio).resolve(
                        atk_char,
                        def_char,
                        atk_dice,
                        def_dice
                    )
                else:
                    value = 0
                values.append(value)
            else:
                values.append(0)

        # Evaluate the final result using the template and the computed values
        try:
            result = _safe_eval_expression(self.template.format(*values))
        except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
            result = 0
        return result


# Define Effect Object
@dataclass
class Effect:
    """
    Represents one effect of a spell.
    
    Attributes:
        target (tuple[TargetScope, TargetStat]): The target scope and target stat for the effect.
        operator (Operator): The operator that defines how the effect modifies the target stat.
        formula (Formula): The formula used to calculate the effect's value.
        effect_type (Optional[str]): The type of the effect, if any.
    """
    target: tuple[TargetScope, TargetStat]
    operator: Operator
    formula: Formula
    effect_type: EffectType

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the effect to the JSON form used in spell files.
        """
        return {
            "target": ".".join(self.target),
            "operator": self.operator,
            "formula": self.formula.expression,
            "effect_type": EffectType(self.effect_type).value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Effect:
        """
        Create an effect from the JSON form used in spell files.
        """
        target_scope, target_stat = str(data.get("target", "target.hp")).split(".", 1)
        formula = Formula(str(data.get("formula", "")))
        try:
            formula.compile()
        except (ValueError, SyntaxError):
            pass  # kept for display; it is compiled again before any evaluation
        return cls(
            target=(target_scope, target_stat),  # type: ignore[arg-type]
            operator=data.get("operator", "bonus"),
            formula=formula,
            effect_type=EffectType(data.get("effect_type", "other")),
        )


# Define Spell Object
@dataclass
class Spell:
    """
    Represents a spell with its effects.

    Attributes:
        name (str): The name of the spell.
        description (str): The description of the spell.
        cost (int): The cost of casting the spell.
        targeting (Targeting): The targeting information for the spell.
        runtime_policy (RuntimePolicy): The runtime policy of the spell.
        effects (list[Effect]): The list of effects that the spell has.
        delay (float | int): The delay before the spell takes effect, if any.
        area_size (int): Radius in cells of an "area" spell (square: diagonals count as 1).
        max_targets (Optional[int]): Most targets of a "multi" spell, None for no limit.
    """
    name: str
    description: str
    cost: int
    targeting: Targeting
    runtime_policy: RuntimePolicy
    effects: list[Effect]
    delay: float | int = float("inf")
    trigger_condition: Optional[Formula] = None
    area_size: int = DEFAULT_AREA_SIZE
    max_targets: Optional[int] = None

    def to_public_dict(self) -> dict[str, Any]:
        """
        Summary of the spell as character sheets show it, without any formula.

        Safe to send to the player who knows the spell.
        """
        delay = self.delay
        finite_delay = isinstance(delay, (int, float)) and isfinite(float(delay))
        return {
            "name": self.name,
            "description": self.description,
            "cost": int(self.cost),
            "targeting": str(getattr(self.targeting, "value", self.targeting) or "single"),
            "policy": RuntimePolicy(self.runtime_policy).value,
            "delay": int(delay) if finite_delay else None,
            "area_size": int(self.area_size),
            "max_targets": int(self.max_targets) if self.max_targets else None,
            "effects": [
                {
                    "target": ".".join(effect.target),
                    "operator": str(effect.operator),
                    "effect_type": EffectType(effect.effect_type).value,
                }
                for effect in self.effects
            ],
        }

    # class methods
    @classmethod
    def from_dict(cls, data: dict) -> Spell:
        """
        Creates a Spell instance from a dictionary.

        Args:
            data (dict): The dictionary containing spell data.

        Returns:
            Spell: The created Spell instance.
        """
        effects = []
        delay = data.get("delay", float("inf"))
        for effect_data in data.get("effects", []):
            target_scope, target_stat = effect_data.get("target", "").split(".")
            formula = Formula(effect_data.get("formula", ""))
            formula.compile()
            effect = Effect(
                target=(target_scope, target_stat),
                operator=effect_data.get("operator", ""),
                formula=formula,
                effect_type=EffectType(effect_data.get("effect_type", "other"))
            )
            effects.append(effect)

        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            cost=data.get("cost", 0),
            targeting=data.get("targeting", ""),
            runtime_policy=RuntimePolicy(data.get("runtime_policy", "instant")),
            effects=effects,
            delay=delay,
            trigger_condition=Formula(data.get("trigger_condition", ""))
            if data.get("trigger_condition") else None,
            area_size=max(0, int(data.get("area_size", DEFAULT_AREA_SIZE) or 0)),
            max_targets=int(data["max_targets"]) if data.get("max_targets") else None,
        )

    @classmethod
    def from_name(cls, name: str) -> Optional[Spell]:
        """
        Creates a Spell instance from its name.

        Args:
            name (str): The name of the spell.

        Returns:
            Optional[Spell]: The created Spell instance if found, otherwise None.
        """
        # We try to get the json file of the named spell
        filename = get_current_campaign().spell_path / f"{name.replace(' ', '_').lower()}.json"
        if not filename.exists():
            return None
        with open(filename, "r", encoding="utf-8") as f:
            data = load(f)
        return cls.from_dict(data)
