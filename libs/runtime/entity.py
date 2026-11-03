#-*- coding: utf-8 -*-

"""
Entity module for the RPG Tabletop App.

This module defines the Entity class which is the object used during runtime
to emulate a character during a session whereas the Character class represents
the static definition of a character outside of a session.
"""

# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
from os.path import exists
from pathlib import Path
from uuid import uuid4, UUID
from json import load

# import local modules
from ..assets.character import Character
from ..assets.spell import Spell
from ..assets.item import Inventory
from .spell_event import SpellEvent
from .spell_effect import SpellEffect, EffectType
from ..dice import Dice, DiceRatio
from ..assets.campaign import get_current_campaign

if TYPE_CHECKING:
    ...


# Create StatsModifier class
@dataclass
class StatsModifier:
    """
    Represents a modifier to the stats of an entity.
    Attributes:
        hp (int): The modifier value for the hit points stat.
        str (int): The modifier value for the strength stat.
        dex (int): The modifier value for the dexterity stat.
        con (int): The modifier value for the constitution stat.
        int (int): The modifier value for the intelligence stat.
        wis (int): The modifier value for the wisdom stat.
        cha (int): The modifier value for the charisma stat.
        per (int): The modifier value for the perception stat.
        agi (int): The modifier value for the agility stat.
        luc (int): The modifier value for the luck stat.
        sur (int): The modifier value for the survival stat.
        stamina (int): The modifier value for the stamina stat.
        mental_health (int): The modifier value for the mental health stat.
        drug_health (int): The modifier value for the drug health stat.
    """
    hp: int = 0
    str: int = 0
    dex: int = 0
    con: int = 0
    int: int = 0
    wis: int = 0
    cha: int = 0
    per: int = 0
    agi: int = 0
    luc: int = 0
    sur: int = 0
    stamina: int = 0
    mental_health: int = 0
    drug_health: int = 0

    # class methods
    @classmethod
    def from_dict(cls, data: dict) -> StatsModifier:
        """
        Creates a StatsModifier instance from a dictionary.

        Args:
            data (dict): A dictionary containing the stat modifier values.

        Returns:
            StatsModifier: An instance of the StatsModifier class.
        """
        return cls(
            hp=data.get("hp", 0),
            str=data.get("str", 0),
            dex=data.get("dex", 0),
            con=data.get("con", 0),
            int=data.get("int", 0),
            wis=data.get("wis", 0),
            cha=data.get("cha", 0),
            per=data.get("per", 0),
            agi=data.get("agi", 0),
            luc=data.get("luc", 0),
            sur=data.get("sur", 0),
            stamina=data.get("stamina", 0),
            mental_health=data.get("mental_health", 0),
            drug_health=data.get("drug_health", 0),
        )

    def to_dict(self) -> dict:
        """
        Convert the StatsModifier instance to a dictionary representation.

        Returns:
            dict: The dictionary representation of the StatsModifier instance.
        """
        return {
            "hp": self.hp,
            "str": self.str,
            "dex": self.dex,
            "con": self.con,
            "int": self.int,
            "wis": self.wis,
            "cha": self.cha,
            "per": self.per,
            "agi": self.agi,
            "luc": self.luc,
            "sur": self.sur,
            "stamina": self.stamina,
            "mental_health": self.mental_health,
            "drug_health": self.drug_health,
        }


