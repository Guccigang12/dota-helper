
   
from __future__ import annotations

import queue
import re
import threading

from PIL import Image
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core.logger import get_logger

log = get_logger("ocr")

                                    
TESS_LANGS = {
    "ch": "chi_sim+eng",
    "en": "eng",
    "latin": "spa+eng",
}

_WS = re.compile(r"\s+")


class OcrSignals(QObject):
                                                                       

    text_ready = pyqtSignal(str)                                           
    error = pyqtSignal(str)


class OcrEngine(QThread):
                                          

    def __init__(self, lang: str = "ch", parent=None) -> None:
        super().__init__(parent)
        self.signals = OcrSignals()
        self._queue: queue.Queue[Image.Image | None] = queue.Queue(maxsize=4)
        self._paused = threading.Event()
        self._paused.clear()                                 
        self._stop_event = threading.Event()
        self._lang = lang

        self._paddle = None
        self._paddle_available = True
        self._tesseract_checked = False
        self._tesseract_available = False

                                                                          

    def set_lang(self, lang: str) -> None:
        self._lang = lang if lang in TESS_LANGS else "ch"
                                                                  
        self._paddle = None

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def submit(self, image: Image.Image) -> None:
                                                                         
        if self._paused.is_set():
            return
        try:
            self._queue.put_nowait(image)
        except queue.Full:
            pass                                                     

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self.wait(5000)

                                                                          

    def run(self) -> None:                                        
        while not self._stop_event.is_set():
            try:
                image = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if image is None:
                break
            if self._paused.is_set():
                continue
            try:
                text = self._recognize(image)
            except Exception as exc:                                                       
                log.exception("Ошибка распознавания")
                self.signals.error.emit(f"Ошибка OCR: {exc}")
                continue

            text = self._clean(text)
            if text:
                self.signals.text_ready.emit(text)

                                                                          

    def _recognize(self, image: Image.Image) -> str:
        if self._paddle_available:
            try:
                if self._paddle is None:
                    from paddleocr import PaddleOCR

                    self._paddle = PaddleOCR(
                        lang=self._lang,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        show_log=False,
                    )
                return self._run_paddle(image)
            except Exception as exc:                
                log.warning("PaddleOCR недоступен (%s) — fallback на Tesseract", exc)
                self._paddle_available = False
                self._paddle = None

        if not self._tesseract_checked:
            try:
                import pytesseract              

                self._tesseract_available = True
            except ImportError:
                self._tesseract_available = False
            self._tesseract_checked = True
            if not self._tesseract_available:
                log.error("OCR недоступен: установи PaddleOCR (paddlepaddle+paddleocr) или Tesseract")

        if self._tesseract_available:
            return self._run_tesseract(image)
        return ""

    def _run_paddle(self, image: Image.Image) -> str:
        import numpy as np

        array = np.array(image.convert("RGB"))
        lines: list[str] = []

        if hasattr(self._paddle, "predict"):
                                                                 
            results = self._paddle.predict(input=array)
            for result in results or []:
                if isinstance(result, dict):
                    for t in result.get("rec_texts") or []:
                        if t:
                            lines.append(str(t))
                elif isinstance(result, (list, tuple)):
                    lines.extend(str(t) for t in result if t)
        else:
                                                                          
            result = self._paddle.ocr(array, cls=False)
            for page in result or []:
                for entry in page or []:
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        text = entry[1]
                        if isinstance(text, (list, tuple)):
                            text = text[0]
                        if text:
                            lines.append(str(text))
        return "\n".join(lines)

    def _run_tesseract(self, image: Image.Image) -> str:
        import pytesseract

        lang = TESS_LANGS.get(self._lang, "eng")
        return pytesseract.image_to_string(image, lang=lang, config="--psm 6")

    @staticmethod
    def _clean(text: str) -> str:
        lines: list[str] = []
        for raw in text.splitlines():
            line = _WS.sub(" ", raw).strip()
            if len(line) >= 2:
                lines.append(line)
        return "\n".join(lines)
