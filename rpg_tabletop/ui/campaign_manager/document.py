# -*- coding: utf-8 -*-

"""
Placeholder tab for a resource that has no specialized editor yet.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


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
