"""Item resource editor."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.core.stats import CORE_STATS as STAT_NAMES
from rpg_tabletop.ui.theme.palette import RESOURCE_PALETTE, field_color
from .base import ResourceEditor


MODIFIER_STATS = (*STAT_NAMES, *RESOURCE_PALETTE)


class ItemEditor(ResourceEditor):
    """Editable item definition backed by its campaign JSON file."""

    resource_label = "Item"
    resource_type = "items"

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self._build_ui()
        self._populate_fields()
        self._mark_clean()

    @property
    def directory(self) -> Path:
        return self.campaign.item_path

    @property
    def item_name(self) -> str:
        """
        The name of the item as entered in the editor.

        Returns:
            str: The trimmed item name.
        """
        return self.name_input.text().strip()

    def _new_data(self) -> dict:
        return {"name": "", "description": "", "modifier": []}

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        name_form = QFormLayout()
        name_form.setContentsMargins(0, 0, 0, 0)
        self.name_input = QLineEdit()
        self.name_input.setObjectName("ItemNameField")
        self.name_input.setPlaceholderText("Item name")
        name_form.addRow("Name", self.name_input)
        layout.addLayout(name_form)

        description_group = QGroupBox("Description")
        description_group.setObjectName("ItemEditorSection")
        description_layout = QVBoxLayout(description_group)
        self.description_input = QPlainTextEdit()
        self.description_input.setObjectName("ItemDescriptionField")
        self.description_input.setPlaceholderText("What does this item do?")
        description_layout.addWidget(self.description_input)
        layout.addWidget(description_group, 1)

        modifiers_group = QGroupBox("Stat modifiers")
        modifiers_group.setObjectName("ItemEditorSection")
        modifiers_layout = QVBoxLayout(modifiers_group)
        self.modifiers_table = QTableWidget(0, 2)
        self.modifiers_table.setObjectName("ItemModifiersTable")
        self.modifiers_table.setHorizontalHeaderLabels(("Stat", "Modifier"))
        self.modifiers_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )
        self.modifiers_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.modifiers_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.modifiers_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        modifiers_layout.addWidget(self.modifiers_table, 1)

        modifier_controls = QHBoxLayout()
        self.stat_picker = QComboBox()
        self.stat_picker.setObjectName("ItemStatPicker")
        self.stat_picker.addItems(MODIFIER_STATS)
        self.modifier_value = self._make_spin_box()
        self.modifier_value.setValue(10)
        self.add_modifier_button = QPushButton("Add")
        self.remove_modifier_button = QPushButton("Remove")
        modifier_controls.addWidget(self.stat_picker, 1)
        modifier_controls.addWidget(self.modifier_value)
        modifier_controls.addWidget(self.add_modifier_button)
        modifier_controls.addWidget(self.remove_modifier_button)
        modifiers_layout.addLayout(modifier_controls)
        layout.addWidget(modifiers_group, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.save_button = QPushButton("Save Item")
        self.save_button.setObjectName("ItemEditorSaveButton")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.add_modifier_button.clicked.connect(self._add_modifier)
        self.remove_modifier_button.clicked.connect(self._remove_modifier)
        self.save_button.clicked.connect(self.save_requested)
        self.name_input.textChanged.connect(self._refresh_modified)
        self.description_input.textChanged.connect(self._refresh_modified)

    @staticmethod
    def _make_spin_box() -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(-9999, 9999)
        return spin_box

    def _populate_fields(self) -> None:
        self.name_input.setText(str(self._data.get("name", "")))
        self.description_input.setPlainText(str(self._data.get("description", "")))
        self.modifiers_table.setRowCount(0)
        for stat, value in self._modifier_entries():
            self._append_modifier_row(stat, value)

    def _modifier_entries(self) -> list[tuple[str, int]]:
        raw_modifiers = self._data.get("modifier", [])
        if not isinstance(raw_modifiers, (list, tuple)):
            return []

        entries = []
        for entry in raw_modifiers:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            try:
                entries.append((str(entry[0]), int(entry[1])))
            except (TypeError, ValueError):
                continue
        return entries

    def _append_modifier_row(self, stat: str, value: int) -> None:
        row = self.modifiers_table.rowCount()
        self.modifiers_table.insertRow(row)
        stat_item = QTableWidgetItem(stat)
        stat_item.setForeground(QColor(field_color(stat)))
        self.modifiers_table.setItem(row, 0, stat_item)
        value_input = self._make_spin_box()
        value_input.setValue(value)
        value_input.valueChanged.connect(self._refresh_modified)
        self.modifiers_table.setCellWidget(row, 1, value_input)

    def _add_modifier(self) -> None:
        stat = self.stat_picker.currentText()
        if not stat:
            return

        for row in range(self.modifiers_table.rowCount()):
            stat_item = self.modifiers_table.item(row, 0)
            value_input = self.modifiers_table.cellWidget(row, 1)
            if (
                stat_item is not None
                and stat_item.text() == stat
                and isinstance(value_input, QSpinBox)
            ):
                value_input.setValue(value_input.value() + self.modifier_value.value())
                return

        self._append_modifier_row(stat, self.modifier_value.value())
        self._refresh_modified()

    def _remove_modifier(self) -> None:
        row = self.modifiers_table.currentRow()
        if row >= 0:
            self.modifiers_table.removeRow(row)
            self._refresh_modified()

    def _current_modifiers(self) -> list[list[str | int]]:
        modifiers: list[list[str | int]] = []
        for row in range(self.modifiers_table.rowCount()):
            stat_item = self.modifiers_table.item(row, 0)
            value_input = self.modifiers_table.cellWidget(row, 1)
            stat = stat_item.text().strip() if stat_item else ""
            if stat and isinstance(value_input, QSpinBox):
                modifiers.append([stat, value_input.value()])
        return modifiers

    def _collect_data(self) -> dict:
        data = deepcopy(self._data)
        data["name"] = self.item_name
        data["description"] = self.description_input.toPlainText()
        data["modifier"] = self._current_modifiers()
        return data
