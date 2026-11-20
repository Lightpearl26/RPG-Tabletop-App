# -*- coding: utf-8 -*-

"""
Launcher of the installed RPG Tabletop applications.

The installer (packaging/installer.iss) lays out:

    <install dir>/                 %LOCALAPPDATA%\\Programs\\RPG Tabletop
    ├── launcher.pyw               this file
    ├── python/                    embedded Python with every dependency
    └── app/                       the code: rpg_tabletop/, requirements.txt, launcher.py

The shortcuts run ``python\\pythonw.exe launcher.pyw gm`` (or ``player``). Before
starting the application, the launcher reads ``__version__`` on the main branch of the
GitHub repository. When it differs from the installed one it offers the update: the
code of the branch is downloaded and replaces app/, then the application starts.
Pushing a new ``__version__`` on main is therefore enough to update every player.

When requirements.txt changes, the new dependencies are installed with pip in a
console window, after the launcher has quit (it holds the Qt libraries pip may
have to replace).

Whatever goes wrong while checking or updating, the installed version still starts.

This file only uses the standard library and PySide6, never the application code,
so that a broken push cannot break the updater. A copy of it travels with the code
(packaging/launcher.py) and replaces launcher.pyw after each update.

Usage:
    pythonw launcher.pyw gm|player [--check]
    python launcher.pyw player --finish-update --wait-pid PID      (internal)

Environment:
    RPG_TABLETOP_NO_UPDATE=1           start without checking
    RPG_TABLETOP_UPDATE_BRANCH=dev     follow another branch (tests)
    RPG_TABLETOP_UPDATE_REPOSITORY     follow another repository ("owner/name")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPOSITORY = "Lightpearl26/RPG-Tabletop-App"
BRANCH = "main"

DISABLE_ENV = "RPG_TABLETOP_NO_UPDATE"
REPOSITORY_ENV = "RPG_TABLETOP_UPDATE_REPOSITORY"
BRANCH_ENV = "RPG_TABLETOP_UPDATE_BRANCH"
DATA_DIR_ENV = "RPG_TABLETOP_DATA_DIR"
# Given to the application: path of this launcher (Help > Check for Updates uses it)
LAUNCHER_ENV = "RPG_TABLETOP_LAUNCHER"

APPS = {
    "gm": ("rpg_tabletop.apps.gm", "RPG Tabletop"),
    "player": ("rpg_tabletop.apps.player", "RPG Tabletop Player"),
}

# Repository paths copied into app/ ("dir/" for a whole folder)
INCLUDED = ("rpg_tabletop/", "requirements.txt", "packaging/launcher.py")
# Where they go in app/, when not at the same place
RENAMED = {"packaging/launcher.py": "launcher.py"}

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)
_USER_AGENT = "RPGTabletop-launcher"
_CHUNK_SIZE = 256 * 1024
_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


class UpdateError(Exception):
    """The update could not be checked, downloaded or installed."""


class UpdateCancelled(UpdateError):
    """The user cancelled the download."""


# ----------------------------------------------------------------------------
# Installation layout
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Layout:
    """Folders and programs of one installation."""

    root: Path

    @classmethod
    def here(cls) -> "Layout":
        return cls(Path(__file__).resolve().parent)

    @property
    def app_dir(self) -> Path:
        return self.root / "app"

    @property
    def new_dir(self) -> Path:
        return self.root / "app.new"

    @property
    def old_dir(self) -> Path:
        return self.root / "app.old"

    @property
    def launcher(self) -> Path:
        return self.root / "launcher.pyw"

    @property
    def python(self) -> Path:
        """Console Python (pip, end of update)."""
        return self.root / "python" / "python.exe"

    @property
    def pythonw(self) -> Path:
        """Python without console window (applications)."""
        return self.root / "python" / "pythonw.exe"


def repository() -> str:
    return os.environ.get(REPOSITORY_ENV, "").strip() or REPOSITORY


def branch() -> str:
    return os.environ.get(BRANCH_ENV, "").strip() or BRANCH


def version_url() -> str:
    return f"https://raw.githubusercontent.com/{repository()}/{branch()}/rpg_tabletop/__init__.py"


def archive_url() -> str:
    return f"https://codeload.github.com/{repository()}/zip/refs/heads/{branch()}"


def changes_url() -> str:
    return f"https://github.com/{repository()}/commits/{branch()}"


def updates_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# ----------------------------------------------------------------------------
# Versions and saved choices
# ----------------------------------------------------------------------------

def read_version(text: str) -> str | None:
    """Value of ``__version__ = "..."`` in the text of a module."""
    match = _VERSION_RE.search(text or "")
    return match.group(1).strip() if match else None


def installed_version(app_dir: Path) -> str | None:
    """Version of the code in `app_dir`, or None if it is missing."""
    try:
        text = (Path(app_dir) / "rpg_tabletop" / "__init__.py").read_text(encoding="utf-8-sig")
    except OSError:
        return None
    return read_version(text)


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (https only)


def fetch_remote_version(timeout: float = 4.0) -> str:
    """``__version__`` on the followed branch."""
    try:
        with _open(version_url(), timeout) as answer:
            text = answer.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as error:
        raise UpdateError(f"GitHub a répondu {error.code} ({version_url()}).") from error
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"Impossible de joindre GitHub : {error}") from error
    version = read_version(text)
    if not version:
        raise UpdateError("Pas de __version__ dans rpg_tabletop/__init__.py sur GitHub.")
    return version


def data_dir() -> Path:
    """Per-user data folder of the applications (same as rpg_tabletop.core.paths)."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(base) / "RPGTabletop"


