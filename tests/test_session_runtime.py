"""SessionRuntime smoke tests (they need PySide6, which the map package imports)."""

import pytest

pytest.importorskip("PySide6")

from rpg_tabletop.assets.campaign import Campaign  # noqa: E402
from rpg_tabletop.session.journal import Session  # noqa: E402
from rpg_tabletop.session.models import SessionEventType  # noqa: E402
from rpg_tabletop.session.runtime import SessionRuntime  # noqa: E402


@pytest.fixture
def campaign(tmp_path):
    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    return campaign


def test_every_concern_is_reachable_from_the_runtime():
    for name in ("_dispatch", "visible_tokens_for", "move_token", "add_entity",
                 "set_stat_modifier", "next_turn", "save"):
        assert callable(getattr(SessionRuntime, name))


def test_journal_round_trip(campaign):
    runtime = SessionRuntime.start_new("Partie", campaign, entities=[])
    runtime.add_note("Début de la partie")
    runtime.add_note("Fin de la partie")
    session_dir = runtime.save().parent

    session = Session.load(session_dir)
    assert session.current_seq == runtime.seq == 2
    notes = [event.message for event in session.events if event.type is SessionEventType.NOTE]
    assert notes == ["Début de la partie", "Fin de la partie"]

    reloaded = SessionRuntime.load(session, campaign)
    assert reloaded.seq == 2
    assert reloaded.entities == {}
    assert reloaded.combat is None
