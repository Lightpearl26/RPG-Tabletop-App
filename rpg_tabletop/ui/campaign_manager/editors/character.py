#-*- coding: utf-8 -*-

"""
Character resource editor.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.character import Stats
from rpg_tabletop.core.stats import CORE_STATS as STAT_NAMES
from rpg_tabletop.ui.theme.palette import RESOURCE_PALETTE, STAT_PALETTE
from .base import ResourceEditor


RESOURCE_FIELDS = ("mental_health", "drug_health")


class CharacterEditor(ResourceEditor):
    """Editable character definition backed by its campaign JSON file."""

    resource_label = "Character"
    resource_type = "characters"

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self.stat_inputs: dict[str, QSpinBox] = {}
        self.resource_inputs: dict[str, QSpinBox] = {}
        self._build_ui()
        self._populate_fields()
        self._update_derived_stats()
        self._mark_clean()

    @property
    def directory(self) -> Path:
        return self.campaign.character_path

    @property
    def character_name(self) -> str:
        """
        Return the character's name as entered in the editor.
        """
        return self.name_input.text().strip()

    def _new_data(self) -> dict:
        return {"stats": {}}

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        name_form = QFormLayout()
        name_form.setContentsMargins(0, 0, 0, 0)
        self.name_input = QLineEdit()
        self.name_input.setObjectName("CharacterNameField")
        self.name_input.setPlaceholderText("Character name")
        name_form.addRow("Name", self.name_input)
        header.addLayout(name_form, 1)

        derived = QVBoxLayout()
        self.level_label = QLabel()
        self.level_label.setObjectName("CharacterDerivedValue")
        self.hp_label = QLabel()
        self.hp_label.setObjectName("CharacterDerivedValue")
        self.stamina_label = QLabel()
        self.stamina_label.setObjectName("CharacterDerivedValue")
        derived.addWidget(self.level_label)
        derived.addWidget(self.hp_label)
        derived.addWidget(self.stamina_label)
        header.addLayout(derived)
        layout.addLayout(header)

        sections = QHBoxLayout()
        sections.setSpacing(14)

        stats_column = QVBoxLayout()
        stats_group = QGroupBox("Core stats")
        stats_group.setObjectName("CharacterEditorSection")
        stats_grid = QGridLayout(stats_group)
        stats_grid.setHorizontalSpacing(14)
        stats_grid.setVerticalSpacing(8)

        for index, stat_name in enumerate(STAT_NAMES):
            row = index // 2
            column = (index % 2) * 2
            stat_label = QLabel(stat_name.upper())
            stat_label.setStyleSheet(f"color: {STAT_PALETTE[stat_name]}")
            stat_input = self._make_spin_box()
            self.stat_inputs[stat_name] = stat_input
            stats_grid.addWidget(stat_label, row, column)
            stats_grid.addWidget(stat_input, row, column + 1)

        stats_column.addWidget(stats_group)

        resources_group = QGroupBox("Resources")
        resources_group.setObjectName("CharacterEditorSection")
        resources_form = QFormLayout(resources_group)
        for resource_name in RESOURCE_FIELDS:
            resource_label = QLabel(resource_name.replace("_", " ").title())
            resource_label.setStyleSheet(
                f"color: {RESOURCE_PALETTE[resource_name]}"
            )
            resource_input = self._make_spin_box()
            self.resource_inputs[resource_name] = resource_input
            resources_form.addRow(resource_label, resource_input)
        stats_column.addWidget(resources_group)
        stats_column.addStretch(1)
        sections.addLayout(stats_column, 1)

        layout.addLayout(sections, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.save_button = QPushButton("Save Character")
        self.save_button.setObjectName("CharacterEditorSaveButton")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.save_button.clicked.connect(self.save_requested)
        self.name_input.textChanged.connect(self._refresh_modified)
        for spin_box in (*self.stat_inputs.values(), *self.resource_inputs.values()):
            spin_box.valueChanged.connect(self._refresh_modified)
        for stat_input in self.stat_inputs.values():
            stat_input.valueChanged.connect(self._update_derived_stats)

    @staticmethod
    def _make_spin_box() -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(-999, 999)
        return spin_box

    def _populate_fields(self) -> None:
        self.name_input.setText(str(self._data.get("name", "")))
        raw_stats = self._data.get("stats", {})
        if not isinstance(raw_stats, dict):
            raw_stats = {}

        defaults = Stats()
        for stat_name, spin_box in self.stat_inputs.items():
            spin_box.setValue(
                int(raw_stats.get(stat_name, getattr(defaults, stat_name)))
            )
        for resource_name, spin_box in self.resource_inputs.items():
            spin_box.setValue(
                int(raw_stats.get(resource_name, getattr(defaults, resource_name)))
            )

    @Slot()
    def _update_derived_stats(self) -> None:
        total_stats = sum(spin_box.value() for spin_box in self.stat_inputs.values())
        level = (total_stats - 500) // 5
        hit_points = 10 + (
            self.stat_inputs["con"].value() + self.stat_inputs["wis"].value()
        ) // 10
        stamina_ratio = (
            0.5*self.stat_inputs["per"].value() +
            0.35*self.stat_inputs["dex"].value() +
            0.15*self.stat_inputs["agi"].value()
        )
        stamina = int(60 + 0.8*stamina_ratio)
        self.level_label.setText(f"Level {level}")
        self.hp_label.setText(f"HP {hit_points}")
        self.stamina_label.setText(f"Stamina {stamina}")

    def _collect_data(self) -> dict:
        data = deepcopy(self._data)
        raw_stats = data.get("stats")
        stats = dict(raw_stats) if isinstance(raw_stats, dict) else {}
        stats.update(
            {field: spin_box.value() for field, spin_box in self.stat_inputs.items()}
        )
        stats.update(
            {
                field: spin_box.value()
                for field, spin_box in self.resource_inputs.items()
            }
        )
        data["name"] = self.character_name
        data["stats"] = stats
        return data
