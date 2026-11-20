# -*- coding: utf-8 -*-

"""
Icon of the applications: title bars, taskbar and dialogs.

The installed applications run under pythonw.exe. Without an application ID of
their own, Windows would group their windows under Python's icon in the taskbar;
the IDs below are also given to the Start menu shortcuts (packaging/installer.iss)
and to the launcher, so that every window of one application shares one icon.
The icon is drawn in packaging/app_icon.svg.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

ICON_FILE = Path(__file__).with_name("app_icon.png")
WINDOWS_ICON_FILE = Path(__file__).with_name("app_icon.ico")

GM_APP_ID = "RPGTabletop.GM"
PLAYER_APP_ID = "RPGTabletop.Player"


def app_icon() -> QIcon:
    """Icon of the applications."""
    return QIcon(str(ICON_FILE))


def apply_app_icon(app: QApplication, app_id: str) -> None:
    """Give every window the application icon, and the taskbar the application ID."""
    if sys.platform == "win32":
        try:
            import ctypes  # pylint: disable=import-outside-toplevel

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass
    app.setWindowIcon(app_icon())
