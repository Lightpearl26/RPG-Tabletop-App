# -*- coding: utf-8 -*-
"""Campaign history: sessions as branches, points, send/take/merge, going back."""

from __future__ import annotations

import json

import pytest

from rpg_tabletop.assets.versioning import CAMPAIGN, VersionStore, VersioningError, merge_json


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    else:
        path.write_bytes(data)


def _read(root, rel):
    return json.loads((root / rel).read_text(encoding="utf-8"))


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "campaign"
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 10, "level": 1, "items": ["sword"]})
    _write(root, "items/sword.json", {"name": "Sword", "damage": 4})
    _write(root, "maps/forest/map.json", {"name": "Forest"})
    _write(root, "maps/forest/tiles/0_0", b"tile-a")
    _write(root, "sessions/s1/session.json", {"journal": True})  # never versioned
    versions = VersionStore(root)
    assert versions.init()
    return versions


def test_switching_rewrites_the_files_and_keeps_both_versions(store):
    root = store.root
    store.create_session("Séance 1", switch=True)
    assert store.current() == "Séance 1"
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 3, "level": 1, "items": ["sword"]})
    _write(root, "items/potion.json", {"name": "Potion"})

    store.switch(CAMPAIGN)
    assert _read(root, "entities/hero.json")["hp"] == 10
    assert not (root / "items/potion.json").exists()
    assert (root / "sessions/s1/session.json").exists()  # journals are not touched

    store.switch("Séance 1")
    assert _read(root, "entities/hero.json")["hp"] == 3
    assert (root / "items/potion.json").exists()


def test_push_makes_the_campaign_exactly_the_session(store):
    root = store.root
    store.create_session("S", switch=True)
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 7, "level": 2, "items": []})
    (root / "items/sword.json").unlink()
    store.save_point("fin de séance")
    store.switch(CAMPAIGN)
    store.push("S")
    assert _read(root, "entities/hero.json") == {"name": "Hero", "hp": 7, "level": 2, "items": []}
    assert not (root / "items/sword.json").exists()


def test_pull_keeps_the_session_changes_and_the_campaign_wins_conflicts(store):
    root = store.root
    store.create_session("S", switch=True)
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 4, "level": 1, "items": ["sword", "rope"]})
    store.switch(CAMPAIGN)
    # The GM edits the campaign meanwhile: level changed, and items too (a conflict)
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 10, "level": 3, "items": ["axe"]})
    _write(root, "items/sword.json", {"name": "Sword", "damage": 6})
    store.save_point("campagne modifiée")
    store.switch("S")

    changes, conflicts = store.pull("S")
    hero = _read(root, "entities/hero.json")
    assert hero["hp"] == 4          # changed in the session only: kept
    assert hero["level"] == 3       # changed in the campaign only: taken
    assert hero["items"] == ["axe"]  # changed on both sides: the campaign wins
    assert _read(root, "items/sword.json")["damage"] == 6
    assert [c.fields for c in conflicts] == [["items"]]
    assert ("entities", "hero") in changes.resources()


def test_merge_between_sessions_asks_for_each_conflicting_asset(store):
    root = store.root
    store.create_session("A", switch=True)
    _write(root, "maps/forest/map.json", {"name": "Forest A"})
    _write(root, "items/sword.json", {"name": "Sword", "damage": 4, "weight": 3})
    store.create_session("B", source=CAMPAIGN, switch=True)
    _write(root, "maps/forest/map.json", {"name": "Forest B"})
    _write(root, "maps/forest/tiles/0_0", b"tile-b")

    asked = []

    def resolve(conflicts):
        asked.extend(c.asset for c in conflicts)
        return {"maps/forest": "theirs"}

    store.merge("A", "B", resolve)
    assert asked == ["maps/forest"]  # a map is one asset: map.json and tiles together
    assert _read(root, "maps/forest/map.json")["name"] == "Forest A"
    assert (root / "maps/forest/tiles/0_0").read_bytes() == b"tile-a"
    assert _read(root, "items/sword.json")["weight"] == 3  # no conflict: merged

    changes, conflicts = store.merge("A", "B", resolve)  # already merged: nothing to do
    assert not changes.paths and not conflicts

    store.switch("A")
    _write(root, "maps/forest/map.json", {"name": "Forest A2"})
    store.switch("B")
    _write(root, "maps/forest/map.json", {"name": "Forest B2"})
    with pytest.raises(VersioningError):
        store.merge("A", "B", lambda conflicts: None)  # cancelled by the user
    assert _read(root, "maps/forest/map.json")["name"] == "Forest B2"


