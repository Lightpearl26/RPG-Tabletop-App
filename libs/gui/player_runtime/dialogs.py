# -*- coding: utf-8 -*-
"""Login and token-inspection dialogs for the Player Runtime."""
from __future__ import annotations

import random
from hashlib import sha256

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
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


class InitiativeRollDialog(QDialog):
    """
    The GM starts a combat and asks for the player's d20.

    The player rolls here, or types the result of a real die, then sends it.
    """

    roll_chosen = Signal(int)

    def __init__(self, entity_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Initiative")
        self.setModal(False)
        self.setMinimumWidth(340)
        root = QVBoxLayout(self)
        title = QLabel(f"Initiative — {entity_name}" if entity_name else "Initiative")
        title.setObjectName("PlayerTitle")
        root.addWidget(title)
        hint = QLabel(
            "Le MJ lance le combat. Lance ton d20, ou saisis le résultat de ton dé : "
            "le plus petit résultat joue en premier."
        )
        hint.setWordWrap(True)
        hint.setObjectName("PlayerMuted")
        root.addWidget(hint)

        row = QHBoxLayout()
        self.value = QSpinBox()
        self.value.setRange(0, 20)
        self.value.setSpecialValueText("—")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setMinimumHeight(48)
        self.value.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.value.valueChanged.connect(self._sync)
        roll = QPushButton("Lancer le d20")
        roll.clicked.connect(self._roll)
        row.addWidget(self.value, 1)
        row.addWidget(roll)
        root.addLayout(row)

        buttons = QDialogButtonBox()
        self.send_button = buttons.addButton("Envoyer au MJ", QDialogButtonBox.ButtonRole.AcceptRole)
        self.send_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self._sync()

    def _roll(self) -> None:
        self.value.setValue(random.randint(1, 20))

    def _sync(self, *_args) -> None:
        self.send_button.setEnabled(self.value.value() > 0)

    def accept(self) -> None:
        if self.value.value() <= 0:
            return
        self.roll_chosen.emit(self.value.value())
        super().accept()


class DiceRollDialog(QDialog):
    """
    The GM's app asks the player to roll dice of an action: the d100 of an
    attack, a defense or a spell, or damage dice.

    The player rolls here, or types the results of real dice, then sends them.
    The GM still checks them before anything happens.
    """

    roll_chosen = Signal(str, str, list)

    PER_ROW = 5

    def __init__(
        self,
        approval_id: str,
        key: str,
        expression: str,
        label: str,
        *,
        retry: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.approval_id = str(approval_id)
        self.key = str(key)
        count_text, _sep, size_text = str(expression).lower().partition("d")
        self.count = max(1, int(count_text or 1))
        self.size = max(2, int(size_text or 100))
        self.setWindowTitle("Jet de dés")
        self.setModal(False)
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)
        title = QLabel(label or f"Lance {expression}")
        title.setObjectName("PlayerTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        hint_text = (
            f"Lance {self.count}d{self.size} ici, ou saisis le résultat de tes vrais dés. "
            "Le MJ vérifie ton jet avant qu'il compte."
        )
        if retry:
            hint_text = "Le MJ a refusé ton jet : relance tes dés. " + hint_text
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setObjectName("PlayerMuted")
        root.addWidget(hint)

        self.values: list[QSpinBox] = []
        row: QHBoxLayout | None = None
        for index in range(self.count):
            if index % self.PER_ROW == 0:
                row = QHBoxLayout()
                root.addLayout(row)
            box = QSpinBox()
            box.setRange(0, self.size)
            box.setSpecialValueText("—")
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setMinimumHeight(44)
            box.setStyleSheet("font-size: 20px; font-weight: 700;")
            box.valueChanged.connect(self._sync)
            self.values.append(box)
            assert row is not None
            row.addWidget(box)

        footer = QHBoxLayout()
        self.total_label = QLabel("")
        self.total_label.setObjectName("PlayerMuted")
        roll = QPushButton(f"Lancer {self.count}d{self.size}")
        roll.clicked.connect(self._roll)
        footer.addWidget(self.total_label, 1)
        footer.addWidget(roll)
        root.addLayout(footer)

        buttons = QDialogButtonBox()
        self.send_button = buttons.addButton("Envoyer au MJ", QDialogButtonBox.ButtonRole.AcceptRole)
        self.send_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self._sync()

    def _roll(self) -> None:
        for box in self.values:
            box.setValue(random.randint(1, self.size))

    def chosen(self) -> list[int]:
        return [box.value() for box in self.values]

    def _sync(self, *_args) -> None:
        values = self.chosen()
        ready = all(value > 0 for value in values)
        self.send_button.setEnabled(ready)
        self.total_label.setText(f"Total : {sum(values)}" if ready and len(values) > 1 else "")

    def accept(self) -> None:
        values = self.chosen()
        if not all(value > 0 for value in values):
            return
        self.roll_chosen.emit(self.approval_id, self.key, values)
        super().accept()
