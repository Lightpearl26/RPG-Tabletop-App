# -*- coding: utf-8 -*-
"""Floating contextual controls for ImageElement and TextElement."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...assets.map import ImageElement, TextElement

class ImageFormattingBar(QFrame):
    """Floating source controls attached to a selected ImageElement."""

    replace_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ImageFormattingBar")
        self.setFrameShape(QFrame.Shape.NoFrame)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(7)

        self.source_label = QLabel("No image", self)
        self.source_label.setObjectName("ImageSourceLabel")
        self.source_label.setMinimumWidth(120)
        self.source_label.setMaximumWidth(220)
        root.addWidget(self.source_label, 1)

        self.replace_button = QPushButton("Choose image…", self)
        self.replace_button.setObjectName("ImageReplaceButton")
        self.replace_button.clicked.connect(self.replace_requested.emit)
        root.addWidget(self.replace_button)

        self.setStyleSheet(
            """
            QFrame#ImageFormattingBar {
                background: #181825;
                border: 1px solid #45475a;
                border-radius: 9px;
            }
            QLabel#ImageSourceLabel {
                color: #bac2de;
                padding: 0 4px;
            }
            QPushButton#ImageReplaceButton {
                min-height: 28px;
                color: #11111b;
                background: #89b4fa;
                border: 1px solid #89b4fa;
                border-radius: 6px;
                padding: 2px 10px;
                font-weight: 600;
            }
            QPushButton#ImageReplaceButton:hover {
                background: #b4befe;
                border-color: #b4befe;
            }
            QPushButton#ImageReplaceButton:disabled {
                color: #6c7086;
                background: #313244;
                border-color: #45475a;
            }
            """
        )

        self.adjustSize()
        self.hide()

    def set_element(self, element: ImageElement) -> None:
        name = element.asset.name or element.asset.as_posix() or "No image"
        self.source_label.setText(name)
        self.source_label.setToolTip(element.asset.as_posix())
        self.replace_button.setEnabled(not element.locked)
        self.adjustSize()


# =============================================================================
# Contextual text formatting bar
# =============================================================================


class TextFormattingBar(QFrame):
    """Floating formatting controls attached to a selected TextElement."""

    text_changed = Signal(str)
    font_family_changed = Signal(str)
    font_size_changed = Signal(float)
    color_changed = Signal(str)
    bold_changed = Signal(bool)
    italic_changed = Signal(bool)
    underline_changed = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TextFormattingBar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._syncing = False
        self._color = "#f9e2af"

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(6)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("TextInlineEditor")
        self.text_edit.setPlaceholderText("Text")
        self.text_edit.setFixedSize(190, 52)
        self.text_edit.textChanged.connect(self._emit_text)
        root.addWidget(self.text_edit)

        format_box = QVBoxLayout()
        format_box.setContentsMargins(0, 0, 0, 0)
        format_box.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(5)

        self.font_combo = QFontComboBox(self)
        self.font_combo.setObjectName("TextFontCombo")
        self.font_combo.setFixedWidth(150)
        self.font_combo.currentFontChanged.connect(
            lambda font: self._emit_family(font.family())
        )
        top.addWidget(self.font_combo)

        self.size_spin = QSpinBox(self)
        self.size_spin.setObjectName("TextSizeSpin")
        self.size_spin.setRange(1, 512)
        self.size_spin.setSuffix(" px")
        self.size_spin.setFixedWidth(82)
        self.size_spin.valueChanged.connect(
            lambda value: self._emit_size(float(value))
        )
        top.addWidget(self.size_spin)

        self.color_button = QPushButton(self)
        self.color_button.setObjectName("TextColorButton")
        self.color_button.setFixedSize(34, 30)
        self.color_button.setToolTip("Text color")
        self.color_button.clicked.connect(self._choose_color)
        top.addWidget(self.color_button)

        format_box.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)

        self.bold_button = self._style_button("B", "Bold")
        font = self.bold_button.font()
        font.setBold(True)
        self.bold_button.setFont(font)
        self.bold_button.toggled.connect(self._emit_bold)
        bottom.addWidget(self.bold_button)

        self.italic_button = self._style_button("I", "Italic")
        font = self.italic_button.font()
        font.setItalic(True)
        self.italic_button.setFont(font)
        self.italic_button.toggled.connect(self._emit_italic)
        bottom.addWidget(self.italic_button)

        self.underline_button = self._style_button("U", "Underline")
        font = self.underline_button.font()
        font.setUnderline(True)
        self.underline_button.setFont(font)
        self.underline_button.toggled.connect(self._emit_underline)
        bottom.addWidget(self.underline_button)
        bottom.addStretch(1)

        format_box.addLayout(bottom)
        root.addLayout(format_box)

        self.setStyleSheet(
            """
            QFrame#TextFormattingBar {
                background: #181825;
                border: 1px solid #45475a;
                border-radius: 9px;
            }
            QPlainTextEdit#TextInlineEditor {
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 4px 6px;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
            }
            QPlainTextEdit#TextInlineEditor:focus,
            QFontComboBox#TextFontCombo:focus,
            QSpinBox#TextSizeSpin:focus {
                border-color: #89b4fa;
            }
            QFontComboBox#TextFontCombo,
            QSpinBox#TextSizeSpin {
                min-height: 28px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 1px 6px;
            }
            QToolButton#TextStyleButton {
                min-width: 31px;
                min-height: 27px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
            }
            QToolButton#TextStyleButton:hover {
                border-color: #585b70;
                background: #252536;
            }
            QToolButton#TextStyleButton:checked {
                color: #11111b;
                background: #89b4fa;
                border-color: #89b4fa;
            }
            QPushButton#TextColorButton {
                border: 1px solid #585b70;
                border-radius: 6px;
            }
            QPushButton#TextColorButton:hover {
                border: 2px solid #cdd6f4;
            }
            """
        )

        self.adjustSize()
        self.hide()

    def _style_button(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("TextStyleButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setFixedSize(33, 29)
        return button

    def _emit_text(self) -> None:
        if not self._syncing:
            self.text_changed.emit(self.text_edit.toPlainText())

    def _emit_family(self, family: str) -> None:
        if not self._syncing:
            self.font_family_changed.emit(family)

    def _emit_size(self, value: float) -> None:
        if not self._syncing:
            self.font_size_changed.emit(value)

    def _emit_bold(self, checked: bool) -> None:
        if not self._syncing:
            self.bold_changed.emit(checked)

    def _emit_italic(self, checked: bool) -> None:
        if not self._syncing:
            self.italic_changed.emit(checked)

    def _emit_underline(self, checked: bool) -> None:
        if not self._syncing:
            self.underline_changed.emit(checked)

    @staticmethod
    def _contrast_text(color: QColor) -> str:
        luminance = (
            0.2126 * color.redF()
            + 0.7152 * color.greenF()
            + 0.0722 * color.blueF()
        )
        return "#11111b" if luminance > 0.55 else "#f5e0dc"

    def _refresh_color(self) -> None:
        color = QColor(self._color)
        text = self._contrast_text(color)
        self.color_button.setStyleSheet(
            f"""
            QPushButton#TextColorButton {{
                color: {text};
                background: {self._color};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#TextColorButton:hover {{
                border: 2px solid #cdd6f4;
            }}
            """
        )

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Text color")
        if not color.isValid():
            return
        self._color = color.name(QColor.NameFormat.HexRgb)
        self._refresh_color()
        if not self._syncing:
            self.color_changed.emit(self._color)

    def set_element(self, element: TextElement) -> None:
        self._syncing = True
        try:
            self.text_edit.setPlainText(element.text)
            self.font_combo.setCurrentFont(
                QFont(element.font_family) if element.font_family else QFont()
            )
            self.size_spin.setValue(max(1, round(element.font_size)))
            self._color = element.font_color
            self.bold_button.setChecked(bool(getattr(element, "bold", False)))
            self.italic_button.setChecked(bool(getattr(element, "italic", False)))
            self.underline_button.setChecked(bool(getattr(element, "underline", False)))
            self._refresh_color()
            enabled = not element.locked
            self.text_edit.setEnabled(enabled)
            self.font_combo.setEnabled(enabled)
            self.size_spin.setEnabled(enabled)
            self.color_button.setEnabled(enabled)
            self.bold_button.setEnabled(enabled)
            self.italic_button.setEnabled(enabled)
            self.underline_button.setEnabled(enabled)
        finally:
            self._syncing = False

    def focus_text(self, select_all: bool = False) -> None:
        self.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        if select_all:
            self.text_edit.selectAll()