def test_go_back_to_a_point_or_start_a_session_from_it(store):
    root = store.root
    store.create_session("S", switch=True)
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 8, "level": 1, "items": []})
    good = store.save_point("avant le combat")
    _write(root, "entities/hero.json", {"name": "Hero", "hp": 0, "level": 1, "items": []})
    store.save_point("après le combat")

    store.create_session("S bis", point=good)
    store.reset("S", good)
    assert _read(root, "entities/hero.json")["hp"] == 8
    assert [point.message for point in store.history("S")][0] == "avant le combat"
    store.switch("S bis")
    assert _read(root, "entities/hero.json")["hp"] == 8


def test_unchanged_files_are_not_read_again_and_nothing_new_makes_no_point(store):
    assert store.save_point("rien") is None
    _write(store.root, "items/sword.json", {"name": "Sword", "damage": 5})
    assert store.save_point("épée") is not None


def test_json_merge_field_by_field():
    base = {"a": 1, "b": {"x": 1, "y": 1}}
    ours = {"a": 2, "b": {"x": 1, "y": 5}}
    theirs = {"a": 1, "b": {"x": 3, "y": 6}, "c": True}
    merged, conflicts = merge_json(base, ours, theirs)
    assert merged == {"a": 2, "b": {"x": 3, "y": 5}, "c": True} and conflicts == ["b.y"]
    merged, _ = merge_json(base, ours, theirs, prefer="theirs")
    assert merged["b"]["y"] == 6


def test_a_session_opened_after_taking_the_campaign_gets_the_new_definitions(tmp_path):
    pytest.importorskip("PySide6")
    import uuid

    from rpg_tabletop.assets.campaign import Campaign, set_current_campaign
    from rpg_tabletop.session import branches, store as session_store
    from rpg_tabletop.session.journal import Session
    from rpg_tabletop.session.runtime import SessionRuntime

    campaign = Campaign("T", tmp_path / "c")
    campaign.ensure_directories()
    set_current_campaign(campaign)
    hero_id = str(uuid.uuid4())
    _write(campaign.root, "characters/hero.json", {"name": "Hero", "stats": {"str": 10}})
    _write(campaign.root, "entities/hero.json",
           {"id": hero_id, "name": "Hero", "character": "Hero", "stats_modifier": {}, "items": [], "spells": []})
    session_store.create(campaign, "S", entities=["hero"], map_id=None)
    versions = branches.versions(campaign)
    assert versions.has_session("S")

    # Played: the session's entity gets a modifier (sheet edit written to its branch)
    branches.checkout(campaign, "S")
    entity = _read(campaign.root, "entities/hero.json")
    entity["stats_modifier"] = {"str": 2}
    _write(campaign.root, "entities/hero.json", entity)
    versions.save_point("partie")

    # Meanwhile the GM improves the character in the campaign
    versions.switch(CAMPAIGN)
    _write(campaign.root, "characters/hero.json", {"name": "Hero", "stats": {"str": 15}})
    versions.save_point("perso amélioré")

    versions.switch("S")
    versions.pull("S")
    runtime = SessionRuntime.load(Session.load(campaign.session_path("S")), campaign)
    for kind, folder in (("character", "characters"), ("entity", "entities")):
        runtime.sync_asset(kind, "hero")
    hero = runtime.entities[hero_id]
    assert hero.character.stats.str == 15          # from the campaign
    assert hero.stats_modifier.str == 2            # from the session
