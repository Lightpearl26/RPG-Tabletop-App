"""Updater: versions, GitHub release parsing, checked downloads and git checkouts."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from rpg_tabletop.net import update


# -- versions ------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("0.1.0", (0, 1)),
        ("v1.2.3", (1, 2, 3)),
        ("V2", (2,)),
        ("1.10.0-rc1", (1, 10)),
        ("garbage", ()),
        ("", ()),
    ],
)
def test_parse_version(text, expected):
    assert update.parse_version(text) == expected


def test_is_newer():
    assert update.is_newer("0.2.0", "0.1.9")
    assert update.is_newer("v1.10", "1.9.9")
    assert not update.is_newer("1.0", "1.0.0")
    assert not update.is_newer("0.9", "1.0")
    assert not update.is_newer("nightly", "0.1")


# -- releases ------------------------------------------------------------------

def _release_json(tag="v9.0.0", assets=(update.GM_INSTALLER, update.CHECKSUMS_ASSET), base="https://x"):
    return {
        "tag_name": tag,
        "body": "## Nouveautés",
        "html_url": "https://github.com/o/r/releases/tag/" + tag,
        "assets": [
            {"name": name, "browser_download_url": f"{base}/{name}", "size": 10} for name in assets
        ],
    }


def test_parse_release():
    release = update.parse_release(_release_json())
    assert release.version == "9.0.0" and release.tag == "v9.0.0"
    assert release.notes == "## Nouveautés"
    assert release.asset(update.GM_INSTALLER).url == "https://x/" + update.GM_INSTALLER
    assert release.asset("missing.exe") is None


def test_check_for_update_offers_newer_release_with_the_right_installer():
    fetch = lambda timeout: _release_json()  # noqa: E731
    assert update.check_for_update(update.GM_INSTALLER, "0.1.0", fetch=fetch).version == "9.0.0"
    # same version, or no installer for this application: nothing to offer
    assert update.check_for_update(update.GM_INSTALLER, "9.0", fetch=fetch) is None
    assert update.check_for_update(update.PLAYER_INSTALLER, "0.1.0", fetch=fetch) is None


def test_skipped_versions_are_remembered_per_application():
    assert update.skipped("gm") == ""
    update.skip("gm", "9.0.0")
    update.skip("git", "abc")
    assert update.skipped("gm") == "9.0.0"
    assert update.skipped("player") == ""
    assert update.skipped("git") == "abc"


def test_updates_can_be_disabled(monkeypatch):
    assert not update.updates_disabled()
    monkeypatch.setenv(update.DISABLE_ENV, "1")
    assert update.updates_disabled()


# -- download and checksum -----------------------------------------------------

def test_expected_checksum():
    sums = "aa11  RPGTabletop-Setup.exe\r\nBB22 *RPGTabletopPlayer-Setup.exe\n"
    assert update.expected_checksum(sums, "RPGTabletop-Setup.exe") == "aa11"
    assert update.expected_checksum(sums, "RPGTabletopPlayer-Setup.exe") == "bb22"
    assert update.expected_checksum(sums, "other.exe") is None


def _published_release(tmp_path, content=b"installer", listed_hash=None):
    """A release whose files are served from a local folder (file:// URLs)."""
    server = tmp_path / "server"
    server.mkdir()
    (server / update.GM_INSTALLER).write_bytes(content)
    digest = listed_hash or hashlib.sha256(content).hexdigest()
    (server / update.CHECKSUMS_ASSET).write_text(f"{digest}  {update.GM_INSTALLER}\n")
    return update.parse_release(_release_json(base=server.as_uri()))


def test_download_installer_checks_the_checksum(tmp_path):
    release = _published_release(tmp_path, b"x" * 600_000)
    seen = []
    path = update.download_installer(
        release, update.GM_INSTALLER, progress=lambda done, total: seen.append((done, total)),
        directory=tmp_path / "downloads",
    )
    assert path.read_bytes() == b"x" * 600_000
    assert seen[-1] == (600_000, 600_000)
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no partial file left


def test_corrupted_download_is_deleted(tmp_path):
    release = _published_release(tmp_path, listed_hash="0" * 64)
    with pytest.raises(update.UpdateError, match="SHA-256"):
        update.download_installer(release, update.GM_INSTALLER, directory=tmp_path / "downloads")
    assert not any((tmp_path / "downloads").iterdir())


def test_installer_without_checksum_is_refused(tmp_path):
    release = update.parse_release(_release_json(assets=(update.GM_INSTALLER,)))
    with pytest.raises(update.UpdateError, match=update.CHECKSUMS_ASSET):
        update.download_installer(release, update.GM_INSTALLER, directory=tmp_path)


def test_download_can_be_cancelled(tmp_path):
    release = _published_release(tmp_path)
    with pytest.raises(update.UpdateCancelled):
        update.download_installer(
            release, update.GM_INSTALLER, cancelled=lambda: True, directory=tmp_path / "d"
        )
    assert not any((tmp_path / "d").iterdir())


def test_installer_is_started_silently_with_relaunch(tmp_path):
    calls = []
    update.start_installer(tmp_path / "setup.exe", popen=lambda cmd, **kw: calls.append(cmd))
    assert calls == [[str(tmp_path / "setup.exe"), "/SILENT", "/SUPPRESSMSGBOXES",
                      "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]]


# -- git checkout --------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def checkouts(tmp_path):
    """A bare remote, a local clone and a second clone used to push new commits."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote, local, other = tmp_path / "remote.git", tmp_path / "local", tmp_path / "other"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", str(remote), str(other))
    for repo in (other,):
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
    (other / "a.txt").write_text("1")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "first")
    _git(other, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(remote), str(local))
    return local, other


def _push(other: Path, name: str, message: str):
    (other / name).write_text(message)
    _git(other, "add", ".")
    _git(other, "commit", "-m", message)
    _git(other, "push", "origin", "HEAD:main")


def test_git_checkout_up_to_date(checkouts):
    local, _ = checkouts
    status = update.check_git_updates(local)
    assert status.behind == 0 and not status.dirty


def test_git_checkout_behind_then_pulled(checkouts):
    local, other = checkouts
    _push(other, "b.txt", "second")
    _push(other, "requirements.txt", "deps")

    status = update.check_git_updates(local)
    assert status.upstream == "origin/main"
    assert (status.behind, status.ahead) == (2, 0) and status.can_fast_forward
    assert len(status.log) == 2 and status.log[0].endswith("deps")
    assert status.dependencies_changed

    update.git_pull(local)
    assert (local / "b.txt").read_text() == "second"
    assert update.check_git_updates(local).behind == 0


def test_git_branch_without_remote(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    _git(tmp_path, "init")
    assert update.check_git_updates(tmp_path) is None
