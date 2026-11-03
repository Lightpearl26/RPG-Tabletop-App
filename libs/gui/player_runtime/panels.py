# -*- coding: utf-8 -*-
"""Player sheet, action controls and chat panels."""
from __future__ import annotations

from html import escape
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QScrollArea, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ...net import ActionIntent, ActionKind, TargetSelection


STAT_ORDER = (
    "str", "con", "agi", "dex", "int", "wis", "cha", "luc", "sur", "per",
    "hp", "stamina", "mental_health", "drug_health",
)
STAT_LABELS = {
    "str": "STR", "con": "CON", "agi": "AGI", "dex": "DEX",
    "int": "INT", "wis": "WIS", "cha": "CHA", "luc": "LUC",
    "sur": "SUR", "per": "PER", "hp": "HP", "stamina": "Stamina",
    "mental_health": "Santé mentale", "drug_health": "Santé toxique",
}
STAT_COLORS = {
    "str": "#E35D6A", "con": "#E39A4A", "dex": "#4C8ED9", "agi": "#43B6D7",
    "int": "#6E68D8", "wis": "#9B6BDB", "cha": "#D76AAE", "luc": "#D8B84E",
    "sur": "#58C26A", "per": "#2F7D5A",
}


class CharacterPanel(QWidget):
    """Read-only view of the local player's own Entity projection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self.name_label = QLabel("En attente de ton Entity")
        self.name_label.setObjectName("PlayerTitle")
        root.addWidget(self.name_label)
        self.character_label = QLabel("Aucune Entity liée par le MJ")
        self.character_label.setObjectName("PlayerMuted")
        root.addWidget(self.character_label)

        self.resource_group = QGroupBox("Ressources")
        self.resource_form = QFormLayout(self.resource_group)
        self.resource_values: dict[str, QLabel] = {}
        for key, label in (("hp", "HP"), ("stamina", "Stamina"),
                           ("mental_health", "Santé mentale"), ("drug_health", "Santé toxique")):
            value = QLabel("—")
            self.resource_values[key] = value
            self.resource_form.addRow(label, value)
        root.addWidget(self.resource_group)

        self.tabs = QTabWidget()
        stats_page = QWidget()
        stats_layout = QGridLayout(stats_page)
        stats_layout.setContentsMargins(8, 8, 8, 8)
        stats_layout.setHorizontalSpacing(14)
        stats_layout.setVerticalSpacing(7)
        self.stat_values: dict[str, QLabel] = {}
        for index, stat in enumerate(STAT_ORDER):
            if stat in {"hp", "stamina", "mental_health", "drug_health"}:
                continue
            label = QLabel(STAT_LABELS[stat])
            label.setStyleSheet(f"color: {STAT_COLORS[stat]}; font-weight: 700;")
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.stat_values[stat] = value
            row, col = divmod(len(self.stat_values) - 1, 2)
            stats_layout.addWidget(label, row, col * 2)
            stats_layout.addWidget(value, row, col * 2 + 1)
        stats_layout.setColumnStretch(1, 1)
        stats_layout.setColumnStretch(3, 1)
        self.tabs.addTab(stats_page, "Caractéristiques")

        self.items_list = QListWidget()
        self.tabs.addTab(self.items_list, "Inventaire")
        self.spells_list = QListWidget()
        self.tabs.addTab(self.spells_list, "Sorts")
        root.addWidget(self.tabs, 1)

        self.effects_label = QLabel("Effets actifs : —")
        self.effects_label.setWordWrap(True)
        self.effects_label.setObjectName("PlayerMuted")
        root.addWidget(self.effects_label)

    def set_entity(self, entity: dict[str, Any] | None) -> None:
        if not isinstance(entity, dict):
            self.name_label.setText("En attente de ton Entity")
            self.character_label.setText("Le MJ doit encore lier une Entity à ton joueur.")
            for label in (*self.resource_values.values(), *self.stat_values.values()):
                label.setText("—")
            self.items_list.clear()
            self.spells_list.clear()
            self.effects_label.setText("Effets actifs : —")
            return

        self.name_label.setText(str(entity.get("name", "Entity")))
        self.character_label.setText(f"Personnage : {entity.get('character', '—')}")
        stats = entity.get("stats", {}) if isinstance(entity.get("stats"), dict) else {}
        for key, label in self.resource_values.items():
            label.setText(str(stats.get(key, "—")))
        for key, label in self.stat_values.items():
            label.setText(str(stats.get(key, "—")))

        self.items_list.clear()
        for item in entity.get("items", []) if isinstance(entity.get("items"), list) else []:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("id") or "Objet")
                quantity = item.get("quantity")
                self.items_list.addItem(f"{name} × {quantity}" if quantity is not None else name)
            else:
                self.items_list.addItem(str(item))
        self.spells_list.clear()
        spells = entity.get("spells", {}) if isinstance(entity.get("spells"), dict) else {}
        for name, spell in spells.items():
            detail = spell if isinstance(spell, dict) else {}
            self.spells_list.addItem(f"{name}  •  {detail.get('cost', '?')} stamina  •  {detail.get('targeting', 'single')}")
        effects = entity.get("spell_effects", [])
        events = entity.get("spell_events", [])
        self.effects_label.setText(f"Effets actifs : {len(effects) if isinstance(effects, list) else 0}  •  Événements actifs : {len(events) if isinstance(events, list) else 0}")


class ActionPanel(QWidget):
    """Targeting and stat-check controls; combat actions live in CombatTurnWidget."""

    stat_check_requested = Signal(str)
    action_requested = Signal(object)
    targeting_mode_changed = Signal(object)
    cancel_pending_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.projection: dict[str, Any] = {}
        self._current_action: dict[str, str] | None = None
        self._selection: dict[str, Any] = {"token_ids": [], "point": None}
        self._pending_approval_id = ""
        self._pending = False
        self._connected = False
        self._own_token_id = ""
        self._token_labels: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(9)

        stat_group = QGroupBox("Test de caractéristique")
        stat_layout = QHBoxLayout(stat_group)
        self.stat_combo = QComboBox()
        self.stat_button = QPushButton("Lancer le test")
        self.stat_button.setObjectName("PlayerPrimaryButton")
        self.stat_button.clicked.connect(self._request_stat)
        stat_layout.addWidget(self.stat_combo, 1)
        stat_layout.addWidget(self.stat_button)
        root.addWidget(stat_group)

        targeting_group = QGroupBox("Ciblage de l'action")
        targeting_layout = QVBoxLayout(targeting_group)
        self.targeting_hint = QLabel("Choisis Strike, Shoot ou un sort dans le panneau Combat.")
        self.targeting_hint.setWordWrap(True)
        self.targeting_hint.setObjectName("PlayerHint")
        targeting_layout.addWidget(self.targeting_hint)
        self.selection_label = QLabel("Cibles : aucune")
        self.selection_label.setWordWrap(True)
        targeting_layout.addWidget(self.selection_label)
        selection_buttons = QHBoxLayout()
        self.clear_selection_button = QPushButton("Annuler le ciblage")
        self.submit_action_button = QPushButton("Envoyer au MJ")
        self.submit_action_button.setObjectName("PlayerPrimaryButton")
        self.clear_selection_button.clicked.connect(lambda: self.clear_action())
        self.submit_action_button.clicked.connect(self._submit_action)
        selection_buttons.addWidget(self.clear_selection_button)
        selection_buttons.addWidget(self.submit_action_button)
        targeting_layout.addLayout(selection_buttons)
        root.addWidget(targeting_group)

        self.pending_label = QLabel("")
        self.pending_label.setWordWrap(True)
        self.pending_label.setObjectName("PlayerHint")
        root.addWidget(self.pending_label)
        self.cancel_pending_button = QPushButton("Annuler la demande en attente")
        self.cancel_pending_button.setObjectName("PlayerDangerButton")
        self.cancel_pending_button.clicked.connect(self._cancel_pending)
        root.addWidget(self.cancel_pending_button)
        root.addStretch(1)
        self._refresh_controls()

    def set_projection(self, projection: dict[str, Any] | None) -> None:
        self.projection = dict(projection or {})
        entity = self.projection.get("entity", {})
        entity = entity if isinstance(entity, dict) else {}
        stats = entity.get("stats", {}) if isinstance(entity.get("stats"), dict) else {}
        current_stat = self.stat_combo.currentData()
        self.stat_combo.clear()
        for stat in STAT_ORDER:
            if stat in stats:
                self.stat_combo.addItem(STAT_LABELS.get(stat, stat.upper()), stat)
        if current_stat:
            index = self.stat_combo.findData(current_stat)
            if index >= 0:
                self.stat_combo.setCurrentIndex(index)

        map_data = self.projection.get("map", {})
        tokens = map_data.get("tokens", []) if isinstance(map_data, dict) else []
        own_token = next((t for t in tokens if isinstance(t, dict) and t.get("self")), None)
        self._own_token_id = str(own_token.get("id", "")) if own_token else ""
        self._token_labels = {}
        own_name = str(entity.get("name", "Toi"))
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token_id = str(token.get("id", ""))
            public = token.get("public", {}) if isinstance(token.get("public"), dict) else {}
            self._token_labels[token_id] = own_name if token.get("self") else str(public.get("name") or "Token non identifié")

        if not bool(self._permissions().get("can_combat_actions", False)) and self._current_action is not None:
            self.clear_action()
        self._refresh_controls()

    def set_target_selection(self, selection: dict[str, Any]) -> None:
        self._selection = dict(selection or {})
        token_ids = self._selection.get("token_ids", [])
        point = self._selection.get("point")
        if token_ids:
            labels = [self._token_labels.get(str(item), "Token") for item in token_ids]
            self.selection_label.setText(f"Cibles : {', '.join(labels)}")
        elif point is not None:
            self.selection_label.setText(f"Point ciblé : ({float(point[0]):.0f}, {float(point[1]):.0f})")
        elif self._current_action and self._current_action.get("mode") == "self":
            self.selection_label.setText("Cible : toi-même")
        else:
            self.selection_label.setText("Cibles : aucune")
        self._refresh_controls()

    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self._refresh_controls()

    def set_pending(self, pending: bool, approval_id: str = "", label: str = "") -> None:
        self._pending = bool(pending)
        self._pending_approval_id = str(approval_id or "")
        self.pending_label.setText(label or ("Demande envoyée au MJ — en attente de validation." if pending else ""))
        if pending and self._current_action is not None:
            self.clear_action()
        self._refresh_controls()

    def _permissions(self) -> dict[str, Any]:
        value = self.projection.get("permissions", {})
        return value if isinstance(value, dict) else {}

    def _available_actions(self) -> dict[str, Any]:
        value = self.projection.get("actions", {})
        return value if isinstance(value, dict) else {}

    def _refresh_controls(self) -> None:
        permissions = self._permissions()
        actions = self._available_actions()
        can_check = bool(permissions.get("can_stat_check", False))
        self.stat_button.setEnabled(self._connected and can_check and not self._pending and self.stat_combo.count() > 0)
        self.cancel_pending_button.setEnabled(self._connected and self._pending and bool(self._pending_approval_id))
        self.clear_selection_button.setEnabled(self._current_action is not None and not self._pending)

        current_available = False
        resolver_enabled = bool(actions.get("resolver_enabled", True))
        if self._current_action is not None and resolver_enabled:
            kind = self._current_action.get("kind", "")
            if kind in {"strike", "shoot"}:
                candidate = actions.get(kind, {})
                current_available = isinstance(candidate, dict) and bool(candidate.get("available", False))
            elif kind == "spell":
                spell_availability = actions.get("spells", {})
                candidate = spell_availability.get(self._current_action.get("action_id", ""), {}) if isinstance(spell_availability, dict) else {}
                current_available = isinstance(candidate, dict) and bool(candidate.get("available", False))
        self.submit_action_button.setEnabled(
            self._connected and current_available and self._current_action is not None
            and self._selection_is_complete() and not self._pending
            and bool(permissions.get("can_combat_actions", False))
        )

    def _request_stat(self) -> None:
        stat = self.stat_combo.currentData()
        if stat:
            self.clear_action()
            self.stat_check_requested.emit(str(stat))

    def begin_strike(self) -> None:
        self._begin_action("strike", "", "single")

    def begin_shoot(self) -> None:
        self._begin_action("shoot", "", "single")

    def begin_spell(self, spell_name: str) -> None:
        spell_name = str(spell_name or "")
        if not spell_name:
            return
        entity = self.projection.get("entity", {})
        spells = entity.get("spells", {}) if isinstance(entity, dict) else {}
        spell = spells.get(spell_name, {}) if isinstance(spells, dict) else {}
        mode = str(spell.get("targeting", "single")).lower() if isinstance(spell, dict) else "single"
        if mode not in {"single", "multi", "self", "area"}:
            mode = "single"
        self._current_action = {"kind": "spell", "action_id": spell_name, "mode": mode}
        self._selection = {"token_ids": [], "point": None}
        if mode == "self":
            self._selection["token_ids"] = [self._own_token_id] if self._own_token_id else []
            self.targeting_hint.setText("Ce sort cible automatiquement ton Entity.")
        elif mode == "area":
            self.targeting_hint.setText("Clique sur la carte pour choisir le centre de la zone. La forme et la portée seront arbitrées par le MJ.")
        elif mode == "multi":
            self.targeting_hint.setText("Clique sur chaque token visible à inclure dans la sélection.")
        else:
            self.targeting_hint.setText("Clique sur le token visible à cibler.")
        self.targeting_mode_changed.emit(mode)
        self.set_target_selection(self._selection)

    def _begin_action(self, kind: str, action_id: str, mode: str) -> None:
        self._current_action = {"kind": kind, "action_id": action_id, "mode": mode}
        self._selection = {"token_ids": [], "point": None}
        self.targeting_hint.setText(
            "Clique sur un token visible pour le sélectionner. Portée et ligne de vue dépendent du resolver côté MJ."
        )
        self.targeting_mode_changed.emit(mode)
        self.set_target_selection(self._selection)

    def _selection_is_complete(self) -> bool:
        if self._current_action is None:
            return False
        mode = self._current_action.get("mode", "single")
        if mode == "self":
            return bool(self._selection.get("token_ids"))
        if mode == "area":
            return self._selection.get("point") is not None
        return bool(self._selection.get("token_ids"))

    def _submit_action(self) -> None:
        if self._current_action is None or not self._selection_is_complete():
            return
        kind = ActionKind(self._current_action["kind"])
        point = self._selection.get("point")
        selection = TargetSelection(
            token_ids=tuple(str(item) for item in self._selection.get("token_ids", [])),
            point=(float(point[0]), float(point[1])) if point is not None else None,
        )
        intent = ActionIntent(
            kind=kind,
            action_id=self._current_action.get("action_id", ""),
            selection=selection,
        )
        self.action_requested.emit(intent)
        self.clear_action(emit_mode=True)

    def clear_action(self, emit_mode: bool = True) -> None:
        self._current_action = None
        self._selection = {"token_ids": [], "point": None}
        self.targeting_hint.setText("Choisis Strike, Shoot ou un sort dans le panneau Combat.")
        self.selection_label.setText("Cibles : aucune")
        if emit_mode:
            self.targeting_mode_changed.emit(None)
        self._refresh_controls()

    def _cancel_pending(self) -> None:
        if self._pending_approval_id:
            self.cancel_pending_requested.emit(self._pending_approval_id)


class ChatPanel(QWidget):
    send_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        self.players = QListWidget()
        self.players.setMaximumHeight(110)
        root.addWidget(QLabel("Joueurs connectés"))
        root.addWidget(self.players)
        root.addWidget(QLabel("Discussion"))
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(False)
        root.addWidget(self.history, 1)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message…")
        self.send_button = QPushButton("Envoyer")
        self.input.returnPressed.connect(self._send)
        self.send_button.clicked.connect(self._send)
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        root.addLayout(row)

    def set_connected(self, connected: bool) -> None:
        self.input.setEnabled(bool(connected))
        self.send_button.setEnabled(bool(connected))

    def _send(self) -> None:
        message = self.input.text().strip()
        if not message:
            return
        self.input.clear()
        self.send_requested.emit(message)

    def append_message(self, sender: str, message: str, *, system: bool = False) -> None:
        color = "#98a2b1" if system else ("#cba6f7" if sender else "#e5e9ef")
        self.history.append(
            f'<span style="color:{color};"><b>{escape(str(sender))}</b></span> '
            f'<span style="color:#e5e9ef;">{escape(str(message))}</span>'
        )

    def set_players(self, names: list[str]) -> None:
        self.players.clear()
        for name in sorted(set(str(item) for item in names), key=str.casefold):
            self.players.addItem(name)
