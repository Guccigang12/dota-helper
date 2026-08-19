
   
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QScreen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import ACCENT, CARD, TEXT


class CropOverlay(QWidget):
                                                                  

    region_selected = pyqtSignal(int, dict)                                                                         
    cancelled = pyqtSignal()

    def __init__(self, screen: QScreen, monitor_index: int, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)

        self._screen = screen
        self._monitor_index = monitor_index
        geometry = screen.geometry()                                          
        self._screen_x, self._screen_y = geometry.x(), geometry.y()
        self.setGeometry(geometry)

        self._start: QPoint | None = None
        self._current: QPoint | None = None

        hint = QLabel(
            "Выдели область игрового чата мышью  •  Esc — отмена"
        )
        hint.setStyleSheet(
            f"background-color: {CARD}; color: {TEXT}; border-radius: 8px; padding: 8px 14px;"
        )
        hint.setFont(QFont("Segoe UI", 11))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.addWidget(hint)
        layout.addStretch(1)

                                                                          

    def paintEvent(self, event) -> None:                                   
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

                                 
        painter.fillRect(self.rect(), QColor(10, 14, 20, 120))

        if self._start is not None and self._current is not None:
            rect = QRect(self._start, self._current).normalized()
                                             
            painter.fillRect(rect, QColor(ACCENT + "33"))
            pen = QPen(QColor(ACCENT), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

            label = f"{rect.width()} × {rect.height()}"
            painter.setPen(QColor(TEXT))
            painter.setFont(QFont("Segoe UI", 10))
            text_rect = rect.translated(0, -26)
            painter.fillRect(text_rect, QColor(CARD))
            painter.drawText(text_rect.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignCenter, label)
        painter.end()

                                                                          

    def mousePressEvent(self, event) -> None:              
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._current = self._start
            self.update()

    def mouseMoveEvent(self, event) -> None:              
        if self._start is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:              
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        end = event.position().toPoint()
        rect = QRect(self._start, end).normalized()
        self._start = None
        self._current = None

        if rect.width() >= 20 and rect.height() >= 20:
            bbox = {
                "left": rect.x(),
                "top": rect.y(),
                "width": rect.width(),
                "height": rect.height(),
            }
            self.region_selected.emit(self._monitor_index, bbox)
        self.close()

    def keyPressEvent(self, event) -> None:              
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:              
                               
        self.cancelled.emit()
        self.close()
