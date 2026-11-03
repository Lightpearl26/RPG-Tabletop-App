# -*- coding: utf-8 -*-
"""Standalone entry point for the RPG Tabletop Player Runtime."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QDialog

from libs.gui.player_runtime import PlayerLoginDialog, PlayerRuntimeWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RPG Tabletop Player")
    app.setOrganizationName("RPGTabletop")

    login = PlayerLoginDialog()
    if login.exec() != QDialog.DialogCode.Accepted:
        return 0

    window = PlayerRuntimeWindow(login.client_settings())
    window.show()
    window.connect_to_server()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
