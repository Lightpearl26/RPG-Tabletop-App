# -*- coding: utf-8 -*-

"""
Update dialogs of the GM and player applications.

``startup_update_check`` runs before the main window opens: it is silent when there
is nothing to do or when GitHub cannot be reached. ``manual_update_check`` is behind
the *Help > Check for Updates* menu and always says what it found.

Both return True when the application must quit at once, because an installer or a
new process has been started to replace it.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEventLoop, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop import __version__
from rpg_tabletop.net import update

# Application key -> installer asset of the GitHub release
INSTALLERS = {"gm": update.GM_INSTALLER, "player": update.PLAYER_INSTALLER}

_CHECK_TIMEOUT = 4.0


@contextmanager
def _busy():
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


class UpdateDialog(QDialog):
    """Offer an update: *Update*, *Later* or *Skip this version*."""

    UPDATE = "update"
    LATER = "later"
    SKIP = "skip"

    def __init__(
        self,
        title: str,
        summary: str,
        details: str = "",
        *,
        can_update: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.choice = self.LATER

        layout = QVBoxLayout(self)
        label = QLabel(summary)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)

        if details:
            notes = QTextBrowser()
            notes.setOpenExternalLinks(True)
            notes.setMarkdown(details)
            notes.setMinimumHeight(220)
            layout.addWidget(notes)

        buttons = QHBoxLayout()
        skip_button = QPushButton("Ignorer cette version")
        skip_button.clicked.connect(lambda: self._choose(self.SKIP))
        buttons.addWidget(skip_button)
        buttons.addStretch(1)
        later_button = QPushButton("Plus tard")
        later_button.clicked.connect(lambda: self._choose(self.LATER))
        buttons.addWidget(later_button)
        if can_update:
            update_button = QPushButton("Mettre à jour")
            update_button.setDefault(True)
            update_button.clicked.connect(lambda: self._choose(self.UPDATE))
            buttons.addWidget(update_button)
        else:
            later_button.setDefault(True)
        layout.addLayout(buttons)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class _DownloadWorker(QThread):
    """Downloads and checks an installer outside the GUI thread."""

    progress = Signal(int, int)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, release: update.ReleaseInfo, asset_name: str) -> None:
        super().__init__()
        self._release = release
        self._asset_name = asset_name

    def run(self) -> None:  # noqa: D401 (Qt override)
        try:
            path = update.download_installer(
                self._release,
                self._asset_name,
                progress=lambda done, total: self.progress.emit(done // 1024, total // 1024),
                cancelled=self.isInterruptionRequested,
            )
        except update.UpdateCancelled:
            self.failed.emit("")
        except update.UpdateError as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(str(path))


class _DownloadProgressDialog(QProgressDialog):
    """
    Progress window of a download.

    The worker's signals are connected to methods of this widget, so Qt delivers
    them in the GUI thread.
    """

    def __init__(self, version: str, parent: QWidget | None) -> None:
        super().__init__(f"Téléchargement de la version {version}…", "Annuler", 0, 0, parent)
        self.version = version
        self.path: str = ""
        self.error: str = ""
        self.setWindowTitle("Mise à jour")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)

    def on_progress(self, done_kib: int, total_kib: int) -> None:
        if total_kib > 0:
            self.setMaximum(total_kib)
            self.setValue(min(done_kib, total_kib))
            self.setLabelText(
                f"Téléchargement de la version {self.version}… "
                f"{done_kib / 1024:.1f} / {total_kib / 1024:.1f} Mo"
            )

    def on_succeeded(self, path: str) -> None:
        self.path = path

    def on_failed(self, message: str) -> None:
        self.error = message


def _download(release: update.ReleaseInfo, asset_name: str, parent: QWidget | None) -> Path | None:
    """Download with a progress window; None if cancelled or failed (already reported)."""
    dialog = _DownloadProgressDialog(release.version, parent)
    worker = _DownloadWorker(release, asset_name)
    loop = QEventLoop()
    worker.progress.connect(dialog.on_progress)
    worker.succeeded.connect(dialog.on_succeeded)
    worker.failed.connect(dialog.on_failed)
    worker.finished.connect(loop.quit)
    dialog.canceled.connect(worker.requestInterruption)

    dialog.show()
    worker.start()
    loop.exec()
    worker.wait()
    QApplication.processEvents()  # deliver the last queued signals
    dialog.close()

    if dialog.path:
        return Path(dialog.path)
    if dialog.error:
        _report_failure(dialog.error, release.page_url, parent)
    return None


def _report_failure(message: str, page_url: str, parent: QWidget | None) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Mise à jour")
    box.setText("La mise à jour n'a pas pu être installée.")
    box.setInformativeText(message)
    if page_url:
        open_button = box.addButton("Ouvrir la page de la version", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(page_url))
    else:
        box.exec()


# ----------------------------------------------------------------------------
# Installed version: GitHub releases
# ----------------------------------------------------------------------------

def _check_release(
    app_key: str, parent: QWidget | None, manual: bool, before_install: Callable[[], bool] | None
) -> bool:
    asset_name = INSTALLERS[app_key]
    update.cleanup_downloads()
    try:
        with _busy():
            release = update.check_for_update(asset_name, timeout=_CHECK_TIMEOUT)
    except update.UpdateError as error:
        if manual:
            QMessageBox.warning(parent, "Mise à jour", str(error))
        return False

    if release is None:
        if manual:
            QMessageBox.information(
                parent, "Mise à jour", f"Vous avez la dernière version ({__version__})."
            )
        return False
    if not manual and update.skipped(app_key) == release.version:
        return False

    link = f' — <a href="{release.page_url}">page de la version</a>' if release.page_url else ""
    dialog = UpdateDialog(
        "Mise à jour disponible",
        f"<b>La version {release.version} est disponible.</b><br>"
        f"Version installée : {__version__}{link}<br><br>"
        "L'application se fermera, puis se relancera une fois la mise à jour installée.",
        release.notes or "*Pas de notes pour cette version.*",
        parent=parent,
    )
    dialog.exec()
    if dialog.choice == UpdateDialog.SKIP:
        update.skip(app_key, release.version)
    if dialog.choice != UpdateDialog.UPDATE:
        return False

    installer = _download(release, asset_name, parent)
    if installer is None:
        return False
    if before_install is not None and not before_install():
        return False
    try:
        update.start_installer(installer)
    except update.UpdateError as error:
        _report_failure(str(error), release.page_url, parent)
        return False
    return True


# ----------------------------------------------------------------------------
# Source checkout: git
# ----------------------------------------------------------------------------

def _check_git(
    parent: QWidget | None, manual: bool, before_install: Callable[[], bool] | None
) -> bool:
    root = update.source_root()
    if root is None:
        if manual:
            QMessageBox.information(
                parent,
                "Mise à jour",
                "Cette copie n'est ni une version installée ni un dépôt git : "
                "les mises à jour automatiques ne sont pas disponibles.",
            )
        return False
    try:
        with _busy():
            status = update.check_git_updates(root)
    except update.UpdateError as error:
        if manual:
            QMessageBox.warning(parent, "Mise à jour", str(error))
        return False

    if status is None or status.behind == 0:
        if manual:
            text = (
                "La branche courante n'a pas de branche distante."
                if status is None
                else f"Le dépôt est à jour avec {status.upstream}."
            )
            QMessageBox.information(parent, "Mise à jour", text)
        return False
    if not manual and update.skipped("git") == status.remote_head:
        return False

    plural = "s" if status.behind > 1 else ""
    summary = f"<b>{status.behind} nouveau{'x' if plural else ''} commit{plural} sur {status.upstream}.</b>"
    if not status.can_fast_forward:
        summary += (
            f"<br><br>Votre branche a aussi {status.ahead} commit(s) local(aux) : "
            "les branches ont divergé, faites la fusion vous-même."
        )
    elif status.dirty:
        summary += (
            "<br><br>Vous avez des modifications locales : git refusera la mise à jour "
            "si elles touchent les mêmes fichiers."
        )
    if status.dependencies_changed:
        summary += "<br><br>Les dépendances changent : il faudra relancer <code>pip install -r requirements.txt</code>."
    details = "\n".join(f"- `{line}`" for line in status.log)

    dialog = UpdateDialog(
        "Mise à jour disponible", summary, details, can_update=status.can_fast_forward, parent=parent
    )
    dialog.exec()
    if dialog.choice == UpdateDialog.SKIP:
        update.skip("git", status.remote_head)
    if dialog.choice != UpdateDialog.UPDATE:
        return False
    if before_install is not None and not before_install():
        return False

    try:
        with _busy():
            update.git_pull(root)
    except update.UpdateError as error:
        QMessageBox.warning(parent, "Mise à jour", str(error))
        return False

    if status.dependencies_changed:
        QMessageBox.information(
            parent,
            "Mise à jour",
            "Code mis à jour. Lancez <code>pip install -r requirements.txt</code> "
            "puis relancez l'application.",
        )
        return True
    if update.can_restart_from_source():
        try:
            update.restart_from_source()
            return True
        except update.UpdateError:
            pass
    QMessageBox.information(parent, "Mise à jour", "Code mis à jour. Relancez l'application.")
    return True


# ----------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------

def startup_update_check(app_key: str, parent: QWidget | None = None) -> bool:
    """
    Check for an update when the application starts.

    Args:
        app_key: "gm" or "player".

    Returns:
        True if the application must quit to let the update run.
    """
    if update.updates_disabled():
        return False
    if update.is_frozen():
        return _check_release(app_key, parent, False, None)
    return _check_git(parent, False, None)


def manual_update_check(
    app_key: str,
    parent: QWidget | None = None,
    before_install: Callable[[], bool] | None = None,
) -> bool:
    """
    Same as startup_update_check, but reports every outcome and ignores skipped versions.

    Args:
        before_install: called once the update is ready, just before it replaces the
            application; returning False cancels it. The GM window passes its
            ``close`` method so that unsaved changes are handled first.
    """
    if update.is_frozen():
        return _check_release(app_key, parent, True, before_install)
    return _check_git(parent, True, before_install)
