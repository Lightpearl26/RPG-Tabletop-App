#-*- coding: utf-8 -*-

"""
GM application: Campaign Manager and game runtime.

Run with ``python -m rpg_tabletop`` (or ``python -m rpg_tabletop.apps.gm``).

It initializes the QApplication, sets the application name,
creates and shows the main CampaignManagerWindow, and starts
the application's event loop.
"""


import sys

from PySide6.QtWidgets import QApplication

from rpg_tabletop.ui.campaign_manager.window import CampaignManagerWindow
from rpg_tabletop.ui.theme.qt_palette import apply_dark_theme


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
