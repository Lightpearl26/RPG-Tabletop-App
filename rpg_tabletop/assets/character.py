#-*- coding: utf-8 -*-

"""
Character module for RPG Tabletop App.

This module defines the Character class and related functionality for managing
character attributes, stats, and other character-related operations in the game.
"""


# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from json import load
from os.path import exists

# import local modules
from .campaign import get_current_campaign


# Create the Stat class for character attributes
@dataclass
class Stats:
    """
    A class representing the stats of a character in the RPG Tabletop App.

    This class can be used to manage and access various character attributes
    such as strength, dexterity, intelligence, and other relevant stats.
    """
    # basic stats
    str: int = 0
    dex: int = 0
    int: int = 0
    con: int = 0
    wis: int = 0
    agi: int = 0
    per: int = 0
    cha: int = 0
    luc: int = 0
    sur: int = 0

    # consumable stats (e.g., health, mana, stamina)
    mental_health: int = 100
    drug_health: int = 100

    # properties
    @property
    def hp(self) -> int:
        """
        Calculate and return the character's hit points (HP) based on their constitution and wisdom.

        Returns:
            int: The calculated hit points of the character.
        """
        return 10 + (self.con + self.wis) // 10

    @property
    def stamina(self) -> int:
        """
        Calculate and return the character's stamina based on their dexterity and perception.

        Returns:
            int: The calculated stamina of the character.
        """
        stat_ratio = 0.5*self.per + 0.35*self.dex + 0.15*self.agi
        return int(60 + 0.8*stat_ratio)

    @property
    def level(self) -> int:
        """
        Calculate and return the character's level based on their experience or stats.

        Returns:
            int: The calculated level of the character.
        """
        total_stats = (
            self.str +
            self.dex +
            self.int +
            self.con +
            self.wis +
            self.agi +
            self.per +
            self.cha +
            self.luc +
            self.sur
        )
        return (total_stats - 500) // 5


# Create the Character class
@dataclass
class Character:
    """
    A class representing a character in the RPG Tabletop App.

    Attributes:
        name (str): The name of the character.
        stats (Stats): The character's stats.
        spells (dict[str, Spell]): The character's spells.
        items (Inventory): The character's inventory.
    """
    name: str
    stats: Stats = field(default_factory=Stats)

    # class methods
    @classmethod
    def from_name(cls, name: str) -> Character:
        """
        Create a Character instance based on the character's name.

        Args:
            name (str): The name of the character.

        Returns:
            Character: A new Character instance with the given name.
        """
        filename = get_current_campaign().character_path / f"{name.replace(' ', '_').lower()}.json"
        if exists(filename):
            with open(filename, "r", encoding="utf-8") as file:
                data = load(file)
            return cls.from_dict(data)
        raise FileNotFoundError(f"Character file '{filename}' not found.")

    @classmethod
    def from_dict(cls, data: dict) -> Character:
        """
        Create a Character instance from a dictionary of character data.

        Args:
            data (dict): A dictionary containing character attributes and stats.

        Returns:
            Character: A new Character instance populated with the provided data.
        """
        stats = Stats(**data.get("stats", {}))
        return cls(
            name=data.get("name", ""),
            stats=stats,
        )
