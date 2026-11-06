# -*- coding: utf-8 -*-

"""
MapAsset: a whole map, its validation, its rendering and its compilation into tiles.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps, loads
from math import ceil
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
    DEFAULT_TILE_FORMAT,
    DEFAULT_TILE_SIZE,
    ERROR,
    FORMAT_VERSION,
    MAP_FILE,
    MAX_MAP_SIDE_PX,
    PREVIEW_FILE,
    PREVIEW_MAX_PX,
    STAGING_DIR,
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
            "elements": [
                element.to_dict() for element in self.elements
                if element.visible and element.flattened
            ],
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
