from __future__ import annotations

from pathlib import Path
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings, SoundTrigger
from .theme import ACCENT, BG_SOFT, CARD, CARD_BORDER, TEXT, TEXT_DIM

MIN_SCALE = 0.6
MAX_SCALE = 2.5
SCALE_STEP = 0.05


class SoundPadOverlay(QWidget):
    play_requested = pyqtSignal(str)
    stop_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    volume_changed = pyqtSignal(float)
    triggers_changed = pyqtSignal()
    close_requested = pyqtSignal()
    scale_changed = pyqtSignal(float)

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
        raw_scale = getattr(settings, "soundpad_overlay_scale", 1.0) or 1.0
        self._scale: float = max(MIN_SCALE, min(MAX_SCALE, float(raw_scale)))
        self._ctrl_dragging: bool = False
        self._drag_start_pos: QPoint | None = None
        self._start_scale: float = 1.0
        self._drag_offset: QPoint | None = None

        self._triggers: list[SoundTrigger] = []
        self._play_buttons: list[QPushButton] = []
        self._grid: QGridLayout | None = None
        self._scroll: QScrollArea | None = None
        self._edit_mode: bool = False

        self._scale_badge: QLabel | None = None
        self._badge_timer = QTimer(self)
        self._badge_timer.setSingleShot(True)
        self._badge_timer.timeout.connect(self._hide_scale_badge)

        self._build_ui()
        self.set_triggers(settings.sound_triggers)
        self.set_volume(settings.master_volume)
        self._restore_geometry()

        self._keep_top_timer = QTimer(self)
        self._keep_top_timer.timeout.connect(self._raise_if_needed)
        self._keep_top_timer.start(5000)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self._container = QWidget()
        self._register_event_filter(self._container)

        self._container_layout = QVBoxLayout(self._container)

        # Компактный заголовок в 1 строку (иконка + громкость + кнопки действий)
        header = QHBoxLayout()
        header.setSpacing(2)
        header.setContentsMargins(0, 0, 0, 0)

        self._title_label = QLabel("🎵")
        self._title_label.setToolTip("Саундпад · Зажмите Ctrl + колёсико мыши для изменения размера")
        self._register_event_filter(self._title_label)
        header.addWidget(self._title_label)

        self._vol_icon = QLabel("🔊")
        self._vol_icon.setToolTip("Громкость воспроизведения")
        self._register_event_filter(self._vol_icon)
        header.addWidget(self._vol_icon)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(int(round(self._settings.master_volume * 100)))
        self._volume_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._volume_slider.valueChanged.connect(self._on_volume_slider_changed)
        self._register_event_filter(self._volume_slider)
        header.addWidget(self._volume_slider)

        # Пустое пространство для удобного перетаскивания окна мышью
        header.addStretch(1)

        self._edit_btn = QPushButton("✎")
        self._edit_btn.setCheckable(True)
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setToolTip("Режим настройки (клик по звуку убирает его из оверлея)")
        self._edit_btn.toggled.connect(self._toggle_edit_mode)
        self._register_event_filter(self._edit_btn)
        header.addWidget(self._edit_btn)

        self._stop_btn = QPushButton("■")
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setToolTip("Остановить все звуки")
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        self._register_event_filter(self._stop_btn)
        header.addWidget(self._stop_btn)

        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setToolTip("Перезагрузить звуковые файлы")
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        self._register_event_filter(self._refresh_btn)
        header.addWidget(self._refresh_btn)

        self._close_btn = QPushButton("✕")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Скрыть оверлей")
        self._close_btn.clicked.connect(self.close_requested.emit)
        self._register_event_filter(self._close_btn)
        header.addWidget(self._close_btn)

        self._container_layout.addLayout(header)

        # Область прокрутки звуков
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._register_event_filter(self._scroll)
        self._register_event_filter(self._scroll.viewport())

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._register_event_filter(self._scroll_content)

        self._grid = QGridLayout(self._scroll_content)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._scroll_content)
        self._container_layout.addWidget(self._scroll)

        root.addWidget(self._container)

        self._apply_scale()

    def _register_event_filter(self, widget: QWidget) -> None:
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Перехват событий мыши и колесика при зажатой клавише Ctrl для масштабирования."""
        event_type = event.type()

        # 1. Масштабирование через Ctrl + колёсико мыши
        if event_type == QEvent.Type.Wheel:
            assert isinstance(event, QWheelEvent)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.change_scale(SCALE_STEP)
                elif delta < 0:
                    self.change_scale(-SCALE_STEP)
                return True

        # 2. Масштабирование через Ctrl + перетаскивание ЛКМ
        elif event_type == QEvent.Type.MouseButtonPress:
            assert isinstance(event, QMouseEvent)
            if event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._ctrl_dragging = True
                self._drag_start_pos = event.globalPosition().toPoint()
                self._start_scale = self._scale
                return True

        elif event_type == QEvent.Type.MouseMove:
            assert isinstance(event, QMouseEvent)
            if self._ctrl_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
                curr = event.globalPosition().toPoint()
                if self._drag_start_pos is not None:
                    diff = curr - self._drag_start_pos
                    # Движение вправо/вниз увеличивает масштаб, влево/вверх уменьшает
                    delta = (diff.x() + diff.y()) / 250.0
                    self.set_scale(self._start_scale + delta)
                return True

        elif event_type == QEvent.Type.MouseButtonRelease:
            if self._ctrl_dragging:
                self._ctrl_dragging = False
                return True

        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.change_scale(SCALE_STEP)
            elif delta < 0:
                self.change_scale(-SCALE_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.change_scale(0.1)
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Minus:
                self.change_scale(-0.1)
                event.accept()
                return
            elif event.key() == Qt.Key.Key_0:
                self.set_scale(1.0)
                event.accept()
                return
        super().keyPressEvent(event)

    def change_scale(self, delta: float) -> None:
        self.set_scale(self._scale + delta)

    def set_scale(self, new_scale: float) -> None:
        new_scale = max(MIN_SCALE, min(MAX_SCALE, round(new_scale, 2)))
        if abs(new_scale - self._scale) < 0.001:
            return
        self._scale = new_scale
        self._settings.soundpad_overlay_scale = new_scale
        self.scale_changed.emit(new_scale)
        self._apply_scale()
        self._show_scale_badge(f"Масштаб: {int(round(new_scale * 100))}%")

    def _apply_scale(self) -> None:
        s = self._scale

        # 1. Контейнер и отступы
        width = int(round(210 * s))
        self._container.setFixedWidth(width)

        margins = max(3, int(round(6 * s)))
        spacing = max(2, int(round(4 * s)))
        radius = max(4, int(round(8 * s)))

        self._container_layout.setContentsMargins(margins, margins, margins, margins)
        self._container_layout.setSpacing(spacing)
        if self._grid is not None:
            self._grid.setSpacing(spacing)

        self._container.setStyleSheet(
            f"""
            background-color: {BG_SOFT};
            border: 1px solid {CARD_BORDER};
            border-radius: {radius}px;
            """
        )

        # 2. Заголовок
        title_font = max(9, int(round(11 * s)))
        self._title_label.setStyleSheet(f"color: {TEXT}; font-size: {title_font}px;")

        vol_font = max(7, int(round(9 * s)))
        vol_margin = max(1, int(round(2 * s)))
        self._vol_icon.setStyleSheet(f"color: {TEXT_DIM}; font-size: {vol_font}px; margin-left: {vol_margin}px;")

        # Ползунок громкости
        slider_w = int(round(50 * s))
        slider_h = max(10, int(round(12 * s)))
        groove_h = max(2, int(round(3 * s)))
        handle_w = max(5, int(round(7 * s)))
        handle_margin = -max(1, int(round(2 * s)))
        handle_r = max(2, int(round(3 * s)))

        self._volume_slider.setFixedWidth(slider_w)
        self._volume_slider.setFixedHeight(slider_h)
        self._volume_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                height: {groove_h}px;
                background: {CARD_BORDER};
                border-radius: 1px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT};
                border-radius: 1px;
            }}
            QSlider::handle:horizontal {{
                width: {handle_w}px;
                margin: {handle_margin}px 0;
                border-radius: {handle_r}px;
                background: {TEXT};
            }}
            """
        )

        # Кнопки действий в шапке
        btn_sz = max(14, int(round(18 * s)))
        btn_font = max(8, int(round(10 * s)))
        btn_r = max(2, int(round(3 * s)))

        for btn in (self._edit_btn, self._stop_btn, self._refresh_btn, self._close_btn):
            btn.setFixedSize(btn_sz, btn_sz)

        self._apply_header_buttons_style(btn_font, btn_r)

        # 3. Область прокрутки
        sb_w = max(3, int(round(4 * s)))
        sb_r = max(1, int(round(2 * s)))
        sb_min_h = max(10, int(round(14 * s)))

        self._scroll.setStyleSheet(
            f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                width: {sb_w}px;
                background: transparent;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {CARD_BORDER};
                min-height: {sb_min_h}px;
                border-radius: {sb_r}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {ACCENT};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )

        # 4. Кнопки звуков
        self._update_sound_buttons_style()

        # 5. Высота скролла
        if self._grid is not None:
            grid_height = self._grid.sizeHint().height()
            max_scroll_h = int(round(260 * s))
            min_scroll_h = max(20, int(round(24 * s)))
            self._scroll.setFixedHeight(min(max(grid_height + 2, min_scroll_h), max_scroll_h))

        self.adjustSize()
        self.update()

    def _apply_header_buttons_style(self, font_size: int, radius: int) -> None:
        if self._edit_mode:
            self._edit_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: rgba(255, 75, 75, 0.25);
                    border: 1px solid #ff5555;
                    color: #ff5555;
                    font-size: {font_size + 1}px;
                    border-radius: {radius}px;
                }}
                """
            )
        else:
            self._edit_btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    color: {TEXT_DIM};
                    font-size: {font_size + 1}px;
                    border-radius: {radius}px;
                }}
                QPushButton:hover {{
                    background: {CARD};
                    color: {TEXT};
                }}
                """
            )

        self._stop_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {TEXT_DIM};
                font-size: {font_size}px;
                border-radius: {radius}px;
            }}
            QPushButton:hover {{
                background: {CARD};
                color: {TEXT};
            }}
            """
        )

        self._refresh_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {TEXT_DIM};
                font-size: {font_size + 1}px;
                border-radius: {radius}px;
            }}
            QPushButton:hover {{
                background: {CARD};
                color: {TEXT};
            }}
            """
        )

        self._close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {TEXT_DIM};
                font-size: {font_size}px;
                border-radius: {radius}px;
            }}
            QPushButton:hover {{
                background: rgba(239, 68, 68, 0.25);
                color: #ef4444;
            }}
            """
        )

    def _update_sound_buttons_style(self) -> None:
        s = self._scale
        btn_font = max(8, int(round(10 * s)))
        btn_pad_v = max(2, int(round(4 * s)))
        btn_pad_h = max(3, int(round(6 * s)))
        btn_min_h = max(18, int(round(22 * s)))
        btn_radius = max(3, int(round(5 * s)))

        for button in self._play_buttons:
            if self._edit_mode:
                button.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: rgba(255, 75, 75, 0.15);
                        border: 1px dashed #ff5555;
                        border-radius: {btn_radius}px;
                        padding: {btn_pad_v}px {btn_pad_h}px;
                        color: #ff8888;
                        font-size: {btn_font}px;
                        font-weight: 600;
                        min-height: {btn_min_h}px;
                    }}
                    QPushButton:hover {{
                        background-color: rgba(255, 75, 75, 0.35);
                        color: #ffffff;
                    }}
                    """
                )
            else:
                button.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: {CARD};
                        border: 1px solid {CARD_BORDER};
                        border-radius: {btn_radius}px;
                        padding: {btn_pad_v}px {btn_pad_h}px;
                        color: {TEXT};
                        font-size: {btn_font}px;
                        font-weight: 600;
                        min-height: {btn_min_h}px;
                    }}
                    QPushButton:hover {{
                        border-color: {ACCENT};
                        color: {ACCENT};
                    }}
                    QPushButton:disabled {{
                        color: {TEXT_DIM};
                    }}
                    """
                )

    def _show_scale_badge(self, text: str) -> None:
        if self._scale_badge is None:
            self._scale_badge = QLabel(self)
            self._scale_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._scale_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        badge_font = max(9, int(round(11 * self._scale)))
        self._scale_badge.setStyleSheet(
            f"""
            background-color: rgba(15, 23, 42, 0.92);
            color: {ACCENT};
            border: 1px solid {ACCENT};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: {badge_font}px;
            font-weight: bold;
            """
        )
        self._scale_badge.setText(text)
        self._scale_badge.adjustSize()
        bx = max(0, (self.width() - self._scale_badge.width()) // 2)
        by = max(0, (self.height() - self._scale_badge.height()) // 2)
        self._scale_badge.move(bx, by)
        self._scale_badge.show()
        self._scale_badge.raise_()
        self._badge_timer.start(1200)

    def _hide_scale_badge(self) -> None:
        if self._scale_badge is not None:
            self._scale_badge.hide()

    def _on_volume_slider_changed(self, value: int) -> None:
        self._volume_slider.setToolTip(f"Громкость: {value}%")
        self.volume_changed.emit(value / 100.0)

    def _toggle_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        s = self._scale
        btn_font = max(8, int(round(10 * s)))
        btn_r = max(2, int(round(3 * s)))
        self._apply_header_buttons_style(btn_font, btn_r)
        self.set_triggers(self._triggers)

    def set_triggers(self, triggers: list[SoundTrigger]) -> None:
        self._triggers = triggers
        if self._grid is None or self._scroll is None:
            return

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._play_buttons.clear()

        # Показываем только доступные звуки, включенные в оверлей
        display_triggers = [t for t in triggers if getattr(t, "in_overlay", True) and t.available]

        if not display_triggers:
            empty_font = max(8, int(round(10 * self._scale)))
            empty_label = QLabel("Все звуки скрыты\nВключите в Саундпаде")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: {empty_font}px; padding: 10px;")
            self._grid.addWidget(empty_label, 0, 0)
            self._scroll.setFixedHeight(int(round(48 * self._scale)))
            return

        cols = 2
        total = len(display_triggers)
        for i, trigger in enumerate(display_triggers):
            btn_text = f"✕ {trigger.label}" if self._edit_mode else trigger.label
            button = QPushButton(btn_text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._register_event_filter(button)

            if self._edit_mode:
                button.setToolTip(f"Кликните, чтобы убрать «{trigger.label}» из оверлея")
                button.clicked.connect(lambda _, t=trigger: self._remove_from_overlay(t))
            else:
                button.setToolTip(f"{trigger.label}\nЛКМ — воспроизвести\nПКМ — убрать из оверлея")
                button.clicked.connect(lambda _, p=trigger.path: self.play_requested.emit(p))

            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, b=button, t=trigger: self._show_context_menu(b, pos, t)
            )

            if total % cols == 1 and i == total - 1:
                self._grid.addWidget(button, i // cols, 0, 1, cols)
            else:
                self._grid.addWidget(button, i // cols, i % cols)

            self._play_buttons.append(button)

        self._update_sound_buttons_style()

        grid_height = self._grid.sizeHint().height()
        max_scroll_h = int(round(260 * self._scale))
        min_scroll_h = max(20, int(round(24 * self._scale)))
        self._scroll.setFixedHeight(min(max(grid_height + 2, min_scroll_h), max_scroll_h))
        self.adjustSize()

    def _show_context_menu(self, button: QPushButton, pos: QPoint, trigger: SoundTrigger) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: {BG_SOFT};
                color: {TEXT};
                border: 1px solid {CARD_BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 5px 14px;
                font-size: 11px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {CARD};
                color: {ACCENT};
            }}
            """
        )
        act_hide = menu.addAction("✕ Убрать из оверлея")
        act_delete = menu.addAction("🗑 Удалить совсем")
        chosen = menu.exec(button.mapToGlobal(pos))
        if chosen == act_hide:
            self._remove_from_overlay(trigger)
        elif chosen == act_delete:
            self._delete_sound_completely(trigger)

    def _remove_from_overlay(self, trigger: SoundTrigger) -> None:
        trigger.in_overlay = False
        self.triggers_changed.emit()
        self.set_triggers(self._settings.sound_triggers)

    def _delete_sound_completely(self, trigger: SoundTrigger) -> None:
        if trigger in self._settings.sound_triggers:
            self._settings.sound_triggers.remove(trigger)
        ignored = list(getattr(self._settings, "ignored_sounds", []))
        stem = Path(trigger.path).stem.lower() if trigger.path else trigger.label.lower()
        if stem and stem not in ignored:
            ignored.append(stem)
            self._settings.ignored_sounds = ignored
        self.triggers_changed.emit()
        self.set_triggers(self._settings.sound_triggers)

    def set_volume(self, volume: float) -> None:
        val = int(round(volume * 100))
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(val)
        self._volume_slider.setToolTip(f"Громкость: {val}%")
        self._volume_slider.blockSignals(False)

    def _raise_if_needed(self) -> None:
        if self.isVisible():
            self.raise_()

    def _restore_geometry(self) -> None:
        geom = getattr(self._settings, "soundpad_overlay_geometry", [])
        if geom and len(geom) >= 2:
            self.move(geom[0], geom[1])

    def closeEvent(self, event) -> None:
        self._settings.soundpad_overlay_geometry = [self.x(), self.y(), self.width(), self.height()]
        super().closeEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._ctrl_dragging = True
                self._drag_start_pos = event.globalPosition().toPoint()
                self._start_scale = self._scale
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._ctrl_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            curr = event.globalPosition().toPoint()
            if self._drag_start_pos is not None:
                diff = curr - self._drag_start_pos
                delta = (diff.x() + diff.y()) / 250.0
                self.set_scale(self._start_scale + delta)
            event.accept()
            return
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._ctrl_dragging:
            self._ctrl_dragging = False
        self._drag_offset = None
        self._settings.soundpad_overlay_geometry = [self.x(), self.y(), self.width(), self.height()]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(ACCENT + "33"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = max(4, int(round(8 * self._scale)))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)
        painter.end()
        super().paintEvent(event)
