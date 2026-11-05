# -*- coding: utf-8 -*-
"""
GM character sheet of the Runtime.

Opened by a right click on a token (map or Tokens panel). Everything about one
entity is shown and edited here: stat modifiers, inventory, known spells, active
effects, running spell events and what players may see of its token.

Each change is applied at once as a session event, so the journal, the players
and time travel all follow. The edited fields are written to the entity file
(`entities/<name>.json`) at the next session save.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...assets.spell import TARGET_STATS, EffectType, RuntimePolicy
from ...runtime import session_store as store
from ...runtime.asset_writeback import entity_files_by_id
from ...runtime.entity import Entity
from ...runtime.rules import DISCLOSABLE_FIELDS, PRIVATE_STATS
from ...runtime.session import SessionRuntime
from ...runtime.spell_effect import SpellEffect
from ..widgets.sheet_parts import (
    CORE_STATS,
    EFFECT_LABELS,
    EFFECTS_COLUMNS,
    EVENTS_COLUMNS,
    ITEMS_COLUMNS,
    POLICY_LABELS,
    ROLE_ID,
    STAT_COLORS,
    STATS_COLUMNS,
    SheetHeader,
    SpellGrid,
    cell,
    duration_label,
    effect_origin_label,
    effect_value_label,
    header_label,
    make_table,
    modifiers_label,
    muted,
    set_resource_bar,
    stat_label,
)


RESOURCE_STATS = ("hp", "stamina", "mental_health", "drug_health")
SHEET_STATS = (*RESOURCE_STATS, *CORE_STATS)
EDIT_DELAY_MS = 450


def _selected_key(table: QTableWidget) -> Optional[str]:
    rows = table.selectionModel().selectedRows()
    if not rows:
        return None
    item = table.item(rows[0].row(), 0)
    return str(item.data(ROLE_ID)) if item is not None and item.data(ROLE_ID) is not None else None


def _select_key(table: QTableWidget, key: Optional[str]) -> None:
    if key is None:
        return
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and str(item.data(ROLE_ID)) == key:
            table.selectRow(row)
            return


def _stat_breakdown(entity: Entity, stat: str) -> tuple[int, int, int, int, int]:
    """(base, GM modifier, items, effects, total), as Entity.get_stat adds them."""
    base, modifier, items, effects = entity.stat_breakdown(stat)
    return base, modifier, items, effects, base + modifier + items + effects


# =============================================================================
# Delayed commits
# =============================================================================


class _PendingEdits(QObject):
    """
    Group the quick changes of one field into one session event.

    Spin box arrows emit a value per click; the edit is committed once the GM
    stops for EDIT_DELAY_MS, or right away before the sheet is redrawn.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._pending: dict[str, tuple[QTimer, Callable[[], None]]] = {}

    def schedule(self, key: str, commit: Callable[[], None]) -> None:
        timer = self._pending.get(key, (None, None))[0]
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(EDIT_DELAY_MS)
            timer.timeout.connect(lambda key=key: self._run(key))
        self._pending[key] = (timer, commit)
        timer.start()

    def is_pending(self, key: str) -> bool:
        return key in self._pending

    def _run(self, key: str) -> None:
        entry = self._pending.pop(key, None)
        if entry is None:
            return
        timer, commit = entry
        timer.stop()
        timer.deleteLater()
        commit()

    def flush(self) -> None:
        for key in list(self._pending):
            self._run(key)


# =============================================================================
# Dialogs
# =============================================================================