def _state_file() -> Path:
    return data_dir() / "update.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def skipped_version() -> str:
    """Version the user chose not to install."""
    value = _load_state().get("skipped", {}).get("launcher", "")
    return value if isinstance(value, str) else ""


def skip_version(version: str) -> None:
    state = _load_state()
    skipped = state.get("skipped") if isinstance(state.get("skipped"), dict) else {}
    skipped["launcher"] = version
    state["skipped"] = skipped
    try:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def log_error(message: str) -> None:
    """Append to launcher.log in the data folder (pythonw has no console)."""
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        with open(data_dir() / "launcher.log", "a", encoding="utf-8") as stream:
            stream.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{message}\n")
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Download and installation of the code
# ----------------------------------------------------------------------------

def download(
    url: str,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download `url` to `destination`; no partial file is left on failure."""
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


def _wanted(relative: str) -> bool:
    if "__pycache__" in relative.split("/"):
        return False
    return any(
        relative == item or (item.endswith("/") and relative.startswith(item)) for item in INCLUDED
    )


def extract_update(archive: Path, destination: Path) -> str:
    """
    Unpack the code of a GitHub branch archive into `destination`.

    GitHub archives hold one top folder ("<repo>-<branch>/"); only the paths listed
    in INCLUDED are kept.

    Returns:
        The version of the extracted code.
    """
    destination = Path(destination)
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir() or "/" not in info.filename:
                    continue
                relative = info.filename.split("/", 1)[1]
                if not _wanted(relative):
                    continue
                target = (destination / RENAMED.get(relative, relative)).resolve()
                if root not in target.parents:
                    raise UpdateError(f"Chemin refusé dans l'archive : {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
    except (zipfile.BadZipFile, OSError) as error:
        raise UpdateError(f"Archive illisible : {error}") from error
    version = installed_version(destination)
    if not version:
        raise UpdateError("L'archive ne contient pas rpg_tabletop/__init__.py avec une version.")
    return version


def _requirements(app_dir: Path) -> list[str]:
    try:
        text = (Path(app_dir) / "requirements.txt").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    lines = (line.split("#", 1)[0].strip().lower() for line in text.splitlines())
    return sorted(line.replace(" ", "") for line in lines if line)


def requirements_changed(old_app: Path, new_app: Path) -> bool:
    """True when pip has to run before the new code can start."""
    return _requirements(old_app) != _requirements(new_app)


def swap_in(layout: Layout) -> None:
    """
    Replace app/ with app.new/ and refresh launcher.pyw.

    Raises:
        UpdateError: a file of app/ is in use; app/ is left as it was.
    """
    shutil.rmtree(layout.old_dir, ignore_errors=True)
    try:
        if layout.app_dir.exists():
            os.replace(layout.app_dir, layout.old_dir)
        os.replace(layout.new_dir, layout.app_dir)
    except OSError as error:
        if layout.old_dir.exists() and not layout.app_dir.exists():
            os.replace(layout.old_dir, layout.app_dir)
        raise UpdateError(
            "Impossible de remplacer le code. Fermez les autres fenêtres RPG Tabletop "
            f"puis relancez. ({error})"
        ) from error
    shutil.rmtree(layout.old_dir, ignore_errors=True)
    new_launcher = layout.app_dir / "launcher.py"
    if new_launcher.is_file():
        try:
            shutil.copyfile(new_launcher, layout.launcher)
        except OSError:
            pass  # the next update will try again


def pip_command(layout: Layout) -> list[str]:
    return [
        str(layout.python), "-m", "pip", "install",
        "-r", str(layout.new_dir / "requirements.txt"),
        "--disable-pip-version-check", "--no-warn-script-location",
    ]


def app_command(layout: Layout, app_key: str) -> list[str]:
    return [str(layout.pythonw), "-m", APPS[app_key][0]]


def start_app(layout: Layout, app_key: str, popen=subprocess.Popen) -> None:
    env = dict(os.environ)
    env[LAUNCHER_ENV] = str(layout.launcher)
    popen(app_command(layout, app_key), cwd=str(layout.root), env=env, close_fds=True)


def start_finisher(layout: Layout, app_key: str, popen=subprocess.Popen) -> None:
    """Start the console step that runs pip once this process has quit."""
    popen(
        [str(layout.python), str(layout.launcher), app_key,
         "--finish-update", "--wait-pid", str(os.getpid())],
        cwd=str(layout.root),
        creationflags=_NEW_CONSOLE,
        close_fds=True,
    )


def wait_for_process(pid: int, timeout: float = 60.0) -> None:
    """Wait until process `pid` has quit (or `timeout` seconds)."""
    if sys.platform == "win32":
        import ctypes  # pylint: disable=import-outside-toplevel

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def finish_update(layout: Layout, app_key: str, wait_pid: int | None, run=subprocess.run) -> int:
    """Console step: install the new dependencies, put the new code in place, start."""
    print("Mise à jour de RPG Tabletop : installation des composants.")
    print("Cela peut prendre quelques minutes, ne fermez pas cette fenêtre.\n")
    if wait_pid:
        wait_for_process(wait_pid)
    try:
        if run(pip_command(layout), check=False).returncode != 0:
            raise UpdateError("pip n'a pas pu installer les composants (voir ci-dessus).")
        swap_in(layout)
    except UpdateError as error:
        shutil.rmtree(layout.new_dir, ignore_errors=True)
        log_error(str(error))
        print(f"\nLa mise à jour a échoué : {error}")
        input("Appuyez sur Entrée pour lancer la version actuelle…")
        start_app(layout, app_key)
        return 1
    print("\nMise à jour terminée.")
    start_app(layout, app_key)
    return 0


# ----------------------------------------------------------------------------
# Window
# ----------------------------------------------------------------------------

def run_gui(layout: Layout, app_key: str, force_check: bool) -> int:
    """Check, offer and install the update, then start the application."""
    # pylint: disable=import-outside-toplevel
    from PySide6.QtCore import QEventLoop, Qt, QThread, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APPS[app_key][1])
    qt_app.setStyle("Fusion")
    title = "Mise à jour"

    class Downloader(QThread):
        progress = Signal(int, int)

        def __init__(self, target: Path) -> None:
            super().__init__()
            self.target = target
            self.error = ""

        def run(self) -> None:
            try:
                download(
                    archive_url(), self.target,
                    progress=lambda done, total: self.progress.emit(done // 1024, total // 1024),
                    cancelled=self.isInterruptionRequested,
                )
            except UpdateCancelled:
                self.error = "cancelled"
            except UpdateError as error:
                self.error = str(error)

    class Progress(QProgressDialog):
        def __init__(self, version: str) -> None:
            super().__init__(f"Téléchargement de la version {version}…", "Annuler", 0, 0)
            self.version = version
            self.setWindowTitle(title)
            self.setMinimumWidth(380)
            self.setMinimumDuration(0)
            self.setAutoClose(False)
            self.setAutoReset(False)

        def on_progress(self, done_kib: int, total_kib: int) -> None:
            if total_kib > 0:  # GitHub does not always give the size
                self.setMaximum(total_kib)
                self.setValue(min(done_kib, total_kib))
            self.setLabelText(
                f"Téléchargement de la version {self.version}… {done_kib / 1024:.1f} Mo"
            )

    def busy_call(function, *args):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return function(*args)
        finally:
            QApplication.restoreOverrideCursor()

    def offer(local: str | None, remote: str) -> str:
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>La version {remote} est disponible.</b>")
        box.setInformativeText(
            f"Version installée : {local or 'inconnue'}<br>"
            f'<a href="{changes_url()}">Voir les changements</a>'
        )
        update_button = box.addButton("Mettre à jour", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Plus tard", QMessageBox.ButtonRole.RejectRole)
        skip_button = box.addButton("Ignorer cette version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(update_button)
        box.exec()
        if box.clickedButton() is update_button:
            return "update"
        if box.clickedButton() is skip_button:
            return "skip"
        return "later"

    def download_archive(version: str) -> Path | None:
        target = Path(tempfile.gettempdir()) / "RPGTabletop-update" / "code.zip"
        dialog = Progress(version)
        worker = Downloader(target)
        loop = QEventLoop()
        worker.progress.connect(dialog.on_progress)
        worker.finished.connect(loop.quit)
        dialog.canceled.connect(worker.requestInterruption)
        dialog.show()
        worker.start()
        loop.exec()
        worker.wait()
        dialog.close()
        if worker.error == "cancelled":
            return None
        if worker.error:
            raise UpdateError(worker.error)
        return target

    def update() -> str:
        """Returns "launch" (start the application now) or "exit" (pip step will)."""
        if updates_disabled() and not force_check:
            return "launch"
        local = installed_version(layout.app_dir)
        try:
            remote = busy_call(fetch_remote_version)
        except UpdateError as error:
            if force_check:
                QMessageBox.warning(None, title, str(error))
            return "launch"
        if remote == local:
            if force_check:
                QMessageBox.information(None, title, f"Vous avez la dernière version ({local}).")
            return "launch"
        if not force_check and skipped_version() == remote:
            return "launch"

        choice = offer(local, remote)
        if choice == "skip":
            skip_version(remote)
        if choice != "update":
            return "launch"

        try:
            archive = download_archive(remote)
            if archive is None:
                return "launch"
            busy_call(extract_update, archive, layout.new_dir)
            archive.unlink(missing_ok=True)
            if requirements_changed(layout.app_dir, layout.new_dir):
                QMessageBox.information(
                    None, title,
                    "Cette version change les composants de l'application. Une fenêtre va "
                    "les installer (quelques minutes), puis l'application démarrera.",
                )
                start_finisher(layout, app_key)
                return "exit"
            swap_in(layout)
        except UpdateError as error:
            shutil.rmtree(layout.new_dir, ignore_errors=True)
            log_error(str(error))
            QMessageBox.warning(
                None, title, f"La mise à jour n'a pas pu être installée.\n\n{error}\n\n"
                "La version actuelle va démarrer.",
            )
        return "launch"

    try:
        outcome = update()
    except Exception:  # pylint: disable=broad-except
        # Never let the updater prevent the game from starting
        log_error(traceback.format_exc())
        outcome = "launch"
    if outcome == "launch":
        if installed_version(layout.app_dir) is None:
            QMessageBox.critical(
                None, title, "Le code de l'application est introuvable : réinstallez RPG Tabletop."
            )
            return 1
        start_app(layout, app_key)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RPG Tabletop launcher")
    parser.add_argument("app", choices=sorted(APPS))
    parser.add_argument("--check", action="store_true", help="always check, say when up to date")
    parser.add_argument("--finish-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    layout = Layout.here()
    if args.finish_update:
        return finish_update(layout, args.app, args.wait_pid)
    return run_gui(layout, args.app, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
