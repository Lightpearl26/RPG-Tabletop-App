# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from rpg_tabletop.net.models import PendingKind, PendingRequest
from rpg_tabletop.session.session import SessionRuntime


KIND_LABELS = {"strike": "Strike", "shoot": "Shoot"}


def describe(pending: PendingRequest, runtime: SessionRuntime) -> str:
    """One line for the request list: who does what."""
    entity = runtime.entities.get(pending.entity_id)
    actor = entity.name if entity is not None else pending.entity_id
    kind = str(pending.preview.get("kind", ""))
    if pending.kind is PendingKind.ACTION:
        what = f"sort {pending.preview.get('spell')}" if kind == "spell" else KIND_LABELS.get(kind, kind)
        text = f"{actor} — {what} (d100)"
    elif pending.kind is PendingKind.DAMAGE:
        text = f"{actor} — dégâts {pending.preview.get('damage', '')}"
    else:
        text = f"{actor} — {pending.kind.value}"
    waiting = sorted({die.owner for die in pending.proposed_dice.values() if die.awaiting and die.owner})
    if waiting:
        text += f" · attend {', '.join(waiting)}"
    return text


class PendingPanel(QWidget):
    approve_requested = Signal(str, object, object)
    reject_requested = Signal(str, str)
    reroll_requested = Signal(str, str)

    def __init__(self, runtime: SessionRuntime, *, actions_executable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.actions_executable = actions_executable
        self._pending: dict[str, PendingRequest] = {}
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        title = QLabel("DEMANDES À VALIDER")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show_current)
        root.addWidget(self.list, 1)
        self.summary = QLabel("Aucune demande en attente")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.targets = QListWidget()
        self.targets.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        root.addWidget(self.targets)
        self.dice = QTableWidget(0, 6)
        self.dice.setHorizontalHeaderLabels(["Clé", "Libellé", "Expression", "Valeurs", "Total", "Lancé par"])
        self.dice.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.dice)
        self.resolver_note = QLabel("")
        self.resolver_note.setObjectName("RuntimeMuted")
        self.resolver_note.setWordWrap(True)
        root.addWidget(self.resolver_note)
        row = QHBoxLayout()
        self.reroll_button = QPushButton("Relancer le dé sélectionné")
        self.approve_button = QPushButton("Approuver")
        self.approve_button.setObjectName("PrimaryButton")
        self.reject_button = QPushButton("Refuser")
        self.reject_button.setObjectName("DangerButton")
        self.reroll_button.clicked.connect(self._reroll)
        self.approve_button.clicked.connect(self._approve)
        self.reject_button.clicked.connect(self._reject)
        row.addWidget(self.reroll_button)
        row.addStretch(1)
        row.addWidget(self.reject_button)
        row.addWidget(self.approve_button)
        root.addLayout(row)
        self._set_controls(False)

    def add_or_update(self, pending: PendingRequest) -> None:
        self._pending[pending.approval_id] = pending
        existing = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == pending.approval_id:
                existing = item
                break
        label = describe(pending, self.runtime)
        if existing is None:
            existing = QListWidgetItem(label)
            existing.setData(Qt.ItemDataRole.UserRole, pending.approval_id)
            self.list.addItem(existing)
        else:
            existing.setText(label)
        if self.list.currentItem() is existing:
            self._show_current()  # a player just sent a die: show it
        else:
            self.list.setCurrentItem(existing)

    def remove(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)
        for i in range(self.list.count() - 1, -1, -1):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == approval_id:
                self.list.takeItem(i)
        self._show_current()

    def current_pending(self) -> PendingRequest | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return self._pending.get(str(item.data(Qt.ItemDataRole.UserRole)))

    def _set_controls(self, enabled: bool) -> None:
        self.targets.setEnabled(enabled)
        self.dice.setEnabled(enabled)
        self.reroll_button.setEnabled(enabled)
        self.approve_button.setEnabled(enabled)
        self.reject_button.setEnabled(enabled)

    def _show_current(self, *_args) -> None:
        pending = self.current_pending()
        if pending is None:
            self.summary.setText("Aucune demande en attente")
            self.targets.clear()
            self.dice.setRowCount(0)
            self.resolver_note.setText("")
            self._set_controls(False)
            return
        lines = [describe(pending, self.runtime), f"Demandé par : {pending.username}"]
        names = [
            self.runtime.entities[entity_id].name
            for entity_id in pending.preview.get("target_entities", [])
            if entity_id in self.runtime.entities
        ]
        if names:
            lines.append(f"Touche : {', '.join(names)}")
        if pending.preview.get("stamina_cost") is not None:
            lines.append(
                f"Coût : {pending.preview['stamina_cost']} STA "
                f"(reste {pending.preview.get('stamina_remaining', '?')} ce tour)"
            )
        self.summary.setText("\n".join(lines))
        self._populate_targets(pending)
        self._populate_dice(pending)
        waiting = sorted({die.owner for die in pending.proposed_dice.values() if die.awaiting and die.owner})
        if waiting:
            self.resolver_note.setText(
                f"En attente des dés de {', '.join(waiting)}. Approuver maintenant garde les "
                "valeurs tirées par l'app à leur place."
            )
        elif pending.kind is PendingKind.DAMAGE and pending.proposed_dice.get("damage") and pending.proposed_dice["damage"].owner:
            self.resolver_note.setText("Refuser demande au joueur de relancer ses dés de dégâts.")
        else:
            self.resolver_note.setText("")
        self._set_controls(True)
        self.approve_button.setEnabled(True)
        self.reroll_button.setEnabled(bool(pending.proposed_dice))
        self.targets.setEnabled(pending.kind is PendingKind.ACTION)
        self.dice.setEnabled(bool(pending.proposed_dice))

    def _populate_targets(self, pending: PendingRequest) -> None:
        self.targets.clear()
        if pending.kind is not PendingKind.ACTION or self.runtime.active_map is None:
            self.targets.setVisible(False)
            return
        self.targets.setVisible(True)
        proposed = set(pending.proposed_targets)
        for token in self.runtime.active_map.tokens:
            entity = self.runtime.entities.get(token.entity_id)
            label = entity.name if entity else token.entity_id
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, token.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if token.id in proposed else Qt.CheckState.Unchecked)
            self.targets.addItem(item)

    def _populate_dice(self, pending: PendingRequest) -> None:
        entries = list(pending.proposed_dice.items())
        self.dice.setRowCount(len(entries))
        for row, (key, die) in enumerate(entries):
            vals = ",".join(str(v) for v in die.values)
            owner = die.owner or "App"
            if die.awaiting:
                owner += " (en attente)"
            for col, value in enumerate((key, die.label, die.expression, vals, str(die.total), owner)):
                item = QTableWidgetItem(value)
                if col in (0, 1, 4, 5):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.dice.setItem(row, col, item)
        self.dice.resizeColumnsToContents()

    def _collect_edits(self) -> tuple[list[str] | None, dict[str, dict[str, Any]] | None]:
        pending = self.current_pending()
        if pending is None:
            return None, None
        targets = None
        if pending.kind is PendingKind.ACTION:
            targets = []
            for i in range(self.targets.count()):
                item = self.targets.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    targets.append(str(item.data(Qt.ItemDataRole.UserRole)))
        dice: dict[str, dict[str, Any]] = {}
        for row in range(self.dice.rowCount()):
            key_item = self.dice.item(row, 0)
            expr_item = self.dice.item(row, 2)
            values_item = self.dice.item(row, 3)
            if not key_item or not expr_item or not values_item:
                continue
            try:
                values = [int(x.strip()) for x in values_item.text().split(",") if x.strip()]
            except ValueError:
                raise ValueError(f"Valeurs de dés invalides pour {key_item.text()}")
            dice[key_item.text()] = {"expression": expr_item.text().strip(), "values": values}
        return targets, dice or None

    def _approve(self) -> None:
        pending = self.current_pending()
        if pending is None:
            return
        try:
            targets, dice = self._collect_edits()
        except ValueError as exc:
            QMessageBox.warning(self, "Demande joueur", str(exc))
            return
        self.approve_requested.emit(pending.approval_id, targets, dice)

    def _reject(self) -> None:
        pending = self.current_pending()
        if pending is None:
            return
        self.reject_requested.emit(pending.approval_id, "")

    def _reroll(self) -> None:
        pending = self.current_pending()
        row = self.dice.currentRow()
        if pending is None or row < 0:
            return
        item = self.dice.item(row, 0)
        if item is not None:
            self.reroll_requested.emit(pending.approval_id, item.text())
