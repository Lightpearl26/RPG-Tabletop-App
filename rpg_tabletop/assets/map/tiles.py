# -*- coding: utf-8 -*-

"""
Compiled (flattened) tiles: naming, formats, the FlattenedMap record and atomic writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from shutil import rmtree
from typing import Optional

from PySide6.QtGui import (
    QImage,
    QImageWriter,
)

from .common import TILE_FORMATS, TILES_DIR

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
