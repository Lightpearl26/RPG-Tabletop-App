# -*- coding: utf-8 -*-

"""
IDE-style Campaign Manager window: resource browsers, editor tabs and the
campaign menus.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTabBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets import resources
from rpg_tabletop.assets.campaign import (
    DEFAULT_CAMPAIGN_NAME,
    Campaign,
    get_current_campaign,
    open_campaign,
    set_current_campaign,
)
from rpg_tabletop.assets.campaign_library import (
    forget_campaign,
    is_campaign_dir,
    recent_campaigns,
)
from rpg_tabletop.assets.references import ReferenceUpdate
from rpg_tabletop.core.paths import default_campaign_dir
from rpg_tabletop.session import store as session_store
from rpg_tabletop.ui.campaign_manager.editors.base import ResourceEditor
from rpg_tabletop.ui.campaign_manager.editors.character import CharacterEditor
from rpg_tabletop.ui.campaign_manager.editors.entity import EntityEditor
from rpg_tabletop.ui.campaign_manager.editors.item import ItemEditor
from rpg_tabletop.ui.campaign_manager.editors.map.editor import MapEditor
from rpg_tabletop.ui.campaign_manager.editors.session import SessionEditor
from rpg_tabletop.ui.campaign_manager.editors.spell import SpellEditor
from rpg_tabletop.ui.cards.base import ResourceCard
from rpg_tabletop.ui.cards.character import THEME as CHARACTER_CARD_THEME
from rpg_tabletop.ui.cards.character import CharacterCard
from rpg_tabletop.ui.cards.entity import THEME as ENTITY_CARD_THEME
from rpg_tabletop.ui.cards.entity import EntityCard
from rpg_tabletop.ui.cards.item import THEME as ITEM_CARD_THEME
from rpg_tabletop.ui.cards.item import ItemCard
from rpg_tabletop.ui.cards.map import THEME as MAP_CARD_THEME
from rpg_tabletop.ui.cards.map import MapCard
from rpg_tabletop.ui.cards.session import THEME as SESSION_CARD_THEME
from rpg_tabletop.ui.cards.session import SessionCard
from rpg_tabletop.ui.cards.spell import THEME as SPELL_CARD_THEME
from rpg_tabletop.ui.cards.spell import SpellCard
from rpg_tabletop.ui.gm_runtime import RuntimeWindow
from rpg_tabletop.ui.theme.stylesheets import CAMPAIGN_MANAGER_STYLESHEET

from .browser import ResourceBrowser
from .document import ResourceDocument
from .overview import RESOURCE_SECTIONS, CampaignOverview

# Every editor derives from ResourceEditor, which rewrites the references to a renamed
# resource in all the other assets (see rpg_tabletop.assets.references) and reports unsaved changes.
EDITOR_TYPES: dict[str, type[ResourceEditor]] = {
    "characters": CharacterEditor,
    "items": ItemEditor,
    "spells": SpellEditor,
    "entities": EntityEditor,
    "maps": MapEditor,
    "sessions": SessionEditor,
}
_LIVE_RESOURCES = {"characters", "entities", "items", "spells", "maps"}


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

    def _notify_runtimes(self, resource_type: str, resource_id: str) -> None:
        if resource_type not in _LIVE_RESOURCES:
            return
        for window in self._runtime_windows:
            if window.isVisible():
                window.on_asset_saved(resource_type, resource_id)

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
        self.recent_menu = file_menu.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
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

    def _campaign_dialog_dir(self) -> str:
        """Starting folder of the New/Open campaign dialogs."""
        if self.campaign.root.absolute() == default_campaign_dir().absolute():
            documents = Path.home() / "Documents"
            return str(documents if documents.is_dir() else Path.home())
        return str(self.campaign.root.parent)

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        current = self.campaign.root.absolute()
        for root in recent_campaigns():
            if root.absolute() == current:
                continue
            action = self.recent_menu.addAction(str(root))
            action.triggered.connect(lambda _checked=False, r=root: self._open_campaign_at(r))
        if not self.recent_menu.isEmpty():
            self.recent_menu.addSeparator()
        default_action = self.recent_menu.addAction("Default Campaign")
        default_action.setEnabled(default_campaign_dir().absolute() != current)
        default_action.triggered.connect(
            lambda _checked=False: self._open_campaign_at(default_campaign_dir())
        )

    def _open_campaign_at(self, root: Path) -> None:
        """Open the campaign stored in `root` (the default campaign is created if needed)."""
        root = Path(root)
        is_default = root.absolute() == default_campaign_dir().absolute()
        if not is_default and not is_campaign_dir(root):
            forget_campaign(root)
            QMessageBox.warning(self, "Open Campaign", f"No campaign found in {root}.")
            return
        if not self._confirm_close(self._open_editors()):
            return
        try:
            if is_campaign_dir(root):
                campaign = open_campaign(root)
            else:
                campaign = Campaign(DEFAULT_CAMPAIGN_NAME, root)
            self.load_campaign(set_current_campaign(campaign))
        except OSError as error:
            QMessageBox.warning(self, "Open Campaign", f"Could not open campaign: {error}")

    def _on_new_campaign(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Campaign", "Campaign name:")
        name = name.strip()
        if not accepted or not name:
            return
        if Path(name).name != name or name in (".", ".."):
            QMessageBox.warning(self, "New Campaign", "Invalid campaign name.")
            return
        parent = QFileDialog.getExistingDirectory(
            self, "Choose the parent folder", self._campaign_dialog_dir()
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
            self, "Open Campaign", self._campaign_dialog_dir()
        )
        if not directory:
            return
        root = Path(directory)
        if not is_campaign_dir(root):
            QMessageBox.warning(self, "Open Campaign", "No campaign.json found in this folder.")
            return
        if not self._confirm_close(self._open_editors()):
            return
        try:
            self.load_campaign(set_current_campaign(open_campaign(root)))
        except OSError as error:
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
        if resource_type == "entities":
            return [EntityCard(path.stem) for path in self.campaign.entity_path.glob("*.json")]
        if resource_type == "sessions":
            return [SessionCard(i.name, i.updated_at) for i in session_store.list_sessions(self.campaign)]
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
        if isinstance(page, SessionEditor):
            page.session_in_use = self._session_in_use
            page.launch_requested.connect(self._launch_session)
            page.sessions_changed.connect(self._on_sessions_changed)

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
            if isinstance(page, SessionEditor):
                page.session_in_use = self._session_in_use
                page.launch_requested.connect(self._launch_session)
                page.sessions_changed.connect(self._on_sessions_changed)
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
        self._notify_runtimes(resource_type, resource_id)
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

    def _session_in_use(self, name: str) -> bool:
        # isVisible : une fenêtre fermée reste dans la liste (destroyed n'est jamais émis)
        return any(w.isVisible() and w.runtime.session.name == name for w in self._runtime_windows)

    def _launch_session(self, name: str) -> None:
        for w in self._runtime_windows:
            if w.isVisible() and w.runtime.session.name == name:
                w.raise_(); w.activateWindow(); return
        try:
            window = RuntimeWindow.open_saved(self.campaign, self.campaign.session_path(name))
        except Exception as error:
            self.statusBar().showMessage(f"Impossible d'ouvrir {name} : {error}", 6000); return
        self._show_runtime_window(window)   # à extraire de _on_start_session_clicked

    def _on_sessions_changed(self) -> None:
        self._refresh_category("sessions"); self.overview.refresh_counts()

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

        for update in updates:
            if update.source == "entities":
                self._notify_runtimes("entities", update.path.stem)

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
        """
        Ask what to do with the unsaved changes of `editors`; return whether closing may go on.
        """
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

    def _show_runtime_window(self, window: RuntimeWindow) -> None:
        self.start_session_requested.emit()
        self._runtime_windows.append(window)
        window.assets_written.connect(self._on_runtime_assets_written)
        window.destroyed.connect(
            lambda _obj=None, window=window: (
                self._runtime_windows.remove(window)
                if window in self._runtime_windows else None
            )
        )
        window.show()
        window.raise_()
        window.activateWindow()
        self.overview.refresh_counts()
        self.statusBar().showMessage(f"Session lancée : {window.runtime.session.name}", 5000)

    def _on_runtime_assets_written(self, stems: list) -> None:
        """
        A runtime wrote character sheet changes to entity files: show them.

        An open editor without unsaved changes reloads the file. One with unsaved
        changes is left alone; saving it would replace what the session wrote.
        """
        conflicts: list[str] = []
        for stem in stems:
            page = self._document_pages.get(("entities", str(stem)))
            if not isinstance(page, ResourceEditor):
                continue
            if page.has_changes:
                conflicts.append(page.title)
                continue
            try:
                page.reload_from_disk()
            except (OSError, ValueError) as error:
                self.statusBar().showMessage(f"Rechargement impossible ({stem}) : {error}", 6000)
                continue
            self._refresh_document_title(page)
        self._refresh_category("entities")
        if conflicts:
            QMessageBox.warning(
                self,
                "Entités modifiées par la session",
                "La session vient d'écrire dans ces entités, ouvertes avec des modifications "
                f"non enregistrées : {', '.join(conflicts)}.\n\n"
                "Les enregistrer remplacerait l'inventaire, les sorts et les modificateurs "
                "écrits par la session.",
            )

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
        runtime_window = RuntimeWindow.start_new(self.campaign, name)

        self._show_runtime_window(runtime_window)

    @staticmethod
    def _section_title(resource_type: str) -> str:
        return next(title for key, title, _, _ in RESOURCE_SECTIONS if key == resource_type)

    @staticmethod
    def _singular_title(resource_type: str) -> str:
        return next(singular for key, _, singular, _ in RESOURCE_SECTIONS if key == resource_type)

    def _copy_name_suggestion(self, resource_type: str, resource_id: str) -> str:
        if resource_type == "sessions":
            return session_store.unique_name(self.campaign, f"{resource_id} (copy)")
        try:
            return resources.suggest_copy_name(self.campaign, resource_type, resource_id)
        except (OSError, ValueError):
            return f"{resource_id} (copy)"

    def _on_resource_duplicate_requested(self, resource_type: str, resource_id: str) -> None:
        self.resource_duplicate_requested.emit(resource_type, resource_id)
        singular = self._singular_title(resource_type)
        name, accepted = QInputDialog.getText(
            self,
            f"Duplicate {singular}",
            "Name of the copy:",
            text=self._copy_name_suggestion(resource_type, resource_id),
        )
        name = name.strip()
        if not accepted or not name:
            return
        try:
            if resource_type == "sessions":
                session_store.duplicate(self.campaign, resource_id, name)
            else:
                resources.duplicate_resource(self.campaign, resource_type, resource_id, name)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Duplicate {singular}", str(error))
            return
        self._refresh_category(resource_type)
        self.overview.refresh_counts()
        self.statusBar().showMessage(f'Duplicated "{resource_id}" as "{name}".', 5000)

    def _on_resource_delete_requested(self, resource_type: str, resource_id: str) -> None:
        self.resource_delete_requested.emit(resource_type, resource_id)
        if self._delete_resource(resource_type, resource_id):
            self._refresh_category(resource_type)
            self.overview.refresh_counts()

    def _delete_resource(self, resource_type: str, resource_id: str) -> bool:
        """Ask, delete, then bring the open pages and the runtimes up to date."""
        singular = self._singular_title(resource_type)
        updates: list[ReferenceUpdate] = []
        try:
            if resource_type == "sessions":
                if self._session_in_use(resource_id):
                    raise ValueError("This session is open in a runtime window: close it first.")
            else:
                updates = resources.delete_impact(self.campaign, resource_type, resource_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Delete {singular}", str(error))
            return False

        text = f'Delete {singular.lower()} "{resource_id}"? This cannot be undone.'
        if updates:
            references = sum(update.count for update in updates)
            text += f"\n\n{references} reference(s) to it will be removed from {len(updates)} file(s)."
        if resource_type == "maps":
            text += "\n\nSessions that use this map will no longer be able to load it."
        answer = QMessageBox.question(
            self,
            f"Delete {singular}",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        try:
            if resource_type == "sessions":
                session_store.delete(self.campaign, resource_id)
            else:
                updates = resources.delete_resource(self.campaign, resource_type, resource_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Delete {singular}", str(error))
            return False

        self._forget_deleted(resource_type, resource_id, updates)
        return True

    def _forget_deleted(
        self,
        resource_type: str,
        resource_id: str,
        updates: list[ReferenceUpdate],
    ) -> None:
        # The page of the deleted resource closes without asking: deleting was confirmed
        editor = self._document_pages.pop((resource_type, resource_id), None)
        if editor is not None:
            index = self.documents.indexOf(editor)
            if index >= 0:
                self.documents.removeTab(index)
            editor.deleteLater()

        # Other open pages must not write the deleted references back on their next save
        for page in self._document_pages.values():
            if isinstance(page, ResourceEditor):
                page.remove_references(resource_type, resource_id)

        for source in sorted({update.source for update in updates}):
            self._refresh_category(source)
        for update in updates:
            if update.source == "entities":
                self._notify_runtimes("entities", update.path.stem)

        if self.inspector_name.text() == resource_id:
            self.inspector_name.setText("Nothing selected")
            self.inspector_type.setText("")
            self.inspector_details.setText("Select a resource to inspect its details.")
        self.statusBar().showMessage(f'Deleted "{resource_id}".', 5000)
