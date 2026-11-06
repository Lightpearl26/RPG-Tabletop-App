# -*- coding: utf-8 -*-

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
`MapAsset.render_scene` (asset.py) draws the scene with a `QPainter`. The editor canvas and the
flatten use that same code, so what the GM sees is what gets compiled. Rendering
needs a `QGuiApplication` (fonts), which the app always has when it compiles.

Coordinates are in pixels. A tile is `tile_size` x `tile_size` pixels; tiles on the
right and bottom edges are padded with transparent pixels.

Modules
-------
common (file layout, issues), geometry (Vec2, Rect), media (images), elements
(scene elements), settings (background, grid, camera, tokens), tiles (compiled
tiles) and asset (MapAsset). Import from the package: `from rpg_tabletop.assets.map import MapAsset`.
"""

from .common import (
    DEFAULT_TILE_FORMAT,
    DEFAULT_TILE_SIZE,
    ERROR,
    FORMAT_VERSION,
    MAP_FILE,
    MAX_MAP_SIDE_PX,
    MEDIA_DIR,
    MapCompileCancelled,
    MapIssue,
    PREVIEW_FILE,
    PREVIEW_MAX_PX,
    STAGING_DIR,
    TILES_DIR,
    TILE_FORMATS,
    WARNING,
)
from .geometry import (
    Rect,
    Vec2,
)
from .media import (
    MediaCache,
    import_media,
    media_size,
)
from .elements import (
    DrawTool,
    DrawingElement,
    ELEMENT_TYPES,
    ImageElement,
    MapElement,
    TextElement,
    WALL_COLOR,
    WALL_WIDTH,
    WallElement,
)
from .settings import (
    CameraState,
    GridSettings,
    InitialTokenPlacement,
    MapBackground,
)
from .tiles import (
    FlattenedMap,
    supported_tile_formats,
    tile_key,
)
from .asset import (
    MapAsset,
    scan_entity_ids,
)

__all__ = [
    "DEFAULT_TILE_FORMAT",
    "DEFAULT_TILE_SIZE",
    "ERROR",
    "FORMAT_VERSION",
    "MAP_FILE",
    "MAX_MAP_SIDE_PX",
    "MEDIA_DIR",
    "MapCompileCancelled",
    "MapIssue",
    "PREVIEW_FILE",
    "PREVIEW_MAX_PX",
    "STAGING_DIR",
    "TILES_DIR",
    "TILE_FORMATS",
    "WARNING",
    "Rect",
    "Vec2",
    "MediaCache",
    "import_media",
    "media_size",
    "DrawTool",
    "DrawingElement",
    "ELEMENT_TYPES",
    "ImageElement",
    "MapElement",
    "TextElement",
    "WALL_COLOR",
    "WALL_WIDTH",
    "WallElement",
    "CameraState",
    "GridSettings",
    "InitialTokenPlacement",
    "MapBackground",
    "FlattenedMap",
    "supported_tile_formats",
    "tile_key",
    "MapAsset",
    "scan_entity_ids",
]
