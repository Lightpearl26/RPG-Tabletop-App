"""Shared fixtures: every test gets its own user data folder and working folder."""

from __future__ import annotations

import pytest

from rpg_tabletop.assets import campaign as campaign_module
from rpg_tabletop.assets import campaign_library


@pytest.fixture(autouse=True)
def user_data(tmp_path, monkeypatch):
    """Per-test user data folder; no legacy folder is found unless a test says so."""
    data = tmp_path / "user-data"
    monkeypatch.setenv("RPG_TABLETOP_DATA_DIR", str(data))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(campaign_module, "_CURRENT_CAMPAIGN", None)
    monkeypatch.setattr(campaign_library, "legacy_default_campaigns", lambda: [])
    return data


def make_campaign(root, name="Campaign"):
    """Create a minimal campaign folder."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "campaign.json").write_text(f'{{"name": "{name}"}}', encoding="utf-8")
    return root
