# -*- coding: utf-8 -*-

"""
Images of a map: import into `media/`, size probing and the render-time cache.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from shutil import copyfile

from PySide6.QtGui import (
    QImage,
    QImageReader,
)

from .common import MEDIA_DIR

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
