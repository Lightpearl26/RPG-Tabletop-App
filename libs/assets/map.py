#-*- coding: utf-8 -*-

"""
Map module for RPG Tabletop App.

A map is the static, editable part of a battlefield. It lives in its own folder:

    maps/<name>/
    ├── map.json      # the source of truth (what the editor edits)
    ├── media/        # imported images, named after their content hash
    ├── tiles/        # compiled (flattened) tiles, read by the runtime
    └── preview.png   # thumbnail used by the map card

What is edited
--------------
The background, the grid settings, an ordered list of elements (images, drawings,
texts) and the initial placement of tokens. The order of `elements` is the z-order.

What is compiled
----------------
Only the background and the elements are flattened, in square tiles. The grid and
the tokens are overlays: they are never drawn in the tiles, so moving a token does
not recompile anything. Anything the runtime adds on top of the tiles (notes,
spell areas, scribbles) is never written back to the map.

One renderer
------------
`MapAsset.render_scene` draws the scene with a `QPainter`. The editor canvas and the
flatten use that same code, so what the GM sees is what gets compiled. Rendering
needs a `QGuiApplication` (fonts), which the app always has when it compiles.

Coordinates are in pixels. A tile is `tile_size` x `tile_size` pixels; tiles on the
right and bottom edges are padded with transparent pixels.
"""


# import built-in modules
from __future__ import annotations
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from json import dumps, loads
from math import ceil, cos, floor, hypot, radians, sin
from pathlib import Path
from shutil import copy2, copyfile, rmtree
from typing import Any, ClassVar, NamedTuple, Optional
from uuid import uuid4

# import third-party modules
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QImageReader,
    QImageWriter,
    QPainter,
    QPen,
    QPolygonF,
)

# import local modules
from .campaign import get_current_campaign
from .references import json_text


# Define constants
FORMAT_VERSION = 1
MAP_FILE = "map.json"
MEDIA_DIR = "media"
TILES_DIR = "tiles"
PREVIEW_FILE = "preview.png"
STAGING_DIR = ".staging"

DEFAULT_TILE_SIZE = 512
DEFAULT_TILE_FORMAT = "webp"
TILE_FORMATS = ("webp", "png")
PREVIEW_MAX_PX = 512

# Safety limit, not a technical one: tiles are rendered one at a time
MAX_MAP_SIDE_PX = 65536

ERROR = "error"
WARNING = "warning"


class MapIssue(NamedTuple):
    """A problem found in a map: errors block saving, warnings do not."""

    severity: str
    message: str


class MapCompileCancelled(Exception):
    """Raised when the progress callback asked to stop the compilation."""


# =============================================================================
# Geometry
# =============================================================================


@dataclass(slots=True)
class Vec2:
    """A point or a vector, in map pixels."""

    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def to_list(self) -> list[float]:
        """
        Serialize the point as `[x, y]`.
        """
        return [self.x, self.y]

    @classmethod
    def from_value(cls, value: Any) -> Vec2:
        """
        Read a point stored as `[x, y]`.

        Raises:
            ValueError: If the value is not a pair of numbers.
        """
        try:
            x, y = value
            return cls(float(x), float(y))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid point: {value!r}") from error


