from __future__ import annotations

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenu,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings
from .theme import app_icon


class MainWindow(QMainWindow):
    quit_requested = pyqtSignal()
    translate_chat_requested = pyqtSignal()
    crop_requested = pyqtSignal()
    soundpad_overlay_toggled = pyqtSignal(bool)

    def __init__(
        self,
        settings: Settings,
        chat_tab,
        translator_tab,
        soundpad_tab,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._chat_tab = chat_tab
        self._translator_tab = translator_tab
        self._soundpad_tab = soundpad_tab
        self._status_label: QLabel | None = None
        self._tray: QSystemTrayIcon | None = None
        self._quitting = False

        self._build_ui()
        self._build_tray()
        self._restore_geometry()

    def _build_ui(self) -> None:
        self.setWindowTitle("Dota Companion")
        self.setMinimumSize(950, 620)
        self.resize(1100, 720)

        tabs = QTabWidget()
        tabs.addTab(self._chat_tab, "💬 Чат-переводчик")
        tabs.addTab(self._translator_tab, "🌐 Переводчик")
        tabs.addTab(self._soundpad_tab, "🎵 Саундпад")
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(tabs)
        
        self._status_label = QLabel("Готово")
        layout.addWidget(self._status_label)
        
        self.setCentralWidget(container)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self._tray = QSystemTrayIcon(app_icon(), self)
        self._tray.setToolTip("Dota Companion")

        menu = QMenu()

        toggle_action = menu.addAction("Показать / Скрыть")
        toggle_action.triggered.connect(self.toggle_visibility)

        menu.addSeparator()

        translate_action = menu.addAction("Перевести чат (F8)")
        translate_action.triggered.connect(self.translate_chat_requested.emit)

        crop_action = menu.addAction("Выделить и перевести область (Ctrl+Shift+F8)")
        crop_action.triggered.connect(self.crop_requested.emit)

        self._soundpad_overlay_action = menu.addAction("Саундпад-оверлей")
        self._soundpad_overlay_action.setCheckable(True)
        self._soundpad_overlay_action.setChecked(self._settings.soundpad_overlay_visible)
        self._soundpad_overlay_action.triggered.connect(self.soundpad_overlay_toggled.emit)

        menu.addSeparator()

        quit_action = menu.addAction("Выход (F10)")
        quit_action.triggered.connect(self.quit_requested.emit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def set_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000)

    def show_tray_message(self, title: str, message: str) -> None:
        if self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def set_soundpad_overlay_checked(self, checked: bool) -> None:
        self._soundpad_overlay_action.blockSignals(True)
        self._soundpad_overlay_action.setChecked(checked)
        self._soundpad_overlay_action.blockSignals(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting:
            self._save_geometry()
            event.accept()
            return
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Dota Companion",
            "Приложение свёрнуто в трей. Выход — F10 или меню трея.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def force_close(self) -> None:
        self._quitting = True
        self._save_geometry()
        self.close()

    def _restore_geometry(self) -> None:
        qsettings = QSettings("DotaCompanion", "DotaCompanion")
        geometry = qsettings.value("main_window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_geometry(self) -> None:
        qsettings = QSettings("DotaCompanion", "DotaCompanion")
        qsettings.setValue("main_window/geometry", self.saveGeometry())
