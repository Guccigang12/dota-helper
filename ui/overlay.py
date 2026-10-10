"""
DotaHelper Overlay Window.
Modern, transparent, frameless, always-on-top overlay for Dota 2.
Displays 9 lobby players' inventory values ($) and real-time chat translations with copy buttons.
"""

from __future__ import annotations

import json
import logging
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.steam_coplay import CoplayPlayer
from core.free_evaluator import InventoryValueResult
from core.chat_translator import TranslatedMessage

logger = logging.getLogger("DotaHelper.Overlay")


class SignalBridge(QObject):
    """Thread-safe signal bridge between background workers and Qt main GUI thread."""

    gsi_state_changed = pyqtSignal(str, str)  # (state, match_id)
    steam_status_changed = pyqtSignal(bool, str)  # (is_ready, local_steam_id)
    lobby_detected = pyqtSignal(list)  # list[CoplayPlayer]
    player_evaluated = pyqtSignal(object)  # InventoryValueResult
    chat_translated = pyqtSignal(list)  # list[TranslatedMessage]
    chat_status_changed = pyqtSignal(str)
    chat_error = pyqtSignal(str)
    open_roi_selector = pyqtSignal()
    toggle_visibility = pyqtSignal()


# ==========================================
# ROI Screen Area Selector (Ctrl+Shift+F8)
# ==========================================

class ROISelectorOverlay(QWidget):
    """Fullscreen semi-transparent overlay to drag-select chat capture area."""

    roi_selected = pyqtSignal(int, int, int, int)  # (left, top, right, bottom)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.start_pos: Optional[QPoint] = None
        self.current_pos: Optional[QPoint] = None
        self.is_selecting = False

        # Match entire desktop geometry across all screens
        screens = QApplication.screens()
        total_rect = QRect()
        for s in screens:
            total_rect = total_rect.united(s.geometry())
        self.setGeometry(total_rect)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.globalPosition().toPoint()
            self.current_pos = self.start_pos
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.is_selecting:
            self.current_pos = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.is_selecting = False
            if self.start_pos and self.current_pos:
                x1 = min(self.start_pos.x(), self.current_pos.x())
                y1 = min(self.start_pos.y(), self.current_pos.y())
                x2 = max(self.start_pos.x(), self.current_pos.x())
                y2 = max(self.start_pos.y(), self.current_pos.y())

                if (x2 - x1) >= 20 and (y2 - y1) >= 20:
                    self.roi_selected.emit(x1, y1, x2, y2)

            self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark translucent overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))

        # Draw helper text
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            "\n\n📐 Выделите область внутриигрового чата Dota 2 мышью. Нажмите ESC для отмены.",
        )

        # Draw selection rectangle
        if self.is_selecting and self.start_pos and self.current_pos:
            r = QRect(self.start_pos, self.current_pos).normalized()
            painter.setPen(QPen(QColor("#00d2ff"), 2))
            painter.setBrush(QBrush(QColor(0, 210, 255, 40)))
            painter.drawRect(r)

            # Draw size text
            size_str = f"{r.width()} x {r.height()}"
            painter.setPen(QColor("#00d2ff"))
            painter.setFont(QFont("Consolas", 10))
            painter.drawText(r.bottomRight() + QPoint(8, 16), size_str)


# ==========================================
# Settings Dialog
# ==========================================

