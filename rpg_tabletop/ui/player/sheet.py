# -*- coding: utf-8 -*-
"""
Independent, non-modal character sheet of the Player Runtime.

Same header, tabs and columns as the GM sheet (`rpg_tabletop.ui.gm_runtime.sheet`), read
only, and built from the player projection: no GM-only stat, no formula.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.ui.common.sheet_parts import (
    CORE_STATS,
    EFFECT_LABELS,
    EFFECTS_COLUMNS,
    EVENTS_COLUMNS,
    ITEMS_COLUMNS,
    POLICY_LABELS,
    STAT_COLORS,
    STATS_COLUMNS,
    SheetHeader,
    SpellGrid,
    cell,
    duration_label,
    effect_origin_label,
    effect_value_label,
    header_label,
    make_table,
    modifiers_label,
    muted,
    set_resource_bar,
    stat_label,
)


RESOURCE_STATS = ("hp", "stamina")  # mental and drug health stay GM-only
SHEET_STATS = (*RESOURCE_STATS, *CORE_STATS)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _signed(value: int) -> str:
    return f"{value:+d}" if value else "—"


class CharacterSheetWindow(QDialog):
    """Opened by a right click on the player's own token."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fiche de personnage")
        self.setModal(False)
        self.resize(720, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.header = SheetHeader(RESOURCE_STATS)
        root.addWidget(self.header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stats_tab(), "Caractéristiques")
        self.items_table = make_table(ITEMS_COLUMNS)
        self.items_table.setColumnWidth(0, 170)
        self.items_table.setColumnWidth(1, 60)
        self.items_table.setColumnWidth(2, 170)
        self.tabs.addTab(self.items_table, "Inventaire")
        self.spell_grid = SpellGrid()
        self.tabs.addTab(self.spell_grid, "Sorts")
        self.effects_table = make_table(EFFECTS_COLUMNS)
        self.effects_table.setColumnWidth(0, 220)
        self.effects_table.setColumnWidth(1, 170)
        self.tabs.addTab(self.effects_table, "Effets actifs")
        self.events_table = make_table(EVENTS_COLUMNS)
        self.events_table.setColumnWidth(0, 150)
        self.tabs.addTab(self.events_table, "Sorts en cours")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Fermer")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

    def _build_stats_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        for column, title in enumerate(STATS_COLUMNS):
            label = header_label(title)
            if title == "Modif.":
                label.setToolTip("Modificateur fixé par le MJ")
            grid.addWidget(label, 0, column)

        self.stat_cells: dict[str, tuple[QLabel, ...]] = {}
        row = 1
        for stat in SHEET_STATS:
            if stat == CORE_STATS[0]:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("color: #303642;")
                grid.addWidget(line, row, 0, 1, len(STATS_COLUMNS))
                row += 1
            name = QLabel(stat_label(stat))
            name.setStyleSheet(f"font-weight: 700; color: {STAT_COLORS.get(stat, '#e5e9ef')};")
            values = tuple(QLabel("—") for _ in range(len(STATS_COLUMNS) - 1))
            values[-1].setStyleSheet("font-weight: 700;")
            for column, widget in enumerate((name, *values)):
                grid.addWidget(widget, row, column)
            self.stat_cells[stat] = values
            row += 1
        grid.setColumnStretch(len(STATS_COLUMNS), 1)
        root.addLayout(grid)
        root.addWidget(muted("Total = base + modificateur + objets + effets."))
        root.addStretch(1)
        return page

    # ---------------------------------------------------------------- refresh
    def set_entity(self, entity: dict[str, Any], token_image: Optional[bytes] = None) -> None:
        """Show the projection of the player's entity (and its token picture)."""
        stats = _dict(entity.get("stats"))
        stats_max = _dict(entity.get("stats_max"))
        details = _dict(entity.get("stats_detail"))
        name = str(entity.get("name", "Entity"))
        self.setWindowTitle(f"Fiche — {name}")

        image = None
        if token_image:
            image = QImage.fromData(token_image)
        level = entity.get("level")
        self.header.set_identity(
            name,
            str(entity.get("character", "")),
            _int(level) if level is not None else None,
            image,
            True,
        )
        for stat, bar in self.header.bars.items():
            value = _int(stats.get(stat))
            set_resource_bar(bar, value, _int(stats_max.get(stat), value))
        self.header.set_damage_modifiers(
            _int(entity.get("damage_reduction")), _int(entity.get("damage_amplification"))
        )

        for stat, (base, modifier, items, effects, total) in self.stat_cells.items():
            parts = _list(details.get(stat))
            if len(parts) == 4:
                base.setText(str(_int(parts[0])))
                modifier.setText(_signed(_int(parts[1])))
                items.setText(_signed(_int(parts[2])))
                effects.setText(_signed(_int(parts[3])))
            else:  # an older server sends totals only
                for label in (base, modifier, items, effects):
                    label.setText("—")
            total.setText(str(_int(stats.get(stat))) if stat in stats else "—")

        self._fill_items(_list(entity.get("items")))
        spells = _dict(entity.get("spells"))
        self.spell_grid.set_spells(
            sorted(((str(key), _dict(value)) for key, value in spells.items()), key=lambda p: p[0].casefold()),
            "Aucun sort connu.",
        )
        self._fill_effects(_list(entity.get("spell_effects")))
        self._fill_events(_list(entity.get("spell_events")))

    def _fill_items(self, items: list) -> None:
        rows = [item for item in items if isinstance(item, dict)]
        self.items_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            quantity = _int(item.get("quantity"), 1)
            description = str(item.get("description", ""))
            self.items_table.setItem(row, 0, cell(item.get("name", "Objet")))
            self.items_table.setItem(row, 1, cell(quantity))
            self.items_table.setItem(row, 2, cell(modifiers_label(item.get("modifier"), quantity)))
            self.items_table.setItem(row, 3, cell(description, tooltip=description))

    def _fill_effects(self, effects: list) -> None:
        rows = [effect for effect in effects if isinstance(effect, dict)]
        self.effects_table.setRowCount(len(rows))
        for row, effect in enumerate(rows):
            effect_type = str(effect.get("effect_type", "other"))
            origin = effect_origin_label(str(effect.get("origin") or ""), str(effect.get("source") or ""))
            self.effects_table.setItem(row, 0, cell(origin, tooltip=origin))
            self.effects_table.setItem(row, 1, cell(EFFECT_LABELS.get(effect_type, effect_type)))
            self.effects_table.setItem(row, 2, cell(stat_label(str(effect.get("target_stat", "?")))))
            self.effects_table.setItem(row, 3, cell(effect_value_label(effect_type, _int(effect.get("delta")))))
            self.effects_table.setItem(row, 4, cell(duration_label(_int(effect.get("duration"), -1))))

    def _fill_events(self, events: list) -> None:
        rows = [event for event in events if isinstance(event, dict)]
        self.events_table.setRowCount(len(rows))
        for row, event in enumerate(rows):
            policy = str(event.get("runtime_policy", ""))
            targets = len(_list(event.get("targets_id")))
            self.events_table.setItem(row, 0, cell(event.get("spell_id", "?")))
            self.events_table.setItem(row, 1, cell(f"{targets} cible(s)"))
            self.events_table.setItem(row, 2, cell(POLICY_LABELS.get(policy, policy)))
            self.events_table.setItem(row, 3, cell(_int(event.get("nb_cast"))))
            self.events_table.setItem(row, 4, cell("Terminé" if event.get("finished") else "En cours"))
            self.events_table.setItem(row, 5, cell(len(_list(event.get("effects")))))
