# -*- coding: utf-8 -*-

"""
Updates seen from inside the applications, without Qt.

* **Installed version.** The code is kept up to date by the launcher
  (packaging/launcher.py), before the application starts: it compares
  ``__version__`` with the one on the main branch of GitHub. The application only
  needs to know how to start the launcher again (*Help > Check for Updates*); the
  launcher gives its path in the RPG_TABLETOP_LAUNCHER environment variable.

* **Source checkout** (``python -m rpg_tabletop``). The remote branch is fetched with
  git and, when it has new commits, ``git pull --ff-only`` brings them in.

The Qt dialogs are in ``rpg_tabletop.ui.common.update_dialog``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rpg_tabletop.core.paths import update_state_file

DISABLE_ENV = "RPG_TABLETOP_NO_UPDATE"
LAUNCHER_ENV = "RPG_TABLETOP_LAUNCHER"

# Files whose change after a git pull means the dependencies must be reinstalled
DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(Exception):
    """An update could not be checked or installed."""


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

def updates_disabled() -> bool:
    """Automatic checks are turned off with RPG_TABLETOP_NO_UPDATE=1."""
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def load_state() -> dict[str, Any]:
    """Saved update choices, such as the version the user chose to skip."""
    try:
        data = json.loads(update_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    """Save the update choices; failing to write them is not an error."""
    path = update_state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def skipped(key: str) -> str:
    """Value (git commit) the user chose to skip for `key`."""
    value = load_state().get("skipped", {}).get(key, "")
    return value if isinstance(value, str) else ""


def skip(key: str, value: str) -> None:
    """Do not offer `value` again for `key`."""
    state = load_state()
    skipped_map = state.get("skipped")
    if not isinstance(skipped_map, dict):
        skipped_map = {}
    skipped_map[key] = value
    state["skipped"] = skipped_map
    save_state(state)


# ----------------------------------------------------------------------------
# Installed version: launcher
# ----------------------------------------------------------------------------

def launcher_path() -> Path | None:
    """Launcher that started this application, if any."""
    value = os.environ.get(LAUNCHER_ENV, "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def launcher_command(launcher: Path, app_key: str) -> list[str]:
    """Start the launcher again, asking it to check for an update now."""
    return [sys.executable, str(launcher), app_key, "--check"]


def restart_with_launcher(
    launcher: Path, app_key: str, popen: Callable[..., Any] = subprocess.Popen
) -> None:
    """Start the launcher; the caller must then quit."""
    try:
        popen(launcher_command(launcher, app_key), cwd=str(launcher.parent), close_fds=True)
    except OSError as error:
        raise UpdateError(f"Impossible de relancer le lanceur : {error}") from error


# ----------------------------------------------------------------------------
# Source checkout (git)
# ----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GitStatus:
    """Position of the local checkout relative to its remote branch."""

    root: Path
    upstream: str
    remote_head: str
    behind: int
    ahead: int
    dirty: bool
    log: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    @property
    def can_fast_forward(self) -> bool:
        """New commits can be brought in without a merge."""
        return self.behind > 0 and self.ahead == 0

    @property
    def dependencies_changed(self) -> bool:
        """The new commits change the dependencies (pip install needed)."""
        return any(Path(name).name in DEPENDENCY_FILES for name in self.changed_files)


def source_root() -> Path | None:
    """Root of the git checkout the code runs from, or None."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def _git(root: Path, *args: str, timeout: float = 20.0, run=subprocess.run) -> str:
    try:
        result = run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError(f"git {args[0]} : {error}") from error
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise UpdateError(f"git {args[0]} : {message}")
    return result.stdout.strip()


def check_git_updates(root: Path, *, timeout: float = 15.0, run=subprocess.run) -> GitStatus | None:
    """
    Fetch the remote branch and compare it with the local one.

    Returns None when the current branch has no remote branch.

    Raises:
        UpdateError: git is missing or the remote could not be reached.
    """
    try:
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", run=run)
    except UpdateError:
        return None
    _git(root, "fetch", "--quiet", timeout=timeout, run=run)
    ahead, behind = (int(n) for n in _git(
        root, "rev-list", "--left-right", "--count", "HEAD...@{u}", run=run
    ).split())
    remote_head = _git(root, "rev-parse", "@{u}", run=run)
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no", run=run))
    log, changed = [], []
    if behind:
        log = _git(root, "log", "--oneline", "--no-decorate", "-n", "30", "HEAD..@{u}", run=run).splitlines()
        changed = _git(root, "diff", "--name-only", "HEAD...@{u}", run=run).splitlines()
    return GitStatus(root, upstream, remote_head, behind, ahead, dirty, log, changed)


def git_pull(root: Path, run=subprocess.run) -> str:
    """Bring in the remote commits without merging (fails if the branches diverged)."""
    return _git(root, "pull", "--ff-only", timeout=120.0, run=run)


def can_restart_from_source() -> bool:
    """False under a debugger, which a plain restart would leave behind."""
    return sys.gettrace() is None and "debugpy" not in sys.modules


def restart_command() -> list[str]:
    """Command line that starts this Python application again."""
    return [sys.executable, *sys.orig_argv[1:]]


def restart_from_source(popen: Callable[..., Any] = subprocess.Popen) -> None:
    """Start the application again from the sources; the caller must then quit."""
    try:
        popen(restart_command(), close_fds=True)
    except OSError as error:
        raise UpdateError(f"Impossible de redémarrer : {error}") from error
