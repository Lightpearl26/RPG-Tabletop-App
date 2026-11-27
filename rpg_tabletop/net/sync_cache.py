# -*- coding: utf-8 -*-
"""Do not send the same heavy parts of a player projection twice.

A RUNTIME_SYNC goes to every player after each change in the session. Two of its
parts are heavy and almost never change: the picture of each visible token
(hex-encoded) and the description of the compiled map (one hash per tile, so
thousands of entries on a big map).

The server keeps, per connection, which pictures and which compiled maps that
player already received, and replaces them by a short reference
(`compact_projection`). The client keeps what it received for the life of the
same connection and puts the parts back before anyone reads the projection
(`expand_projection`). Both caches start empty on each new connection, so they
always agree.
"""
from __future__ import annotations

from typing import Any


def compiled_signature(compiled: dict[str, Any]) -> str:
    """What identifies one compilation of a map: same signature, same tiles."""
    size = compiled.get("size", [0, 0])
    origin = compiled.get("origin", [0, 0])
    try:
        width, height = (int(value) for value in size)
        ox, oy = (int(value) for value in origin)
    except (TypeError, ValueError):
        width = height = ox = oy = 0
    return (
        f"{compiled.get('source_hash', '')}:{compiled.get('tile_size', '')}:"
        f"{compiled.get('format', '')}:{width}x{height}:{ox},{oy}:{len(compiled.get('tiles', {}) or {})}"
    )


def compact_projection(
    projection: dict[str, Any],
    sent_images: set[str],
    sent_compiled: set[str],
) -> dict[str, Any]:
    """
    Server side: a copy of `projection` without what this player already has.

    `sent_images` and `sent_compiled` belong to the player's connection; they are
    updated with what this projection sends in full.
    """
    map_data = projection.get("map")
    if not isinstance(map_data, dict):
        return projection
    map_copy = dict(map_data)

    tokens = []
    for token in map_data.get("tokens", []) or []:
        if isinstance(token, dict) and token.get("image_id") and token.get("image") is not None:
            image_id = str(token["image_id"])
            if image_id in sent_images:
                token = {key: value for key, value in token.items() if key != "image"}
            else:
                sent_images.add(image_id)
        tokens.append(token)
    map_copy["tokens"] = tokens

    asset = map_data.get("asset")
    if isinstance(asset, dict) and isinstance(asset.get("compiled"), dict):
        compiled = asset["compiled"]
        signature = str(compiled.get("signature") or "")
        if signature and not compiled.get("cached"):
            if signature in sent_compiled:
                asset = dict(asset)
                asset["compiled"] = {"signature": signature, "cached": True}
                map_copy["asset"] = asset
            else:
                sent_compiled.add(signature)

    result = dict(projection)
    result["map"] = map_copy
    return result


def expand_projection(
    projection: dict[str, Any],
    images: dict[str, str],
    compiled_maps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Client side: put back the parts the server left out, in place.

    `images` (image id -> hex) and `compiled_maps` (signature -> compiled map)
    belong to the connection and learn every full part this projection carries.
    """
    map_data = projection.get("map")
    if not isinstance(map_data, dict):
        return projection

    for token in map_data.get("tokens", []) or []:
        if not isinstance(token, dict) or not token.get("image_id"):
            continue
        image_id = str(token["image_id"])
        if token.get("image") is not None:
            images[image_id] = token["image"]
        elif image_id in images:
            token["image"] = images[image_id]

    asset = map_data.get("asset")
    if isinstance(asset, dict) and isinstance(asset.get("compiled"), dict):
        compiled = asset["compiled"]
        signature = str(compiled.get("signature") or "")
        if compiled.get("cached"):
            # Unknown reference: never happens with a well-behaved server, and
            # showing "not compiled" is better than drawing the wrong tiles.
            asset["compiled"] = compiled_maps.get(signature)
        elif signature:
            compiled_maps[signature] = compiled
    return projection
