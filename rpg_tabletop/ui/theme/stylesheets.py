# -*- coding: utf-8 -*-

"""
Qt stylesheets of the Campaign Manager, the GM runtime and the player application.

Colors are written ``$name`` and resolved from `palette.COLORS`.
"""

from string import Template

from .palette import COLORS


def _qss(template: str) -> str:
    """Resolve the ``$color`` placeholders of a stylesheet."""
    return Template(template).substitute(COLORS)


CAMPAIGN_MANAGER_STYLESHEET = _qss("""
QMainWindow#CampaignManager {
    background-color: $window;
    color: $text;
}

QToolBar#CampaignToolbar {
    background-color: $surface;
    border: none;
    border-bottom: 1px solid $border;
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
QLabel#ResourceDocumentNotice { color: $muted; }

QPushButton#ToolbarButton {
    background-color: $button;
    border: 1px solid $border_strong;
    border-radius: 4px;
    color: $button_text;
    padding: 6px 10px;
}

QPushButton#ToolbarButton:hover { background-color: $button_hover; }

QPushButton#StartSessionButton {
    background-color: $accent;
    border: 1px solid $accent_border;
    border-radius: 4px;
    color: white;
    font-weight: 700;
    padding: 7px 12px;
}

QPushButton#StartSessionButton:hover { background-color: $accent_hover; }

QPushButton#CharacterEditorSaveButton,
QPushButton#ItemEditorSaveButton,
QPushButton#SpellEditorSaveButton {
    background-color: $accent;
    border: 1px solid $accent_border;
    border-radius: 4px;
    color: white;
    font-weight: 700;
    padding: 7px 12px;
}

QPushButton#CharacterEditorSaveButton:hover,
QPushButton#ItemEditorSaveButton:hover,
QPushButton#SpellEditorSaveButton:hover { background-color: $accent_hover; }

QMenuBar {
    background-color: $surface;
    border-bottom: 1px solid $border;
    color: #c3cad4;
}

QMenuBar::item { padding: 5px 9px; }
QMenuBar::item:selected { background-color: $accent_soft; }
QMenu { background-color: $base; border: 1px solid $border_strong; color: $button_text; }
QMenu::item:selected { background-color: $accent_soft; }

QFrame#CampaignSidebar {
    background-color: $surface;
    border-right: 1px solid $border;
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
    background-color: $accent_soft;
    color: #e9ddff;
    border-left: 2px solid $accent_bright;
}

QTabWidget#CampaignDocuments::pane {
    background-color: $window;
    border: none;
}

QTabBar::tab {
    background-color: $base;
    border: none;
    border-right: 1px solid $border;
    color: $text_secondary;
    min-height: 32px;
    padding: 0 14px;
}

QTabBar::tab:selected {
    background-color: $window;
    border-top: 2px solid $accent_bright;
    color: $accent_text;
}

QTabBar::tab:hover:!selected { background-color: #292f39; }

QLabel#resourceBrowserTitle {
    color: #edf0f4;
    font-size: 17px;
    font-weight: 700;
}

QLabel#resourceBrowserCount {
    color: $muted;
    font-size: 11px;
}

QLineEdit#resourceBrowserSearch {
    background-color: $base;
    border: 1px solid $input_border;
    border-radius: 4px;
    color: $text;
    padding: 7px 9px;
    selection-background-color: $accent;
}

QLineEdit#resourceBrowserSearch:focus { border-color: $accent_bright; }

QPushButton#resourceBrowserCreateButton {
    background-color: $accent;
    border: 1px solid $accent_border;
    border-radius: 4px;
    color: white;
    padding: 6px 10px;
}

QPushButton#resourceBrowserCreateButton:hover { background-color: $accent_hover; }

QScrollArea#resourceBrowserScrollArea,
QScrollArea#resourceBrowserScrollArea > QWidget,
QWidget#resourceCardsContainer {
    background: transparent;
    border: none;
}

QFrame#resourceCard {
    background-color: $base;
    border: 1px solid $border;
    border-radius: 5px;
}

QFrame#resourceCard:hover { border-color: #596578; }

QFrame#resourceCard[selected="true"] {
    background-color: #292433;
    border: 2px solid $accent_bright;
}

QFrame#resourceCard QLabel { background: transparent; }

QLabel#BrowserEmptyState {
    background: transparent;
    color: $muted;
    font-size: 13px;
}

QDockWidget#CampaignInspector { color: $text_bright; font-weight: 700; }

QDockWidget#CampaignInspector::title {
    background-color: $surface;
    border-bottom: 1px solid $border;
    padding: 9px 10px;
}

QFrame#InspectorContents { background-color: $surface; }
QLabel#InspectorResourceName { color: #eff2f6; font-size: 16px; font-weight: 700; }
QLabel#InspectorResourceType { color: $link; font-size: 10px; font-weight: 700; }
QLabel#InspectorDetails { color: #adb6c3; }
QFrame#OverviewMetric, QFrame#ResourceDocument {
    background-color: $base;
    border: 1px solid $border;
    border-radius: 4px;
}

QLabel#OverviewMetricValue { color: $accent_text; font-size: 20px; font-weight: 700; }
QLabel#SectionHeading { color: #dce2ea; font-size: 12px; }

QGroupBox#CharacterEditorSection,
QGroupBox#ItemEditorSection,
QGroupBox#SpellEditorSection {
    background-color: $surface;
    border: 1px solid $border;
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
    background-color: $base;
    border: 1px solid $input_border;
    border-radius: 4px;
    color: $text;
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
    border-color: $accent_bright;
}

QListWidget#CharacterSpellsList,
QTableWidget#CharacterInventoryTable,
QTableWidget#ItemModifiersTable {
    background-color: $window;
    border: 1px solid $border;
    color: $button_text;
    gridline-color: $border;
}

QHeaderView::section {
    background-color: $header;
    border: none;
    color: $text_secondary;
    padding: 5px 7px;
}

QLabel#CharacterDerivedValue {
    background-color: $base;
    border: 1px solid $border;
    border-radius: 4px;
    color: #e0d1fb;
    font-weight: 700;
    min-width: 72px;
    padding: 7px 10px;
}

QStatusBar { background-color: $surface; border-top: 1px solid $border; color: #aab4c1; }

QSpinBox:disabled,
QComboBox#SpellCombo:disabled { background-color: #1b1f27; color: $disabled; }

QLineEdit#SpellFormulaField { font-family: Consolas, "Courier New", monospace; }

QComboBox#SpellCombo QAbstractItemView {
    background-color: $base;
    border: 1px solid $border_strong;
    color: $text;
    outline: none;
    selection-background-color: $accent_soft;
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
    background-color: $surface;
    border-top: 1px solid $border;
}

QLabel#SpellEditorHint { color: $muted; font-weight: 400; }
QLabel#SpellEditorIssueList { background: transparent; font-weight: 400; }

QLabel#SpellEditorIssues { background: transparent; color: $muted; font-weight: 600; }
QLabel#SpellEditorIssues[severity="ok"] { color: #58c26a; }
QLabel#SpellEditorIssues[severity="warning"] { color: #e39a4a; }
QLabel#SpellEditorIssues[severity="error"] { color: #e35d6a; }

QPushButton#SpellAddEffectButton,
QPushButton#SpellTesterButton,
QToolButton#SpellInsertButton {
    background-color: $button;
    border: 1px solid $border_strong;
    border-radius: 4px;
    color: $button_text;
    font-weight: 400;
    padding: 5px 10px;
}

QToolButton#SpellInsertButton { padding-right: 20px; }

QPushButton#SpellAddEffectButton:hover,
QPushButton#SpellTesterButton:hover,
QToolButton#SpellInsertButton:hover { background-color: $button_hover; }

QPushButton#SpellTesterButton:checked { border-color: $accent_bright; }
QPushButton#SpellTesterButton:disabled { background-color: #1b1f27; color: $disabled; }

QFrame#SpellEffect {
    background-color: $base;
    border: 1px solid $border;
    border-radius: 5px;
}

QFrame#SpellEffect[expanded="true"] { border-color: $accent_bright; }

QFrame#SpellEffectHeader {
    background: transparent;
    border: none;
    border-radius: 5px;
}

QFrame#SpellEffectHeader:hover { background-color: #272d37; }
QFrame#SpellEffectBody { background: transparent; border: none; }

QFrame#SpellEffect QLabel { background: transparent; }
QLabel#SpellEffectChevron { color: $text_secondary; min-width: 12px; }
QLabel#SpellEffectIndex { color: $muted; font-weight: 700; min-width: 14px; }

QLabel#SpellEffectFormula {
    color: $button_text;
    font-family: Consolas, "Courier New", monospace;
}

QLabel#SpellEffectFormula[empty="true"] { color: $disabled; font-style: italic; }
QLabel#SpellEffectResult { color: $link; font-weight: 700; }
QLabel#SpellEffectTest { color: $muted; }

QToolButton#SpellEffectButton {
    background: transparent;
    border: none;
    border-radius: 3px;
    color: $text_secondary;
    padding: 2px 6px;
}

QToolButton#SpellEffectButton:hover { background-color: $button_hover; color: #edf0f4; }
QToolButton#SpellEffectButton:disabled { color: #4a5261; }

QFrame#SpellTester {
    background-color: $surface;
    border: 1px solid $border;
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
QLabel#SpellTesterChevron { color: $text_secondary; min-width: 12px; }
QLabel#SpellTesterTitle { color: #dce2ea; font-weight: 700; }
QLabel#SpellTesterHeading { color: $muted; font-weight: 700; }
QLabel#SpellTesterSummary { color: $muted; }
QLabel#SpellTesterNotice { color: #e39a4a; }
QLabel#SpellTesterResults { color: $button_text; }
""")


