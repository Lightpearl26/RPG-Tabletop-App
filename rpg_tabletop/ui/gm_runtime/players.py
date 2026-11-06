# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.session.runtime import SessionRuntime


class PlayerAssignmentPanel(QWidget):
    """Liste les joueurs connectés et associe chacun à une Entity de session."""

    bind_requested = Signal(str, str)

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._server_running = False
        self.setObjectName("RuntimePanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("AFFECTATION DES JOUEURS")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)

        self.status = QLabel("Démarre le serveur pour afficher les joueurs connectés.")
        self.status.setObjectName("RuntimeMuted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.players = QTableWidget(0, 3)
        self.players.setHorizontalHeaderLabels(["Joueur", "Ping", "Entity liée"])
        self.players.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.players.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.players.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.players.verticalHeader().setVisible(False)
        self.players.horizontalHeader().setStretchLastSection(True)
        self.players.itemSelectionChanged.connect(self._update_controls)
        root.addWidget(self.players, 1)

        root.addWidget(QLabel("Associer le joueur sélectionné à une Entity de la session"))
        binding = QHBoxLayout()
        self.entity_combo = QComboBox()
        self.entity_combo.setEnabled(False)
        self.entity_combo.currentIndexChanged.connect(self._update_controls)
        self.bind_button = QPushButton("Affecter")
        self.bind_button.setObjectName("PrimaryButton")
        self.bind_button.setEnabled(False)
        self.bind_button.clicked.connect(self._bind)
        binding.addWidget(self.entity_combo, 1)
        binding.addWidget(self.bind_button)
        root.addLayout(binding)

        hint = QLabel(
            "La liste des Entities provient exclusivement de la session en cours. "
            "Un joueur ne choisit jamais lui-même son Entity."
        )
        hint.setObjectName("RuntimeMuted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.refresh_entities()
        self._update_controls()

    def refresh_entities(self) -> None:
        current = self.entity_combo.currentData()
        self.entity_combo.blockSignals(True)
        self.entity_combo.clear()
        for entity_id, entity in sorted(
            self.runtime.entities.items(), key=lambda item: item[1].name.casefold()
        ):
            self.entity_combo.addItem(entity.name, entity_id)
        if current:
            index = self.entity_combo.findData(current)
            if index >= 0:
                self.entity_combo.setCurrentIndex(index)
        self.entity_combo.blockSignals(False)
        self._update_controls()

    def set_server_running(self, running: bool) -> None:
        self._server_running = bool(running)
        if not self._server_running:
            self.status.setText("Serveur arrêté — aucune affectation possible.")
        self._update_controls()

    def refresh_players(self, server) -> None:
        selected_username = None
        selected_row = self.players.currentRow()
        if selected_row >= 0:
            item = self.players.item(selected_row, 0)
            if item is not None:
                selected_username = item.text()

        if server is None:
            clients = []
        else:
            try:
                clients = list(server.clients.items())
            except RuntimeError:
                clients = []

        self.status.setText(
            f"{len(clients)} joueur(s) connecté(s). "
            "Sélectionne un joueur pour modifier son Entity liée."
            if clients
            else (
                "Aucun joueur connecté. Les affectations seront disponibles "
                "lorsqu'un joueur rejoindra la session."
                if self._server_running
                else "Serveur arrêté — aucune affectation possible."
            )
        )

        self.players.setRowCount(len(clients))
        for row, (username, client) in enumerate(clients):
            entity = self.runtime.entities.get(client.bound_entity_id or "")
            entity_name = entity.name if entity is not None else "Non affectée"
            ping = "—" if client.ping_ms is None else f"{int(client.ping_ms)} ms"
            values = (username, ping, entity_name)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, username)
                self.players.setItem(row, column, item)
            if username == selected_username:
                self.players.selectRow(row)

        self._update_controls()

    def _update_controls(self, *_args) -> None:
        has_player = self.players.currentRow() >= 0
        has_entity = self.entity_combo.currentData() is not None
        enabled = self._server_running and has_player and has_entity
        self.entity_combo.setEnabled(self._server_running and has_player and has_entity)
        self.bind_button.setEnabled(enabled)

    def _bind(self) -> None:
        row = self.players.currentRow()
        entity_id = self.entity_combo.currentData()
        if row < 0 or entity_id is None:
            return
        item = self.players.item(row, 0)
        if item is None:
            return
        self.bind_requested.emit(item.text(), str(entity_id))
