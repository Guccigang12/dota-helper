                                                        
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

class Card(QFrame):
                                                         

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        if title:
            title_label = QLabel(title)
            title_label.setObjectName("cardTitle")
            layout.addWidget(title_label)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        layout.addLayout(self._content_layout)
        layout.addStretch(1)

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout


class ToggleSwitch(QCheckBox):
                                      

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class NeonButton(QPushButton):
                                               

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("neon")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class GhostButton(QPushButton):
                                     

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("ghost")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class PlayButton(QPushButton):
                                          

    def __init__(self, text: str = "▶", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("play")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(52)


def make_row(*widgets: QWidget, spacing: int = 8) -> QWidget:
                                                
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for w in widgets:
        layout.addWidget(w)
    layout.addStretch(1)
    return container


def dim_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("dim")
    return label
