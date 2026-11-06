# -*- coding: utf-8 -*-
"""Session Entity palette: token previews, folders, drag onto the map."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.session.runtime import SessionRuntime


TOKEN_ENTITY_MIME = "application/x-rpg-tabletop-session-entity"
FOLDERS_FILE = "token_folders.json"
ICON_SIZE = 72
ROLE_KIND = Qt.ItemDataRole.UserRole + 1  # "entity" | "folder" | "up"


# ------------------------------------------------------------------ folders
class TokenFolderStore:
    """One-level folders of entities, stored per campaign."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.folders: list[str] = []
        self.assignments: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        folders = raw.get("folders", [])
        assignments = raw.get("assignments", {})
        self.folders = (
            [str(f).strip() for f in folders if str(f).strip()] if isinstance(folders, list) else []
        )
        self.assignments = (
            {str(k): str(v) for k, v in assignments.items()} if isinstance(assignments, dict) else {}
        )

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            tmp.write_text(
                json.dumps(
                    {"folders": self.folders, "assignments": self.assignments},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError:
            pass

    def _exists(self, name: str) -> bool:
        return any(name.casefold() == folder.casefold() for folder in self.folders)

    def folder_of(self, entity_id: str) -> str | None:
        folder = self.assignments.get(str(entity_id))
        return folder if folder in self.folders else None

    def add_folder(self, name: str) -> bool:
        name = name.strip()
        if not name or self._exists(name):
            return False
        self.folders.append(name)
        return True

    def rename_folder(self, old: str, new: str) -> bool:
        new = new.strip()
        if old not in self.folders or not new:
            return False
        if new.casefold() != old.casefold() and self._exists(new):
            return False
        self.folders[self.folders.index(old)] = new
        self.assignments = {k: (new if v == old else v) for k, v in self.assignments.items()}
        return True

    def delete_folder(self, name: str) -> None:
        if name in self.folders:
            self.folders.remove(name)
        self.assignments = {k: v for k, v in self.assignments.items() if v != name}

    def move(self, entity_id: str, folder: str | None) -> None:
        if folder is None or folder not in self.folders:
            self.assignments.pop(str(entity_id), None)
        else:
            self.assignments[str(entity_id)] = folder


# -------------------------------------------------------------------- icons
def _folder_icon(*, up: bool = False) -> QIcon:
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#6c7086" if up else "#d8b84e"))
    painter.drawRoundedRect(QRectF(8, 16, 26, 14), 4, 4)
    painter.drawRoundedRect(QRectF(8, 24, ICON_SIZE - 16, 36), 6, 6)
    if up:
        font = QFont()
        font.setBold(True)
        font.setPixelSize(24)
        painter.setFont(font)
        painter.setPen(QColor("#11111b"))
        painter.drawText(QRectF(8, 24, ICON_SIZE - 16, 36), Qt.AlignmentFlag.AlignCenter, "↑")
    painter.end()
    return QIcon(pixmap)


def _token_icon(name: str, image_path: Path | None, placed: bool) -> QIcon:
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    circle = QRectF(4, 4, ICON_SIZE - 8, ICON_SIZE - 8)

    clip = QPainterPath()
    clip.addEllipse(circle)
    painter.setClipPath(clip)
    image = QImage(str(image_path)) if image_path is not None else QImage()
    if not image.isNull():
        side = min(image.width(), image.height())
        source = QRectF((image.width() - side) / 2.0, (image.height() - side) / 2.0, side, side)
        painter.drawImage(circle, image, source)
    else:
        painter.fillRect(circle, QColor("#2d3440"))
        initials = "".join(word[:1] for word in name.split())[:2].upper() or "?"
        font = QFont()
        font.setBold(True)
        font.setPixelSize(24)
        painter.setFont(font)
        painter.setPen(QColor("#eef2f7"))
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, initials)
    painter.setClipping(False)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#a6e3a1" if placed else "#596578"), 3))
    painter.drawEllipse(circle)
    painter.end()
    return QIcon(pixmap)


# --------------------------------------------------------------------- list
class _TokenList(QListWidget):
    """Icon grid: entities can be dragged out, onto the map or onto a folder."""

    entity_dropped = Signal(str, str)  # entity id, folder name ("" = root)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setGridSize(QSize(ICON_SIZE + 34, ICON_SIZE + 48))
        self.setWordWrap(True)
        self.setSpacing(2)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt API
        item = self.currentItem()
        if item is None or item.data(ROLE_KIND) != "entity":
            return
        entity_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not entity_id:
            return
        mime = QMimeData()
        mime.setData(TOKEN_ENTITY_MIME, entity_id.encode("utf-8"))
        mime.setText(entity_id)
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = item.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(ICON_SIZE, ICON_SIZE))
        drag.exec(Qt.DropAction.CopyAction)

    def _folder_item_at(self, pos) -> QListWidgetItem | None:
        item = self.itemAt(pos)
        if item is not None and item.data(ROLE_KIND) in ("folder", "up"):
            return item
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasFormat(TOKEN_ENTITY_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            event.mimeData().hasFormat(TOKEN_ENTITY_MIME)
            and self._folder_item_at(event.position().toPoint()) is not None
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        target = self._folder_item_at(event.position().toPoint())
        if target is None or not event.mimeData().hasFormat(TOKEN_ENTITY_MIME):
            event.ignore()
            return
        try:
            entity_id = bytes(event.mimeData().data(TOKEN_ENTITY_MIME)).decode("utf-8").strip()
        except (UnicodeDecodeError, TypeError):
            event.ignore()
            return
        folder = str(target.data(Qt.ItemDataRole.UserRole) or "") if target.data(ROLE_KIND) == "folder" else ""
        self.entity_dropped.emit(entity_id, folder)
        event.acceptProposedAction()


# -------------------------------------------------------------------- panel
class TokenManagerPanel(QWidget):
    """Lists only Entities loaded in the current SessionRuntime."""

    # Entity id whose character sheet should open
    sheet_requested = Signal(str)
    # Entity id to put in the combat initiative
    combat_add_requested = Signal(str)

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.store = TokenFolderStore(Path(runtime.campaign.root) / ".runtime" / FOLDERS_FILE)
        self._current_folder: str | None = None
        self._icon_cache: dict[tuple, QIcon] = {}
        self.setObjectName("RuntimePanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        title = QLabel("TOKENS DE SESSION")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)
        hint = QLabel(
            "Glisse une entité sur la map pour placer ou déplacer son token, "
            "ou sur un dossier pour la ranger. Double-clic ou clic droit pour "
            "ouvrir sa feuille de personnage."
        )
        hint.setObjectName("RuntimeMuted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher une entité…")
        self.search.textChanged.connect(lambda _text: self.refresh())
        self.new_folder_button = QPushButton("+ Dossier")
        self.new_folder_button.clicked.connect(lambda: self._new_folder())
        row.addWidget(self.search, 1)
        row.addWidget(self.new_folder_button)
        root.addLayout(row)

        self.location = QLabel("Dossier : racine")
        self.location.setObjectName("RuntimeMuted")
        root.addWidget(self.location)

        self.list = _TokenList()
        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.entity_dropped.connect(self._move_entity)
        root.addWidget(self.list, 1)

        self.summary = QLabel("0 entité")
        self.summary.setObjectName("RuntimeMuted")
        root.addWidget(self.summary)
        self.refresh()

    # ---------------------------------------------------------------- items
    @staticmethod
    def _make_item(text: str, icon: QIcon, kind: str, key: str, tooltip: str = "") -> QListWidgetItem:
        item = QListWidgetItem(icon, text)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setData(ROLE_KIND, kind)
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        return item

    def _entity_icon(self, entity, placed: bool) -> QIcon:
        try:
            path = entity.token
        except Exception:
            path = None
        key = (str(entity.id), entity.name, str(path), placed)
        icon = self._icon_cache.get(key)
        if icon is None:
            if len(self._icon_cache) > 512:
                self._icon_cache.clear()
            icon = _token_icon(entity.name, path, placed)
            self._icon_cache[key] = icon
        return icon

    def _selected_entity_id(self) -> str | None:
        item = self.list.currentItem()
        if item is not None and item.data(ROLE_KIND) == "entity":
            return str(item.data(Qt.ItemDataRole.UserRole))
        return None

    # -------------------------------------------------------------- refresh
    def refresh(self) -> None:
        selected = self._selected_entity_id()
        self.list.clear()

        tokens = self.runtime.active_map.tokens if self.runtime.active_map is not None else []
        placed_ids = {token.entity_id for token in tokens}
        entities = sorted(
            self.runtime.entities.items(), key=lambda pair: pair[1].name.casefold()
        )
        if self._current_folder is not None and self._current_folder not in self.store.folders:
            self._current_folder = None

        query = self.search.text().strip().casefold()
        if query:
            shown = [(i, e) for i, e in entities if query in e.name.casefold()]
            self.location.setText("Recherche dans tous les dossiers")
        else:
            if self._current_folder is None:
                self.location.setText("Dossier : racine")
                for folder in sorted(self.store.folders, key=str.casefold):
                    count = sum(1 for i, _e in entities if self.store.folder_of(i) == folder)
                    self.list.addItem(self._make_item(
                        f"{folder}\n({count})", _folder_icon(), "folder", folder,
                        "Double-clic pour ouvrir · glisse une entité dessus pour la ranger",
                    ))
            else:
                self.location.setText(f"Dossier : {self._current_folder}")
                self.list.addItem(self._make_item(
                    "Retour", _folder_icon(up=True), "up", "",
                    "Retour à la racine · dépose une entité ici pour la sortir du dossier",
                ))
            shown = [(i, e) for i, e in entities if self.store.folder_of(i) == self._current_folder]

        for entity_id, entity in shown:
            placed = entity_id in placed_ids
            folder = self.store.folder_of(entity_id)
            tooltip = (
                f"{entity.name}\n"
                f"{'Token présent sur la map' if placed else 'Glisser sur la map pour créer un token'}"
                + (f"\nDossier : {folder}" if folder else "")
            )
            item = self._make_item(
                entity.name, self._entity_icon(entity, placed), "entity", entity_id, tooltip
            )
            self.list.addItem(item)
            if entity_id == selected:
                self.list.setCurrentItem(item)

        self.summary.setText(
            f"{len(entities)} entité(s) · {len(placed_ids & {i for i, _ in entities})} token(s) sur la map "
            f"· {len(self.store.folders)} dossier(s)"
        )

    # ------------------------------------------------------------ navigation
    def _open_item(self, item: QListWidgetItem) -> None:
        kind = item.data(ROLE_KIND)
        if kind == "entity":
            self.sheet_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        elif kind == "folder":
            self._current_folder = str(item.data(Qt.ItemDataRole.UserRole))
            self.search.clear()
            self.refresh()
        elif kind == "up":
            self._current_folder = None
            self.refresh()

    # --------------------------------------------------------------- folders
    def _new_folder(self, move_entity_id: str | None = None) -> None:
        name, accepted = QInputDialog.getText(self, "Nouveau dossier", "Nom du dossier :")
        name = name.strip()
        if not accepted or not name:
            return
        if not self.store.add_folder(name):
            QMessageBox.warning(self, "Dossier", "Un dossier porte déjà ce nom.")
            return
        if move_entity_id:
            self.store.move(move_entity_id, name)
        self.store.save()
        self.refresh()

    def _rename_folder(self, folder: str) -> None:
        name, accepted = QInputDialog.getText(self, "Renommer le dossier", "Nom du dossier :", text=folder)
        name = name.strip()
        if not accepted or not name or name == folder:
            return
        if not self.store.rename_folder(folder, name):
            QMessageBox.warning(self, "Dossier", "Nom invalide ou déjà utilisé.")
            return
        if self._current_folder == folder:
            self._current_folder = name
        self.store.save()
        self.refresh()

    def _delete_folder(self, folder: str) -> None:
        answer = QMessageBox.question(
            self,
            "Supprimer le dossier",
            f"Supprimer le dossier « {folder} » ? Ses entités retournent à la racine.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_folder(folder)
        if self._current_folder == folder:
            self._current_folder = None
        self.store.save()
        self.refresh()

    def _move_entity(self, entity_id: str, folder: str) -> None:
        if entity_id not in self.runtime.entities:
            return
        self.store.move(entity_id, folder or None)
        self.store.save()
        self.refresh()

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        kind = item.data(ROLE_KIND) if item is not None else None
        menu = QMenu(self)

        if kind == "entity":
            entity_id = str(item.data(Qt.ItemDataRole.UserRole))
            sheet_action = menu.addAction("Feuille de personnage…")
            sheet_action.triggered.connect(lambda _=False, i=entity_id: self.sheet_requested.emit(i))
            menu.setDefaultAction(sheet_action)
            combat_action = menu.addAction("Ajouter à l'initiative")
            combat_action.triggered.connect(lambda _=False, i=entity_id: self.combat_add_requested.emit(i))
            menu.addSeparator()
            move_menu = menu.addMenu("Déplacer vers")
            root_action = move_menu.addAction("Racine")
            root_action.setEnabled(self.store.folder_of(entity_id) is not None)
            root_action.triggered.connect(lambda _=False, i=entity_id: self._move_entity(i, ""))
            for folder in sorted(self.store.folders, key=str.casefold):
                action = move_menu.addAction(folder)
                action.setEnabled(self.store.folder_of(entity_id) != folder)
                action.triggered.connect(lambda _=False, i=entity_id, f=folder: self._move_entity(i, f))
            move_menu.addSeparator()
            new_action = move_menu.addAction("Nouveau dossier…")
            new_action.triggered.connect(lambda _=False, i=entity_id: self._new_folder(i))
        elif kind == "folder":
            folder = str(item.data(Qt.ItemDataRole.UserRole))
            rename = menu.addAction("Renommer…")
            rename.triggered.connect(lambda _=False, f=folder: self._rename_folder(f))
            delete = menu.addAction("Supprimer le dossier")
            delete.triggered.connect(lambda _=False, f=folder: self._delete_folder(f))
        else:
            new_action = menu.addAction("Nouveau dossier…")
            new_action.triggered.connect(lambda _=False: self._new_folder())

        menu.exec(self.list.viewport().mapToGlobal(pos))