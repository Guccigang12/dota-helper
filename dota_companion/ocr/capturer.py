from __future__ import annotations

import threading
from typing import Any

import mss
from PIL import Image

from ..core.logger import get_logger

log = get_logger("capturer")


class ScreenCapturer:
    """Thread-safe screen capture utility using mss."""

    def __init__(self) -> None:
        if hasattr(mss, "MSS"):
            self._sct = mss.MSS()
        else:
            self._sct = mss.mss()
        self._lock = threading.Lock()

    def monitors(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sct.monitors)

    def grab(self, bbox: dict[str, int]) -> Image.Image:
        with self._lock:
            shot = self._sct.grab(bbox)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def close(self) -> None:
        with self._lock:
            try:
                self._sct.close()
            except Exception as exc:
                log.debug("Ошибка при закрытии capturer: %s", exc)
