# -*- coding: utf-8 -*-
"""GM combat controls and the shared animated initiative reel."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..widgets.combat_turn_widget import CombatTurnWidget
from ...runtime.session import SessionRuntime


class CombatPanel(QWidget):
    runtime_changed = Signal()
    action_requested = Signal(str, str)

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._initiative_ids: list[str] = []
        self.setObjectName("RuntimePanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.tabs = QTabWidget()
        self.combat_page = QWidget()
        combat_layout = QVBoxLayout(self.combat_page)
        combat_layout.setContentsMargins(0, 0, 0, 0)
        combat_layout.setSpacing(8)

        self.turn_widget = CombatTurnWidget(self.combat_page)
        self.turn_widget.end_turn_requested.connect(self.next_turn)
        self.turn_widget.strike_requested.connect(lambda: self.action_requested.emit("strike", ""))
        self.turn_widget.shoot_requested.connect(lambda: self.action_requested.emit("shoot", ""))
        self.turn_widget.spell_requested.connect(lambda spell: self.action_requested.emit("spell", spell))
        combat_layout.addWidget(self.turn_widget)

        self.combat_state_label = QLabel("Le combat n'a pas commencé.")
        self.combat_state_label.setObjectName("RuntimeMuted")
        self.combat_state_label.setWordWrap(True)
        combat_layout.addWidget(self.combat_state_label)

        self.end_combat_button = QPushButton("Terminer le combat")
        self.end_combat_button.setObjectName("DangerButton")
        self.end_combat_button.clicked.connect(self.end_combat)
        combat_layout.addWidget(self.end_combat_button)
        combat_layout.addStretch(1)
        self.tabs.addTab(self.combat_page, "Combat")

        self.initiative_page = QWidget()
        initiative_layout = QVBoxLayout(self.initiative_page)
        initiative_layout.setContentsMargins(0, 0, 0, 0)
        initiative_layout.setSpacing(7)
        heading = QLabel("PARTICIPANTS / INITIATIVE")
        heading.setObjectName("RuntimeHeading")
        initiative_layout.addWidget(heading)
        self.initiative_hint = QLabel(
            "Coche les entités de la session et organise leur ordre avant de lancer le combat."
        )
        self.initiative_hint.setObjectName("RuntimeMuted")
        self.initiative_hint.setWordWrap(True)
        initiative_layout.addWidget(self.initiative_hint)

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        initiative_layout.addWidget(self.list, 1)

        move_buttons = QHBoxLayout()
        self.up_button = QPushButton("Monter")
        self.down_button = QPushButton("Descendre")
        self.up_button.clicked.connect(lambda: self._move_item(-1))
        self.down_button.clicked.connect(lambda: self._move_item(1))
        move_buttons.addWidget(self.up_button)
        move_buttons.addWidget(self.down_button)
        initiative_layout.addLayout(move_buttons)

        self.start_button = QPushButton("Démarrer le combat")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.start_combat)
        initiative_layout.addWidget(self.start_button)
        self.tabs.addTab(self.initiative_page, "Initiative")
        root.addWidget(self.tabs, 1)

        self.refresh(reset_list=True)

    def _move_item(self, delta: int) -> None:
        if self.runtime.combat is not None:
            return
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def _populate_initiative(self, *, reset_list: bool) -> None:
        combat = self.runtime.combat
        order = list(combat.initiative_order) if combat is not None else list(self.runtime.entities)
        seen = set(order)
        order.extend(entity_id for entity_id in self.runtime.entities if entity_id not in seen)
        order = [entity_id for entity_id in order if entity_id in self.runtime.entities]
        if not reset_list and self._initiative_ids == order:
            return
        self._initiative_ids = list(order)
        self.list.clear()
        for entity_id in order:
            entity = self.runtime.entities.get(entity_id)
            if entity is None:
                continue
            item = QListWidgetItem(entity.name)
            item.setData(Qt.ItemDataRole.UserRole, entity_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(
                Qt.CheckState.Checked
                if combat is None or entity_id in combat.participant_ids
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)

    def _carousel_items(self) -> list[dict[str, Any]]:
        combat = self.runtime.combat
        if combat is None or not combat.initiative_order:
            return []
        order = list(combat.initiative_order)
        try:
            current_index = order.index(combat.current_entity_id)
        except ValueError:
            current_index = max(0, min(len(order) - 1, int(combat.turn_index)))

        result: list[dict[str, Any]] = []
        for offset in (-1, 0, 1):
            entity_id = order[(current_index + offset) % len(order)]
            entity = self.runtime.entities.get(entity_id)
            if entity is None:
                result.append({"id": entity_id, "name": "Entité inconnue"})
                continue
            item: dict[str, Any] = {
                "id": entity_id,
                "name": entity.name,
                "subtitle": getattr(entity.character, "name", ""),
            }
            if offset == 0 and combat.turn_state is not None:
                turn = combat.turn_state
                item.update({
                    "stamina_remaining": turn.stamina_remaining,
                    "stamina_max": turn.stamina_max,
                    "movement_remaining": turn.movement_remaining,
                    "movement_max": turn.movement_max,
                })
            result.append(item)
        return result

    def refresh(self, *, reset_list: bool = False) -> None:
        combat = self.runtime.combat
        self._populate_initiative(reset_list=reset_list)

        active = combat is not None
        if active:
            current = self.runtime.entities.get(combat.current_entity_id or "")
            current_name = current.name if current is not None else "Entité inconnue"
            self.combat_state_label.setText(
                f"Round {combat.round} · Tour de {current_name} · "
                f"{len(combat.participant_ids)} participant(s)"
            )
            self.turn_widget.set_combat_state({
                "active": True,
                "round": combat.round,
                "turn_key": f"{combat.round}:{combat.turn_index}",
                "carousel": self._carousel_items(),
                "own_turn": False,
                "player_view": False,
            })
            current_entity = self.runtime.entities.get(combat.current_entity_id or "")
            spells = []
            if current_entity is not None:
                spells = [
                    {
                        "id": name,
                        "name": name,
                        "cost": int(getattr(spell, "cost", 0) or 0),
                        "available": False,
                    }
                    for name, spell in current_entity.spells.items()
                ]
            self.turn_widget.set_action_options(spells=spells)
            self.turn_widget.set_permissions(
                connected=True,
                can_act=False,
                can_end_turn=True,
                action_hint="Les actions directes MJ seront reliées au resolver de combat."
            )
        else:
            self.combat_state_label.setText("Le combat n'a pas commencé.")
            self.turn_widget.set_combat_state({"active": False}, animate=False)
            self.turn_widget.set_action_options(spells=[])
            self.turn_widget.set_permissions(
                connected=True,
                can_act=False,
                can_end_turn=False,
            )

        self.end_combat_button.setEnabled(active)
        self.start_button.setEnabled(not active and bool(self.runtime.entities))
        self.list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
            if not active else QAbstractItemView.DragDropMode.NoDragDrop
        )
        self.list.setEnabled(not active)
        self.up_button.setEnabled(not active)
        self.down_button.setEnabled(not active)
        self.initiative_hint.setText(
            "L'ordre est verrouillé pendant le combat."
            if active else
            "Coche les entités de la session et organise leur ordre avant de lancer le combat."
        )

    def _checked_order(self) -> list[str]:
        result: list[str] = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return result

    def start_combat(self) -> None:
        participants = self._checked_order()
        if not participants:
            QMessageBox.warning(self, "Combat", "Sélectionne au moins un participant.")
            return
        try:
            self.runtime.start_combat(participants, initiative_order=participants)
        except Exception as exc:
            QMessageBox.critical(self, "Combat", str(exc))
            return
        self.refresh(reset_list=True)
        self.runtime_changed.emit()
        self.tabs.setCurrentWidget(self.combat_page)

    def next_turn(self) -> None:
        try:
            self.runtime.next_turn()
        except Exception as exc:
            QMessageBox.critical(self, "Combat", str(exc))
            return
        self.refresh(reset_list=False)
        self.runtime_changed.emit()

    def end_combat(self) -> None:
        try:
            self.runtime.end_combat()
        except Exception as exc:
            QMessageBox.critical(self, "Combat", str(exc))
            return
        self.refresh(reset_list=True)
        self.runtime_changed.emit()
        self.tabs.setCurrentWidget(self.initiative_page)
