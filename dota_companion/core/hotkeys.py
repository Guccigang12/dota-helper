
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal

from .logger import get_logger

log = get_logger("hotkeys")

                             
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
WM_HOTKEY = 0x0312

HOTKEY_CALIBRATE = 1                  
HOTKEY_PAUSE = 2           
HOTKEY_EXIT = 3             

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


WM_QUIT = 0x0012

_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetMessageW.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class HotkeyManager(QObject):
                                                                              

    calibrate_region = pyqtSignal()
    toggle_pause = pyqtSignal()
    exit_app = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._stop_event = threading.Event()
        self._registered: dict[int, tuple[int, int]] = {}

                                                                          

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._thread_id = self._thread.native_id

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread_id is not None:
                                                             
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)

                                                                          

    def _register(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        ok = bool(_user32.RegisterHotKey(None, hotkey_id, modifiers, vk))
        if ok:
            self._registered[hotkey_id] = (modifiers, vk)
        return ok

    def _unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            _user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def _run(self) -> None:
                                                                           
                                                                             
        bindings = [
            (HOTKEY_CALIBRATE, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_F8, "Ctrl+Shift+F8"),
            (HOTKEY_PAUSE, 0, VK_F9, "F9"),
            (HOTKEY_EXIT, 0, VK_F10, "F10"),
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
        if hotkey_id == HOTKEY_CALIBRATE:
            self.calibrate_region.emit()
        elif hotkey_id == HOTKEY_PAUSE:
            self.toggle_pause.emit()
        elif hotkey_id == HOTKEY_EXIT:
            self.exit_app.emit()
