# -*- coding: utf-8 -*-

"""Infinite maps: sized to their content at compilation, around a fixed origin."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from rpg_tabletop.assets.map import (  # noqa: E402
    DrawTool,
    DrawingElement,
    InitialTokenPlacement,
    MapAsset,
    MediaCache,
    Vec2,
    supported_tile_formats,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QGuiApplication.instance() or QGuiApplication([])
    yield app


def _rect_element(x0: float, y0: float, x1: float, y1: float) -> DrawingElement:
    return DrawingElement(
        tool=DrawTool.RECT,
        points=[Vec2(x0, y0), Vec2(x1, y1)],
        stroke_width=0.0,
        fill_color="#ff0000",
    )


def _tile_format() -> str:
    formats = supported_tile_formats()
    if not formats:
        pytest.skip("No tile format available")
    return formats[-1]


def test_empty_infinite_map_gets_a_margin_around_the_origin():
    asset = MapAsset(infinite=True)
    rect = asset.auto_rect()
    cell = asset.grid.cell_size
    assert (rect.x, rect.y, rect.width, rect.height) == (-cell, -cell, 2 * cell, 2 * cell)


def test_auto_rect_follows_content_on_grid_lines():
    asset = MapAsset(infinite=True)
    asset.elements.append(_rect_element(-300, -10, 100, 200))
    rect = asset.auto_rect()
    # Bounds are inflated by 1 px, then snapped outward on 64 px cells, plus one cell
    assert rect.x == -384 and rect.y == -128
    assert rect.right == 192 and rect.bottom == 320
    assert rect.x % 64 == 0 and rect.width % 64 == 0


def test_tokens_count_in_the_content():
    asset = MapAsset(infinite=True)
    asset.initial_tokens.append(InitialTokenPlacement(entity_id="e", pos=Vec2(1000, 1000)))
    rect = asset.auto_rect()
    assert rect.x <= 1000 - 32 and rect.right >= 1000 + 32


def test_fit_does_not_move_anything():
    asset = MapAsset(infinite=True)
    element = _rect_element(-500, -500, -400, -400)
    asset.elements.append(element)
    before = element.to_dict()
    asset.fit_to_content()
    assert element.to_dict() == before
    assert asset.origin_x < -500 and asset.origin_y < -500


def test_round_trip_keeps_origin_and_mode():
    asset = MapAsset(infinite=True, origin_x=-128, origin_y=64, width=256, height=512)
    copy = MapAsset.from_dict(asset.to_dict())
    assert (copy.infinite, copy.origin_x, copy.origin_y, copy.width, copy.height) == (
        True, -128, 64, 256, 512,
    )


def test_old_maps_read_as_fixed_at_zero_and_keep_their_hash():
    old = {"version": 1, "id": "abc", "name": "Old", "size": [4096, 4096]}
    asset = MapAsset.from_dict(old)
    assert not asset.infinite and (asset.origin_x, asset.origin_y) == (0, 0)
    fresh = MapAsset(id="abc", name="Old")
    assert asset.source_hash() == fresh.source_hash()


def test_save_sizes_the_map_and_offsets_the_tiles(tmp_path):
    tile_format = _tile_format()
    asset = MapAsset(infinite=True)
    asset.elements.append(_rect_element(-600, -100, -500, 0))
    compiled = asset.save(tmp_path, tile_size=128, tile_format=tile_format)

    assert (compiled.origin_x, compiled.origin_y) == (asset.origin_x, asset.origin_y)
    assert (compiled.width, compiled.height) == (asset.width, asset.height)
    assert asset.origin_x == -704 and asset.origin_y == -192
    assert asset.width == 320 and asset.height == 320

    # Read back: the map file and the compilation agree
    loaded = MapAsset.load(tmp_path)
    assert loaded.infinite and loaded.origin_x == -704 and loaded.width == 320
    assert loaded.compiled is not None and loaded.compiled.origin_x == -704

    # The red rectangle (-600..-500, -100..0) lands at 104..204, 92..192 from the corner
    image = asset.render_tile(0, 0, 128, MediaCache(tmp_path))
    assert image.pixelColor(110, 100).name() == "#ff0000"
    assert image.pixelColor(50, 50).name() != "#ff0000"


def test_failed_validation_restores_the_previous_size(tmp_path):
    asset = MapAsset(infinite=True, width=100, height=100)
    asset.elements.append(_rect_element(0, 0, 70000, 10))
    with pytest.raises(ValueError, match="content spans"):
        asset.save(tmp_path, tile_format=_tile_format())
    assert (asset.origin_x, asset.origin_y, asset.width, asset.height) == (0, 0, 100, 100)


def test_fixed_maps_are_not_resized(tmp_path):
    asset = MapAsset(width=512, height=256)
    asset.elements.append(_rect_element(-600, -100, -500, 0))
    asset.save(tmp_path, tile_size=256, tile_format=_tile_format())
    assert (asset.origin_x, asset.origin_y, asset.width, asset.height) == (0, 0, 512, 256)
