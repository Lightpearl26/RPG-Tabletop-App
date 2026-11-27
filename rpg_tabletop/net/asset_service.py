# -*- coding: utf-8 -*-
"""
Server side of ASSET_REQUEST: what a player may download, checked per file.

Each reference is checked when its turn comes to be sent, against the session
as it is then: a tile that left the player's field of view in the meantime is
refused. A modified client therefore cannot fetch the rest of the map, another
map, a GM-only file or the picture of a token it does not see.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rpg_tabletop.core.geometry import tiles_in_box
from rpg_tabletop.session.runtime import SessionRuntime
from .assets import AssetRef, AssetStatus
from .projection import (
    compiled_version, element_visible, player_view_box, token_image_id,
)


@dataclass(slots=True)
class AssetAnswer:
    """Either a file to send (`path`) or a status without a file."""
    ref: AssetRef
    status: AssetStatus | None = None
    path: Path | None = None
    # Sent with the chunks or the status (hash, version, color...)
    extra: dict[str, Any] = field(default_factory=dict)

    def status_args(self) -> dict[str, Any]:
        return {"ref": str(self.ref), "status": self.status.value if self.status else "", **self.extra}


def answer_asset(runtime: SessionRuntime, entity_id: str | None, ref: AssetRef, have: str = "") -> AssetAnswer:
    if entity_id is None:
        return AssetAnswer(ref, AssetStatus.DENIED)
    try:
        if ref.kind == "tile":
            return _tile(runtime, entity_id, ref, have)
        if ref.kind == "token":
            return _token(runtime, entity_id, ref, have)
        return _media(runtime, entity_id, ref)
    except (KeyError, RuntimeError, ValueError, OSError):
        return AssetAnswer(ref, AssetStatus.MISSING)


def _tile(runtime: SessionRuntime, entity_id: str, ref: AssetRef, have: str) -> AssetAnswer:
    if ref.map_id != runtime.active_map_id:
        return AssetAnswer(ref, AssetStatus.DENIED)
    box = player_view_box(runtime, entity_id)
    if box is False:
        return AssetAnswer(ref, AssetStatus.DENIED)
    asset = runtime._active_map_asset()
    compiled = asset.compiled
    if compiled is None:
        return AssetAnswer(ref, AssetStatus.MISSING)
    version = compiled_version(compiled)
    col, row = (int(part) for part in ref.name.split("_", 1))
    if col >= compiled.columns or row >= compiled.rows:
        return AssetAnswer(ref, AssetStatus.EMPTY, extra={"version": version})
    if box is not None:
        visible = tiles_in_box(
            box, compiled.origin_x, compiled.origin_y, compiled.tile_size,
            compiled.width, compiled.height,
        )
        if (col, row) not in visible:
            return AssetAnswer(ref, AssetStatus.DENIED)
    if ref.name in compiled.uniform:
        return AssetAnswer(ref, AssetStatus.UNIFORM,
                           extra={"version": version, "color": compiled.uniform[ref.name]})
    digest = compiled.tiles.get(ref.name)
    if digest is None:
        return AssetAnswer(ref, AssetStatus.EMPTY, extra={"version": version})
    if have and have == digest:
        return AssetAnswer(ref, AssetStatus.UNCHANGED, extra={"version": version, "hash": digest})
    path = compiled.tile_path(runtime.campaign.map_path(ref.map_id), col, row)
    if path is None or not path.is_file():
        return AssetAnswer(ref, AssetStatus.MISSING)
    return AssetAnswer(ref, path=path, extra={"version": version, "hash": digest})


def _token(runtime: SessionRuntime, entity_id: str, ref: AssetRef, have: str) -> AssetAnswer:
    if runtime.active_map is None:
        return AssetAnswer(ref, AssetStatus.DENIED)
    for token in runtime.visible_tokens_for(entity_id):
        entity = runtime.entities.get(str(token.entity_id))
        if entity is None or not entity.token or token_image_id(entity.token) != ref.name:
            continue
        if have and have == ref.name:
            return AssetAnswer(ref, AssetStatus.UNCHANGED, extra={"hash": ref.name})
        return AssetAnswer(ref, path=Path(entity.token), extra={"hash": ref.name})
    return AssetAnswer(ref, AssetStatus.DENIED)


def _media(runtime: SessionRuntime, entity_id: str, ref: AssetRef) -> AssetAnswer:
    if ref.map_id != runtime.active_map_id or runtime.active_map is None:
        return AssetAnswer(ref, AssetStatus.DENIED)
    box = player_view_box(runtime, entity_id)
    for element in runtime.active_map.elements:
        asset = getattr(element, "asset", None)
        if asset is None or Path(asset).as_posix() != ref.name:
            continue
        if not getattr(element, "visible", True) or not element_visible(element, box):
            continue
        map_dir = runtime.campaign.map_path(ref.map_id).resolve()
        path = (map_dir / ref.name).resolve()
        path.relative_to(map_dir)  # ValueError (-> missing) if it escapes the map folder
        if not path.is_file():
            return AssetAnswer(ref, AssetStatus.MISSING)
        return AssetAnswer(ref, path=path)
    return AssetAnswer(ref, AssetStatus.DENIED)
