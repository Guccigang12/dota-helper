
   
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings, SoundTrigger
from .theme import ACCENT, BG_SOFT, CARD, CARD_BORDER, TEXT, TEXT_DIM
from .widgets import GhostButton, PlayButton


class SoundPadOverlay(QWidget):
                                   

    play_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    volume_changed = pyqtSignal(float)
    close_requested = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Саундпад")

        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._triggers: list[SoundTrigger] = []
        self._play_buttons: list[PlayButton] = []
        self._grid: QGridLayout | None = None

        self._build_ui()
        self.set_triggers(settings.sound_triggers)
        self.set_volume(settings.master_volume)

                                                                 
        self._keep_top_timer = QTimer(self)
        self._keep_top_timer.timeout.connect(self._raise_if_needed)
        self._keep_top_timer.start(5000)

                                                                          

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        container = QWidget()
        container.setStyleSheet(
            f"background-color: {BG_SOFT}; border: 1px solid {CARD_BORDER}; border-radius: 12px;"
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

               
        header = QHBoxLayout()
        title = QLabel("🎵 Саундпад")
        title.setStyleSheet(f"color: {TEXT}; font-weight: 700; font-size: 13px;")
        header.addWidget(title)
        header.addStretch(1)
        refresh_btn = GhostButton("⟳")
        refresh_btn.setToolTip("Перезагрузить звуковые файлы")
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        close_btn = GhostButton("✕")
        close_btn.setToolTip("Скрыть оверлей")
        close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(refresh_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

                         
        self._grid = QGridLayout()
        self._grid.setSpacing(6)
        layout.addLayout(self._grid)

                   
        volume_row = QHBoxLayout()
        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet(f"color: {TEXT_DIM};")
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setFixedWidth(120)
        self._volume_slider.valueChanged.connect(
            lambda v: self.volume_changed.emit(v / 100.0)
        )
        volume_row.addWidget(vol_icon)
        volume_row.addWidget(self._volume_slider)
        volume_row.addStretch(1)
        layout.addLayout(volume_row)

        root.addWidget(container)
        self.setFixedWidth(300)

                                                                          

    def set_triggers(self, triggers: list[SoundTrigger]) -> None:
        self._triggers = triggers
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._play_buttons.clear()

        for i, trigger in enumerate(triggers[:12]):
            button = QPushButton(trigger.label)
            button.setToolTip(trigger.path or "Файл не выбран")
            button.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {CARD};
                    border: 1px solid {CARD_BORDER};
                    border-radius: 8px;
                    padding: 8px 6px;
                    color: {TEXT};
                    font-size: 12px;
                }}
                QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
                QPushButton:disabled {{ color: {TEXT_DIM}; }}
                """
            )
            if trigger.available:
                button.clicked.connect(lambda _, p=trigger.path: self.play_requested.emit(p))
            else:
                button.setEnabled(False)
                button.setToolTip(f"Нет файла для «{trigger.label}» — добавь в папку звуков")
            self._grid.addWidget(button, i // 3, i % 3)

    def set_volume(self, volume: float) -> None:
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(int(round(volume * 100)))
        self._volume_slider.blockSignals(False)

                                                                          

    def _raise_if_needed(self) -> None:
        if self.isVisible():
            self.raise_()

                                                                            

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
        painter.setPen(QColor(ACCENT + "44"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        painter.end()
        super().paintEvent(event)
