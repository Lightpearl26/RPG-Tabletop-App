# -*- coding: utf-8 -*-

"""
Scene elements of a map (images, drawings, texts, walls) and their rendering.

The order of `MapAsset.elements` is the z-order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import hypot
from pathlib import Path
from typing import ClassVar, Optional
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
    QPolygonF,
)

from .common import ERROR, MapIssue, _clamp01, _is_color
from .geometry import Rect, Vec2, _rotated_bounds
from .media import MediaCache

# =============================================================================
# Elements
# =============================================================================


class DrawTool(str, Enum):
    """The shape of a drawing element."""

    FREEHAND = "freehand"
    LINE = "line"
    RECT = "rect"
    CIRCLE = "circle"


@dataclass(slots=True)
class MapElement:
    """
    Base class of everything that is drawn on the map and flattened.

    Subclasses provide `bounds` (never smaller than what `render` really draws,
    since it is used to skip elements outside a tile) and `render`.
    """

    type_name: ClassVar[str] = ""
    # False for GM-only elements (walls): never baked into the compiled tiles
    flattened: ClassVar[bool] = True

    id: str = field(default_factory=lambda: uuid4().hex)
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0

    def bounds(self) -> Rect:
        """
        Return a rectangle that contains everything this element draws.
        """
        raise NotImplementedError

    def render(self, painter: QPainter, media: MediaCache) -> None:
        """
        Draw the element in map coordinates.
        """
        raise NotImplementedError

    def referenced_assets(self) -> list[Path]:
        """
        Return the map-relative paths of the files this element needs.
        """
        return []

    def issues(self) -> list[MapIssue]:
        """
        Return the problems of the element itself (files are checked by the map).
        """
        if not 0.0 <= self.opacity <= 1.0:
            return [MapIssue(ERROR, "Opacity must be between 0 and 1.")]
        return []

    def to_dict(self) -> dict:
        """
        Serialize the element.
        """
        return {
            "type": self.type_name,
            "id": self.id,
            "visible": self.visible,
            "locked": self.locked,
            "opacity": self.opacity,
        }

    @staticmethod
    def _common(data: dict) -> dict:
        return {
            "id": str(data.get("id") or uuid4().hex),
            "visible": bool(data.get("visible", True)),
            "locked": bool(data.get("locked", False)),
            "opacity": float(data.get("opacity", 1.0)),
        }

    @staticmethod
    def from_dict(data: dict) -> MapElement:
        """
        Create the right kind of element from its serialized form.

        Raises:
            ValueError: If the type is unknown or a field is invalid.
        """
        if not isinstance(data, dict):
            raise ValueError("A map element must be an object.")
        kind = ELEMENT_TYPES.get(str(data.get("type", "")))
        if kind is None:
            raise ValueError(f"Unknown element type: {data.get('type')!r}")
        try:
            return kind.read(data)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(f"Invalid {kind.type_name} element: {error}") from error

    @classmethod
    def read(cls, data: dict) -> MapElement:
        """
        Create the element from its serialized form (see `from_dict`).
        """
        raise NotImplementedError


@dataclass(slots=True)
class ImageElement(MapElement):
    """An image placed on the map, scaled to `width` x `height` and rotated."""

    type_name: ClassVar[str] = "image"

    asset: Path = field(default_factory=Path)
    pos: Vec2 = field(default_factory=Vec2)
    width: float = 128.0
    height: float = 128.0
    rotation: float = 0.0

    def bounds(self) -> Rect:
        return _rotated_bounds(
            Rect(self.pos.x, self.pos.y, self.width, self.height),
            self.rotation,
        ).inflate(1.0)

    def render(self, painter: QPainter, media: MediaCache) -> None:
        image = media.get(self.asset)
        painter.save()
        painter.setOpacity(_clamp01(self.opacity))
        painter.translate(self.pos.x + self.width / 2, self.pos.y + self.height / 2)
        if self.rotation:
            painter.rotate(self.rotation)
        painter.drawImage(
            QRectF(-self.width / 2, -self.height / 2, self.width, self.height),
            image,
        )
        painter.restore()

    def referenced_assets(self) -> list[Path]:
        return [self.asset]

    def issues(self) -> list[MapIssue]:
        issues = MapElement.issues(self)
        if self.width <= 0 or self.height <= 0:
            issues.append(MapIssue(ERROR, "The image size must be positive."))
        return issues

    def to_dict(self) -> dict:
        return {
            **MapElement.to_dict(self),
            "asset": self.asset.as_posix(),
            "pos": self.pos.to_list(),
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
        }

    @classmethod
    def read(cls, data: dict) -> ImageElement:
        return cls(
            **cls._common(data),
            asset=Path(str(data["asset"])),
            pos=Vec2.from_value(data.get("pos", [0, 0])),
            width=float(data.get("width", 128.0)),
            height=float(data.get("height", 128.0)),
            rotation=float(data.get("rotation", 0.0)),
        )


@dataclass(slots=True)
class DrawingElement(MapElement):
    """
    A stroke or a shape.

    `FREEHAND` joins all the points. `LINE` joins the first and the last point.
    `RECT` and `CIRCLE` use the first and the last point as opposite corners of
    their bounding box; `CIRCLE` is the ellipse inscribed in that box.
    """

    type_name: ClassVar[str] = "drawing"

    tool: DrawTool = DrawTool.FREEHAND
    points: list[Vec2] = field(default_factory=list)
    stroke_color: str = "#f9e2af"
    stroke_width: float = 2.0
    fill_color: Optional[str] = None

    def bounds(self) -> Rect:
        return Rect.from_points(self.points).inflate(max(0.0, self.stroke_width) / 2 + 1.0)

    def render(self, painter: QPainter, media: MediaCache) -> None:
        if not self.points:
            return
        painter.save()
        painter.setOpacity(_clamp01(self.opacity))
        if self.stroke_width > 0:
            pen = QPen(QColor(self.stroke_color), self.stroke_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        if self.fill_color and self.tool in (DrawTool.RECT, DrawTool.CIRCLE):
            painter.setBrush(QBrush(QColor(self.fill_color)))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)

        first, last = self.points[0], self.points[-1]
        if self.tool in (DrawTool.RECT, DrawTool.CIRCLE):
            box = QRectF(QPointF(first.x, first.y), QPointF(last.x, last.y)).normalized()
            if self.tool is DrawTool.RECT:
                painter.drawRect(box)
            else:
                painter.drawEllipse(box)
        elif self.tool is DrawTool.LINE:
            painter.drawLine(QPointF(first.x, first.y), QPointF(last.x, last.y))
        elif len(self.points) == 1:
            painter.drawPoint(QPointF(first.x, first.y))
        else:
            painter.drawPolyline(QPolygonF([QPointF(p.x, p.y) for p in self.points]))
        painter.restore()

    def issues(self) -> list[MapIssue]:
        issues = MapElement.issues(self)
        if not self.points:
            issues.append(MapIssue(ERROR, "A drawing needs at least one point."))
        elif self.tool is not DrawTool.FREEHAND and len(self.points) < 2:
            issues.append(MapIssue(ERROR, f"A {self.tool.value} needs two points."))
        if self.stroke_width < 0:
            issues.append(MapIssue(ERROR, "The stroke width cannot be negative."))
        if not _is_color(self.stroke_color):
            issues.append(MapIssue(ERROR, f"Invalid stroke color: {self.stroke_color!r}."))
        if self.fill_color is not None and not _is_color(self.fill_color):
            issues.append(MapIssue(ERROR, f"Invalid fill color: {self.fill_color!r}."))
        return issues

    def to_dict(self) -> dict:
        return {
            **MapElement.to_dict(self),
            "tool": self.tool.value,
            "points": [point.to_list() for point in self.points],
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "fill_color": self.fill_color,
        }

    @classmethod
    def read(cls, data: dict) -> DrawingElement:
        fill = data.get("fill_color")
        return cls(
            **cls._common(data),
            tool=DrawTool(str(data.get("tool", DrawTool.FREEHAND.value))),
            points=[Vec2.from_value(point) for point in data.get("points", [])],
            stroke_color=str(data.get("stroke_color", "#f9e2af")),
            stroke_width=float(data.get("stroke_width", 2.0)),
            fill_color=str(fill) if fill else None,
        )


@dataclass(slots=True)
class TextElement(MapElement):
    """
    A text block. `pos` is the top-left corner of the block, which is rotated
    around its center. Lines are separated by `\\n`.
    """

    type_name: ClassVar[str] = "text"

    text: str = "Text"
    pos: Vec2 = field(default_factory=Vec2)
    font_size: float = 14.0
    font_color: str = "#f9e2af"
    font_family: Optional[str] = None
    rotation: float = 0.0

    def bounds(self) -> Rect:
        # The real size depends on the font, so this is a deliberate overestimate
        # (one em per character): drawing a text on a tile it does not reach is
        # harmless, missing one is a silent bug.
        lines = self.text.split("\n")
        width = max(len(line) for line in lines) * self.font_size
        height = len(lines) * self.font_size * 1.5
        margin = self.font_size * 0.5
        if not self.rotation:
            return Rect(self.pos.x - margin, self.pos.y - margin,
                        width + 2 * margin, height + 2 * margin)
        # The block turns around its real center, which is inside the estimate:
        # every point stays within `hypot(width, height)` of `pos`.
        radius = hypot(width, height) + margin
        return Rect(self.pos.x - radius, self.pos.y - radius, 2 * radius, 2 * radius)

    def render(self, painter: QPainter, media: MediaCache) -> None:
        font = QFont(self.font_family) if self.font_family else QFont()
        font.setPixelSize(max(1, round(self.font_size)))
        metrics = QFontMetricsF(font)
        lines = self.text.split("\n")
        line_height = metrics.lineSpacing()
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = line_height * len(lines)

        painter.save()
        painter.setOpacity(_clamp01(self.opacity))
        painter.setFont(font)
        painter.setPen(QColor(self.font_color))
        painter.translate(self.pos.x + width / 2, self.pos.y + height / 2)
        if self.rotation:
            painter.rotate(self.rotation)
        painter.translate(-width / 2, -height / 2)
        for index, line in enumerate(lines):
            painter.drawText(QPointF(0.0, index * line_height + metrics.ascent()), line)
        painter.restore()

    def issues(self) -> list[MapIssue]:
        issues = MapElement.issues(self)
        if self.font_size <= 0:
            issues.append(MapIssue(ERROR, "The font size must be positive."))
        if not _is_color(self.font_color):
            issues.append(MapIssue(ERROR, f"Invalid text color: {self.font_color!r}."))
        return issues

    def to_dict(self) -> dict:
        return {
            **MapElement.to_dict(self),
            "text": self.text,
            "pos": self.pos.to_list(),
            "font_size": self.font_size,
            "font_color": self.font_color,
            "font_family": self.font_family,
            "rotation": self.rotation,
        }

    @classmethod
    def read(cls, data: dict) -> TextElement:
        family = data.get("font_family")
        return cls(
            **cls._common(data),
            text=str(data.get("text", "")),
            pos=Vec2.from_value(data.get("pos", [0, 0])),
            font_size=float(data.get("font_size", 14.0)),
            font_color=str(data.get("font_color", "#f9e2af")),
            font_family=str(family) if family else None,
            rotation=float(data.get("rotation", 0.0)),
        )


WALL_COLOR = "#ff6b4a"
WALL_WIDTH = 6.0


@dataclass(slots=True)
class WallElement(DrawingElement):
    """
    A wall segment: it blocks vision, shots, spells and player moves.

    GM-only: it is never flattened into the tiles players download, and the
    editor and the GM runtime draw it on top of the scene.
    """

    type_name: ClassVar[str] = "wall"
    flattened: ClassVar[bool] = False

    tool: DrawTool = DrawTool.LINE
    stroke_color: str = WALL_COLOR
    stroke_width: float = WALL_WIDTH

    def bounds(self) -> Rect:
        return Rect.from_points(self.points).inflate(WALL_WIDTH)

    def render(self, painter: QPainter, media: MediaCache) -> None:
        if len(self.points) < 2:
            return
        painter.save()
        painter.setOpacity(1.0)
        pen = QPen(QColor(WALL_COLOR), WALL_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([QPointF(p.x, p.y) for p in self.points]))
        painter.restore()

    def issues(self) -> list[MapIssue]:
        if len(self.points) < 2:
            return [MapIssue(ERROR, "A wall needs two points.")]
        return []

    def segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        return [
            ((first.x, first.y), (second.x, second.y))
            for first, second in zip(self.points, self.points[1:])
            if (first.x, first.y) != (second.x, second.y)
        ]


ELEMENT_TYPES: dict[str, type[MapElement]] = {
    kind.type_name: kind for kind in (ImageElement, DrawingElement, TextElement, WallElement)
}
