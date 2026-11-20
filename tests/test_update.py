"""In-app updater: saved choices, launcher restart and git checkouts."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rpg_tabletop.net import update


# -- settings and launcher -------------------------------------------------------

def test_skipped_values_are_remembered_per_key():
    assert update.skipped("git") == ""
    update.skip("git", "abc")
    assert update.skipped("git") == "abc"
    assert update.skipped("other") == ""


def test_updates_can_be_disabled(monkeypatch):
    assert not update.updates_disabled()
    monkeypatch.setenv(update.DISABLE_ENV, "1")
    assert update.updates_disabled()


def test_launcher_path_comes_from_the_environment(tmp_path, monkeypatch):
    assert update.launcher_path() is None
    launcher = tmp_path / "launcher.pyw"
    monkeypatch.setenv(update.LAUNCHER_ENV, str(launcher))
    assert update.launcher_path() is None  # missing file
    launcher.write_text("")
    assert update.launcher_path() == launcher


def test_restart_with_launcher_asks_for_a_check(tmp_path):
    calls = []
    launcher = tmp_path / "launcher.pyw"
    update.restart_with_launcher(launcher, "gm", popen=lambda cmd, **kw: calls.append(cmd))
    assert calls == [[sys.executable, str(launcher), "gm", "--check"]]


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
