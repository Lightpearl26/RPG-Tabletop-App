# -*- coding: utf-8 -*-

"""
Entity card module for the RPG Tabletop App.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .base import ResourceCard
from rpg_tabletop.core.stats import CORE_STATS as STAT_NAMES
from rpg_tabletop.ui.theme.palette import RESOURCE_PALETTE, STAT_PALETTE
from rpg_tabletop.session.entity import Entity


THEME = """
QLabel#EntityCardName {
    font-size: 15px;
    font-weight: 700;
}

QLabel#EntityCardLevel {
    color: #9da4b5;
    font-size: 12px;
}

QLabel#EntityCardStatName,
QLabel#EntityCardStatValue,
QLabel#EntityCardResourceLabel,
QLabel#EntityCardResourceValue {
    color: #d7d9e0;
    font-size: 11px;
}

QProgressBar#EntityCardStatBar {
    background-color: #171a22;
    border: none;
    border-radius: 3px;
}

QProgressBar#EntityCardStatBar::chunk {
    border-radius: 3px;
}

QProgressBar#EntityCardStatBar[statColor="red"]::chunk {
    background-color: #d9534f;
}

QProgressBar#EntityCardStatBar[statColor="orange"]::chunk {
    background-color: #f0ad4e;
}

QProgressBar#EntityCardStatBar[statColor="green"]::chunk {
    background-color: #5cb85c;
}

QProgressBar#EntityCardStatBar[statColor="blue"]::chunk {
    background-color: #5bc0de;
}

QProgressBar#EntityCardStatBar[statColor="purple"]::chunk {
    background-color: #8b7cff;
}

QLabel#EntityCardResourceTitle {
    color: #9da4b5;
    font-size: 11px;
    font-weight: 700;
}

QProgressBar#EntityCardResourceBar {
    background-color: #171a22;
    border: none;
    border-radius: 4px;
}

