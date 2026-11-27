# -*- coding: utf-8 -*-
"""GM runtime window: maps created during a session can be opened."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from rpg_tabletop.assets.campaign import Campaign  # noqa: E402
from rpg_tabletop.assets.map import MapAsset  # noqa: E402
from rpg_tabletop.session.runtime import SessionRuntime  # noqa: E402
from rpg_tabletop.ui.gm_runtime.window import RuntimeWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_a_map_created_during_the_session_can_be_selected(app, tmp_path):
    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    MapAsset(width=512, height=512).save(campaign.map_path("cave"))
    runtime = SessionRuntime.start_new("Partie", campaign, entities=[], initial_map_id="cave")
    window = RuntimeWindow(runtime)
    try:
        assert window.map_combo.findData("forest") < 0

        MapAsset(width=512, height=512).save(campaign.map_path("forest"))  # made in the IDE
        window.on_asset_saved("maps", "forest")
        index = window.map_combo.findData("forest")
        assert index >= 0 and window.map_combo.currentData() == "cave"

        window.map_combo.setCurrentIndex(index)
        assert runtime.active_map_id == "forest"

        MapAsset(width=512, height=512).save(campaign.map_path("tower"))  # no notification
        window.map_combo.about_to_open()
        assert window.map_combo.findData("tower") >= 0
        assert window.map_combo.currentData() == "forest"
    finally:
        window._closing = True
        window.close()
