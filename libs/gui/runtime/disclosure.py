# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from ...runtime.session import SessionRuntime


DISCLOSABLE_FIELDS = (
    "name", "hp", "stamina", "mental_health", "drug_health",
    "str", "con", "dex", "agi", "int", "wis", "cha", "luc", "sur", "per",
)


class TokenDisclosureDialog(QDialog):
    def __init__(self, runtime: SessionRuntime, token_id: str, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.token_id = token_id
        self.setWindowTitle("Player-visible token information")
        self.resize(360, 520)
        state = runtime.active_map
        token = state.token_by_id(token_id) if state else None
        if token is None:
            raise KeyError(token_id)
        self.token = token
        entity = runtime.entities.get(token.entity_id)
        root = QVBoxLayout(self)
        title = QLabel(entity.name if entity else token.entity_id)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(title)
        hint = QLabel("Checked fields are the only entity data sent to players who can currently see this token.")
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(hint)
        form = QFormLayout()
        self.checks: dict[str, QCheckBox] = {}
        for name in DISCLOSABLE_FIELDS:
            check = QCheckBox(name.upper())
            check.setChecked(name in token.disclosed_fields)
            self.checks[name] = check
            value = ""
            if entity is not None:
                value = entity.name if name == "name" else str(entity.get_stat(name))
            form.addRow(check, QLabel(value))
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_fields(self) -> list[str]:
        return [name for name, check in self.checks.items() if check.isChecked()]
