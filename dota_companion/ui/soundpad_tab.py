
                                                      
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings, SoundTrigger
from .theme import ACCENT, CARD, CARD_BORDER, TEXT, TEXT_DIM
from .widgets import Card, GhostButton, NeonButton, dim_label


class SoundPadTab(QWidget):
                            

    play_requested = pyqtSignal(str)                        
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
        self._device_combo.setMinimumWidth(280)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._refresh_devices_btn = GhostButton("⟳")
        self._refresh_devices_btn.setToolTip("Обновить список устройств")
        self._refresh_devices_btn.clicked.connect(self.reload_devices)
        audio_row.addWidget(device_label)
        audio_row.addWidget(self._device_combo)
        audio_row.addWidget(self._refresh_devices_btn)

        master_label = dim_label("Мастер:")
        self._master_slider = QSlider(Qt.Orientation.Horizontal)
        self._master_slider.setRange(0, 100)
        self._master_slider.setValue(int(round(settings.master_volume * 100)))
        self._master_slider.setFixedWidth(130)
        self._master_slider.valueChanged.connect(self._on_master_volume)
        audio_row.addWidget(master_label)
        audio_row.addWidget(self._master_slider)

        voice_label = dim_label("Микрофон:")
        self._voice_slider = QSlider(Qt.Orientation.Horizontal)
        self._voice_slider.setRange(0, 100)
        self._voice_slider.setValue(int(round(settings.voice_volume * 100)))
        self._voice_slider.setFixedWidth(130)
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

        refresh_btn = NeonButton("⟳ Refresh")
        refresh_btn.setToolTip("Перезагрузить звуковые файлы")
        refresh_btn.clicked.connect(self.refresh_requested.emit)

        overlay_btn = GhostButton("Плавающий оверлей")
        overlay_btn.clicked.connect(self.show_overlay_requested.emit)

        triggers_toolbar.addWidget(dir_label)
        triggers_toolbar.addWidget(self._dir_label_value)
        triggers_toolbar.addWidget(browse_btn)
        triggers_toolbar.addStretch(1)
        triggers_toolbar.addWidget(overlay_btn)
        triggers_toolbar.addWidget(refresh_btn)
        triggers_card.content_layout().addLayout(triggers_toolbar)

        self._grid = QGridLayout()
        self._grid.setSpacing(8)
        triggers_card.content_layout().addLayout(self._grid)
        root.addWidget(triggers_card)

        hint = dim_label(
            "Положи звуки в папку (wav/mp3/ogg/flac). Файл <label>.wav автоматически "
            "подхватится к триггеру с тем же именем; для игры в голос выбери "
            "виртуальный кабель (VB-Audio Virtual Cable)."
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

                                                             
        selected = 0
        if current:
            for i in range(self._device_combo.count()):
                if current in self._device_combo.itemData(i):
                    selected = i
                    break
        self._device_combo.setCurrentIndex(selected)
        self._device_combo.blockSignals(False)

    def _on_device_changed(self) -> None:
        self._settings.audio_device = self._device_combo.currentData() or ""
        self.settings_changed.emit()

    def _on_master_volume(self, value: int) -> None:
        self._settings.master_volume = value / 100.0
        self.settings_changed.emit()

    def _on_voice_volume(self, value: int) -> None:
        self._settings.voice_volume = value / 100.0
        self.settings_changed.emit()

                                                                          

    def set_triggers(self, triggers: list[SoundTrigger]) -> None:
        self._triggers = triggers
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
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        label = QLabel(trigger.label)
        label.setStyleSheet(f"color: {TEXT}; font-weight: 600;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setSpacing(6)

        play_btn = QPushButton("▶ Play")
        play_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {ACCENT}; color: #0b1018; font-weight: 700;
                border: none; border-radius: 6px; padding: 4px 10px;
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
        file_btn.setToolTip("Выбрать файл для триггера")
        file_btn.setFixedWidth(30)
        file_btn.clicked.connect(lambda _, i=index: self._pick_file(i))

        row.addWidget(play_btn)
        row.addWidget(file_btn)
        layout.addWidget(label)
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
