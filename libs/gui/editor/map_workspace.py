# -*- coding: utf-8 -*-
"""Map editing workspace embedded by :class:`MapEditor`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from ...assets.map import (
    ImageElement,
    MapAsset,
    MapCompileCancelled,
    Vec2,
    import_media,
    media_size,
)

from .map_canvas import MapCanvas
from .map_tokens import (
    CampaignTokenEntityProvider,
    NullTokenEntityProvider,
    TokenEntityProvider,
    TokenSourceData,
    TokenSourceList,
)
from .map_types import DRAW_TOOLS, EditorTool

class MapWorkspace(QWidget):
    """Map editor surface designed to be embedded in CampaignManagerWindow."""

    def __init__(
        self,
        asset: Optional[MapAsset] = None,
        map_dir: Optional[Path] = None,
        token_provider: Optional[TokenEntityProvider] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.map_dir: Optional[Path] = Path(map_dir) if map_dir is not None else None
        self.asset = asset or MapAsset()

        if token_provider is not None:
            self.token_provider: TokenEntityProvider = token_provider
        else:
            try:
                self.token_provider = CampaignTokenEntityProvider(parent=self)
                # Touch the campaign now so a missing current campaign is detected
                # before the toolbar starts requesting resources.
                _ = self.token_provider.campaign
            except Exception:
                self.token_provider = NullTokenEntityProvider()

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._body_layout = QHBoxLayout()
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)

        self._root_layout.addLayout(
            self._body_layout,
            1,
        )

        self.canvas = MapCanvas(
            asset=self.asset,
            map_dir=self.map_dir or Path.cwd(),
            token_provider=self.token_provider,
            parent=self,
        )

        self._build_editor_toolbar()

        self._body_layout.addWidget(
            self.canvas,
            1,
        )

        self._build_token_toolbar()
        self._build_status_bar()
        self._connect_signals()

        self._set_map_loaded(self.map_dir is not None)

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_editor_toolbar(self) -> None:
        toolbar = QToolBar("Tools", self)
        self._editor_toolbar = toolbar
        toolbar.setObjectName("MapEditorTools")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setOrientation(Qt.Orientation.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMinimumWidth(196)
        toolbar.setMaximumWidth(196)
        toolbar.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )

        self._body_layout.addWidget(
            toolbar,
            0,
        )

        self._apply_editor_toolbar_style(toolbar)

        toolbar.addWidget(self._section_label("TOOLS"))

        self._tool_action_group = QActionGroup(self)
        self._tool_action_group.setExclusive(True)
        self._tool_actions: dict[EditorTool, QAction] = {}

        def add_tool(label: str, tool: EditorTool, shortcut: Optional[str] = None) -> QAction:
            action = QAction(self._tool_icon(tool), label, self)
            action.setCheckable(True)
            if shortcut:
                action.setShortcut(shortcut)
                action.setToolTip(f"{label} ({shortcut})")
            else:
                action.setToolTip(label)
            action.triggered.connect(lambda _checked=False, t=tool: self._activate_tool(t))
            self._tool_action_group.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[tool] = action
            return action

        add_tool("Select", EditorTool.SELECT, "V")
        add_tool("Image", EditorTool.IMAGE, "I")
        add_tool("Freehand", EditorTool.FREEHAND, "P")
        add_tool("Line", EditorTool.LINE, "L")
        add_tool("Rectangle", EditorTool.RECT, "R")
        add_tool("Ellipse", EditorTool.CIRCLE, "O")
        add_tool("Text", EditorTool.TEXT, "T")
        wall = add_tool("Wall", EditorTool.WALL, "W")
        wall.setToolTip(
            "Wall (W): drag a segment, snapped to the grid corners. Walls block vision, "
            "shots, spells and player moves; only the GM sees them."
        )
        self._tool_actions[EditorTool.SELECT].setChecked(True)

        toolbar.addSeparator()
        toolbar.addWidget(self._section_label("APPEARANCE"))

        toolbar.addWidget(self._field_label("Stroke / text color"))
        self._stroke_color_button = QPushButton()
        self._stroke_color_button.setObjectName("ColorSwatch")
        self._stroke_color_button.setToolTip("Stroke / text color")
        self._stroke_color_button.setFixedHeight(32)
        self._stroke_color_button.clicked.connect(self._choose_stroke_color)
        toolbar.addWidget(self._stroke_color_button)

        self._fill_enabled = QCheckBox("Fill shape")
        self._fill_enabled.toggled.connect(self._apply_tool_style)
        self._fill_enabled.toggled.connect(lambda _checked: self._update_tool_controls())
        toolbar.addWidget(self._fill_enabled)

        self._fill_color_button = QPushButton()
        self._fill_color_button.setObjectName("ColorSwatch")
        self._fill_color_button.setToolTip("Fill color")
        self._fill_color_button.setFixedHeight(32)
        self._fill_color_button.clicked.connect(self._choose_fill_color)
        toolbar.addWidget(self._fill_color_button)

        toolbar.addWidget(self._field_label("Stroke width"))
        self._stroke_width = QDoubleSpinBox()
        self._stroke_width.setRange(0.0, 100.0)
        self._stroke_width.setDecimals(1)
        self._stroke_width.setSingleStep(0.5)
        self._stroke_width.setSuffix(" px")
        self._stroke_width.setValue(self.canvas.stroke_width)
        self._stroke_width.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._stroke_width)

        toolbar.addWidget(self._field_label("Opacity"))
        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.0, 1.0)
        self._opacity.setDecimals(2)
        self._opacity.setSingleStep(0.05)
        self._opacity.setValue(self.canvas.element_opacity)
        self._opacity.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._opacity)

        toolbar.addWidget(self._field_label("Text size"))
        self._font_size = QSpinBox()
        self._font_size.setRange(1, 512)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(round(self.canvas.text_font_size))
        self._font_size.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._font_size)

        self._stroke_color_value = self.canvas.stroke_color
        self._fill_color_value = "#89b4fa"
        self._refresh_color_buttons()
        self._update_tool_controls()

        toolbar.addSeparator()
        toolbar.addWidget(self._section_label("VIEW"))

        fit_action = QAction("Fit Map", self)
        fit_action.setShortcut("F")
        fit_action.setToolTip("Fit the whole map in the editor viewport (F)")
        fit_action.triggered.connect(self.canvas.fit_map)
        toolbar.addAction(fit_action)

        reload_media_action = QAction("Reload Media", self)
        reload_media_action.setToolTip("Reload imported image files from disk")
        reload_media_action.triggered.connect(self.canvas.reload_media)
        toolbar.addAction(reload_media_action)

        compile_action = QAction(
            "Compile",
            self,
        )
        compile_action.setToolTip(
            "Compile map tiles and preview"
        )
        compile_action.triggered.connect(
            self.compile_map
        )
        toolbar.addAction(
            compile_action
        )

    def _build_token_toolbar(self) -> None:
        toolbar = QToolBar("Tokens", self)
        self._token_toolbar = toolbar
        toolbar.setObjectName("MapTokenTools")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setOrientation(Qt.Orientation.Vertical)
        toolbar.setMinimumWidth(248)
        toolbar.setMaximumWidth(248)
        toolbar.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )

        self._body_layout.addWidget(
            toolbar,
            0,
        )

        container = QWidget(toolbar)
        root = QVBoxLayout(container)
        root.setContentsMargins(9, 10, 9, 10)
        root.setSpacing(8)

        title = QLabel("TOKENS", container)
        title.setObjectName("TokenToolbarTitle")
        root.addWidget(title)

        self._token_search = QLineEdit(container)
        self._token_search.setPlaceholderText("Search characters / entities…")
        self._token_search.setClearButtonEnabled(True)
        self._token_search.textChanged.connect(self.refresh_token_sources)
        root.addWidget(self._token_search)

        self._token_tabs = QTabWidget(container)
        self._token_tabs.setDocumentMode(True)
        root.addWidget(self._token_tabs, 1)

        # Characters ---------------------------------------------------------
        character_page = QWidget(self._token_tabs)
        character_layout = QVBoxLayout(character_page)
        character_layout.setContentsMargins(5, 7, 5, 5)
        character_layout.setSpacing(7)
        self._character_list = TokenSourceList("character", character_page)
        self._character_list.setAlternatingRowColors(False)
        self._character_list.itemDoubleClicked.connect(
            lambda _item: self._create_selected_character_token()
        )
        character_layout.addWidget(self._character_list, 1)
        self._character_add_button = QPushButton("Create Entity && place", character_page)
        self._character_add_button.clicked.connect(self._create_selected_character_token)
        character_layout.addWidget(self._character_add_button)
        self._token_tabs.addTab(character_page, "Characters")

        # Entities -----------------------------------------------------------
        entity_page = QWidget(self._token_tabs)
        entity_layout = QVBoxLayout(entity_page)
        entity_layout.setContentsMargins(5, 7, 5, 5)
        entity_layout.setSpacing(7)
        self._entity_list = TokenSourceList("entity", entity_page)
        self._entity_list.itemDoubleClicked.connect(
            lambda _item: self._place_selected_entity_token()
        )
        entity_layout.addWidget(self._entity_list, 1)
        entity_buttons = QHBoxLayout()
        entity_buttons.setContentsMargins(0, 0, 0, 0)
        entity_buttons.setSpacing(6)
        self._entity_place_button = QPushButton("Place", entity_page)
        self._entity_place_button.clicked.connect(self._place_selected_entity_token)
        entity_buttons.addWidget(self._entity_place_button, 1)
        self._entity_edit_button = QPushButton("Open sheet", entity_page)
        self._entity_edit_button.clicked.connect(self._edit_selected_entity)
        entity_buttons.addWidget(self._entity_edit_button, 1)
        entity_layout.addLayout(entity_buttons)
        self._token_tabs.addTab(entity_page, "Entities")

        self._token_provider_hint = QLabel(container)
        self._token_provider_hint.setWordWrap(True)
        self._token_provider_hint.setObjectName("TokenProviderHint")
        root.addWidget(self._token_provider_hint)

        refresh = QPushButton("Refresh", container)
        refresh.clicked.connect(self.refresh_token_sources)
        root.addWidget(refresh)

        toolbar.addWidget(container)
        self._apply_token_toolbar_style(toolbar)
        self.refresh_token_sources()

    @staticmethod
    def _apply_token_toolbar_style(toolbar: QToolBar) -> None:
        toolbar.setStyleSheet(
            """
            QToolBar#MapTokenTools {
                background: #181825;
                border: none;
                border-left: 1px solid #313244;
            }
            QToolBar#MapTokenTools QWidget {
                color: #cdd6f4;
                background: #181825;
            }
            QLabel#TokenToolbarTitle {
                color: #6c7086;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 4px 2px;
            }
            QLabel#TokenProviderHint {
                color: #6c7086;
                font-size: 10px;
                padding: 3px;
            }
            QToolBar#MapTokenTools QLineEdit,
            QToolBar#MapTokenTools QListWidget {
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 5px;
                selection-background-color: #313244;
                selection-color: #89b4fa;
            }
            QToolBar#MapTokenTools QLineEdit:focus,
            QToolBar#MapTokenTools QListWidget:focus {
                border-color: #89b4fa;
            }
            QToolBar#MapTokenTools QTabWidget::pane {
                border: 1px solid #313244;
                border-radius: 7px;
                background: #181825;
            }
            QToolBar#MapTokenTools QTabBar::tab {
                color: #9399b2;
                background: #11111b;
                border: 1px solid #313244;
                padding: 6px 10px;
            }
            QToolBar#MapTokenTools QTabBar::tab:selected {
                color: #89b4fa;
                background: #313244;
            }
            QToolBar#MapTokenTools QPushButton {
                min-height: 28px;
                color: #cdd6f4;
                background: #313244;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 4px 7px;
            }
            QToolBar#MapTokenTools QPushButton:hover {
                border-color: #89b4fa;
                color: #89b4fa;
            }
            QToolBar#MapTokenTools QPushButton:disabled {
                color: #585b70;
                background: #1e1e2e;
            }
            """
        )

    def set_token_provider(self, provider: Optional[TokenEntityProvider]) -> None:
        self.token_provider = provider or NullTokenEntityProvider()
        self.canvas.set_token_provider(self.token_provider)
        self.refresh_token_sources()

    @staticmethod
    def _token_source_icon(source: TokenSourceData) -> QIcon:
        """Render a compact token-like portrait used by the resource lists and drag pixmap."""
        width = 72
        height = 86
        portrait_size = 64

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        portrait_rect = QRectF(
            (width - portrait_size) / 2.0,
            1.0,
            portrait_size,
            portrait_size,
        )

        image = QImage(str(source.portrait)) if source.portrait else QImage()

        if not image.isNull():
            MapCanvas._draw_cover_image(
                painter,
                portrait_rect,
                image,
            )
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#313244"))
            painter.drawRoundedRect(
                portrait_rect,
                8.0,
                8.0,
            )

            initials = "".join(
                part[:1]
                for part in source.name.split()[:2]
            ).upper() or "?"

            font = QFont()
            font.setBold(True)
            font.setPixelSize(20)
            painter.setFont(font)
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(
                portrait_rect,
                Qt.AlignmentFlag.AlignCenter,
                initials,
            )

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#89b4fa"), 2.0))
        painter.drawRoundedRect(
            portrait_rect,
            8.0,
            8.0,
        )

        # Tiny HP / stamina bars make the thumbnail read as a token preview,
        # rather than as a generic file thumbnail.
        bar_x = 7.0
        bar_w = width - 14.0
        bar_h = 4.0
        hp_y = 69.0
        sta_y = 76.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#313244"))
        painter.drawRoundedRect(QRectF(bar_x, hp_y, bar_w, bar_h), 2.0, 2.0)
        painter.drawRoundedRect(QRectF(bar_x, sta_y, bar_w, bar_h), 2.0, 2.0)

        painter.setBrush(QColor("#f38ba8"))
        painter.drawRoundedRect(QRectF(bar_x, hp_y, bar_w, bar_h), 2.0, 2.0)

        painter.setBrush(QColor("#fab387"))
        painter.drawRoundedRect(QRectF(bar_x, sta_y, bar_w, bar_h), 2.0, 2.0)

        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _populate_token_list(
        cls,
        widget: QListWidget,
        sources: list[TokenSourceData],
        query: str,
    ) -> None:
        widget.clear()
        widget.setIconSize(QSize(64, 78))

        query = query.casefold().strip()

        for source in sources:
            haystack = f"{source.name} {source.subtitle}".casefold()
            if query and query not in haystack:
                continue

            text = (
                source.name
                if not source.subtitle
                else f"{source.name}\n{source.subtitle}"
            )

            item = QListWidgetItem(
                cls._token_source_icon(source),
                text,
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                source.id,
            )
            item.setToolTip(
                (source.subtitle or source.name)
                + "\nDrag onto the map to place this token."
            )
            item.setSizeHint(QSize(0, 88))
            widget.addItem(item)

    def refresh_token_sources(self) -> None:
        try:
            characters = list(self.token_provider.list_characters())
            entities = list(self.token_provider.list_entities())
            error = ""
        except Exception as exc:
            characters = []
            entities = []
            error = str(exc)

        query = self._token_search.text() if hasattr(self, "_token_search") else ""
        if hasattr(self, "_character_list"):
            self._populate_token_list(self._character_list, characters, query)
        if hasattr(self, "_entity_list"):
            # Mark entities that already have a token on this map.
            decorated: list[TokenSourceData] = []
            for source in entities:
                subtitle = source.subtitle
                if self.asset.token_for(source.id) is not None:
                    subtitle = f"{subtitle} • on map" if subtitle else "on map"
                decorated.append(
                    TokenSourceData(
                        source.id,
                        source.name,
                        subtitle,
                        source.portrait,
                    )
                )
            self._populate_token_list(self._entity_list, decorated, query)

        if hasattr(self, "_token_provider_hint"):
            if error:
                self._token_provider_hint.setText(f"Provider error: {error}")
            elif isinstance(self.token_provider, NullTokenEntityProvider):
                self._token_provider_hint.setText(
                    "No current Campaign is available for Characters / Entities."
                )
            else:
                self._token_provider_hint.setText(
                    f"{len(characters)} characters • {len(entities)} entities\n"
                    "Drag a portrait onto the map."
                )

    @staticmethod
    def _selected_source_id(widget: QListWidget) -> Optional[str]:
        item = widget.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else None

    def _create_selected_character_token(self) -> None:
        character_id = self._selected_source_id(self._character_list)
        if not character_id:
            return
        try:
            entity_id = self.token_provider.create_entity_from_character(character_id)
            if not entity_id:
                return
            # Rule: a Character always becomes an Entity and its editor is opened
            # before a token can be placed.
            if not self.token_provider.edit_entity(entity_id):
                self.refresh_token_sources()
                return
            self.refresh_token_sources()
            self.canvas.begin_token_placement(entity_id)
            self._status_bar.showMessage("Click on the map to place the new Entity token.", 4000)
        except Exception as error:
            QMessageBox.critical(self, "Create Entity token", str(error))

    def _place_selected_entity_token(self) -> None:
        entity_id = self._selected_source_id(self._entity_list)
        if not entity_id:
            return
        self.canvas.begin_token_placement(entity_id)
        existing = self.asset.token_for(entity_id)
        message = "Click on the map to move this Entity token." if existing else "Click on the map to place this Entity token."
        self._status_bar.showMessage(message, 4000)

    def _edit_selected_entity(self) -> None:
        entity_id = self._selected_source_id(self._entity_list)
        if not entity_id:
            return
        self._open_entity_sheet(entity_id)

    def _open_entity_sheet(self, entity_id: str) -> None:
        try:
            self.token_provider.edit_entity(entity_id)
            self.canvas.update()
            self.refresh_token_sources()
        except Exception as error:
            QMessageBox.critical(self, "Entity", str(error))

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ToolbarSectionLabel")
        return label

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ToolbarFieldLabel")
        return label

    @staticmethod
    def _apply_editor_toolbar_style(toolbar: QToolBar) -> None:
        toolbar.setStyleSheet(
            """
            QToolBar#MapEditorTools {
                background: #181825;
                border: none;
                border-right: 1px solid #313244;
                spacing: 4px;
                padding: 10px 8px;
            }

            QToolBar#MapEditorTools QToolButton {
                min-width: 158px;
                min-height: 30px;
                padding: 5px 8px;
                margin: 1px 0px;
                color: #cdd6f4;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
            }

            QToolBar#MapEditorTools QToolButton:hover {
                background: #252536;
                border-color: #313244;
            }

            QToolBar#MapEditorTools QToolButton:checked {
                color: #89b4fa;
                background: #313244;
                border-color: #45475a;
                font-weight: 600;
            }

            QToolBar#MapEditorTools QToolButton:pressed {
                background: #45475a;
            }

            QToolBar#MapEditorTools::separator {
                background: #313244;
                height: 1px;
                margin: 9px 4px;
            }

            QLabel#ToolbarSectionLabel {
                color: #6c7086;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 4px 4px 5px 4px;
            }

            QLabel#ToolbarFieldLabel {
                color: #a6adc8;
                font-size: 11px;
                padding: 7px 3px 3px 3px;
            }

            QToolBar#MapEditorTools QDoubleSpinBox,
            QToolBar#MapEditorTools QSpinBox {
                min-height: 28px;
                padding: 2px 7px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:hover,
            QToolBar#MapEditorTools QSpinBox:hover {
                border-color: #45475a;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:focus,
            QToolBar#MapEditorTools QSpinBox:focus {
                border-color: #89b4fa;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:disabled,
            QToolBar#MapEditorTools QSpinBox:disabled {
                color: #585b70;
                background: #181825;
            }

            QToolBar#MapEditorTools QCheckBox {
                min-height: 25px;
                padding: 5px 3px 2px 3px;
                color: #cdd6f4;
                spacing: 8px;
            }

            QToolBar#MapEditorTools QCheckBox:disabled {
                color: #585b70;
            }

            QToolBar#MapEditorTools QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #585b70;
                background: #11111b;
            }

            QToolBar#MapEditorTools QCheckBox::indicator:hover {
                border-color: #89b4fa;
            }

            QToolBar#MapEditorTools QCheckBox::indicator:checked {
                background: #89b4fa;
                border-color: #89b4fa;
            }

            QPushButton#ColorSwatch {
                min-width: 158px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }

            QPushButton#ColorSwatch:hover {
                border-width: 2px;
            }

            QPushButton#ColorSwatch:disabled {
                color: #6c7086;
                background: #1e1e2e;
                border-color: #313244;
            }
            """
        )

    @classmethod
    def _tool_icon(cls, tool: EditorTool) -> QIcon:
        icon = QIcon()
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#cdd6f4")),
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#89b4fa")),
            QIcon.Mode.Normal,
            QIcon.State.On,
        )
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#585b70")),
            QIcon.Mode.Disabled,
            QIcon.State.Off,
        )
        return icon

    @staticmethod
    def _tool_icon_pixmap(tool: EditorTool, color: QColor) -> QPixmap:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if tool is EditorTool.SELECT:
            polygon = QPolygonF([
                QPointF(5.0, 3.0),
                QPointF(17.0, 13.0),
                QPointF(11.5, 14.0),
                QPointF(15.5, 20.0),
                QPointF(12.5, 21.5),
                QPointF(8.5, 15.2),
                QPointF(5.0, 19.0),
            ])
            painter.drawPolygon(polygon)

        elif tool is EditorTool.IMAGE:
            painter.drawRoundedRect(QRectF(3.5, 4.0, 17.0, 16.0), 2.0, 2.0)
            painter.drawEllipse(QPointF(8.0, 8.5), 1.4, 1.4)
            painter.drawPolyline(QPolygonF([
                QPointF(5.5, 17.0),
                QPointF(10.0, 12.5),
                QPointF(13.0, 15.0),
                QPointF(16.0, 11.5),
                QPointF(19.0, 15.0),
            ]))

        elif tool is EditorTool.FREEHAND:
            path = QPainterPath(QPointF(3.0, 17.0))
            path.cubicTo(QPointF(7.0, 5.0), QPointF(10.0, 21.0), QPointF(14.0, 10.0))
            path.cubicTo(QPointF(16.0, 5.0), QPointF(19.0, 7.0), QPointF(21.0, 4.0))
            painter.drawPath(path)

        elif tool is EditorTool.LINE:
            painter.drawLine(QPointF(4.0, 19.0), QPointF(20.0, 5.0))

        elif tool is EditorTool.WALL:
            for row, offset in ((6.0, 0.0), (12.0, 4.0), (18.0, 0.0)):
                painter.drawLine(QPointF(3.0, row), QPointF(21.0, row))
                for x in range(int(3 + offset), 21, 8):
                    painter.drawLine(QPointF(float(x), row - 6.0 if row > 6.0 else 3.0), QPointF(float(x), row))
            painter.drawEllipse(QPointF(4.0, 19.0), 1.2, 1.2)
            painter.drawEllipse(QPointF(20.0, 5.0), 1.2, 1.2)

        elif tool is EditorTool.RECT:
            painter.drawRect(QRectF(4.0, 5.0, 16.0, 14.0))

        elif tool is EditorTool.CIRCLE:
            painter.drawEllipse(QRectF(4.0, 4.0, 16.0, 16.0))

        elif tool is EditorTool.TEXT:
            font = QFont()
            font.setBold(True)
            font.setPixelSize(17)
            painter.setFont(font)
            painter.drawText(QRectF(2.0, 1.0, 20.0, 21.0), Qt.AlignmentFlag.AlignCenter, "T")

        painter.end()
        return pixmap

    def _activate_tool(self, tool: EditorTool) -> None:
        if tool is EditorTool.IMAGE:
            self._choose_image_tool()
            return
        self.canvas.set_tool(tool)

    def _choose_image_file(self, title: str = "Import image") -> Optional[Path]:
        source, _filter = QFileDialog.getOpenFileName(
            self,
            title,
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
        )
        return Path(source) if source else None

    def _import_image_file(self, source: Path) -> Optional[tuple[Path, int, int]]:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return None

        relative = import_media(map_dir, source)
        width, height = media_size(map_dir, relative)
        if width <= 0 or height <= 0:
            width, height = 128, 128
        self.canvas.reload_media()
        return relative, width, height

    def _choose_image_tool(self) -> None:
        source = self._choose_image_file("Import image")
        if source is None:
            self._tool_actions[EditorTool.SELECT].setChecked(True)
            self.canvas.set_tool(EditorTool.SELECT)
            return

        try:
            imported = self._import_image_file(source)
            if imported is None:
                self._tool_actions[EditorTool.SELECT].setChecked(True)
                self.canvas.set_tool(EditorTool.SELECT)
                return

            relative, width, height = imported
            self.canvas.set_pending_image(relative, width, height)
            self._tool_actions[EditorTool.IMAGE].setChecked(True)
        except Exception as error:
            QMessageBox.critical(self, "Import image", str(error))
            self._tool_actions[EditorTool.SELECT].setChecked(True)
            self.canvas.set_tool(EditorTool.SELECT)

    def _replace_selected_image(self) -> None:
        element = self.canvas.selected_element()
        if not isinstance(element, ImageElement) or element.locked:
            return

        source = self._choose_image_file("Choose image")
        if source is None:
            return

        try:
            imported = self._import_image_file(source)
            if imported is None:
                return

            relative, _width, _height = imported
            # Replacing the source intentionally preserves the current frame size.
            self.canvas.replace_selected_image_asset(relative)
        except Exception as error:
            QMessageBox.critical(self, "Choose image", str(error))

    def _choose_stroke_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._stroke_color_value), self, "Choose color")
        if color.isValid():
            self._stroke_color_value = color.name(QColor.NameFormat.HexRgb)
            self._refresh_color_buttons()
            self._apply_tool_style()

    def _choose_fill_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._fill_color_value), self, "Choose fill color")
        if color.isValid():
            self._fill_color_value = color.name(QColor.NameFormat.HexRgb)
            self._fill_enabled.setChecked(True)
            self._refresh_color_buttons()
            self._apply_tool_style()

    @staticmethod
    def _swatch_text_color(value: str) -> str:
        color = QColor(value)
        luminance = (
            0.2126 * color.redF()
            + 0.7152 * color.greenF()
            + 0.0722 * color.blueF()
        )
        return "#11111b" if luminance > 0.55 else "#f5e0dc"

    def _refresh_color_buttons(self) -> None:
        stroke_text = self._swatch_text_color(self._stroke_color_value)
        fill_text = self._swatch_text_color(self._fill_color_value)

        self._stroke_color_button.setStyleSheet(
            f"""
            QPushButton#ColorSwatch {{
                color: {stroke_text};
                background: {self._stroke_color_value};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#ColorSwatch:hover {{
                border: 2px solid #cdd6f4;
            }}
            QPushButton#ColorSwatch:disabled {{
                color: #6c7086;
                background: #1e1e2e;
                border: 1px solid #313244;
            }}
            """
        )
        self._stroke_color_button.setText(self._stroke_color_value.upper())

        self._fill_color_button.setStyleSheet(
            f"""
            QPushButton#ColorSwatch {{
                color: {fill_text};
                background: {self._fill_color_value};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#ColorSwatch:hover {{
                border: 2px solid #cdd6f4;
            }}
            QPushButton#ColorSwatch:disabled {{
                color: #6c7086;
                background: #1e1e2e;
                border: 1px solid #313244;
            }}
            """
        )
        self._fill_color_button.setText(self._fill_color_value.upper())

    def _apply_tool_style(self, *_args) -> None:
        self.canvas.set_creation_style(
            stroke_color=self._stroke_color_value,
            fill_color=self._fill_color_value if self._fill_enabled.isChecked() else None,
            stroke_width=self._stroke_width.value(),
            opacity=self._opacity.value(),
            font_size=float(self._font_size.value()),
        )

    def _sync_tool_action(self, tool: EditorTool) -> None:
        action = self._tool_actions.get(tool)
        if action is not None:
            action.setChecked(True)
        self._update_tool_controls()
        self._update_status()

    def _update_tool_controls(self) -> None:
        tool = self.canvas.active_tool
        drawing = tool in DRAW_TOOLS
        shape = tool in (EditorTool.RECT, EditorTool.CIRCLE)
        colored = drawing or tool is EditorTool.TEXT

        self._stroke_color_button.setEnabled(colored)
        self._stroke_width.setEnabled(drawing)
        self._fill_enabled.setEnabled(shape)
        self._fill_color_button.setEnabled(shape and self._fill_enabled.isChecked())
        self._opacity.setEnabled(tool is not EditorTool.SELECT)
        self._font_size.setEnabled(tool is EditorTool.TEXT)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self._status_bar = bar

        self._root_layout.addWidget(
            bar,
            0,
        )

        self._update_status()

    def _connect_signals(self) -> None:
        self.canvas.mouse_world_moved.connect(self._on_mouse_world_moved)
        self.canvas.zoom_changed.connect(lambda _zoom: self._update_status())
        self.canvas.selection_changed.connect(lambda _element: self._update_status())
        self.canvas.tool_changed.connect(self._sync_tool_action)
        self.canvas.image_replace_requested.connect(self._replace_selected_image)
        self.canvas.token_double_clicked.connect(self._open_entity_sheet)
        self.canvas.token_placed.connect(lambda _entity_id: self.refresh_token_sources())
        self.canvas.history_changed.connect(lambda _undo, _redo: self._update_status())

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def _update_status(self, world: Optional[Vec2] = None) -> None:
        selected = self.canvas.selected_elements()
        if len(selected) > 1:
            selection_text = f" | selected: {len(selected)} elements"
        elif selected:
            selection = selected[0]
            selection_text = f" | selected: {selection.type_name} ({selection.id[:8]})"
        elif self.canvas.camera_selected:
            state = self.asset.initial_gm_camera
            selection_text = (
                " | selected: initial GM camera"
                f" ({state.zoom:.2f}x)"
            )
        else:
            selection_text = ""
        mouse_text = (
            f" | x={world.x:.1f}, y={world.y:.1f}"
            if world is not None
            else ""
        )

        token_text = (
            f" | placing token: {self.canvas.pending_token_entity_id[:8]}"
            if self.canvas.pending_token_entity_id
            else ""
        )
        self._status_bar.showMessage(
            f"{self.asset.name} | tool: {self.canvas.active_tool.name.lower()} "
            f"| zoom: {self.canvas.camera.zoom:.2f}x"
            f"{mouse_text}{selection_text}{token_text}"
        )

    def _on_mouse_world_moved(self, x: float, y: float) -> None:
        self._update_status(Vec2(x, y))

    # -------------------------------------------------------------------------
    # Map operations
    # -------------------------------------------------------------------------

    def _set_map_loaded(self, loaded: bool) -> None:
        """Enable editing only when the editor owns a persistent map folder."""
        self.canvas.setEnabled(loaded)
        if hasattr(self, "_editor_toolbar"):
            self._editor_toolbar.setEnabled(loaded)
        if hasattr(self, "_token_toolbar"):
            self._token_toolbar.setEnabled(loaded)
        if hasattr(self, "_save_button"):
            self._save_button.setEnabled(loaded)
        if hasattr(self, "_compile_button"):
            self._compile_button.setEnabled(loaded)

    def _activate_map(self, asset: MapAsset, map_dir: Path, *, fit: bool = False) -> None:
        self.map_dir = Path(map_dir)
        self.asset = asset
        self.canvas.set_asset(asset, self.map_dir)
        self._tool_actions[EditorTool.SELECT].setChecked(True)
        self._set_map_loaded(True)
        self.refresh_token_sources()
        if fit:
            self.canvas.fit_map()
        self._update_status()

    def _require_map_dir(self) -> Optional[Path]:
        if self.map_dir is not None:
            return self.map_dir

        QMessageBox.information(
            self,
            "No map open",
            "Create or open a map first.",
        )
        return None

    def compile_map(self) -> None:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return

        try:
            compiled = self.asset.compile(
                map_dir,
            )
        except MapCompileCancelled:
            self._status_bar.showMessage("Compilation cancelled.", 3000)
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Compile map",
                str(error),
            )
            self._update_status()
            return

        self._status_bar.showMessage(
            f"Compiled {compiled.columns} x {compiled.rows} tiles.",
            4000,
        )

    def save(self) -> None:
        """Standard save entry point for host IDE integration."""
        self.save_map()

    def save_map(self) -> None:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return

        try:
            compiled = self.asset.save(
                map_dir,
            )
        except MapCompileCancelled:
            self._status_bar.showMessage("Save cancelled.", 3000)
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Save map",
                str(error),
            )
            self._update_status()
            return

        self._status_bar.showMessage(
            f"Saved and compiled ({compiled.columns} x {compiled.rows} tiles).",
            4000,
        )

    def set_initial_camera(self) -> None:
        """Compatibility helper; normal UI edits the camera directly."""
        before = self.canvas._capture_state()
        self.canvas.set_current_as_initial_camera()
        self.canvas._select_camera()
        self.canvas._commit_history(before)
        self._status_bar.showMessage(
            "Current editor view stored as initial GM camera.",
            3000,
        )
