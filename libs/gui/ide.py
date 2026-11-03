#-*- coding: utf-8 -*-

"""
IDE-style Campaign Manager workspace.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTabBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..assets.campaign import Campaign, get_current_campaign, set_current_campaign
from ..assets.references import ReferenceUpdate
from .browser import ResourceBrowser
from .cards.base import ResourceCard
from .cards.character import CharacterCard, THEME as CHARACTER_CARD_THEME
from .cards.entity import THEME as ENTITY_CARD_THEME
from .cards.item import ItemCard, THEME as ITEM_CARD_THEME
from .cards.map import MapCard, THEME as MAP_CARD_THEME
from .cards.session import THEME as SESSION_CARD_THEME
from .cards.spell import SpellCard, THEME as SPELL_CARD_THEME
from .editor.base import ResourceEditor
from .editor.character import CharacterEditor
from .editor.item import ItemEditor
from .editor.spell import SpellEditor
from .theme import CAMPAIGN_MANAGER_STYLESHEET


RESOURCE_SECTIONS = (
    ("characters", "Characters", "Character", (300, 250)),
    ("entities", "Entities", "Entity", (300, 310)),
    ("items", "Items", "Item", (300, 250)),
    ("spells", "Spells", "Spell", (450, 350)),
    ("maps", "Maps", "Map", (300, 280)),
    ("sessions", "Sessions", "Session", (300, 200)),
)

# Every editor derives from ResourceEditor, which rewrites the references to a renamed
# resource in all the other assets (see libs.assets.references) and reports unsaved changes.
EDITOR_TYPES: dict[str, type[ResourceEditor]] = {
    "characters": CharacterEditor,
    "items": ItemEditor,
    "spells": SpellEditor,
}


class CampaignOverview(QWidget):
    """Campaign landing page with resource counts and the session action."""

    start_session_requested = Signal()

    def __init__(self, campaign: Campaign, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.campaign = campaign
        self.resource_counts = self._get_resource_counts()
        self.count_labels: dict[str, QLabel] = {}
        self._build_ui()

    def _get_resource_counts(self) -> dict[str, int]:
        def json_count(path) -> int:
            return len(tuple(path.glob("*.json"))) if path.is_dir() else 0

        maps = self.campaign.maps_path
        sessions = self.campaign.sessions_path
        return {
            "Characters": json_count(self.campaign.character_path),
            "Entities": json_count(self.campaign.entity_path),
            "Items": json_count(self.campaign.item_path),
            "Spells": json_count(self.campaign.spell_path),
            "Maps": sum(
                path.is_dir() and path.name != "media"
                for path in maps.iterdir()
            ) if maps.is_dir() else 0,
            "Sessions": sum(path.is_dir() for path in sessions.iterdir())
            if sessions.is_dir() else 0,
        }

    def refresh_counts(self) -> None:
        """
        Refresh the resource counts displayed in the overview.

        This method updates the internal resource counts and refreshes
        the corresponding labels in the UI.
        """
        self.resource_counts = self._get_resource_counts()
        for resource_name, label in self.count_labels.items():
            label.setText(str(self.resource_counts[resource_name]))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(16)

        title = QLabel(self.campaign.name.upper())
        title.setObjectName("OverviewTitle")
        layout.addWidget(title)

        path_label = QLabel(str(self.campaign.root))
        path_label.setObjectName("MutedText")
        layout.addWidget(path_label)

        section_heading = QLabel("CAMPAIGN RESOURCES")
        section_heading.setObjectName("SectionHeading")
        layout.addWidget(section_heading)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        metrics.setVerticalSpacing(10)
        names = [section[1] for section in RESOURCE_SECTIONS]
        for index, name in enumerate(names):
            metric = QFrame()
            metric.setObjectName("OverviewMetric")
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(14, 12, 14, 12)
            metric_layout.setSpacing(3)

            count_label = QLabel(str(self.resource_counts[name]))
            count_label.setObjectName("OverviewMetricValue")
            self.count_labels[name] = count_label
            name_label = QLabel(name)
            name_label.setObjectName("OverviewMetricName")
            metric_layout.addWidget(count_label)
            metric_layout.addWidget(name_label)
            metrics.addWidget(metric, index // 3, index % 3)

        layout.addLayout(metrics)

        recent_heading = QLabel("RECENT")
        recent_heading.setObjectName("SectionHeading")
        layout.addWidget(recent_heading)
        recent_note = QLabel("Recent resources and sessions will appear here.")
        recent_note.setObjectName("MutedText")
        layout.addWidget(recent_note)
        layout.addStretch(1)

        self.start_session_button = QPushButton("Start New Session")
        self.start_session_button.setObjectName("StartSessionButton")
        self.start_session_button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.start_session_button.clicked.connect(self.start_session_requested)
        layout.addWidget(self.start_session_button, 0, Qt.AlignmentFlag.AlignRight)


class ResourceDocument(QWidget):
    """Document tab scaffold for a resource awaiting its specialized editor."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        summary: str,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.resource_type = resource_type
        self.resource_id = resource_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(10)

        title = QLabel(resource_id)
        title.setObjectName("ResourceDocumentTitle")
        kind = QLabel(resource_type.upper())
        kind.setObjectName("InspectorResourceType")

        details = QFrame()
        details.setObjectName("ResourceDocument")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(16, 14, 16, 14)
        details_layout.setSpacing(8)
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        notice = QLabel(
            "The editor for this resource is not connected yet."
            if not is_new
            else "The creation form for this resource is not connected yet."
        )
        notice.setObjectName("ResourceDocumentNotice")
        notice.setWordWrap(True)
        details_layout.addWidget(summary_label)
        details_layout.addWidget(notice)

        layout.addWidget(title)
        layout.addWidget(kind)
        layout.addWidget(details)
        layout.addStretch(1)