# Create Entity class
@dataclass
class Entity:
    """
    The Entity class represents a character during a runtime session.
    
    It combines the base stats from the Character class with any runtime modifications,
    such as temporary buffs, debuffs, and equipment effects.
    
    Attributes:
        name (str): The name of the entity.
        character (Character): The base character associated with the entity.
        stats_modifier (StatsModifier): The runtime modifications to the character's stats.
        spell_events (list[SpellEvent]): The list of spell events affecting the entity.
        spell_effects (list[SpellEffect]): The list of spell effects currently applied to
                                           the entity.
        items (Inventory): The inventory of the character attached to the entity.
        hp (int): The current hit points of the entity.
        str (int): The current strength of the entity.
        dex (int): The current dexterity of the entity.
        con (int): The current constitution of the entity.
        int (int): The current intelligence of the entity.
        wis (int): The current wisdom of the entity.
        cha (int): The current charisma of the entity.
        per (int): The current perception of the entity.
        agi (int): The current agility of the entity.
        luc (int): The current luck of the entity.
        sur (int): The current survival of the entity.
        stamina (int): The current stamina of the entity.
        mental_health (int): The current mental health of the entity.
        drug_health (int): The current drug health of the entity.
    """
    name: str
    character: Character
    spells: dict[str, Spell] = field(default_factory=dict)
    items: Inventory = field(default_factory=Inventory)
    id: UUID = field(default_factory=uuid4)
    stats_modifier: StatsModifier = field(default_factory=StatsModifier)
    spell_events: list[SpellEvent] = field(default_factory=list)
    spell_effects: list[SpellEffect] = field(default_factory=list)

    # properties
    @property
    def hp(self) -> int:
        """
        Returns the current hp of the entity.
        """
        return self.get_stat("hp")

    @property
    def str(self) -> int:
        """
        Returns the current strength of the entity.
        """
        return self.get_stat("str")

    @property
    def dex(self) -> int:
        """
        Returns the current dexterity of the entity.
        """
        return self.get_stat("dex")

    @property
    def con(self) -> int:
        """
        Returns the current constitution of the entity.
        """
        return self.get_stat("con")

    @property
    def int(self) -> int:
        """
        Returns the current intelligence of the entity.
        """
        return self.get_stat("int")

    @property
    def wis(self) -> int:
        """
        Returns the current wisdom of the entity.
        """
        return self.get_stat("wis")

    @property
    def cha(self) -> int:
        """
        Returns the current charisma of the entity.
        """
        return self.get_stat("cha")

    @property
    def per(self) -> int:
        """
        Returns the current perception of the entity.
        """
        return self.get_stat("per")

    @property
    def agi(self) -> int:
        """
        Returns the current agility of the entity.
        """
        return self.get_stat("agi")

    @property
    def luc(self) -> int:
        """
        Returns the current luck of the entity.
        """
        return self.get_stat("luc")

    @property
    def sur(self) -> int:
        """
        Returns the current survival of the entity.
        """
        return self.get_stat("sur")

    @property
    def mental_health(self) -> int:
        """
        Returns the current mental health of the entity.
        """
        return self.get_stat("mental_health")

    @property
    def drug_health(self) -> int:
        """
        Returns the current drug health of the entity.
        """
        return self.get_stat("drug_health")

    @property
    def stamina(self) -> int:
        """
        Returns the current stamina of the entity.
        """
        return self.get_stat("stamina")

    @property
    def damage_amplification(self) -> int:
        """
        Returns the current damage amplification of the entity.
        """
        return sum(
            effect.delta
            for effect in self.spell_effects
            if effect.effect_type == EffectType.DAMAGE_AMPLIFICATION
        )

    @property
    def damage_reduction(self) -> int:
        """
        Returns the current damage reduction of the entity.
        """
        return min(100, sum( # We ensure that damage reduction don't exceed 100%
            effect.delta
            for effect in self.spell_effects
            if effect.effect_type == EffectType.DAMAGE_REDUCTION
        ))

        # properties

    @property
    def token(self) -> Optional[Path]:
        """
        Get the file path of the character's token image.

        Returns:
            Optional[Path]: The file path of the character's token image, or None if not set.
        """
        token_path = get_current_campaign().entity_path / "tokens" / f"{self.name.lower().replace(" ", "_")}.webp"
        return Path(token_path) if exists(token_path) else None

    # methods
    def get_stat(self, stat_name: str) -> int:
        """
        Returns the current value of the specified stat for the entity.
        """
        base_stat = getattr(self.character.stats, stat_name, 0)
        modifier = getattr(self.stats_modifier, stat_name, 0)
        item_modifier = self.items.get_stat_modifier(stat_name)
        spell_modifier = sum(
            effect.delta for effect in self.spell_effects if effect.target_stat == stat_name
            and effect.effect_type not in (
                EffectType.DAMAGE_AMPLIFICATION, EffectType.DAMAGE_REDUCTION
            ) # Exclude damage amplification and reduction effects from stat calculation even
            # if they target the smae stat. The stat indicated in the effect for damage
            # amplification and reduction is only here for placeholder purposes.
        )
        return base_stat + modifier + item_modifier + spell_modifier

    def get_stat_max(self, stat_name: str) -> int:
        """
        Maximum of a resource: base stat + items, without runtime changes
        (spell effects, spent stamina). Used for resource bars.
        """
        return self.get_stat(stat_name)

    def strike(
        self,
        target: Entity,
        user_dices: Optional[Dice]=None,
        target_dices: Optional[Dice]=None
    ) -> str:
        """
        Executes a strike action against the target entity.

        Args:
            target (Entity): The target entity to strike.
            user_dices (Optional[Dice]): The dice used by the user for the strike.
            target_dices (Optional[Dice]): The dice used by the target for defense.

        Returns:
            str: A dice command describing the outcome of the strike.
        """
        if not user_dices:
            user_dices = Dice.roll("1d100")
        if not target_dices:
            target_dices = Dice.roll("1d100")
        user_value = DiceRatio("str", 100).resolve(self, user_dices)
        target_value = DiceRatio("con", 100).resolve(target, target_dices)
        nb_dice = max(0, (user_value - target_value)//10)

        dice_value = (
            0 if not user_dices.critical_success and target_dices.critical_success else
            0 if user_dices.critical_failure and not target_dices.critical_failure else
            6 if user_dices.critical_success and target_dices.critical_success else
            12 if user_dices.critical_success and target_dices.critical_failure else
            8 if user_dices.critical_success or target_dices.critical_failure else
            4
        )
        return f"{nb_dice}d{dice_value}"

    def shoot(
        self,
        target: Entity,
        user_dices: Optional[Dice]=None,
        target_dices: Optional[Dice]=None
    ) -> str:
        """
        Executes a shoot action against the target entity.

        Args:
            target (Entity): The target entity to shoot.
            user_dices (Optional[Dice]): The dice used by the user for the shoot.
            target_dices (Optional[Dice]): The dice used by the target for defense.

        Returns:
            str: A dice command describing the outcome of the shoot.
        """
        if not user_dices:
            user_dices = Dice.roll("1d100")
        if not target_dices:
            target_dices = Dice.roll("1d100")
        user_value = DiceRatio("dex", 100).resolve(self, user_dices)
        target_value = DiceRatio("agi", 100).resolve(target, target_dices)
        nb_dice = max(0, (user_value - target_value)//10)

        dice_value = (
            0 if not user_dices.critical_success and target_dices.critical_success else
            0 if user_dices.critical_failure and not target_dices.critical_failure else
            6 if user_dices.critical_success and target_dices.critical_success else
            12 if user_dices.critical_success and target_dices.critical_failure else
            8 if user_dices.critical_success or target_dices.critical_failure else
            4
        )
        return f"{nb_dice}d{dice_value}"

    def get_spell(self, spell_name: str) -> Optional[Spell]:
        """
        Get a spell by its name from the entity's spells.

        Args:
            spell_name (str): The name of the spell to retrieve.

        Returns:
            Optional[Spell]: The spell if found, None otherwise.
        """
        return self.spells.get(spell_name)

    def cast_spell(
        self,
        spell_name: str,
        targets: list[Entity],
        user_dices: Optional[dict[str, int]]=None,
        targets_dices: Optional[dict[str, dict[str, int]]]=None
    ) -> bool:
        """
        Executes a cast spell action against the target entities.

        Args:
            spell_name (str): The name of the spell to cast.
            targets (list[Entity]): The list of target entities for the spell.
            user_dices (Optional[dict[str, int]]): The dice used by the user for the spell.
            targets_dices (Optional[dict[str, dict[str, int]]]): The dice used by the targets
                                                                 for defense.

        Returns:
            bool: True if the spell was successfully cast, False otherwise.
        """
        spell = self.get_spell(spell_name)
        if spell is None:
            return False

        campaign = get_current_campaign()
        campaign.spells.register(spell.name, spell)
        campaign.entities.register(str(self.id), self)
        for entity in targets:
            campaign.entities.register(str(entity.id), entity)

        spell_event = SpellEvent(
            spell_id = spell.name,
            caster_id = str(self.id),
            targets_id = [str(entity.id) for entity in targets],
            effects = [],
            runtime_policy = spell.runtime_policy
        )

        spell_event.apply(
            [str(target.id) for target in targets],
            user_dices=user_dices,
            targets_dices=targets_dices
        )

        self.spell_events.append(spell_event)
        return True

    def apply_damage(self, stat_name: str, amount: int) -> tuple[int, list[SpellEffect]]:
        """
        Apply damage to an entity, absorbing with shields first (cascade by duration).
        
        Shields are consumed in order of expiration (FIFO: duration_remaining ascending).
        Preferred shield model is a positive HP bonus (delta > 0) with effect_type=SHIELD.
        When a shield is fully consumed, it is removed from spell_effects.
        Residual damage that exceeds shield absorption still applies as normal.
        
        We also apply damage reduction to the residual damage before shield absorption.
        
        Arguments:
            stat_name(str): The stat to damage (e.g., "hp", "stamina")
            amount(int): The base damage amount (>0 for damage, <0 for healing)
        
        Returns:
            tuple[int, list[SpellEffect]]:
                - residual_damage (int): remaining damage after shield absorption
                - consumed_shields (list[SpellEffect]): shields that were fully consumed
        
        Example:
            If entity has shield of 30 and takes 50 damage:
            - Shield absorbs 30, becomes 0, is removed
            - Entity takes 20 real damage
            - Returns (20, [shield_effect])
        """
        consumed_shields : list[SpellEffect] = []
        # We apply damage reduction to the incoming damage before considering shields.
        residual_damage = max(0, amount * (100 - self.damage_reduction) // 100)

        if amount <= 0:
            return residual_damage, consumed_shields

        # Get shields of this stat
        shields_on_stat = [
            effect
            for effect in self.spell_effects
            if effect.target_stat == stat_name
            and effect.effect_type == EffectType.SHIELD
        ]
        shields_on_stat.sort(
            key=lambda e: e.duration
            if e.duration > 0 else float('inf')
        )

        # consume shields in order of expiration (FIFO)
        for shield in shields_on_stat:
            if residual_damage <= 0:
                break
            if shield.delta > 0:
                absorbed = min(residual_damage, shield.delta)
                residual_damage -= absorbed
                shield.delta -= absorbed
                if shield.delta <= 0:
                    consumed_shields.append(shield)
                    self.spell_effects.remove(shield)

        return residual_damage, consumed_shields

    # class methods
    @classmethod
    def from_character(cls, entity_name: str, character_name: str) -> Optional[Entity]:
        """
        Create an Entity instance from a character.
        
        Arguments:
            entity_name (str): The name of the entity to create.
            character_name (str): The name of the character to base the entity on.

        Returns:
            Optional[Entity]: The created Entity instance, or None if the character does not exist.
        """
        character = Character.from_name(character_name)
        if character is None:
            return None

        filename = (
            get_current_campaign().entity_path /
            f"{entity_name.lower().replace(' ', '_')}.json"
        )
        try:
            with open(filename, "r", encoding="utf-8") as f:
                raw = load(f)
        except FileNotFoundError:
            raw = {}

        stat_modifier = StatsModifier.from_dict(raw.get("stat_modifier", {}))

        spells = {}
        for spell_name in raw.get("spells", []):
            spell_name: str
            spell = get_current_campaign().spells.get(spell_name)
            if not spell:
                spell = Spell.from_name(spell_name)
                if spell:
                    get_current_campaign().spells.register(spell_name, spell)
            if spell:
                spells[spell_name] = spell

        items = Inventory.from_list(
            raw.get("items", raw.get("inventory", []))
        )

        return cls(
            name=entity_name,
            character=character,
            stats_modifier=stat_modifier,
            spells=spells,
            items=items,
        )

    @classmethod
    def from_name(cls, entity_name: str) -> Optional[Entity]:
        """
        Create an Entity instance from its name.

        Arguments:
            entity_name (str): The name of the entity to create.

        Returns:
            Optional[Entity]: The created Entity instance, or None if the entity does not exist.
        """
        filename = (
            get_current_campaign().entity_path /
            f"{entity_name.lower().replace(' ', '_')}.json"
        )
        try:
            with open(filename, "r", encoding="utf-8") as f:
                raw = load(f)
        except FileNotFoundError:
            return None

        character_name = raw.get("character")
        if not character_name:
            return None

        return cls.from_character(entity_name, character_name)

    def to_dict(self) -> dict:
        """
        Convert the Entity instance to a dictionary representation.

        Returns:
            dict: The dictionary representation of the Entity instance.
        """
        return {
            "name": self.name,
            "character": self.character.name if self.character else None,
            "stat_modifier": self.stats_modifier.to_dict() if self.stats_modifier else {},
            "spells": list(self.spells.keys()) if self.spells else [],
            "items": self.items.to_list() if self.items else [],
        }
