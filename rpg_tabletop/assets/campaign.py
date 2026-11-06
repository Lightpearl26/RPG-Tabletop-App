#-*- coding: utf-8 -*-
#pylint: disable=global-variable-not-assigned, global-statement

"""
Campaign module for RPG Tabletop App.

This module defines the Campaign class and related functionality for managing
files related to campaigns in the RPG Tabletop App. Each campaign typically has
its own set of assets such as characters, items, and spells.

The campaign class gives a structured way to access and manage these assets,
ensuring that each campaign's data is organized and easily retrievable. It also
manages runtime saving of campaign data during the application's execution.

A save state is called a "Session".
"""


# import built-in modules
from __future__ import annotations
from typing import TYPE_CHECKING, Generic, TypeVar
from typing import Optional
from json import dump
from pathlib import Path


if TYPE_CHECKING:
    from rpg_tabletop.session.entity import Entity
    from .spell import Spell

# Define types and constants
T = TypeVar("T")
_CURRENT_CAMPAIGN: Optional[Campaign] = None


# Create registries
class Registry(Generic[T]):
    """
    Generic registry class for managing runtime objects within a campaign.
    
    This registry only operates during runtime sessions.
    """
    def __init__(self):
        self._objects: dict[str, T] = {}

    def register(self, object_id: str, obj: T):
        """
        Register an object within the campaign.

        Args:
            object_id (str): The unique identifier for the object.
            obj: The object instance to register.
        """
        self._objects[object_id] = obj

    def get(self, object_id: str) -> Optional[T]:
        """
        Retrieve an object by its unique identifier.

        Args:
            object_id (str): The unique identifier for the object.

        Returns:
            The object instance if found, otherwise None.
        """
        return self._objects.get(object_id)

    def unregister(self, object_id: str):
        """
        Unregister an object by its unique identifier.

        Args:
            object_id (str): The unique identifier for the object.
        """
        if object_id in self._objects:
            del self._objects[object_id]

    def require(self, object_id: str) -> T:
        """
        Retrieve an object by its unique identifier, raising an error if not found.

        Args:
            object_id (str): The unique identifier for the object.

        Returns:
            The object instance if found.

        Raises:
            KeyError: If the object is not found in the registry.
        """
        if object_id not in self._objects:
            raise KeyError(f"Object with ID '{object_id}' not found in registry.")
        return self._objects[object_id]

    def values(self) -> list[T]:
        """
        Get a list of all object instances in the registry.

        Returns:
            list[T]: A list of all object instances.
        """
        return list(self._objects.values())

    def items(self) -> list[tuple[str, T]]:
        """
        Get a list of all object ID and instance pairs in the registry.

        Returns:
            list[tuple[str, T]]: A list of tuples containing object IDs
                                 and their corresponding instances.
        """
        return list(self._objects.items())

    def keys(self) -> list[str]:
        """
        Get a list of all object IDs in the registry.

        Returns:
            list[str]: A list of all object IDs.
        """
        return list(self._objects.keys())

    def clear(self) -> None:
        """
        Clear all objects from the registry.
        """
        self._objects.clear()

    # special methods
    def __contains__(self, object_id: str) -> bool:
        """
        Check if an object with the given ID exists in the registry.

        Args:
            object_id (str): The unique identifier for the object.

        Returns:
            bool: True if the object exists, False otherwise.
        """
        return object_id in self._objects

    def __len__(self) -> int:
        """
        Get the number of objects in the registry.

        Returns:
            int: The number of objects in the registry.
        """
        return len(self._objects)

    def __iter__(self):
        """
        Iterate over the object IDs in the registry.

        Returns:
            Iterator[str]: An iterator over the object IDs.
        """
        return iter(self._objects.values())


# Create Campaign class
class Campaign:
    """
    Campaign class represents a single campaign in the RPG Tabletop App.
    
    Attributes:
        name (str): The name of the campaign.
        root (Path): The root directory of the campaign's files.
        character_path (Path): Path to the characters directory within the campaign's root
                               directory.
        item_path (Path): Path to the items directory within the campaign's root directory.
        spell_path (Path): Path to the spells directory within the campaign's root directory.
        maps_path (Path): Path to the maps directory within the campaign's root directory.
        session_path (Path): Path to the sessions directory within the campaign's root directory.
        json (Path): Path to the campaign's JSON file within the campaign's root directory.
    """
    def __init__(self, name, root: Path):
        self.name = name
        self.root = Path(root)
        self.entities: Registry[Entity] = Registry()
        self.spells: Registry[Spell] = Registry()
        self._restored_spell_effects: dict = {}

    # properties
    @property
    def character_path(self) -> Path:
        """
        Path to the characters directory within the campaign's root directory.
        """
        return self.root / "characters"

    @property
    def item_path(self) -> Path:
        """
        Path to the items directory within the campaign's root directory.
        """
        return self.root / "items"

    @property
    def spell_path(self) -> Path:
        """
        Path to the spells directory within the campaign's root directory.
        """
        return self.root / "spells"

    @property
    def maps_path(self) -> Path:
        """
        Path to the maps directory within the campaign's root directory.
        """
        return self.root / "maps"

    @property
    def map_media_path(self) -> Path:
        """
        Path to the map media directory within the campaign's root directory.
        """
        return self.maps_path / "media"

    @property
    def sessions_path(self) -> Path:
        """
        Path to the sessions directory within the campaign's root directory.
        """
        return self.root / "sessions"

    @property
    def entity_path(self) -> Path:
        """
        Path to the entities directory within the campaign's root directory.
        """
        return self.root / "entities"

    @property
    def json(self) -> Path:
        """
        Path to the campaign's JSON file within the campaign's root directory.
        """
        return self.root / "campaign.json"

    # methods
    def ensure_directories(self):
        """
        Ensure that all necessary directories for the campaign exist within
        the campaign's root directory.
        """
        for directory in (
            self.character_path,
            self.item_path,
            self.spell_path,
            self.maps_path,
            self.sessions_path,
            self.map_media_path,
            self.entity_path,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def map_path(self, map_name: str) -> Path:
        """
        Path to a specific map directory.

        Args:
            map_name (str): The name of the map file.

        Returns:
            Path: The full path to the specified map directory.
        """
        return self.maps_path / map_name

    def session_path(self, session_name: str) -> Path:
        """
        Path to a specific session directory.

        Args:
            session_name (str): The name of the session.

        Returns:
            Path: The full path to the specified session directory.
        """
        return self.sessions_path / session_name


# create functions for managing campaigns
def get_current_campaign() -> Campaign:
    """
    Get the current active campaign. If no campaign is set, a default
    campaign will be created and set as the current campaign.
    """
    global _CURRENT_CAMPAIGN
    if _CURRENT_CAMPAIGN is None:
        set_current_campaign(Campaign("Unnamed Campaign", Path.cwd() / "default_campaign"))
    assert _CURRENT_CAMPAIGN is not None
    return _CURRENT_CAMPAIGN

def set_current_campaign(campaign: Campaign):
    """
    Set the current active campaign.
    """
    global _CURRENT_CAMPAIGN
    campaign.ensure_directories()
    with open(campaign.json, "w", encoding="utf-8") as file:
        dump({
            "name": campaign.name,
            "root": str(campaign.root)
        }, file, ensure_ascii=False, indent=4)
    _CURRENT_CAMPAIGN = campaign
    return _CURRENT_CAMPAIGN
