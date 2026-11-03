#-*- coding: utf-8 -*-

"""
Item module for RPG Tabletop App.

This module defines the Item and Inventory classes and related functionality for managing
items in the game.
"""


# import built-in modules
from __future__ import annotations
from dataclasses import dataclass, field
from json import load


# import local modules
from .campaign import get_current_campaign


# Item class
@dataclass
class Item:
    """
    Represents an item in the RPG Tabletop App.
    
    Attributes:
        name (str): The name of the item.
        description (str): The description of the item.
        modifier (list[tuple[str, int]]): A list of tuples representing the item's modifiers,
                                          where each tuple contains a stat name and its
                                          corresponding modifier value.
    """
    name: str
    description: str
    modifier: list[tuple[str, int]]

    # class methods
    @classmethod
    def from_dict(cls, data: dict) -> Item:
        """
        Create an Item instance from a dictionary.
        """
        return cls(
            name=data["name"],
            description=data["description"],
            modifier=data["modifier"],
        )

    @classmethod
    def from_name(cls, name: str) -> Item:
        """
        Retrieve an Item instance by its name.
        """
        filename = get_current_campaign().item_path / f"{name.lower().replace(' ', '_')}.json"
        with open(filename, "r", encoding="utf-8") as f:
            data = load(f)
            return cls.from_dict(data)
        raise FileNotFoundError(f"Item '{name}' not found at {filename}")


# Inventory class
@dataclass
class Inventory:
    """
    Represents an inventory in the RPG Tabletop App.
    
    Attributes:
        items (dict[str, int]): A dictionary mapping item names to their quantities in the
                                inventory.
        _item_cache (dict[str, Item]): A cache for storing loaded Item instances to avoid
                                       redundant file reads.
    """
    items: dict[str, int] = field(default_factory=dict)
    _item_cache: dict[str, Item] = field(default_factory=dict)

    # methods
    def add_item(self, item_name: str, quantity: int = 1) -> None:
        """
        Add a specified quantity of an item to the inventory.
        
        Args:
            item_name (str): The name of the item to add.
            quantity (int): The number of items to add. Defaults to 1.
        """
        self.items[item_name] = self.items.get(item_name, 0) + quantity

    def remove_item(self, item_name: str, quantity: int = 1) -> None:
        """
        Remove a specified quantity of an item from the inventory.
        
        Args:
            item_name (str): The name of the item to remove from the inventory.
            quantity (int): The number of items to remove. Defaults to 1.
        """
        if item_name not in self.items:
            return
        if self.items[item_name] > quantity:
            self.items[item_name] -= quantity
        else:
            del self.items[item_name]
            self._item_cache.pop(item_name, None)

    def get_stat_modifier(self, stat: str) -> int:
        """
        Calculate the total modifier for a given stat based on the items in the inventory.
        
        Args:
            stat (str): The name of the stat for which to calculate the total modifier.

        Returns:
            int: The total modifier for the specified stat based on the items in the inventory.
        """
        total_modifier = 0
        for item_name, quantity in self.items.items():
            item = self._item_cache.get(item_name)
            # If not already cached, we cache the item after loading it
            if item is None:
                item = Item.from_name(item_name)
                if item is None:
                    continue
                self._item_cache[item_name] = item
            # Apply the item's modifiers to the total modifier for the specified stat
            for modifier_stat, modifier_value in item.modifier:
                if modifier_stat == stat:
                    total_modifier += modifier_value * quantity
        return total_modifier

    def to_list(self) -> list[tuple[str, int]]:
        """
        Convert the inventory items to a list of tuples containing item names and their quantities.
        """
        return list(self.items.items())

    # class methods
    @classmethod
    def from_list(cls, items_list: list[tuple[str, int]]) -> Inventory:
        """
        Create an Inventory instance from a list of item names and their quantities.
        
        Args:
            items_list (list[tuple[str, int]]): A list of tuples where each tuple contains
                                                  an item name and its quantity.
        """
        return cls(items=dict(items_list))
