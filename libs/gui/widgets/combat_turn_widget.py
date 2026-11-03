# -*- coding: utf-8 -*-
"""Shared animated combat-turn reel used by the GM and player runtimes."""
from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _CombatReelView(QWidget):
    """Three-slot vertical reel with a short slot-machine-like transition."""

    SLOT_HEIGHT = 82
    CARD_HEIGHT = 72

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self.SLOT_HEIGHT * 3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._items: list[dict[str, Any] | None] = [None, None, None]
        self._old_items: list[dict[str, Any] | None] = []
        self._new_items: list[dict[str, Any] | None] = []
        self._progress = 0.0
        self._transitioning = False
        self._has_state = False

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(360)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._on_animation_value)
        self._animation.finished.connect(self._finish_transition)

    @staticmethod
    def _normalise(items: Iterable[dict[str, Any] | None] | None) -> list[dict[str, Any] | None]:
        values = list(items or [])[:3]
        values.extend([None] * (3 - len(values)))
        return [dict(item) if isinstance(item, dict) else None for item in values]

    @staticmethod
    def _identity(item: dict[str, Any] | None) -> str:
        if not item:
            return ""
        return str(item.get("id") or item.get("name") or "")

    def set_items(
        self,
        items: Iterable[dict[str, Any] | None] | None,
        *,
        animate: bool = False,
        force: bool = False,
    ) -> None:
        new_items = self._normalise(items)
        if self._transitioning:
            old_target_ids = [self._identity(item) for item in self._new_items]
            new_target_ids = [self._identity(item) for item in new_items]
            if old_target_ids == new_target_ids:
                # Refresh live stamina/movement values without restarting the reel.
                self._new_items = new_items
                return
        old_current = self._identity(self._items[1])
        new_current = self._identity(new_items[1])
        should_animate = (
            animate
            and self._has_state
            and bool(old_current)
            and bool(new_current)
            and (old_current != new_current or force)
        )

        self._animation.stop()
        if should_animate:
            self._old_items = self._items
            self._new_items = new_items
            self._progress = 0.0
            self._transitioning = True
            self._animation.start()
        else:
            self._items = new_items
            self._old_items = []
            self._new_items = []
            self._progress = 0.0
            self._transitioning = False
            self._has_state = any(item is not None for item in new_items)
            self.update()

    def _on_animation_value(self, value: Any) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def _finish_transition(self) -> None:
        self._items = self._new_items or self._items
        self._old_items = []
        self._new_items = []
        self._progress = 0.0
        self._transitioning = False
        self._has_state = any(item is not None for item in self._items)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#171c24"))

        if not any(self._items) and not self._transitioning:
            painter.setPen(QColor("#8e99a9"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Aucun combat actif",
            )
            painter.end()
            return

        if self._transitioning:
            offset = self._progress * self.SLOT_HEIGHT
            for index, item in enumerate(self._old_items):
                self._draw_card(painter, item, index * self.SLOT_HEIGHT - offset)
            entering = self._new_items[2] if len(self._new_items) > 2 else None
            self._draw_card(painter, entering, 3 * self.SLOT_HEIGHT - offset)
        else:
            for index, item in enumerate(self._items):
                self._draw_card(painter, item, index * self.SLOT_HEIGHT)
        painter.end()

    def _draw_card(self, painter: QPainter, item: dict[str, Any] | None, y: float) -> None:
        top = y + (self.SLOT_HEIGHT - self.CARD_HEIGHT) / 2
        rect = self.rect().adjusted(5, 0, -5, 0)
        card = QRectF(rect.left(), top, rect.width(), self.CARD_HEIGHT)
        if top + self.CARD_HEIGHT < 0 or top > self.height():
            return

        center_y = self.SLOT_HEIGHT + self.CARD_HEIGHT / 2
        item_center = top + self.CARD_HEIGHT / 2
        center_weight = max(0.0, 1.0 - abs(item_center - center_y) / self.SLOT_HEIGHT)
        is_current = center_weight >= 0.72

        background = QColor("#242b36")
        border = QColor("#394555")
        if is_current:
            background = QColor("#302b3b")
            border = QColor("#c49a4b")
        elif center_weight > 0.0:
            background = QColor("#202731")

        painter.setPen(QPen(border, 1.5 if is_current else 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(card, 9, 9)

        if not item:
            painter.setPen(QColor("#657184"))
            painter.drawText(card, Qt.AlignmentFlag.AlignCenter, "—")
            return

        name = str(item.get("name") or "Participant inconnu")
        if not item.get("visible", True):
            name = str(item.get("name") or "???")
        subtitle = str(item.get("subtitle") or "")

        label = "TOUR ACTUEL" if is_current else ("À VENIR" if item_center > center_y else "PRÉCÉDENT")
        painter.setPen(QColor("#d4b36e" if is_current else "#8d9aab"))
        label_font = QFont()
        label_font.setPointSize(7)
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.drawText(card.adjusted(12, 5, -10, -5), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, label)

        name_font = QFont()
        name_font.setPointSize(10 if is_current else 9)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor("#f0f2f6" if is_current else "#c1c9d5"))
        name_rect = card.adjusted(12, 20, -10, -22)
        name = painter.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideRight, max(20, int(name_rect.width()))
        )
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)

        if is_current:
            stamina = item.get("stamina_remaining")
            stamina_max = item.get("stamina_max")
            movement = item.get("movement_remaining")
            movement_max = item.get("movement_max")
            stamina_text = (
                f"STA {stamina}/{stamina_max}"
                if stamina is not None and stamina_max is not None else "STA —"
            )
            movement_text = (
                f"MVT {movement:g}/{movement_max:g}"
                if isinstance(movement, (int, float)) and isinstance(movement_max, (int, float))
                else "MVT —"
            )
            detail_font = QFont()
            detail_font.setPointSize(7)
            painter.setFont(detail_font)
            painter.setPen(QColor("#aeb8c7"))
            painter.drawText(
                card.adjusted(12, 40, -10, -13),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"{stamina_text}     {movement_text}",
            )
            if stamina is not None and stamina_max:
                ratio = max(0.0, min(1.0, float(stamina) / float(stamina_max)))
                bar = QRectF(card.left() + 12, card.bottom() - 8, card.width() - 24, 4)
                fill = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#171c24"))
                painter.drawRoundedRect(bar, 2, 2)
                painter.setBrush(QColor("#c49a4b"))
                painter.drawRoundedRect(fill, 2, 2)
        elif subtitle:
            detail_font = QFont()
            detail_font.setPointSize(7)
            painter.setFont(detail_font)
            painter.setPen(QColor("#8491a3"))
            painter.drawText(
                card.adjusted(12, 37, -10, -4),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                subtitle,
            )


