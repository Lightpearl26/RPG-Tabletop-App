# -*- coding: utf-8 -*-
"""
Files sent to players: references, chunked transfer, client-side storage.

A player never asks for a file path. It asks for a *reference*, which the server
checks against what that player may see right now (net.asset_service):

    tile:<map_id>:<col>_<row>     one tile of the GM's current map
    token:<image id>              the picture of a token the player sees
    media:<map_id>:<path>         an image used by an annotation the player sees
    preview:<map_id>              the whole map in 512 px, for maps without a
                                  field of view (no grid) seen from far away

A file is sent as ASSET_CHUNK messages, 64 KB each, the bytes as the frame's
binary attachment (protocol.FLAG_BLOB). Outcomes without a file are one
ASSET_STATUS message (see AssetStatus).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

# Small chunks let pings, syncs and replies slip in between two pieces of a file
DEFAULT_CHUNK_BYTES = 64 * 1024


class AssetStatus(str, Enum):
    UNCHANGED = "unchanged"  # the copy the player has ("have") is still right
    UNIFORM = "uniform"      # a tile of one color, no file: "color" says which
    EMPTY = "empty"          # a tile with nothing drawn on it, no file
    DENIED = "denied"        # not visible to this player now
    MISSING = "missing"      # should exist but cannot be read


@dataclass(frozen=True, slots=True)
class AssetRef:
    kind: str       # "tile", "token", "media" or "preview"
    map_id: str     # "" for tokens
    name: str       # tile key, image id or map-relative path

    def __str__(self) -> str:
        if self.kind == "token":
            return f"token:{self.name}"
        if self.kind == "preview":
            return f"preview:{self.map_id}"
        return f"{self.kind}:{self.map_id}:{self.name}"

    @property
    def cache_path(self) -> PurePosixPath:
        """Where the player stores it, relative to its session cache."""
        if self.kind == "tile":
            return PurePosixPath("maps", self.map_id, "tiles", self.name)
        if self.kind == "token":
            return PurePosixPath("tokens", self.name)
        if self.kind == "preview":
            return PurePosixPath("maps", self.map_id, "preview")
        return PurePosixPath("maps", self.map_id, self.name)


def _safe_part(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def parse_ref(raw: Any) -> AssetRef:
    """Read and check a reference. Raises ValueError for anything unexpected."""
    text = str(raw or "")
    kind, _, rest = text.partition(":")
    if kind == "token":
        if not rest or not all(char in "0123456789abcdef" for char in rest) or len(rest) != 64:
            raise ValueError(f"Invalid token picture reference: {text!r}")
        return AssetRef("token", "", rest)
    if kind == "preview":
        if not _safe_part(rest):
            raise ValueError(f"Invalid preview reference: {text!r}")
        return AssetRef("preview", rest, "preview")
    if kind not in {"tile", "media"}:
        raise ValueError(f"Unknown asset reference: {text!r}")
    map_id, _, name = rest.partition(":")
    if not _safe_part(map_id) or not name:
        raise ValueError(f"Invalid asset reference: {text!r}")
    if kind == "tile":
        col, sep, row = name.partition("_")
        if not sep or not col.isdigit() or not row.isdigit():
            raise ValueError(f"Invalid tile key: {name!r}")
        return AssetRef("tile", map_id, f"{int(col)}_{int(row)}")
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe media path: {name!r}")
    return AssetRef("media", map_id, path.as_posix())


def file_chunks(
    ref: AssetRef, raw: bytes, extra: dict[str, Any], *, chunk_bytes: int = DEFAULT_CHUNK_BYTES
) -> Iterator[tuple[dict[str, Any], bytes]]:
    """(ASSET_CHUNK arguments, bytes) of each piece of a file."""
    count = max(1, (len(raw) + chunk_bytes - 1) // chunk_bytes)
    for index in range(count):
        yield (
            {"ref": str(ref), "index": index, "count": count, "size": len(raw), **extra},
            raw[index * chunk_bytes:(index + 1) * chunk_bytes],
        )


class AssetReceiver:
    """Puts chunks back together and stores complete files under `cache_root`."""

    def __init__(self, cache_root: str | Path) -> None:
        self.cache_root = Path(cache_root)
        self._buffers: dict[str, dict[int, bytes]] = {}

    def reset(self, cache_root: str | Path | None = None) -> None:
        if cache_root is not None:
            self.cache_root = Path(cache_root)
        self._buffers.clear()

    def receive_chunk(self, payload: dict[str, Any], data: bytes) -> Path | None:
        """Store one chunk; return the file once complete. Raises ValueError on bad data."""
        ref = parse_ref(payload.get("ref"))
        index, count = int(payload["index"]), int(payload["count"])
        if index < 0 or count <= 0 or index >= count:
            raise ValueError("Invalid asset chunk index")
        key = str(ref)
        if index == 0:
            self._buffers[key] = {}  # a new copy of this file restarts it
        buffer = self._buffers.get(key)
        if buffer is None:
            return None  # the start of this file was lost (cache switched)
        buffer[index] = bytes(data)
        if len(buffer) != count:
            return None
        raw = b"".join(buffer[i] for i in range(count))
        del self._buffers[key]
        if len(raw) != int(payload.get("size", len(raw))):
            raise ValueError("Asset size mismatch")
        target = self.cache_root / Path(*ref.cache_path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part")
        tmp.write_bytes(raw)
        tmp.replace(target)
        return target
