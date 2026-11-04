
#-*- coding: utf-8 -*-
"""Editor for campaign entity resources."""

from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4
from pathlib import Path
from os.path import exists
from typing import Optional

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QImage, QFont, QColor, QImageReader
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QVBoxLayout, QWidget, QSizePolicy, QFileDialog
)

from ...assets.campaign import Campaign
from ...runtime.entity import StatsModifier
from ..cards import STAT_NAMES, STAT_PALETTE
from .base import ResourceEditor


TOKEN_SIZE = 64


class EntityEditor(ResourceEditor):
    """Edit persistent settings while preserving the Entity runtime snapshot."""

    resource_label = "Entity"
    resource_type = "entities"

    def __init__(
        self, resource_id: str = "", campaign: Optional[Campaign] = None,
        *, is_new: bool = False, parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self.stat_modifiers_inputs: dict[str, QSpinBox] = {}
        self._build_ui()
        self._populate_fields()
        self._mark_clean()

    @property
    def directory(self) -> Path:
        return self.campaign.entity_path

    @property
    def entity_name(self) -> str:
        return self.name_input.text().strip()

    def _new_data(self) -> dict:
        return {
            "id": str(uuid4()),
            "name": "",
            "character": "",
            "stats_modifier": {},
            "items": [],
            "spells": [],
            "spell_events": [],
            "spell_effects": []
        }

    @staticmethod
    def _resource_names(directory: Path) -> list[str]:
        """Resolve resource names from JSON, falling back to their file names."""
        if not directory.exists():
            return []
        names: set[str] = set()
        for path in directory.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                name = raw.get("name") if isinstance(raw, dict) else None
            except (OSError, ValueError):
                name = None
            names.add(str(name).strip() if name else path.stem.replace("_", " "))
        return sorted(names, key=str.casefold)

    @staticmethod
    def _make_spin_box(minimum: int = -999, maximum: int = 999) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(14)

        header = QHBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setObjectName("EntityNameField")
        self.name_input.setPlaceholderText("Entity name")
        self.character_input = QComboBox()
        self.character_input.setEditable(True)
        self.character_input.addItem("")
        self.character_input.addItems(self._resource_names(self.campaign.character_path))
        form.addRow("Name", self.name_input)
        form.addRow("Base character", self.character_input)
        header.addLayout(form)
        layout = QVBoxLayout()
        label = QLabel("Token")
        layout.addWidget(label)
        self.token_input = QLabel()
        self.token_input.mousePressEvent = self._select_token
        layout.addWidget(self.token_input)
        header.addLayout(layout)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(14)
        stats_group = QGroupBox("Stat modifiers")
        stats_layout = QGridLayout(stats_group)
        stat_names = list(dict.fromkeys(("hp", "stamina", "mental_health", "drug_health", *STAT_NAMES)))
        for index, stat in enumerate(stat_names):
            label = QLabel(stat.upper())
            if stat in STAT_PALETTE:
                label.setStyleSheet(f"color: {STAT_PALETTE[stat]}")
            spin = self._make_spin_box()
            self.stat_modifiers_inputs[stat] = spin
            stats_layout.addWidget(label, index, 0)
            stats_layout.addWidget(spin, index, 1)
            spin.valueChanged.connect(self._refresh_modified)
        grid.addWidget(stats_group, 0, 0, 2, 1)

        inventory_group = QGroupBox("Inventory")
        inventory_layout = QVBoxLayout(inventory_group)
        self.inventory_field = QListWidget()
        inventory_layout.addWidget(self.inventory_field)
        add_item_controls = QHBoxLayout()
        self.item_search_input = QComboBox()
        self.item_search_input.setEditable(True)
        self.item_search_input.addItem("")
        self.item_search_input.addItems(self._resource_names(self.campaign.item_path))
        self.item_search_input.setMinimumWidth(130)
        self.item_quantity_input = self._make_spin_box(1, 999999)
        self.item_quantity_input.setValue(1)
        self.add_item_button = QPushButton("Add")
        self.remove_item_button = QPushButton("Remove")
        for widget in (self.item_search_input, self.item_quantity_input, self.add_item_button, self.remove_item_button):
            add_item_controls.addWidget(widget)
        inventory_layout.addLayout(add_item_controls)
        grid.addWidget(inventory_group, 0, 1)

        spells_group = QGroupBox("Spells")
        spells_layout = QVBoxLayout(spells_group)
        self.spells_field = QListWidget()
        spells_layout.addWidget(self.spells_field)
        spell_controls = QHBoxLayout()
        self.spell_search_input = QComboBox()
        self.spell_search_input.setEditable(True)
        self.spell_search_input.addItem("")
        self.spell_search_input.addItems(self._resource_names(self.campaign.spell_path))
        self.add_spell_button = QPushButton("Add")
        self.remove_spell_button = QPushButton("Remove")
        for widget in (self.spell_search_input, self.add_spell_button, self.remove_spell_button):
            spell_controls.addWidget(widget)
        spells_layout.addLayout(spell_controls)
        grid.addWidget(spells_group, 1, 1)

        # Runtime information is shown here for visibility, not edited here.
        # Spell effects and events are advanced/managed by SessionEditor.
        runtime_group = QGroupBox("Runtime state (read-only)")
        runtime_layout = QVBoxLayout(runtime_group)
        runtime_layout.addWidget(QLabel("Active spell effects"))
        self.spell_effects_field = QListWidget()
        self.spell_effects_field.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        runtime_layout.addWidget(self.spell_effects_field)
        runtime_layout.addWidget(QLabel("Spell events (nb_cast = progression)"))
        self.spell_events_field = QListWidget()
        self.spell_events_field.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        runtime_layout.addWidget(self.spell_events_field)
        grid.addWidget(runtime_group, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 2)
        root.addLayout(grid, 1)

        self.save_button = QPushButton("Save Entity")
        self.save_button.setObjectName("EntityEditorSaveButton")
        root.addWidget(self.save_button)
        self.save_button.clicked.connect(self.save_requested)
        self.name_input.textChanged.connect(self._refresh_modified)
        self.character_input.currentTextChanged.connect(self._refresh_modified)
        self.add_item_button.clicked.connect(self._add_item)
        self.remove_item_button.clicked.connect(self._remove_item)
        self.add_spell_button.clicked.connect(self._add_spell)
        self.remove_spell_button.clicked.connect(self._remove_spell)
        line_edit = self.item_search_input.lineEdit()
        assert line_edit is not None
        line_edit.returnPressed.connect(self._add_item)
        line_edit = self.spell_search_input.lineEdit()
        assert line_edit is not None
        line_edit.returnPressed.connect(self._add_spell)

    def _add_item_row(self, name: str, quantity: int) -> None:
        if not name or quantity <= 0:
            return

        # If the item already exists, increase its quantity.
        for index in range(self.inventory_field.count()):
            row = self.inventory_field.item(index)

            if row.data(Qt.ItemDataRole.UserRole) == name:
                widget = self.inventory_field.itemWidget(row)
                spin = widget.findChild(QSpinBox, "ItemQuantitySpinBox")
                assert spin is not None
                spin.setValue(spin.value() + quantity)
                return

        # Create the list item.
        row = QListWidgetItem()
        row.setData(Qt.ItemDataRole.UserRole, name)

        # Create a container for the item name and quantity.
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(12)

        # Item name.
        label = QLabel(name)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred
        )

        # Item quantity.
        spin = self._make_spin_box(1, 999999)
        spin.setValue(quantity)
        spin.setFixedWidth(90)
        spin.setObjectName("ItemQuantitySpinBox")
        spin.valueChanged.connect(self._refresh_modified)

        layout.addWidget(label, 1)
        layout.addWidget(spin, 0)

        # Insert the widget into the list.
        row.setSizeHint(container.sizeHint())
        self.inventory_field.addItem(row)
        self.inventory_field.setItemWidget(row, container)

    def _add_item(self) -> None:
        name = self.item_search_input.currentText().strip()
        if name:
            self._add_item_row(name, self.item_quantity_input.value())
            self._refresh_modified()

    def _remove_item(self) -> None:
        for row in reversed(self.inventory_field.selectedItems()):
            self.inventory_field.takeItem(self.inventory_field.row(row))
        self._refresh_modified()

    def _add_spell(self) -> None:
        name = self.spell_search_input.currentText().strip()
        if name and name not in self._get_spells():
            self.spells_field.addItem(name)
            self._refresh_modified()

    def _remove_spell(self) -> None:
        for row in reversed(self.spells_field.selectedItems()):
            self.spells_field.takeItem(self.spells_field.row(row))
        self._refresh_modified()

    def _populate_fields(self) -> None:
        self.name_input.setText(str(self._data.get("name") or self.resource_id.replace("_", " ")))
        self.character_input.setCurrentText(str(self._data.get("character", "")))
        pixmap = QPixmap(TOKEN_SIZE, TOKEN_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        circle = QRectF(4, 4, TOKEN_SIZE-8, TOKEN_SIZE-8)
        clip = QPainterPath()
        clip.addEllipse(circle)
        image_path = self.campaign.entity_path / "tokens" / f"{self.entity_name}.webp"
        if not exists(image_path):
            image_path = None
        image = QImage(str(image_path)) if image_path is not None else QImage()
        if not image.isNull():
            side = min(image.width(), image.height())
            source = QRectF((image.width()-side)/2.0, (image.height()-side)/2.0, side, side)
            painter.drawImage(circle, image, source)
        else:
            painter.fillRect(circle, QColor("#2d3440"))
            font = QFont()
            font.setBold(True)
            font.setPixelSize(8)
            painter.setFont(font)
            painter.setPen(QColor("#eef2f7"))
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, "No token")
        painter.setClipping(False)
        painter.end()
        self.token_input.setPixmap(pixmap)
        modifiers = self._data.get("stats_modifier", {})
        if not isinstance(modifiers, dict):
            modifiers = {}
        defaults = StatsModifier()
        for name, spin in self.stat_modifiers_inputs.items():
            spin.setValue(int(modifiers.get(name, getattr(defaults, name, 0))))
        self.inventory_field.clear()
        raw_items = self._data.get("items", self._data.get("inventory", []))
        if isinstance(raw_items, dict):
            raw_items = raw_items.items()
        for item in raw_items or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                self._add_item_row(str(item[0]), int(item[1]))
        self.spells_field.clear()
        for spell in self._data.get("spells", []) or []:
            self.spells_field.addItem(str(spell))
        self._populate_runtime_fields()

    def _select_token(self, _) -> None:
        """
        Select a new token image
        """
        source, _filter = QFileDialog.getOpenFileName(
            self,
            "Import token",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
        )
        if source:
            path = Path(source)
            token_path = self.campaign.entity_path / "tokens"
            previous_limit = QImageReader.allocationLimit()
            QImageReader.setAllocationLimit(0)
            image = QImage(str(path))
            straight = image.convertToFormat(QImage.Format.Format_ARGB32)
            saved = straight.save(str(token_path / f"{self.entity_name.lower().replace(" ", "_")}.webp"), "WEBP", 100)
            if not saved:
                raise OSError(f"Could not import image: {path}")
            QImageReader.setAllocationLimit(previous_limit)
            self._populate_fields()

    def _populate_runtime_fields(self) -> None:
        """Summarize saved runtime values without constructing live objects."""
        self.spell_effects_field.clear()
        for effect in self._data.get("spell_effects", []) or []:
            if not isinstance(effect, dict):
                continue
            duration = effect.get("duration", "?")
            duration_label = "permanent" if isinstance(duration, int) and duration < 0 else f"duration: {duration}"
            self.spell_effects_field.addItem(
                f"{effect.get('effect_type', '?')} · {effect.get('target_stat', '?')} "
                f"({effect.get('delta', '?'):+}) · {duration_label}"
                if isinstance(effect.get("delta"), (int, float)) else
                f"{effect.get('effect_type', '?')} · {effect.get('target_stat', '?')} · {duration_label}"
            )
        self.spell_events_field.clear()
        for event in self._data.get("spell_events", []) or []:
            if isinstance(event, dict):
                status = "finished" if event.get("finished", False) else "ongoing"
                self.spell_events_field.addItem(
                    f"{event.get('spell_id', '?')} · nb_cast: {event.get('nb_cast', 0)} · {status}"
                )

    def _get_items(self) -> list[list]:
        result = []

        for index in range(self.inventory_field.count()):
            row = self.inventory_field.item(index)
            widget = self.inventory_field.itemWidget(row)

            if widget is None:
                continue

            spin = widget.findChild(QSpinBox, "ItemQuantitySpinBox")

            if spin is None:
                continue

            name = row.data(Qt.ItemDataRole.UserRole)
            result.append([name, spin.value()])

        return result

    def _get_spells(self) -> list[str]:
        return [self.spells_field.item(i).text() for i in range(self.spells_field.count())]

    def _collect_data(self) -> dict:
        data = deepcopy(self._data)

        # Remove legacy names.
        data.pop("stats_modifier", None)
        data.pop("stat_modifiers", None)
        data.pop("inventory", None)

        data["name"] = self.entity_name
        data["character"] = self.character_input.currentText().strip()

        data["stats_modifier"] = {
            name: spin.value()
            for name, spin in self.stat_modifiers_inputs.items()
        }

        data["items"] = self._get_items()
        data["spells"] = self._get_spells()

        # Preserve runtime data.
        data.setdefault("spell_events", [])
        data.setdefault("spell_effects", [])

        return data

    def save(self) -> Path:
        if not self.character_input.currentText().strip():
            raise ValueError("A base character is required for an entity.")
        # A session may have updated the runtime snapshot while this editor was
        # open. Copy its latest state before ResourceEditor performs its normal
        # atomic write and reference-renaming workflow. Do not recast any spells.
        runtime_keys = ("id", "spell_events", "spell_effects")
        if self.file_path is not None and self.file_path.is_file():
            current = json.loads(self.file_path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                raise ValueError("Entity JSON must contain an object.")
            for key in runtime_keys:
                if key in current:
                    self._data[key] = deepcopy(current[key])
                else:
                    self._data.pop(key, None)
        saved_path = super().save()
        self._reload_fields()
        self._mark_clean()
        return saved_path
