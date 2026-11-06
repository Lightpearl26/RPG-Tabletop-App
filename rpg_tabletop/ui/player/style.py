# -*- coding: utf-8 -*-
"""Dark theme shared by the Player Runtime widgets."""

PLAYER_RUNTIME_STYLESHEET = """
QMainWindow#PlayerRuntimeWindow, QWidget#PlayerRuntimeRoot {
    background: #171a21;
    color: #e5e9ef;
}
QWidget { color: #e5e9ef; }
QFrame#PlayerHeader, QFrame#PlayerPanel {
    background: #1c2028;
    border: 1px solid #303642;
    border-radius: 6px;
}
QLabel#PlayerTitle { color: #f0eaff; font-size: 16px; font-weight: 700; }
QLabel#PlayerMuted, QLabel#PlayerHint { color: #98a2b1; }
QLabel#PlayerSectionTitle {
    color: #b9c1ce; font-size: 10px; font-weight: 700;
    padding: 5px 0;
}
QTabWidget::pane { border: 1px solid #303642; background: #1c2028; }
QTabBar::tab {
    background: #20252e; color: #aeb7c4; border: 0;
    border-right: 1px solid #303642; padding: 8px 12px;
}
QTabBar::tab:selected { background: #302a3f; color: #f0eaff; border-top: 2px solid #9b6bdb; }
QPushButton {
    background: #292f3a; color: #e5e9ef; border: 1px solid #3a4351;
    border-radius: 4px; padding: 6px 9px;
}
QPushButton:hover { background: #353d49; }
QPushButton:disabled { color: #687180; background: #20252e; border-color: #303642; }
QPushButton#PlayerPrimaryButton { background: #7356b5; border-color: #8769ca; color: white; font-weight: 700; }
QPushButton#PlayerPrimaryButton:hover { background: #8061c5; }
QPushButton#PlayerDangerButton { background: #63343b; border-color: #8a4650; }
QLineEdit, QComboBox, QSpinBox, QTextBrowser, QListWidget, QTableWidget {
    background: #171c24; color: #e5e9ef; border: 1px solid #394250;
    border-radius: 4px; padding: 5px; selection-background-color: #7356b5;
}
QComboBox QAbstractItemView { background: #20252e; color: #e5e9ef; selection-background-color: #7356b5; }
QProgressBar { background: #171c24; border: 1px solid #394250; border-radius: 4px; text-align: center; color: #e5e9ef; }
QProgressBar::chunk { background: #7356b5; border-radius: 3px; }
QStatusBar { background: #1c2028; color: #98a2b1; border-top: 1px solid #303642; }
QSplitter::handle { background: #303642; }
QScrollArea { background: transparent; border: none; }
QGroupBox { border: 1px solid #303642; border-radius: 5px; margin-top: 8px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #b9c1ce; }
QScrollBar:vertical { background: #171c24; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #46505f; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
