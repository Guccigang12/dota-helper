from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
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
from .theme import ACCENT, GOLD, RADIANT, TEXT
from .widgets import Card, GhostButton, NeonButton, ToggleSwitch, dim_label

PROVIDERS = [
    ("ollama", "Ollama / Локальная LLM (без ограничений)"),
    ("groq", "Groq API (бесплатно, сверхбыстро ~0.2с)"),
    ("openrouter", "OpenRouter API (бесплатные модели :free)"),
    ("deepseek", "DeepSeek API"),
    ("gemini", "Gemini 2.0 (Google AI Studio)"),
    ("google", "Google Translate (со словарем сленга)"),
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
    ("all", "Авто (все языки: RU, EN, ZH)"),
    ("ch", "Китайский + английский"),
    ("latin", "Испанский / латиница"),
    ("en", "Английский"),
    ("ru", "Русский"),
]

SOURCE_COLORS = {
    "OCR": ACCENT,
    "Выделение": ACCENT,
    "Выделение→перевод": ACCENT,
    "Vision→перевод": RADIANT,
    "ЛОГ": GOLD,
    "OCR+ЛОГ": RADIANT,
}


class ChatTab(QWidget):
    translate_requested = pyqtSignal()
    region_requested = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # 1. Message History
        history_card = Card("💬 История сообщений чата Dota 2")
        self._history = QListWidget()
        self._history.setWordWrap(True)
        self._history.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        history_card.content_layout().addWidget(self._history)
        root.addWidget(history_card, 1)

        # 2. Quick Settings
        quick_card = Card("⚡ Быстрые настройки оверлея")
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

        self._slang_toggle = ToggleSwitch("Dota 2 сленг-фильтр")
        self._slang_toggle.setToolTip("Использовать специальный словарь терминов, сокращений и игрового сленга")
        self._slang_toggle.setChecked(settings.slang_enhancement)
        self._slang_toggle.toggled.connect(self._on_slang_toggled)

        self._vision_toggle = ToggleSwitch("Vision по фото")
        self._vision_toggle.setToolTip("Прямой мультимодальный AI-перевод скриншота без ошибок OCR")
        self._vision_toggle.setChecked(settings.use_vision_for_crop)
        self._vision_toggle.toggled.connect(self._on_vision_toggled)

        quick_row.addWidget(self._quick_translation)
        quick_row.addWidget(self._overlay_toggle)
        quick_row.addWidget(self._notifications)
        quick_row.addWidget(self._slang_toggle)
        quick_row.addWidget(self._vision_toggle)
        quick_row.addStretch(1)

        sliders_col = QVBoxLayout()
        sliders_col.setSpacing(4)

        text_size_row = QHBoxLayout()
        text_size_label = dim_label("Размер текста:")
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(10, 28)
        self._font_slider.setValue(settings.font_size)
        self._font_slider.setFixedWidth(120)
        self._font_value = dim_label(str(settings.font_size))
        self._font_slider.valueChanged.connect(self._on_font_changed)
        text_size_row.addWidget(text_size_label)
        text_size_row.addWidget(self._font_slider)
        text_size_row.addWidget(self._font_value)
        sliders_col.addLayout(text_size_row)

        showtime_row = QHBoxLayout()
        showtime_label = dim_label("Время показа:")
        self._showtime_slider = QSlider(Qt.Orientation.Horizontal)
        self._showtime_slider.setRange(2, 30)
        self._showtime_slider.setValue(settings.showtime_ms // 1000)
        self._showtime_slider.setFixedWidth(120)
        self._showtime_value = dim_label(f"{settings.showtime_ms // 1000} с")
        self._showtime_slider.valueChanged.connect(self._on_showtime_changed)
        showtime_row.addWidget(showtime_label)
        showtime_row.addWidget(self._showtime_slider)
        showtime_row.addWidget(self._showtime_value)
        sliders_col.addLayout(showtime_row)

        quick_row.addLayout(sliders_col)
        quick_card.content_layout().addLayout(quick_row)
        root.addWidget(quick_card)

        # 3. Quick Chat Translation & Screen Area Selection
        ocr_card = Card("✂ Перевод чата по требованию (F8 / Ctrl+Shift+F8)")
        ocr_row = QHBoxLayout()
        ocr_row.setSpacing(12)

        self._translate_btn = NeonButton("⚡ Перевести чат (F8)")
        self._translate_btn.setToolTip("Мгновенно захватить и перевести сохранённую область чата (также работает по клавишам F8 и Ctrl+F8)")
        self._translate_btn.clicked.connect(self.translate_requested.emit)

        self._region_btn = GhostButton("✂ Задать область чата (Ctrl+Shift+F8)")
        self._region_btn.setToolTip("Выделить прямоугольную область чата мышкой на экране")
        self._region_btn.clicked.connect(self.region_requested.emit)

        ocr_lang_label = dim_label("Язык OCR:")
        self._ocr_lang_combo = QComboBox()
        for code, title in OCR_LANGS:
            self._ocr_lang_combo.addItem(title, code)
        idx = self._ocr_lang_combo.findData(settings.ocr_lang)
        self._ocr_lang_combo.setCurrentIndex(max(idx, 0))
        self._ocr_lang_combo.currentIndexChanged.connect(self._on_ocr_lang_changed)

        ocr_row.addWidget(self._translate_btn)
        ocr_row.addWidget(self._region_btn)
        ocr_row.addSpacing(16)
        ocr_row.addWidget(ocr_lang_label)
        ocr_row.addWidget(self._ocr_lang_combo)
        ocr_row.addStretch(1)
        ocr_card.content_layout().addLayout(ocr_row)

        self._region_label = dim_label(self._region_text())
        ocr_card.content_layout().addWidget(self._region_label)
        root.addWidget(ocr_card)

        # 4. Translation Provider Configuration
        trans_card = Card("🌐 Настройки сервиса перевода (Dota 2 Slang & LLM)")
        trans_layout = QVBoxLayout()
        trans_layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(10)

        provider_label = dim_label("Провайдер:")
        self._provider_combo = QComboBox()
        for code, title in PROVIDERS:
            self._provider_combo.addItem(title, code)
        idx = self._provider_combo.findData(settings.translator_provider)
        self._provider_combo.setCurrentIndex(max(idx, 0))
        self._provider_combo.currentIndexChanged.connect(self._on_provider_selection_changed)

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

        row1.addWidget(provider_label)
        row1.addWidget(self._provider_combo, 1)
        row1.addWidget(source_label)
        row1.addWidget(self._source_combo)
        row1.addWidget(target_label)
        row1.addWidget(self._target_combo)
        trans_layout.addLayout(row1)

        # Dynamic Provider Fields Row
        self._params_row = QHBoxLayout()
        self._params_row.setSpacing(8)

        # Base URL input (for Ollama / Custom / LibreTranslate)
        self._url_label = dim_label("URL API:")
        self._url_input = QLineEdit(settings.custom_api_url)
        self._url_input.setPlaceholderText("http://localhost:11434/v1")
        self._url_input.editingFinished.connect(self._on_translate_settings_changed)

        # API Key input
        self._key_label = dim_label("API-ключ:")
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("API-ключ")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.editingFinished.connect(self._on_translate_settings_changed)

        # Model input
        self._model_label = dim_label("Модель:")
        self._model_input = QLineEdit()
        self._model_input.setPlaceholderText("Название модели")
        self._model_input.editingFinished.connect(self._on_translate_settings_changed)

        self._params_row.addWidget(self._url_label)
        self._params_row.addWidget(self._url_input)
        self._params_row.addWidget(self._key_label)
        self._params_row.addWidget(self._key_input)
        self._params_row.addWidget(self._model_label)
        self._params_row.addWidget(self._model_input)
        trans_layout.addLayout(self._params_row)

        self._provider_hint = dim_label("")
        trans_layout.addWidget(self._provider_hint)

        trans_card.content_layout().addLayout(trans_layout)
        root.addWidget(trans_card)

        self._update_fields_for_provider(settings.translator_provider)

    def _region_text(self) -> str:
        region = self._settings.chat_region
        if region.valid:
            return (
                f"Область чата: {region.width}×{region.height} "
                f"на мониторе {region.monitor} (x={region.x}, y={region.y})"
            )
        return "Область чата не выбрана — нажми Ctrl+Shift+F8 для выделения"

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

    def _on_slang_toggled(self, checked: bool) -> None:
        self._settings.slang_enhancement = checked
        self.settings_changed.emit()

    def _on_vision_toggled(self, checked: bool) -> None:
        self._settings.use_vision_for_crop = checked
        self.settings_changed.emit()

    def _on_font_changed(self, value: int) -> None:
        self._settings.font_size = value
        self._font_value.setText(str(value))
        self.settings_changed.emit()

    def _on_showtime_changed(self, seconds: int) -> None:
        self._settings.showtime_ms = seconds * 1000
        self._showtime_value.setText(f"{seconds} с")
        self.settings_changed.emit()

    def _on_provider_selection_changed(self) -> None:
        provider = self._provider_combo.currentData()
        self._settings.translator_provider = provider
        self._update_fields_for_provider(provider)
        self._on_translate_settings_changed()

    def _update_fields_for_provider(self, provider: str) -> None:
        s = self._settings

        self._url_label.setVisible(False)
        self._url_input.setVisible(False)
        self._key_label.setVisible(False)
        self._key_input.setVisible(False)
        self._model_label.setVisible(False)
        self._model_input.setVisible(False)
        self._provider_hint.setText("")

        if provider == "ollama":
            self._url_label.setVisible(True)
            self._url_input.setVisible(True)
            self._url_input.setText(s.custom_api_url or "http://localhost:11434/v1")
            self._model_label.setVisible(True)
            self._model_input.setVisible(True)
            self._model_input.setText(s.custom_model or "qwen2.5:7b")
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.custom_api_key)
            self._key_input.setPlaceholderText("Не требуется для Ollama")
            self._provider_hint.setText(
                "💡 100% бесплатно и без цензуры. Запусти Ollama (`ollama run qwen2.5:7b` или `ollama run llama3.1:8b`)."
            )

        elif provider == "groq":
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.groq_api_key)
            self._key_input.setPlaceholderText("gsk_...")
            self._model_label.setVisible(True)
            self._model_input.setVisible(True)
            self._model_input.setText(s.groq_model or "qwen/qwen3.8-27b")
            self._provider_hint.setText(
                "⚡ Сверхбыстрый перевод (~0.2с). Бесплатный ключ на console.groq.com (14 400 запросов/день)."
            )

        elif provider == "openrouter":
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.openrouter_api_key)
            self._key_input.setPlaceholderText("sk-or-v1-...")
            self._model_label.setVisible(True)
            self._model_input.setVisible(True)
            self._model_input.setText(s.openrouter_model or "meta-llama/llama-3.3-70b-instruct:free")
            self._provider_hint.setText(
                "🌐 Доступ к бесплатным моделям (с суффиксом :free) на openrouter.ai."
            )

        elif provider == "deepseek":
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.deepseek_api_key)
            self._key_input.setPlaceholderText("sk-...")
            self._model_label.setVisible(True)
            self._model_input.setVisible(True)
            self._model_input.setText(s.deepseek_model or "deepseek-chat")
            self._provider_hint.setText("🤖 Официальный DeepSeek API (deepseek-chat / deepseek-reasoner).")

        elif provider == "gemini":
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.gemini_api_key)
            self._key_input.setPlaceholderText("AIzaSy...")
            self._model_label.setVisible(True)
            self._model_input.setVisible(True)
            self._model_input.setText(s.gemini_model or "gemini-2.0-flash")
            self._provider_hint.setText(
                "✨ Бесплатный ключ на aistudio.google.com. Поддерживает прямой Vision-перевод по фото."
            )

        elif provider == "deepl":
            self._key_label.setVisible(True)
            self._key_input.setVisible(True)
            self._key_input.setText(s.deepl_api_key)
            self._key_input.setPlaceholderText("DeepL API-ключ")
            self._provider_hint.setText("💼 Требуется ключ DeepL Free или Pro.")

        elif provider == "libretranslate":
            self._url_label.setVisible(True)
            self._url_input.setVisible(True)
            self._url_input.setText(s.libretranslate_url or "https://libretranslate.com/translate")
            self._provider_hint.setText("🌐 Сервер LibreTranslate.")

        elif provider == "google":
            self._provider_hint.setText(
                "🚀 Бесплатный встроенный Google Translate + локальный словарь сленга Dota 2."
            )

    def _on_translate_settings_changed(self) -> None:
        provider = self._provider_combo.currentData()
        self._settings.translator_provider = provider
        self._settings.source_lang = self._source_combo.currentData()
        self._settings.target_lang = self._target_combo.currentData()

        if provider == "ollama":
            self._settings.custom_api_url = self._url_input.text().strip() or "http://localhost:11434/v1"
            self._settings.custom_model = self._model_input.text().strip() or "qwen2.5:7b"
            self._settings.custom_api_key = self._key_input.text().strip()
        elif provider == "groq":
            self._settings.groq_api_key = self._key_input.text().strip()
            self._settings.groq_model = self._model_input.text().strip() or "qwen/qwen3.8-27b"
        elif provider == "openrouter":
            self._settings.openrouter_api_key = self._key_input.text().strip()
            self._settings.openrouter_model = self._model_input.text().strip() or "meta-llama/llama-3.3-70b-instruct:free"
        elif provider == "deepseek":
            self._settings.deepseek_api_key = self._key_input.text().strip()
            self._settings.deepseek_model = self._model_input.text().strip() or "deepseek-chat"
        elif provider == "gemini":
            self._settings.gemini_api_key = self._key_input.text().strip()
            self._settings.gemini_model = self._model_input.text().strip() or "gemini-2.0-flash"
        elif provider == "deepl":
            self._settings.deepl_api_key = self._key_input.text().strip()
        elif provider == "libretranslate":
            self._settings.libretranslate_url = self._url_input.text().strip() or "https://libretranslate.com/translate"

        self.settings_changed.emit()

    def add_message(self, source: str, original: str, translated: str | None = None) -> None:
        color = SOURCE_COLORS.get(source, ACCENT)
        text = f"{original}"
        if translated:
            text += f"  →  {translated}"

        item = QListWidgetItem()
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            f"color: {TEXT}; padding: 4px 6px; "
            f"border-left: 3px solid {color}; background: transparent;"
        )
        hint = label.sizeHint()
        view_w = max(self._history.viewport().width() - 24, 100)
        needed_h = label.heightForWidth(view_w)
        h = max(needed_h + 10, hint.height() + 10, 28)
        item.setSizeHint(QSize(view_w, h))

        self._history.addItem(item)
        self._history.setItemWidget(item, label)
        self._history.scrollToBottom()

        while self._history.count() > 300:
            self._history.takeItem(0)

    def clear_history(self) -> None:
        self._history.clear()
