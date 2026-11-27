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


def _projection(present=True, tiles=None, x=1000.0):
    tiles = tiles if tiles is not None else {f"{c}_{r}": f"h{c}{r}" for c in range(40) for r in range(40)}
    compiled = {
        "source_map_id": "big", "source_hash": "s", "size": [40 * 256, 40 * 256], "origin": [0, 0],
        "tile_size": 256, "format": "webp", "tiles": tiles, "uniform": {}, "signature": str(hash(str(tiles))),
    }
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
            "tokens": [{"id": "t", "entity_id": "e", "pos": [x, 1000.0], "self": True}] if present else [],
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


def test_only_tiles_of_the_field_of_view_are_requested(window):
    window._apply_projection(_projection())
    window.map_view.fit_map()
    window._request_current_visible_assets()
    requested = [item["path"] for items, _ in window.sent for item in items]
    assert 0 < len(requested) <= 16  # 11x11 cells of 64 px around the token: a few tiles, not 1600
    assert len(set(requested)) == len(requested)


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


def test_recompiled_map_keeps_unchanged_tiles(window, tmp_path):
    window._apply_projection(_projection())
    tiles_dir = window.controller.cache_root / "maps" / "big" / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    (tiles_dir / "0_0.webp").write_bytes(b"a")
    (tiles_dir / "1_0.webp").write_bytes(b"b")
    tiles = {f"{c}_{r}": f"h{c}{r}" for c in range(40) for r in range(40)}
    tiles["1_0"] = "changed"
    window._apply_projection(_projection(tiles=tiles))
    assert (tiles_dir / "0_0.webp").is_file()
    assert not (tiles_dir / "1_0.webp").exists()
