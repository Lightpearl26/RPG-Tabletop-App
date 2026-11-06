# -*- coding: utf-8 -*-
"""Player sheet, action controls and chat panels."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QPushButton, QVBoxLayout, QWidget,
)

from rpg_tabletop.net import ActionIntent, ActionKind, TargetSelection
from rpg_tabletop.ui.common.chat_panel import ChatPanel as SharedChatPanel


STAT_ORDER = (
    "str", "con", "agi", "dex", "int", "wis", "cha", "luc", "sur", "per",
    "hp", "stamina",
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

    def _range(self) -> int:
        targeting = self.projection.get("targeting", {})
        try:
            return int(targeting.get("range", 0)) if isinstance(targeting, dict) else 0
        except (TypeError, ValueError):
            return 0

    def begin_strike(self) -> None:
        self._begin_action(
            "strike", "", {"mode": "single", "allowed": "strike"},
            "Clique sur un token au contact : une des huit cases autour de toi, sans mur entre vous.",
        )

    def begin_shoot(self) -> None:
        self._begin_action(
            "shoot", "", {"mode": "single", "allowed": "shoot"},
            "Clique sur un token en ligne de vue : visible, sans mur ni autre token sur la trajectoire.",
        )

    def begin_spell(self, spell_name: str) -> None:
        spell_name = str(spell_name or "")
        if not spell_name:
            return
        entity = self.projection.get("entity", {})
        spells = entity.get("spells", {}) if isinstance(entity, dict) else {}
        spell = spells.get(spell_name, {}) if isinstance(spells, dict) else {}
        spell = spell if isinstance(spell, dict) else {}
        mode = str(spell.get("targeting", "single")).lower()
        if mode not in {"single", "multi", "self", "area", "line"}:
            mode = "single"
        self._current_action = {"kind": "spell", "action_id": spell_name, "mode": mode}
        self._selection = {"token_ids": [], "point": None}
        spec: dict[str, Any] = {"mode": mode}
        reach = self._range()
        if mode == "self":
            self._selection["token_ids"] = [self._own_token_id] if self._own_token_id else []
            self.targeting_hint.setText("Ce sort cible automatiquement ton Entity.")
        elif mode == "area":
            size = int(spell.get("area_size") or 0)
            spec["area_size"] = size
            self.targeting_hint.setText(
                f"Clique sur la case centrale, à {reach} case(s) au plus. La zone couvre {size} case(s) "
                "autour (toi compris si tu es dedans) ; les murs protègent ce qui est derrière."
            )
        elif mode == "line":
            self.targeting_hint.setText(
                f"Clique sur une case pour donner la direction : la ligne part de toi et porte à "
                f"{reach} case(s), elle traverse les tokens et s'arrête au premier mur."
            )
        elif mode == "multi":
            spec["allowed"] = "spell_tokens"
            maximum = spell.get("max_targets")
            spec["max"] = int(maximum) if maximum else None
            limit = f" ({int(maximum)} au plus)" if maximum else ""
            self.targeting_hint.setText(f"Clique sur chaque token en ligne de vue à inclure{limit}.")
        else:
            spec["allowed"] = "spell_tokens"
            self.targeting_hint.setText("Clique sur le token à cibler : en ligne de vue, toi compris.")
        self.targeting_mode_changed.emit(spec)
        self.set_target_selection(self._selection)

    def _begin_action(self, kind: str, action_id: str, spec: dict[str, Any], hint: str) -> None:
        self._current_action = {"kind": kind, "action_id": action_id, "mode": spec["mode"]}
        self._selection = {"token_ids": [], "point": None}
        self.targeting_hint.setText(hint)
        self.targeting_mode_changed.emit(spec)
        self.set_target_selection(self._selection)

    def _selection_is_complete(self) -> bool:
        if self._current_action is None:
            return False
        mode = self._current_action.get("mode", "single")
        if mode == "self":
            return bool(self._selection.get("token_ids"))
        if mode in {"area", "line"}:
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


class ChatPanel(SharedChatPanel):
    """Shared chat (with level filters) plus the list of connected players."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, settings_scope="PlayerRuntime")
        self.setObjectName("")
        self.input.setPlaceholderText("Message…")
        layout = self.layout()
        self.players = QListWidget()
        self.players.setMaximumHeight(110)
        layout.insertWidget(0, QLabel("Joueurs connectés"))
        layout.insertWidget(1, self.players)
        layout.insertWidget(2, QLabel("Discussion"))

    def set_players(self, names: list[str]) -> None:
        self.players.clear()
        for name in sorted(set(str(item) for item in names), key=str.casefold):
            self.players.addItem(name)
