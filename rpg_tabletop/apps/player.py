# -*- coding: utf-8 -*-

"""
Player application: connects to a GM runtime.

Run with ``python -m rpg_tabletop.apps.player``.
"""


from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QDialog

from rpg_tabletop.ui.common.update_dialog import startup_update_check
from rpg_tabletop.ui.player import PlayerLoginDialog, PlayerRuntimeWindow
from rpg_tabletop.ui.theme.qt_palette import apply_dark_theme


def main() -> int:
    """Launching app"""
    app = QApplication(sys.argv)
    app.setApplicationName("RPG Tabletop Player")
    app.setOrganizationName("RPGTabletop")
    apply_dark_theme(app)

    # Before connecting: the player must run the same version as the GM's server
    if startup_update_check("player"):
        return 0  # the installer (or a new process) takes over

    login = PlayerLoginDialog()
    if login.exec() != QDialog.DialogCode.Accepted:
        return 0

    window = PlayerRuntimeWindow(login.client_settings())
    window.show()
    window.connect_to_server()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
