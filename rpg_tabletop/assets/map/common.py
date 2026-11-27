# -*- coding: utf-8 -*-

"""
File layout, limits, validation issues and errors shared by the map modules.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from PySide6.QtGui import (
    QColor,
)

# Define constants
FORMAT_VERSION = 1
MAP_FILE = "map.json"
MEDIA_DIR = "media"
TILES_DIR = "tiles"
PREVIEW_FILE = "preview.png"
STAGING_DIR = ".staging"
# Kept with the tiles: the compilation and the scene they show (incremental builds)
TILE_INDEX_FILE = "_index.json"

DEFAULT_TILE_SIZE = 512
DEFAULT_TILE_FORMAT = "webp"
TILE_FORMATS = ("webp", "png")
PREVIEW_MAX_PX = 512

# Safety limit, not a technical one: tiles are rendered one at a time
MAX_MAP_SIDE_PX = 65536

# Infinite maps: empty space kept around the content when the map is sized at
# compilation, in grid cells (or in pixels when the map has no grid).
AUTO_SIZE_MARGIN_CELLS = 1
AUTO_SIZE_MARGIN_PX = 64

ERROR = "error"
WARNING = "warning"


class MapIssue(NamedTuple):
    """A problem found in a map: errors block saving, warnings do not."""

    severity: str
    message: str


class MapCompileCancelled(Exception):
    """Raised when the progress callback asked to stop the compilation."""


def _canonical(value: Any) -> Any:
    """
    Normalize numbers (`10` and `10.0` are the same) so equal scenes hash equally.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _is_color(value: Any) -> bool:
    return isinstance(value, str) and QColor(value).isValid()
