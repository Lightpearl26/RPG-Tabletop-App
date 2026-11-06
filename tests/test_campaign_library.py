import os

from conftest import make_campaign

from rpg_tabletop.assets import campaign_library as library
from rpg_tabletop.core import paths


def test_read_campaign_name_falls_back_to_folder_name(tmp_path):
    root = tmp_path / "Ma campagne"
    root.mkdir()
    (root / "campaign.json").write_text("{}", encoding="utf-8")
    assert library.read_campaign_name(root) == "Ma campagne"
    make_campaign(root, "Aventure")
    assert library.read_campaign_name(root) == "Aventure"


def test_recent_campaigns_most_recent_first_without_duplicates(tmp_path):
    a = make_campaign(tmp_path / "a")
    b = make_campaign(tmp_path / "b")
    library.remember_campaign(a)
    library.remember_campaign(b)
    library.remember_campaign(a)
    assert library.recent_campaigns() == [a, b]
    assert library.last_campaign() == a


def test_recent_campaigns_skip_missing_folders_and_are_capped(tmp_path):
    roots = [make_campaign(tmp_path / f"c{i}") for i in range(library.MAX_RECENT_CAMPAIGNS + 2)]
    for root in roots:
        library.remember_campaign(root)
    recent = library.recent_campaigns()
    assert len(recent) == library.MAX_RECENT_CAMPAIGNS
    assert recent[0] == roots[-1]

    (roots[-1] / "campaign.json").unlink()
    assert library.last_campaign() == roots[-2]

    library.forget_campaign(roots[-2])
    assert library.last_campaign() == roots[-3]


def _legacy(tmp_path, name, mtime):
    root = make_campaign(tmp_path / name / "default_campaign", name)
    (root / "characters").mkdir()
    (root / "characters" / "Alban.json").write_text("{}", encoding="utf-8")
    certs = root / ".runtime" / "certs"
    certs.mkdir(parents=True)
    (certs / "server.crt").write_text(f"CERT {name}")
    (certs / "server.key").write_text(f"KEY {name}")
    (root / ".runtime" / "token_folders.json").write_text("{}", encoding="utf-8")
    os.utime(root / "campaign.json", (mtime, mtime))
    return root


def test_migration_copies_the_most_recently_used_legacy_folder(tmp_path, monkeypatch):
    old = _legacy(tmp_path, "sources", 1_000)
    recent = _legacy(tmp_path, "dist", 2_000)
    monkeypatch.setattr(library, "legacy_default_campaigns", lambda: [old, recent])

    assert library.migrate_legacy_default_campaign() == recent

    target = paths.default_campaign_dir()
    assert library.read_campaign_name(target) == "dist"
    assert (target / "characters" / "Alban.json").is_file()
    assert (target / ".runtime" / "token_folders.json").is_file()
    # the private key does not travel with the campaign, it becomes the machine's
    assert not (target / ".runtime" / "certs").exists()
    cert, _ = paths.server_certificate_files()
    assert cert.read_text() == "CERT dist"
    # the old folders are left untouched
    assert (recent / ".runtime" / "certs" / "server.key").is_file()


def test_migration_happens_once(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path, "sources", 1_000)
    monkeypatch.setattr(library, "legacy_default_campaigns", lambda: [legacy])
    assert library.migrate_legacy_default_campaign() == legacy
    assert library.migrate_legacy_default_campaign() is None
