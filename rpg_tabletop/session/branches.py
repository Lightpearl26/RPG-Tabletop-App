# -*- coding: utf-8 -*-
"""
Sessions are the branches of the campaign history (assets.versioning).

A session's journal (sessions/<name>/session.json) records what happened in
play; its branch holds its version of the assets (entities, items, spells,
characters, maps). The runtime of a session works on the campaign folder
while that folder is checked out on the session's branch.

Each session save records a point whose `meta.session_seq` is the journal
position: going back to a point can cut the journal to the same moment, and a
session started from an old journal position starts from the matching assets.
"""
from __future__ import annotations

from typing import Optional

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.versioning import CAMPAIGN, Changes, VersionStore


def versions(campaign: Campaign) -> VersionStore:
    """The history of the campaign, created with a first point if it has none."""
    store = VersionStore(campaign.root)
    store.init()
    return store


def ensure_branch(campaign: Campaign, name: str, *, source: Optional[str] = None) -> VersionStore:
    """
    Make sure the session has its branch (sessions created before the history
    start from the campaign).
    """
    store = versions(campaign)
    if not store.has_session(name):
        store.create_session(name, source=CAMPAIGN if source is None else source)
    return store


def checkout(campaign: Campaign, name: str) -> Changes:
    """Put the campaign folder on the session's branch (saving the current files first)."""
    return ensure_branch(campaign, name).switch(name)


def point_at(store: VersionStore, session: str, seq: int) -> Optional[str]:
    """The latest point of `session` saved at journal position `seq` or before."""
    for point in store.history(session, limit=100000):
        recorded = point.meta.get("session_seq")
        if isinstance(recorded, int) and recorded <= seq:
            return point.id
    return None


def record_save(campaign: Campaign, name: str, seq: int) -> Optional[str]:
    """After a session save: a point of its branch at this journal position."""
    store = versions(campaign)
    if store.current() != name:
        return None  # files not on this session's branch: nothing of it to record
    return store.save_point(f"Sauvegarde de la session (événement #{seq})",
                            meta={"session_seq": int(seq)}, force=True)
