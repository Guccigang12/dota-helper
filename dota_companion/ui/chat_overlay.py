
   
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core.settings import Settings
from .theme import CARD, CARD_BORDER, TEXT

ACCENT_LINE = "#4fa3ff55"


class ChatOverlay(QWidget):
                                           

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._messages: list[QLabel] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(6)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._restore_geometry()
        self.apply_settings()

                                                                          

    def apply_settings(self) -> None:
        font = QFont("Segoe UI", self._settings.font_size)
        for label in self._messages:
            label.setFont(font)
        self._hide_timer.setInterval(self._settings.showtime_ms)

    def set_font_size(self, px: int) -> None:
        self._settings.font_size = px
        self.apply_settings()

    def set_showtime(self, ms: int) -> None:
        self._settings.showtime_ms = ms
        self.apply_settings()

    def show_message(self, text: str) -> None:
        if not text.strip():
            return
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(QFont("Segoe UI", self._settings.font_size))
        label.setStyleSheet(
            f"color: {TEXT}; background-color: {CARD}DD; border: 1px solid {CARD_BORDER};"
            "border-radius: 8px; padding: 6px 10px;"
        )
        label.setMaximumWidth(420)

        self._layout.addWidget(label)
        self._messages.append(label)
                                        
        while len(self._messages) > 4:
            old = self._messages.pop(0)
            self._layout.removeWidget(old)
            old.deleteLater()

        self.adjustSize()
        self._restore_geometry()
        self.show()
        self.raise_()
        self._hide_timer.start(self._settings.showtime_ms)

    def clear_messages(self) -> None:
        for label in self._messages:
            self._layout.removeWidget(label)
            label.deleteLater()
        self._messages.clear()
        self.hide()

                                                                          

    def _restore_geometry(self) -> None:
        geometry = self._settings.chat_overlay_geometry
        if len(geometry) == 4:
            self.setGeometry(geometry[0], geometry[1], geometry[2], geometry[3])
        else:
            self.adjustSize()
            screen = self.screen() or self.windowHandle().screen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 48)

    def closeEvent(self, event) -> None:              
        self._settings.chat_overlay_geometry = [
            self.x(), self.y(), self.width(), self.height()
        ]
        super().closeEvent(event)

                                                                            

    def mousePressEvent(self, event) -> None:              
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:              
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:              
        self._drag_offset = None

                                                                          

    def paintEvent(self, event) -> None:              
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(ACCENT_LINE))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        painter.end()
        super().paintEvent(event)
