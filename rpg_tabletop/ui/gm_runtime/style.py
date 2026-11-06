# -*- coding: utf-8 -*-

RUNTIME_STYLESHEET = r"""
QMainWindow#RuntimeWindow {
    background: #171a21;
    color: #e5e9ef;
}
QToolBar#RuntimeToolbar {
    background: #1c2028;
    border: none;
    border-bottom: 1px solid #303642;
    spacing: 6px;
    padding: 5px 8px;
}
QToolBar#RuntimeMapTools {
    background: #1c2028;
    border: none;
    border-right: 1px solid #303642;
    spacing: 4px;
    padding: 6px 4px;
}
QWidget#RuntimeMapWorkspace { background: #11141a; }
QLabel#RuntimeServerSummary {
    color: #a9d6b3;
    font-weight: 700;
    padding: 2px 8px;
}
QLabel#RuntimeServerStatus {
    color: #e1c27a;
    font-weight: 700;
    padding: 4px 0;
}
QDockWidget {
    color: #dfe4ec;
    font-weight: 700;
}
QDockWidget::title {
    background: #1c2028;
    border-bottom: 1px solid #303642;
    padding: 7px 9px;
}
QWidget#RuntimePanel, QFrame#RuntimePanel {
    background: #1c2028;
}
QLabel#RuntimeHeading {
    color: #e9edf3;
    font-size: 13px;
    font-weight: 700;
}
QLabel#RuntimeMuted {
    color: #98a2b1;
}
QPushButton {
    background: #292f3a;
    border: 1px solid #3a4351;
    border-radius: 4px;
    color: #d9dee7;
    min-height: 26px;
    padding: 3px 8px;
}
QPushButton:hover { background: #333b48; }
QPushButton:disabled { color: #68717f; background: #222730; }
QPushButton#PrimaryButton {
    background: #7356b5;
    border-color: #8769ca;
    color: white;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover { background: #8061c5; }
QPushButton#DangerButton {
    background: #56313a;
    border-color: #76434f;
}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {
    background: #20252e;
    border: 1px solid #394250;
    color: #e5e9ef;
    selection-background-color: #7356b5;
    selection-color: white;
}
QLineEdit, QSpinBox, QComboBox { min-height: 25px; padding: 2px 6px; }
QHeaderView::section {
    background: #252b35;
    color: #cbd2dc;
    border: none;
    border-right: 1px solid #303642;
    border-bottom: 1px solid #303642;
    padding: 5px;
}
QTabWidget::pane { border: 1px solid #303642; }
QTabBar::tab {
    background: #20252e;
    color: #aeb7c4;
    padding: 6px 10px;
}
QTabBar::tab:selected { background: #302a3f; color: #f0eaff; }
QStatusBar {
    background: #1c2028;
    color: #aeb7c4;
    border-top: 1px solid #303642;
}
QMenu {
    background: #20252e;
    color: #d9dee7;
    border: 1px solid #3a4351;
}
QMenu::item:selected { background: #302a3f; }
"""