class EffectDialog(QDialog):
    """Create or change one active effect."""

    def __init__(
        self,
        effect: Optional[SpellEffect] = None,
        parent: QWidget | None = None,
        *,
        spell_source: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier l'effet" if effect else "Ajouter un effet")
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.origin_input = QLineEdit()
        self.origin_input.setMaxLength(200)
        self.origin_input.setPlaceholderText("ex. Rocher tombé, Piège à pointes, Potion avariée…")

        self.type_input = QComboBox()
        for effect_type in EffectType:
            self.type_input.addItem(EFFECT_LABELS.get(effect_type.value, effect_type.value), effect_type.value)
        self.stat_input = QComboBox()
        for stat in TARGET_STATS:
            self.stat_input.addItem(stat_label(stat), stat)
        self.delta_input = QSpinBox()
        self.delta_input.setRange(-99999, 99999)
        self.permanent_input = QCheckBox("Permanent")
        self.duration_input = QSpinBox()
        self.duration_input.setRange(1, 9999)
        self.duration_input.setSuffix(" tour(s)")
        self.permanent_input.toggled.connect(lambda on: self.duration_input.setEnabled(not on))

        duration_row = QHBoxLayout()
        duration_row.addWidget(self.permanent_input)
        duration_row.addWidget(self.duration_input, 1)
        form.addRow("Origine", self.origin_input)
        if spell_source:
            form.addRow("", muted(f"Créé par le sort {spell_source}."))
        form.addRow("Type", self.type_input)
        form.addRow("Statistique", self.stat_input)
        form.addRow("Valeur (Δ)", self.delta_input)
        form.addRow("Durée", duration_row)
        root.addLayout(form)
        root.addWidget(muted(
            "La valeur s'ajoute à la statistique : négative pour des dégâts ou un malus. "
            "Pour la réduction et l'amplification de dégâts, c'est un pourcentage. "
            "Seuls les sorts décomptent les durées : un effet ajouté ici garde la sienne "
            "jusqu'à ce que tu la changes."
        ))

        if effect is not None:
            self.type_input.setCurrentIndex(max(0, self.type_input.findData(EffectType(effect.effect_type).value)))
            self.stat_input.setCurrentIndex(max(0, self.stat_input.findData(str(effect.target_stat))))
            self.delta_input.setValue(int(effect.delta))
            self.permanent_input.setChecked(int(effect.duration) < 0)
            self.duration_input.setValue(max(1, int(effect.duration)))
            self.origin_input.setText(effect.origin)
        else:
            self.type_input.setCurrentIndex(max(0, self.type_input.findData("buff")))
            self.permanent_input.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        return {
            "effect_type": self.type_input.currentData(),
            "stat": self.stat_input.currentData(),
            "delta": self.delta_input.value(),
            "duration": -1 if self.permanent_input.isChecked() else self.duration_input.value(),
            "origin": self.origin_input.text().strip(),
        }


class SpellEventDialog(QDialog):
    """Change the progression of a running spell event."""

    def __init__(self, nb_cast: int, finished: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modifier le sort en cours")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.cast_input = QSpinBox()
        self.cast_input.setRange(0, 9999)
        self.cast_input.setValue(int(nb_cast))
        self.finished_input = QCheckBox("Terminé")
        self.finished_input.setChecked(bool(finished))
        form.addRow("Lancers effectués", self.cast_input)
        form.addRow("", self.finished_input)
        root.addLayout(form)
        root.addWidget(muted(
            "Les lancers comptent la progression du sort (maintenu, différé…). "
            "Marquer le sort terminé garde ses effets ; « Arrêter » les retire."
        ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


# =============================================================================
# Sheet
# =============================================================================


class GMCharacterSheet(QDialog):
    """Non-modal sheet of one session entity, for the GM."""

    # Emitted after each change recorded in the session
    runtime_changed = Signal()

    def __init__(self, runtime: SessionRuntime, entity_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if str(entity_id) not in runtime.entities:
            raise KeyError(f"Unknown entity: {entity_id}")
        self.runtime = runtime
        self.entity_id = str(entity_id)
        self._signature: Optional[str] = None
        self._refreshing = False
        self._pending = _PendingEdits(self)
        self._asset_file = entity_files_by_id(runtime.campaign).get(self.entity_id)

        self.setModal(False)
        self.setObjectName("RuntimePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(760, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stats_tab(), "Caractéristiques")
        self.tabs.addTab(self._build_items_tab(), "Inventaire")
        self.tabs.addTab(self._build_spells_tab(), "Sorts")
        self.tabs.addTab(self._build_effects_tab(), "Effets actifs")
        self.tabs.addTab(self._build_events_tab(), "Sorts en cours")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addWidget(muted(
            "Chaque modification est appliquée tout de suite à la session. "
            "Elle est écrite dans le fichier de l'entité à la prochaine sauvegarde (Ctrl+S)."
        ), 1)
        close = QPushButton("Fermer")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        self.refresh(force=True)

    @property
    def entity(self) -> Optional[Entity]:
        return self.runtime.entities.get(self.entity_id)

    def show_tab(self, name: str) -> None:
        index = {"stats": 0, "visibility": 0, "items": 1, "spells": 2, "effects": 3, "events": 4}.get(name)
        if index is not None:
            self.tabs.setCurrentIndex(index)

    # ------------------------------------------------------------------ build
    def _build_header(self) -> SheetHeader:
        self.header = SheetHeader(RESOURCE_STATS)
        for stat in PRIVATE_STATS:
            self.header.bars[stat].setToolTip("Visible par le MJ seulement")
        self.asset_label = muted()
        self.pending_label = QLabel("● Modifications à écrire dans le fichier à la prochaine sauvegarde")
        self.pending_label.setStyleSheet("color: #e1c27a;")
        self.header.info.insertWidget(2, self.asset_label)
        self.header.info.insertWidget(3, self.pending_label)
        return self.header

    def _build_stats_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        # Player visibility of the token: the name here, each stat on its row
        self.disclosure_checks: dict[str, QCheckBox] = {}
        visibility = QHBoxLayout()
        visibility.setSpacing(8)
        title = QLabel("Visible par les joueurs :")
        title.setStyleSheet("font-weight: 700; color: #cbd2dc;")
        name_check = QCheckBox("Nom")
        name_check.toggled.connect(lambda _on: self._disclosure_changed())
        self.disclosure_checks["name"] = name_check
        self.show_all_button = QPushButton("Tout montrer")
        self.show_all_button.clicked.connect(lambda: self._set_disclosure(list(DISCLOSABLE_FIELDS)))
        self.hide_all_button = QPushButton("Tout masquer")
        self.hide_all_button.clicked.connect(lambda: self._set_disclosure([]))
        visibility.addWidget(title)
        visibility.addWidget(name_check)
        visibility.addStretch(1)
        visibility.addWidget(self.show_all_button)
        visibility.addWidget(self.hide_all_button)
        root.addLayout(visibility)
        self.visibility_missing = muted(
            "Pas de token sur la carte active : place l'entité sur la carte pour choisir "
            "ce que les joueurs en voient."
        )
        self.visibility_missing.setStyleSheet("color: #e1c27a;")
        root.addWidget(self.visibility_missing)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        headers = (*STATS_COLUMNS, "Joueurs")
        for column, title in enumerate(headers):
            label = header_label(title)
            if title == "Modif.":
                label.setToolTip("Modificateur MJ : la part de la stat réglée à la main")
            if title == "Joueurs":
                label.setToolTip(
                    "Coché : envoyé aux joueurs qui voient le token (PV et stamina "
                    "affichent aussi leur barre sur la carte)."
                )
            grid.addWidget(label, 0, column)

        self.stat_cells: dict[str, tuple[QLabel, QSpinBox, QLabel, QLabel, QLabel]] = {}
        row = 1
        for stat in SHEET_STATS:
            if stat == CORE_STATS[0]:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("color: #303642;")
                grid.addWidget(line, row, 0, 1, len(headers))
                row += 1
            name = QLabel(stat_label(stat) + ("  · MJ" if stat in PRIVATE_STATS else ""))
            name.setStyleSheet(f"font-weight: 700; color: {STAT_COLORS.get(stat, '#e5e9ef')};")
            if stat in PRIVATE_STATS:
                name.setToolTip("Jamais montrée aux joueurs")
            base, items, effects, total = QLabel(), QLabel(), QLabel(), QLabel()
            total.setStyleSheet("font-weight: 700;")
            spin = QSpinBox()
            spin.setRange(-99999, 99999)
            spin.setKeyboardTracking(False)
            spin.setFixedWidth(96)
            spin.valueChanged.connect(lambda value, stat=stat: self._stat_changed(stat, value))
            if stat in DISCLOSABLE_FIELDS:
                visible: QWidget = QCheckBox()
                visible.setToolTip(f"{stat_label(stat)} visible par les joueurs")
                visible.toggled.connect(lambda _on: self._disclosure_changed())
                self.disclosure_checks[stat] = visible
            else:
                visible = muted("MJ")
                visible.setToolTip("Jamais montrée aux joueurs")
            for column, widget in enumerate((name, base, spin, items, effects, total, visible)):
                grid.addWidget(widget, row, column)
            self.stat_cells[stat] = (base, spin, items, effects, total)
            row += 1
        grid.setColumnStretch(len(headers), 1)
        root.addLayout(grid)
        root.addWidget(muted(
            "Le modificateur MJ est la part de la stat que tu règles à la main : il inclut "
            "aussi la stamina dépensée par les sorts. Total = base + modificateur + objets + effets."
        ))
        root.addStretch(1)
        return page

    def _build_items_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        self.items_table = make_table(ITEMS_COLUMNS)
        self.items_table.setColumnWidth(0, 170)
        self.items_table.setColumnWidth(1, 90)
        self.items_table.setColumnWidth(2, 170)
        root.addWidget(self.items_table, 1)

        row = QHBoxLayout()
        self.item_input = QComboBox()
        self.item_input.setMinimumWidth(220)
        self.item_quantity = QSpinBox()
        self.item_quantity.setRange(1, 9999)
        add = QPushButton("Ajouter")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add_item)
        remove = QPushButton("Retirer")
        remove.setObjectName("DangerButton")
        remove.clicked.connect(self._remove_item)
        row.addWidget(self.item_input, 1)
        row.addWidget(self.item_quantity)
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(remove)
        root.addLayout(row)
        return page

    def _build_spells_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)

        self.spell_grid = SpellGrid(forgettable=True)
        self.spell_grid.forget_requested.connect(self._remove_spell)
        root.addWidget(self.spell_grid, 1)

        row = QHBoxLayout()
        self.spell_input = QComboBox()
        self.spell_input.setMinimumWidth(220)
        learn = QPushButton("Apprendre")
        learn.setObjectName("PrimaryButton")
        learn.clicked.connect(self._add_spell)
        row.addWidget(self.spell_input, 1)
        row.addWidget(learn)
        row.addStretch(1)
        root.addLayout(row)
        return page

    def _build_effects_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        self.effects_table = make_table(EFFECTS_COLUMNS)
        self.effects_table.setColumnWidth(0, 220)
        self.effects_table.setColumnWidth(1, 170)
        self.effects_table.itemDoubleClicked.connect(lambda _item: self._edit_effect())
        root.addWidget(self.effects_table, 1)
        row = QHBoxLayout()
        add = QPushButton("Ajouter…")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add_effect)
        edit = QPushButton("Modifier…")
        edit.clicked.connect(self._edit_effect)
        remove = QPushButton("Retirer")
        remove.setObjectName("DangerButton")
        remove.clicked.connect(self._remove_effect)
        row.addWidget(add)
        row.addWidget(edit)
        row.addStretch(1)
        row.addWidget(remove)
        root.addLayout(row)
        return page

    def _build_events_tab(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.addWidget(muted("Sorts lancés par cette entité et toujours suivis par la session."))
        self.events_table = make_table(EVENTS_COLUMNS)
        self.events_table.setColumnWidth(0, 150)
        self.events_table.setColumnWidth(1, 180)
        self.events_table.itemDoubleClicked.connect(lambda _item: self._edit_event())
        root.addWidget(self.events_table, 1)
        row = QHBoxLayout()
        edit = QPushButton("Modifier…")
        edit.clicked.connect(self._edit_event)
        stop = QPushButton("Arrêter")
        stop.setToolTip("Termine le sort et retire les effets qu'il a créés")
        stop.clicked.connect(self._stop_event)
        remove = QPushButton("Supprimer")
        remove.setObjectName("DangerButton")
        remove.setToolTip("Supprime le sort en cours et ses effets")
        remove.clicked.connect(self._delete_event)
        row.addWidget(edit)
        row.addWidget(stop)
        row.addStretch(1)
        row.addWidget(remove)
        root.addLayout(row)
        return page

    # ---------------------------------------------------------------- refresh
    def _token(self):
        state = self.runtime.active_map
        return state.token_for_entity(self.entity_id) if state is not None else None

    def _state_signature(self, entity: Entity) -> str:
        token = self._token()
        events = [
            (str(event.id), event.spell_id, owner.name)
            for owner in self.runtime.entities.values()
            for event in owner.spell_events
        ]
        return json.dumps(
            {
                "entity": entity.to_dict(),
                "stats": [_stat_breakdown(entity, stat) for stat in SHEET_STATS],
                "spells": {name: spell.to_public_dict() for name, spell in entity.spells.items()},
                "token": list(token.disclosed_fields) if token is not None else None,
                "pending": sorted(self.runtime.asset_changes.get(self.entity_id, ())),
                "names": sorted(e.name for e in self.runtime.entities.values()),
                "events": events,
            },
            sort_keys=True,
            default=str,
        )

    def refresh(self, *, force: bool = False) -> None:
        """Redraw from the runtime; cheap when nothing about the entity changed."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            entity = self.entity
            if entity is None:
                self.close()
                return
            signature = self._state_signature(entity)
            if not force and signature == self._signature:
                return
            # Commit what the GM is typing before the widgets are rebuilt
            self._pending.flush()
            entity = self.entity
            if entity is None:
                self.close()
                return
            self._signature = self._state_signature(entity)
            self._refresh_header(entity)
            self._refresh_stats(entity)
            self._refresh_items(entity)
            self._refresh_spells(entity)
            self._refresh_effects(entity)
            self._refresh_events(entity)
            self._refresh_visibility(entity)
        finally:
            self._refreshing = False

    def _refresh_header(self, entity: Entity) -> None:
        self.setWindowTitle(f"Fiche — {entity.name}")
        image = QImage(str(entity.token)) if entity.token is not None else None
        self.header.set_identity(
            entity.name,
            entity.character.name,
            entity.character.stats.level,
            image,
            self._token() is not None,
        )
        if self._asset_file is not None:
            self.asset_label.setText(f"Fichier : entities/{self._asset_file.name}")
        else:
            self.asset_label.setText("Entité de session : les modifications restent dans la session.")
        pending = self.runtime.asset_changes.get(self.entity_id, set())
        self.pending_label.setVisible(bool(pending) and self._asset_file is not None)

        for stat, bar in self.header.bars.items():
            base, _modifier, items, _effects, total = _stat_breakdown(entity, stat)
            reference = base + items + int(getattr(entity.authored_modifier, stat, 0))
            set_resource_bar(
                bar, total, reference,
                f"Actuel {total} · référence {reference} (base + objets + fichier)",
            )
        self.header.set_damage_modifiers(entity.damage_reduction, entity.damage_amplification)

    def _refresh_stats(self, entity: Entity) -> None:
        for stat, (base, spin, items, effects, total) in self.stat_cells.items():
            values = _stat_breakdown(entity, stat)
            base.setText(str(values[0]))
            items.setText(f"{values[2]:+d}" if values[2] else "—")
            effects.setText(f"{values[3]:+d}" if values[3] else "—")
            total.setText(str(values[4]))
            if not self._pending.is_pending(f"stat:{stat}") and not spin.hasFocus():
                spin.blockSignals(True)
                spin.setValue(values[1])
                spin.blockSignals(False)

    def _refresh_items(self, entity: Entity) -> None:
        selected = _selected_key(self.items_table)
        rows = sorted(entity.items.items.items(), key=lambda pair: pair[0].casefold())
        self.items_table.setRowCount(len(rows))
        for row, (name, quantity) in enumerate(rows):
            item = entity.items._item_cache.get(name)
            modifiers = modifiers_label(item.modifier, quantity) if item is not None else "?"
            description = item.description if item is not None else "Définition introuvable"
            self.items_table.setItem(row, 0, cell(name, name))
            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setKeyboardTracking(False)
            spin.setValue(int(quantity))
            spin.setToolTip("0 retire l'objet")
            spin.valueChanged.connect(lambda value, name=name: self._item_quantity_changed(name, value))
            self.items_table.setCellWidget(row, 1, spin)
            self.items_table.setItem(row, 2, cell(modifiers))
            self.items_table.setItem(row, 3, cell(description, tooltip=description))
        _select_key(self.items_table, selected)

        current = self.item_input.currentText()
        self.item_input.clear()
        self.item_input.addItems(store.item_names(self.runtime.campaign))
        self.item_input.setCurrentIndex(max(0, self.item_input.findText(current)))

    def _refresh_spells(self, entity: Entity) -> None:
        self.spell_grid.set_spells(
            [
                (name, spell.to_public_dict())
                for name, spell in sorted(entity.spells.items(), key=lambda pair: pair[0].casefold())
            ],
            "Aucun sort connu. Choisis-en un ci-dessous.",
        )

        known = {name.casefold() for name in entity.spells}
        current = self.spell_input.currentText()
        self.spell_input.clear()
        self.spell_input.addItems(
            [name for name in store.spell_names(self.runtime.campaign) if name.casefold() not in known]
        )
        self.spell_input.setCurrentIndex(max(0, self.spell_input.findText(current)))

    def _spell_source(self, effect: SpellEffect) -> str:
        """'Spell — Caster' for an effect created by a spell event, '' otherwise."""
        if effect.event_id is None:
            return ""
        for caster in self.runtime.entities.values():
            for event in caster.spell_events:
                if event.id == effect.event_id:
                    return f"{event.spell_id} — {caster.name}"
        return "sort terminé"

    def _effect_origin(self, effect: SpellEffect) -> str:
        return effect_origin_label(effect.origin, self._spell_source(effect))

    def _refresh_effects(self, entity: Entity) -> None:
        selected = _selected_key(self.effects_table)
        self.effects_table.setRowCount(len(entity.spell_effects))
        for row, effect in enumerate(entity.spell_effects):
            effect_type = EffectType(effect.effect_type).value
            origin = self._effect_origin(effect)
            self.effects_table.setItem(row, 0, cell(origin, str(effect.id), tooltip=origin))
            self.effects_table.setItem(row, 1, cell(EFFECT_LABELS.get(effect_type, effect_type)))
            self.effects_table.setItem(row, 2, cell(stat_label(str(effect.target_stat))))
            self.effects_table.setItem(row, 3, cell(effect_value_label(effect_type, effect.delta)))
            self.effects_table.setItem(row, 4, cell(duration_label(int(effect.duration))))
        _select_key(self.effects_table, selected)

    def _refresh_events(self, entity: Entity) -> None:
        selected = _selected_key(self.events_table)
        self.events_table.setRowCount(len(entity.spell_events))
        for row, event in enumerate(entity.spell_events):
            targets = ", ".join(
                self.runtime.entities[target_id].name if target_id in self.runtime.entities else "?"
                for target_id in event.targets_id
            )
            policy = RuntimePolicy(event.runtime_policy).value
            self.events_table.setItem(row, 0, cell(event.spell_id, str(event.id)))
            self.events_table.setItem(row, 1, cell(targets or "—", tooltip=targets))
            self.events_table.setItem(row, 2, cell(POLICY_LABELS.get(policy, policy)))
            self.events_table.setItem(row, 3, cell(event.nb_cast))
            self.events_table.setItem(row, 4, cell("Terminé" if event.finished else "En cours"))
            self.events_table.setItem(row, 5, cell(len(event.effects)))
        _select_key(self.events_table, selected)

    def _refresh_visibility(self, entity: Entity) -> None:
        token = self._token()
        enabled = token is not None
        self.visibility_missing.setVisible(not enabled)
        disclosed = set(token.disclosed_fields) if token is not None else set()
        for field, check in self.disclosure_checks.items():
            check.blockSignals(True)
            check.setChecked(field in disclosed)
            check.setEnabled(enabled)
            check.blockSignals(False)
        self.show_all_button.setEnabled(enabled)
        self.hide_all_button.setEnabled(enabled)

    # ---------------------------------------------------------------- actions
    def _run(self, title: str, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Record one change; report errors without losing the sheet."""
        try:
            event = action(*args, **kwargs)
        except (KeyError, ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, title, str(error).strip("'\""))
            self.refresh(force=True)
            return None
        if event is not None:
            self.runtime_changed.emit()
        return event

    def _stat_changed(self, stat: str, _value: int) -> None:
        spin = self.stat_cells[stat][1]
        self._pending.schedule(
            f"stat:{stat}",
            lambda: self._run(
                "Modificateur", self.runtime.set_stat_modifier, self.entity_id, stat, spin.value()
            ),
        )

    def _item_quantity_changed(self, name: str, value: int) -> None:
        self._pending.schedule(
            f"item:{name}",
            lambda: self._run("Inventaire", self.runtime.set_item_quantity, self.entity_id, name, value),
        )

    def _add_item(self) -> None:
        name = self.item_input.currentText().strip()
        entity = self.entity
        if not name or entity is None:
            return
        current = next((n for n in entity.items.items if n.casefold() == name.casefold()), name)
        quantity = int(entity.items.items.get(current, 0)) + self.item_quantity.value()
        self._run("Inventaire", self.runtime.set_item_quantity, self.entity_id, current, quantity)

    def _remove_item(self) -> None:
        name = _selected_key(self.items_table)
        if name is not None:
            self._run("Inventaire", self.runtime.set_item_quantity, self.entity_id, name, 0)

    def _add_spell(self) -> None:
        name = self.spell_input.currentText().strip()
        if name:
            self._run("Sorts", self.runtime.add_spell, self.entity_id, name)

    def _remove_spell(self, name: str) -> None:
        self._run("Sorts", self.runtime.remove_spell, self.entity_id, name)

    def _add_effect(self) -> None:
        dialog = EffectDialog(parent=self)
        if dialog.exec():
            values = dialog.values()
            self._run(
                "Effets",
                self.runtime.add_manual_effect,
                self.entity_id,
                values["effect_type"],
                values["stat"],
                values["delta"],
                values["duration"],
                values["origin"],
            )

    def _edit_effect(self) -> None:
        effect_id = _selected_key(self.effects_table)
        if effect_id is None:
            return
        try:
            effect = self.runtime.find_effect(effect_id)
        except KeyError:
            self.refresh(force=True)
            return
        dialog = EffectDialog(effect, self, spell_source=self._spell_source(effect))
        if dialog.exec():
            self._run("Effets", self.runtime.edit_effect, effect_id, **dialog.values())

    def _remove_effect(self) -> None:
        effect_id = _selected_key(self.effects_table)
        if effect_id is not None:
            self._run("Effets", self.runtime.delete_effect, effect_id)

    def _selected_event(self) -> Optional[str]:
        return _selected_key(self.events_table)

    def _edit_event(self) -> None:
        event_id = self._selected_event()
        if event_id is None:
            return
        try:
            event = self.runtime.find_spell_event(self.entity_id, event_id)
        except KeyError:
            self.refresh(force=True)
            return
        dialog = SpellEventDialog(event.nb_cast, event.finished, self)
        if dialog.exec():
            self._run(
                "Sorts en cours",
                self.runtime.edit_spell_event,
                self.entity_id,
                event_id,
                nb_cast=dialog.cast_input.value(),
                finished=dialog.finished_input.isChecked(),
            )

    def _stop_event(self) -> None:
        event_id = self._selected_event()
        if event_id is not None:
            self._run("Sorts en cours", self.runtime.stop_spell_event, self.entity_id, event_id)

    def _delete_event(self) -> None:
        event_id = self._selected_event()
        if event_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Supprimer le sort en cours",
            "Supprimer ce sort en cours et les effets qu'il a créés ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run("Sorts en cours", self.runtime.delete_spell_event, self.entity_id, event_id)

    def _disclosure_changed(self) -> None:
        self._set_disclosure(
            [field for field in DISCLOSABLE_FIELDS if self.disclosure_checks[field].isChecked()]
        )

    def _set_disclosure(self, fields: list[str]) -> None:
        token = self._token()
        if token is None:
            return
        if sorted(fields) == sorted(token.disclosed_fields):
            self.refresh(force=True)
            return
        self._run("Visibilité joueurs", self.runtime.set_token_disclosure, token.id, fields)

    # ------------------------------------------------------------------ close
    def flush_pending(self) -> None:
        """Record the edits still waiting for their delay (before a session save)."""
        self._pending.flush()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._pending.flush()
        super().closeEvent(event)
