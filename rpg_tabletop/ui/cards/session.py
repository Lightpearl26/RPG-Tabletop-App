# -*- coding: utf-8 -*-

"""
Session card module for the RPG Tabletop App.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .base import ResourceCard


THEME = """
QLabel#SessionCardName {
    font-size: 15px;
    font-weight: 700;
}

QLabel#SessionCardDate {
    color: #9da4b5;
    font-size: 12px;
}
"""


class SessionCard(ResourceCard):
    """Preview card showing a session's name and date."""

    def __init__(
        self,
        session_name: str,
        session_date: date | datetime | str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(session_name, parent)

        self.session_name = session_name
        self.session_date = session_date

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.name_label = QLabel()
        self.name_label.setObjectName("SessionCardName")
        layout.addWidget(self.name_label)

        self.date_label = QLabel()
        self.date_label.setObjectName("SessionCardDate")
        layout.addWidget(self.date_label)
        layout.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        """Refresh the displayed session name and date."""
        self.name_label.setText(self.session_name)
        self.date_label.setText(self._format_date(self.session_date))

    def search_text(self) -> str:
        """Text indexed by ResourceBrowser."""
        return f"{self.session_name} {self._format_date(self.session_date)}".casefold()

    @staticmethod
    def _format_date(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y")
        if isinstance(value, date):
            return value.strftime("%d/%m/%Y")

        date_text = value.strip()
        try:
            return datetime.fromisoformat(
                date_text.replace("Z", "+00:00")
            ).strftime("%d/%m/%Y")
        except ValueError:
            return date_text