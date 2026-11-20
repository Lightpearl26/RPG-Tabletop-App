# -*- coding: utf-8 -*-

"""
Automatic updates of the GM and player applications, without Qt.

Two ways of running the applications are supported:

* **Installed version** (PyInstaller build installed by the Inno Setup installer).
  The latest GitHub release of the repository is compared with ``__version__``.
  When it is newer, its installer for this application is downloaded, its SHA-256
  checksum is checked against the ``SHA256SUMS.txt`` file of the release, and it is
  started in silent mode. The application then quits; the installer replaces the
  files and starts the application again.

* **Source checkout** (``python -m rpg_tabletop``). The remote branch is fetched with
  git and, when it has new commits, ``git pull --ff-only`` brings them in.

The Qt dialogs that drive these steps are in ``rpg_tabletop.ui.common.update_dialog``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rpg_tabletop import __version__
from rpg_tabletop.core.paths import update_state_file

GITHUB_REPOSITORY = "Lightpearl26/RPG-Tabletop-App"
CHECKSUMS_ASSET = "SHA256SUMS.txt"

# Installer of each application, as attached to every GitHub release
GM_INSTALLER = "RPGTabletop-Setup.exe"
PLAYER_INSTALLER = "RPGTabletopPlayer-Setup.exe"

DISABLE_ENV = "RPG_TABLETOP_NO_UPDATE"
REPOSITORY_ENV = "RPG_TABLETOP_UPDATE_REPOSITORY"

# Files whose change after a git pull means the dependencies must be reinstalled
DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml")

_USER_AGENT = f"RPGTabletop/{__version__}"
_CHUNK_SIZE = 256 * 1024
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(Exception):
    """An update could not be checked, downloaded or installed."""


class UpdateCancelled(UpdateError):
    """The user cancelled a download."""


# ----------------------------------------------------------------------------
# Versions
# ----------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """
    Numeric part of a version or tag: ``"v1.2.0"`` -> ``(1, 2, 0)``.

    Anything after the numbers (``-rc1``, ``+build``) is ignored. Trailing zeros are
    removed so that ``1.2`` and ``1.2.0`` compare equal.
    """
    match = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return ()
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    """True if version `candidate` comes after version `current`."""
    new = parse_version(candidate)
    return bool(new) and new > parse_version(current)


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

def updates_disabled() -> bool:
    """Automatic checks are turned off with RPG_TABLETOP_NO_UPDATE=1."""
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def repository() -> str:
    """GitHub repository ("owner/name") that publishes the releases."""
    return os.environ.get(REPOSITORY_ENV, "").strip() or GITHUB_REPOSITORY


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
    """Version (or git commit) the user chose to skip for `key` ("gm", "player", "git")."""
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
# GitHub releases
# ----------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """File attached to a GitHub release."""

    name: str
    url: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """Latest published release, reduced to what the updater needs."""

    version: str
    tag: str
    notes: str
    page_url: str
    assets: dict[str, ReleaseAsset] = field(default_factory=dict)

    def asset(self, name: str) -> ReleaseAsset | None:
        """Asset called `name`, if the release has one."""
        return self.assets.get(name)


def parse_release(data: dict[str, Any]) -> ReleaseInfo:
    """Build a ReleaseInfo from the JSON answer of the GitHub releases API."""
    tag = str(data.get("tag_name") or "")
    assets = {}
    for item in data.get("assets") or []:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if name and url:
            assets[name] = ReleaseAsset(name, url, int(item.get("size") or 0))
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or ""),
        assets=assets,
    )


def _open(url: str, timeout: float, accept: str = "application/octet-stream"):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (https only)


def fetch_latest_release(repo: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """
    JSON description of the latest release of `repo`.

    GitHub's "latest" release ignores drafts and pre-releases, so publishing a
    pre-release is a way to test a version without offering it to everyone.
    """
    url = f"https://api.github.com/repos/{repo or repository()}/releases/latest"
    try:
        with _open(url, timeout, accept="application/vnd.github+json") as answer:
            return json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("Aucune version publiée sur GitHub.") from error
        raise UpdateError(f"GitHub a répondu {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateError(f"Impossible de joindre GitHub : {error}") from error


def check_for_update(
    asset_name: str,
    current: str = __version__,
    *,
    timeout: float = 5.0,
    fetch: Callable[..., dict[str, Any]] = fetch_latest_release,
) -> ReleaseInfo | None:
    """
    Latest release if it is newer than `current` and ships `asset_name`.

    Raises:
        UpdateError: GitHub could not be reached or gave an unexpected answer.
    """
    release = parse_release(fetch(timeout=timeout))
    if not is_newer(release.version, current):
        return None
    if release.asset(asset_name) is None:
        return None
    return release


# ----------------------------------------------------------------------------
# Download and checksum
# ----------------------------------------------------------------------------

def expected_checksum(checksums: str, name: str) -> str | None:
    """SHA-256 of `name` in a ``sha256sum``-style file (``<hash>  <name>`` lines)."""
    for line in checksums.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].lstrip("*").strip() == name:
            return parts[0].lower()
    return None


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, as lowercase hexadecimal."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 30.0,
) -> Path:
    """
    Download `url` to `destination`, reporting ``progress(received, total)``.

    The file is written next to `destination` and renamed at the end, so an
    interrupted download never leaves a partial file under the final name.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        with _open(url, timeout) as answer, open(partial, "wb") as stream:
            total = int(answer.headers.get("Content-Length") or 0)
            received = 0
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateCancelled("Téléchargement annulé.")
                chunk = answer.read(_CHUNK_SIZE)
                if not chunk:
                    break
                stream.write(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, total)
        os.replace(partial, destination)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Échec du téléchargement : {error}") from error
    return destination