@dataclass(slots=True)
class Rect:
    """An axis-aligned rectangle, in map pixels."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def right(self) -> float:
        """The x coordinate of the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """The y coordinate of the bottom edge."""
        return self.y + self.height

    def intersects(self, other: Rect) -> bool:
        """
        Whether the two rectangles overlap or touch.

        Touching counts on purpose: filtering must never miss an element.
        """
        return not (
            self.right < other.x
            or other.right < self.x
            or self.bottom < other.y
            or other.bottom < self.y
        )

    def inflate(self, margin: float) -> Rect:
        """
        Return a rectangle grown by `margin` on every side.
        """
        return Rect(
            self.x - margin,
            self.y - margin,
            self.width + 2 * margin,
            self.height + 2 * margin,
        )

    def to_qrectf(self) -> QRectF:
        """
        Convert to a Qt rectangle.
        """
        return QRectF(self.x, self.y, self.width, self.height)

    @classmethod
    def from_points(cls, points: list[Vec2]) -> Rect:
        """
        Return the bounding box of `points`, empty if there is none.
        """
        if not points:
            return cls()
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        return cls(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _rotated_bounds(rect: Rect, angle_deg: float) -> Rect:
    """
    Bounding box of `rect` once rotated around its center.
    """
    if not angle_deg:
        return Rect(rect.x, rect.y, rect.width, rect.height)
    center = Vec2(rect.x + rect.width / 2, rect.y + rect.height / 2)
    cosine, sine = cos(radians(angle_deg)), sin(radians(angle_deg))
    corners = [
        Vec2(rect.x, rect.y),
        Vec2(rect.right, rect.y),
        Vec2(rect.right, rect.bottom),
        Vec2(rect.x, rect.bottom),
    ]
    rotated = []
    for corner in corners:
        dx, dy = corner.x - center.x, corner.y - center.y
        rotated.append(
            Vec2(
                center.x + dx * cosine - dy * sine,
                center.y + dx * sine + dy * cosine,
            )
        )
    return Rect.from_points(rotated)


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


# =============================================================================
# Media
# =============================================================================


def _resolve_asset(base_dir: Path, asset: Path) -> Path:
    """
    Resolve a map-relative asset path, refusing absolute and parent paths.
    """
    asset = Path(asset)
    if asset.is_absolute() or ".." in asset.parts:
        raise ValueError(f"Asset path must stay inside the map folder: {asset}")
    return Path(base_dir) / asset


class MediaCache:
    """Loads the images of a map once and keeps them for the whole render."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._images: dict[Path, QImage] = {}

    def get(self, asset: Path) -> QImage:
        """
        Return the image stored at the map-relative path `asset`.

        Raises:
            FileNotFoundError: If the file is missing or is not a readable image.
        """
        key = Path(asset)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        path = _resolve_asset(self.base_dir, key)
        image = QImage(str(path))
        if image.isNull():
            raise FileNotFoundError(f"Map image not found or unreadable: {path}")
        self._images[key] = image
        return image


def import_media(map_dir: Path, source: Path) -> Path:
    """
    Copy an image into the `media` folder of a map and return its relative path.

    The file is named after its content hash, so importing the same image twice
    gives the same path and costs nothing.

    Raises:
        FileNotFoundError: If `source` is not a file.
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    digest = sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    relative = Path(MEDIA_DIR) / f"{digest.hexdigest()[:16]}{source.suffix.lower()}"
    target = Path(map_dir) / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            copyfile(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return relative


def media_size(map_dir: Path, asset: Path) -> tuple[int, int]:
    """
    Return the pixel size of an image without decoding it, `(0, 0)` if unreadable.
    """
    size = QImageReader(str(_resolve_asset(map_dir, asset))).size()
    return (size.width(), size.height()) if size.isValid() else (0, 0)


def scan_entity_ids(directory: Path) -> dict[str, Path]:
    """
    Map the `id` of every entity file of `directory` to its path.

    When two files share an id, the first one (in name order) wins.
    """
    found: dict[str, Path] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            raw = loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and raw.get("id"):
            found.setdefault(str(raw["id"]), path)
    return found


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


ELEMENT_TYPES: dict[str, type[MapElement]] = {
    kind.type_name: kind for kind in (ImageElement, DrawingElement, TextElement)
}


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


# =============================================================================
# Compiled map
# =============================================================================


def tile_key(column: int, row: int) -> str:
    """
    Return the name of the tile at (`column`, `row`), such as `"3_1"`.
    """
    return f"{column}_{row}"


def supported_tile_formats() -> tuple[str, ...]:
    """
    Return the tile formats this Qt installation can write.
    """
    available = {
        bytes(name).decode("ascii", "ignore").lower()
        for name in QImageWriter.supportedImageFormats()
    }
    return tuple(name for name in TILE_FORMATS if name in available)


@dataclass(slots=True)
class FlattenedMap:
    """
    What the last compilation produced, stored in `map.json` under `compiled`.

    Attributes:
        source_map_id: The id of the map that was compiled.
        source_hash: Hash of everything that affects the pixels.
        width, height: The size of the map in pixels.
        tile_size: The side of a tile in pixels.
        tile_format: The format of the tile files.
        tiles: For each written tile, the hash of its pixels.
        uniform: For each tile of a single color, that color. These tiles have no
                 file: the runtime fills them itself.
    """

    source_map_id: str
    source_hash: str
    width: int
    height: int
    tile_size: int
    tile_format: str
    tiles: dict[str, str] = field(default_factory=dict)
    uniform: dict[str, str] = field(default_factory=dict)

    @property
    def columns(self) -> int:
        """The number of tile columns."""
        return ceil(self.width / self.tile_size)

    @property
    def rows(self) -> int:
        """The number of tile rows."""
        return ceil(self.height / self.tile_size)

    def tile_path(self, map_dir: Path, column: int, row: int) -> Optional[Path]:
        """
        Return the file of a tile, or None if the tile is a single color.
        """
        key = tile_key(column, row)
        if key not in self.tiles:
            return None
        return Path(map_dir) / TILES_DIR / f"{key}.{self.tile_format}"

    def matches(self, source_hash: str, tile_size: int, tile_format: str) -> bool:
        """
        Whether this compilation is still valid for those parameters.
        """
        return (
            self.source_hash == source_hash
            and self.tile_size == tile_size
            and self.tile_format == tile_format
        )

    def to_dict(self) -> dict:
        """
        Serialize the compilation result.
        """
        return {
            "source_map_id": self.source_map_id,
            "source_hash": self.source_hash,
            "size": [self.width, self.height],
            "tile_size": self.tile_size,
            "format": self.tile_format,
            "tiles": dict(self.tiles),
            "uniform": dict(self.uniform),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FlattenedMap:
        """
        Create a compilation result from its serialized form.

        Raises:
            ValueError: If a field is missing or invalid.
        """
        try:
            width, height = data["size"]
            return cls(
                source_map_id=str(data["source_map_id"]),
                source_hash=str(data["source_hash"]),
                width=int(width),
                height=int(height),
                tile_size=int(data["tile_size"]),
                tile_format=str(data["format"]),
                tiles={str(k): str(v) for k, v in dict(data.get("tiles", {})).items()},
                uniform={str(k): str(v) for k, v in dict(data.get("uniform", {})).items()},
            )
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f"Invalid compiled map data: {error}") from error


# =============================================================================
# File helpers
# =============================================================================


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _commit(items: list[tuple[Path, Path]]) -> None:
    """
    Move every `source` onto its `target`; if one move fails, put everything back.

    A target that already exists is kept aside until all the moves succeeded.
    """
    done: list[tuple[Path, Optional[Path]]] = []
    try:
        for source, target in items:
            backup: Optional[Path] = None
            if target.exists():
                backup = target.with_name(f".{target.name}.bak")
                _remove(backup)
                target.rename(backup)
            done.append((target, backup))
            source.rename(target)
    except BaseException:
        for target, backup in reversed(done):
            _remove(target)
            if backup is not None:
                backup.rename(target)
        raise
    for _target, backup in done:
        if backup is not None:
            _remove(backup)


def _save_tile(image: QImage, path: Path, tile_format: str) -> None:
    straight = image.convertToFormat(QImage.Format.Format_ARGB32)
    if tile_format == "webp":
        # With the Qt WebP plugin, quality 100 means lossless
        saved = straight.save(str(path), "WEBP", 100)
    else:
        saved = straight.save(str(path), "PNG")
    if not saved:
        raise OSError(f"Could not write tile: {path}")


# =============================================================================
# Map asset
# =============================================================================


@dataclass(slots=True)
class MapAsset:
    """
    A map: the editable scene plus the result of its last compilation.

    Attributes:
        id: The unique identifier of the map.
        name: The display name of the map.
        width, height: The size of the map in pixels.
        background: The background of the scene.
        grid: The grid settings (overlay, never flattened).
        elements: The drawn elements, from bottom to top.
        initial_tokens: Where entities start (overlay, never flattened).
        initial_gm_camera: Where the GM camera starts.
        compiled: The last compilation, None if the map was never compiled.
    """

    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "New Map"
    width: int = 4096
    height: int = 4096
    background: MapBackground = field(default_factory=MapBackground)
    grid: GridSettings = field(default_factory=GridSettings)
    elements: list[MapElement] = field(default_factory=list)
    initial_tokens: list[InitialTokenPlacement] = field(default_factory=list)
    initial_gm_camera: CameraState = field(default_factory=CameraState)
    compiled: Optional[FlattenedMap] = None

    # ----- Hash --------------------------------------------------------------

    def source_hash(self) -> str:
        """
        Hash everything that changes the compiled pixels.

        The grid, the tokens and the camera are left out on purpose. Images are
        named after their content, so their path stands for their content.
        """
        signature = {
            "size": [self.width, self.height],
            "background": self.background.to_dict(),
            "elements": [element.to_dict() for element in self.elements if element.visible],
        }
        text = dumps(_canonical(signature), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return sha256(text.encode("utf-8")).hexdigest()

    def is_compiled(self, tile_size: int = DEFAULT_TILE_SIZE,
                    tile_format: str = DEFAULT_TILE_FORMAT) -> bool:
        """
        Whether the stored compilation matches the current scene.
        """
        return self.compiled is not None and self.compiled.matches(
            self.source_hash(), tile_size, tile_format
        )

    # ----- Tokens ------------------------------------------------------------

    def token_for(self, entity_id: str) -> Optional[InitialTokenPlacement]:
        """
        Return the token of an entity, if it is on this map.
        """
        return next((t for t in self.initial_tokens if t.entity_id == entity_id), None)

    def place_token(self, entity_id: str, pos: Vec2, *, snap: bool = True) -> InitialTokenPlacement:
        """
        Put an entity on the map: move its token if it has one, create it otherwise.

        An entity has at most one token per map.

        Raises:
            ValueError: If `entity_id` is empty.
        """
        entity_id = str(entity_id).strip()
        if not entity_id:
            raise ValueError("A token needs an entity id.")
        target = (
            self.grid.cell_center(pos)
            if snap and self.grid.enabled and self.grid.cell_size > 0
            else Vec2(pos.x, pos.y)
        )
        token = self.token_for(entity_id)
        if token is None:
            token = InitialTokenPlacement(entity_id=entity_id, pos=target)
            self.initial_tokens.append(token)
        else:
            token.pos = target
        return token

    def remove_token(self, token_id: str) -> None:
        """
        Remove a token from the map. The entity itself is kept.
        """
        self.initial_tokens = [t for t in self.initial_tokens if t.id != token_id]

    # ----- Validation --------------------------------------------------------

    def issues(
        self,
        map_dir: Optional[Path] = None,
        known_entities: Optional[Collection[str]] = None,
    ) -> list[MapIssue]:
        """
        Return every problem of the map.

        Args:
            map_dir: When given, the files the map needs must exist in it.
            known_entities: When given, the ids of the entities that exist; tokens
                            pointing elsewhere are reported as warnings.
        """
        issues: list[MapIssue] = []
        if self.width <= 0 or self.height <= 0:
            issues.append(MapIssue(ERROR, "The map size must be positive."))
        elif max(self.width, self.height) > MAX_MAP_SIDE_PX:
            issues.append(MapIssue(ERROR, f"A map side cannot exceed {MAX_MAP_SIDE_PX} px."))
        if self.grid.cell_size <= 0:
            issues.append(MapIssue(ERROR, "The grid cell size must be positive."))
        if not _is_color(self.background.color):
            issues.append(MapIssue(ERROR, f"Invalid background color: {self.background.color!r}."))
        if not _is_color(self.grid.color):
            issues.append(MapIssue(ERROR, f"Invalid grid color: {self.grid.color!r}."))

        assets: list[tuple[str, Path]] = []
        if self.background.image_asset is not None:
            assets.append(("Background", self.background.image_asset))

        seen_elements: set[str] = set()
        for index, element in enumerate(self.elements, start=1):
            label = f"Element {index} ({element.type_name})"
            if element.id in seen_elements:
                issues.append(MapIssue(ERROR, f"{label}: duplicate id {element.id}."))
            seen_elements.add(element.id)
            issues.extend(MapIssue(i.severity, f"{label}: {i.message}") for i in element.issues())
            assets.extend((label, asset) for asset in element.referenced_assets())

        for label, asset in assets:
            if asset.is_absolute() or ".." in asset.parts:
                issues.append(MapIssue(ERROR, f"{label}: the image path must be relative to the map."))
            elif map_dir is not None and not (Path(map_dir) / asset).is_file():
                issues.append(MapIssue(ERROR, f"{label}: image not found: {asset.as_posix()}."))

        seen_tokens: set[str] = set()
        seen_entities: set[str] = set()
        for index, token in enumerate(self.initial_tokens, start=1):
            label = f"Token {index}"
            if token.id in seen_tokens:
                issues.append(MapIssue(ERROR, f"{label}: duplicate id {token.id}."))
            seen_tokens.add(token.id)
            if not token.entity_id:
                issues.append(MapIssue(ERROR, f"{label}: no entity."))
            elif token.entity_id in seen_entities:
                issues.append(MapIssue(ERROR, f"{label}: the entity is already on this map."))
            elif known_entities is not None and token.entity_id not in known_entities:
                issues.append(MapIssue(WARNING, f"{label}: the entity no longer exists."))
            seen_entities.add(token.entity_id)
            if token.size_cells <= 0:
                issues.append(MapIssue(ERROR, f"{label}: the size must be positive."))
            if not 0.0 <= token.opacity <= 1.0:
                issues.append(MapIssue(ERROR, f"{label}: opacity must be between 0 and 1."))
            if not (0 <= token.pos.x <= self.width and 0 <= token.pos.y <= self.height):
                issues.append(MapIssue(WARNING, f"{label}: the token is outside the map."))
        return issues

    def validate(
        self,
        map_dir: Optional[Path] = None,
        known_entities: Optional[Collection[str]] = None,
    ) -> None:
        """
        Check the map.

        Raises:
            ValueError: With the first error found (warnings are ignored).
        """
        for issue in self.issues(map_dir, known_entities):
            if issue.severity == ERROR:
                raise ValueError(issue.message)

    # ----- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        """
        Serialize the map to the content of `map.json`.
        """
        data: dict[str, Any] = {
            "version": FORMAT_VERSION,
            "id": self.id,
            "name": self.name,
            "size": [self.width, self.height],
            "background": self.background.to_dict(),
            "grid": self.grid.to_dict(),
            "elements": [element.to_dict() for element in self.elements],
            "initial_tokens": [token.to_dict() for token in self.initial_tokens],
            "initial_gm_camera": self.initial_gm_camera.to_dict(),
        }
        if self.compiled is not None:
            data["compiled"] = self.compiled.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> MapAsset:
        """
        Create a map from the content of `map.json`.

        Raises:
            ValueError: If the data is not a valid map.
        """
        if not isinstance(data, dict):
            raise ValueError("A map file must contain a JSON object.")
        try:
            version = int(data.get("version", FORMAT_VERSION))
            if version > FORMAT_VERSION:
                raise ValueError(f"Map format {version} is newer than this app understands.")
            width, height = data.get("size", [4096, 4096])
            compiled = data.get("compiled")
            return cls(
                id=str(data.get("id") or uuid4().hex),
                name=str(data.get("name", "New Map")),
                width=int(width),
                height=int(height),
                background=MapBackground.from_dict(data.get("background", {})),
                grid=GridSettings.from_dict(data.get("grid", {})),
                elements=[MapElement.from_dict(e) for e in data.get("elements", [])],
                initial_tokens=[
                    InitialTokenPlacement.from_dict(t) for t in data.get("initial_tokens", [])
                ],
                initial_gm_camera=CameraState.from_dict(data.get("initial_gm_camera", {})),
                compiled=FlattenedMap.from_dict(compiled) if compiled else None,
            )
        except (TypeError, AttributeError) as error:
            raise ValueError(f"Invalid map data: {error}") from error

    @classmethod
    def load(cls, map_dir: Path) -> MapAsset:
        """
        Read the map stored in `map_dir`.

        Raises:
            FileNotFoundError: If the folder has no `map.json`.
            ValueError: If the file is not a valid map.
        """
        path = Path(map_dir) / MAP_FILE
        if not path.is_file():
            raise FileNotFoundError(f"Map file not found: {path}")
        try:
            return cls.from_dict(loads(path.read_text(encoding="utf-8")))
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from error

    @classmethod
    def from_name(cls, name: str) -> MapAsset:
        """
        Read a map of the current campaign by its folder name.

        Raises:
            FileNotFoundError: If the map does not exist.
        """
        return cls.load(get_current_campaign().map_path(name))

    @staticmethod
    def read_compiled(map_dir: Path) -> Optional[FlattenedMap]:
        """
        Return the compilation recorded in a map folder, None if there is none.
        """
        try:
            data = loads((Path(map_dir) / MAP_FILE).read_text(encoding="utf-8"))
            return FlattenedMap.from_dict(data["compiled"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # ----- Rendering ---------------------------------------------------------

    def render_scene(self, painter: QPainter, media: MediaCache, view: Optional[Rect] = None) -> None:
        """
        Draw the background and the visible elements in map coordinates.

        The editor canvas and the flatten both call this. Set the painter transform
        (zoom, scroll, tile offset) before calling it.

        Args:
            view: The visible area in map coordinates; elements outside are skipped.
        """
        map_rect = Rect(0.0, 0.0, float(self.width), float(self.height))
        painter.save()
        painter.setClipRect(map_rect.to_qrectf(), Qt.ClipOperation.IntersectClip)
        self.background.render(painter, media, map_rect)
        for element in self.elements:
            if not element.visible:
                continue
            if view is not None and not element.bounds().intersects(view):
                continue
            element.render(painter, media)
        painter.restore()

    @staticmethod
    def _new_image(width: int, height: int) -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    @staticmethod
    def _quality_hints(painter: QPainter) -> None:
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )

    def render_tile(self, column: int, row: int, tile_size: int, media: MediaCache) -> QImage:
        """
        Render one tile, with its own image: memory use does not depend on map size.
        """
        origin_x, origin_y = column * tile_size, row * tile_size
        image = self._new_image(tile_size, tile_size)
        painter = QPainter(image)
        try:
            self._quality_hints(painter)
            painter.translate(-origin_x, -origin_y)
            self.render_scene(
                painter,
                media,
                Rect(float(origin_x), float(origin_y), float(tile_size), float(tile_size)),
            )
        finally:
            painter.end()
        return image

    def render_preview(self, media: MediaCache, max_side: int = PREVIEW_MAX_PX) -> QImage:
        """
        Render the whole map in a thumbnail.
        """
        scale = min(1.0, max_side / max(self.width, self.height))
        image = self._new_image(max(1, round(self.width * scale)), max(1, round(self.height * scale)))
        painter = QPainter(image)
        try:
            self._quality_hints(painter)
            painter.scale(scale, scale)
            self.render_scene(painter, media)
        finally:
            painter.end()
        return image

    # ----- Compilation -------------------------------------------------------

    def _build(
        self,
        staging: Path,
        map_dir: Path,
        tile_size: int,
        tile_format: str,
        previous: Optional[FlattenedMap],
        progress: Optional[Callable[[int, int], bool]],
    ) -> tuple[FlattenedMap, bool]:
        """
        Render the tiles and the preview into `staging`.

        Returns:
            The compilation result and whether anything was rebuilt. When the
            previous compilation is still valid nothing is rendered.
        """
        if tile_size <= 0:
            raise ValueError("The tile size must be positive.")
        if tile_format not in supported_tile_formats():
            raise ValueError(
                f"This Qt installation cannot write {tile_format!r} tiles "
                f"(available: {', '.join(supported_tile_formats()) or 'none'})."
            )

        source_hash = self.source_hash()
        if (
            previous is not None
            and previous.matches(source_hash, tile_size, tile_format)
            and (map_dir / TILES_DIR).is_dir()
            and (map_dir / PREVIEW_FILE).is_file()
        ):
            return previous, False

        columns, rows = ceil(self.width / tile_size), ceil(self.height / tile_size)
        total = columns * rows
        tiles_dir = staging / TILES_DIR
        tiles_dir.mkdir(parents=True)
        old_tiles_dir = map_dir / TILES_DIR
        reusable = (
            previous
            if previous is not None
            and previous.tile_size == tile_size
            and previous.tile_format == tile_format
            else None
        )

        media = MediaCache(map_dir)
        tiles: dict[str, str] = {}
        uniform: dict[str, str] = {}

        # Qt refuses to decode large images by default
        previous_limit = QImageReader.allocationLimit()
        QImageReader.setAllocationLimit(0)
        try:
            done = 0
            for row in range(rows):
                for column in range(columns):
                    if progress is not None and not progress(done, total):
                        raise MapCompileCancelled()
                    key = tile_key(column, row)
                    image = self.render_tile(column, row, tile_size, media)
                    raw = bytes(image.constBits())
                    first_pixel = raw[:4]
                    if raw == first_pixel * (len(raw) // 4):
                        uniform[key] = image.pixelColor(0, 0).name(QColor.NameFormat.HexArgb)
                    else:
                        digest = sha256(raw).hexdigest()
                        tiles[key] = digest
                        target = tiles_dir / f"{key}.{tile_format}"
                        old = old_tiles_dir / f"{key}.{tile_format}"
                        if reusable is not None and reusable.tiles.get(key) == digest and old.is_file():
                            copy2(old, target)
                        else:
                            _save_tile(image, target, tile_format)
                    done += 1
            if progress is not None:
                progress(total, total)

            preview = self.render_preview(media)
            if not preview.save(str(staging / PREVIEW_FILE), "PNG"):
                raise OSError("Could not write the map preview.")
        finally:
            QImageReader.setAllocationLimit(previous_limit)

        compiled = FlattenedMap(
            source_map_id=self.id,
            source_hash=source_hash,
            width=self.width,
            height=self.height,
            tile_size=tile_size,
            tile_format=tile_format,
            tiles=tiles,
            uniform=uniform,
        )
        return compiled, True

    def _write(
        self,
        map_dir: Path,
        tile_size: int,
        tile_format: str,
        progress: Optional[Callable[[int, int], bool]],
        *,
        write_json: bool,
    ) -> FlattenedMap:
        map_dir = Path(map_dir)
        map_dir.mkdir(parents=True, exist_ok=True)
        self.validate(map_dir)

        staging = map_dir / STAGING_DIR
        _remove(staging)
        staging.mkdir()
        previous_state = self.compiled
        try:
            compiled, rebuilt = self._build(
                staging,
                map_dir,
                tile_size,
                tile_format,
                self.read_compiled(map_dir),
                progress,
            )
            self.compiled = compiled
            items: list[tuple[Path, Path]] = []
            if rebuilt:
                items.append((staging / TILES_DIR, map_dir / TILES_DIR))
                items.append((staging / PREVIEW_FILE, map_dir / PREVIEW_FILE))
            if write_json:
                (staging / MAP_FILE).write_text(json_text(self.to_dict()), encoding="utf-8")
                items.append((staging / MAP_FILE, map_dir / MAP_FILE))
            _commit(items)
        except BaseException:
            self.compiled = previous_state
            raise
        finally:
            _remove(staging)
        return compiled

    def compile(
        self,
        map_dir: Path,
        *,
        tile_size: int = DEFAULT_TILE_SIZE,
        tile_format: str = DEFAULT_TILE_FORMAT,
        progress: Optional[Callable[[int, int], bool]] = None,
    ) -> FlattenedMap:
        """
        Flatten the scene into tiles and a preview, without writing `map.json`.

        Either every file is replaced or none is.

        Args:
            progress: Called as `progress(done, total)`; returning False cancels.

        Raises:
            ValueError: If the map is invalid or the tile format is unavailable.
            MapCompileCancelled: If `progress` returned False.
        """
        return self._write(map_dir, tile_size, tile_format, progress, write_json=False)

    def save(
        self,
        map_dir: Path,
        *,
        tile_size: int = DEFAULT_TILE_SIZE,
        tile_format: str = DEFAULT_TILE_FORMAT,
        progress: Optional[Callable[[int, int], bool]] = None,
    ) -> FlattenedMap:
        """
        Compile the map and write `map.json`, as a single all-or-nothing operation.

        When only overlays changed (tokens, grid, camera), no tile is rendered.
        Images must already be in `media` (see `import_media`).

        Raises:
            ValueError: If the map is invalid or the tile format is unavailable.
            MapCompileCancelled: If `progress` returned False.
        """
        return self._write(map_dir, tile_size, tile_format, progress, write_json=True)