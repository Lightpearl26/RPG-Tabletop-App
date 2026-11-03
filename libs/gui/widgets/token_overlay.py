# -*- coding: utf-8 -*-
"""Name label and resource bars drawn around a token (map coordinates)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter

HP_COLOR = "#f38ba8"
STAMINA_COLOR = "#fab387"


def _bar(painter: QPainter, rect: QRectF, value: float, maximum: float, color: str) -> None:
    radius = rect.height() / 2.0
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#313244"))
    painter.drawRoundedRect(rect, radius, radius)
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, float(value) / float(maximum)))
    if ratio > 0:
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(
            QRectF(rect.x(), rect.y(), rect.width() * ratio, rect.height()), radius, radius
        )


def draw_token_overlay(
    painter: QPainter,
    token_rect: QRectF,
    *,
    name: str = "",
    hp: Optional[float] = None,
    hp_max: Optional[float] = None,
    stamina: Optional[float] = None,
    stamina_max: Optional[float] = None,
) -> None:
    """Draw the full name above the token and, when given, HP/stamina bars below."""
    size = token_rect.width()
    painter.save()
    try:
        if name:
            font = QFont()
            font.setBold(True)
            font.setPixelSize(max(8, round(size * 0.17)))
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            width = size * 1.8
            text = metrics.elidedText(name, Qt.TextElideMode.ElideRight, width)
            height = metrics.height()
            box = QRectF(
                token_rect.center().x() - width / 2.0,
                token_rect.top() - height - size * 0.04,
                width,
                height,
            )
            shift = max(1.0, size * 0.015)
            painter.setPen(QColor("#11111b"))
            painter.drawText(box.translated(shift, shift), Qt.AlignmentFlag.AlignCenter, text)
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

        bar_h = max(4.0, size * 0.08)
        gap = max(2.0, size * 0.03)
        y = token_rect.bottom() + max(3.0, size * 0.05)
        for value, maximum, color in (
            (hp, hp_max, HP_COLOR),
            (stamina, stamina_max, STAMINA_COLOR),
        ):
            if value is None or maximum is None:
                continue
            _bar(painter, QRectF(token_rect.left(), y, size, bar_h), value, maximum, color)
            y += bar_h + gap
    finally:
        painter.restore()
