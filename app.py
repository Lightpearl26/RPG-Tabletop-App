#! venv/Scripts/python
#-*- coding: utf-8 -*-


"""
RPG Tabletop App

This module contains the entry point for the RPG Tabletop App.
It initializes the QApplication, sets the application name,
creates and shows the main CampaignManagerWindow, and starts
the application's event loop.
"""


import sys

from PySide6.QtWidgets import QApplication

from libs.gui.ide import CampaignManagerWindow
from libs.gui.appearance import apply_dark_theme


def main() -> int:
    """
    Entry point for the RPG Tabletop App.

    Initializes the QApplication, sets the application name,
    creates and shows the main CampaignManagerWindow, and
    starts the application's event loop.

    Returns:
        int: The exit code of the application.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("RPG Tabletop App")
    apply_dark_theme(app)

    window = CampaignManagerWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
