# -*- coding: utf-8 -*-

"""
Colors of the applications.

`COLORS` holds the named colors of the dark theme. The stylesheets refer to them as
``$name`` (see `stylesheets.py`) and the Qt palette uses the same values, so a color is
changed in one place only.

`STAT_PALETTE` and `RESOURCE_PALETTE` give each statistic its color across every view.
"""

COLORS: dict[str, str] = {
    # Backgrounds, from the deepest to the most raised
    "window": "#171a21",
    "sunken": "#171c24",
    "surface": "#1c2028",
    "base": "#20252e",
    "header": "#252b35",
    "button": "#292f3a",
    "button_hover": "#333b48",
    # Borders
    "border": "#303642",
    "input_border": "#394250",
    "border_strong": "#3a4351",
    # Text
    "text": "#e5e9ef",
    "text_bright": "#dfe4ec",
    "button_text": "#d9dee7",
    "text_secondary": "#aeb7c4",
    "text_section": "#b9c1ce",
    "muted": "#98a2b1",
    "disabled": "#6f7a8a",
    # Accent (purple)
    "accent": "#7356b5",
    "accent_hover": "#8061c5",
    "accent_border": "#8769ca",
    "accent_bright": "#9b6bdb",
    "accent_soft": "#302a3f",
    "accent_text": "#f0eaff",
    "link": "#b59be0",
}


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

RESOURCE_PALETTE: dict[str, str] = {
    "hp": "#E35D6A",
    "stamina": "#D8B84E",
    "drug_health": "#B06BD3",
    "mental_health": "#7A76D8",
}

DEFAULT_FIELD_COLOR = "#7F8A9B"


def field_color(field_name: str) -> str:
    """
    Return the UI color associated with a stat/resource field.
    """
    field_name = field_name.lower()

    return (
        STAT_PALETTE.get(field_name)
        or RESOURCE_PALETTE.get(field_name)
        or DEFAULT_FIELD_COLOR
    )
