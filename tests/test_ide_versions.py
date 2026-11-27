# -*- coding: utf-8 -*-
"""The IDE works on the campaign or on a session (a branch of its history)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from rpg_tabletop.assets.campaign import Campaign, set_current_campaign  # noqa: E402
from rpg_tabletop.assets.versioning import CAMPAIGN  # noqa: E402
from rpg_tabletop.ui.campaign_manager.window import CampaignManagerWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ide(app, tmp_path, monkeypatch):
    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    (campaign.item_path / "sword.json").write_text(json.dumps({"name": "Sword", "description": "", "modifier": [], "damage": 4}), encoding="utf-8")
    set_current_campaign(campaign)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"warning: {a[2]}"))
    window = CampaignManagerWindow(campaign)
    yield window
    for runtime in list(window._runtime_windows):
        runtime._closing = True
        runtime.close()
    window.close()


def _sword(ide):
    return json.loads((ide.campaign.item_path / "sword.json").read_text(encoding="utf-8"))


def _set_sword(ide, **values):
    data = _sword(ide)
    data.update(values)
    (ide.campaign.item_path / "sword.json").write_text(json.dumps(data), encoding="utf-8")


def test_sessions_are_branches_the_ide_can_switch_to(ide, monkeypatch):
    assert ide.versions.current() == CAMPAIGN
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Séance 1", True))
    ide._on_new_version_session()
    assert ide.versions.current() == "Séance 1"
    assert ide.campaign.session_path("Séance 1").is_dir()  # a session has its journal
    assert "Séance 1" in ide.windowTitle()

    _set_sword(ide, damage=9)          # edited in the session's context
    ide.versions.save_point("épée")
    ide._on_context_requested(CAMPAIGN)
    assert _sword(ide)["damage"] == 4   # the campaign did not change
    ide._on_context_requested("Séance 1")
    assert _sword(ide)["damage"] == 9


def test_pull_and_push(ide, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("S", True))
    ide._on_new_version_session()
    _set_sword(ide, damage=7)
    ide.versions.save_point()
    ide._on_context_requested(CAMPAIGN)
    _set_sword(ide, weight=3)           # the campaign changes meanwhile
    ide.versions.save_point()
    ide._on_context_requested("S")

    ide._on_pull_campaign()
    assert (_sword(ide)["damage"], _sword(ide)["weight"]) == (7, 3)

    ide._on_push_session()
    ide._on_context_requested(CAMPAIGN)
    assert (_sword(ide)["damage"], _sword(ide)["weight"]) == (7, 3)


def test_a_running_session_locks_the_context(ide, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Partie", True))
    ide._on_new_version_session()
    ide._on_context_requested(CAMPAIGN)

    ide._launch_session("Partie")        # the game opens on its own branch
    assert ide.versions.current() == "Partie"
    assert ide._running_session() == "Partie"
    assert not ide.version_bar.combo.isEnabled()
    ide._on_context_requested(CAMPAIGN)  # refused while the game runs
    assert ide.versions.current() == "Partie"


def test_go_back_in_the_history(ide, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("S", True))
    ide._on_new_version_session()
    _set_sword(ide, damage=5)
    good = ide.versions.save_point("bon état")
    _set_sword(ide, damage=0)
    ide.versions.save_point("mauvais état")
    ide._reset_to_point("S", good)
    assert _sword(ide)["damage"] == 5
