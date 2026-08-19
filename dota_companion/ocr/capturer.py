
from __future__ import annotations

from typing import Any

import mss
from PIL import Image

from ..core.logger import get_logger

log = get_logger("capturer")


class ScreenCapturer:
    def __init__(self) -> None:
        self._sct = mss.mss()

                                                                          

    def monitors(self) -> list[dict[str, Any]]:
                                                                              
        return self._sct.monitors

    def grab(self, bbox: dict[str, int]) -> Image.Image:
                                                                                                 
        shot = self._sct.grab(bbox)
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:                                             
            pass
