
                                                      
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings, SoundTrigger
from .theme import ACCENT, CARD, CARD_BORDER, TEXT, TEXT_DIM
from .widgets import Card, GhostButton, NeonButton, dim_label


class SoundPadTab(QWidget):
    play_requested = pyqtSignal(str)
    stop_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    settings_changed = pyqtSignal()
    show_overlay_requested = pyqtSignal()

    def __init__(
        self,
        settings: Settings,
        device_list_provider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._device_list_provider = device_list_provider
        self._triggers: list[SoundTrigger] = []
        self._grid: QGridLayout | None = None
        self._play_buttons: dict[int, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        audio_card = Card("🔊 Устройство вывода и громкость")
        audio_row = QHBoxLayout()
        audio_row.setSpacing(10)

        device_label = dim_label("Устройство (VAC):")
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(260)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._refresh_devices_btn = GhostButton("⟳")
        self._refresh_devices_btn.setToolTip("Обновить список устройств")
        self._refresh_devices_btn.clicked.connect(self.reload_devices)
        audio_row.addWidget(device_label)
        audio_row.addWidget(self._device_combo)
        audio_row.addWidget(self._refresh_devices_btn)

        self._monitor_check = QCheckBox("Слышать в наушниках")
        self._monitor_check.setToolTip(
            "Дублировать звук в системные наушники/динамики при выводе в виртуальный кабель, чтобы вы сами слышали то, что воспроизводите"
        )
        self._monitor_check.setStyleSheet(f"color: {TEXT}; font-weight: 600; font-size: 12px;")
        self._monitor_check.setChecked(getattr(settings, "soundpad_monitor", True))
        self._monitor_check.stateChanged.connect(self._on_monitor_changed)
        audio_row.addWidget(self._monitor_check)

        master_label = dim_label("Наушники:")
        self._master_slider = QSlider(Qt.Orientation.Horizontal)
        self._master_slider.setRange(0, 100)
        self._master_slider.setValue(int(round(settings.master_volume * 100)))
        self._master_slider.setFixedWidth(110)
        self._master_slider.valueChanged.connect(self._on_master_volume)
        audio_row.addWidget(master_label)
        audio_row.addWidget(self._master_slider)

        voice_label = dim_label("Микрофон:")
        self._voice_slider = QSlider(Qt.Orientation.Horizontal)
        self._voice_slider.setRange(0, 100)
        self._voice_slider.setValue(int(round(settings.voice_volume * 100)))
        self._voice_slider.setFixedWidth(110)
        self._voice_slider.valueChanged.connect(self._on_voice_volume)
        audio_row.addWidget(voice_label)
        audio_row.addWidget(self._voice_slider)

        audio_row.addStretch(1)
        audio_card.content_layout().addLayout(audio_row)
        root.addWidget(audio_card)

        triggers_card = Card("🎵 Аудио-триггеры")
        triggers_toolbar = QHBoxLayout()

        dir_label = dim_label("Папка звуков:")
        self._dir_label_value = QLabel()
        self._dir_label_value.setStyleSheet(f"color: {TEXT_DIM};")
        self._dir_label_value.setText(str(self._settings.sound_dir or "—"))
        browse_btn = GhostButton("Выбрать…")
        browse_btn.clicked.connect(self._browse_sound_dir)

        test_btn = GhostButton("🔔 Тест")
        test_btn.setToolTip("Проверить воспроизведение звука")
        test_btn.clicked.connect(self._test_playback)

        stop_btn = GhostButton("⏹ Стоп")
        stop_btn.setToolTip("Остановить воспроизведение всех звуков")
        stop_btn.clicked.connect(self.stop_requested.emit)

        refresh_btn = NeonButton("⟳ Refresh")
        refresh_btn.setToolTip("Перезагрузить звуковые файлы из папки")
        refresh_btn.clicked.connect(self.refresh_requested.emit)

        overlay_btn = GhostButton("Плавающий оверлей")
        overlay_btn.clicked.connect(self.show_overlay_requested.emit)

        triggers_toolbar.addWidget(dir_label)
        triggers_toolbar.addWidget(self._dir_label_value)
        triggers_toolbar.addWidget(browse_btn)
        triggers_toolbar.addStretch(1)

        self._restore_btn = GhostButton("🗑 Восстановить (0)")
        self._restore_btn.setToolTip("Восстановить скрытые/удаленные звуки из папки")
        self._restore_btn.clicked.connect(self._restore_ignored_sounds)
        self._restore_btn.setVisible(False)
        triggers_toolbar.addWidget(self._restore_btn)

        triggers_toolbar.addWidget(test_btn)
        triggers_toolbar.addWidget(stop_btn)
        triggers_toolbar.addWidget(overlay_btn)
        triggers_toolbar.addWidget(refresh_btn)
        triggers_card.content_layout().addLayout(triggers_toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(scroll_content)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(scroll_content)
        triggers_card.content_layout().addWidget(scroll)
        root.addWidget(triggers_card, 1)

        hint = dim_label(
            "Положи звуки в папку (wav/mp3/ogg/flac) — все файлы подхватятся автоматически. "
            "Крестик (✕) на карточке удаляет звук из списка, а галочка «Оверлей» позволяет убрать звук только из оверлея."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.reload_devices()
        self.set_triggers(settings.sound_triggers)

    def reload_devices(self) -> None:
        devices = self._device_list_provider()
        current = self._settings.audio_device

        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("Системный вывод (по умолчанию)", "")
        for device in devices:
            self._device_combo.addItem(device, device)

        # Выбираем текущее сохранённое устройство
        selected = 0
        if current:
            # 1. Точное совпадение
            for i in range(self._device_combo.count()):
                if current == self._device_combo.itemData(i):
                    selected = i
                    break
            # 2. Сопоставление по ID и названию
            if selected == 0:
                cur_id = current.split(":", 1)[0].strip() if ":" in current else ""
                cur_name = (current.split(":", 1)[1].strip() if ":" in current else current).split("[")[0].strip().lower()
                for i in range(1, self._device_combo.count()):
                    item_str = self._device_combo.itemData(i)
                    item_id = item_str.split(":", 1)[0].strip() if ":" in item_str else ""
                    item_name = (item_str.split(":", 1)[1].strip() if ":" in item_str else item_str).split("[")[0].strip().lower()
                    if cur_id and cur_id == item_id:
                        selected = i
                        break
                    if cur_name and cur_name in item_name:
                        selected = i
                        break

        self._device_combo.setCurrentIndex(selected)
        self._device_combo.blockSignals(False)

    def _on_device_changed(self) -> None:
        self._settings.audio_device = self._device_combo.currentData() or ""
        self.settings_changed.emit()

    def _on_monitor_changed(self, state: int) -> None:
        self._settings.soundpad_monitor = bool(state)
        self.settings_changed.emit()

    def _test_playback(self) -> None:
        for t in self._triggers:
            if t.available:
                self.play_requested.emit(t.path)
                return

    def _on_master_volume(self, value: int) -> None:
        self._settings.master_volume = value / 100.0
        self.settings_changed.emit()

    def _on_voice_volume(self, value: int) -> None:
        self._settings.voice_volume = value / 100.0
        self.settings_changed.emit()

    def _update_restore_btn(self) -> None:
        ignored = getattr(self._settings, "ignored_sounds", [])
        if ignored:
            self._restore_btn.setText(f"🗑 Восстановить ({len(ignored)})")
            self._restore_btn.setVisible(True)
        else:
            self._restore_btn.setVisible(False)

    def _restore_ignored_sounds(self) -> None:
        self._settings.ignored_sounds = []
        self._update_restore_btn()
        self.settings_changed.emit()
        self.refresh_requested.emit()

    def _delete_trigger(self, index: int) -> None:
        if not (0 <= index < len(self._triggers)):
            return
        trigger = self._triggers.pop(index)
        ignored = list(getattr(self._settings, "ignored_sounds", []))
        stem = Path(trigger.path).stem.lower() if trigger.path else trigger.label.lower()
        if stem and stem not in ignored:
            ignored.append(stem)
            self._settings.ignored_sounds = ignored

        self._settings.sound_triggers = self._triggers
        self.settings_changed.emit()
        self.set_triggers(self._triggers)

    def _toggle_in_overlay(self, index: int, in_overlay: bool) -> None:
        if 0 <= index < len(self._triggers):
            self._triggers[index].in_overlay = in_overlay
            self._settings.sound_triggers = self._triggers
            self.settings_changed.emit()

    def set_triggers(self, triggers: list[SoundTrigger]) -> None:
        self._triggers = triggers
        self._update_restore_btn()
        if self._grid is None:
            return
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._play_buttons.clear()

        for i, trigger in enumerate(triggers):
            self._grid.addWidget(self._build_trigger_cell(i, trigger), i // 4, i % 4)

    def _build_trigger_cell(self, index: int, trigger: SoundTrigger) -> QWidget:
        cell = QWidget()
        cell.setStyleSheet(
            f"background-color: {CARD}; border: 1px solid {CARD_BORDER}; border-radius: 8px;"
        )
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Верхняя плашка карточки: название звука и кнопка удаления ✕
        header = QHBoxLayout()
        header.setSpacing(4)

        label = QLabel(trigger.label)
        label.setStyleSheet(f"color: {TEXT}; font-weight: 600; font-size: 12px;")
        label.setToolTip(trigger.path or "Файл не привязан")

        del_btn = QPushButton("✕")
        del_btn.setToolTip("Удалить звук из списка (и оверлея)")
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent; color: {TEXT_DIM}; border: none; font-size: 11px; font-weight: bold; border-radius: 4px;
            }}
            QPushButton:hover {{ background-color: rgba(255, 75, 75, 0.25); color: #ff5555; }}
            """
        )
        del_btn.clicked.connect(lambda _, i=index: self._delete_trigger(i))

        header.addWidget(label, 1)
        header.addWidget(del_btn)
        layout.addLayout(header)

        # Нижняя строка управления
        row = QHBoxLayout()
        row.setSpacing(6)

        play_btn = QPushButton("▶ Play")
        play_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {ACCENT}; color: #0b1018; font-weight: 700;
                border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: #6cb5ff; }}
            QPushButton:disabled {{ background-color: {CARD}; color: {TEXT_DIM}; }}
            """
        )
        if trigger.available:
            play_btn.clicked.connect(lambda _, p=trigger.path: self.play_requested.emit(p))
        else:
            play_btn.setEnabled(False)
        self._play_buttons[index] = play_btn

        file_btn = QPushButton("…")
        file_btn.setToolTip("Выбрать другой файл")
        file_btn.setFixedWidth(26)
        file_btn.clicked.connect(lambda _, i=index: self._pick_file(i))

        overlay_check = QCheckBox("Оверлей")
        overlay_check.setToolTip("Отображать этот звук в плавающем оверлее")
        overlay_check.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        overlay_check.setChecked(getattr(trigger, "in_overlay", True))
        overlay_check.stateChanged.connect(lambda state, i=index: self._toggle_in_overlay(i, bool(state)))

        row.addWidget(play_btn)
        row.addWidget(file_btn)
        row.addWidget(overlay_check)
        layout.addLayout(row)
        return cell

    def _pick_file(self, index: int) -> None:
        if not (0 <= index < len(self._triggers)):
            return
        start_dir = self._settings.sound_dir or str(Path.home())
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выбери звуковой файл", start_dir, "Аудио (*.wav *.mp3 *.ogg *.flac)"
        )
        if file_path:
            self._triggers[index].path = file_path
            self.settings_changed.emit()
            self.set_triggers(self._triggers)

    def _browse_sound_dir(self) -> None:
        start_dir = self._settings.sound_dir or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Папка со звуками", start_dir)
        if directory:
            self._settings.sound_dir = directory
            self._dir_label_value.setText(directory)
            self.settings_changed.emit()
            self.refresh_requested.emit()
