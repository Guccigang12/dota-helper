
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal

from .logger import get_logger

log = get_logger("hotkeys")

import sys

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_T = 0x54
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
WM_HOTKEY = 0x0312

HOTKEY_CALIBRATE = 1
HOTKEY_CALIBRATE_ALT = 4
HOTKEY_TRANSLATE_CHAT = 2
HOTKEY_TRANSLATE_CHAT_CTRL = 5
HOTKEY_EXIT = 3

WM_QUIT = 0x0012


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
else:
    _user32 = None
    _kernel32 = None


class HotkeyManager(QObject):
    calibrate_region = pyqtSignal()
    translate_chat = pyqtSignal()
    exit_app = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._registered: dict[int, tuple[int, int]] = {}

    def start(self) -> None:
        if sys.platform != "win32" or _user32 is None:
            log.warning("Глобальные хоткеи поддерживаются только на Windows")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=1.0)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread_id is not None and _user32 is not None:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)

    def _register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        if _user32 is None:
            return False
        ok = bool(_user32.RegisterHotKey(None, hotkey_id, modifiers, vk))
        if ok:
            self._registered[hotkey_id] = (modifiers, vk)
        return ok

    def _unregister_all(self) -> None:
        if _user32 is not None:
            for hotkey_id in list(self._registered):
                _user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def _run(self) -> None:
        if _user32 is None:
            return
        self._thread_id = threading.get_native_id()
        self._ready_event.set()

        bindings = [
            (HOTKEY_TRANSLATE_CHAT, MOD_NOREPEAT, VK_F8, "F8"),
            (HOTKEY_TRANSLATE_CHAT_CTRL, MOD_CONTROL | MOD_NOREPEAT, VK_F8, "Ctrl+F8"),
            (HOTKEY_CALIBRATE, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_F8, "Ctrl+Shift+F8"),
            (HOTKEY_CALIBRATE_ALT, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_T, "Ctrl+Shift+T"),
            (HOTKEY_EXIT, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_F10, "Ctrl+Shift+F10"),
        ]
        for hotkey_id, mods, vk, label in bindings:
            if self._register(hotkey_id, mods, vk):
                log.info("Хоткей зарегистрирован: %s", label)
            else:
                log.warning("Не удалось зарегистрировать хоткей %s (занят другим приложением?)", label)

        msg = MSG()
        while not self._stop_event.is_set():
            result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == WM_HOTKEY:
                self._dispatch(int(msg.wParam))

        self._unregister_all()

    def _dispatch(self, hotkey_id: int) -> None:
        if hotkey_id in (HOTKEY_CALIBRATE, HOTKEY_CALIBRATE_ALT):
            self.calibrate_region.emit()
        elif hotkey_id in (HOTKEY_TRANSLATE_CHAT, HOTKEY_TRANSLATE_CHAT_CTRL):
            self.translate_chat.emit()
        elif hotkey_id == HOTKEY_EXIT:
            self.exit_app.emit()
