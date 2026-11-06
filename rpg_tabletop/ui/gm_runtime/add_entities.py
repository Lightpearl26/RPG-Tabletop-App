# -*- coding: utf-8 -*-
"""Add entities to a running session: from the campaign, or cloned from a character."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.session import store
from rpg_tabletop.session.entity import Entity
from rpg_tabletop.session.runtime import SessionRuntime


class AddEntitiesDialog(QDialog):
    """
    Collects the Entity objects to add; the caller records them with
    `SessionRuntime.add_entity`.

    - Campaign tab: entities saved in the campaign that are not in the session yet.
    - Character tab: one or several fresh entities built from a character. They
      only live in the session (their definition is embedded in the journal).
    """

    def __init__(self, runtime: SessionRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._entities: list[Entity] = []
        self.setWindowTitle("Ajouter des entités à la session")
        self.resize(440, 520)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_campaign_tab(), "Entités de la campagne")
        self.tabs.addTab(self._build_character_tab(), "Depuis un personnage")
        root.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ajouter")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def entities(self) -> list[Entity]:
        """The entities built when the dialog was accepted."""
        return list(self._entities)

    # ------------------------------------------------------------------ tabs
    def _session_names(self) -> set[str]:
        return {entity.name.casefold() for entity in self.runtime.entities.values()}

    def _build_campaign_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.campaign_list = QListWidget()
        self.campaign_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        present = self._session_names()
        for name in store.entity_names(self.runtime.campaign):
            if name.casefold() not in present:
                self.campaign_list.addItem(name)
        layout.addWidget(self.campaign_list, 1)
        if self.campaign_list.count() == 0:
            layout.addWidget(QLabel("Toutes les entités de la campagne sont déjà dans la session."))
        return page

    def _build_character_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.character_input = QComboBox()
        self.character_input.addItems(store.character_names(self.runtime.campaign))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Par défaut : le nom du personnage")
        self.count_input = QSpinBox()
        self.count_input.setRange(1, 50)
        hint = QLabel(
            "Avec plusieurs exemplaires, les noms sont numérotés (Gobelin 1, Gobelin 2…). "
            "Ces entités n'existent que dans la session."
        )
        hint.setWordWrap(True)
        form.addRow("Personnage", self.character_input)
        form.addRow("Nom", self.name_input)
        form.addRow("Exemplaires", self.count_input)
        form.addRow(hint)
        return page

    def _filter(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.campaign_list.count()):
            item = self.campaign_list.item(row)
            item.setHidden(bool(query) and query not in item.text().casefold())

    # ----------------------------------------------------------------- build
    def _from_campaign(self) -> list[Entity]:
        entities: list[Entity] = []
        for item in self.campaign_list.selectedItems():
            try:
                entity = Entity.from_name(item.text())
            except (TypeError, ValueError):  # a file without a valid id
                entity = None
            if entity is None:
                raise ValueError(f"Entité introuvable, sans id ou sans personnage : {item.text()}")
            entities.append(entity)
        return entities

    def _from_character(self) -> list[Entity]:
        character = self.character_input.currentText().strip()
        if not character:
            raise ValueError("Aucun personnage disponible.")
        base = self.name_input.text().strip() or character
        if any(c in store.INVALID_NAME_CHARACTERS for c in base):
            raise ValueError("Le nom contient un caractère invalide.")
        count = self.count_input.value()
        taken = self._session_names() | {n.casefold() for n in store.entity_names(self.runtime.campaign)}

        names: list[str] = []
        if count == 1:
            if base.casefold() in taken:
                raise ValueError(f"Le nom « {base} » est déjà utilisé.")
            names.append(base)
        else:
            number = 1
            while len(names) < count:
                candidate = f"{base} {number}"
                number += 1
                if candidate.casefold() not in taken:
                    names.append(candidate)
                    taken.add(candidate.casefold())

        entities: list[Entity] = []
        for name in names:
            entity = Entity.from_character(name, character)
            if entity is None:
                raise ValueError(f"Personnage introuvable : {character}")
            entities.append(entity)
        return entities

    def accept(self) -> None:
        try:
            entities = self._from_campaign() if self.tabs.currentIndex() == 0 else self._from_character()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Ajouter des entités", str(error))
            return
        if not entities:
            QMessageBox.information(self, "Ajouter des entités", "Rien n'est sélectionné.")
            return
        self._entities = entities
        super().accept()
