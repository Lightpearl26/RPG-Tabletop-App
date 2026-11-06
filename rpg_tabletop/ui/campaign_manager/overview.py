# -*- coding: utf-8 -*-

"""
Campaign overview page: resource counts and shortcuts of the open campaign.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.campaign import Campaign

RESOURCE_SECTIONS = (
    ("characters", "Characters", "Character", (300, 250)),
    ("entities", "Entities", "Entity", (300, 310)),
    ("items", "Items", "Item", (300, 250)),
    ("spells", "Spells", "Spell", (450, 350)),
    ("maps", "Maps", "Map", (300, 280)),
    ("sessions", "Sessions", "Session", (300, 200)),
)


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
