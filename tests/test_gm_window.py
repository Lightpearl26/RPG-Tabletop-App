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


def test_map_edits_made_in_the_session_are_published_with_only_their_tiles(app, tmp_path, monkeypatch):
    from rpg_tabletop.assets.map import DrawingElement, DrawTool, Vec2, supported_tile_formats

    if not supported_tile_formats():
        pytest.skip("No tile format available")
    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    asset = MapAsset(infinite=True)
    for index in range(4):
        asset.elements.append(DrawingElement(tool=DrawTool.RECT, points=[Vec2(index * 600, 0), Vec2(index * 600 + 100, 100)],
                                             stroke_width=0, fill_color="#ff0000"))
    asset.save(campaign.map_path("cave"))
    runtime = SessionRuntime.start_new("Partie", campaign, entities=[], initial_map_id="cave")
    window = RuntimeWindow(runtime)
    published = []
    monkeypatch.setattr(window, "broadcast_runtime_sync", lambda: published.append(True))
    try:
        window.mode_tabs.setCurrentWidget(window._edit_page)  # "Édition de la carte"
        editor = window._map_editor
        assert editor is not None and editor.resource_id == "cave"

        before = MapAsset.load(campaign.map_path("cave")).compiled
        editor.asset.elements[1].points = [Vec2(650, 0), Vec2(750, 100)]  # the GM moves a shape
        editor._refresh_modified()
        assert window._publish_timer.isActive()  # published after a short pause
        assert window.publish_map_edits()

        after = MapAsset.load(campaign.map_path("cave")).compiled
        changed = {key for key in set(before.tiles) | set(after.tiles) if before.tiles.get(key) != after.tiles.get(key)}
        assert changed and len(changed) <= 4          # only the tiles around the shape
        assert published                               # players are told right away
        assert window.map_view.asset.compiled.source_hash == after.source_hash
    finally:
        window._closing = True
        window.close()


def test_an_entity_created_from_the_session_joins_the_game(app, tmp_path, monkeypatch):
    import json
    import uuid
    from rpg_tabletop.assets.campaign import set_current_campaign
    from rpg_tabletop.ui.campaign_manager.window import CampaignManagerWindow
    from rpg_tabletop.ui.campaign_manager.editors.base import ResourceEditor

    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    set_current_campaign(campaign)
    (campaign.character_path / "orc.json").write_text(json.dumps({"name": "Orc", "stats": {}}), encoding="utf-8")
    ide = CampaignManagerWindow(campaign)
    runtime = SessionRuntime.start_new("Partie", campaign, entities=[])
    window = RuntimeWindow(runtime)
    ide._show_runtime_window(window)
    try:
        window.new_resource_requested.emit("entities")   # menu « Créer > Entité… »
        editor = ide.documents.currentWidget()
        assert isinstance(editor, ResourceEditor) and editor.is_new
        entity_id = str(uuid.uuid4())
        path = campaign.entity_path / "grunt.json"
        path.write_text(json.dumps({"id": entity_id, "name": "Grunt", "character": "Orc"}), encoding="utf-8")
        ide._on_resource_saved("entities", editor, str(path), "Grunt")
        assert entity_id in runtime.entities
    finally:
        window._closing = True
        window.close()
        ide.close()
