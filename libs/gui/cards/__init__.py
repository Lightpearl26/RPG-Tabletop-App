#-*- coding: utf-8 -*-

"""
GUI cards library for the RPG Tabletop App.
"""

# Contants
STAT_PALETTE: dict[str, str] = {
    "str": "#E35D6A",
    "con": "#E39A4A",
    "dex": "#4C8ED9",
    "agi": "#43B6D7",
    "int": "#6E68D8",
    "wis": "#9B6BDB",
    "cha": "#D76AAE",
    "luc": "#D8B84E",
    "sur": "#58C26A",
    "per": "#2F7D5A",
}
STAT_NAMES = (
    "str",
    "con",
    "dex",
    "agi",
    "int",
    "wis",
    "cha",
    "luc",
    "sur",
    "per",
)


RESOURCE_PALETTE: dict[str, str] = {
    "hp": "#E35D6A",
    "stamina": "#D8B84E",
    "drug_health": "#B06BD3",
    "mental_health": "#7A76D8",
}


DEFAULT_FIELD_COLOR = "#7F8A9B"


def field_color(
    field_name: str,
) -> str:
    """
    Return the UI color associated with a stat/resource field.
    """
    field_name = field_name.lower()

    return (
        STAT_PALETTE.get(field_name)
        or RESOURCE_PALETTE.get(field_name)
        or DEFAULT_FIELD_COLOR
    )


# Local module imports
from . import base
from . import character
from . import entity
from . import item
from . import formula
from . import map
from . import session
from . import spell


__all__ = [
    "base",
    "character",
    "entity",
    "item",
    "formula",
    "map",
    "session",
    "spell",
]
