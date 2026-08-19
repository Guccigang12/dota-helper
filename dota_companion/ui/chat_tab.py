
                                                                      
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings
from .theme import ACCENT, GOLD, RADIANT, TEXT, TEXT_DIM
from .widgets import Card, GhostButton, NeonButton, ToggleSwitch, dim_label

PROVIDERS = [
    ("google", "Google (бесплатно)"),
    ("gemini", "Gemini API"),
    ("deepl", "DeepL API"),
    ("libretranslate", "LibreTranslate"),
]

LANGUAGES = [
    ("auto", "Авто"),
    ("ru", "Русский"),
    ("en", "Английский"),
    ("zh", "Китайский"),
    ("es", "Испанский"),
    ("de", "Немецкий"),
    ("pt", "Португальский"),
    ("ko", "Корейский"),
]

OCR_LANGS = [
    ("ch", "Китайский + английский"),
    ("latin", "Испанский / латиница"),
    ("en", "Английский"),
]

SOURCE_COLORS = {
    "OCR": ACCENT,
    "ЛОГ": GOLD,
    "OCR+ЛОГ": RADIANT,
}


class ChatTab(QWidget):
                                             

    ocr_toggle_requested = pyqtSignal(bool)                           
    region_requested = pyqtSignal()                                  
    settings_changed = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

                         
        history_card = Card("💬 История сообщений")
        self._history = QListWidget()
        self._history.setWordWrap(True)
        self._history.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        history_card.content_layout().addWidget(self._history)
        root.addWidget(history_card, 1)

                                   
        quick_card = Card("⚡ Быстрые настройки")
        quick_row = QHBoxLayout()
        quick_row.setSpacing(16)

        self._quick_translation = ToggleSwitch("Quick translation")
        self._quick_translation.setChecked(settings.quick_translation)
        self._quick_translation.toggled.connect(self._on_quick_translation)

        self._overlay_toggle = ToggleSwitch("Over the game")
        self._overlay_toggle.setToolTip("Показывать переведённые сообщения поверх игры")
        self._overlay_toggle.setChecked(settings.overlay_over_game)
        self._overlay_toggle.toggled.connect(self._on_overlay_toggle)

        self._notifications = ToggleSwitch("Notifications")
        self._notifications.setToolTip("Уведомления в трее о новых сообщениях")
        self._notifications.setChecked(settings.notifications_enabled)
        self._notifications.toggled.connect(self._on_notifications)

        quick_row.addWidget(self._quick_translation)
        quick_row.addWidget(self._overlay_toggle)
        quick_row.addWidget(self._notifications)
        quick_row.addStretch(1)

                  
        sliders_col = QVBoxLayout()
        sliders_col.setSpacing(4)

        text_size_row = QHBoxLayout()
        text_size_label = dim_label("Text size:")
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(10, 28)
        self._font_slider.setValue(settings.font_size)
        self._font_slider.setFixedWidth(140)
        self._font_value = dim_label(str(settings.font_size))
        self._font_slider.valueChanged.connect(self._on_font_changed)
        text_size_row.addWidget(text_size_label)
        text_size_row.addWidget(self._font_slider)
        text_size_row.addWidget(self._font_value)
        sliders_col.addLayout(text_size_row)

        showtime_row = QHBoxLayout()
        showtime_label = dim_label("Showtime:")
        self._showtime_slider = QSlider(Qt.Orientation.Horizontal)
        self._showtime_slider.setRange(2, 30)           
        self._showtime_slider.setValue(settings.showtime_ms // 1000)
        self._showtime_slider.setFixedWidth(140)
        self._showtime_value = dim_label(f"{settings.showtime_ms // 1000} с")
        self._showtime_slider.valueChanged.connect(self._on_showtime_changed)
        showtime_row.addWidget(showtime_label)
        showtime_row.addWidget(self._showtime_slider)
        showtime_row.addWidget(self._showtime_value)
        sliders_col.addLayout(showtime_row)

        quick_row.addLayout(sliders_col)
        quick_card.content_layout().addLayout(quick_row)
        root.addWidget(quick_card)

                     
        ocr_card = Card("📷 Распознавание чата")
        ocr_row = QHBoxLayout()
        ocr_row.setSpacing(10)

        self._ocr_toggle = ToggleSwitch("OCR")
        self._ocr_toggle.setChecked(settings.ocr_enabled)
        self._ocr_toggle.toggled.connect(self._on_ocr_toggled)

        self._pause_btn = NeonButton("Пауза (F9)")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause_clicked)

        self._region_btn = GhostButton("⌖ Область чата (Ctrl+Shift+F8)")
        self._region_btn.clicked.connect(self.region_requested.emit)

        self._region_label = dim_label(self._region_text())

        ocr_lang_label = dim_label("Язык OCR:")
        self._ocr_lang_combo = QComboBox()
        for code, title in OCR_LANGS:
            self._ocr_lang_combo.addItem(title, code)
        idx = self._ocr_lang_combo.findData(settings.ocr_lang)
        self._ocr_lang_combo.setCurrentIndex(max(idx, 0))
        self._ocr_lang_combo.currentIndexChanged.connect(self._on_ocr_lang_changed)

        ocr_row.addWidget(self._ocr_toggle)
        ocr_row.addWidget(self._pause_btn)
        ocr_row.addWidget(self._region_btn)
        ocr_row.addWidget(ocr_lang_label)
        ocr_row.addWidget(self._ocr_lang_combo)
        ocr_row.addStretch(1)
        ocr_card.content_layout().addLayout(ocr_row)
        ocr_card.content_layout().addWidget(self._region_label)
        root.addWidget(ocr_card)

                         
        trans_card = Card("🌐 Перевод")
        trans_row = QHBoxLayout()
        trans_row.setSpacing(10)

        provider_label = dim_label("Провайдер:")
        self._provider_combo = QComboBox()
        for code, title in PROVIDERS:
            self._provider_combo.addItem(title, code)
        idx = self._provider_combo.findData(settings.translator_provider)
        self._provider_combo.setCurrentIndex(max(idx, 0))
        self._provider_combo.currentIndexChanged.connect(self._on_translate_settings_changed)

        self._deepl_key = QLineEdit(settings.deepl_api_key)
        self._deepl_key.setPlaceholderText("DeepL API-ключ")
        self._deepl_key.setMaximumWidth(180)
        self._deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._deepl_key.editingFinished.connect(self._on_translate_settings_changed)
        self._deepl_key.setVisible(settings.translator_provider == "deepl")

        self._gemini_key = QLineEdit(settings.gemini_api_key)
        self._gemini_key.setPlaceholderText("Gemini API-ключ")
        self._gemini_key.setMaximumWidth(200)
        self._gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_key.editingFinished.connect(self._on_translate_settings_changed)
        self._gemini_key.setVisible(settings.translator_provider == "gemini")

        self._gemini_model = QLineEdit(settings.gemini_model)
        self._gemini_model.setPlaceholderText("модель (gemini-3.5-flash-lite)")
        self._gemini_model.setMaximumWidth(200)
        self._gemini_model.editingFinished.connect(self._on_translate_settings_changed)
        self._gemini_model.setVisible(settings.translator_provider == "gemini")

        source_label = dim_label("Из:")
        self._source_combo = QComboBox()
        for code, title in LANGUAGES:
            self._source_combo.addItem(title, code)
        idx = self._source_combo.findData(settings.source_lang)
        self._source_combo.setCurrentIndex(max(idx, 0))
        self._source_combo.currentIndexChanged.connect(self._on_translate_settings_changed)

        target_label = dim_label("В:")
        self._target_combo = QComboBox()
        for code, title in LANGUAGES:
            if code != "auto":
                self._target_combo.addItem(title, code)
        idx = self._target_combo.findData(settings.target_lang)
        self._target_combo.setCurrentIndex(max(idx, 1))
        self._target_combo.currentIndexChanged.connect(self._on_translate_settings_changed)

        trans_row.addWidget(provider_label)
        trans_row.addWidget(self._provider_combo)
        trans_row.addWidget(self._deepl_key)
        trans_row.addWidget(self._gemini_key)
        trans_row.addWidget(self._gemini_model)
        trans_row.addWidget(source_label)
        trans_row.addWidget(self._source_combo)
        trans_row.addWidget(target_label)
        trans_row.addWidget(self._target_combo)
        trans_row.addStretch(1)
        trans_card.content_layout().addLayout(trans_row)
        root.addWidget(trans_card)

                                                                          

    def _region_text(self) -> str:
        region = self._settings.chat_region
        if region.valid:
            return (
                f"Область чата: {region.width}×{region.height} "
                f"на мониторе {region.monitor} (x={region.x}, y={region.y})"
            )
        return "Область чата не выбрана — нажми Ctrl+Shift+F8"

                                                                          

    def _on_ocr_toggled(self, checked: bool) -> None:
        self._settings.ocr_enabled = checked
        self._pause_btn.setEnabled(checked)
        self.ocr_toggle_requested.emit(checked)
        self.settings_changed.emit()

    def _on_pause_clicked(self) -> None:
        self._settings.ocr_paused = not self._settings.ocr_paused
        self._sync_pause_button()
        self.settings_changed.emit()

    def _sync_pause_button(self) -> None:
        paused = self._settings.ocr_paused
        self._pause_btn.setText("Продолжить (F9)" if paused else "Пауза (F9)")

    def toggle_pause(self) -> None:
                                      
        self._settings.ocr_paused = not self._settings.ocr_paused
        self._sync_pause_button()
        self.settings_changed.emit()

    def set_ocr_active(self, active: bool) -> None:
        self._ocr_toggle.blockSignals(True)
        self._ocr_toggle.setChecked(active)
        self._ocr_toggle.blockSignals(False)
        self._pause_btn.setEnabled(active)
        self._settings.ocr_enabled = active

    def refresh_region_label(self) -> None:
        self._region_label.setText(self._region_text())

    def _on_ocr_lang_changed(self) -> None:
        self._settings.ocr_lang = self._ocr_lang_combo.currentData()
        self.settings_changed.emit()

                                                                          

    def _on_quick_translation(self, checked: bool) -> None:
        self._settings.quick_translation = checked
        self.settings_changed.emit()

    def _on_overlay_toggle(self, checked: bool) -> None:
        self._settings.overlay_over_game = checked
        self.settings_changed.emit()

    def _on_notifications(self, checked: bool) -> None:
        self._settings.notifications_enabled = checked
        self.settings_changed.emit()

    def _on_font_changed(self, value: int) -> None:
        self._settings.font_size = value
        self._font_value.setText(str(value))
        self.settings_changed.emit()

    def _on_showtime_changed(self, seconds: int) -> None:
        self._settings.showtime_ms = seconds * 1000
        self._showtime_value.setText(f"{seconds} с")
        self.settings_changed.emit()

    def _on_translate_settings_changed(self) -> None:
        self._settings.translator_provider = self._provider_combo.currentData()
        self._settings.deepl_api_key = self._deepl_key.text().strip()
        self._settings.gemini_api_key = self._gemini_key.text().strip()
        self._settings.gemini_model = self._gemini_model.text().strip() or "gemini-3.5-flash-lite"
        self._settings.source_lang = self._source_combo.currentData()
        self._settings.target_lang = self._target_combo.currentData()
        is_gemini = self._settings.translator_provider == "gemini"
        self._deepl_key.setVisible(self._settings.translator_provider == "deepl")
        self._gemini_key.setVisible(is_gemini)
        self._gemini_model.setVisible(is_gemini)
        self.settings_changed.emit()

                                                                          

    def add_message(self, source: str, original: str, translated: str | None = None) -> None:
                                            
        color = SOURCE_COLORS.get(source, ACCENT)
        prefix = f"[{source}] "
        text = f"{original}"
        if translated:
            text += f"  →  {translated}"

        item = QListWidgetItem()
        self._history.addItem(item)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            f"color: {TEXT}; padding: 4px 6px; "
            f"border-left: 3px solid {color}; background: transparent;"
        )
        item.setSizeHint(label.sizeHint().adjusted(4, 8, 4, 8))
        self._history.setItemWidget(item, label)

                         
        self._history.scrollToBottom()

                                   
        while self._history.count() > 300:
            self._history.takeItem(0)

    def clear_history(self) -> None:
        self._history.clear()
