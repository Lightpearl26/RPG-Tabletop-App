# -*- coding: utf-8 -*-
"""
GM combat controls: the initiative roster and the animated turn reel.

Initiative: the GM adds the combatants one at a time. "Lancer l'initiative"
rolls a d20 for every non-player entity and asks connected players for theirs;
the GM can type or reroll any result. The combat order is the ascending order
of the results (ties: higher AGI first). During a combat, an entity added to
the roster rolls at once and joins the order where its d20 puts it.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..widgets.combat_turn_widget import CombatTurnWidget
from ...runtime.initiative import InitiativeRoster, RollSource, roll_d20
from ...runtime.session import SessionRuntime


ROLE_ID = Qt.ItemDataRole.UserRole
SOURCE_LABELS = {
    RollSource.AUTO: "Auto",
    RollSource.PLAYER: "Joueur",
    RollSource.GM: "MJ",
}


class CombatPanel(QWidget):
    runtime_changed = Signal()
    action_requested = Signal(str, str)
    # Ask a player for their d20: username, entity id
    initiative_requested = Signal(str, str)
    # Withdraw that request: username
    initiative_cancelled = Signal(str)

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.roster = InitiativeRoster()
        # Set by the window: username of the connected player bound to an entity
        self.player_lookup: Callable[[str], Optional[str]] = lambda _entity_id: None
        self._signature: Optional[str] = None
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
        self.tabs.addTab(self._build_initiative_page(), "Initiative")
        root.addWidget(self.tabs, 1)

        self.refresh(reset_list=True)

    # ------------------------------------------------------------------ build
    def _build_initiative_page(self) -> QWidget:
        self.initiative_page = QWidget()
        layout = QVBoxLayout(self.initiative_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        heading = QLabel("PARTICIPANTS / INITIATIVE")
        heading.setObjectName("RuntimeHeading")
        layout.addWidget(heading)
        self.initiative_hint = QLabel()
        self.initiative_hint.setObjectName("RuntimeMuted")
        self.initiative_hint.setWordWrap(True)
        layout.addWidget(self.initiative_hint)

        add_row = QHBoxLayout()
        self.entity_input = QComboBox()
        self.entity_input.setMinimumWidth(150)
        self.add_button = QPushButton("Ajouter")
        self.add_button.clicked.connect(self._add_selected)
        add_row.addWidget(self.entity_input, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)
        self.add_tokens_button = QPushButton("Ajouter les tokens de la carte")
        self.add_tokens_button.setToolTip("Ajoute toutes les entités qui ont un token sur la carte active")
        self.add_tokens_button.clicked.connect(self._add_map_tokens)
        layout.addWidget(self.add_tokens_button)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Entité", "Contrôle", "d20", "État"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 64)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self.table, 1)

        row_buttons = QHBoxLayout()
        self.roll_for_button = QPushButton("Lancer à sa place")
        self.roll_for_button.setToolTip("Lance le d20 pour cette entité (joueur absent ou trop long)")
        self.roll_for_button.clicked.connect(self._roll_for_selected)
        self.ask_again_button = QPushButton("Redemander")
        self.ask_again_button.setToolTip("Renvoie la demande de d20 au joueur")
        self.ask_again_button.clicked.connect(self._ask_again_selected)
        self.remove_button = QPushButton("Retirer")
        self.remove_button.setObjectName("DangerButton")
        self.remove_button.clicked.connect(self._remove_selected)
        row_buttons.addWidget(self.roll_for_button)
        row_buttons.addWidget(self.ask_again_button)
        row_buttons.addStretch(1)
        row_buttons.addWidget(self.remove_button)
        layout.addLayout(row_buttons)

        self.status_label = QLabel()
        self.status_label.setObjectName("RuntimeMuted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        bottom = QHBoxLayout()
        self.reset_button = QPushButton("Annuler les jets")
        self.reset_button.clicked.connect(self.reset_rolls)
        self.start_button = QPushButton("Lancer l'initiative")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._primary_action)
        bottom.addWidget(self.reset_button)
        bottom.addWidget(self.start_button, 1)
        layout.addLayout(bottom)
        return self.initiative_page

    # --------------------------------------------------------------- helpers
    def _agility(self, entity_id: str) -> int:
        entity = self.runtime.entities.get(entity_id)
        return int(entity.get_stat("agi")) if entity is not None else 0

    def _name(self, entity_id: str) -> str:
        entity = self.runtime.entities.get(entity_id)
        return entity.name if entity is not None else "Entité inconnue"

    def _in_combat(self, entity_id: str) -> bool:
        combat = self.runtime.combat
        return combat is not None and entity_id in combat.participant_ids

    def _selected_id(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return str(item.data(ROLE_ID)) if item is not None else None

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "Initiative", text)

    def _changed(self) -> None:
        self.refresh(reset_list=True)

    # ---------------------------------------------------------------- roster
    def add_entity(self, entity_id: str) -> None:
        """Put an entity in the initiative (and roll at once if rolls already started)."""
        entity_id = str(entity_id)
        if entity_id not in self.runtime.entities:
            return
        if entity_id in self.roster or self._in_combat(entity_id):
            self.status_label.setText(f"{self._name(entity_id)} est déjà dans l'initiative.")
            return
        self.roster.add(entity_id)
        if self.roster.rolling or self.runtime.combat is not None:
            self._roll_entry(entity_id)
        self._changed()

    def _add_selected(self) -> None:
        entity_id = self.entity_input.currentData()
        if entity_id:
            self.add_entity(str(entity_id))

    def _add_map_tokens(self) -> None:
        state = self.runtime.active_map
        if state is None:
            self._warn("Aucune carte active.")
            return
        added = 0
        for token in state.tokens:
            entity_id = str(token.entity_id)
            if entity_id in self.runtime.entities and entity_id not in self.roster and not self._in_combat(entity_id):
                self.roster.add(entity_id)
                if self.roster.rolling or self.runtime.combat is not None:
                    self._roll_entry(entity_id)
                added += 1
        self.status_label.setText(f"{added} entité(s) ajoutée(s) depuis la carte.")
        self._changed()

    def _remove_selected(self) -> None:
        entity_id = self._selected_id()
        if entity_id is None:
            return
        if entity_id in self.roster:
            entry = self.roster.remove(entity_id)
            if entry is not None and entry.waiting_for:
                self.initiative_cancelled.emit(entry.waiting_for)
            self._changed()
            return
        if self._in_combat(entity_id):
            try:
                self.runtime.remove_combat_participant(entity_id)
            except (KeyError, ValueError, RuntimeError) as error:
                self._warn(str(error).strip("'\""))
                return
            self._changed()
            self.runtime_changed.emit()

    # ----------------------------------------------------------------- rolls
    def _roll_entry(self, entity_id: str) -> None:
        """Roll for a non-player entity, ask the player otherwise."""
        username = self.player_lookup(entity_id)
        if username:
            self.roster.wait_for(entity_id, username)
            self.initiative_requested.emit(username, entity_id)
        else:
            self.roster.set_roll(entity_id, roll_d20(), RollSource.AUTO)
            self._join_running_combat(entity_id)

    def _join_running_combat(self, entity_id: str) -> None:
        """During a combat, a rolled roster entry takes its place in the order."""
        entry = self.roster.entry(entity_id)
        if self.runtime.combat is None or entry is None or entry.roll is None:
            return
        try:
            self.runtime.add_combat_participant(entity_id, entry.roll)
        except (KeyError, ValueError, RuntimeError) as error:
            self._warn(str(error).strip("'\""))
            return
        self.roster.remove(entity_id)
        self.status_label.setText(f"{self._name(entity_id)} rejoint le combat (d20 = {entry.roll}).")
        self.runtime_changed.emit()

    def launch_initiative(self) -> None:
        if not self.roster.entries:
            self._warn("Ajoute au moins une entité à l'initiative.")
            return
        self.roster.rolling = True
        for entity_id in self.roster.missing():
            entry = self.roster.entry(entity_id)
            if entry is not None and not entry.waiting_for:
                self._roll_entry(entity_id)
        waiting = [entry for entry in self.roster.entries if entry.waiting_for]
        self.status_label.setText(
            f"En attente de {len(waiting)} joueur(s)." if waiting else "Tous les d20 sont lancés."
        )
        self._changed()

    def reset_rolls(self) -> None:
        for _entity_id, username in self.roster.reset_rolls():
            self.initiative_cancelled.emit(username)
        self.status_label.setText("Jets annulés.")
        self._changed()

    def _roll_for_selected(self) -> None:
        entity_id = self._selected_id()
        entry = self.roster.entry(entity_id) if entity_id else None
        if entry is None:
            return
        if entry.waiting_for:
            self.initiative_cancelled.emit(entry.waiting_for)
        self.roster.set_roll(entry.entity_id, roll_d20(), RollSource.GM)
        self._join_running_combat(entry.entity_id)
        self._changed()

    def _ask_again_selected(self) -> None:
        entity_id = self._selected_id()
        entry = self.roster.entry(entity_id) if entity_id else None
        if entry is None:
            return
        username = self.player_lookup(entry.entity_id)
        if not username:
            self._warn("Aucun joueur connecté ne contrôle cette entité.")
            return
        self.roster.wait_for(entry.entity_id, username)
        self.initiative_requested.emit(username, entry.entity_id)
        self._changed()

    def _gm_roll_changed(self, entity_id: str, value: int) -> None:
        entry = self.roster.entry(entity_id)
        if entry is None:
            return
        if value <= 0:
            if entry.roll is not None:
                entry.roll, entry.source = None, None
                self._changed()
            return
        if entry.waiting_for:
            self.initiative_cancelled.emit(entry.waiting_for)
        self.roster.set_roll(entity_id, value, RollSource.GM)
        self._join_running_combat(entity_id)
        self._changed()

    def receive_player_roll(self, entity_id: str, value: int, username: str) -> None:
        """A player sent their d20 (through the network bridge)."""
        entry = self.roster.entry(str(entity_id))
        if entry is None or entry.waiting_for != str(username):
            return  # the GM rolled meanwhile, or removed the entity
        try:
            self.roster.set_roll(entry.entity_id, value, RollSource.PLAYER)
        except ValueError:
            return
        self.status_label.setText(f"{username} a fait {int(value)} pour {self._name(entry.entity_id)}.")
        self._join_running_combat(entry.entity_id)
        self._changed()

    # ---------------------------------------------------------------- combat
    def _primary_action(self) -> None:
        if self.runtime.combat is not None:
            return
        if self.roster.rolling and self.roster.complete:
            self.start_combat()
        else:
            self.launch_initiative()

    def start_combat(self) -> None:
        if not self.roster.complete:
            self._warn("Il manque des d20 : attends les joueurs ou lance à leur place.")
            return
        try:
            self.runtime.start_combat_with_initiative(self.roster.rolls())
        except Exception as exc:
            QMessageBox.critical(self, "Combat", str(exc))
            return
        self.roster.clear()
        self.status_label.setText("")
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
        for _entity_id, username in self.roster.reset_rolls():
            self.initiative_cancelled.emit(username)
        self.roster.clear()
        self.refresh(reset_list=True)
        self.runtime_changed.emit()
        self.tabs.setCurrentWidget(self.initiative_page)

    # --------------------------------------------------------------- refresh
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

    def _rows(self) -> list[dict[str, Any]]:
        """What the initiative table shows, in turn order."""
        rows: list[dict[str, Any]] = []
        combat = self.runtime.combat
        if combat is not None:
            for entity_id in combat.initiative_order:
                roll = combat.initiative_rolls.get(entity_id)
                current = entity_id == combat.current_entity_id
                rows.append({
                    "id": entity_id,
                    "roll": roll,
                    "state": "▶ Son tour" if current else "En combat",
                    "editable": False,
                })
        for entity_id in self.roster.order(self._agility, self._name):
            entry = self.roster.entry(entity_id)
            assert entry is not None
            if entry.waiting_for:
                connected = self.player_lookup(entity_id) == entry.waiting_for
                state = f"En attente de {entry.waiting_for}" if connected else f"{entry.waiting_for} déconnecté"
            elif entry.roll is not None:
                state = SOURCE_LABELS.get(entry.source, "") if entry.source else ""
            else:
                state = "À lancer" if combat is None else "Rejoint le combat"
            rows.append({"id": entity_id, "roll": entry.roll, "state": state, "editable": True})
        return rows

    def _refresh_table(self) -> None:
        rows = self._rows()
        signature = json.dumps(
            [rows, {entity_id: self.player_lookup(entity_id) for entity_id in self.runtime.entities},
             sorted((i, e.name) for i, e in self.runtime.entities.items())],
            sort_keys=True, default=str,
        )
        if signature == self._signature:
            return
        self._signature = signature
        selected = self._selected_id()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            entity_id = row["id"]
            name = QTableWidgetItem(self._name(entity_id))
            name.setData(ROLE_ID, entity_id)
            self.table.setItem(index, 0, name)
            player = self.player_lookup(entity_id)
            self.table.setItem(index, 1, QTableWidgetItem(player or "PNJ"))
            spin = QSpinBox()
            spin.setRange(0, 20)
            spin.setSpecialValueText("—")
            spin.setKeyboardTracking(False)
            spin.setValue(int(row["roll"] or 0))
            spin.setEnabled(bool(row["editable"]))
            spin.setToolTip("Résultat du d20 (modifiable par le MJ)")
            spin.valueChanged.connect(lambda value, entity_id=entity_id: self._gm_roll_changed(entity_id, value))
            self.table.setCellWidget(index, 2, spin)
            self.table.setItem(index, 3, QTableWidgetItem(row["state"]))
            if entity_id == selected:
                self.table.selectRow(index)

    def _refresh_entity_choices(self) -> None:
        current = self.entity_input.currentData()
        self.entity_input.blockSignals(True)
        self.entity_input.clear()
        for entity_id, entity in sorted(self.runtime.entities.items(), key=lambda pair: pair[1].name.casefold()):
            if entity_id in self.roster or self._in_combat(entity_id):
                continue
            player = self.player_lookup(entity_id)
            self.entity_input.addItem(f"{entity.name} ({player})" if player else entity.name, entity_id)
        index = self.entity_input.findData(current)
        self.entity_input.setCurrentIndex(max(0, index))
        self.entity_input.blockSignals(False)

    def _sync_buttons(self) -> None:
        active = self.runtime.combat is not None
        entity_id = self._selected_id()
        entry = self.roster.entry(entity_id) if entity_id else None
        self.add_button.setEnabled(self.entity_input.count() > 0)
        self.roll_for_button.setEnabled(entry is not None and (self.roster.rolling or active))
        self.ask_again_button.setEnabled(
            entry is not None and bool(self.player_lookup(entry.entity_id)) and (self.roster.rolling or active)
        )
        self.remove_button.setEnabled(
            entity_id is not None
            and (entry is not None or (active and entity_id != self.runtime.combat.current_entity_id))
        )
        self.reset_button.setEnabled(not active and self.roster.rolling)
        self.start_button.setEnabled(not active and bool(self.roster.entries))
        if active:
            self.start_button.setText("Combat en cours")
        elif self.roster.rolling and self.roster.complete:
            self.start_button.setText("Démarrer le combat")
        elif self.roster.rolling:
            self.start_button.setText(f"En attente de {len(self.roster.missing())} d20…")
            self.start_button.setEnabled(False)
        else:
            self.start_button.setText("Lancer l'initiative")

    def _refresh_turn_actions(self, entity_id: str) -> None:
        """Strike, shoot and spells of the entity whose turn it is: the GM makes it act."""
        try:
            actions = self.runtime.available_actions(entity_id)
        except KeyError:
            actions = {}
        spells = [
            {"id": name, "name": name, "cost": info.get("cost", 0), "available": info.get("available", False)}
            for name, info in actions.get("spells", {}).items()
        ]
        strike = actions.get("strike", {})
        shoot = actions.get("shoot", {})
        self.turn_widget.set_action_options(
            strike_cost=int(strike.get("cost", 60)),
            strike_available=bool(strike.get("available", False)),
            shoot_cost=int(shoot.get("cost", 60)),
            shoot_available=bool(shoot.get("available", False)),
            spells=spells,
        )
        player = self.player_lookup(entity_id) if self.player_lookup is not None else None
        stamina = int(actions.get("stamina_remaining", 0))
        if player:
            hint = f"Tour de {player} : il agit depuis son client (tu peux aussi agir pour lui)."
        else:
            hint = f"Fais agir l'entité : choisis l'action puis la cible sur la carte. Budget : {stamina}."
        self.turn_widget.set_permissions(
            connected=True,
            can_act=bool(actions.get("combat_actions_enabled", False)),
            can_end_turn=True,
            action_hint=hint,
        )

    def refresh(self, *, reset_list: bool = False) -> None:
        combat = self.runtime.combat
        self.roster.keep_only(self.runtime.entities)
        if reset_list:
            self._signature = None

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
            self._refresh_turn_actions(combat.current_entity_id or "")
            self.initiative_hint.setText(
                "Combat en cours. Une entité ajoutée lance son d20 et prend sa place dans "
                "l'ordre : après le tour actuel elle joue ce round, avant elle joue au suivant."
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
            self.initiative_hint.setText(
                "Ajoute les combattants un à un. Au lancement, chacun fait un d20 : "
                "automatiquement pour les PNJ, depuis leur client pour les joueurs connectés. "
                "Le plus petit résultat joue en premier (égalité : la plus haute AGI)."
            )

        self.end_combat_button.setEnabled(active)
        self._refresh_entity_choices()
        self._refresh_table()
        self._sync_buttons()