GM_RUNTIME_STYLESHEET = _qss(r"""
QMainWindow#RuntimeWindow {
    background: $window;
    color: $text;
}
QToolBar#RuntimeToolbar {
    background: $surface;
    border: none;
    border-bottom: 1px solid $border;
    spacing: 6px;
    padding: 5px 8px;
}
QToolBar#RuntimeMapTools {
    background: $surface;
    border: none;
    border-right: 1px solid $border;
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
    color: $text_bright;
    font-weight: 700;
}
QDockWidget::title {
    background: $surface;
    border-bottom: 1px solid $border;
    padding: 7px 9px;
}
QWidget#RuntimePanel, QFrame#RuntimePanel {
    background: $surface;
}
QLabel#RuntimeHeading {
    color: #e9edf3;
    font-size: 13px;
    font-weight: 700;
}
QLabel#RuntimeMuted {
    color: $muted;
}
QPushButton {
    background: $button;
    border: 1px solid $border_strong;
    border-radius: 4px;
    color: $button_text;
    min-height: 26px;
    padding: 3px 8px;
}
QPushButton:hover { background: $button_hover; }
QPushButton:disabled { color: #68717f; background: #222730; }
QPushButton#PrimaryButton {
    background: $accent;
    border-color: $accent_border;
    color: white;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover { background: $accent_hover; }
QPushButton#DangerButton {
    background: #56313a;
    border-color: #76434f;
}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {
    background: $base;
    border: 1px solid $input_border;
    color: $text;
    selection-background-color: $accent;
    selection-color: white;
}
QLineEdit, QSpinBox, QComboBox { min-height: 25px; padding: 2px 6px; }
QHeaderView::section {
    background: $header;
    color: #cbd2dc;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
    padding: 5px;
}
QTabWidget::pane { border: 1px solid $border; }
QTabBar::tab {
    background: $base;
    color: $text_secondary;
    padding: 6px 10px;
}
QTabBar::tab:selected { background: $accent_soft; color: $accent_text; }
QStatusBar {
    background: $surface;
    color: $text_secondary;
    border-top: 1px solid $border;
}
QMenu {
    background: $base;
    color: $button_text;
    border: 1px solid $border_strong;
}
QMenu::item:selected { background: $accent_soft; }
""")


