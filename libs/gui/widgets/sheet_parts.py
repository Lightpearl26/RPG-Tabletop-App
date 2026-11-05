# -*- coding: utf-8 -*-
"""
Building blocks shared by the GM and player character sheets.

Both sheets show the same header, tabs, columns and spell cards; only the GM
one can edit. Everything here works from plain values (or the
`Spell.to_public_dict` summary), so the player side never needs runtime objects.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..cards.formula import EntityStatBadge, FlowLayout
from ..cards.spell import (
    COST_COLOR,
    DELAY_COLOR,
    EFFECT_BONUS_COLOR,
    EFFECT_MALUS_COLOR,
    EFFECT_TYPE_COLOR,
    POLICY_COLOR,
    TARGETING_COLOR,
)


# =============================================================================
# Labels and colors
# =============================================================================

STAT_LABELS = {
    "hp": "PV", "stamina": "Stamina", "mental_health": "Santé mentale",
    "drug_health": "Santé toxique", "str": "STR", "con": "CON", "dex": "DEX",
    "agi": "AGI", "int": "INT", "wis": "WIS", "cha": "CHA", "luc": "LUC",
    "sur": "SUR", "per": "PER", "name": "Nom",
}
STAT_COLORS = {
    "str": "#E35D6A", "con": "#E39A4A", "dex": "#4C8ED9", "agi": "#43B6D7",
    "int": "#6E68D8", "wis": "#9B6BDB", "cha": "#D76AAE", "luc": "#D8B84E",
    "sur": "#58C26A", "per": "#2F7D5A",
    "hp": "#E35D6A", "stamina": "#D8B84E",
    "mental_health": "#7F8A9B", "drug_health": "#7F8A9B",
}
CORE_STATS = ("str", "con", "dex", "agi", "int", "wis", "cha", "luc", "sur", "per")
EFFECT_LABELS = {
    "damage": "Dégâts",
    "heal": "Soin",
    "shield": "Bouclier",
    "damage_reduction": "Réduction de dégâts",
    "damage_amplification": "Amplification de dégâts",
    "buff": "Bonus",
    "debuff": "Malus",
    "control": "Contrôle",
    "other": "Autre",
}
# Effects whose value is a percentage, not a stat change
PERCENT_EFFECTS = ("damage_reduction", "damage_amplification")
POLICY_LABELS = {
    "instant": "Instantané",
    "maintain": "Maintenu",
    "refresh": "Rafraîchi",
    "delay": "Différé",
    "passiv": "Passif",
    "toggle": "Bascule",
    "trigger": "Déclencheur",
}
TARGETING_LABELS = {
    "single": "Cible unique",
    "multi": "Multicible",
    "self": "Soi-même",
    "area": "Zone",
}

# Same tabs and columns on both sides
STATS_COLUMNS = ("Stat", "Base", "Modif.", "Objets", "Effets", "Total")
ITEMS_COLUMNS = ("Objet", "Qté", "Modificateurs", "Description")
EFFECTS_COLUMNS = ("Origine", "Type", "Stat", "Valeur", "Durée")
EVENTS_COLUMNS = ("Sort", "Cibles", "Politique", "Lancers", "État", "Effets")
MUTED_COLOR = "#98a2b1"
ROLE_ID = Qt.ItemDataRole.UserRole


def stat_label(stat: str) -> str:
    return STAT_LABELS.get(stat, stat.upper())


def duration_label(duration: int) -> str:
    return "Permanent" if int(duration) < 0 else f"{int(duration)} tour(s)"


def effect_value_label(effect_type: str, delta: int) -> str:
    return f"{int(delta):+d}{' %' if effect_type in PERCENT_EFFECTS else ''}"


def effect_origin_label(origin: str, source: str) -> str:
    """The GM's description and/or the spell it comes from; 'MJ' when neither."""
    parts = [part for part in (origin, source) if part]
    return " · ".join(parts) if parts else "MJ"


def modifiers_label(modifiers: Any, quantity: int = 1) -> str:
    parts = []
    for entry in modifiers or []:
        try:
            stat, amount = entry
            parts.append(f"{stat_label(str(stat))} {int(amount) * int(quantity):+d}")
        except (TypeError, ValueError):
            continue
    return ", ".join(parts) or "—"


