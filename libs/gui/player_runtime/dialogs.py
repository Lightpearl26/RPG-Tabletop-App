# -*- coding: utf-8 -*-
"""Login and token-inspection dialogs for the Player Runtime."""
from __future__ import annotations

from hashlib import sha256

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QSpinBox, QVBoxLayout, QWidget,
)

from ...net import ClientSettings


class PlayerLoginDialog(QDialog):
    """Minimal player login; TLS trust is pinned automatically on first use."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connexion au serveur MJ")
        self.setMinimumWidth(420)
        self.settings = QSettings("RPGTabletop", "PlayerRuntime")

        root = QVBoxLayout(self)
        intro = QLabel(
            "Connecte-toi à la partie hébergée par le MJ. "
            "L'adresse et le port peuvent être ceux du VPS relay."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.host = QLineEdit(str(self.settings.value("host", "")))
        self.host.setPlaceholderText("Adresse du MJ ou du VPS relay")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(self.settings.value("port", 7799)))
        self.username = QLineEdit(str(self.settings.value("username", "Player")))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Adresse du serveur", self.host)
        form.addRow("Port", self.port)
        form.addRow("Nom de joueur", self.username)
        form.addRow("Mot de passe de la room", self.password)
        root.addLayout(form)

        note = QLabel(
            "La connexion est chiffrée automatiquement. Le certificat du serveur est "
            "mémorisé au premier contact ; aucun fingerprint ni réglage TLS n'est à saisir. "
            "Le joueur ne choisit pas son Entity : le MJ effectue l'association depuis son Runtime."
        )
        note.setWordWrap(True)
        note.setObjectName("PlayerHint")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Se connecter")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def trust_key(host: str, port: int) -> str:
        digest = sha256(f"{host.strip().casefold()}:{int(port)}".encode("utf-8")).hexdigest()
        return f"trusted_fingerprints/{digest}"

    def accept(self) -> None:
        host = self.host.text().strip()
        username = self.username.text().strip()
        if not host or not username or not self.password.text():
            QMessageBox.warning(
                self,
                "Connexion",
                "Adresse, nom de joueur et mot de passe de la room sont obligatoires.",
            )
            return
        self.settings.setValue("host", host)
        self.settings.setValue("port", self.port.value())
        self.settings.setValue("username", username)
        super().accept()

    def client_settings(self) -> ClientSettings:
        host = self.host.text().strip()
        port = self.port.value()
        fingerprint = str(self.settings.value(self.trust_key(host, port), ""))
        return ClientSettings(
            host=host,
            port=port,
            username=self.username.text().strip(),
            password=self.password.text(),
            expected_fingerprint=fingerprint,
            trust_on_first_use=True,
        )


class TokenInspectDialog(QDialog):
    """Displays only fields returned by the server's public_entity_view()."""

    FIELD_LABELS = {
        "name": "Nom", "hp": "HP", "stamina": "Stamina",
        "str": "STR", "con": "CON", "dex": "DEX", "agi": "AGI",
        "int": "INT", "wis": "WIS", "cha": "CHA", "luc": "LUC",
        "sur": "SUR", "per": "PER",
    }

    def __init__(self, entity_view: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Informations du token")
        self.setMinimumWidth(280)
        root = QVBoxLayout(self)
        name = str(entity_view.get("name") or "Token")
        title = QLabel(name)
        title.setObjectName("PlayerTitle")
        root.addWidget(title)
        visible = [
            (key, value) for key, value in entity_view.items()
            if key in self.FIELD_LABELS and key != "name"
        ]
        if not visible:
            note = QLabel("Aucune information supplémentaire n'a été dévoilée par le MJ.")
            note.setWordWrap(True)
            note.setObjectName("PlayerMuted")
            root.addWidget(note)
        else:
            form = QFormLayout()
            for key, value in visible:
                maximum = entity_view.get(f"{key}_max")
                text = f"{value} / {maximum}" if maximum is not None else str(value)
                form.addRow(self.FIELD_LABELS[key], QLabel(text))
            root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
