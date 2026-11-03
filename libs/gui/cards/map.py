# -*- coding: utf-8 -*-

"""
Map card module for the RPG Tabletop App.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .base import ResourceCard
from ...assets.campaign import get_current_campaign


THEME = """
QLabel#MapCardPreview {
    background-color: #171a22;
    border: 1px solid #383d49;
    color: #9da4b5;
}

QLabel#MapCardName {
    font-size: 14px;
    font-weight: 600;
}
"""


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


class MapCard(ResourceCard):
    """Preview card showing a map image and its name."""

    def __init__(
        self,
        resource_id: str,
        parent: QWidget | None = None,
        *,
        preview_path: str | Path | None = None,
    ) -> None:
        super().__init__(resource_id, parent)

        self._preview_path = Path(preview_path) if preview_path else self._find_preview()
        self._source_pixmap = (
            QPixmap(str(self._preview_path))
            if self._preview_path is not None
            else QPixmap()
        )

        self._build_ui()
        self.refresh()

    def _find_preview(self) -> Path | None:
        """Find a named preview in the campaign's map or media directory."""
        if Path(self.resource_id).name != self.resource_id:
            return None

        campaign = get_current_campaign()
        map_directory = campaign.map_path(self.resource_id)

        candidates = [
            campaign.map_media_path / f"{self.resource_id}{extension}"
            for extension in IMAGE_EXTENSIONS
        ]
        candidates.extend(
            map_directory / f"{preview_name}{extension}"
            for preview_name in ("preview", "thumbnail", self.resource_id)
            for extension in IMAGE_EXTENSIONS
        )

        for candidate in candidates:
            if candidate.is_file():
                return candidate

        if map_directory.is_dir():
            for candidate in sorted(map_directory.iterdir()):
                if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
                    return candidate

        return None

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.preview_label = QLabel()
        self.preview_label.setObjectName("MapCardPreview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(110)
        layout.addWidget(self.preview_label, 1)

        self.name_label = QLabel()
        self.name_label.setObjectName("MapCardName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)

    def refresh(self) -> None:
        """Refresh the map name and its image preview."""
        self.name_label.setText(self.resource_id)
        if self._source_pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Aperçu indisponible")
            return

        self.preview_label.setText("")
        self._update_preview()

    def _update_preview(self) -> None:
        if self._source_pixmap.isNull():
            return

        size = self.preview_label.size()
        if size.isEmpty():
            return

        self.preview_label.setPixmap(
            self._source_pixmap.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview()

    def search_text(self) -> str:
        """Text indexed by ResourceBrowser."""
        return self.resource_id.casefold()