#-*- coding: utf-8 -*-

"""
Campaign Manager GUI theme stylesheet.
"""

CAMPAIGN_MANAGER_STYLESHEET = """
QMainWindow#CampaignManager {
    background-color: #171a21;
    color: #e5e9ef;
}

QToolBar#CampaignToolbar {
    background-color: #1c2028;
    border: none;
    border-bottom: 1px solid #303642;
    spacing: 8px;
    padding: 6px 10px;
}

QLabel#CampaignToolbarTitle, QLabel#OverviewTitle, QLabel#ResourceDocumentTitle {
    color: #edf0f4;
    font-weight: 700;
}

QLabel#CampaignToolbarTitle { font-size: 13px; }
QLabel#CampaignToolbarTitle { padding-right: 8px; }
QLabel#CampaignToolbarPath { padding-right: 8px; }
QLabel#OverviewTitle { font-size: 22px; }
QLabel#ResourceDocumentTitle { font-size: 18px; }
QLabel#CampaignToolbarPath, QLabel#MutedText, QLabel#OverviewMetricName,
QLabel#ResourceDocumentNotice { color: #98a2b1; }

QPushButton#ToolbarButton {
    background-color: #292f3a;
    border: 1px solid #3a4351;
    border-radius: 4px;
    color: #d9dee7;
    padding: 6px 10px;
}

QPushButton#ToolbarButton:hover { background-color: #333b48; }

QPushButton#StartSessionButton {
    background-color: #7356b5;
    border: 1px solid #8769ca;
    border-radius: 4px;
    color: white;
    font-weight: 700;
    padding: 7px 12px;
}

QPushButton#StartSessionButton:hover { background-color: #8061c5; }

QPushButton#CharacterEditorSaveButton,
QPushButton#ItemEditorSaveButton,
QPushButton#SpellEditorSaveButton {
    background-color: #7356b5;
    border: 1px solid #8769ca;
    border-radius: 4px;
    color: white;
    font-weight: 700;
    padding: 7px 12px;
}

QPushButton#CharacterEditorSaveButton:hover,
QPushButton#ItemEditorSaveButton:hover,
QPushButton#SpellEditorSaveButton:hover { background-color: #8061c5; }

QMenuBar {
    background-color: #1c2028;
    border-bottom: 1px solid #303642;
    color: #c3cad4;
}

QMenuBar::item { padding: 5px 9px; }
QMenuBar::item:selected { background-color: #302a3f; }
QMenu { background-color: #20252e; border: 1px solid #3a4351; color: #d9dee7; }
QMenu::item:selected { background-color: #302a3f; }

QFrame#CampaignSidebar {
    background-color: #1c2028;
    border-right: 1px solid #303642;
}

QLabel#SidebarHeading, QLabel#SectionHeading {
    color: #aab2bf;
    font-size: 10px;
    font-weight: 700;
    padding: 8px 10px 4px;
}

QListWidget#CampaignNavigation {
    background: transparent;
    border: none;
    outline: none;
    padding: 4px 6px;
}

QListWidget#CampaignNavigation::item {
    border-radius: 4px;
    color: #c3cad4;
    min-height: 32px;
    padding: 0 10px;
}

QListWidget#CampaignNavigation::item:hover { background-color: #272d37; }

QListWidget#CampaignNavigation::item:selected {
    background-color: #302a3f;
    color: #e9ddff;
    border-left: 2px solid #9b6bdb;
}

QTabWidget#CampaignDocuments::pane {
    background-color: #171a21;
    border: none;
}

QTabBar::tab {
    background-color: #20252e;
    border: none;
    border-right: 1px solid #303642;
    color: #aeb7c4;
    min-height: 32px;
    padding: 0 14px;
}

QTabBar::tab:selected {
    background-color: #171a21;
    border-top: 2px solid #9b6bdb;
    color: #f0eaff;
}

QTabBar::tab:hover:!selected { background-color: #292f39; }

QLabel#resourceBrowserTitle {
    color: #edf0f4;
    font-size: 17px;
    font-weight: 700;
}

QLabel#resourceBrowserCount {
    color: #98a2b1;
    font-size: 11px;
}

QLineEdit#resourceBrowserSearch {
    background-color: #20252e;
    border: 1px solid #394250;
    border-radius: 4px;
    color: #e5e9ef;
    padding: 7px 9px;
    selection-background-color: #7356b5;
}

QLineEdit#resourceBrowserSearch:focus { border-color: #9b6bdb; }

QPushButton#resourceBrowserCreateButton {
    background-color: #7356b5;
    border: 1px solid #8769ca;
    border-radius: 4px;
    color: white;
    padding: 6px 10px;
}

QPushButton#resourceBrowserCreateButton:hover { background-color: #8061c5; }

QScrollArea#resourceBrowserScrollArea,
QScrollArea#resourceBrowserScrollArea > QWidget,
QWidget#resourceCardsContainer {
    background: transparent;
    border: none;
}

QFrame#resourceCard {
    background-color: #20252e;
    border: 1px solid #303642;
    border-radius: 5px;
}

QFrame#resourceCard:hover { border-color: #596578; }

QFrame#resourceCard[selected="true"] {
    background-color: #292433;
    border: 2px solid #9b6bdb;
}

QFrame#resourceCard QLabel { background: transparent; }

QLabel#BrowserEmptyState {
    background: transparent;
    color: #98a2b1;
    font-size: 13px;
}

QDockWidget#CampaignInspector { color: #dfe4ec; font-weight: 700; }

QDockWidget#CampaignInspector::title {
    background-color: #1c2028;
    border-bottom: 1px solid #303642;
    padding: 9px 10px;
}

QFrame#InspectorContents { background-color: #1c2028; }
QLabel#InspectorResourceName { color: #eff2f6; font-size: 16px; font-weight: 700; }
QLabel#InspectorResourceType { color: #b59be0; font-size: 10px; font-weight: 700; }
QLabel#InspectorDetails { color: #adb6c3; }
QFrame#OverviewMetric, QFrame#ResourceDocument {
    background-color: #20252e;
    border: 1px solid #303642;
    border-radius: 4px;
}

QLabel#OverviewMetricValue { color: #f0eaff; font-size: 20px; font-weight: 700; }
QLabel#SectionHeading { color: #dce2ea; font-size: 12px; }

QGroupBox#CharacterEditorSection,
QGroupBox#ItemEditorSection,
QGroupBox#SpellEditorSection {
    background-color: #1c2028;
    border: 1px solid #303642;
    border-radius: 4px;
    color: #dce2ea;
    font-weight: 700;
    margin-top: 12px;
    padding: 10px 8px 8px;
}

QGroupBox#CharacterEditorSection::title,
QGroupBox#ItemEditorSection::title,
QGroupBox#SpellEditorSection::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}

QLineEdit#CharacterNameField,
QLineEdit#ItemNameField,
QLineEdit#SpellNameField,
QLineEdit#SpellFormulaField,
QComboBox#CharacterSpellPicker,
QComboBox#CharacterItemPicker,
QComboBox#ItemStatPicker,
QComboBox#SpellCombo,
QPlainTextEdit#ItemDescriptionField,
QPlainTextEdit#SpellDescriptionField,
QSpinBox {
    background-color: #20252e;
    border: 1px solid #394250;
    border-radius: 4px;
    color: #e5e9ef;
    min-height: 26px;
    padding: 2px 7px;
}

QLineEdit#CharacterNameField:focus,
QLineEdit#ItemNameField:focus,
QLineEdit#SpellNameField:focus,
QLineEdit#SpellFormulaField:focus,
QComboBox#CharacterSpellPicker:focus,
QComboBox#CharacterItemPicker:focus,
QComboBox#ItemStatPicker:focus,
QComboBox#SpellCombo:focus,
QPlainTextEdit#ItemDescriptionField:focus,
QPlainTextEdit#SpellDescriptionField:focus,
QSpinBox:focus {
    border-color: #9b6bdb;
}

QListWidget#CharacterSpellsList,
QTableWidget#CharacterInventoryTable,
QTableWidget#ItemModifiersTable {
    background-color: #171a21;
    border: 1px solid #303642;
    color: #d9dee7;
    gridline-color: #303642;
}

QHeaderView::section {
    background-color: #252b35;
    border: none;
    color: #aeb7c4;
    padding: 5px 7px;
}

QLabel#CharacterDerivedValue {
    background-color: #20252e;
    border: 1px solid #303642;
    border-radius: 4px;
    color: #e0d1fb;
    font-weight: 700;
    min-width: 72px;
    padding: 7px 10px;
}

QStatusBar { background-color: #1c2028; border-top: 1px solid #303642; color: #aab4c1; }

QSpinBox:disabled,
QComboBox#SpellCombo:disabled { background-color: #1b1f27; color: #6f7a8a; }

QLineEdit#SpellFormulaField { font-family: Consolas, "Courier New", monospace; }

QComboBox#SpellCombo QAbstractItemView {
    background-color: #20252e;
    border: 1px solid #3a4351;
    color: #e5e9ef;
    outline: none;
    selection-background-color: #302a3f;
}

QScrollArea#SpellEditorScroll,
QScrollArea#SpellEditorScroll > QWidget,
QWidget#SpellEditorContent,
QWidget#SpellFormulaGroup,
QWidget#SpellEffectsList {
    background: transparent;
    border: none;
}

QWidget#SpellEditorContent > QLabel,
QGroupBox#SpellEditorSection QLabel { background: transparent; }

QGroupBox#SpellEditorSection * { font-weight: 400; }

QFrame#SpellEditorFooter {
    background-color: #1c2028;
    border-top: 1px solid #303642;
}

QLabel#SpellEditorHint { color: #98a2b1; font-weight: 400; }
QLabel#SpellEditorIssueList { background: transparent; font-weight: 400; }

QLabel#SpellEditorIssues { background: transparent; color: #98a2b1; font-weight: 600; }
QLabel#SpellEditorIssues[severity="ok"] { color: #58c26a; }
QLabel#SpellEditorIssues[severity="warning"] { color: #e39a4a; }
QLabel#SpellEditorIssues[severity="error"] { color: #e35d6a; }

QPushButton#SpellAddEffectButton,
QPushButton#SpellTesterButton,
QToolButton#SpellInsertButton {
    background-color: #292f3a;
    border: 1px solid #3a4351;
    border-radius: 4px;
    color: #d9dee7;
    font-weight: 400;
    padding: 5px 10px;
}

QToolButton#SpellInsertButton { padding-right: 20px; }

QPushButton#SpellAddEffectButton:hover,
QPushButton#SpellTesterButton:hover,
QToolButton#SpellInsertButton:hover { background-color: #333b48; }

QPushButton#SpellTesterButton:checked { border-color: #9b6bdb; }
QPushButton#SpellTesterButton:disabled { background-color: #1b1f27; color: #6f7a8a; }

QFrame#SpellEffect {
    background-color: #20252e;
    border: 1px solid #303642;
    border-radius: 5px;
}

QFrame#SpellEffect[expanded="true"] { border-color: #9b6bdb; }

QFrame#SpellEffectHeader {
    background: transparent;
    border: none;
    border-radius: 5px;
}

QFrame#SpellEffectHeader:hover { background-color: #272d37; }
QFrame#SpellEffectBody { background: transparent; border: none; }

QFrame#SpellEffect QLabel { background: transparent; }
QLabel#SpellEffectChevron { color: #aeb7c4; min-width: 12px; }
QLabel#SpellEffectIndex { color: #98a2b1; font-weight: 700; min-width: 14px; }

QLabel#SpellEffectFormula {
    color: #d9dee7;
    font-family: Consolas, "Courier New", monospace;
}

QLabel#SpellEffectFormula[empty="true"] { color: #6f7a8a; font-style: italic; }
QLabel#SpellEffectResult { color: #b59be0; font-weight: 700; }
QLabel#SpellEffectTest { color: #98a2b1; }

QToolButton#SpellEffectButton {
    background: transparent;
    border: none;
    border-radius: 3px;
    color: #aeb7c4;
    padding: 2px 6px;
}

QToolButton#SpellEffectButton:hover { background-color: #333b48; color: #edf0f4; }
QToolButton#SpellEffectButton:disabled { color: #4a5261; }

QFrame#SpellTester {
    background-color: #1c2028;
    border: 1px solid #303642;
    border-radius: 4px;
}

QFrame#SpellTesterHeader { background: transparent; border: none; border-radius: 4px; }
QFrame#SpellTesterHeader:hover { background-color: #222833; }

QFrame#SpellTesterBody,
QWidget#SpellTesterStats,
QWidget#SpellTesterFixed {
    background: transparent;
    border: none;
}

QFrame#SpellTester QLabel { background: transparent; }
QLabel#SpellTesterChevron { color: #aeb7c4; min-width: 12px; }
QLabel#SpellTesterTitle { color: #dce2ea; font-weight: 700; }
QLabel#SpellTesterHeading { color: #98a2b1; font-weight: 700; }
QLabel#SpellTesterSummary { color: #98a2b1; }
QLabel#SpellTesterNotice { color: #e39a4a; }
QLabel#SpellTesterResults { color: #d9dee7; }
"""