class CampaignManagerWindow(QMainWindow):
    """Campaign workspace with category browsers, document tabs and inspector."""

    save_requested = Signal()
    validate_requested = Signal()
    start_session_requested = Signal()
    resource_open_requested = Signal(str, str)
    resource_create_requested = Signal(str)
    resource_duplicate_requested = Signal(str, str)
    resource_delete_requested = Signal(str, str)

    def __init__(
        self,
        campaign: Campaign | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.campaign = campaign or get_current_campaign()
        self._browsers: dict[str, ResourceBrowser] = {}
        self._category_pages: dict[str, QWidget] = {}
        self._document_pages: dict[tuple[str, str], QWidget] = {}
        self._new_document_index = 0
        self._runtime_windows: list[QMainWindow] = []
        self.overview = CampaignOverview(self.campaign)
        self._preloaded = False

        self.setObjectName("CampaignManager")
        self.setWindowTitle(f"Campaign Manager - {self.campaign.name}")
        self.resize(1440, 900)
        self.setMinimumSize(980, 640)
        card_styles = (
            CHARACTER_CARD_THEME,
            ENTITY_CARD_THEME,
            ITEM_CARD_THEME,
            MAP_CARD_THEME,
            SESSION_CARD_THEME,
            SPELL_CARD_THEME,
        )
        self.setStyleSheet(
            CAMPAIGN_MANAGER_STYLESHEET + "\n" + "\n".join(card_styles)
        )

        self._build_toolbar()
        self._build_workspace()
        self._connect_signals()
        self.statusBar().showMessage(str(self.campaign.root))

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Campaign Toolbar", self)
        toolbar.setObjectName("CampaignToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.toolbar = toolbar
        self.campaign_name_label = QLabel(self.campaign.name)
        self.campaign_name_label.setObjectName("CampaignToolbarTitle")
        self.campaign_path_label = QLabel(str(self.campaign.root))
        self.campaign_path_label.setObjectName("CampaignToolbarPath")
        self.campaign_path_label.setMaximumWidth(380)
        self.campaign_path_label.setToolTip(str(self.campaign.root))
        toolbar.addWidget(self.campaign_name_label)
        toolbar.addWidget(self.campaign_path_label)
        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        toolbar.addWidget(spacer)

        self.save_button = self._make_toolbar_button("Save")
        self.validate_button = self._make_toolbar_button("Validate")
        self.start_session_button = QPushButton("Start Session")
        self.start_session_button.setObjectName("StartSessionButton")
        self.save_action = QAction("Save", self)
        self.save_action.setObjectName("SaveCharacterAction")
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.save_action.triggered.connect(self._on_save_clicked)
        self.addAction(self.save_action)
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.validate_button)
        toolbar.addWidget(self.start_session_button)

    def _preload_categories(self) -> None:
        """Show every category page once to pay the first-show cost, invisibly."""
        self.documents.blockSignals(True)
        try:
            for resource_type, *_ in RESOURCE_SECTIONS:
                page = self._get_category_page(resource_type)
                if self.documents.indexOf(page) < 0:
                    self.documents.addTab(page, self._section_title(resource_type))
                self.documents.setCurrentWidget(page)
                QApplication.processEvents()    # show + layout + paint réels

            for resource_type, *_ in RESOURCE_SECTIONS:
                index = self.documents.indexOf(self._category_pages[resource_type])
                if index >= 0:
                    self.documents.removeTab(index)
            self.documents.setCurrentWidget(self.overview)
            self.navigation.setCurrentRow(0)
            QApplication.processEvents()
        finally:
            self.documents.blockSignals(False)
            self.setWindowOpacity(1.0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._preloaded:
            self._preloaded = True
            self.setWindowOpacity(0.0)          # invisible pendant le préchargement
            QTimer.singleShot(0, self._preload_categories)

    @staticmethod
    def _make_toolbar_button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("ToolbarButton")
        button.setStyleSheet("QPushButton#ToolbarButton { margin: 2px 7px; }")
        return button

    def _build_workspace(self) -> None:
        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("CampaignSidebar")
        sidebar.setFixedWidth(188)
        self.sidebar = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 10, 0, 8)
        sidebar_layout.setSpacing(4)

        heading = QLabel("CAMPAIGN")
        heading.setObjectName("SidebarHeading")
        sidebar_layout.addWidget(heading)
        self.navigation = QListWidget()
        self.navigation.setObjectName("CampaignNavigation")
        self.navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.navigation.addItem("Overview")
        for resource_type, title, _, _ in RESOURCE_SECTIONS:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, resource_type)
            self.navigation.addItem(item)
        sidebar_layout.addWidget(self.navigation, 1)
        workspace_layout.addWidget(sidebar)

        self.documents = QTabWidget()
        self.documents.setObjectName("CampaignDocuments")
        self.documents.setTabsClosable(True)
        self.documents.setMovable(True)
        workspace_layout.addWidget(self.documents, 1)
        self.documents.addTab(self.overview, "Overview")
        self.documents.tabBar().setTabButton(
            0,
            QTabBar.ButtonPosition.RightSide,
            None,
        )
        self.setCentralWidget(workspace)

        inspector = QDockWidget("INSPECTOR", self)
        inspector.setObjectName("CampaignInspector")
        inspector.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        inspector.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        inspector.setMinimumWidth(230)
        inspector.setMaximumWidth(340)

        content = QFrame()
        content.setObjectName("InspectorContents")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(8)
        self.inspector_name = QLabel("Nothing selected")
        self.inspector_name.setObjectName("InspectorResourceName")
        self.inspector_name.setWordWrap(True)
        self.inspector_type = QLabel("")
        self.inspector_type.setObjectName("InspectorResourceType")
        self.inspector_details = QLabel("Select a resource to inspect its details.")
        self.inspector_details.setObjectName("InspectorDetails")
        self.inspector_details.setWordWrap(True)
        content_layout.addWidget(self.inspector_name)
        content_layout.addWidget(self.inspector_type)
        content_layout.addSpacing(8)
        content_layout.addWidget(self.inspector_details)
        content_layout.addStretch(1)
        inspector.setWidget(content)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)
        self.inspector_dock = inspector

        self._build_menus()
        self.navigation.setCurrentRow(0)

    def _add_action(
        self,
        menu: QMenu,
        text: str,
        slot,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        self.addAction(action)
        return action

    _IMPORT_DIRECTORIES = {
        "characters": "character_path",
        "entities": "entity_path",
        "items": "item_path",
        "spells": "spell_path",
    }

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        self._add_action(
            file_menu, "New Campaign...", self._on_new_campaign, "Ctrl+Shift+N"
        )
        self._add_action(
            file_menu,
            "Open Campaign...",
            self._on_open_campaign,
            QKeySequence.StandardKey.Open,
        )
        self._add_action(file_menu, "Reload Campaign", self._on_reload_campaign, "F5")
        file_menu.addSeparator()

        new_menu = file_menu.addMenu("New Resource")
        import_menu = file_menu.addMenu("Import Resource")
        for resource_type, _, singular, _ in RESOURCE_SECTIONS:
            self._add_action(
                new_menu,
                singular,
                lambda rt=resource_type: self._on_resource_create_requested(rt),
            )
            if resource_type in self._IMPORT_DIRECTORIES:
                self._add_action(
                    import_menu,
                    f"{singular} (.json)...",
                    lambda rt=resource_type: self._on_import_resources(rt),
                )
        file_menu.addSeparator()

        file_menu.addAction(self.save_action)
        self._add_action(
            file_menu,
            "Close Tab",
            lambda: self._close_tab(self.documents.currentIndex()),
            QKeySequence.StandardKey.Close,
        )
        self._add_action(file_menu, "Close All Tabs", self._close_all_tabs)
        file_menu.addSeparator()
        self._add_action(
            file_menu, "Open Campaign Folder", self._on_open_campaign_folder
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "Exit", self.close, "Ctrl+Q")

        edit_menu = menu_bar.addMenu("Edit")
        for text, method, shortcut in (
            ("Undo", "undo", QKeySequence.StandardKey.Undo),
            ("Redo", "redo", QKeySequence.StandardKey.Redo),
            (None, None, None),
            ("Cut", "cut", QKeySequence.StandardKey.Cut),
            ("Copy", "copy", QKeySequence.StandardKey.Copy),
            ("Paste", "paste", QKeySequence.StandardKey.Paste),
            ("Select All", "selectAll", QKeySequence.StandardKey.SelectAll),
        ):
            if text is None:
                edit_menu.addSeparator()
                continue
            assert method is not None
            self._add_action(
                edit_menu,
                text,
                lambda name=method: self._edit_focused(name),
                shortcut,
            )
        edit_menu.addSeparator()
        self._add_action(
            edit_menu,
            "Find Resources",
            self._focus_search,
            QKeySequence.StandardKey.Find,
        )

        view_menu = menu_bar.addMenu("View")
        for text, widget in (
            ("Toolbar", self.toolbar),
            ("Navigation Sidebar", self.sidebar),
            ("Status Bar", self.statusBar()),
        ):
            toggle = QAction(text, self, checkable=True, checked=True)
            toggle.toggled.connect(widget.setVisible)
            view_menu.addAction(toggle)
        view_menu.addAction(self.inspector_dock.toggleViewAction())
        view_menu.addSeparator()
        for row in range(self.navigation.count()):
            self._add_action(
                view_menu,
                self.navigation.item(row).text(),
                lambda r=row: self.navigation.setCurrentRow(r),
                f"Ctrl+{row + 1}",
            )
        view_menu.addSeparator()
        self._add_action(
            view_menu, "Next Tab", lambda: self._step_tab(1), "Ctrl+PgDown"
        )
        self._add_action(
            view_menu, "Previous Tab", lambda: self._step_tab(-1), "Ctrl+PgUp"
        )

    def _edit_focused(self, method_name: str) -> None:
        widget = QApplication.focusWidget()
        action = getattr(widget, method_name, None)
        if callable(action):
            action()

    def _focus_search(self) -> None:
        current = self.documents.currentWidget()
        for resource_type, page in self._category_pages.items():
            if page is current:
                search = self._browsers[resource_type].search_edit
                search.setFocus()
                search.selectAll()
                return

    def _step_tab(self, step: int) -> None:
        count = self.documents.count()
        if count:
            self.documents.setCurrentIndex((self.documents.currentIndex() + step) % count)

    def _close_all_tabs(self) -> None:
        if not self._confirm_close(self._open_editors()):
            return
        for index in range(self.documents.count() - 1, 0, -1):
            self._remove_tab(index)

    def _on_open_campaign_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.campaign.root)))

    def _on_reload_campaign(self) -> None:
        if self._confirm_close(self._open_editors()):
            self.load_campaign(self.campaign)

    def _on_new_campaign(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Campaign", "Campaign name:")
        name = name.strip()
        if not accepted or not name:
            return
        if Path(name).name != name or name in (".", ".."):
            QMessageBox.warning(self, "New Campaign", "Invalid campaign name.")
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Choose the parent folder", str(self.campaign.root.parent)
        )
        if not parent:
            return
        root = Path(parent) / name
        if root.exists() and any(root.iterdir()):
            QMessageBox.warning(self, "New Campaign", f"{root} already exists and is not empty.")
            return
        if not self._confirm_close(self._open_editors()):
            return
        try:
            self.load_campaign(set_current_campaign(Campaign(name, root)))
        except OSError as error:
            QMessageBox.warning(self, "New Campaign", f"Could not create campaign: {error}")

    def _on_open_campaign(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Open Campaign", str(self.campaign.root.parent)
        )
        if not directory:
            return
        root = Path(directory)
        manifest = root / "campaign.json"
        if not manifest.is_file():
            QMessageBox.warning(self, "Open Campaign", "No campaign.json found in this folder.")
            return
        if not self._confirm_close(self._open_editors()):
            return
        try:
            name = json.loads(manifest.read_text(encoding="utf-8")).get("name") or root.name
            self.load_campaign(set_current_campaign(Campaign(str(name), root)))
        except (OSError, ValueError, AttributeError) as error:
            QMessageBox.warning(self, "Open Campaign", f"Could not open campaign: {error}")

    def _on_import_resources(self, resource_type: str) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import Resources", "", "JSON files (*.json)"
        )
        if not files:
            return
        target = getattr(self.campaign, self._IMPORT_DIRECTORIES[resource_type])
        target.mkdir(parents=True, exist_ok=True)
        imported = skipped = 0
        for file in files:
            source = Path(file)
            destination = target / source.name
            if destination.exists():
                skipped += 1
                continue
            try:
                shutil.copy2(source, destination)
            except OSError:
                skipped += 1
                continue
            imported += 1
        self._refresh_category(resource_type)
        self.overview.refresh_counts()
        self.statusBar().showMessage(
            f"Imported {imported} file(s), skipped {skipped}.", 5000
        )

    def load_campaign(self, campaign: Campaign) -> None:
        """Switch the window to `campaign`, discarding all open pages without asking."""
        self.campaign = campaign
        for page in self._category_pages.values():
            page.deleteLater()
        self._browsers.clear()
        self._category_pages.clear()
        self._document_pages.clear()

        self.documents.blockSignals(True)
        for index in range(self.documents.count() - 1, -1, -1):
            page = self.documents.widget(index)
            self.documents.removeTab(index)
            if page is not None:
                page.deleteLater()
        self.overview = CampaignOverview(campaign)
        self.documents.addTab(self.overview, "Overview")
        self.documents.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        self.documents.blockSignals(False)
        self.overview.start_session_requested.connect(self._on_start_session_clicked)

        self.inspector_name.setText("Nothing selected")
        self.inspector_type.setText("")
        self.inspector_details.setText("Select a resource to inspect its details.")
        self.campaign_name_label.setText(campaign.name)
        self.campaign_path_label.setText(str(campaign.root))
        self.campaign_path_label.setToolTip(str(campaign.root))
        self.setWindowTitle(f"Campaign Manager - {campaign.name}")
        self.statusBar().showMessage(str(campaign.root))
        self.navigation.setCurrentRow(0)
        self.documents.setCurrentWidget(self.overview)
        self._preload_categories()

    def _connect_signals(self) -> None:
        self.navigation.currentRowChanged.connect(self._on_navigation_changed)
        self.documents.currentChanged.connect(self._on_document_tab_changed)
        self.documents.tabCloseRequested.connect(self._close_tab)
        self.save_button.clicked.connect(self._on_save_clicked)
        self.validate_button.clicked.connect(self._on_validate_clicked)
        self.start_session_button.clicked.connect(self._on_start_session_clicked)
        self.overview.start_session_requested.connect(self._on_start_session_clicked)

    def _on_navigation_changed(self, row: int) -> None:
        if row <= 0:
            self.documents.setCurrentWidget(self.overview)
            return

        resource_type = self.navigation.item(row).data(Qt.ItemDataRole.UserRole)
        if resource_type:
            self._open_category(resource_type)

    def _get_category_page(self, resource_type: str) -> QWidget:
        page = self._category_pages.get(resource_type)
        if page is None:
            page = self._create_category_page(resource_type)
            self._category_pages[resource_type] = page
        return page

    def _open_category(self, resource_type: str) -> None:
        page = self._get_category_page(resource_type)
        if self.documents.indexOf(page) < 0:   # pas encore (ou plus) dans les onglets
            self.documents.addTab(page, self._section_title(resource_type))
        self.documents.setCurrentWidget(page)

    def _create_category_page(self, resource_type: str) -> QWidget:
        _, title, singular, card_size = next(
            section for section in RESOURCE_SECTIONS if section[0] == resource_type
        )
        if resource_type not in self._browsers:
            browser = ResourceBrowser(
                resource_type=resource_type,
                title=title,
                card_size=card_size,
            )
            browser.create_button.setText(f"New {singular}")
            browser.resource_selected.connect(self._on_resource_selected)
            browser.resource_open_requested.connect(self._on_resource_opened)
            browser.resource_create_requested.connect(self._on_resource_create_requested)
            browser.resource_duplicate_requested.connect(
                self._on_resource_duplicate_requested
            )
            browser.resource_delete_requested.connect(self._on_resource_delete_requested)

            cards = self._load_cards(resource_type)
            browser.set_cards(cards)
            self._browsers[resource_type] = browser

        page = QWidget()
        stack = QStackedLayout(page)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self._browsers[resource_type])
        empty_state = QLabel("No persisted resources yet")
        empty_state.setObjectName("BrowserEmptyState")
        empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_state.setHidden(bool(self._browsers[resource_type].cards))
        stack.addWidget(empty_state)
        return page

    def _load_cards(self, resource_type: str) -> list[ResourceCard]:
        if resource_type == "characters":
            return [
                CharacterCard(path.stem)
                for path in self.campaign.character_path.glob("*.json")
            ]
        if resource_type == "items":
            return [ItemCard(path.stem) for path in self.campaign.item_path.glob("*.json")]
        if resource_type == "spells":
            return [SpellCard(path.stem) for path in self.campaign.spell_path.glob("*.json")]
        if resource_type == "maps":
            return [
                MapCard(path.name)
                for path in self.campaign.maps_path.iterdir()
                if path.is_dir() and path.name != "media"
            ] if self.campaign.maps_path.is_dir() else []
        return []

    def _on_resource_selected(self, resource_type: str, resource_id: str) -> None:
        self._show_inspector(resource_type, resource_id)

    def _on_resource_opened(self, resource_type: str, resource_id: str) -> None:
        self.resource_open_requested.emit(resource_type, resource_id)
        self._show_inspector(resource_type, resource_id)
        self._open_resource_document(resource_type, resource_id)

    def _open_resource_document(
        self,
        resource_type: str,
        resource_id: str,
        *,
        is_new: bool = False,
    ) -> None:
        if is_new:
            self._new_document_index += 1
            key = (resource_type, f"new:{self._new_document_index}")
        else:
            key = (resource_type, resource_id)

        page = self._document_pages.get(key)
        if page is not None:
            self.documents.setCurrentWidget(page)
            return

        editor_type = EDITOR_TYPES.get(resource_type)
        if editor_type is not None:
            try:
                page = editor_type(resource_id, self.campaign, is_new=is_new)
            except (OSError, ValueError) as error:
                self.statusBar().showMessage(
                    f"Could not open {resource_id}: {error}",
                    6000,
                )
                return
            page.saved.connect(
                lambda file_path, name, editor=page, kind=resource_type: (
                    self._on_resource_saved(kind, editor, file_path, name)
                )
            )
            page.renamed.connect(
                lambda old_stem, new_name, updates, editor=page: (
                    self._on_resource_renamed(editor, old_stem, new_name, updates)
                )
            )
            page.save_requested.connect(self._on_save_clicked)
            page.modified_changed.connect(
                lambda _modified, editor=page: self._refresh_document_title(editor)
            )
        else:
            page = ResourceDocument(
                resource_type,
                resource_id,
                self._card_summary(self._find_card(resource_type, resource_id)),
                is_new=is_new,
            )
        self._document_pages[key] = page
        title = (
            self._document_title(page)
            if isinstance(page, ResourceEditor)
            else resource_id
        )
        self.documents.addTab(page, title)
        self.documents.setCurrentWidget(page)

    def _on_resource_saved(
        self,
        resource_type: str,
        editor: ResourceEditor,
        file_path: str,
        name: str,
    ) -> None:
        resource_id = Path(file_path).stem
        self._document_pages = {
            key: page
            for key, page in self._document_pages.items()
            if page is not editor
        }
        self._document_pages[(resource_type, resource_id)] = editor

        self._refresh_document_title(editor)
        self._refresh_category(resource_type)
        self.overview.refresh_counts()
        self._show_inspector(resource_type, resource_id)
        self.statusBar().showMessage(f"Saved {name}.", 4000)

    @staticmethod
    def _document_title(editor: ResourceEditor) -> str:
        return f"*{editor.title}" if editor.is_modified else editor.title

    def _refresh_document_title(self, editor: ResourceEditor) -> None:
        index = self.documents.indexOf(editor)
        if index >= 0:
            self.documents.setTabText(index, self._document_title(editor))

    def _on_resource_renamed(
        self,
        editor: ResourceEditor,
        old_stem: str,
        new_name: str,
        updates: list[ReferenceUpdate],
    ) -> None:
        for page in self._document_pages.values():
            if page is not editor and isinstance(page, ResourceEditor):
                page.update_references(editor.resource_type, old_stem, new_name)

        if not updates:
            return

        for source in sorted({update.source for update in updates}):
            self._refresh_category(source)

        references = sum(update.count for update in updates)
        reference_label = "reference" if references == 1 else "references"
        file_label = "file" if len(updates) == 1 else "files"
        self.statusBar().showMessage(
            f"Saved {new_name}. Updated {references} {reference_label} "
            f"in {len(updates)} {file_label}.",
            6000,
        )

    def _refresh_category(self, resource_type: str) -> None:
        browser = self._browsers.get(resource_type)
        if browser is None:
            return
        cards = self._load_cards(resource_type)
        browser.set_cards(cards)
        page = self._category_pages.get(resource_type)
        empty_state = page.findChild(QLabel, "BrowserEmptyState") if page else None
        if empty_state is not None:
            empty_state.setHidden(bool(cards))

    def _show_inspector(self, resource_type: str, resource_id: str) -> None:
        self.inspector_name.setText(resource_id)
        self.inspector_type.setText(resource_type.upper())
        self.inspector_details.setText(
            self._card_summary(self._find_card(resource_type, resource_id))
        )

    def _find_card(self, resource_type: str, resource_id: str) -> ResourceCard | None:
        browser = self._browsers.get(resource_type)
        if browser is None:
            return None
        return next(
            (card for card in browser.cards if card.resource_id == resource_id),
            None,
        )

    @staticmethod
    def _card_summary(card: ResourceCard | None) -> str:
        if card is None:
            return "No persisted resource details are available yet."
        if isinstance(card, CharacterCard):
            return f"Level {card.character.stats.level}\nHP {card.character.stats.hp}"
        if isinstance(card, ItemCard):
            return card.item.description
        if isinstance(card, SpellCard):
            return f"{card.spell.description}\n\n{len(card.spell.effects)} effects"
        if isinstance(card, MapCard):
            return "Map preview available." if card._preview_path else "No map preview found."
        return card.search_text()

    def _on_resource_create_requested(self, resource_type: str) -> None:
        self.resource_create_requested.emit(resource_type)
        self._open_resource_document(
            resource_type,
            f"New {self._singular_title(resource_type)}",
            is_new=True,
        )

    def _on_resource_duplicate_requested(
        self,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.resource_duplicate_requested.emit(resource_type, resource_id)

    def _on_resource_delete_requested(
        self,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.resource_delete_requested.emit(resource_type, resource_id)

    def _on_document_tab_changed(self, index: int) -> None:
        page = self.documents.widget(index)
        if page is self.overview:
            self.navigation.setCurrentRow(0)
            return
        for resource_type, category_page in self._category_pages.items():
            if page is category_page:
                row = next(
                    row
                    for row in range(1, self.navigation.count())
                    if self.navigation.item(row).data(Qt.ItemDataRole.UserRole)
                    == resource_type
                )
                self.navigation.setCurrentRow(row)
                return

    def _close_tab(self, index: int) -> None:
        page = self.documents.widget(index)
        if isinstance(page, ResourceEditor) and not self._confirm_close([page]):
            return
        self._remove_tab(index)

    def _remove_tab(self, index: int) -> None:
        page = self.documents.widget(index)
        if page is None or page is self.overview:
            return
        is_category = any(value is page for value in self._category_pages.values())
        self._document_pages = {
            key: value for key, value in self._document_pages.items() if value is not page
        }
        self.documents.removeTab(index)
        if not is_category:
            page.deleteLater()

    def _open_editors(self) -> list[ResourceEditor]:
        pages = (self.documents.widget(index) for index in range(self.documents.count()))
        return [page for page in pages if isinstance(page, ResourceEditor)]

    def _confirm_close(self, editors: list[ResourceEditor]) -> bool:
        """Ask what to do with the unsaved changes of `editors`; return whether closing may go on."""
        pending = [editor for editor in editors if editor.has_changes]
        if not pending:
            return True

        self.documents.setCurrentWidget(pending[0])
        answer = self._ask_unsaved_changes([editor.title for editor in pending])
        if answer == QMessageBox.StandardButton.Discard:
            return True
        if answer != QMessageBox.StandardButton.Save:
            return False

        for editor in pending:
            self.documents.setCurrentWidget(editor)
            if not self._save_editor(editor):
                return False
        return True

    def _ask_unsaved_changes(self, names: list[str]) -> QMessageBox.StandardButton:
        if len(names) == 1:
            text = f'"{names[0]}" has unsaved changes. Save them before closing?'
        else:
            listing = "\n".join(f"- {name}" for name in names)
            text = f"These resources have unsaved changes. Save them before closing?\n\n{listing}"
        buttons = (
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        return QMessageBox.question(
            self,
            "Unsaved changes",
            text,
            buttons,
            QMessageBox.StandardButton.Save,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Keep the window open when the user cancels the unsaved changes prompt."""
        if self._confirm_close(self._open_editors()):
            event.accept()
        else:
            event.ignore()

    def _on_save_clicked(self) -> None:
        self.save_requested.emit()
        page = self.documents.currentWidget()
        if isinstance(page, ResourceEditor):
            self._save_editor(page)
            return
        self.statusBar().showMessage("Save requested.", 4000)

    def _save_editor(self, editor: ResourceEditor) -> bool:
        try:
            editor.save()
        except (OSError, ValueError) as error:
            label = editor.resource_label.lower()
            self.statusBar().showMessage(f"Could not save {label}: {error}", 6000)
            return False
        return True

    def _on_validate_clicked(self) -> None:
        self.validate_requested.emit()
        self.statusBar().showMessage("Validation requested.", 4000)

    def _on_start_session_clicked(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "Nouvelle session",
            "Nom de la session :",
            text="Nouvelle session",
        )
        name = name.strip()
        if not accepted or not name:
            return
        try:
            from .runtime import RuntimeWindow
            runtime_window = RuntimeWindow.start_new(self.campaign, name)
        except Exception as exc:
            QMessageBox.critical(self, "Nouvelle session", str(exc))
            return

        self.start_session_requested.emit()
        self._runtime_windows.append(runtime_window)
        runtime_window.destroyed.connect(
            lambda _obj=None, window=runtime_window: (
                self._runtime_windows.remove(window)
                if window in self._runtime_windows else None
            )
        )
        runtime_window.show()
        runtime_window.raise_()
        runtime_window.activateWindow()
        self.overview.refresh_counts()
        self.statusBar().showMessage(f"Session lancée : {name}", 5000)

    @staticmethod
    def _section_title(resource_type: str) -> str:
        return next(title for key, title, _, _ in RESOURCE_SECTIONS if key == resource_type)

    @staticmethod
    def _singular_title(resource_type: str) -> str:
        return next(singular for key, _, singular, _ in RESOURCE_SECTIONS if key == resource_type)