class CombatTurnWidget(QWidget):
    """Shared combat UI. The parent supplies state and handles emitted intents."""

    strike_requested = Signal()
    shoot_requested = Signal()
    spell_requested = Signal(str)
    end_turn_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CombatTurnWidget")
        self._state: dict[str, Any] = {}
        self._last_turn_key: str | None = None
        self._connected = False
        self._can_act = False
        self._can_end_turn = False
        self._pending = False
        self._strike_available = False
        self._shoot_available = False
        self._strike_cost = 60
        self._shoot_cost = 60

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("COMBAT")
        self.title_label.setObjectName("CombatWidgetHeading")
        self.round_label = QLabel("—")
        self.round_label.setObjectName("CombatWidgetRound")
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.round_label)
        root.addLayout(header)

        self.reel = _CombatReelView(self)
        root.addWidget(self.reel)

        self.status_label = QLabel("En attente du combat")
        self.status_label.setObjectName("CombatWidgetStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        action_row = QHBoxLayout()
        self.strike_button = QPushButton("Strike · 60")
        self.shoot_button = QPushButton("Shoot · 60")
        self.strike_button.setObjectName("CombatActionButton")
        self.shoot_button.setObjectName("CombatActionButton")
        self.strike_button.clicked.connect(lambda: self.strike_requested.emit())
        self.shoot_button.clicked.connect(lambda: self.shoot_requested.emit())
        action_row.addWidget(self.strike_button)
        action_row.addWidget(self.shoot_button)
        root.addLayout(action_row)

        spell_row = QHBoxLayout()
        self.spell_combo = QComboBox()
        self.spell_combo.addItem("Choisir un sort…", None)
        self.spell_combo.currentIndexChanged.connect(lambda _index: self._refresh_buttons())
        self.spell_button = QPushButton("Spell")
        self.spell_button.setObjectName("CombatActionButton")
        self.spell_button.clicked.connect(self._request_spell)
        spell_row.addWidget(self.spell_combo, 1)
        spell_row.addWidget(self.spell_button)
        root.addLayout(spell_row)

        self.end_turn_button = QPushButton("Fin de tour")
        self.end_turn_button.setObjectName("CombatEndTurnButton")
        self.end_turn_button.clicked.connect(lambda: self.end_turn_requested.emit())
        root.addWidget(self.end_turn_button)

        self.setStyleSheet(self._stylesheet())
        self._refresh_buttons()

    @staticmethod
    def _stylesheet() -> str:
        return r"""
        QWidget#CombatTurnWidget { background: #1c2028; }
        QLabel#CombatWidgetHeading { color: #e9edf3; font-size: 13px; font-weight: 800; letter-spacing: 1px; }
        QLabel#CombatWidgetRound { color: #d4b36e; font-size: 11px; font-weight: 700; }
        QLabel#CombatWidgetStatus { color: #98a2b1; font-size: 10px; }
        QPushButton#CombatActionButton {
            background: #292f3a; border: 1px solid #424c5b; border-radius: 5px;
            color: #e5e9ef; min-height: 30px; padding: 4px 8px; font-weight: 700;
        }
        QPushButton#CombatActionButton:hover { background: #353e4b; }
        QPushButton#CombatActionButton:disabled { color: #687180; background: #20252e; border-color: #303642; }
        QPushButton#CombatEndTurnButton {
            background: #7356b5; border: 1px solid #8769ca; border-radius: 5px;
            color: white; min-height: 32px; padding: 4px 8px; font-weight: 800;
        }
        QPushButton#CombatEndTurnButton:hover { background: #8061c5; }
        QPushButton#CombatEndTurnButton:disabled { color: #687180; background: #20252e; border-color: #303642; }
        QComboBox { background: #20252e; color: #e5e9ef; border: 1px solid #394250; border-radius: 4px; padding: 4px; min-height: 26px; }
        """

    def set_combat_state(self, state: dict[str, Any] | None, *, animate: bool = True) -> None:
        self._state = dict(state or {})
        active = bool(self._state.get("active", False))
        round_number = self._state.get("round")
        self.round_label.setText(f"ROUND {round_number}" if active and round_number else "—")
        self.reel.setVisible(active)
        if active:
            items = self._state.get("carousel", [])
            turn_key = str(self._state.get("turn_key") or "")
            if not turn_key:
                current = items[1] if isinstance(items, list) and len(items) > 1 else None
                current_id = current.get("id") or current.get("name") if isinstance(current, dict) else ""
                turn_key = f"{round_number}:{current_id}"
            turn_changed = self._last_turn_key is not None and turn_key != self._last_turn_key
            self.reel.set_items(items, animate=animate, force=turn_changed)
            self._last_turn_key = turn_key
            current = items[1] if isinstance(items, list) and len(items) > 1 else None
            current_name = current.get("name", "Participant") if isinstance(current, dict) else "Participant"
            if self._state.get("own_turn"):
                self.status_label.setText("C'est ton tour." if self._state.get("player_view") else "Tour actif — contrôle MJ")
            else:
                self.status_label.setText(f"Tour de {current_name}" if current and current.get("visible", True) else "Tour d'un participant non visible")
        else:
            self.reel.set_items([], animate=False)
            self._last_turn_key = None
            self.status_label.setText("Aucun combat actif — la map reste libre.")
        self._refresh_buttons()

    def set_action_options(
        self,
        *,
        strike_cost: int = 60,
        strike_available: bool = False,
        shoot_cost: int = 60,
        shoot_available: bool = False,
        spells: Iterable[dict[str, Any]] = (),
    ) -> None:
        self._strike_cost = max(0, int(strike_cost))
        self._shoot_cost = max(0, int(shoot_cost))
        self._strike_available = bool(strike_available)
        self._shoot_available = bool(shoot_available)
        selected = self.spell_combo.currentData()
        self.spell_combo.blockSignals(True)
        self.spell_combo.clear()
        self.spell_combo.addItem("Choisir un sort…", None)
        for spell in spells:
            if not isinstance(spell, dict):
                continue
            name = str(spell.get("name") or spell.get("id") or "Sort")
            spell_id = str(spell.get("id") or name)
            cost = max(0, int(spell.get("cost", 0)))
            available = bool(spell.get("available", False))
            suffix = f" · {cost} STA" + ("" if available else " · indisponible")
            self.spell_combo.addItem(name + suffix, spell_id)
            index = self.spell_combo.count() - 1
            self.spell_combo.setItemData(index, available, Qt.ItemDataRole.UserRole + 1)
        if selected:
            index = self.spell_combo.findData(selected)
            if index >= 0:
                self.spell_combo.setCurrentIndex(index)
        self.spell_combo.blockSignals(False)
        self._refresh_buttons()

    def set_permissions(
        self,
        *,
        connected: bool,
        can_act: bool,
        can_end_turn: bool,
        pending: bool = False,
        action_hint: str = "",
    ) -> None:
        self._connected = bool(connected)
        self._can_act = bool(can_act)
        self._can_end_turn = bool(can_end_turn)
        self._pending = bool(pending)
        if action_hint:
            self.status_label.setText(action_hint)
        elif self._pending:
            self.status_label.setText("Demande en attente de validation du MJ.")
        elif not self._connected and bool(self._state.get("player_view")):
            self.status_label.setText("Déconnecté — commandes indisponibles.")
        else:
            active = bool(self._state.get("active", False))
            if active and self._state.get("own_turn"):
                self.status_label.setText("C'est ton tour." if self._state.get("player_view") else "Tour actif")
            elif active:
                items = self._state.get("carousel", [])
                current = items[1] if isinstance(items, list) and len(items) > 1 else None
                if isinstance(current, dict) and current.get("visible", True):
                    self.status_label.setText(f"Tour de {current.get('name', 'Participant')}")
                else:
                    self.status_label.setText("Tour d'un participant non visible")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        active = bool(self._state.get("active", False))
        can_act = active and self._connected and self._can_act and not self._pending
        self.strike_button.setText(f"Strike · {self._strike_cost}")
        self.shoot_button.setText(f"Shoot · {self._shoot_cost}")
        self.strike_button.setEnabled(can_act and self._strike_available)
        self.shoot_button.setEnabled(can_act and self._shoot_available)
        spell_index = self.spell_combo.currentIndex()
        spell_available = bool(
            spell_index > 0
            and self.spell_combo.itemData(spell_index, Qt.ItemDataRole.UserRole + 1)
        )
        self.spell_combo.setEnabled(can_act and self.spell_combo.count() > 1)
        self.spell_button.setEnabled(can_act and spell_available)
        self.end_turn_button.setEnabled(
            active and self._connected and self._can_end_turn and not self._pending
        )

    def _request_spell(self) -> None:
        spell_id = self.spell_combo.currentData()
        if spell_id:
            self.spell_requested.emit(str(spell_id))
