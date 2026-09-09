from __future__ import annotations

from PIL import Image
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QScreen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import ACCENT, CARD, TEXT


def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    img = pil_img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class CropOverlay(QWidget):
    """
    Экранный оверлей для выделения области текста.
    При отпускании мыши вырезает чистый фрагмент скриншота и эмитит сигнал.
    """

    area_captured = pyqtSignal(object, int, dict)  # (PIL.Image, monitor_index, bbox)
    region_selected = pyqtSignal(int, dict)  # (monitor_index, bbox)
    cancelled = pyqtSignal()

    def __init__(
        self,
        screen: QScreen,
        monitor_index: int,
        screenshot: Image.Image | None = None,
        pixmap: QPixmap | None = None,
        parent: QWidget | None = None,
    ) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._screen = screen
        self._monitor_index = monitor_index
        self._screenshot = screenshot
        self._pixmap = pixmap

        self.setScreen(screen)
        geometry = screen.geometry()
        self._screen_x, self._screen_y = geometry.x(), geometry.y()
        self.setGeometry(geometry)

        if self._pixmap is not None and self._screenshot is None:
            try:
                qimg = self._pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                w, h = qimg.width(), qimg.height()
                ptr = qimg.bits()
                ptr.setsize(h * w * 4)
                self._screenshot = Image.frombuffer("RGBA", (w, h), bytes(ptr), "raw", "RGBA", 0, 1).convert("RGB")
            except Exception:
                self._screenshot = None
        elif self._screenshot is not None and self._pixmap is None:
            try:
                self._pixmap = pil_to_qpixmap(self._screenshot)
            except Exception:
                self._pixmap = None

        self._start: QPoint | None = None
        self._current: QPoint | None = None

        hint_text = f"✂ Экран {monitor_index} ({screen.name()})  •  Выделите область чата мышью  •  Esc — отмена"
        hint = QLabel(hint_text)
        hint.setStyleSheet(
            f"background-color: {CARD}EE; color: {TEXT}; border: 1px solid {ACCENT}88; "
            f"border-radius: 8px; padding: 8px 18px; font-weight: 500;"
        )
        hint.setFont(QFont("Segoe UI", 11))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.addWidget(hint, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        if self._pixmap is not None and not self._pixmap.isNull():
            # 1. Draw base screenshot
            painter.drawPixmap(0, 0, w, h, self._pixmap)
            # 2. Dim entire screen
            painter.fillRect(0, 0, w, h, QColor(10, 14, 20, 140))

            # 3. If dragging, cut out the bright selected region
            if self._start is not None and self._current is not None:
                rect = QRect(self._start, self._current).normalized()
                if rect.width() > 0 and rect.height() > 0:
                    # Calculate scale for source pixmap
                    scale_x = self._pixmap.width() / max(w, 1)
                    scale_y = self._pixmap.height() / max(h, 1)
                    src_x = int(rect.x() * scale_x)
                    src_y = int(rect.y() * scale_y)
                    src_w = int(rect.width() * scale_x)
                    src_h = int(rect.height() * scale_y)

                    # Draw clean un-dimmed screenshot inside selected rect
                    painter.drawPixmap(
                        rect,
                        self._pixmap,
                        QRect(src_x, src_y, src_w, src_h),
                    )

                    # Subtle neon tint
                    painter.fillRect(rect, QColor(ACCENT + "22"))

                    # Neon glowing border
                    pen = QPen(QColor(ACCENT), 2, Qt.PenStyle.SolidLine)
                    painter.setPen(pen)
                    painter.drawRect(rect)

                    # Corner accents
                    self._draw_corners(painter, rect)

                    # Size badge
                    label = f"{rect.width()} × {rect.height()} px"
                    painter.setFont(QFont("Segoe UI", 10))
                    text_rect = QRect(rect.left(), max(10, rect.top() - 28), 120, 24)
                    painter.fillRect(text_rect, QColor(CARD))
                    painter.setPen(QColor(ACCENT))
                    painter.drawRect(text_rect)
                    painter.setPen(QColor(TEXT))
                    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
        else:
            # Fallback when screenshot pixmap is unavailable
            painter.fillRect(self.rect(), QColor(10, 14, 20, 160))
            if self._start is not None and self._current is not None:
                rect = QRect(self._start, self._current).normalized()
                painter.fillRect(rect, QColor(ACCENT + "33"))
                pen = QPen(QColor(ACCENT), 2, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawRect(rect)

        painter.end()

    def _draw_corners(self, painter: QPainter, rect: QRect) -> None:
        pen = QPen(QColor(ACCENT), 4, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        size = min(12, min(rect.width(), rect.height()) // 3)
        if size <= 0:
            return

        l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
        # Top-left
        painter.drawLine(l, t, l + size, t)
        painter.drawLine(l, t, l, t + size)
        # Top-right
        painter.drawLine(r, t, r - size, t)
        painter.drawLine(r, t, r, t + size)
        # Bottom-left
        painter.drawLine(l, b, l + size, b)
        painter.drawLine(l, b, l, b - size)
        # Bottom-right
        painter.drawLine(r, b, r - size, b)
        painter.drawLine(r, b, r, b - size)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._current = self._start
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()

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

        if rect.width() >= 10 and rect.height() >= 10:
            crop_l = rect.x()
            crop_t = rect.y()
            crop_w = rect.width()
            crop_h = rect.height()

            cropped_image: Image.Image | None = None
            if self._screenshot is not None:
                scale_x = self._screenshot.width / max(self.width(), 1)
                scale_y = self._screenshot.height / max(self.height(), 1)
                crop_l = int(rect.x() * scale_x)
                crop_t = int(rect.y() * scale_y)
                crop_r = int((rect.x() + rect.width()) * scale_x)
                crop_b = int((rect.y() + rect.height()) * scale_y)
                crop_w = max(1, crop_r - crop_l)
                crop_h = max(1, crop_b - crop_t)
                try:
                    cropped_image = self._screenshot.crop((crop_l, crop_t, crop_r, crop_b))
                except Exception:
                    cropped_image = None

            bbox = {
                "left": crop_l,
                "top": crop_t,
                "width": crop_w,
                "height": crop_h,
            }

            self.region_selected.emit(self._monitor_index, bbox)
            if cropped_image is not None:
                self.area_captured.emit(cropped_image, self._monitor_index, bbox)
        else:
            self.cancelled.emit()

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