class SettingsDialog(QDialog):
    """Dialog to adjust API keys, tokens, and preferences."""

    def __init__(self, config_path: Path, on_saved_callback=None, parent=None) -> None:
        super().__init__(parent)
        self.config_path = config_path
        self.on_saved_callback = on_saved_callback

        self.setWindowTitle("Настройки Dota Helper")
        self.setFixedWidth(460)
        self.setStyleSheet("""
            QDialog {
                background-color: #12141c;
                color: #e0e4ef;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                color: #a0a6b8;
                font-size: 12px;
                font-weight: 500;
            }
            QLineEdit {
                background-color: #1c202d;
                border: 1px solid #2e3447;
                border-radius: 6px;
                color: #ffffff;
                padding: 7px 10px;
                font-size: 13px;
                selection-background-color: #00d2ff;
            }
            QLineEdit:focus {
                border: 1px solid #00d2ff;
            }
            QPushButton {
                background-color: #0084ff;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #00a2ff;
            }
        """)

        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("⚙️ Конфигурация модулей")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title)

        # Free Pricing & Inventory Checker Settings
        layout.addWidget(QLabel("Валюта оценки инвентарей (Market Dota 2):"))
        self.combo_currency = QComboBox()
        self.combo_currency.addItems(["USD", "RUB"])
        self.combo_currency.setStyleSheet("""
            QComboBox {
                background-color: #1c202d;
                border: 1px solid #2e3447;
                border-radius: 6px;
                color: #ffffff;
                padding: 6px 10px;
                font-size: 13px;
            }
            QComboBox QAbstractItemView {
                background-color: #1c202d;
                color: #ffffff;
                selection-background-color: #0084ff;
            }
        """)
        layout.addWidget(self.combo_currency)

        layout.addWidget(QLabel("Мин. цена предмета (вещи дешевле не учитываются):"))
        self.input_min_price = QLineEdit()
        self.input_min_price.setPlaceholderText("0.5 (для USD) или 45.0 (для RUB)")
        layout.addWidget(self.input_min_price)

        layout.addWidget(QLabel("Прокси / Cloudflare Worker (опционально):"))
        self.input_proxy = QLineEdit()
        self.input_proxy.setPlaceholderText("http://127.0.0.1:10808 или https://worker.workers.dev (опционально)")
        layout.addWidget(self.input_proxy)

        self.check_auto_proxies = QCheckBox("Автоматически использовать бесплатные прокси при блокировке")
        self.check_auto_proxies.setStyleSheet("color: #b0b8cb; font-size: 12px;")
        layout.addWidget(self.check_auto_proxies)

        # Groq AI Key & Model
        layout.addWidget(QLabel("⚡ Groq API Key (AI-переводчик чата ~0.2с):"))
        self.input_groq = QLineEdit()
        self.input_groq.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_groq.setPlaceholderText("gsk_...")
        layout.addWidget(self.input_groq)

        layout.addWidget(QLabel("Модель Groq (по умолчанию: qwen/qwen3.8-27b):"))
        self.input_groq_model = QLineEdit()
        self.input_groq_model.setPlaceholderText("qwen/qwen3.8-27b")
        layout.addWidget(self.input_groq_model)

        # OpenRouter Key
        layout.addWidget(QLabel("OpenRouter API Key (опционально, для Vision):"))
        self.input_openrouter = QLineEdit()
        self.input_openrouter.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_openrouter.setPlaceholderText("sk-or-v1-...")
        layout.addWidget(self.input_openrouter)

        # OpenRouter Model
        layout.addWidget(QLabel("Модель OpenRouter (опционально):"))
        self.input_model = QLineEdit()
        self.input_model.setPlaceholderText("google/gemini-2.5-flash")
        layout.addWidget(self.input_model)

        # GSI Token
        layout.addWidget(QLabel("GSI Auth Token:"))
        self.input_gsi = QLineEdit()
        layout.addWidget(self.input_gsi)

        # Save Button
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save_values)
        btn_box.addWidget(btn_save)

        layout.addLayout(btn_box)

    def _load_values(self) -> None:
        if not self.config_path.exists():
            return
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            checker_cfg = cfg.get("inventory_checker", cfg.get("pricing", {}))
            curr = checker_cfg.get("currency", "USD").upper()
            idx = self.combo_currency.findText(curr)
            if idx >= 0:
                self.combo_currency.setCurrentIndex(idx)
            self.input_min_price.setText(str(checker_cfg.get("min_item_price", 0.5)))
            proxy_val = (
                checker_cfg.get("cloudflare_worker_url", "")
                or checker_cfg.get("proxy_url", "")
                or checker_cfg.get("proxy", "")
                or checker_cfg.get("reverse_proxy_url", "")
            )
            self.input_proxy.setText(proxy_val)
            self.input_groq.setText(cfg.get("groq", {}).get("api_key", "") or os.environ.get("GROQ_API_KEY", ""))
            self.input_groq_model.setText(cfg.get("groq", {}).get("model", "qwen/qwen3.8-27b"))
            self.input_openrouter.setText(cfg.get("openrouter", {}).get("api_key", ""))
            self.input_model.setText(cfg.get("openrouter", {}).get("model", "google/gemini-2.5-flash"))
            self.input_gsi.setText(cfg.get("gsi", {}).get("auth_token", "DOTAHELPER_GSI_SECRET_KEY"))
        except Exception as e:
            logger.error("Error reading config in settings dialog: %s", e)

    def _save_values(self) -> None:
        try:
            cfg = {}
            if self.config_path.exists():
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))

            if "inventory_checker" not in cfg:
                cfg["inventory_checker"] = {}
            cfg["inventory_checker"]["currency"] = self.combo_currency.currentText()
            try:
                cfg["inventory_checker"]["min_item_price"] = float(self.input_min_price.text().strip() or "0.5")
            except ValueError:
                cfg["inventory_checker"]["min_item_price"] = 0.5

            proxy_val = self.input_proxy.text().strip()
            if "workers.dev" in proxy_val or "/inventory" in proxy_val:
                cfg["inventory_checker"]["cloudflare_worker_url"] = proxy_val
                cfg["inventory_checker"]["proxy_url"] = ""
            else:
                cfg["inventory_checker"]["proxy_url"] = proxy_val
                cfg["inventory_checker"]["cloudflare_worker_url"] = ""

            cfg["inventory_checker"]["use_auto_free_proxies"] = self.check_auto_proxies.isChecked()
            if "cache_ttl_hours" not in cfg["inventory_checker"]:
                cfg["inventory_checker"]["cache_ttl_hours"] = 12
            if "request_interval_seconds" not in cfg["inventory_checker"]:
                cfg["inventory_checker"]["request_interval_seconds"] = 1.5

            cfg.pop("lzt", None)
            cfg.pop("pricing", None)

            if "groq" not in cfg:
                cfg["groq"] = {}
            cfg["groq"]["api_key"] = self.input_groq.text().strip()
            cfg["groq"]["model"] = self.input_groq_model.text().strip() or "qwen/qwen3.8-27b"

            if "openrouter" not in cfg:
                cfg["openrouter"] = {}
            cfg["openrouter"]["api_key"] = self.input_openrouter.text().strip()
            cfg["openrouter"]["model"] = self.input_model.text().strip() or "google/gemini-2.5-flash"

            if "gsi" not in cfg:
                cfg["gsi"] = {}
            cfg["gsi"]["auth_token"] = self.input_gsi.text().strip()

            self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Saved configuration via Settings dialog.")

            if self.on_saved_callback:
                self.on_saved_callback(cfg)

            self.accept()
        except Exception as e:
            logger.error("Failed to save config: %s", e)


