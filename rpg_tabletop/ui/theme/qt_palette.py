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


# Same colors as the Campaign Manager and Runtime stylesheets
_ACTIVE = {
    QPalette.ColorRole.Window: "#171a21",
    QPalette.ColorRole.WindowText: "#e5e9ef",
    QPalette.ColorRole.Base: "#20252e",
    QPalette.ColorRole.AlternateBase: "#1c2028",
    QPalette.ColorRole.ToolTipBase: "#20252e",
    QPalette.ColorRole.ToolTipText: "#e5e9ef",
    QPalette.ColorRole.Text: "#e5e9ef",
    QPalette.ColorRole.Button: "#292f3a",
    QPalette.ColorRole.ButtonText: "#d9dee7",
    QPalette.ColorRole.BrightText: "#ffffff",
    QPalette.ColorRole.Link: "#b59be0",
    QPalette.ColorRole.Highlight: "#7356b5",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#6f7a8a",
}

_DISABLED = {
    QPalette.ColorRole.WindowText: "#6f7a8a",
    QPalette.ColorRole.Text: "#6f7a8a",
    QPalette.ColorRole.ButtonText: "#6f7a8a",
    QPalette.ColorRole.Highlight: "#3a4351",
    QPalette.ColorRole.HighlightedText: "#aeb7c4",
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