QProgressBar#EntityCardResourceBar::chunk {
    border-radius: 4px;
}
"""


STAT_FIELDS = tuple(
    (stat_name, stat_name.upper())
    for stat_name in STAT_NAMES
)

RESOURCE_FIELDS = (
    ("hp", "HP"),
    ("stamina", "STAMINA"),
    ("mental_health", "MENTAL HEALTH"),
    ("drug_health", "DRUG HEALTH"),
)


class EntityCard(ResourceCard):
    """Preview card showing an entity's effective stats."""

    STAT_BAR_MAX = 300

    def __init__(
        self,
        resource_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(resource_id, parent)

        self.entity = Entity.from_name(resource_id)
        self.stat_value_labels: dict[str, QLabel] = {}
        self.stats_bars: dict[str, QProgressBar] = {}
        self.resource_value_labels: dict[str, QLabel] = {}
        self.resource_bars: dict[str, QProgressBar] = {}

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.name_label = QLabel()
        self.name_label.setObjectName("EntityCardName")
        header_layout.addWidget(self.name_label)
        header_layout.addStretch(1)

        self.level_label = QLabel()
        self.level_label.setObjectName("EntityCardLevel")
        self.level_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header_layout.addWidget(self.level_label)
        layout.addLayout(header_layout)

        self.character_label = QLabel()
        self.character_label.setObjectName("EntityCardLevel")
        layout.addWidget(self.character_label)

        stats_layout = QGridLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setHorizontalSpacing(5)
        stats_layout.setVerticalSpacing(4)

        for index, (stat_name, label_text) in enumerate(STAT_FIELDS):
            row = index // 2
            column = (index % 2) * 3

            stat_label = QLabel(label_text)
            stat_label.setObjectName("EntityCardStatName")
            stat_label.setToolTip(stat_name.replace("_", " ").title())
            stat_label.setStyleSheet(
                f"color: {STAT_PALETTE.get(stat_name, RESOURCE_PALETTE.get(stat_name, '#7F8A9B'))}"
            )
            stat_label.setFixedWidth(42)

            value_label = QLabel()
            value_label.setObjectName("EntityCardStatValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            value_label.setFixedWidth(26)
            self.stat_value_labels[stat_name] = value_label

            stat_bar = QProgressBar()
            stat_bar.setObjectName("EntityCardStatBar")
            stat_bar.setRange(0, self.STAT_BAR_MAX)
            stat_bar.setTextVisible(False)
            stat_bar.setFixedHeight(6)
            self.stats_bars[stat_name] = stat_bar

            stats_layout.addWidget(stat_label, row, column)
            stats_layout.addWidget(value_label, row, column + 1)
            stats_layout.addWidget(stat_bar, row, column + 2)

        stats_layout.setColumnStretch(2, 1)
        stats_layout.setColumnStretch(5, 1)
        layout.addLayout(stats_layout)

        resource_title = QLabel("RESOURCES")
        resource_title.setObjectName("EntityCardResourceTitle")
        layout.addWidget(resource_title)

        resources_layout = QVBoxLayout()
        resources_layout.setContentsMargins(0, 0, 0, 0)
        resources_layout.setSpacing(5)

        for resource_name, label_text in RESOURCE_FIELDS:
            resource_row = QHBoxLayout()
            resource_row.setContentsMargins(0, 0, 0, 0)
            resource_row.setSpacing(8)

            resource_label = QLabel(label_text)
            resource_label.setObjectName("EntityCardResourceLabel")
            resource_label.setFixedWidth(92)

            resource_bar = QProgressBar()
            resource_bar.setObjectName("EntityCardResourceBar")
            resource_bar.setTextVisible(False)
            resource_bar.setFixedHeight(9)
            color = RESOURCE_PALETTE[resource_name]
            resource_bar.setStyleSheet(
                f"QProgressBar#EntityCardResourceBar::chunk {{ background-color: {color}; }}"
            )
            self.resource_bars[resource_name] = resource_bar

            value_label = QLabel()
            value_label.setObjectName("EntityCardResourceValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            value_label.setFixedWidth(62)
            self.resource_value_labels[resource_name] = value_label

            resource_row.addWidget(resource_label)
            resource_row.addWidget(resource_bar, 1)
            resource_row.addWidget(value_label)
            resources_layout.addLayout(resource_row)

        layout.addLayout(resources_layout)
        layout.addStretch(1)

    def refresh(self) -> None:
        """Refresh the card from the runtime entity."""
        self.name_label.setText(self.entity.name)
        self.character_label.setText(self.entity.character.name)
        self.level_label.setText(
            f"Lv. {self.entity.character.stats.level}"
        )

        for stat_name, _ in STAT_FIELDS:
            value = self.entity.get_stat(stat_name)
            self.stat_value_labels[stat_name].setText(str(value))
            stat_bar = self.stats_bars[stat_name]
            stat_bar.setValue(max(0, min(int(value), self.STAT_BAR_MAX)))
            stat_bar.setProperty("statColor", self._get_stat_color_name(value))
            stat_bar.style().unpolish(stat_bar)
            stat_bar.style().polish(stat_bar)
            stat_bar.update()

        for resource_name, _ in RESOURCE_FIELDS:
            value = self.entity.get_stat(resource_name)
            maximum = self._get_resource_maximum(resource_name)
            bar_maximum = max(1, maximum)

            self.resource_value_labels[resource_name].setText(
                f"{value} / {maximum}"
            )
            self.resource_bars[resource_name].setRange(0, bar_maximum)
            self.resource_bars[resource_name].setValue(
                max(0, min(int(value), bar_maximum))
            )

    def _get_stat_color_name(self, value: int | float) -> str:
        """Return the color tier associated with a stat value."""
        if value <= 50:
            return "red"
        if value <= 100:
            return "orange"
        if value <= 150:
            return "green"
        if value <= 200:
            return "blue"
        return "purple"

    def _get_resource_maximum(self, resource_name: str) -> int:
        """Return the character's base maximum for a consumable resource."""
        stats = self.entity.character.stats
        if resource_name == "hp":
            return max(0, int(stats.hp))
        if resource_name == "stamina":
            return max(0, int(stats.stamina))
        return max(0, int(getattr(stats, resource_name, 0)))

    def search_text(self) -> str:
        """Text indexed by ResourceBrowser."""
        return (
            f"{self.entity.name} {self.entity.character.name} "
            f"{self.entity.id}"
        ).casefold()
