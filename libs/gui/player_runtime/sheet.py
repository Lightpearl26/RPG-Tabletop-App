# -*- coding: utf-8 -*-
"""Independent, non-modal character sheet of the Player Runtime."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QProgressBar, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .panels import STAT_COLORS, STAT_LABELS

CORE_STATS = ("str", "con", "dex", "agi", "int", "wis", "cha", "luc", "sur", "per")
STAT_BAR_MAX = 300
RESOURCES = (("hp", "HP", "#f38ba8"), ("stamina", "Stamina", "#fab387"))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class CharacterSheetWindow(QDialog):
    """Opened by a right click on the player's own token."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fiche de personnage")
        self.setModal(False)
        self.resize(540, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.name_label = QLabel("—")
        self.name_label.setObjectName("PlayerTitle")
        self.character_label = QLabel("")
        self.character_label.setObjectName("PlayerMuted")
        root.addWidget(self.name_label)
        root.addWidget(self.character_label)

        self.resource_bars: dict[str, QProgressBar] = {}
        for key, label, color in RESOURCES:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setFixedWidth(64)
            bar = QProgressBar()
            bar.setFormat("%v / %m")
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")
            self.resource_bars[key] = bar
            row.addWidget(name)
            row.addWidget(bar, 1)
            root.addLayout(row)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        stats_page = QWidget()
        grid = QGridLayout(stats_page)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)
        self.stat_values: dict[str, QLabel] = {}
        self.stat_bars: dict[str, QProgressBar] = {}
        for row, stat in enumerate(CORE_STATS):
            color = STAT_COLORS.get(stat, "#7F8A9B")
            label = QLabel(STAT_LABELS.get(stat, stat.upper()))
            label.setStyleSheet(f"color: {color}; font-weight: 700;")
            label.setFixedWidth(48)
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setFixedWidth(40)
            bar = QProgressBar()
            bar.setRange(0, STAT_BAR_MAX)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")
            self.stat_values[stat] = value
            self.stat_bars[stat] = bar
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            grid.addWidget(bar, row, 2)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(len(CORE_STATS), 1)
        self.tabs.addTab(stats_page, "Caractéristiques")

        self.items_table = self._make_table(("Objet", "Qté", "Modificateurs"))
        self.tabs.addTab(self.items_table, "Inventaire")
        self.spells_table = self._make_table(("Sort", "Coût", "Ciblage", "Description"))
        self.tabs.addTab(self.spells_table, "Sorts")
        self.effects_list = QListWidget()
        self.tabs.addTab(self.effects_list, "Effets")

    @staticmethod
    def _make_table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _fill(table: QTableWidget, rows: list[tuple[list[str], str]]) -> None:
        table.setRowCount(len(rows))
        for r, (cells, tooltip) in enumerate(rows):
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if tooltip:
                    item.setToolTip(tooltip)
                table.setItem(r, c, item)
        table.resizeColumnsToContents()

    def set_entity(self, entity: dict[str, Any]) -> None:
        stats = entity.get("stats") if isinstance(entity.get("stats"), dict) else {}
        stats_max = entity.get("stats_max") if isinstance(entity.get("stats_max"), dict) else {}
        self.name_label.setText(str(entity.get("name", "Entity")))
        self.character_label.setText(f"Personnage : {entity.get('character', '—')}")
        self.setWindowTitle(f"Fiche — {entity.get('name', 'Entity')}")

        for key, bar in self.resource_bars.items():
            value = _int(stats.get(key))
            maximum = _int(stats_max.get(key), value)
            bar.setRange(0, max(1, maximum, value))
            bar.setValue(max(0, value))
        for stat in CORE_STATS:
            value = _int(stats.get(stat))
            self.stat_values[stat].setText(str(value) if stat in stats else "—")
            self.stat_bars[stat].setValue(max(0, min(STAT_BAR_MAX, value)))

        rows = []
        for item in entity.get("items", []) if isinstance(entity.get("items"), list) else []:
            if not isinstance(item, dict):
                rows.append(([str(item), "", ""], ""))
                continue
            mods = []
            for entry in item.get("modifier", []) or []:
                try:
                    stat, amount = entry
                    mods.append(f"{STAT_LABELS.get(str(stat), str(stat).upper())} {int(amount):+d}")
                except (TypeError, ValueError):
                    continue
            rows.append((
                [str(item.get("name", "Objet")), str(item.get("quantity", "")), ", ".join(mods)],
                str(item.get("description", "")),
            ))
        self._fill(self.items_table, rows)

        rows = []
        spells = entity.get("spells") if isinstance(entity.get("spells"), dict) else {}
        for name, detail in spells.items():
            detail = detail if isinstance(detail, dict) else {}
            description = str(detail.get("description", ""))
            rows.append((
                [str(name), f"{detail.get('cost', '?')} STA",
                 str(detail.get("targeting", "single")), description],
                description,
            ))
        self._fill(self.spells_table, rows)

        self.effects_list.clear()
        for effect in entity.get("spell_effects", []) if isinstance(entity.get("spell_effects"), list) else []:
            if not isinstance(effect, dict):
                continue
            stat = str(effect.get("target_stat", "?"))
            duration = _int(effect.get("duration"), 0)
            self.effects_list.addItem(
                f"{effect.get('effect_type', '?')} · {STAT_LABELS.get(stat, stat.upper())} "
                f"{_int(effect.get('delta')):+d} · "
                f"{'permanent' if duration < 0 else f'{duration} tour(s)'}"
            )