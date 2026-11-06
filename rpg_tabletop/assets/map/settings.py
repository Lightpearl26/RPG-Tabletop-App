# -*- coding: utf-8 -*-

"""
Map-wide settings: background, grid, initial camera and initial token placements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from pathlib import Path
from typing import Optional
from uuid import uuid4

from PySide6.QtGui import (
    QColor,
    QPainter,
)

from .geometry import Rect, Vec2
from .media import MediaCache

# =============================================================================
# Background, grid, camera, tokens
# =============================================================================


@dataclass(slots=True)
class MapBackground:
    """The color of the map and an optional image stretched over all of it."""

    color: str = "#1e1e2e"
    image_asset: Optional[Path] = None

    def render(self, painter: QPainter, media: MediaCache, map_rect: Rect) -> None:
        """
        Draw the background over `map_rect`.
        """
        painter.fillRect(map_rect.to_qrectf(), QColor(self.color))
        if self.image_asset is not None:
            painter.drawImage(map_rect.to_qrectf(), media.get(self.image_asset))

    def to_dict(self) -> dict:
        """
        Serialize the background.
        """
        return {
            "color": self.color,
            "image_asset": self.image_asset.as_posix() if self.image_asset else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MapBackground:
        """
        Create a background from its serialized form.
        """
        image = data.get("image_asset")
        return cls(
            color=str(data.get("color", "#1e1e2e")),
            image_asset=Path(str(image)) if image else None,
        )


@dataclass(slots=True)
class GridSettings:
    """
    The grid. It is an overlay: the editor and the runtime draw it, the flatten
    never does. The runtime also uses it to snap tokens.
    """

    enabled: bool = True
    cell_size: int = 64
    offset_x: float = 0.0
    offset_y: float = 0.0
    color: str = "#4a4a4a"
    opacity: float = 0.35

    def cell_center(self, point: Vec2) -> Vec2:
        """
        Return the center of the grid cell that contains `point`.
        """
        size = float(self.cell_size)
        column = floor((point.x - self.offset_x) / size)
        row = floor((point.y - self.offset_y) / size)
        return Vec2(
            self.offset_x + (column + 0.5) * size,
            self.offset_y + (row + 0.5) * size,
        )

    def to_dict(self) -> dict:
        """
        Serialize the grid settings.
        """
        return {
            "enabled": self.enabled,
            "cell_size": self.cell_size,
            "offset": [self.offset_x, self.offset_y],
            "color": self.color,
            "opacity": self.opacity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GridSettings:
        """
        Create grid settings from their serialized form.
        """
        offset = Vec2.from_value(data.get("offset", [0, 0]))
        return cls(
            enabled=bool(data.get("enabled", True)),
            cell_size=int(data.get("cell_size", 64)),
            offset_x=offset.x,
            offset_y=offset.y,
            color=str(data.get("color", "#4a4a4a")),
            opacity=float(data.get("opacity", 0.35)),
        )


@dataclass(slots=True)
class CameraState:
    """Where the GM camera starts when the map is opened."""

    center: Vec2 = field(default_factory=Vec2)
    zoom: float = 1.0

    def to_dict(self) -> dict:
        """
        Serialize the camera.
        """
        return {"center": self.center.to_list(), "zoom": self.zoom}

    @classmethod
    def from_dict(cls, data: dict) -> CameraState:
        """
        Create a camera from its serialized form.
        """
        return cls(
            center=Vec2.from_value(data.get("center", [0, 0])),
            zoom=float(data.get("zoom", 1.0)),
        )


@dataclass(slots=True)
class InitialTokenPlacement:
    """
    Where an entity starts on the map.

    A token is a reference to a persistent entity by its uuid, so renaming the
    entity never breaks the map. Everything else (name, character, icon, bars)
    is read from the entity.
    """

    entity_id: str
    pos: Vec2 = field(default_factory=Vec2)
    size_cells: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    hidden: bool = False
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict:
        """
        Serialize the token placement.
        """
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "pos": self.pos.to_list(),
            "size_cells": self.size_cells,
            "rotation": self.rotation,
            "opacity": self.opacity,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InitialTokenPlacement:
        """
        Create a token placement from its serialized form.

        Raises:
            ValueError: If a field is invalid.
        """
        try:
            return cls(
                entity_id=str(data["entity_id"]),
                pos=Vec2.from_value(data.get("pos", [0, 0])),
                size_cells=float(data.get("size_cells", 1.0)),
                rotation=float(data.get("rotation", 0.0)),
                opacity=float(data.get("opacity", 1.0)),
                hidden=bool(data.get("hidden", False)),
                id=str(data.get("id") or uuid4().hex),
            )
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f"Invalid token placement: {error}") from error
