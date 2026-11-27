# -*- coding: utf-8 -*-
"""
What the player knows about the tiles of the current map.

The server sends only the structure of the map (size, origin, tile size and a
compilation `version`), never the list of its tiles. The player learns each tile
when it asks for it, and keeps the answer here, saved next to the tile files:

    {"v": version, "h": hash}   a file, in the cache under tiles/<key>
    {"v": version, "c": color}  a tile of one color, no file
    {"v": version, "e": 1}      nothing drawn there, no file

An entry learnt for an older compilation is still drawn (better than a hole)
but asked again, with its hash, so that the server answers "unchanged" instead
of sending the same file twice.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

INDEX_FILE = ".tiles.json"


class TileCache:
    def __init__(self) -> None:
        self.map_root: Path | None = None
        self.version = ""
        self.layout: dict[str, Any] | None = None
        self.entries: dict[str, dict[str, Any]] = {}
        self._dirty = False

    # ------------------------------------------------------------------ map
    def open(self, map_root: Path, compiled: dict[str, Any] | None) -> bool:
        """
        Use the cache of one map. Return True when the files known so far are
        no longer valid (other map, other tiling), so decoded images must go.
        """
        map_root = Path(map_root)
        layout = None
        if isinstance(compiled, dict):
            layout = {"tile_size": compiled.get("tile_size"), "origin": list(compiled.get("origin", [0, 0]))}
            self.version = str(compiled.get("version", ""))
        else:
            self.version = ""
        if map_root == self.map_root and (layout is None or layout == self.layout):
            return False

        self.save()
        self.map_root = map_root
        self.entries = {}
        self.layout = layout
        try:
            saved = json.loads((map_root / INDEX_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict) and layout is not None and saved.get("layout") == layout:
            entries = saved.get("tiles")
            self.entries = {str(k): v for k, v in entries.items() if isinstance(v, dict)} if isinstance(entries, dict) else {}
        elif layout is not None:
            # Unknown cache or another tiling: the same key is another area now
            shutil.rmtree(map_root / "tiles", ignore_errors=True)
            self._dirty = True
        return True

    def close(self) -> None:
        self.save()
        self.map_root = None
        self.layout = None
        self.entries = {}
        self.version = ""

    def save(self) -> None:
        if not self._dirty or self.map_root is None or self.layout is None:
            return
        try:
            self.map_root.mkdir(parents=True, exist_ok=True)
            (self.map_root / INDEX_FILE).write_text(
                json.dumps({"layout": self.layout, "tiles": self.entries}, separators=(",", ":")),
                encoding="utf-8",
            )
            self._dirty = False
        except OSError:
            pass

    # ---------------------------------------------------------------- tiles
    def tile_file(self, key: str) -> Path | None:
        return None if self.map_root is None else self.map_root / "tiles" / key

    def lookup(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def request_for(self, key: str) -> str | None:
        """
        None: nothing to ask. Otherwise the hash of the copy in the cache, to
        send as "have" ("" when there is none).
        """
        entry = self.entries.get(key)
        path = self.tile_file(key)
        has_file = bool(entry and "h" in entry and path is not None and path.is_file())
        if entry is not None and entry.get("v") == self.version:
            if "h" in entry and not has_file:
                return ""  # the file was removed: ask again
            return None
        return str(entry["h"]) if has_file else ""

    def record(self, key: str, *, version: str, file_hash: str | None = None,
               color: str | None = None, empty: bool = False) -> None:
        if file_hash is not None:
            entry: dict[str, Any] = {"v": version, "h": file_hash}
        elif color is not None:
            entry = {"v": version, "c": color}
        elif empty:
            entry = {"v": version, "e": 1}
        else:
            return
        if not file_hash:
            path = self.tile_file(key)
            if path is not None:
                path.unlink(missing_ok=True)
        self.entries[key] = entry
        self._dirty = True
