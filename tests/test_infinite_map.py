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


def test_old_maps_become_infinite_keep_their_frame_and_their_hash():
    old = {"version": 1, "id": "abc", "name": "Old", "size": [4096, 4096]}
    asset = MapAsset.from_dict(old)
    assert asset.infinite and (asset.origin_x, asset.origin_y) == (0, 0)
    rect = asset.auto_rect()
    assert rect.x <= 0 and rect.y <= 0 and rect.right >= 4096 and rect.bottom >= 4096
    fresh = MapAsset(id="abc", name="Old")
    assert asset.source_hash() == fresh.source_hash()  # no recompilation for nothing


def test_the_background_image_of_an_old_map_stays_where_it_was():
    from rpg_tabletop.assets.map import ImageElement

    old = {"version": 1, "id": "abc", "name": "Old", "size": [2000, 1000],
           "background": {"color": "#102030", "image_asset": "media/bg.png"}}
    asset = MapAsset.from_dict(old)
    image = asset.elements[0]
    assert isinstance(image, ImageElement) and asset.background.image_asset is None
    assert (image.pos.x, image.pos.y, image.width, image.height) == (0, 0, 2000, 1000)
    again = MapAsset.from_dict(asset.to_dict())
    assert len(again.elements) == 1 and again.kept_area == asset.kept_area


def test_save_sizes_the_map_and_offsets_the_tiles(tmp_path):
    tile_format = _tile_format()
    asset = MapAsset(infinite=True)
    asset.elements.append(_rect_element(-600, -100, -500, 0))
    compiled = asset.save(tmp_path, tile_size=128, tile_format=tile_format)

    assert asset.origin_x == -704 and asset.origin_y == -192
    assert asset.width == 320 and asset.height == 320
    # Tiles are counted from a multiple of the tile size and cover the whole map
    assert (compiled.origin_x, compiled.origin_y) == (-768, -256)
    assert compiled.origin_x % 128 == 0 and compiled.origin_y % 128 == 0
    assert compiled.origin_x + compiled.width >= asset.origin_x + asset.width
    assert compiled.origin_y + compiled.height >= asset.origin_y + asset.height

    # Read back: the map file and the compilation agree
    loaded = MapAsset.load(tmp_path)
    assert loaded.infinite and loaded.origin_x == -704 and loaded.width == 320
    assert loaded.compiled is not None and loaded.compiled.origin_x == -768

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


def _scene(tile_format):
    asset = MapAsset(infinite=True)
    for index in range(6):
        x = index * 700
        asset.elements.append(_rect_element(x, 0, x + 200, 300))
    return asset


def _full_build(asset, tmp_path, tile_format):
    """The same scene compiled from scratch, for comparison."""
    fresh = MapAsset.from_dict(asset.to_dict())
    fresh.compiled = None
    return fresh.save(tmp_path / "full", tile_size=256, tile_format=tile_format)


def test_incremental_build_renders_only_the_changed_tiles_and_matches_a_full_build(tmp_path, monkeypatch):
    tile_format = _tile_format()
    asset = _scene(tile_format)
    asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)

    rendered = []
    original = MapAsset.render_tile
    monkeypatch.setattr(MapAsset, "render_tile",
                        lambda self, c, r, *a, **k: rendered.append((c, r)) or original(self, c, r, *a, **k))
    # Move one rectangle a little: only the tiles around it are drawn again
    asset.elements[2] = _rect_element(1450, 0, 1650, 300)
    asset.elements[2].id = "moved"
    compiled = asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)
    total = (compiled.width // 256) * (compiled.height // 256)
    assert 0 < len(rendered) < total // 3

    monkeypatch.setattr(MapAsset, "render_tile", original)
    full = _full_build(asset, tmp_path, tile_format)
    assert compiled.tiles == full.tiles and compiled.uniform == full.uniform
    for key in compiled.tiles:  # every recorded tile has its file
        assert (tmp_path / "map" / "tiles" / f"{key}.{tile_format}").is_file()


def test_growing_to_the_left_keeps_the_tiles_already_made(tmp_path, monkeypatch):
    tile_format = _tile_format()
    asset = _scene(tile_format)
    first = asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)

    rendered = []
    original = MapAsset.render_tile
    monkeypatch.setattr(MapAsset, "render_tile",
                        lambda self, c, r, *a, **k: rendered.append((c, r)) or original(self, c, r, *a, **k))
    asset.elements.append(_rect_element(-2000, 0, -1800, 200))  # far to the left
    compiled = asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)
    assert compiled.origin_x < first.origin_x
    assert len(rendered) < (compiled.width // 256) * (compiled.height // 256)

    monkeypatch.setattr(MapAsset, "render_tile", original)
    full = _full_build(asset, tmp_path, tile_format)
    assert compiled.tiles == full.tiles and compiled.uniform == full.uniform


def test_reordering_or_a_new_background_redraws_everything(tmp_path, monkeypatch):
    tile_format = _tile_format()
    asset = _scene(tile_format)
    asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)
    asset.background.color = "#334455"
    compiled = asset.save(tmp_path / "map", tile_size=256, tile_format=tile_format)
    full = _full_build(asset, tmp_path, tile_format)
    assert compiled.tiles == full.tiles and compiled.uniform == full.uniform
