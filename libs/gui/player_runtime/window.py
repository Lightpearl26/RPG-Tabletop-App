# -*- coding: utf-8 -*-
"""Main Player Runtime window. It only consumes server projections."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

from PySide6.QtCore import QStandardPaths, QTimer, Qt, QSettings
from PySide6.QtGui import QCloseEvent, QAction
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget, QScrollArea, QFrame,
)

from ...net import ClientSettings
from ...net.protocol import ChatMessage, CommandMessage, ErrorMessage
from .connection import PlayerNetworkController
from .dialogs import PlayerLoginDialog, TokenInspectDialog
from .map_view import PlayerMapView
from .panels import ActionPanel, CharacterPanel, ChatPanel
from ..widgets.combat_turn_widget import CombatTurnWidget
from .style import PLAYER_RUNTIME_STYLESHEET
from .panels import ActionPanel, ChatPanel
from .sheet import CharacterSheetWindow


class PlayerRuntimeWindow(QMainWindow):
    def __init__(
        self,
        settings: ClientSettings,
        *,
        asset_cache: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        if asset_cache is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
            asset_cache = Path(base or (Path.home() / ".rpg_tabletop")) / "player_cache"
        self._base_asset_cache = Path(asset_cache)
        self.controller = PlayerNetworkController(self._base_asset_cache, self)
        self._cache_session_id = ""
        self.projection: dict[str, Any] | None = None
        self._pending_approval_id = ""
        self._has_pending = False
        self._requested_assets: set[str] = set()
        self._closing = False
        self._asset_request_timer = QTimer(self)
        self._asset_request_timer.setSingleShot(True)
        self._asset_request_timer.setInterval(250)
        self._asset_request_timer.timeout.connect(self._request_current_visible_assets)

        self.setObjectName("PlayerRuntimeWindow")
        self.setWindowTitle("RPG Tabletop — Joueur")
        self.resize(1440, 900)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(PLAYER_RUNTIME_STYLESHEET)
        self._sheet: CharacterSheetWindow | None = None
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("PlayerRuntimeRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(8)

        header = QFrame()
        header.setObjectName("PlayerHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        self.title_label = QLabel("RPG TABLETOP")
        self.title_label.setObjectName("PlayerTitle")
        self.turn_label = QLabel("—")
        self.turn_label.setObjectName("PlayerMuted")
        self.budget_label = QLabel("")
        self.budget_label.hide()
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.turn_label)
        layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_map_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setCollapsible(1, False)
        splitter.setSizes([300, 860, 340])
        layout.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Connexion au serveur MJ…")

    def _build_left_panel(self) -> QWidget:
        """Left: connection state and chat."""
        panel = QWidget()
        panel.setMinimumWidth(260)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        box = QFrame()
        box.setObjectName("PlayerPanel")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(10, 8, 10, 10)
        box_layout.setSpacing(5)
        heading = QLabel("CONNEXION")
        heading.setObjectName("PlayerSectionTitle")
        self.connection_label = QLabel("Connexion en cours…")
        self.connection_label.setObjectName("PlayerMuted")
        self.session_label = QLabel("Session : —")
        self.session_label.setObjectName("PlayerMuted")
        self.entity_label = QLabel("Entity : en attente du MJ")
        self.entity_label.setObjectName("PlayerMuted")
        for label in (self.connection_label, self.session_label, self.entity_label):
            label.setWordWrap(True)
        buttons = QHBoxLayout()
        self.reconnect_button = QPushButton("Se reconnecter")
        self.disconnect_button = QPushButton("Déconnecter")
        self.reconnect_button.clicked.connect(self._reconnect)
        self.disconnect_button.clicked.connect(self._disconnect)
        buttons.addWidget(self.reconnect_button)
        buttons.addWidget(self.disconnect_button)
        box_layout.addWidget(heading)
        box_layout.addWidget(self.connection_label)
        box_layout.addWidget(self.session_label)
        box_layout.addWidget(self.entity_label)
        box_layout.addLayout(buttons)

        self.chat_panel = ChatPanel()
        layout.addWidget(box)
        layout.addWidget(self.chat_panel, 1)
        return panel

    def _build_map_panel(self) -> QWidget:
        """Center: the map."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        toolbar = QHBoxLayout()
        self.fit_button = QPushButton("Recentrer la vue")
        self.reload_assets_button = QPushButton("Recharger les assets")
        self.map_status = QLabel("En attente de la carte du MJ")
        self.map_status.setObjectName("PlayerMuted")
        self.map_view = PlayerMapView(self.controller.cache_root)
        self.fit_button.clicked.connect(self.map_view.fit_map)
        self.reload_assets_button.clicked.connect(self._retry_map_assets)
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.reload_assets_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.map_status)
        layout.addLayout(toolbar)
        layout.addWidget(self.map_view, 1)
        return container

    def _build_right_panel(self) -> QWidget:
        """Right: combat, actions and stat checks."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)
        self.combat_widget = CombatTurnWidget()
        self.action_panel = ActionPanel()
        layout.addWidget(self.combat_widget)
        layout.addWidget(self.action_panel)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(330)
        scroll.setWidget(content)
        return scroll

    def _connect_signals(self) -> None:
        self.controller.message_received.connect(self._on_network_message)
        self.controller.connection_changed.connect(self._on_connection_changed)
        self.action_panel.set_connected(False)
        self.chat_panel.set_connected(False)
        self.controller.operation_finished.connect(self._on_operation_finished)

        self.map_view.move_requested.connect(self.controller.request_move)
        self.map_view.token_inspect_requested.connect(self.controller.inspect_token)
        self.map_view.target_selection_changed.connect(self.action_panel.set_target_selection)
        self.map_view.status_message.connect(self.statusBar().showMessage)
        self.map_view.viewport_changed.connect(self._schedule_visible_asset_request)

        self.action_panel.targeting_mode_changed.connect(self.map_view.set_targeting_mode)
        self.action_panel.stat_check_requested.connect(self.controller.request_stat_check)
        self.action_panel.action_requested.connect(self.controller.request_action)
        self.action_panel.cancel_pending_requested.connect(self.controller.cancel_pending)
        self.combat_widget.strike_requested.connect(self.action_panel.begin_strike)
        self.combat_widget.shoot_requested.connect(self.action_panel.begin_shoot)
        self.combat_widget.spell_requested.connect(self.action_panel.begin_spell)
        self.combat_widget.end_turn_requested.connect(self.controller.request_end_turn)
        self.chat_panel.send_requested.connect(self.controller.send_chat)
        self.map_view.character_sheet_requested.connect(self._open_character_sheet)

    # --------------------------------------------------------------- connection
    def connect_to_server(self) -> None:
        try:
            self.connection_label.setText(f"Connexion à {self.settings.host}:{self.settings.port}…")
            self.controller.connect(self.settings)
        except Exception as exc:
            self.connection_label.setText("Connexion impossible")
            QMessageBox.critical(self, "Connexion", str(exc))

    def _on_connection_changed(self, connected: bool, message: str) -> None:
        self.connection_label.setText("Connecté" if connected else message)
        self.action_panel.set_connected(connected)
        self.chat_panel.set_connected(connected)
        self._refresh_combat_widget_permissions()
        self.map_view.set_has_pending(self._has_pending or not connected)
        if not connected:
            self._clear_pending()
            if not self._closing:
                self.statusBar().showMessage(message, 8000)

    def _on_operation_finished(self, label: str, result: Any, error: Any) -> None:
        if self._closing:
            return
        if label == "connect":
            if error is not None:
                self.connection_label.setText("Échec de connexion")
                self.chat_panel.append_message("Système", str(error), system=True)
                client = self.controller.client
                if (
                    client is not None
                    and client.peer_fingerprint
                    and "fingerprint mismatch" in str(error).casefold()
                ):
                    answer = QMessageBox.question(
                        self,
                        "Certificat du serveur modifié",
                        "Le certificat présenté par ce serveur a changé. "
                        "Veux-tu mémoriser le nouveau certificat et réessayer ?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        trust = QSettings("RPGTabletop", "PlayerRuntime")
                        trust.setValue(
                            PlayerLoginDialog.trust_key(self.settings.host, self.settings.port),
                            client.peer_fingerprint,
                        )
                        self.statusBar().showMessage(
                            "Nouveau certificat mémorisé. Reconnecte-toi pour continuer.", 8000
                        )
                        return
                QMessageBox.warning(self, "Connexion au MJ", str(error))
                return
            client = self.controller.client
            if client is not None and client.peer_fingerprint:
                trust = QSettings("RPGTabletop", "PlayerRuntime")
                trust.setValue(
                    PlayerLoginDialog.trust_key(self.settings.host, self.settings.port),
                    client.peer_fingerprint,
                )
            self.controller.connection_changed.emit(True, "Connecté")
            self.statusBar().showMessage("Connecté au serveur MJ — authentification réussie", 5000)
            if self.projection and isinstance(self.projection.get("map"), dict):
                self._schedule_visible_asset_request()
            if not self.controller.bound_entity_id:
                self.chat_panel.append_message(
                    "Système", "Connecté. En attente que le MJ associe ton joueur à une Entity.", system=True
                )
            return
        if error is not None:
            self.statusBar().showMessage(f"{label} : {error}", 8000)
            if label not in {"assets", "runtime_sync"}:
                self.chat_panel.append_message("Système", str(error), system=True)
            return
        if label == "move":
            self.statusBar().showMessage("Déplacement envoyé", 4000)
        elif label == "stat_check":
            self.statusBar().showMessage("Test envoyé au MJ", 4000)
        elif label == "action":
            self.statusBar().showMessage("Action envoyée au MJ", 4000)
        elif label == "end_turn":
            self.statusBar().showMessage("Demande de fin de tour envoyée au MJ", 4000)

    def _reconnect(self) -> None:
        self.controller.shutdown()
        self._requested_assets.clear()
        self.projection = None
        self._apply_projection({})
        dialog = PlayerLoginDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings = dialog.client_settings()
        self.connect_to_server()

    def _disconnect(self) -> None:
        self.controller.shutdown()
        self.connection_label.setText("Déconnecté")
        self.statusBar().showMessage("Déconnecté du serveur MJ")

    # --------------------------------------------------------------- messages
    def _on_network_message(self, message: Any) -> None:
        if isinstance(message, ChatMessage):
            self.chat_panel.append_message(message.sender, message.message)
            return
        if isinstance(message, ErrorMessage):
            self._forget_missing_asset_requests()
            self.chat_panel.append_message("Réseau", message.reason, system=True)
            self.statusBar().showMessage(message.reason, 8000)
            return
        if not isinstance(message, CommandMessage):
            return

        command = message.command
        args = message.args
        if command == "RUNTIME_SYNC":
            projection = args.get("projection")
            if isinstance(projection, dict):
                self._apply_projection(projection)
        elif command == "ENTITY_BOUND":
            self.chat_panel.append_message("Système", "Le MJ vient de t'associer à une Entity.", system=True)
        elif command == "ENTITY_UNBOUND":
            self._clear_pending()
            self.projection = None
            self._apply_projection({})
            self.chat_panel.append_message("Système", "Le MJ t'a retiré ton Entity.", system=True)
        elif command in {"REQUEST_PENDING", "ACTION_PENDING"}:
            self._pending_approval_id = str(args.get("approval_id", ""))
            kind = str(args.get("kind", "action"))
            self._set_pending(True, self._pending_approval_id, f"{kind} — en attente de validation du MJ.")
        elif command == "REQUEST_REJECTED":
            # A rejection can be the response to a second request racing with
            # an already-pending one. Do not clear the first approval card.
            if not self._pending_approval_id:
                self._clear_pending()
            reason = str(args.get("reason", "Demande refusée par le serveur."))
            self.chat_panel.append_message("Système", reason, system=True)
            self.statusBar().showMessage(reason, 8000)
        elif command == "REQUEST_CANCELLED":
            self._clear_pending()
            self.chat_panel.append_message("Système", "Demande annulée.", system=True)
        elif command in {"MOVE_RESULT", "STAT_CHECK_RESULT", "ACTION_RESULT", "END_TURN_RESULT"}:
            self._handle_action_result(command, args)
        elif command == "TOKEN_INSPECT_RESULT":
            entity_view = args.get("entity", {})
            if isinstance(entity_view, dict):
                dialog = TokenInspectDialog(entity_view, self)
                dialog.exec()
        elif command == "PLAYER_LIST":
            self._refresh_players(args.get("players", []))
        elif command == "PLAYER_JOINED":
            name = str(args.get("username", ""))
            self._refresh_players_from_client()
            if name:
                self.chat_panel.append_message("Système", f"{name} a rejoint la partie.", system=True)
        elif command == "PLAYER_LEFT":
            name = str(args.get("username", ""))
            self._refresh_players_from_client()
            if name:
                self.chat_panel.append_message("Système", f"{name} a quitté la partie.", system=True)
        elif command == "ASSET_CHUNK":
            self.map_view.update()
        elif command == "ASSET_DONE":
            self.map_view.update()
            self.statusBar().showMessage("Synchronisation des ressources de carte terminée", 4000)

    def _handle_action_result(self, command: str, args: dict[str, Any]) -> None:
        self._clear_pending()
        if not bool(args.get("success", False)):
            reason = str(args.get("message") or args.get("reason") or "Action refusée.")
            self.chat_panel.append_message("MJ", reason, system=True)
            self.statusBar().showMessage(reason, 8000)
            return

        event = args.get("event", {}) if isinstance(args.get("event"), dict) else {}
        data = event.get("data", {}) if isinstance(event.get("data"), dict) else {}
        if command == "STAT_CHECK_RESULT":
            stat = str(data.get("stat", "?")).upper()
            total = data.get("total", "—")
            dice = str(data.get("dice", ""))
            values = data.get("values", [])
            roll_text = ", ".join(str(value) for value in values) if isinstance(values, list) else str(values)
            outcome = "réussite" if data.get("success") else "échec"
            if data.get("critical_success"):
                outcome += " critique"
            elif data.get("critical_failure"):
                outcome += " critique"
            details = f"{dice} [{roll_text}]" if dice or roll_text else "jet"
            text = f"Test {stat} : {details} = {total} — {outcome}"
        elif command == "MOVE_RESULT":
            text = "Déplacement effectué."
        elif command == "END_TURN_RESULT":
            text = "Ton tour est terminé."
        else:
            text = str(args.get("message") or "Action validée par le MJ.")
        self.chat_panel.append_message("Système", text, system=True)
        self.statusBar().showMessage(text, 5000)

    def _set_pending(self, pending: bool, approval_id: str = "", label: str = "") -> None:
        self._has_pending = bool(pending)
        self.action_panel.set_pending(pending, approval_id, label)
        self._refresh_combat_widget_permissions()
        self.map_view.set_has_pending(self._has_pending or not self.controller.is_connected)

    def _refresh_combat_widget_permissions(self) -> None:
        projection = self.projection or {}
        permissions = projection.get("permissions", {})
        permissions = permissions if isinstance(permissions, dict) else {}
        combat = projection.get("combat", {})
        combat = combat if isinstance(combat, dict) else {}
        own_turn = bool(combat.get("active") and combat.get("own_turn"))
        actions = projection.get("actions", {})
        actions = actions if isinstance(actions, dict) else {}
        resolver_enabled = bool(actions.get("resolver_enabled", True))
        action_hint = ""
        if own_turn and not resolver_enabled:
            action_hint = "Ton tour a commencé, mais le resolver d'action du MJ n'est pas configuré."
        self.combat_widget.set_permissions(
            connected=self.controller.is_connected,
            can_act=bool(permissions.get("can_combat_actions", False)) and resolver_enabled,
            can_end_turn=own_turn,
            pending=self._has_pending,
            action_hint=action_hint,
        )

    def _clear_pending(self) -> None:
        self._pending_approval_id = ""
        self._set_pending(False)

    # --------------------------------------------------------------- projection
    def _apply_projection(self, projection: dict[str, Any]) -> None:
        self.projection = dict(projection) if projection else None
        if not projection:
            self.map_view.set_projection({})
            if self._sheet is not None:
                self._sheet.hide()
            self.action_panel.set_projection({})
            self.combat_widget.set_combat_state({"active": False}, animate=False)
            self.combat_widget.set_action_options(spells=[])
            self.combat_widget.set_permissions(
                connected=self.controller.is_connected,
                can_act=False,
                can_end_turn=False,
                pending=self._has_pending,
            )
            self.session_label.setText("Session : —")
            self.entity_label.setText("Entity : en attente du MJ")
            self.turn_label.setText("—")
            self.budget_label.setText("")
            self.map_status.setText("En attente de la carte du MJ")
            self.setWindowTitle("RPG Tabletop — Joueur")
            return

        entity = projection.get("entity", {}) if isinstance(projection.get("entity"), dict) else {}
        session = projection.get("session", {}) if isinstance(projection.get("session"), dict) else {}
        combat = projection.get("combat", {}) if isinstance(projection.get("combat"), dict) else {}
        map_data = projection.get("map")
        self._set_session_cache(str(session.get("id", "")))
        if isinstance(map_data, dict):
            self._prepare_map_cache(map_data)
        if self._sheet is not None and self._sheet.isVisible():
            self._sheet.set_entity(entity)
        self.action_panel.set_projection(projection)
        self.map_view.set_projection(projection)

        entity_name = str(entity.get("name", "Entity"))
        character_name = str(entity.get("character", ""))
        self.setWindowTitle(f"RPG Tabletop — {entity_name}")
        self.entity_label.setText(f"Entity : {entity_name} ({character_name})")
        self.session_label.setText(f"Session : {session.get('name', '—')}")

        combat_active = bool(combat.get("active"))
        own_turn = bool(combat.get("own_turn"))
        if not combat_active:
            self.turn_label.setText("Hors combat · déplacement libre")
        elif own_turn:
            self.turn_label.setText(f"Ton tour · Round {combat.get('round', 1)}")
        else:
            current_items = combat.get("carousel", [])
            current = current_items[1] if isinstance(current_items, list) and len(current_items) > 1 else None
            current_name = str(current.get("name", "")) if isinstance(current, dict) else ""
            turn_text = f"Tour de {current_name}" if current_name and current.get("visible", True) else "Tour d'un participant non visible"
            self.turn_label.setText(f"Round {combat.get('round', 1)} · {turn_text}")
        self.budget_label.setText("")

        carousel_state = dict(combat)
        carousel_state["player_view"] = True
        carousel_state["turn_key"] = f"{combat.get('round', 1)}:{combat.get('turn_index', 0)}"
        self.combat_widget.set_combat_state(carousel_state, animate=True)

        actions = projection.get("actions", {}) if isinstance(projection.get("actions"), dict) else {}
        strike = actions.get("strike", {}) if isinstance(actions.get("strike"), dict) else {}
        shoot = actions.get("shoot", {}) if isinstance(actions.get("shoot"), dict) else {}
        spell_availability = actions.get("spells", {}) if isinstance(actions.get("spells"), dict) else {}
        spells = entity.get("spells", {}) if isinstance(entity.get("spells"), dict) else {}
        spell_options = []
        for spell_name, spell_info in spells.items():
            spell_info = spell_info if isinstance(spell_info, dict) else {}
            availability = spell_availability.get(spell_name, {})
            availability = availability if isinstance(availability, dict) else {}
            spell_options.append({
                "id": spell_name,
                "name": spell_name,
                "cost": int(spell_info.get("cost", availability.get("cost", 0)) or 0),
                "available": bool(availability.get("available", False)),
            })
        self.combat_widget.set_action_options(
            strike_cost=int(strike.get("cost", 60) or 60),
            strike_available=bool(strike.get("available", False)),
            shoot_cost=int(shoot.get("cost", 60) or 60),
            shoot_available=bool(shoot.get("available", False)),
            spells=spell_options,
        )
        self._refresh_combat_widget_permissions()

        if isinstance(map_data, dict):
            map_asset = map_data.get("asset", {})
            map_name = str(map_asset.get("name", map_data.get("map_id", "Carte"))) if isinstance(map_asset, dict) else "Carte"
            fov = map_data.get("fov", {}) if isinstance(map_data.get("fov"), dict) else {}
            radius = int(fov.get("radius_cells", 0) or 0)
            self.map_status.setText(
                f"{map_name} · {len(map_data.get('tokens', []))} token(s) visible(s) · Vision {radius} cases"
            )
            self._schedule_visible_asset_request()
        else:
            self.map_status.setText("Aucune carte active")

    def _open_character_sheet(self) -> None:
        entity = (self.projection or {}).get("entity")
        if not isinstance(entity, dict):
            return
        if self._sheet is None:
            self._sheet = CharacterSheetWindow(self)
        self._sheet.set_entity(entity)
        self._sheet.show()
        self._sheet.raise_()
        self._sheet.activateWindow()

    def _set_session_cache(self, session_id: str) -> None:
        """Isolate downloaded map files between sessions/campaigns."""
        safe_id = PurePosixPath(str(session_id).replace("\\", "/"))
        if not session_id or safe_id.is_absolute() or len(safe_id.parts) != 1 or session_id in {".", ".."}:
            return
        if session_id == self._cache_session_id:
            return
        session_root = self._base_asset_cache / "sessions" / session_id
        session_root.mkdir(parents=True, exist_ok=True)
        self._cache_session_id = session_id
        self._requested_assets.clear()
        self.controller.cache_root = session_root
        if self.controller.client is not None and self.controller.client.asset_receiver is not None:
            receiver = self.controller.client.asset_receiver
            receiver.cache_root = session_root
            receiver._buffers.clear()
            receiver._meta.clear()
        self.map_view.set_cache_root(session_root)

    def _schedule_visible_asset_request(self) -> None:
        if self.projection and isinstance(self.projection.get("map"), dict):
            self._asset_request_timer.start()

    def _request_current_visible_assets(self) -> None:
        if self.projection and isinstance(self.projection.get("map"), dict):
            self._request_missing_map_assets(self.projection["map"])

    def _prepare_map_cache(self, map_data: dict[str, Any]) -> None:
        map_id = str(map_data.get("map_id", ""))
        safe_id = PurePosixPath(map_id.replace("\\", "/"))
        if not map_id or safe_id.is_absolute() or len(safe_id.parts) != 1 or map_id in {".", ".."}:
            return
        asset = map_data.get("asset", {})
        compiled = asset.get("compiled") if isinstance(asset, dict) else None
        if not isinstance(compiled, dict):
            return
        signature = sha256(
            json.dumps(compiled, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        map_root = self.controller.cache_root / "maps" / map_id
        marker = map_root / ".compiled_signature"
        previous = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        if map_root.exists() and previous != signature:
            shutil.rmtree(map_root, ignore_errors=True)
            self._requested_assets.clear()
            self.map_view.invalidate_cached_assets()
        map_root.mkdir(parents=True, exist_ok=True)
        marker.write_text(signature, encoding="utf-8")

    def _retry_map_assets(self) -> None:
        if self.projection and isinstance(self.projection.get("map"), dict):
            self._requested_assets.clear()
            self._request_missing_map_assets(self.projection["map"])

    def _forget_missing_asset_requests(self) -> None:
        for relative in tuple(self._requested_assets):
            local = self.controller.cache_root / "maps" / Path(*PurePosixPath(relative).parts)
            if not local.is_file():
                self._requested_assets.discard(relative)

    def _request_missing_map_assets(self, map_data: dict[str, Any]) -> None:
        if not self.controller.is_connected:
            return
        map_id = str(map_data.get("map_id", ""))
        map_path = PurePosixPath(map_id.replace("\\", "/"))
        if not map_id or map_path.is_absolute() or len(map_path.parts) != 1 or map_id in {".", ".."}:
            return
        asset = map_data.get("asset", {})
        if not isinstance(asset, dict):
            asset = {}
        wanted: list[dict[str, str]] = []

        # Request only compiled tiles intersecting the current viewport.
        compiled = asset.get("compiled")
        if isinstance(compiled, dict):
            tile_format = str(compiled.get("format", "webp"))
            if tile_format in {"webp", "png"}:
                tiles = compiled.get("tiles", {}) if isinstance(compiled.get("tiles"), dict) else {}
                visible_keys = self.map_view.visible_tile_keys()
                for key in sorted(visible_keys):
                    if key not in tiles or not all(part.isdigit() for part in key.split("_", 1)):
                        continue
                    relative = f"{map_id}/tiles/{key}.{tile_format}"
                    if relative in self._requested_assets:
                        continue
                    local = self.controller.cache_root / "maps" / Path(*PurePosixPath(relative).parts)
                    if not local.is_file():
                        self._requested_assets.add(relative)
                        wanted.append({"namespace": "maps", "path": relative})

        # Runtime-added image elements may reference files under this map's media/ folder.
        for element in map_data.get("elements", []):
            if not isinstance(element, dict) or not element.get("asset"):
                continue
            relative_asset = PurePosixPath(str(element["asset"]).replace("\\", "/"))
            if relative_asset.is_absolute() or ".." in relative_asset.parts:
                continue
            relative = f"{map_id}/{relative_asset.as_posix()}"
            if relative in self._requested_assets:
                continue
            local = self.controller.cache_root / "maps" / Path(*PurePosixPath(relative).parts)
            if not local.is_file():
                self._requested_assets.add(relative)
                wanted.append({"namespace": "maps", "path": relative})
        if wanted:
            self.controller.request_assets(wanted)

    # --------------------------------------------------------------- players
    def _refresh_players(self, players: Any) -> None:
        names = [str(name) for name in players] if isinstance(players, list) else []
        self.chat_panel.set_players(names)

    def _refresh_players_from_client(self) -> None:
        client = self.controller.client
        if client is not None:
            self.chat_panel.set_players(list(client.players))

    # ---------------------------------------------------------------- lifecycle
    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.controller.shutdown()
        super().closeEvent(event)
