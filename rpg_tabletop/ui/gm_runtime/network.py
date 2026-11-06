# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.session.runtime import SessionRuntime


class NetworkPanel(QWidget):
    """Configuration et état du serveur hébergé par le MJ.

    La gestion des joueurs et leur association à une Entity sont volontairement
    séparées dans PlayerAssignmentPanel.
    """

    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.settings = QSettings("RPGTabletop", "GMRuntime")
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("SERVEUR")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)

        self.status_pill = QLabel("● Serveur arrêté")
        self.status_pill.setObjectName("RuntimeServerStatus")
        root.addWidget(self.status_pill)

        self.players_summary = QLabel("0 joueur connecté · Ping moyen —")
        self.players_summary.setObjectName("RuntimeMuted")
        root.addWidget(self.players_summary)

        form = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.settings.value("server/port", 7799)))
        self.password = QLineEdit("ROOM42")
        self.password.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.firewall = QCheckBox("Configuration automatique du pare-feu Windows")
        self.firewall.setChecked(self._setting_bool("server/firewall", True))
        self.upnp = QCheckBox("UPnP")
        self.upnp.setChecked(self._setting_bool("server/upnp", True))
        self.relay_enabled = QCheckBox("Relais VPS")
        self.relay_enabled.setChecked(self._setting_bool("relay/enabled", False))
        self.relay_host = QLineEdit(str(self.settings.value("relay/host", "")))
        self.relay_host.setPlaceholderText("relay.example.com")
        self.relay_user = QLineEdit(str(self.settings.value("relay/user", "root")))
        self.relay_ssh_port = QSpinBox()
        self.relay_ssh_port.setRange(1, 65535)
        self.relay_ssh_port.setValue(int(self.settings.value("relay/ssh_port", 22)))
        self.relay_port = QSpinBox()
        self.relay_port.setRange(1, 65535)
        self.relay_port.setValue(int(self.settings.value("relay/public_port", 7799)))
        self.relay_tunnel_port = QSpinBox()
        self.relay_tunnel_port.setRange(1, 65535)
        self.relay_tunnel_port.setValue(int(self.settings.value("relay/tunnel_port", 17799)))
        self.relay_key = QLineEdit(str(self.settings.value("relay/key_file", "")))
        self.relay_key.setPlaceholderText("Chemin de la clé privée SSH")

        relay_hint = QLabel(
            "Valeurs historiques : serveur MJ 7799 · port public VPS 7799 · "
            "tunnel SSH 17799. L'hôte VPS reste à renseigner ; la clé privée est "
            "optionnelle si SSH utilise déjà un agent ou une identité configurée."
        )
        relay_hint.setObjectName("RuntimeMuted")
        relay_hint.setWordWrap(True)

        form.addRow("Port", self.port)
        form.addRow("Mot de passe de la room", self.password)
        form.addRow("", self.firewall)
        form.addRow("", self.upnp)
        form.addRow("", self.relay_enabled)
        form.addRow("Hôte VPS", self.relay_host)
        form.addRow("Utilisateur SSH", self.relay_user)
        form.addRow("Port SSH", self.relay_ssh_port)
        form.addRow("Port public", self.relay_port)
        form.addRow("Port du tunnel", self.relay_tunnel_port)
        form.addRow("Clé SSH privée", self.relay_key)
        form.addRow("", relay_hint)
        root.addLayout(form)

        buttons = QHBoxLayout()
        self.start = QPushButton("Démarrer le serveur")
        self.start.setObjectName("PrimaryButton")
        self.stop = QPushButton("Arrêter")
        self.stop.setObjectName("DangerButton")
        self.stop.setEnabled(False)
        self.start.clicked.connect(self.start_requested)
        self.stop.clicked.connect(self.stop_requested)
        buttons.addWidget(self.start)
        buttons.addWidget(self.stop)
        root.addLayout(buttons)

        self.info = QLabel("Serveur arrêté")
        self.info.setObjectName("RuntimeMuted")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.info)
        root.addStretch(1)

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "on"}
        return bool(value)

    def save_settings(self) -> None:
        self.settings.setValue("server/port", self.port.value())
        self.settings.setValue("server/firewall", self.firewall.isChecked())
        self.settings.setValue("server/upnp", self.upnp.isChecked())
        self.settings.setValue("relay/enabled", self.relay_enabled.isChecked())
        self.settings.setValue("relay/host", self.relay_host.text().strip())
        self.settings.setValue("relay/user", self.relay_user.text().strip() or "root")
        self.settings.setValue("relay/ssh_port", self.relay_ssh_port.value())
        self.settings.setValue("relay/public_port", self.relay_port.value())
        self.settings.setValue("relay/tunnel_port", self.relay_tunnel_port.value())
        self.settings.setValue("relay/key_file", self.relay_key.text().strip())

    def set_running(self, running: bool, info: str = "") -> None:
        self.start.setEnabled(not running)
        self.stop.setEnabled(running)
        if not running:
            self.status_pill.setText("● Serveur arrêté")
        elif str(info).casefold().startswith("démarrage"):
            self.status_pill.setText("● Démarrage…")
        else:
            self.status_pill.setText("● Serveur actif")

        for widget in (
            self.port,
            self.password,
            self.firewall,
            self.upnp,
            self.relay_enabled,
            self.relay_host,
            self.relay_user,
            self.relay_ssh_port,
            self.relay_port,
            self.relay_tunnel_port,
            self.relay_key,
        ):
            widget.setEnabled(not running)
        self.info.setText(info or ("Serveur actif" if running else "Serveur arrêté"))

    def refresh_connections(self, server) -> None:
        """Affiche uniquement le résumé de connexion, sans gérer les bindings."""
        if server is None:
            self.players_summary.setText("0 joueur connecté · Ping moyen —")
            return
        try:
            clients = list(server.clients.values())
        except RuntimeError:
            clients = []
        pings = [int(client.ping_ms) for client in clients if client.ping_ms is not None]
        average = f"{round(sum(pings) / len(pings))} ms" if pings else "—"
        self.players_summary.setText(
            f"{len(clients)} joueur(s) connecté(s) · Ping moyen {average}"
        )