PLAYER_STYLESHEET = _qss("""
QMainWindow#PlayerRuntimeWindow, QWidget#PlayerRuntimeRoot {
    background: $window;
    color: $text;
}
QWidget { color: $text; }
QFrame#PlayerHeader, QFrame#PlayerPanel {
    background: $surface;
    border: 1px solid $border;
    border-radius: 6px;
}
QLabel#PlayerTitle { color: $accent_text; font-size: 16px; font-weight: 700; }
QLabel#PlayerMuted, QLabel#PlayerHint { color: $muted; }
QLabel#PlayerSectionTitle {
    color: $text_section; font-size: 10px; font-weight: 700;
    padding: 5px 0;
}
QTabWidget::pane { border: 1px solid $border; background: $surface; }
QTabBar::tab {
    background: $base; color: $text_secondary; border: 0;
    border-right: 1px solid $border; padding: 8px 12px;
}
QTabBar::tab:selected { background: $accent_soft; color: $accent_text; border-top: 2px solid $accent_bright; }
QPushButton {
    background: $button; color: $text; border: 1px solid $border_strong;
    border-radius: 4px; padding: 6px 9px;
}
QPushButton:hover { background: #353d49; }
QPushButton:disabled { color: #687180; background: $base; border-color: $border; }
QPushButton#PlayerPrimaryButton { background: $accent; border-color: $accent_border; color: white; font-weight: 700; }
QPushButton#PlayerPrimaryButton:hover { background: $accent_hover; }
QPushButton#PlayerDangerButton { background: #63343b; border-color: #8a4650; }
QLineEdit, QComboBox, QSpinBox, QTextBrowser, QListWidget, QTableWidget {
    background: $sunken; color: $text; border: 1px solid $input_border;
    border-radius: 4px; padding: 5px; selection-background-color: $accent;
}
QComboBox QAbstractItemView { background: $base; color: $text; selection-background-color: $accent; }
QProgressBar { background: $sunken; border: 1px solid $input_border; border-radius: 4px; text-align: center; color: $text; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QStatusBar { background: $surface; color: $muted; border-top: 1px solid $border; }
QSplitter::handle { background: $border; }
QScrollArea { background: transparent; border: none; }
QGroupBox { border: 1px solid $border; border-radius: 5px; margin-top: 8px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: $text_section; }
QScrollBar:vertical { background: $sunken; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #46505f; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
""")
