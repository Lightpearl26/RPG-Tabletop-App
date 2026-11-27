# -*- coding: utf-8 -*-

"""
MapAsset: a whole map, its validation, its rendering and its compilation into tiles.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps, loads
from math import ceil, floor
from pathlib import Path
from shutil import copy2
from typing import Any, Optional
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QImageReader,
    QPainter,
)

from rpg_tabletop.assets.campaign import get_current_campaign
from rpg_tabletop.assets.references import json_text

from .common import (
    AUTO_SIZE_MARGIN_CELLS,
    AUTO_SIZE_MARGIN_PX,
    DEFAULT_TILE_FORMAT,
    DEFAULT_TILE_SIZE,
    ERROR,
    FORMAT_VERSION,
    MAP_FILE,
    MAX_MAP_SIDE_PX,
    PREVIEW_FILE,
    PREVIEW_MAX_PX,
    STAGING_DIR,
    TILE_INDEX_FILE,
    TILES_DIR,
    WARNING,
    MapCompileCancelled,
    MapIssue,
    _canonical,
    _is_color,
)
from .elements import MapElement, WallElement
from .geometry import Rect, Vec2
from .media import MediaCache
from .settings import CameraState, GridSettings, InitialTokenPlacement, MapBackground
from .tiles import (
    FlattenedMap,
    _commit,
    _remove,
    _save_tile,
    supported_tile_formats,
    tile_key,
)


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
        origin_x, origin_y: The map coordinates of the top-left corner of the map.
        infinite: Whether the map is sized to its content at each compilation
                  (see `fit_to_content`); `width`, `height` and the origin then
                  hold the size of the last compilation.
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
    origin_x: int = 0
    origin_y: int = 0
    infinite: bool = False
    background: MapBackground = field(default_factory=MapBackground)
    grid: GridSettings = field(default_factory=GridSettings)
    elements: list[MapElement] = field(default_factory=list)
    initial_tokens: list[InitialTokenPlacement] = field(default_factory=list)
    initial_gm_camera: CameraState = field(default_factory=CameraState)
    compiled: Optional[FlattenedMap] = None
    # Area that always belongs to the map, content or not: the frame of a map
    # made before every map was infinite (see `from_dict`)
    kept_area: Optional[Rect] = None

    # ----- Hash --------------------------------------------------------------

    def source_hash(self) -> str:
        """
        Hash everything that changes the compiled pixels.

        The grid, the tokens and the camera are left out on purpose. Images are
        named after their content, so their path stands for their content.
        """
        signature: dict[str, Any] = {
            "size": [self.width, self.height],
            "background": self.background.to_dict(),
            "elements": [
                element.to_dict() for element in self.elements
                if element.visible and element.flattened
            ],
        }
        if self.origin_x or self.origin_y:
            # Left out at (0, 0) so maps made before origins keep their hash
            signature["origin"] = [self.origin_x, self.origin_y]
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

    # ----- Bounds ------------------------------------------------------------

    def map_rect(self) -> Rect:
        """
        The area covered by the map (and by its tiles), in map coordinates.
        """
        return Rect(float(self.origin_x), float(self.origin_y), float(self.width), float(self.height))

    def token_rect(self, token: InitialTokenPlacement) -> Rect:
        """
        The square a token covers, before rotation.
        """
        cell = float(self.grid.cell_size) if self.grid.cell_size > 0 else 64.0
        side = cell * max(0.0, token.size_cells)
        return Rect(token.pos.x - side / 2, token.pos.y - side / 2, side, side)

    def content_bounds(self) -> Optional[Rect]:
        """
        Bounding box of everything placed on the map, None if the map is empty.

        Every element counts, hidden ones and walls included (they can be shown
        or still block in play), and so does every token. The background does not.
        """
        rects = [element.bounds() for element in self.elements]
        if self.kept_area is not None:
            rects.append(self.kept_area)
        for token in self.initial_tokens:
            # A rotated square stays inside the circle around it
            rect = self.token_rect(token)
            rects.append(rect.inflate(rect.width * 0.21))
        if not rects:
            return None
        left = min(rect.x for rect in rects)
        top = min(rect.y for rect in rects)
        right = max(rect.right for rect in rects)
        bottom = max(rect.bottom for rect in rects)
        return Rect(left, top, right - left, bottom - top)

    def auto_rect(self) -> Rect:
        """
        The area an infinite map takes when it is compiled.

        The content bounds plus a margin, with edges on grid lines when the map has
        a grid and on whole pixels in any case. An empty map gets the margin around
        the origin.
        """
        content = self.content_bounds() or Rect(0.0, 0.0, 0.0, 0.0)
        if self.grid.enabled and self.grid.cell_size > 0:
            cell = float(self.grid.cell_size)
            ox, oy = self.grid.offset_x, self.grid.offset_y
            margin = AUTO_SIZE_MARGIN_CELLS
            left = ox + (floor((content.x - ox) / cell) - margin) * cell
            top = oy + (floor((content.y - oy) / cell) - margin) * cell
            right = ox + (ceil((content.right - ox) / cell) + margin) * cell
            bottom = oy + (ceil((content.bottom - oy) / cell) + margin) * cell
        else:
            margin = float(AUTO_SIZE_MARGIN_PX)
            left, top = content.x - margin, content.y - margin
            right, bottom = content.right + margin, content.bottom + margin
        x, y = floor(left), floor(top)
        width = max(1, ceil(right) - x)
        height = max(1, ceil(bottom) - y)
        return Rect(float(x), float(y), float(width), float(height))

    def fit_to_content(self) -> Rect:
        """
        Size the map to `auto_rect()`. Nothing moves: only the origin and size change.
        """
        rect = self.auto_rect()
        self.origin_x, self.origin_y = int(rect.x), int(rect.y)
        self.width, self.height = int(rect.width), int(rect.height)
        return rect

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
            if self.infinite:
                issues.append(MapIssue(
                    ERROR,
                    f"The content spans {self.width} x {self.height} px; "
                    f"a map side cannot exceed {MAX_MAP_SIDE_PX} px.",
                ))
            else:
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
            if not (
                self.origin_x <= token.pos.x <= self.origin_x + self.width
                and self.origin_y <= token.pos.y <= self.origin_y + self.height
            ):
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
            "origin": [self.origin_x, self.origin_y],
            "infinite": self.infinite,
            "background": self.background.to_dict(),
            "grid": self.grid.to_dict(),
            "elements": [element.to_dict() for element in self.elements],
            "initial_tokens": [token.to_dict() for token in self.initial_tokens],
            "initial_gm_camera": self.initial_gm_camera.to_dict(),
        }
        if self.kept_area is not None:
            area = self.kept_area
            data["kept_area"] = [area.x, area.y, area.width, area.height]
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
            origin_x, origin_y = data.get("origin", [0, 0])
            compiled = data.get("compiled")
            kept = data.get("kept_area")
            asset = cls(
                id=str(data.get("id") or uuid4().hex),
                name=str(data.get("name", "New Map")),
                width=int(width),
                height=int(height),
                origin_x=int(origin_x),
                origin_y=int(origin_y),
                infinite=bool(data.get("infinite", False)),
                background=MapBackground.from_dict(data.get("background", {})),
                grid=GridSettings.from_dict(data.get("grid", {})),
                elements=[MapElement.from_dict(e) for e in data.get("elements", [])],
                initial_tokens=[
                    InitialTokenPlacement.from_dict(t) for t in data.get("initial_tokens", [])
                ],
                initial_gm_camera=CameraState.from_dict(data.get("initial_gm_camera", {})),
                compiled=FlattenedMap.from_dict(compiled) if compiled else None,
                kept_area=Rect(*(float(v) for v in kept)) if kept else None,
            )
            if not asset.infinite:
                asset._become_infinite()
            return asset
        except (TypeError, AttributeError) as error:
            raise ValueError(f"Invalid map data: {error}") from error

    def _become_infinite(self) -> None:
        """
        Every map is infinite: a fixed map, read from an older file, becomes one
        without anything moving. Its frame stays part of the map (`kept_area`)
        and its background image, which covered that frame, becomes an image
        placed under everything at the same place (as a background it would
        stretch over the content area, which changes with the content).
        Origin, size and compilation are unchanged until the next save.
        """
        frame = Rect(float(self.origin_x), float(self.origin_y), float(self.width), float(self.height))
        self.infinite = True
        self.kept_area = frame
        if self.background.image_asset is not None:
            from .elements import ImageElement

            self.elements.insert(0, ImageElement(
                asset=self.background.image_asset,
                pos=Vec2(frame.x, frame.y),
                width=frame.width,
                height=frame.height,
            ))
            self.background.image_asset = None

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

    def render_scene(
        self,
        painter: QPainter,
        media: MediaCache,
        view: Optional[Rect] = None,
        *,
        bounded: bool = True,
    ) -> None:
        """
        Draw the background and the visible elements in map coordinates.

        The editor canvas and the flatten both call this. Set the painter transform
        (zoom, scroll, tile offset) before calling it.

        Args:
            view: The visible area in map coordinates; elements outside are skipped.
            bounded: Clip to `map_rect()`. The editor turns it off for infinite
                     maps: the background color then fills the whole view.
        """
        map_rect = self.map_rect()
        area = map_rect if bounded or view is None else view
        painter.save()
        painter.setClipRect(area.to_qrectf(), Qt.ClipOperation.IntersectClip)
        painter.fillRect(area.to_qrectf(), QColor(self.background.color))
        # The background image always covers the map rectangle only
        self.background.render(painter, media, map_rect)
        for element in self.elements:
            if not element.visible or not element.flattened:
                continue
            if view is not None and not element.bounds().intersects(view):
                continue
            element.render(painter, media)
        painter.restore()

    def walls(self) -> list[WallElement]:
        """The walls of the map (hidden ones included: they still block)."""
        return [element for element in self.elements if isinstance(element, WallElement)]

    def wall_segments(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        return [segment for wall in self.walls() for segment in wall.segments()]

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

    def render_tile(self, column: int, row: int, tile_size: int, media: MediaCache,
                    *, origin: Optional[tuple[int, int]] = None) -> QImage:
        """
        Render one tile, with its own image: memory use does not depend on map size.

        `origin`: the corner of tile 0_0 (default: the map's origin; see `tile_grid`).
        """
        grid_x, grid_y = origin if origin is not None else (self.origin_x, self.origin_y)
        origin_x = grid_x + column * tile_size
        origin_y = grid_y + row * tile_size
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
            painter.translate(-self.origin_x, -self.origin_y)
            self.render_scene(painter, media)
        finally:
            painter.end()
        return image

    # ----- Compilation -------------------------------------------------------

    def tile_grid(self, tile_size: int) -> tuple[int, int, int, int]:
        """
        (origin x, origin y, columns, rows) of the tiles covering the map.

        The tiles of an infinite map are counted from a multiple of the tile
        size: when the map grows, the tiles already made keep their place and
        their number, so only the new area has to be rendered.
        """
        if self.infinite:
            gx = floor(self.origin_x / tile_size) * tile_size
            gy = floor(self.origin_y / tile_size) * tile_size
        else:
            gx, gy = self.origin_x, self.origin_y
        columns = max(1, ceil((self.origin_x + self.width - gx) / tile_size))
        rows = max(1, ceil((self.origin_y + self.height - gy) / tile_size))
        return gx, gy, columns, rows

    def _scene_record(self) -> dict[str, Any]:
        """What the pixels of the tiles depend on (stored next to them)."""
        return {
            "rect": [self.origin_x, self.origin_y, self.width, self.height],
            "background": self.background.to_dict(),
            "elements": [
                element.to_dict() for element in self.elements
                if element.visible and element.flattened
            ],
        }

    @staticmethod
    def _dirty_rects(before: dict[str, Any], after: dict[str, Any]) -> Optional[list[Rect]]:
        """
        Areas whose pixels may differ between two scene records; None when
        everything may (background changed, elements reordered).
        """
        if before.get("background") != after.get("background"):
            return None
        old = {str(e.get("id")): e for e in before.get("elements", [])}
        new = {str(e.get("id")): e for e in after.get("elements", [])}
        common_old = [key for key in old if key in new]
        common_new = [key for key in new if key in old]
        if common_old != common_new:
            return None  # an element moved above or below another one
        rects: list[Rect] = []
        for key in set(old) | set(new):
            if old.get(key) == new.get(key):
                continue
            for data in (old.get(key), new.get(key)):
                if data is None:
                    continue
                try:
                    rects.append(MapElement.from_dict(data).bounds().inflate(2.0))
                except ValueError:
                    return None
        return rects

    def _build(
        self,
        staging: Path,
        map_dir: Path,
        tile_size: int,
        tile_format: str,
        previous: Optional[FlattenedMap],
        progress: Optional[Callable[[int, int], bool]],
    ) -> tuple[FlattenedMap, bool, Optional[set[str]]]:
        """
        Render the tiles and the preview into `staging`.

        Only the tiles the change can reach are rendered: the record stored
        with the tiles (tiles/_index.json) says which scene they show, the
        difference with the current scene gives the areas to render again;
        every other tile is taken from the previous compilation.

        Returns:
            The compilation, whether anything was rebuilt, and, when the tile
            grid did not move, the keys whose file must be replaced in place
            (None: the whole tiles folder in `staging` replaces the old one).
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
            return previous, False, None

        gx, gy, columns, rows = self.tile_grid(tile_size)
        total = columns * rows
        tiles_dir = staging / TILES_DIR
        tiles_dir.mkdir(parents=True)
        old_tiles_dir = map_dir / TILES_DIR
        scene = self._scene_record()

        # The previous compilation, as recorded with its tiles
        old: Optional[FlattenedMap] = None
        dirty: Optional[list[Rect]] = None
        old_rect: Optional[Rect] = None
        try:
            record = loads((old_tiles_dir / TILE_INDEX_FILE).read_text(encoding="utf-8"))
            candidate = FlattenedMap.from_dict(record["compiled"])
            if candidate.tile_size == tile_size and candidate.tile_format == tile_format:
                dirty = self._dirty_rects(record["scene"], scene)
                if dirty is not None:
                    old = candidate
                    old_rect = Rect(*(float(v) for v in record["scene"]["rect"]))
        except (OSError, ValueError, KeyError, TypeError):
            old = None
        in_place = old is not None and (old.origin_x, old.origin_y) == (gx, gy)
        new_rect = self.map_rect()

        media = MediaCache(map_dir)
        tiles: dict[str, str] = {}
        uniform: dict[str, str] = {}
        rendered: set[str] = set()

        # Qt refuses to decode large images by default
        previous_limit = QImageReader.allocationLimit()
        QImageReader.setAllocationLimit(0)
        try:
            done = 0
            for row in range(rows):
                for column in range(columns):
                    if progress is not None and not progress(done, total):
                        raise MapCompileCancelled()
                    done += 1
                    key = tile_key(column, row)
                    area = Rect(float(gx + column * tile_size), float(gy + row * tile_size),
                                float(tile_size), float(tile_size))
                    if old is not None and self._reuse_tile(
                        old, old_rect, new_rect, area, dirty, key, tiles, uniform,
                        old_tiles_dir, tiles_dir, in_place,
                    ):
                        continue
                    image = self.render_tile(column, row, tile_size, media, origin=(gx, gy))
                    raw = bytes(image.constBits())
                    first_pixel = raw[:4]
                    if raw == first_pixel * (len(raw) // 4):
                        uniform[key] = image.pixelColor(0, 0).name(QColor.NameFormat.HexArgb)
                    else:
                        tiles[key] = sha256(raw).hexdigest()
                        _save_tile(image, tiles_dir / f"{key}.{tile_format}", tile_format)
                    rendered.add(key)
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
            width=columns * tile_size,
            height=rows * tile_size,
            origin_x=gx,
            origin_y=gy,
            tile_size=tile_size,
            tile_format=tile_format,
            tiles=tiles,
            uniform=uniform,
        )
        (tiles_dir / TILE_INDEX_FILE).write_text(
            dumps({"compiled": compiled.to_dict(), "scene": scene}, separators=(",", ":")), encoding="utf-8"
        )
        if in_place:
            # Rendered tiles, plus the files of tiles that became uniform or left
            rendered |= {key for key in old.tiles if key not in tiles}
            rendered.add(TILE_INDEX_FILE)
        return compiled, True, rendered if in_place else None

    @staticmethod
    def _reuse_tile(
        old: FlattenedMap, old_rect: Rect, new_rect: Rect, area: Rect, dirty: list[Rect],
        key: str, tiles: dict[str, str], uniform: dict[str, str],
        old_tiles_dir: Path, tiles_dir: Path, in_place: bool,
    ) -> bool:
        """Take a tile from the previous compilation when nothing changed in it."""
        if any(area.intersects(rect) for rect in dirty):
            return False
        # The scene is clipped to the map's area: it must cover the tile the same way
        if _clip(area, old_rect) != _clip(area, new_rect):
            return False
        old_col = (area.x - old.origin_x) / old.tile_size
        old_row = (area.y - old.origin_y) / old.tile_size
        if old_col != int(old_col) or old_row != int(old_row) or old_col < 0 or old_row < 0:
            return False
        old_key = tile_key(int(old_col), int(old_row))
        if old_key in old.uniform:
            uniform[key] = old.uniform[old_key]
            return True
        if old_key not in old.tiles:
            return False
        source = old_tiles_dir / f"{old_key}.{old.tile_format}"
        if not source.is_file():
            return False
        tiles[key] = old.tiles[old_key]
        if not in_place:
            copy2(source, tiles_dir / f"{key}.{old.tile_format}")
        return True

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
        previous_state = self.compiled
        previous_rect = (self.origin_x, self.origin_y, self.width, self.height)
        if self.infinite:
            self.fit_to_content()
        try:
            self.validate(map_dir)
        except BaseException:
            self.origin_x, self.origin_y, self.width, self.height = previous_rect
            raise

        staging = map_dir / STAGING_DIR
        _remove(staging)
        staging.mkdir()
        try:
            compiled, rebuilt, changed_tiles = self._build(
                staging,
                map_dir,
                tile_size,
                tile_format,
                self.read_compiled(map_dir),
                progress,
            )
            self.compiled = compiled
            items: list[tuple[Path, Path]] = []
            if rebuilt and changed_tiles is not None:
                # Same tile grid: only the tiles that changed are replaced
                _replace_tiles(staging / TILES_DIR, map_dir / TILES_DIR, changed_tiles, tile_format)
                items.append((staging / PREVIEW_FILE, map_dir / PREVIEW_FILE))
            elif rebuilt:
                items.append((staging / TILES_DIR, map_dir / TILES_DIR))
                items.append((staging / PREVIEW_FILE, map_dir / PREVIEW_FILE))
            if write_json:
                (staging / MAP_FILE).write_text(json_text(self.to_dict()), encoding="utf-8")
                items.append((staging / MAP_FILE, map_dir / MAP_FILE))
            _commit(items)
        except BaseException:
            self.compiled = previous_state
            self.origin_x, self.origin_y, self.width, self.height = previous_rect
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

        Either every file is replaced or none is. An infinite map is first sized
        to its content (`fit_to_content`).

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


def _clip(area: Rect, rect: Rect) -> Optional[tuple[float, float, float, float]]:
    left, top = max(area.x, rect.x), max(area.y, rect.y)
    right, bottom = min(area.right, rect.right), min(area.bottom, rect.bottom)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _replace_tiles(source_dir: Path, target_dir: Path, keys: set[str], tile_format: str) -> None:
    """
    Update a tiles folder in place: each listed tile is replaced by its new
    file, or removed when it has none any more (uniform, or out of the map).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for key in sorted(keys):
        name = key if key == TILE_INDEX_FILE else f"{key}.{tile_format}"
        source, target = source_dir / name, target_dir / name
        if source.is_file():
            source.replace(target)
        else:
            target.unlink(missing_ok=True)