# ==========================================
# Main Overlay Window
# ==========================================

class OverlayWindow(QWidget):
    """
    Main Floating Overlay Window.
    Includes inventory assessment tab (9 players) and vision chat translation tab.
    """

    force_scan_requested = pyqtSignal()
    translate_chat_requested = pyqtSignal()
    select_roi_requested = pyqtSignal()
    sound_play_requested = pyqtSignal(str)
    sound_stop_requested = pyqtSignal()
    sound_device_changed = pyqtSignal(str)
    sound_volume_changed = pyqtSignal(float)
    sound_monitor_toggled = pyqtSignal(bool)
    sound_refresh_requested = pyqtSignal()

    def __init__(self, config_path: Path = Path("config.json")) -> None:
        super().__init__()
        self.config_path = config_path
        self.signals = SignalBridge()

        # Window state
        self.drag_position: Optional[QPoint] = None
        self.always_on_top = True
        self.player_rows: Dict[str, Dict[str, Any]] = {}
        self.total_inventory_usd: float = 0.0
        self.currency: str = "USD"
        if self.config_path.exists():
            try:
                _cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.currency = _cfg.get("pricing", {}).get("currency", "USD").upper()
            except Exception:
                pass
        self.on_settings_saved: Optional[Callable[[Dict[str, Any]], None]] = None

        self._setup_window_flags()
        self._build_ui()
        self._apply_styles()
        self._connect_signals()

    def _setup_window_flags(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(480, 680)
        self.resize(520, 740)

        # Default position
        self.move(60, 60)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Outer Container with background styling
        self.container = QFrame(self)
        self.container.setObjectName("MainContainer")
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(14, 12, 14, 14)
        container_layout.setSpacing(10)

        # 1. Custom Title Bar
        title_bar = self._create_title_bar()
        container_layout.addWidget(title_bar)

        # 2. Status Ribbon
        self.status_ribbon = self._create_status_ribbon()
        container_layout.addWidget(self.status_ribbon)

        # 3. Tab Widget
        self.tabs = QTabWidget()
        self.tab_inventory = self._create_inventory_tab()
        self.tab_chat = self._create_chat_tab()
        self.tab_soundpad = self._create_soundpad_tab()

        self.tabs.addTab(self.tab_inventory, "🎒 Инвентари (9)")
        self.tabs.addTab(self.tab_chat, "💬 Переводчик (F8)")
        self.tabs.addTab(self.tab_soundpad, "🔊 Саундпад")
        container_layout.addWidget(self.tabs)

        main_layout.addWidget(self.container)

    def _create_title_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Title Label
        title_lbl = QLabel("⚔ DOTA HELPER")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 800; color: #00d2ff; letter-spacing: 1px;")
        layout.addWidget(title_lbl)

        # Badge
        self.lbl_match_state = QLabel("Ожидание")
        self.lbl_match_state.setStyleSheet(
            "background-color: #202434; color: #8f9bb3; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self.lbl_match_state)

        layout.addStretch()

        # Settings Button
        btn_settings = QPushButton("⚙️")
        btn_settings.setFixedSize(26, 26)
        btn_settings.setToolTip("Настройки")
        btn_settings.clicked.connect(self._open_settings)
        layout.addWidget(btn_settings)

        # Pin / Always on top
        self.btn_pin = QPushButton("📌")
        self.btn_pin.setFixedSize(26, 26)
        self.btn_pin.setToolTip("Закрепить поверх всех окон")
        self.btn_pin.clicked.connect(self._toggle_pin)
        layout.addWidget(self.btn_pin)

        # Minimize / Collapse
        btn_min = QPushButton("🗕")
        btn_min.setFixedSize(26, 26)
        btn_min.setToolTip("Свернуть")
        btn_min.clicked.connect(self.showMinimized)
        layout.addWidget(btn_min)

        # Close
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(26, 26)
        btn_close.setObjectName("CloseButton")
        btn_close.setToolTip("Закрыть")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

        return bar

    def _create_status_ribbon(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("RibbonFrame")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(14)

        self.lbl_gsi_status = QLabel("⚪ GSI: Ожидание")
        self.lbl_gsi_status.setStyleSheet("color: #8f9bb3; font-size: 11px; font-weight: 600;")
        layout.addWidget(self.lbl_gsi_status)

        self.lbl_steam_status = QLabel("⚪ Steam: Инициализация")
        self.lbl_steam_status.setStyleSheet("color: #8f9bb3; font-size: 11px; font-weight: 600;")
        layout.addWidget(self.lbl_steam_status)

        layout.addStretch()

        init_sym = "₽" if getattr(self, "currency", "USD") == "RUB" else "$"
        init_txt = "Сумма: $0.00" if init_sym == "$" else "Сумма: 0.00 ₽"
        self.lbl_total_val = QLabel(init_txt)
        self.lbl_total_val.setStyleSheet("color: #ffb703; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.lbl_total_val)

        return widget

    def _create_inventory_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        # Toolbar
        tool_bar = QHBoxLayout()
        self.lbl_lobby_info = QLabel("Лобби: 0 игроков")
        self.lbl_lobby_info.setStyleSheet("color: #a0a6b8; font-size: 12px; font-weight: 500;")
        tool_bar.addWidget(self.lbl_lobby_info)

        tool_bar.addStretch()

        btn_scan = QPushButton("⚡ Сканировать лобби")
        btn_scan.setObjectName("PrimaryButton")
        btn_scan.clicked.connect(self.force_scan_requested.emit)
        tool_bar.addWidget(btn_scan)

        layout.addLayout(tool_bar)

        # Scroll area for players list
        self.scroll_inventory = QScrollArea()
        self.scroll_inventory.setWidgetResizable(True)
        self.scroll_inventory.setFrameShape(QFrame.Shape.NoFrame)

        self.inventory_container = QWidget()
        self.inventory_layout = QVBoxLayout(self.inventory_container)
        self.inventory_layout.setContentsMargins(0, 0, 0, 0)
        self.inventory_layout.setSpacing(6)

        # Placeholder label
        self.lbl_inv_placeholder = QLabel(
            "⏳ Ожидание выбора героев (Hero Selection)...\n"
            "После начала драфта модуль Steamworks автоматически определит 9 игроков.\n\n"
            "Или нажмите «⚡ Сканировать лобби» для мгновенного обновления."
        )
        self.lbl_inv_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_inv_placeholder.setStyleSheet("color: #646b82; font-size: 13px; line-height: 1.5; padding: 40px;")
        self.inventory_layout.addWidget(self.lbl_inv_placeholder)
        self.inventory_layout.addStretch()

        self.scroll_inventory.setWidget(self.inventory_container)
        layout.addWidget(self.scroll_inventory)

        return tab

    def _create_chat_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        # Toolbar
        tool_bar = QHBoxLayout()
        tool_bar.setSpacing(8)

        btn_translate = QPushButton("📸 Перевести чат (F8)")
        btn_translate.setObjectName("PrimaryButton")
        btn_translate.clicked.connect(self.translate_chat_requested.emit)
        tool_bar.addWidget(btn_translate)

        btn_roi = QPushButton("📐 Выбрать область (Ctrl+Shift+F8)")
        btn_roi.clicked.connect(self.select_roi_requested.emit)
        tool_bar.addWidget(btn_roi)

        layout.addLayout(tool_bar)

        # Chat Status label
        self.lbl_chat_status = QLabel("Готов к захвату (F8)")
        self.lbl_chat_status.setStyleSheet("color: #727b93; font-size: 11px;")
        layout.addWidget(self.lbl_chat_status)

        # Scroll Area for translation cards
        self.scroll_chat = QScrollArea()
        self.scroll_chat.setWidgetResizable(True)
        self.scroll_chat.setFrameShape(QFrame.Shape.NoFrame)

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(8)

        # Placeholder
        self.lbl_chat_placeholder = QLabel("Нажмите F8 во время игры для захвата и перевода сообщений чата.")
        self.lbl_chat_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_chat_placeholder.setStyleSheet("color: #646b82; font-size: 12px; padding: 40px;")
        self.chat_layout.addWidget(self.lbl_chat_placeholder)
        self.chat_layout.addStretch()

        self.scroll_chat.setWidget(self.chat_container)
        layout.addWidget(self.scroll_chat)

        return tab

    def _create_soundpad_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        # 1. Output Device and Volume Control Card
        ctrl_card = QFrame()
        ctrl_card.setObjectName("RibbonFrame")
        ctrl_layout = QVBoxLayout(ctrl_card)
        ctrl_layout.setContentsMargins(10, 10, 10, 10)
        ctrl_layout.setSpacing(8)

        # Device Selection Row
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        lbl_dev = QLabel("🎙 Вывод (VAC):")
        lbl_dev.setStyleSheet("color: #8c94a8; font-size: 11px; font-weight: 600;")
        dev_row.addWidget(lbl_dev)

        self.combo_audio_dev = QComboBox()
        self.combo_audio_dev.setStyleSheet("""
            QComboBox {
                background-color: #1c202d;
                border: 1px solid #2e354a;
                border-radius: 6px;
                color: #ffffff;
                padding: 4px 8px;
                font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
        """)
        self.combo_audio_dev.currentTextChanged.connect(self._on_audio_device_changed)
        dev_row.addWidget(self.combo_audio_dev, stretch=1)
        ctrl_layout.addLayout(dev_row)

        # Volume & Headphones Monitoring Row
        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)

        lbl_vol = QLabel("🔊 Громкость:")
        lbl_vol.setStyleSheet("color: #8c94a8; font-size: 11px; font-weight: 600;")
        vol_row.addWidget(lbl_vol)

        self.slider_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.setValue(80)
        self.slider_vol.valueChanged.connect(lambda v: self.sound_volume_changed.emit(v / 100.0))
        vol_row.addWidget(self.slider_vol, stretch=1)

        self.chk_monitor = QCheckBox("🎧 Наушники")
        self.chk_monitor.setChecked(True)
        self.chk_monitor.setStyleSheet("color: #00d2ff; font-size: 11px; font-weight: 600;")
        self.chk_monitor.toggled.connect(self.sound_monitor_toggled.emit)
        vol_row.addWidget(self.chk_monitor)

        ctrl_layout.addLayout(vol_row)
        layout.addWidget(ctrl_card)

        # 2. Action Buttons Row (Stop, Open Folder, Refresh)
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        btn_stop = QPushButton("⏹ Стоп все")
        btn_stop.setStyleSheet("background-color: #ff3366; color: white; border-radius: 6px; padding: 6px 12px; font-weight: 700; border: none;")
        btn_stop.clicked.connect(self.sound_stop_requested.emit)
        act_row.addWidget(btn_stop)

        btn_open_folder = QPushButton("📂 Папка со звуками")
        btn_open_folder.clicked.connect(self._open_sounds_folder)
        act_row.addWidget(btn_open_folder)

        btn_refresh = QPushButton("⟳ Обновить")
        btn_refresh.clicked.connect(self.sound_refresh_requested.emit)
        act_row.addWidget(btn_refresh)

        layout.addLayout(act_row)

        # 3. Scrollable Sounds List
        self.scroll_sounds = QScrollArea()
        self.scroll_sounds.setWidgetResizable(True)
        self.scroll_sounds.setFrameShape(QFrame.Shape.NoFrame)

        self.sounds_container = QWidget()
        self.sounds_layout = QVBoxLayout(self.sounds_container)
        self.sounds_layout.setContentsMargins(0, 0, 0, 0)
        self.sounds_layout.setSpacing(6)

        self.lbl_no_sounds = QLabel("В папке sounds пока нет аудиофайлов.\nНажмите «📂 Папка со звуками», закиньте туда .mp3 или .wav файлы и нажмите «⟳ Обновить»!")
        self.lbl_no_sounds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_no_sounds.setStyleSheet("color: #727b93; font-size: 12px; padding: 30px;")
        self.sounds_layout.addWidget(self.lbl_no_sounds)
        self.sounds_layout.addStretch()

        self.scroll_sounds.setWidget(self.sounds_container)
        layout.addWidget(self.scroll_sounds)

        return tab

    def _open_sounds_folder(self) -> None:
        sounds_dir = Path("sounds").resolve()
        sounds_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(sounds_dir))
        except Exception as e:
            logger.warning("Could not open sounds folder: %s", e)

    def _on_audio_device_changed(self, text: str) -> None:
        if text:
            self.sound_device_changed.emit(text)

    def set_soundpad_devices(self, devices: List[str], current: str = "") -> None:
        self.combo_audio_dev.blockSignals(True)
        self.combo_audio_dev.clear()
        self.combo_audio_dev.addItem("Системный вывод (Default)")
        for dev in devices:
            self.combo_audio_dev.addItem(dev)
        if current:
            idx = self.combo_audio_dev.findText(current)
            if idx >= 0:
                self.combo_audio_dev.setCurrentIndex(idx)
        self.combo_audio_dev.blockSignals(False)

    def update_sounds_list(self, sounds: List[Dict[str, str]]) -> None:
        while self.sounds_layout.count():
            item = self.sounds_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not sounds:
            self.lbl_no_sounds = QLabel("В папке sounds пока нет аудиофайлов.\nНажмите «📂 Папка со звуками», закиньте туда .mp3 или .wav файлы и нажмите «⟳ Обновить»!")
            self.lbl_no_sounds.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_no_sounds.setStyleSheet("color: #727b93; font-size: 12px; padding: 30px;")
            self.sounds_layout.addWidget(self.lbl_no_sounds)
            self.sounds_layout.addStretch()
            return

        for item in sounds:
            row_card = QFrame()
            row_card.setStyleSheet("""
                QFrame {
                    background-color: #171a24;
                    border: 1px solid #232738;
                    border-radius: 6px;
                }
                QFrame:hover {
                    border: 1px solid #00d2ff;
                }
            """)
            row_layout = QHBoxLayout(row_card)
            row_layout.setContentsMargins(10, 6, 10, 6)
            row_layout.setSpacing(10)

            lbl_name = QLabel(item["name"])
            lbl_name.setStyleSheet("color: #ffffff; font-weight: 600; font-size: 12px;")
            row_layout.addWidget(lbl_name, stretch=1)

            btn_play = QPushButton("▶ Играть")
            btn_play.setStyleSheet("""
                QPushButton {
                    background-color: #00e676;
                    color: #000000;
                    font-weight: 700;
                    font-size: 11px;
                    padding: 4px 12px;
                    border-radius: 4px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #69f0ae;
                }
            """)
            sound_path = item["path"]
            btn_play.clicked.connect(lambda _, p=sound_path: self.sound_play_requested.emit(p))
            row_layout.addWidget(btn_play)

            self.sounds_layout.addWidget(row_card)

        self.sounds_layout.addStretch()

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            #MainContainer {
                background-color: rgba(17, 19, 26, 0.95);
                border: 1px solid #232738;
                border-radius: 12px;
            }
            #RibbonFrame {
                background-color: #171a24;
                border: 1px solid #232738;
                border-radius: 8px;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar::tab {
                background-color: #181b26;
                color: #8c94a8;
                padding: 8px 16px;
                border-radius: 6px;
                margin-right: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #23283b;
                color: #00d2ff;
                border: 1px solid #2e3650;
            }
            QPushButton {
                background-color: #202434;
                color: #e0e4ef;
                border: 1px solid #2e354a;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2c3248;
                border: 1px solid #3d4663;
                color: #ffffff;
            }
            #PrimaryButton {
                background-color: #0084ff;
                color: white;
                border: none;
            }
            #PrimaryButton:hover {
                background-color: #009dff;
            }
            #CloseButton:hover {
                background-color: #ff3366;
                color: white;
            }
            QScrollBar:vertical {
                background: #141722;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #2e354a;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    def _connect_signals(self) -> None:
        self.signals.gsi_state_changed.connect(self._on_gsi_state_changed)
        self.signals.steam_status_changed.connect(self._on_steam_status_changed)
        self.signals.lobby_detected.connect(self._on_lobby_detected)
        self.signals.player_evaluated.connect(self._on_player_evaluated)
        self.signals.chat_translated.connect(self._on_chat_translated)
        self.signals.chat_status_changed.connect(self._on_chat_status_changed)
        self.signals.chat_error.connect(self._on_chat_error)
        self.signals.open_roi_selector.connect(self._open_roi_selector)
        self.signals.toggle_visibility.connect(self._toggle_visibility)

    # ==========================================
    # Slot Handlers (Thread-safe)
    # ==========================================

    def _on_gsi_state_changed(self, state: str, match_id: str) -> None:
        state_labels = {
            "HERO_SELECTION": "Выбор героев",
            "STRATEGY_TIME": "Стратегия",
            "PRE_GAME": "Перед игрой",
            "GAME_IN_PROGRESS": "В игре",
            "POST_GAME": "Матч окончен",
            "INIT": "Инициализация",
        }
        display_state = state_labels.get(state, state)
        self.lbl_match_state.setText(f"{display_state} {f'#{match_id}' if match_id else ''}".strip())

        color = "#00e676" if state in ("HERO_SELECTION", "GAME_IN_PROGRESS") else "#8f9bb3"
        self.lbl_gsi_status.setText(f"🟢 GSI: {display_state}")
        self.lbl_gsi_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")

    def _on_steam_status_changed(self, is_ready: bool, local_id: str) -> None:
        if is_ready:
            self.lbl_steam_status.setText("🟢 Steam: Подключен")
            self.lbl_steam_status.setStyleSheet("color: #00e676; font-size: 11px; font-weight: 600;")
        else:
            self.lbl_steam_status.setText("🔴 Steam: Не запущен")
            self.lbl_steam_status.setStyleSheet("color: #ff3366; font-size: 11px; font-weight: 600;")

    def _on_lobby_detected(self, players: List[CoplayPlayer]) -> None:
        # Clear layout
        while self.inventory_layout.count() > 0:
            item = self.inventory_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.player_rows.clear()
        self.total_inventory_usd = 0.0
        self._update_total_display()

        if not players:
            placeholder = QLabel("Игроки в лобби не обнаружены. Повторите сканирование.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #646b82; font-size: 12px; padding: 20px;")
            self.inventory_layout.addWidget(placeholder)
            self.inventory_layout.addStretch()
            return

        self.lbl_lobby_info.setText(f"Лобби: {len(players)} игроков")

        for idx, p in enumerate(players, start=1):
            card = self._create_player_card(idx, p)
            self.inventory_layout.addWidget(card)

        self.inventory_layout.addStretch()

    def _create_player_card(self, index: int, player: CoplayPlayer) -> QWidget:
        card = QFrame()
        card.setObjectName("PlayerCard")
        if not player.is_delta:
            card.setStyleSheet("""
                #PlayerCard {
                    background-color: #12141d;
                    border: 1px dashed #222638;
                    border-radius: 8px;
                }
                #PlayerCard:hover {
                    border: 1px solid #2e354d;
                    background-color: #161924;
                }
            """)
        else:
            card.setStyleSheet("""
                #PlayerCard {
                    background-color: #161924;
                    border: 1px solid #222738;
                    border-radius: 8px;
                }
                #PlayerCard:hover {
                    border: 1px solid #2e354d;
                    background-color: #1a1e2b;
                }
            """)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # Index / Avatar placeholder
        lbl_idx = QLabel(f"#{index}")
        lbl_idx.setStyleSheet("color: #646b82; font-weight: bold; font-size: 12px; width: 24px;")
        layout.addWidget(lbl_idx)

        # Name & SteamID column
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        name_display = player.name if player.name and player.name != "Unknown" else f"Игрок {player.steam_id[-4:]}"
        if not player.is_delta:
            name_display = f"{name_display} (из истории)"
        lbl_name = QLabel(name_display)
        if not player.is_delta:
            lbl_name.setStyleSheet("color: #7b839b; font-weight: 500; font-size: 12px;")
        else:
            lbl_name.setStyleSheet("color: #ffffff; font-weight: 600; font-size: 13px;")
        info_col.addWidget(lbl_name)

        sid_row = QHBoxLayout()
        sid_row.setSpacing(6)
        lbl_sid = QLabel(player.steam_id)
        lbl_sid.setStyleSheet("color: #727b93; font-size: 11px; font-family: Consolas;")
        sid_row.addWidget(lbl_sid)

        btn_copy = QPushButton("📋")
        btn_copy.setFixedSize(20, 20)
        btn_copy.setToolTip("Скопировать SteamID")
        btn_copy.setStyleSheet("font-size: 10px; padding: 0px;")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(player.steam_id))
        sid_row.addWidget(btn_copy)

        btn_web = QPushButton("🌐")
        btn_web.setFixedSize(20, 20)
        btn_web.setToolTip("Открыть профиль Steam")
        btn_web.setStyleSheet("font-size: 10px; padding: 0px;")
        btn_web.clicked.connect(lambda: webbrowser.open(player.profile_url))
        sid_row.addWidget(btn_web)

        sid_row.addStretch()
        info_col.addLayout(sid_row)
        layout.addLayout(info_col)

        layout.addStretch()

        # Inventory Value Column
        val_col = QVBoxLayout()
        val_col.setSpacing(2)
        val_col.setAlignment(Qt.AlignmentFlag.AlignRight)

        lbl_val = QLabel("⏳ Оценка...")
        lbl_val.setStyleSheet("color: #ffb703; font-weight: bold; font-size: 14px;")
        lbl_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        val_col.addWidget(lbl_val)

        status_text = "В очереди" if player.is_delta else "Из истории"
        lbl_status = QLabel(status_text)
        lbl_status.setStyleSheet("color: #626a80; font-size: 10px;" if not player.is_delta else "color: #8f9bb3; font-size: 10px;")
        lbl_status.setAlignment(Qt.AlignmentFlag.AlignRight)
        val_col.addWidget(lbl_status)

        layout.addLayout(val_col)

        # Save references for fast lookup by steam_id
        self.player_rows[player.steam_id] = {
            "name_label": lbl_name,
            "val_label": lbl_val,
            "status_label": lbl_status,
            "value": 0.0,
        }

        return card

    def _on_player_evaluated(self, res: InventoryValueResult) -> None:
        if res.steam_id not in self.player_rows:
            return

        row = self.player_rows[res.steam_id]
        val_lbl = row["val_label"]
        status_lbl = row["status_label"]

        res_curr = getattr(res, "currency", self.currency).upper()
        sym = "₽" if res_curr == "RUB" else "$"

        if res.status in ("OK", "CACHED"):
            val = res.value_usd or 0.0
            row["value"] = val
            if sym == "$":
                val_lbl.setText(f"${val:,.2f}")
            else:
                val_lbl.setText(f"{val:,.2f} ₽")
            val_lbl.setStyleSheet("color: #00e676; font-weight: bold; font-size: 14px;")
            status_lbl.setText("Кэш" if res.cached else "Steam")
            status_lbl.setStyleSheet("color: #00d2ff; font-size: 10px;")
            if getattr(res, "items_count", 0) > 0:
                val_lbl.setToolTip(
                    f"Учтено предметов: {res.valued_items_count} (всего в инвентаре: {res.items_count})"
                )
        elif res.status == "HIDDEN":
            row["value"] = 0.0
            val_lbl.setText("[Скрыт]")
            val_lbl.setStyleSheet("color: #ff3366; font-weight: bold; font-size: 13px;")
            status_lbl.setText("Приватный")
            status_lbl.setStyleSheet("color: #ff3366; font-size: 10px;")
        elif res.status == "NO_TOKEN":
            val_lbl.setText("[Без токена]")
            val_lbl.setStyleSheet("color: #a0a6b8; font-weight: 500; font-size: 12px;")
            status_lbl.setText("Не настроено")
            status_lbl.setStyleSheet("color: #ffb703; font-size: 10px;")
        elif res.status == "ERROR":
            val_lbl.setText("[Ошибка]")
            val_lbl.setStyleSheet("color: #ff3366; font-size: 12px;")
            status_lbl.setText(res.error_message or "Ошибка")
            status_lbl.setStyleSheet("color: #ff3366; font-size: 10px;")
        elif res.status == "QUEUED":
            val_lbl.setText("⏳ Оценка...")
            val_lbl.setStyleSheet("color: #ffb703; font-weight: bold; font-size: 13px;")
            status_lbl.setText("В очереди")

        self._recalculate_total()

    def _recalculate_total(self) -> None:
        self.total_inventory_usd = sum(row.get("value", 0.0) for row in self.player_rows.values())
        self._update_total_display()

    def _update_total_display(self) -> None:
        sym = "₽" if getattr(self, "currency", "USD").upper() == "RUB" else "$"
        if sym == "$":
            self.lbl_total_val.setText(f"Сумма: ${self.total_inventory_usd:,.2f}")
        else:
            self.lbl_total_val.setText(f"Сумма: {self.total_inventory_usd:,.2f} ₽")

    def _on_chat_translated(self, messages: List[TranslatedMessage]) -> None:
        if hasattr(self, "lbl_chat_placeholder") and self.lbl_chat_placeholder.isVisible():
            self.lbl_chat_placeholder.setVisible(False)

        if not messages:
            card = QFrame()
            card.setStyleSheet("background-color: #171b26; border-radius: 6px; padding: 8px;")
            cl = QVBoxLayout(card)
            cl.addWidget(QLabel("Сообщений в выбранной области чата не обнаружено."))
            self.chat_layout.insertWidget(0, card)
            return

        for msg in messages:
            card = self._create_chat_message_card(msg)
            # Insert newest messages at top
            self.chat_layout.insertWidget(0, card)

    def _create_chat_message_card(self, msg: TranslatedMessage) -> QWidget:
        card = QFrame()
        card.setObjectName("ChatCard")
        card.setStyleSheet("""
            #ChatCard {
                background-color: #161924;
                border-left: 3px solid #00d2ff;
                border-top: 1px solid #222738;
                border-right: 1px solid #222738;
                border-bottom: 1px solid #222738;
                border-radius: 6px;
            }
        """)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Top row: Kind badge + time + Copy button
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        lbl_kind = QLabel(f"[{msg.kind}]")
        lbl_kind.setStyleSheet("color: #00d2ff; font-weight: bold; font-size: 11px;")
        top_row.addWidget(lbl_kind)

        time_str = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
        lbl_time = QLabel(time_str)
        lbl_time.setStyleSheet("color: #646b82; font-size: 10px;")
        top_row.addWidget(lbl_time)

        top_row.addStretch()

        btn_copy = QPushButton("📋 Скопировать перевод")
        btn_copy.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(msg.translation))
        top_row.addWidget(btn_copy)

        layout.addLayout(top_row)

        # Original source text
        if msg.source:
            lbl_src = QLabel(f'"{msg.source}"')
            lbl_src.setWordWrap(True)
            lbl_src.setStyleSheet("color: #8f9bb3; font-style: italic; font-size: 12px;")
            layout.addWidget(lbl_src)

        # Translation
        lbl_trans = QLabel(f"🇷🇺 {msg.translation}")
        lbl_trans.setWordWrap(True)
        lbl_trans.setStyleSheet("color: #ffffff; font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_trans)

        return card

    def _on_chat_status_changed(self, status: str) -> None:
        self.lbl_chat_status.setText(status)

    def _on_chat_error(self, err: str) -> None:
        self.lbl_chat_status.setText(f"❌ {err}")
        self.lbl_chat_status.setStyleSheet("color: #ff3366; font-size: 11px; font-weight: 600;")

    # ==========================================
    # Window Interactions
    # ==========================================

    def _open_roi_selector(self) -> None:
        selector = ROISelectorOverlay()
        selector.roi_selected.connect(self._on_roi_selected)
        selector.show()
        # Keep reference to avoid garbage collection
        self._roi_selector = selector

    def _on_roi_selected(self, x1: int, y1: int, x2: int, y2: int) -> None:
        logger.info("New ROI selected: %d, %d, %d, %d", x1, y1, x2, y2)
        self.lbl_chat_status.setText(f"Область сохранена: [{x1}, {y1}, {x2}, {y2}]")
        self.select_roi_requested.emit()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config_path, on_saved_callback=self._handle_settings_saved, parent=self)
        dlg.exec()

    def _handle_settings_saved(self, cfg: Dict[str, Any]) -> None:
        self.currency = cfg.get("pricing", {}).get("currency", "USD").upper()
        self._update_total_display()
        if self.on_settings_saved:
            self.on_settings_saved(cfg)

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _toggle_pin(self) -> None:
        self.always_on_top = not self.always_on_top
        self.btn_pin.setText("📌" if self.always_on_top else "📍")
        flags = self.windowFlags()
        if self.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # Window Dragging support
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_position:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.drag_position = None
