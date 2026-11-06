#-*- coding: utf-8 -*-

"""
Application appearance, independent from the system settings.

The stylesheets color the widgets they name; everything else (message boxes,
file dialogs, scroll bars, menus, tooltips) takes its colors from the palette,
which Qt 6 builds from the system light/dark setting. The style and the palette
are therefore fixed here, once, before any window exists.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .palette import COLORS


# Same colors as the stylesheets
_ACTIVE = {
    QPalette.ColorRole.Window: COLORS["window"],
    QPalette.ColorRole.WindowText: COLORS["text"],
    QPalette.ColorRole.Base: COLORS["base"],
    QPalette.ColorRole.AlternateBase: COLORS["surface"],
    QPalette.ColorRole.ToolTipBase: COLORS["base"],
    QPalette.ColorRole.ToolTipText: COLORS["text"],
    QPalette.ColorRole.Text: COLORS["text"],
    QPalette.ColorRole.Button: COLORS["button"],
    QPalette.ColorRole.ButtonText: COLORS["button_text"],
    QPalette.ColorRole.BrightText: "#ffffff",
    QPalette.ColorRole.Link: COLORS["link"],
    QPalette.ColorRole.Highlight: COLORS["accent"],
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: COLORS["disabled"],
}

_DISABLED = {
    QPalette.ColorRole.WindowText: COLORS["disabled"],
    QPalette.ColorRole.Text: COLORS["disabled"],
    QPalette.ColorRole.ButtonText: COLORS["disabled"],
    QPalette.ColorRole.Highlight: COLORS["border_strong"],
    QPalette.ColorRole.HighlightedText: COLORS["text_secondary"],
}


def dark_palette() -> QPalette:
    """
    Return the dark palette of the application.
    """
    palette = QPalette()
    for role, color in _ACTIVE.items():
        palette.setColor(role, QColor(color))
    for role, color in _DISABLED.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return palette


def apply_dark_theme(app: QApplication) -> None:
    """
    Force the Fusion style and the dark palette, whatever the system uses.

    Call it right after creating the QApplication, before any window.
    """
    # Fusion draws from the palette; the native styles follow the system theme
    app.setStyle("Fusion")
    app.setPalette(dark_palette())

    # Qt 6.8+: also keeps native parts (such as title bars) from following the system
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark)
