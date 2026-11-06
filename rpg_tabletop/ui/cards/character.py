# -*- coding: utf-8 -*-

"""
Character card module for the RPG Tabletop App.
"""

from __future__ import annotations

# import third-party modules
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QProgressBar,
    QGridLayout,
)

# import local modules
from .base import ResourceCard
from . import STAT_PALETTE, STAT_NAMES
from rpg_tabletop.assets.character import Character


# constants
THEME = """
QProgressBar#CharacterCardStatBar {
    background-color: #171a22;
    border: none;
    border-radius: 3px;
}

QProgressBar#CharacterCardStatBar::chunk {
    border-radius: 3px;
}

QLabel#CharacterCardName {
    font-size: 15px;
    font-weight: 700;
}

QLabel#CharacterCardLevel {
    color: #9da4b5;
    font-size: 12px;
}

QLabel#CharacterCardHP {
    font-weight: 600;
}

QLabel#CharacterCardStatName {
    color: #d7d9e0;
    font-size: 12px;
}

QLabel#CharacterCardStatValue {
    color: #d7d9e0;
    font-size: 12px;
}

QLabel#CharacterCardStatSeparator {
    color: #555c6e;
    font-size: 12px;
}
"""


class CharacterCard(ResourceCard):
    """
    Character preview card.
    """
    STAT_BAR_MAX = 300

    def __init__(
        self,
        resource_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(resource_id, parent)

        self.character = Character.from_name(resource_id)

        self.stat_name_labels: dict[str, QLabel] = {}
        self.stat_value_labels: dict[str, QLabel] = {}
        self.stats_bars: dict[str, QProgressBar] = {}

        self.build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        """
        Build the character card user interface.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        # --------------------------------------------------------------
        # Header
        # --------------------------------------------------------------

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.name_label = QLabel()
        self.name_label.setObjectName("CharacterCardName")

        self.level_label = QLabel()
        self.level_label.setObjectName("CharacterCardLevel")
        self.level_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        header_layout.addWidget(self.name_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.level_label)

        layout.addLayout(header_layout)

        # --------------------------------------------------------------
        # HP
        # --------------------------------------------------------------

        self.hp_label = QLabel()
        self.hp_label.setObjectName("CharacterCardHP")
        layout.addWidget(self.hp_label)

        layout.addSpacing(2)

        # --------------------------------------------------------------
        # Stats grid
        # --------------------------------------------------------------

        stats_layout = QGridLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setHorizontalSpacing(5)
        stats_layout.setVerticalSpacing(3)

        for row, stat_name in enumerate(STAT_NAMES):
            # Stat name
            stat_name_label = QLabel()
            stat_name_label.setObjectName("CharacterCardStatName")
            stat_name_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
            )
            stat_name_label.setStyleSheet(f"color: {STAT_PALETTE[stat_name]}")
            stat_name_label.setFixedWidth(34)

            self.stat_name_labels[stat_name] = stat_name_label

            # Stat value
            stat_value_label = QLabel()
            stat_value_label.setObjectName("CharacterCardStatValue")
            stat_value_label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )
            stat_value_label.setFixedWidth(28)

            self.stat_value_labels[stat_name] = stat_value_label

            # Separator
            separator = QLabel("|")
            separator.setObjectName("CharacterCardStatSeparator")
            separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
            separator.setFixedWidth(8)

            # Progress bar
            stat_bar = QProgressBar()
            stat_bar.setObjectName("CharacterCardStatBar")
            stat_bar.setRange(0, self.STAT_BAR_MAX)
            stat_bar.setTextVisible(False)
            stat_bar.setFixedHeight(6)
            stat_bar.setStyleSheet(
                f"QProgressBar#CharacterCardStatBar::chunk {{ background-color: {STAT_PALETTE[stat_name]}; }}"
            )

            self.stats_bars[stat_name] = stat_bar

            stats_layout.addWidget(stat_name_label, row, 0)
            stats_layout.addWidget(stat_value_label, row, 1)
            stats_layout.addWidget(separator, row, 2)
            stats_layout.addWidget(stat_bar, row, 3)

        stats_layout.setColumnStretch(3, 1)

        layout.addLayout(stats_layout)
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """
        Refresh the card from the Character resource.
        """
        self.name_label.setText(self.character.name)

        level = self.character.stats.level
        if level is None:
            self.level_label.clear()
        else:
            self.level_label.setText(f"Lv. {level}")

        hp = self.character.stats.hp
        self.hp_label.setText(f"HP: {hp}")

        for stat_name in STAT_NAMES:
            value = self._get_stat_value(stat_name)

            self.stat_name_labels[stat_name].setText(
                f"{stat_name.upper()}:"
            )

            self.stat_value_labels[stat_name].setText(
                str(value)
            )

            clamped_value = max(
                0,
                min(
                    int(value),
                    self.STAT_BAR_MAX,
                ),
            )

            bar = self.stats_bars[stat_name]
            bar.setValue(clamped_value)

    # ------------------------------------------------------------------
    # Character data
    # ------------------------------------------------------------------

    def _get_stat_value(
        self,
        stat_name: str,
    ) -> int | float:
        """
        Retrieve a stat from Character.stats.
        """
        return getattr(
            self.character.stats,
            stat_name,
            0,
        )

    # ------------------------------------------------------------------
    # Browser search
    # ------------------------------------------------------------------

    def search_text(self) -> str:
        """
        Text indexed by ResourceBrowser.
        """
        return f"{self.resource_id} {self.character.name}"


