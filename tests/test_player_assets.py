# -*- coding: utf-8 -*-
"""Player window: which map files it asks for, and what it keeps in its cache."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rpg_tabletop.net import ClientSettings  # noqa: E402
from rpg_tabletop.ui.player.window import PlayerRuntimeWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _projection(present=True, version="v1", x=1000.0):
    compiled = {"version": version, "size": [40 * 256, 40 * 256], "origin": [0, 0],
                "tile_size": 256, "format": "webp"}
    return {
        "session": {"id": "sess", "name": "S"},
        "entity": {"id": "e", "name": "Hero"},
        "combat": {"active": False},
        "map": {
            "map_id": "big", "present": present,
            "asset": {"id": "big", "name": "Big", "size": [40 * 256, 40 * 256], "origin": [0, 0],
                      "grid": {"enabled": True, "cell_size": 64, "offset": [0, 0]},
                      "compiled": compiled if present else None},
            "fov": {"radius_cells": 5, "origin": [x, 1000.0], "metric": "chebyshev"} if present else None,
            "tokens": [{"id": "t", "entity_id": "e", "pos": [x, 1000.0], "self": True,
                        "image_id": "a" * 64}] if present else [],
            "elements": [],
        },
    }


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    win = PlayerRuntimeWindow(ClientSettings(host="127.0.0.1"), asset_cache=tmp_path / "cache")
    win.resize(1400, 900)
    win.show()
    sent = []
    monkeypatch.setattr(type(win.controller), "is_connected", property(lambda self: True))
    monkeypatch.setattr(win.controller, "request_assets",
                        lambda items, replace=False: sent.append((list(items), replace)))
    win.sent = sent
    yield win
    win._closing = True
    win.close()


def _refs(window):
    return [item["ref"] for items, _ in window.sent for item in items]


def test_only_tiles_of_the_field_of_view_are_requested(window):
    window._apply_projection(_projection())
    window.map_view.fit_map()
    window._request_current_visible_assets()
    refs = _refs(window)
    tiles = [ref for ref in refs if ref.startswith("tile:big:")]
    assert 0 < len(tiles) <= 16  # 11x11 cells of 64 px around the token: a few tiles, not 1600
    assert "token:" + "a" * 64 in refs  # the token picture is downloaded the same way
    assert len(set(refs)) == len(refs)


def test_player_absent_from_the_map_requests_nothing(window):
    window._apply_projection(_projection(present=False))
    window._request_current_visible_assets()
    assert window.sent == []
    assert "pas sur cette carte" in window.map_status.text()


def test_moving_away_drops_tiles_not_received(window):
    window._apply_projection(_projection(x=1000.0))
    window._request_current_visible_assets()
    window._apply_projection(_projection(x=8000.0))
    window._request_current_visible_assets()
    assert window.sent[-1][1] is True  # replace: the old tiles are no longer wanted


def test_cached_tiles_are_not_asked_again_and_are_checked_after_a_recompile(window):
    window._apply_projection(_projection())
    keys = sorted(window.map_view.visible_tile_keys())
    tiles = window.map_view.tiles
    first, second = keys[0], keys[1]
    tiles.tile_file(first).parent.mkdir(parents=True, exist_ok=True)
    tiles.tile_file(first).write_bytes(b"x")
    window._asset_received({"ref": f"tile:big:{first}", "hash": "h1", "version": "v1",
                            "file": str(tiles.tile_file(first))})
    window._asset_status({"ref": f"tile:big:{second}", "status": "uniform", "color": "#ff102030",
                          "version": "v1"})
    window._requested_assets.clear()
    window.sent.clear()
    window._request_current_visible_assets()
    refs = _refs(window)
    assert f"tile:big:{first}" not in refs and f"tile:big:{second}" not in refs

    window._apply_projection(_projection(version="v2"))  # the GM saved the map again
    window._requested_assets.clear()
    window.sent.clear()
    window._request_current_visible_assets()
    items = {item["ref"]: item["have"] for batch, _ in window.sent for item in batch}
    assert items[f"tile:big:{first}"] == "h1"  # server can answer "unchanged"
    assert items[f"tile:big:{second}"] == ""


def test_tile_cache_survives_a_restart_and_drops_another_tiling(tmp_path):
    from rpg_tabletop.ui.player.tile_cache import TileCache

    compiled = {"version": "v1", "tile_size": 256, "origin": [0, 0]}
    cache = TileCache()
    cache.open(tmp_path / "m", compiled)
    cache.tile_file("0_0").parent.mkdir(parents=True)
    cache.tile_file("0_0").write_bytes(b"x")
    cache.record("0_0", version="v1", file_hash="h")
    cache.close()

    again = TileCache()
    again.open(tmp_path / "m", compiled)
    assert again.request_for("0_0") is None
    again.close()

    other = TileCache()
    other.open(tmp_path / "m", dict(compiled, tile_size=512))
    assert other.lookup("0_0") is None and not (tmp_path / "m" / "tiles" / "0_0").exists()


def test_view_paints_cached_tiles_without_errors(window, capfd):
    window._apply_projection(_projection())
    tiles = window.map_view.tiles
    for key in window.map_view.visible_tile_keys():
        tiles.record(key, version="v1", color="#ff334455")
    window.map_view.grab()
    assert "Traceback" not in capfd.readouterr().err
