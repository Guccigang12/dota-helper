
   
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings
from .theme import ACCENT, TEXT_DIM
from .widgets import Card, GhostButton, NeonButton, dim_label

LANGUAGES = [
    ("auto", "Авто"),
    ("ru", "Русский"),
    ("en", "Английский"),
    ("zh", "Китайский"),
    ("es", "Испанский"),
    ("de", "Немецкий"),
    ("fr", "Французский"),
    ("pt", "Португальский"),
    ("ko", "Корейский"),
    ("ja", "Японский"),
    ("uk", "Украинский"),
]

AUTO_DELAY_MS = 900


class TranslatorTab(QWidget):
                                             

    translate_requested = pyqtSignal(str, str, str)                                 
    settings_changed = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

                                   
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(dim_label("Из:"))
        self._source_combo = QComboBox()
        for code, title in LANGUAGES:
            self._source_combo.addItem(title, code)
        idx = self._source_combo.findData(settings.manual_source_lang)
        self._source_combo.setCurrentIndex(max(idx, 0))
        self._source_combo.currentIndexChanged.connect(self._on_langs_changed)

        arrow = QLabel("⇄")
        arrow.setStyleSheet(f"color: {ACCENT}; font-size: 16px; font-weight: 700;")

        toolbar.addWidget(dim_label("В:"))
        self._target_combo = QComboBox()
        for code, title in LANGUAGES:
            if code != "auto":
                self._target_combo.addItem(title, code)
        idx = self._target_combo.findData(settings.manual_target_lang)
        self._target_combo.setCurrentIndex(max(idx, 1))
        self._target_combo.currentIndexChanged.connect(self._on_langs_changed)

        toolbar.addSpacing(12)
        self._auto_check = QCheckBox("Авто-перевод")
        self._auto_check.setToolTip("Переводить автоматически при вводе")
        self._auto_check.setChecked(True)

        self._translate_btn = NeonButton("Перевести (Ctrl+Enter)")
        self._translate_btn.clicked.connect(self.request_translation)

        toolbar.addWidget(self._source_combo)
        toolbar.addWidget(arrow)
        toolbar.addWidget(self._target_combo)
        toolbar.addWidget(self._auto_check)
        toolbar.addStretch(1)
        toolbar.addWidget(self._translate_btn)
        root.addLayout(toolbar)

                            
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        input_card = Card("✍️ Твой текст")
        self._input_edit = QPlainTextEdit()
        self._input_edit.setPlaceholderText("Введи текст — перевод появится справа…")
        self._input_edit.setMinimumHeight(300)
        input_card.content_layout().addWidget(self._input_edit)
        splitter.addWidget(input_card)

        output_card = Card("🌐 Перевод")
        self._output_edit = QPlainTextEdit()
        self._output_edit.setReadOnly(True)
        self._output_edit.setPlaceholderText("Перевод появится здесь…")
        output_card.content_layout().addWidget(self._output_edit)
        splitter.addWidget(output_card)

        splitter.setSizes([500, 500])
        root.addWidget(splitter, 1)

                               
        bottom = QHBoxLayout()
        self._copy_btn = GhostButton("📋 Копировать перевод")
        self._copy_btn.clicked.connect(self._copy_result)
        self._provider_label = dim_label("")
        bottom.addWidget(self._copy_btn)
        bottom.addStretch(1)
        bottom.addWidget(self._provider_label)
        root.addLayout(bottom)

                                          
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(AUTO_DELAY_MS)
        self._auto_timer.timeout.connect(self.request_translation)
        self._input_edit.textChanged.connect(self._on_text_changed)

                                         
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._input_edit)
        shortcut.activated.connect(self.request_translation)

        self.set_provider_info("gemini", self._settings.gemini_model)

                                                                          

    def _on_text_changed(self) -> None:
        if self._auto_check.isChecked():
            self._auto_timer.start()

    def _on_langs_changed(self) -> None:
        self._settings.manual_source_lang = self._source_combo.currentData()
        self._settings.manual_target_lang = self._target_combo.currentData()
        self.settings_changed.emit()

                                                                          

    def request_translation(self) -> None:
        text = self._input_edit.toPlainText().strip()
        if not text or self._busy:
            return
        source = self._source_combo.currentData()
        target = self._target_combo.currentData()
        self.set_busy(True)
        self.translate_requested.emit(text, source, target)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._translate_btn.setEnabled(not busy)
        if busy:
            self._output_edit.setPlaceholderText("Перевод…")

    def show_result(self, text: str) -> None:
        self.set_busy(False)
        self._output_edit.setPlainText(text)

    def show_error(self, message: str) -> None:
        self.set_busy(False)
        self._output_edit.setPlainText(f"⚠ Ошибка перевода: {message}")

    def set_provider_info(self, provider: str, model: str = "") -> None:
        names = {
            "gemini": "Gemini API",
            "google": "Google",
            "deepl": "DeepL",
            "libretranslate": "LibreTranslate",
        }
        text = f"Провайдер: {names.get(provider, provider)}"
        if provider == "gemini" and model:
            text += f" · модель {model}"
        self._provider_label.setText(text)

    def _copy_result(self) -> None:
        text = self._output_edit.toPlainText()
        if text:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self._provider_label.setText("Скопировано ✓")
