import json

from conftest import make_campaign

from rpg_tabletop.assets import campaign as campaign_module
from rpg_tabletop.assets.campaign import (
    Campaign,
    get_current_campaign,
    set_current_campaign,
    startup_campaign,
)
from rpg_tabletop.assets.campaign_library import last_campaign
from rpg_tabletop.core.paths import default_campaign_dir


def test_first_start_uses_the_default_campaign():
    campaign = get_current_campaign()
    assert campaign.root == default_campaign_dir()
    assert campaign.name == campaign_module.DEFAULT_CAMPAIGN_NAME
    assert (campaign.root / "characters").is_dir()


def test_campaign_json_no_longer_stores_the_folder(tmp_path):
    root = tmp_path / "Somewhere" / "Aventure"
    root.mkdir(parents=True)
    (root / "campaign.json").write_text(
        json.dumps({"name": "Old", "root": "A:\\old\\path", "custom": 1}), encoding="utf-8"
    )
    set_current_campaign(Campaign("Aventure", root))
    data = json.loads((root / "campaign.json").read_text(encoding="utf-8"))
    assert data == {"name": "Aventure", "custom": 1}


def test_startup_reopens_the_last_campaign(tmp_path):
    root = make_campaign(tmp_path / "Clé USB" / "Aventure", "Aventure")
    set_current_campaign(Campaign("Aventure", root))
    assert last_campaign() == root

    campaign_module._CURRENT_CAMPAIGN = None
    campaign = startup_campaign()
    assert (campaign.root, campaign.name) == (root, "Aventure")


def test_startup_falls_back_to_the_default_campaign_when_the_last_one_is_gone(tmp_path):
    root = make_campaign(tmp_path / "usb" / "Aventure", "Aventure")
    set_current_campaign(Campaign("Aventure", root))
    (root / "campaign.json").unlink()

    assert startup_campaign().root == default_campaign_dir()


def test_a_failed_migration_does_not_prevent_starting(monkeypatch):
    def fail():
        raise PermissionError("disk full")

    monkeypatch.setattr(campaign_module, "migrate_legacy_default_campaign", fail)
    assert startup_campaign().root == default_campaign_dir()