def download_dir() -> Path:
    """Temporary folder of the downloaded installers."""
    return Path(tempfile.gettempdir()) / "RPGTabletop-update"


def download_installer(
    release: ReleaseInfo,
    asset_name: str,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    directory: Path | None = None,
) -> Path:
    """
    Download the installer `asset_name` of `release` and check its SHA-256.

    The release must carry a SHA256SUMS.txt file listing the installer; an
    installer that cannot be checked is never started.
    """
    asset = release.asset(asset_name)
    sums = release.asset(CHECKSUMS_ASSET)
    if asset is None:
        raise UpdateError(f"La version {release.version} ne contient pas {asset_name}.")
    if sums is None:
        raise UpdateError(f"La version {release.version} ne contient pas {CHECKSUMS_ASSET}.")

    try:
        with _open(sums.url, 15.0) as answer:
            checksums = answer.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"Impossible de lire {CHECKSUMS_ASSET} : {error}") from error
    expected = expected_checksum(checksums, asset_name)
    if expected is None:
        raise UpdateError(f"{asset_name} n'est pas listé dans {CHECKSUMS_ASSET}.")

    directory = Path(directory or download_dir())
    target = download(
        asset.url, directory / f"{Path(asset_name).stem}-{release.version}.exe", progress, cancelled
    )
    if sha256_of(target) != expected:
        target.unlink(missing_ok=True)
        raise UpdateError("Le fichier téléchargé est corrompu (somme SHA-256 différente).")
    return target


# ----------------------------------------------------------------------------
# Running application
# ----------------------------------------------------------------------------

def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than from the sources."""
    return bool(getattr(sys, "frozen", False))


def installer_command(installer: Path) -> list[str]:
    """
    Command that installs the update silently and starts the application again.

    /SILENT shows only a progress window. /RELAUNCH=1 is read by installer.iss to
    start the application once the files are replaced.
    """
    return [
        str(installer),
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RELAUNCH=1",
    ]


def start_installer(installer: Path, popen: Callable[..., Any] = subprocess.Popen) -> None:
    """
    Start the installer in its own process; the caller must then quit.

    Raises:
        UpdateError: the installer could not be started.
    """
    try:
        popen(installer_command(installer), close_fds=True)
    except OSError as error:
        raise UpdateError(f"Impossible de lancer l'installeur : {error}") from error


def cleanup_downloads(directory: Path | None = None) -> None:
    """Remove the installers left by previous updates."""
    directory = Path(directory or download_dir())
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        try:
            path.unlink()
        except OSError:
            pass  # still in use by a running installer


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
    if is_frozen():
        return None
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
