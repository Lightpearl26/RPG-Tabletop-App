# -*- coding: utf-8 -*-

"""
Update dialogs of the GM and player applications.

In the installed version the launcher updates the code before the application
starts (packaging/launcher.py); *Help > Check for Updates* closes the application
and starts the launcher again. From a source checkout, the application itself
offers ``git pull`` when the remote branch has new commits.

``startup_update_check`` and ``manual_update_check`` return True when the
application must quit at once, because a new process takes over.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop import __version__
from rpg_tabletop.net import update


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


# ----------------------------------------------------------------------------
# Installed version: launcher
# ----------------------------------------------------------------------------

def _check_with_launcher(
    app_key: str, parent: QWidget | None, before_install: Callable[[], bool] | None
) -> bool | None:
    """None when the application was not started by the launcher."""
    launcher = update.launcher_path()
    if launcher is None:
        return None
    answer = QMessageBox.question(
        parent,
        "Mise à jour",
        f"Version installée : {__version__}.<br><br>"
        "Pour chercher une mise à jour, l'application va se fermer et le lanceur "
        "va vérifier la version sur GitHub. Continuer ?",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    if before_install is not None and not before_install():
        return False
    try:
        update.restart_with_launcher(launcher, app_key)
    except update.UpdateError as error:
        QMessageBox.warning(parent, "Mise à jour", str(error))
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

def startup_update_check(app_key: str, parent: QWidget | None = None) -> bool:  # pylint: disable=unused-argument
    """
    Check for an update when the application starts.

    Only from a source checkout: the installed version was already checked by the
    launcher.

    Args:
        app_key: "gm" or "player".

    Returns:
        True if the application must quit (it restarts with the new code).
    """
    if update.updates_disabled() or update.launcher_path() is not None:
        return False
    return _check_git(parent, False, None)


def manual_update_check(
    app_key: str,
    parent: QWidget | None = None,
    before_install: Callable[[], bool] | None = None,
) -> bool:
    """
    *Help > Check for Updates*: reports every outcome and ignores skipped versions.

    Args:
        before_install: called just before the application is replaced or closed;
            returning False cancels. The GM window passes its ``close`` method so that
            unsaved changes are handled first.
    """
    result = _check_with_launcher(app_key, parent, before_install)
    if result is not None:
        return result
    return _check_git(parent, True, before_install)
