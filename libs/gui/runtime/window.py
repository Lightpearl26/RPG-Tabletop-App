# -*- coding: utf-8 -*-
"""GM Runtime window for RPG Tabletop App."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Any

from PySide6.QtCore import Qt, QTimer, QSettings, QSize
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QToolBar,
    QWidget,
)

from ...assets.map import DrawTool
from ...net.assets import AssetCatalog
from ...net.config import RelaySettings, ServerSettings, TLSSettings
from ...net.protocol import ChatMessage
from ...net.runner import AsyncioLoopThread
from ...net.server import RPGNetworkServer, create_runtime_server
from ...runtime.session import Session, SessionRuntime
from ..theme import CAMPAIGN_MANAGER_STYLESHEET
from ..widgets.chat_panel import ChatPanel
from .bridge import RuntimeNetworkBridge
from .combat import CombatPanel
from .disclosure import TokenDisclosureDialog
from .journal import JournalPanel
from .map_view import RuntimeMapView
from .network import NetworkPanel
from .pending import PendingPanel
from .players import PlayerAssignmentPanel
from .registry import EntityRegistryPanel
from .style import RUNTIME_STYLESHEET
from .tokens import TokenManagerPanel
from ...runtime.live_sync import RESOURCE_KINDS
from .add_entities import AddEntitiesDialog


class RuntimeWindow(QMainWindow):
    def __init__(
        self,
        runtime: SessionRuntime,
        *,
        action_preparer: Callable[..., Any] | None = None,
        action_executor: Callable[..., Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.campaign = runtime.campaign
        self.action_preparer = action_preparer
        self.action_executor = action_executor
        self.network_loop: AsyncioLoopThread | None = None
        self.server: RPGNetworkServer | None = None
        self.bridge = RuntimeNetworkBridge(self)
        self._last_seq = -1
        self._closing = False
        self._server_starting = False
        self._server_stopping = False
        self._settings = QSettings("RPGTabletop", "GMRuntime")

        self.setObjectName("RuntimeWindow")
        self.setWindowTitle(f"RPG Tabletop Runtime — {runtime.session.name}")
        self.resize(1600, 960)
        self.setDockNestingEnabled(True)
        self.setStyleSheet(CAMPAIGN_MANAGER_STYLESHEET + RUNTIME_STYLESHEET)

        self.map_view = RuntimeMapView(runtime, self)
        self._build_toolbar()
        self._build_map_workspace()
        self._build_docks()
        self._restore_layout()
        self._connect()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh_all)
        self.refresh_timer.start()
        self.refresh_all(force=True)

    @classmethod
    def start_new(cls, campaign, name: str, *, initial_map_id: str | None = None, **kwargs):
        runtime = SessionRuntime.start_new(name, campaign, initial_map_id=initial_map_id)
        return cls(runtime, **kwargs)

    @classmethod
    def open_saved(cls, campaign, session_dir: str | Path, **kwargs):
        session = Session.load(Path(session_dir))
        runtime = SessionRuntime.load(session, campaign)
        return cls(runtime, **kwargs)

    # ---------------------------------------------------------------- UI
    def _build_toolbar(self) -> None:
        bar = QToolBar("Runtime", self)
        bar.setObjectName("RuntimeToolbar")
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addWidget(QLabel("Carte :"))
        self.map_combo = QComboBox()
        self.map_combo.setMinimumWidth(210)
        self.map_combo.addItem("— Aucune carte —", None)
        for path in sorted(self.campaign.maps_path.iterdir()) if self.campaign.maps_path.is_dir() else []:
            if path.is_dir() and (path / "map.json").is_file():
                self.map_combo.addItem(path.name, path.name)
        idx = self.map_combo.findData(self.runtime.active_map_id)
        self.map_combo.setCurrentIndex(max(0, idx))
        self.map_combo.currentIndexChanged.connect(self._map_selected)
        bar.addWidget(self.map_combo)

        fit = QAction("Ajuster", self)
        fit.triggered.connect(self.map_view.fit_map)
        bar.addAction(fit)
        save = QAction("Sauvegarder", self)
        save.setShortcut("Ctrl+S")
        save.triggered.connect(self.save_session)
        bar.addAction(save)
        note = QAction("Note", self)
        note.triggered.connect(self.add_note)
        bar.addAction(note)

        add_entities = QAction("Ajouter des entités", self)
        add_entities.triggered.connect(self.add_entities)
        bar.addAction(add_entities)

        bar.addSeparator()
        self.server_summary_label = QLabel("● Serveur arrêté · 0 joueur")
        self.server_summary_label.setObjectName("RuntimeServerSummary")
        bar.addWidget(self.server_summary_label)

        restore = QAction("Restaurer la disposition", self)
        restore.triggered.connect(self._restore_default_layout)
        self.menuBar().addMenu("Affichage").addAction(restore)

    def _build_map_workspace(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("RuntimeMapWorkspace")
        from PySide6.QtWidgets import QHBoxLayout
        layout = QHBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.map_tools = QToolBar("Outils de dessin MJ", workspace)
        self.map_tools.setObjectName("RuntimeMapTools")
        self.map_tools.setOrientation(Qt.Orientation.Vertical)
        self.map_tools.setMovable(False)
        self.map_tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.map_tools.setIconSize(QSize(18, 18))
        self._map_tool_group = QActionGroup(self)
        self._map_tool_group.setExclusive(True)

        tools = (
            ("Sélection", None),
            ("Main libre", DrawTool.FREEHAND.value),
            ("Ligne", DrawTool.LINE.value),
            ("Rectangle", DrawTool.RECT.value),
            ("Ellipse", DrawTool.CIRCLE.value),
            ("Gomme", "eraser"),
        )
        self._map_tool_actions: dict[str, QAction] = {}
        for label, tool_value in tools:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(tool_value)
            self._map_tool_group.addAction(action)
            self.map_tools.addAction(action)
            self._map_tool_actions[label] = action
            action.triggered.connect(lambda checked=False, a=action: self._set_map_tool(a.data()))
        self._map_tool_actions["Sélection"].setChecked(True)

        self.map_tools.addSeparator()
        color_action = QAction("Couleur", self)
        color_action.triggered.connect(self._choose_drawing_color)
        self.map_tools.addAction(color_action)
        self.drawing_width = QSpinBox()
        self.drawing_width.setRange(1, 24)
        self.drawing_width.setValue(3)
        self.drawing_width.setToolTip("Épaisseur du trait")
        self.drawing_width.valueChanged.connect(self.map_view.set_drawing_width)
        self.map_tools.addWidget(self.drawing_width)

        layout.addWidget(self.map_tools)
        layout.addWidget(self.map_view, 1)
        self.setCentralWidget(workspace)

    def _set_map_tool(self, tool_value: Any) -> None:
        self.map_view.set_drawing_tool(tool_value)

    def _choose_drawing_color(self) -> None:
        color = QColorDialog.getColor(QColor("#f9e2af"), self, "Couleur des annotations MJ")
        if color.isValid():
            self.map_view.set_drawing_color(color.name())

    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"Dock_{title.replace(' ', '_').upper()}")
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        self.network_panel = NetworkPanel(self.runtime, self)
        self.network_dock = self._dock(
            "Réseau", self.network_panel, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.network_dock.setMinimumWidth(285)

        self.combat_panel = CombatPanel(self.runtime, self)
        self.combat_dock = self._dock(
            "Combat", self.combat_panel, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.combat_dock.setMinimumWidth(345)

        self.registry_panel = EntityRegistryPanel(self.runtime, self)
        self.registry_dock = self._dock(
            "Entités", self.registry_panel, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.tabifyDockWidget(self.combat_dock, self.registry_dock)

        self.token_panel = TokenManagerPanel(self.runtime, self)
        self.token_dock = self._dock(
            "Tokens", self.token_panel, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.tabifyDockWidget(self.registry_dock, self.token_dock)

        self.pending_panel = PendingPanel(
            self.runtime,
            actions_executable=self.action_executor is not None,
            parent=self,
        )
        self.pending_dock = self._dock(
            "Demandes joueurs", self.pending_panel, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.tabifyDockWidget(self.token_dock, self.pending_dock)

        self.player_assignment_panel = PlayerAssignmentPanel(self.runtime, self)
        self.players_dock = self._dock(
            "Affectation joueurs",
            self.player_assignment_panel,
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.tabifyDockWidget(self.pending_dock, self.players_dock)
        self.combat_dock.raise_()

        self.bottom_tabs = QTabWidget()
        self.chat_panel = ChatPanel(self.bottom_tabs)
        self.journal_panel = JournalPanel(self.runtime, self.bottom_tabs)
        self.bottom_tabs.addTab(self.chat_panel, "Chat")
        self.bottom_tabs.addTab(self.journal_panel, "Journal")
        self.journal_dock = self._dock(
            "Communication / Journal", self.bottom_tabs, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.journal_dock.setMinimumHeight(175)
        self.bottom_tabs.setCurrentWidget(self.chat_panel)

    def _restore_layout(self) -> None:
        state = self._settings.value("dock_state")
        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        else:
            self.resizeDocks(
                [self.network_dock, self.combat_dock],
                [300, 360],
                Qt.Orientation.Horizontal,
            )
            self.resizeDocks(
                [self.journal_dock],
                [205],
                Qt.Orientation.Vertical,
            )
            self.combat_dock.raise_()
            self.bottom_tabs.setCurrentWidget(self.chat_panel)

    def _restore_default_layout(self) -> None:
        self._settings.remove("dock_state")
        self._settings.remove("geometry")
        self.resize(1600, 960)
        self._build_docks_for_restore()

    def _build_docks_for_restore(self) -> None:
        # Recreate only the dock arrangement, keeping all existing widgets alive.
        for dock in (self.network_dock, self.combat_dock, self.registry_dock,
                     self.token_dock, self.pending_dock, self.players_dock,
                     self.journal_dock):
            self.removeDockWidget(dock)
            dock.show()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.network_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.combat_dock)
        self.tabifyDockWidget(self.combat_dock, self.registry_dock)
        self.tabifyDockWidget(self.registry_dock, self.token_dock)
        self.tabifyDockWidget(self.token_dock, self.pending_dock)
        self.tabifyDockWidget(self.pending_dock, self.players_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.journal_dock)
        self.combat_dock.raise_()
        self.bottom_tabs.setCurrentWidget(self.chat_panel)
        self.resizeDocks(
            [self.network_dock, self.combat_dock],
            [300, 360],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.journal_dock],
            [205],
            Qt.Orientation.Vertical,
        )

    def _connect(self) -> None:
        self.map_view.runtime_changed.connect(self._runtime_changed)
        self.map_view.token_context_requested.connect(self._show_token_disclosure)
        self.map_view.status_message.connect(self.statusBar().showMessage)
        self.combat_panel.runtime_changed.connect(self._runtime_changed)
        self.combat_panel.action_requested.connect(self._gm_action_requested)
        self.network_panel.start_requested.connect(self.start_server)
        self.network_panel.stop_requested.connect(self.stop_server)
        self.player_assignment_panel.bind_requested.connect(self.bind_player)
        self.chat_panel.send_requested.connect(self.send_chat)
        self.pending_panel.approve_requested.connect(self.approve_pending)
        self.pending_panel.reject_requested.connect(self.reject_pending)
        self.pending_panel.reroll_requested.connect(self.reroll_pending)

        self.bridge.log_received.connect(self._network_log)
        self.bridge.pending_created.connect(self.pending_panel.add_or_update)
        self.bridge.pending_updated.connect(self.pending_panel.add_or_update)
        self.bridge.chat_received.connect(self._chat_received)
        self.bridge.client_connected.connect(lambda _u: self.refresh_all(force=True))
        self.bridge.client_disconnected.connect(lambda _u: self.refresh_all(force=True))
        self.bridge.async_done.connect(self._async_done)

    # ------------------------------------------------------------- runtime
    def add_entities(self) -> None:
        dialog = AddEntitiesDialog(self.runtime, self)
        if not dialog.exec():
            return
        added: list[str] = []
        for entity in dialog.entities():
            try:
                self.runtime.add_entity(entity)
            except Exception as exc:
                QMessageBox.critical(self, "Ajouter des entités", f"{entity.name} : {exc}")
                break
            added.append(entity.name)
        if added:
            self.statusBar().showMessage(f"Entités ajoutées : {', '.join(added)}", 6000)
            self._runtime_changed()

    def on_asset_saved(self, resource_type: str, resource_id: str) -> None:
        """Called by the IDE each time an asset is saved."""
        if resource_type == "maps":
            if resource_id == self.runtime.active_map_id:
                self.map_view.reload_active_map(reset_camera=False)
                self.broadcast_runtime_sync()
                self.statusBar().showMessage(f"Carte « {resource_id} » rechargée.", 4000)
            return
        kind = RESOURCE_KINDS.get(resource_type)
        if kind is None:
            return
        try:
            event = self.runtime.sync_asset(kind, resource_id)
        except Exception as exc:  # invalid or half-written file: keep the session untouched
            self.statusBar().showMessage(f"Synchronisation impossible ({resource_id}) : {exc}", 8000)
            return
        if event is not None:
            self.statusBar().showMessage(event.message, 5000)
            self._runtime_changed()

    def _map_selected(self) -> None:
        map_id = self.map_combo.currentData()
        if map_id == self.runtime.active_map_id:
            return
        try:
            self.runtime.change_map(map_id)
        except Exception as exc:
            QMessageBox.critical(self, "Change map", str(exc))
            idx = self.map_combo.findData(self.runtime.active_map_id)
            self.map_combo.blockSignals(True)
            self.map_combo.setCurrentIndex(max(0, idx))
            self.map_combo.blockSignals(False)
            return
        self.map_view.reload_active_map(reset_camera=True)
        self._runtime_changed()

    def _runtime_changed(self) -> None:
        self.refresh_all(force=True)
        self.broadcast_runtime_sync()

    def refresh_all(self, *, force: bool = False) -> None:
        seq = self.runtime.session.current_seq
        if force or seq != self._last_seq:
            self._last_seq = seq
            self.map_view.reload_active_map(reset_camera=False)
            self.registry_panel.refresh()
            self.token_panel.refresh()
            self.combat_panel.refresh(reset_list=False)
            self.journal_panel.refresh()
            self.player_assignment_panel.refresh_entities()
            self.statusBar().showMessage(
                f"Session {self.runtime.session.name}  •  seq {seq}  •  "
                f"map {self.runtime.active_map_id or 'none'}"
            )
        self.network_panel.refresh_connections(self.server)
        self.player_assignment_panel.refresh_players(self.server)
        self._refresh_server_summary()

    def _refresh_server_summary(self) -> None:
        if self._server_starting:
            self.server_summary_label.setText("● Démarrage du serveur…")
            return
        if self._server_stopping:
            self.server_summary_label.setText("● Arrêt du serveur…")
            return
        if self.server is None or not self.server.is_running:
            self.server_summary_label.setText("● Serveur arrêté · 0 joueur")
            return
        try:
            clients = list(self.server.clients.values())
        except RuntimeError:
            clients = []
        pings = [int(client.ping_ms) for client in clients if client.ping_ms is not None]
        average = f"{round(sum(pings) / len(pings))} ms" if pings else "ping —"
        self.server_summary_label.setText(
            f"● Serveur actif · {len(clients)} joueur(s) · {average}"
        )

    def save_session(self) -> None:
        try:
            path = self.runtime.save()
        except Exception as exc:
            QMessageBox.critical(self, "Sauvegarder la session", str(exc))
            return
        self.statusBar().showMessage(f"Session sauvegardée : {path}", 5000)

    def add_note(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Note de session", "Note :")
        if not ok or not text.strip():
            return
        self.runtime.add_note(text.strip())
        self._runtime_changed()

    def _show_token_disclosure(self, token_id: str) -> None:
        try:
            dialog = TokenDisclosureDialog(self.runtime, token_id, self)
        except Exception as exc:
            QMessageBox.warning(self, "Token", str(exc))
            return
        if dialog.exec():
            try:
                self.runtime.set_token_disclosure(token_id, dialog.selected_fields())
            except Exception as exc:
                QMessageBox.critical(self, "Token", str(exc))
                return
            self._runtime_changed()

    def _gm_action_requested(self, kind: str, action_id: str) -> None:
        self.statusBar().showMessage(
            f"Action MJ {kind}{f' : {action_id}' if action_id else ''} : resolver direct à connecter.",
            6000,
        )

    # ------------------------------------------------------------- network
    def _server_settings(self) -> ServerSettings:
        self.network_panel.save_settings()
        runtime_dir = self.campaign.root / ".runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        relay_enabled = self.network_panel.relay_enabled.isChecked()
        relay_host = self.network_panel.relay_host.text().strip()
        relay_key = self.network_panel.relay_key.text().strip()
        if relay_enabled and not relay_host:
            raise ValueError("Le relais VPS nécessite l'hôte du VPS.")
        relay = RelaySettings(
            enabled=relay_enabled,
            host=relay_host,
            user=self.network_panel.relay_user.text().strip() or "root",
            ssh_port=self.network_panel.relay_ssh_port.value(),
            public_port=self.network_panel.relay_port.value(),
            tunnel_port=self.network_panel.relay_tunnel_port.value(),
            key_file=relay_key,
        )
        return ServerSettings(
            port=self.network_panel.port.value(),
            password=self.network_panel.password.text(),
            tls=TLSSettings(
                cert_file=str(runtime_dir / "certs" / "server.crt"),
                key_file=str(runtime_dir / "certs" / "server.key"),
                common_name="rpg-tabletop-server",
            ),
            relay=relay,
            enable_firewall_setup=self.network_panel.firewall.isChecked(),
            enable_upnp=self.network_panel.upnp.isChecked(),
        )

    def _asset_catalog(self) -> AssetCatalog:
        catalog = AssetCatalog()
        if self.campaign.maps_path.is_dir():
            catalog.register_root("maps", self.campaign.maps_path)
        token_dir = self.campaign.character_path / "tokens"
        if token_dir.is_dir():
            catalog.register_root("character_tokens", token_dir)
        return catalog

    def start_server(self) -> None:
        if self.server is not None:
            return
        try:
            settings = self._server_settings()
            settings.validate()
            self.network_loop = AsyncioLoopThread()
            self.network_loop.start()
            self.server = create_runtime_server(
                self.runtime,
                settings,
                action_preparer=self.action_preparer,
                action_executor=self.action_executor,
                asset_catalog=self._asset_catalog(),
                on_log=self.bridge.on_log,
                on_pending=self.bridge.on_pending,
                on_pending_updated=self.bridge.on_pending_updated,
                on_client_connected=self.bridge.on_client_connected,
                on_client_disconnected=self.bridge.on_client_disconnected,
                on_chat=self.bridge.on_chat,
            )
            self._server_starting = True
            self._server_stopping = False
            self._submit(self.server.start(), "server_started")
            self.network_panel.set_running(True, "Démarrage du serveur…")
            self.player_assignment_panel.set_server_running(False)
            self.server_summary_label.setText("● Démarrage du serveur…")
            self.chat_panel.set_connected(False)
        except Exception as exc:
            self.server = None
            self.chat_panel.set_connected(False)
            if self.network_loop is not None:
                self.network_loop.stop()
                self.network_loop = None
            QMessageBox.critical(self, "Serveur réseau", str(exc))

    def stop_server(self) -> None:
        if self.server is None or self.network_loop is None:
            return
        self._server_stopping = True
        self._server_starting = False
        self.server_summary_label.setText("● Arrêt du serveur…")
        self._submit(self.server.stop(), "server_stopped")

    def send_chat(self, message: str) -> None:
        text = str(message).strip()
        if not text or self.server is None or not self.server.is_running or self.network_loop is None:
            self.statusBar().showMessage("Le serveur doit être actif pour envoyer un message.", 4000)
            return
        self.chat_panel.append_message("MJ", text)
        self._submit(self.server.broadcast(ChatMessage("MJ", text[:4000])), "gm_chat")

    def _chat_received(self, sender: str, message: str) -> None:
        self.chat_panel.append_message(sender, message)

    def _submit(self, coroutine, label: str) -> None:
        if self.network_loop is None:
            coroutine.close()
            return
        future = self.network_loop.submit(coroutine)

        def done(fut):
            try:
                result = fut.result()
                self.bridge.async_done.emit(label, result, None)
            except Exception as exc:
                self.bridge.async_done.emit(label, None, exc)

        future.add_done_callback(done)

    def _async_done(self, label: str, result, error) -> None:
        if error is not None:
            if label == "gm_chat":
                self.chat_panel.append_message("Système", str(error), system=True)
            else:
                QMessageBox.critical(self, "Runtime réseau", str(error))
            if label == "server_started":
                self._server_starting = False
                self._server_stopping = False
                self.network_panel.set_running(False)
                self.player_assignment_panel.set_server_running(False)
                self.chat_panel.set_connected(False)
                self.server = None
                self._shutdown_network_runner()
                self._refresh_server_summary()
            elif label == "server_stopped":
                self._server_stopping = False
                self._refresh_server_summary()
            return
        if label == "server_started" and self.server is not None:
            self._server_starting = False
            self._server_stopping = False
            info = self.server.connection_info()
            lines = [
                f"LAN : {info['lan']['host']}:{info['lan']['port']}",
                f"TLS SHA-256 : {info['tls_fingerprint_sha256']}",
            ]
            if info.get("upnp"):
                upnp = info["upnp"]
                if upnp.get("mapped"):
                    lines.append(f"WAN : {upnp.get('external_address')}:{upnp.get('port')}")
                if upnp.get("behind_cgnat"):
                    lines.append("CGNAT détecté — les joueurs distants doivent utiliser le VPS relay")
            if info.get("relay"):
                lines.append(f"Relay : {info['relay']['host']}:{info['relay']['port']}")
            self.network_panel.set_running(True, "\n".join(lines))
            self.player_assignment_panel.set_server_running(True)
            self.chat_panel.set_connected(True)
            self.refresh_all(force=True)
        elif label == "server_stopped":
            self._server_stopping = False
            self._server_starting = False
            self.network_panel.set_running(False)
            self.player_assignment_panel.set_server_running(False)
            self.chat_panel.set_connected(False)
            self.server = None
            self._shutdown_network_runner()
            self.refresh_all(force=True)
        elif label.startswith("approve:") or label.startswith("reject:"):
            approval_id = label.split(":", 1)[1]
            self.pending_panel.remove(approval_id)
            self.refresh_all(force=True)
        elif label.startswith("bind:"):
            self.refresh_all(force=True)

    def _shutdown_network_runner(self) -> None:
        if self.network_loop is not None:
            self.network_loop.stop()
            self.network_loop = None

    def _network_log(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def bind_player(self, username: str, entity_id: str) -> None:
        if self.server is None:
            return
        self._submit(self.server.bind_player(username, entity_id), f"bind:{username}")

    def approve_pending(self, approval_id: str, targets, dice) -> None:
        if self.server is None:
            return
        try:
            if targets is not None or dice is not None:
                self.server.edit_pending_action(approval_id, targets=targets, dice=dice)
        except Exception as exc:
            QMessageBox.warning(self, "Demande joueur", str(exc))
            return
        self._submit(self.server.approve_pending(approval_id), f"approve:{approval_id}")

    def reject_pending(self, approval_id: str, reason: str) -> None:
        if self.server is None:
            return
        self._submit(self.server.reject_pending(approval_id, reason=reason), f"reject:{approval_id}")

    def reroll_pending(self, approval_id: str, dice_key: str) -> None:
        if self.server is None:
            return
        try:
            pending = self.server.reroll_pending_die(approval_id, dice_key)
            self.pending_panel.add_or_update(pending)
        except Exception as exc:
            QMessageBox.warning(self, "Demande joueur", str(exc))

    def broadcast_runtime_sync(self) -> None:
        if self.server is not None and self.server.is_running and self.network_loop is not None:
            self._submit(self.server.broadcast_runtime_sync(), "sync")

    # ------------------------------------------------------------- close
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.refresh_timer.stop()
        self._settings.setValue("dock_state", self.saveState())
        self._settings.setValue("geometry", self.saveGeometry())
        try:
            self.runtime.save()
        except Exception:
            pass
        if self.server is not None and self.network_loop is not None:
            future = self.network_loop.submit(self.server.stop())
            try:
                future.result(timeout=4.0)
            except Exception:
                pass
            self.server = None
        self._shutdown_network_runner()
        event.accept()
