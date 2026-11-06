# -*- coding: utf-8 -*-
"""Small shared text chat panel for runtime windows, with log-level filters."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Every line has a level: chat messages and action results are Info,
# system lines are Debug, refusals Warning, failures Error.
DEBUG = "debug"
INFO = "info"
WARNING = "warning"
ERROR = "error"

LEVELS: tuple[tuple[str, str], ...] = (
    (DEBUG, "Debug"),
    (INFO, "Info"),
    (WARNING, "Avertissement"),
    (ERROR, "Erreur"),
)
DEFAULT_LEVELS = frozenset({INFO, WARNING, ERROR})
LEVEL_COLORS = {DEBUG: "#6c7480", INFO: "#98a2b1", WARNING: "#f9c74f", ERROR: "#ff6b6b"}
MAX_LINES = 2000


def network_log_level(message: str) -> str:
    """Level of a line of the server log, which carries none of its own."""
    text = str(message).casefold()
    if any(word in text for word in ("error", "erreur", "failed", "unavailable", "refused", "denied")):
        return ERROR
    if any(word in text for word in ("warning", "manual", "cgnat", "mismatch", "rate limit")):
        return WARNING
    return DEBUG  # the rest is a system line


@dataclass(frozen=True)
class ChatLine:
    level: str
    sender: str
    message: str
    system: bool = False  # written by the app, not by a person


class ChatPanel(QWidget):
    """
    Chat history plus one checkbox per log level (Debug, Info, Avertissement,
    Erreur). Messages between people and action results are Info, system
    lines are Debug.

    Args:
        settings_scope: QSettings application name used to remember the
                        checked levels ("" to not remember them).
    """

    send_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, settings_scope: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("RuntimePanel")
        self._settings_scope = settings_scope
        self._lines: list[ChatLine] = []
        self._levels = set(self._load_levels())
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        title = QLabel("Afficher :")
        title.setObjectName("Muted")
        filters.addWidget(title)
        self.level_boxes: dict[str, QCheckBox] = {}
        for level, label in LEVELS:
            box = QCheckBox(label)
            box.setChecked(level in self._levels)
            box.setStyleSheet(f"QCheckBox {{ color: {LEVEL_COLORS[level]}; }}")
            box.toggled.connect(lambda checked, level=level: self.set_level_visible(level, checked))
            self.level_boxes[level] = box
            filters.addWidget(box)
        filters.addStretch(1)
        root.addLayout(filters)

        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(False)
        root.addWidget(self.history, 1)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Écrire un message aux joueurs…")
        self.send_button = QPushButton("Envoyer")
        self.input.returnPressed.connect(self._send)
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        root.addLayout(row)
        self.set_connected(False)

    # ------------------------------------------------------------ settings
    def _load_levels(self) -> set[str]:
        if not self._settings_scope:
            return set(DEFAULT_LEVELS)
        try:
            stored = QSettings("RPGTabletop", self._settings_scope).value("chat/levels")
        except Exception:
            return set(DEFAULT_LEVELS)
        if stored is None:
            return set(DEFAULT_LEVELS)
        if isinstance(stored, str):
            stored = [item for item in stored.split(",") if item]
        known = {level for level, _label in LEVELS}
        try:
            return {str(item) for item in stored} & known
        except TypeError:
            return set(DEFAULT_LEVELS)

    def _save_levels(self) -> None:
        if not self._settings_scope:
            return
        try:
            QSettings("RPGTabletop", self._settings_scope).setValue("chat/levels", ",".join(sorted(self._levels)))
        except Exception:
            pass

    # ------------------------------------------------------------ filters
    def visible_levels(self) -> set[str]:
        return set(self._levels)

    def set_level_visible(self, level: str, visible: bool) -> None:
        if (level in self._levels) == bool(visible):
            return
        if visible:
            self._levels.add(level)
        else:
            self._levels.discard(level)
        box = self.level_boxes.get(level)
        if box is not None and box.isChecked() != bool(visible):
            box.setChecked(bool(visible))
        self._save_levels()
        self._render_all()

    def is_shown(self, line: ChatLine) -> bool:
        return line.level in self._levels

    def shown_lines(self) -> list[ChatLine]:
        return [line for line in self._lines if self.is_shown(line)]

    # ------------------------------------------------------------ input
    def set_connected(self, connected: bool) -> None:
        self.input.setEnabled(bool(connected))
        self.send_button.setEnabled(bool(connected))

    def _send(self) -> None:
        message = self.input.text().strip()
        if not message:
            return
        self.input.clear()
        self.send_requested.emit(message)

    # ------------------------------------------------------------ history
    def append_message(self, sender: str, message: str, *, system: bool = False, level: str | None = None) -> None:
        """
        Add a line. Without `level`, a system line is Debug and a message
        written by a person is Info.
        """
        if level is None:
            level = DEBUG if system else INFO
        line = ChatLine(str(level), str(sender), str(message), bool(system))
        self._lines.append(line)
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]
        if self.is_shown(line):
            self.history.append(self._html(line))

    def append_log(self, level: str, message: str, sender: str = "Système") -> None:
        """A line written by the app (system, result, refusal or error)."""
        self.append_message(sender, message, system=True, level=level)

    def _render_all(self) -> None:
        self.history.clear()
        for line in self.shown_lines():
            self.history.append(self._html(line))

    @staticmethod
    def _html(line: ChatLine) -> str:
        if line.system:
            color = LEVEL_COLORS.get(line.level, LEVEL_COLORS[INFO])
            text_color = color if line.level in (WARNING, ERROR) else (
                "#e5e9ef" if line.level == INFO else "#c7ced8"
            )
        elif line.sender.casefold() in {"mj", "gm"}:
            color, text_color = "#cba6f7", "#e5e9ef"
        else:
            color, text_color = "#8ec5ff", "#e5e9ef"
        return (
            f'<span style="color:{color};"><b>{escape(line.sender)}</b></span> '
            f'<span style="color:{text_color};">{escape(line.message)}</span>'
        )