# =============================================================================
# Small widgets
# =============================================================================


def muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {MUTED_COLOR};")
    return label


def header_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-weight: 700; color: #cbd2dc;")
    return label


def tag(text: str, color: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    label.setStyleSheet(
        f"QLabel {{ background-color: {color}; color: white; border: none; border-radius: 6px;"
        " padding: 2px 6px; font-size: 10px; font-weight: 600; }"
    )
    return label


def make_table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setAlternatingRowColors(True)
    return table


def cell(text: Any, key: Optional[str] = None, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if key is not None:
        item.setData(ROLE_ID, key)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def resource_bar(stat: str) -> QProgressBar:
    bar = QProgressBar()
    bar.setFormat("%v / %m")
    bar.setFixedWidth(210)
    bar.setStyleSheet(f"QProgressBar::chunk {{ background: {STAT_COLORS.get(stat, '#9b6bdb')}; }}")
    return bar


def set_resource_bar(bar: QProgressBar, value: int, reference: int, tooltip: str = "") -> None:
    bar.setRange(0, max(1, int(reference), int(value)))
    bar.setValue(max(0, int(value)))
    bar.setToolTip(tooltip)


def token_pixmap(name: str, image: Optional[QImage], *, size: int = 72, placed: bool = True) -> QPixmap:
    """Round token picture, or the initials when there is no image."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    circle = QRectF(4, 4, size - 8, size - 8)
    clip = QPainterPath()
    clip.addEllipse(circle)
    painter.setClipPath(clip)
    if image is not None and not image.isNull():
        side = min(image.width(), image.height())
        source = QRectF((image.width() - side) / 2.0, (image.height() - side) / 2.0, side, side)
        painter.drawImage(circle, image, source)
    else:
        painter.fillRect(circle, QColor("#2d3440"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(10, size // 3))
        painter.setFont(font)
        painter.setPen(QColor("#eef2f7"))
        initials = "".join(word[:1] for word in name.split())[:2].upper() or "?"
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, initials)
    painter.setClipping(False)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#a6e3a1" if placed else "#596578"), 3))
    painter.drawEllipse(circle)
    painter.end()
    return pixmap


# =============================================================================
# Header
# =============================================================================


class SheetHeader(QWidget):
    """Token, name, base character and resource bars, on top of both sheets."""

    def __init__(self, resource_stats: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.token_label = QLabel()
        self.token_label.setFixedSize(72, 72)
        layout.addWidget(self.token_label, 0, Qt.AlignmentFlag.AlignTop)

        self.info = QVBoxLayout()
        self.info.setSpacing(3)
        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #f0eaff;")
        self.subtitle_label = muted()
        self.info.addWidget(self.name_label)
        self.info.addWidget(self.subtitle_label)
        self.info.addStretch(1)
        layout.addLayout(self.info, 1)

        bars = QGridLayout()
        bars.setHorizontalSpacing(8)
        bars.setVerticalSpacing(4)
        self.bars: dict[str, QProgressBar] = {}
        for row, stat in enumerate(resource_stats):
            bars.addWidget(QLabel(stat_label(stat)), row, 0)
            self.bars[stat] = resource_bar(stat)
            bars.addWidget(self.bars[stat], row, 1)
        self.damage_label = muted()
        bars.addWidget(self.damage_label, len(resource_stats), 0, 1, 2)
        layout.addLayout(bars)

    def set_identity(
        self,
        name: str,
        character: str,
        level: Optional[int],
        image: Optional[QImage],
        placed: bool,
    ) -> None:
        self.token_label.setPixmap(token_pixmap(name, image, placed=placed))
        self.name_label.setText(name)
        subtitle = f"Personnage de base : {character or '—'}"
        if level is not None:
            subtitle += f" · Niveau {level}"
        self.subtitle_label.setText(subtitle)

    def set_damage_modifiers(self, reduction: int, amplification: int) -> None:
        parts = []
        if reduction:
            parts.append(f"Réduction de dégâts {reduction} %")
        if amplification:
            parts.append(f"Amplification {amplification} %")
        self.damage_label.setText(" · ".join(parts))


# =============================================================================
# Spells
# =============================================================================


class SpellTile(QFrame):
    """
    Compact card of a known spell, built from `Spell.to_public_dict()`.

    Shows the effects (stat, type, bonus or malus), never their formulas.
    """

    forget_requested = Signal(str)

    def __init__(
        self,
        key: str,
        spell: dict[str, Any],
        *,
        forgettable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("SheetSpellTile")
        self.setStyleSheet(
            "QFrame#SheetSpellTile { background: #232936; border: 1px solid #303642;"
            " border-radius: 8px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        name = QLabel(str(spell.get("name") or key))
        name.setStyleSheet("background: transparent; color: #f2f3f5; font-size: 14px; font-weight: 700;")
        name.setWordWrap(True)
        header.addWidget(name, 1)
        header.addWidget(tag(f"{int(spell.get('cost', 0) or 0)} STA", COST_COLOR))
        if forgettable:
            forget = QPushButton("Oublier")
            forget.setObjectName("DangerButton")
            forget.setToolTip("Retirer ce sort de l'entité (ses lancers en cours continuent)")
            forget.setStyleSheet("min-height: 20px; padding: 1px 8px;")
            forget.clicked.connect(lambda: self.forget_requested.emit(self.key))
            header.addWidget(forget)
        root.addLayout(header)

        description = str(spell.get("description") or "")
        if description:
            text = QLabel(description)
            text.setWordWrap(True)
            text.setStyleSheet("background: transparent; color: #aeb6c8; font-size: 11px;")
            root.addWidget(text)

        chips = [effect for effect in spell.get("effects", []) or [] if isinstance(effect, dict)]
        if chips:
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            flow = FlowLayout(container, horizontal_spacing=6, vertical_spacing=4)
            flow.setContentsMargins(0, 0, 0, 0)
            for effect in chips:
                flow.addWidget(self._effect_chip(effect))
            root.addWidget(container)

        tags = QHBoxLayout()
        tags.setSpacing(5)
        tags.addStretch(1)
        targeting = str(spell.get("targeting") or "single")
        tags.addWidget(tag(TARGETING_LABELS.get(targeting, targeting).upper(), TARGETING_COLOR))
        if spell.get("delay") is not None:
            tags.addWidget(tag(f"DÉLAI {spell['delay']}", DELAY_COLOR))
        policy = str(spell.get("policy") or "instant")
        tags.addWidget(tag(POLICY_LABELS.get(policy, policy).upper(), POLICY_COLOR.get(policy, "#7F8A9B")))
        root.addLayout(tags)

    @staticmethod
    def _effect_chip(effect: dict[str, Any]) -> QFrame:
        """[TARGET | STAT] DAMAGE, framed in green (bonus) or red (malus)."""
        bonus = str(effect.get("operator", "")).lower() == "bonus"
        accent = EFFECT_BONUS_COLOR if bonus else EFFECT_MALUS_COLOR
        frame = QFrame()
        frame.setObjectName("SheetSpellEffect")
        frame.setStyleSheet(
            f"QFrame#SheetSpellEffect {{ background: #1b2029; border: none;"
            f" border-left: 3px solid {accent}; border-radius: 5px; }}"
        )
        frame.setToolTip("Bonus" if bonus else "Malus")
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(5)
        scope, _dot, stat = str(effect.get("target", "target.hp")).partition(".")
        row.addWidget(EntityStatBadge(scope, stat or "hp"))
        effect_type = str(effect.get("effect_type") or "other")
        row.addWidget(tag(
            EFFECT_LABELS.get(effect_type, effect_type).upper(),
            EFFECT_TYPE_COLOR.get(effect_type, EFFECT_TYPE_COLOR["other"]),
        ))
        return frame


class SpellGrid(QScrollArea):
    """Spell cards, two per row."""

    forget_requested = Signal(str)

    def __init__(self, *, forgettable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.forgettable = forgettable
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(2, 2, 6, 2)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.setWidget(container)

    def set_spells(self, spells: list[tuple[str, dict[str, Any]]], empty_text: str) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        if not spells:
            self.grid.addWidget(muted(empty_text), 0, 0)
        for index, (key, spell) in enumerate(spells):
            tile = SpellTile(key, spell, forgettable=self.forgettable)
            tile.forget_requested.connect(self.forget_requested)
            self.grid.addWidget(tile, index // 2, index % 2)
