from __future__ import annotations

import asyncio
import os
import queue
import re
import shutil
import threading
from pathlib import Path

from PIL import Image, ImageEnhance
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core.logger import get_logger

log = get_logger("ocr")

TESS_LANGS = {
    "all": "rus+eng+chi_sim",
    "ch": "chi_sim+eng+rus",
    "en": "eng+rus",
    "latin": "spa+eng+rus",
    "ru": "rus+eng",
}

# Windows OCR (winrt) BCP-47 tags for each app language
WINOCR_LANGS: dict[str, list[str]] = {
    "all": ["ru-RU", "ru", "en-US", "en", "zh-Hans-CN", "zh-CN"],
    "ch": [
        "zh-Hans-CN",
        "zh-CN",
        "zh-Hans",
        "zh-Hans-SG",
        "zh-SG",
        "zh-Hant-TW",
        "zh-TW",
        "zh-Hant-HK",
        "zh-HK",
        "zh-Hant-MO",
        "zh-Hans-MO",
        "zh-MO",
        "zh-Hant",
        "zh",
    ],
    "en": ["en-US", "en-GB", "en-CA", "en-AU", "en-NZ", "en-IE", "en-ZA", "en"],
    "latin": [
        "es-ES",
        "es-MX",
        "es-419",
        "es-US",
        "es",
        "pt-BR",
        "pt-PT",
        "pt",
        "it-IT",
        "it",
        "fr-FR",
        "fr-CA",
        "fr-BE",
        "fr-CH",
        "fr",
        "de-DE",
        "de-AT",
        "de-CH",
        "de",
    ],
    "ru": ["ru-RU", "ru", "ru-BY", "ru-KZ", "ru-UA"],
}

_WS = re.compile(r"\s+")
# Latin/Cyrillic letters, digits and common punctuation that survives game chat OCR
_NOISE_RE = re.compile(r"[^\w\s.,!?:;\"'()\[\]{}<>/@#%&*+\-=~«»„“”…№°-]", re.UNICODE)


def _find_tesseract_binary() -> str | None:
    in_path = shutil.which("tesseract")
    if in_path:
        return in_path

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    custom = os.environ.get("TESSERACT_PATH")
    if custom:
        candidates.insert(0, Path(custom))

    for p in candidates:
        if p and p.is_file():
            return str(p)
    return None


class OcrSignals(QObject):
    text_ready = pyqtSignal(str)
    error = pyqtSignal(str)
    backend_changed = pyqtSignal(str)


class OcrEngine(QThread):
    def __init__(self, lang: str = "ch", parent=None) -> None:
        super().__init__(parent)
        self.signals = OcrSignals()
        self._queue: queue.Queue[Image.Image | None] = queue.Queue(maxsize=4)
        self._paused = threading.Event()
        self._paused.clear()
        self._stop_event = threading.Event()
        self._lang = lang

        self._winocr_available: bool | None = None  # None = not checked yet
        self._winocr_lang_tag: str | None = None
        self._winocr_lang_warning: str | None = None
        self._winocr_lang_missing = False

        self._paddle = None
        self._paddle_available = True
        self._tesseract_checked = False
        self._tesseract_available = False
        self._tesseract_installed_langs: set[str] = set()
        self._active_backend = ""
        self._backend_announced = False
        self._lock = threading.RLock()

    # === Public API ===

    def set_lang(self, lang: str) -> None:
        with self._lock:
            if lang == self._lang:
                return
            self._lang = lang if lang in TESS_LANGS else "ch"
            # Re-resolve Windows OCR language pack for the new language
            self._winocr_available = None
            self._winocr_lang_tag = None
            self._winocr_lang_warning = None
            self._winocr_lang_missing = False
            self._paddle = None
            self._backend_announced = False

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def active_backend(self) -> str:
        return self._active_backend

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

    # === Worker loop ===

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
                text = self.recognize_image(image)
            except Exception as exc:
                log.exception("Ошибка распознавания")
                self.signals.error.emit(f"Ошибка OCR: {exc}")
                continue

            if text:
                self.signals.text_ready.emit(text)

    def recognize_image(self, image: Image.Image) -> str:
        with self._lock:
            # 1. Сначала пробуем распознать исходное изображение без искажений масштабирования
            raw_text = self._recognize(image)
            cleaned = self._clean(raw_text)
            if cleaned:
                return cleaned

            # 2. Если не распознано — применяем масштабирование и повышение контраста
            processed = self._preprocess(image)
            raw_text = self._recognize(processed)
            return self._clean(raw_text)

    # === Preprocessing ===

    def _preprocess(self, image: Image.Image) -> Image.Image:
        img = image.convert("RGB")
        w, h = img.size
        # Upscale small crops (common for game chat text) for higher OCR accuracy
        if h < 80 or w < 300:
            scale = max(2, min(4, int(120 / max(h, 1))))
            img = img.resize((w * scale, h * scale), Image.Resampling.LANCZOS)

        # Enhance contrast slightly
        try:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.4)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.3)
        except Exception:
            pass
        return img

    # === Backend chain: Windows OCR -> PaddleOCR -> Tesseract ===

    def _recognize(self, image: Image.Image) -> str:
        # 1. Windows OCR с основным языком
        if self._winocr_available is not False:
            text = self._try_winocr(image)
            if text and text.strip():
                return text

        # 2. Windows OCR с инверсией цвета (светлый текст на темном фоне чата Доты)
        if self._winocr_available:
            from PIL import ImageOps
            try:
                inv = ImageOps.invert(image.convert("RGB"))
                text = self._try_winocr(inv)
                if text and text.strip():
                    return text
            except Exception:
                pass

        # 3. Windows OCR с другими установленными языками системы (RU, EN, ZH)
        if self._winocr_available:
            from PIL import ImageOps
            try:
                inv = ImageOps.invert(image.convert("RGB"))
                other_langs = [l for l in getattr(self, "_installed_winocr_langs", []) if l != self._winocr_lang_tag]
                for o_lang in other_langs:
                    import winocr
                    res = winocr.recognize_pil_sync(image, o_lang)
                    t = (res.get("text") or "").strip() if isinstance(res, dict) else str(getattr(res, "text", "")).strip()
                    if t:
                        log.info("Windows OCR распознал текст через резервный язык %s: %r", o_lang, t)
                        return t
                    res_inv = winocr.recognize_pil_sync(inv, o_lang)
                    t_inv = (res_inv.get("text") or "").strip() if isinstance(res_inv, dict) else str(getattr(res_inv, "text", "")).strip()
                    if t_inv:
                        log.info("Windows OCR (инверсия) распознал текст через %s: %r", o_lang, t_inv)
                        return t_inv
            except Exception as exc:
                log.debug("Ошибка резервных языков WinOCR: %s", exc)

        # 4. PaddleOCR
        if self._paddle_available:
            try:
                if self._paddle is None:
                    from paddleocr import PaddleOCR

                    self._paddle = PaddleOCR(
                        lang=self._lang if self._lang != "all" else "ch",
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        show_log=False,
                    )
                p_text = self._run_paddle(image)
                if p_text and p_text.strip():
                    return p_text
            except Exception as exc:
                log.warning("PaddleOCR недоступен (%s) — fallback на Tesseract", exc)
                self._paddle_available = False
                self._paddle = None

        # 5. Tesseract OCR (поддерживает мультиязычность chi_sim+rus+eng)
        self._ensure_tesseract_setup()

        if self._tesseract_available:
            t_text = self._run_tesseract(image)
            if t_text and t_text.strip():
                return t_text
            try:
                from PIL import ImageOps
                inv = ImageOps.invert(image.convert("RGB"))
                t_inv = self._run_tesseract(inv)
                if t_inv and t_inv.strip():
                    return t_inv
            except Exception:
                pass

            # Если основной язык Tesseract ничего не дал, пробуем другие установленные языки
            for alt_l in ["rus+eng", "chi_sim+eng", "spa+eng"]:
                try:
                    alt_res = self._run_tesseract(image, alt_lang=alt_l)
                    if alt_res and alt_res.strip():
                        log.info("Tesseract распознал текст через %s: %r", alt_l, alt_res)
                        return alt_res
                except Exception:
                    pass
        return ""

    def _try_winocr(self, image: Image.Image) -> str | None:
        """
        Recognize via built-in Windows OCR (Windows.Media.Ocr).
        Returns recognized text, or None if backend is unavailable/failed
        (caller falls through to PaddleOCR/Tesseract).
        """
        if self._winocr_available is None:
            self._init_winocr()

        if not self._winocr_available or not self._winocr_lang_tag:
            return None

        try:
            return self._run_winocr(image)
        except Exception as exc:
            log.warning("Windows OCR не сработал (%s) — fallback на следующий движок", exc)
            self._winocr_available = False
            self._backend_announced = False
            return None

    def _init_winocr(self) -> None:
        self._winocr_available = False
        try:
            from winrt.windows.media.ocr import OcrEngine

            installed = {
                str(getattr(l, "language_tag", "") or "")
                for l in OcrEngine.available_recognizer_languages
            }
            installed.discard("")
            self._installed_winocr_langs = [t for t in installed if t]

            candidates = WINOCR_LANGS.get(self._lang, ["en-US", "en"])
            # 1. Exact match
            chosen = next((t for t in candidates if t in installed), None)

            # 2. Case-insensitive / prefix matching (e.g. zh-Hans matches zh-Hans-CN)
            if not chosen:
                installed_map = {tag.lower(): tag for tag in installed}
                for cand in candidates:
                    cand_l = cand.lower()
                    if cand_l in installed_map:
                        chosen = installed_map[cand_l]
                        break
                    for inst_l, orig_tag in installed_map.items():
                        if inst_l.startswith(cand_l) or cand_l.startswith(inst_l):
                            chosen = orig_tag
                            break
                    if chosen:
                        break

            if chosen:
                self._winocr_available = True
                self._winocr_lang_tag = chosen
                self._winocr_lang_missing = False
                self._active_backend = f"Windows OCR ({chosen})"
                log.info("Windows OCR доступен, язык: %s", chosen)
            else:
                # Recognizer exists, but the needed language pack is missing
                self._winocr_available = True
                self._winocr_lang_tag = next(iter(installed), None)
                self._winocr_lang_missing = True
                self._active_backend = f"Windows OCR ({self._winocr_lang_tag})"
                missing_pkg = "Language.OCR~~~zh-CN~0.0.1.0" if self._lang == "ch" else f"Language.OCR~~~{candidates[0]}~0.0.1.0"
                self._winocr_lang_warning = (
                    f"Для языка «{self._lang}» не установлен языковой пакет Windows OCR. "
                    f"Распознавание временно идёт языком «{self._winocr_lang_tag}». "
                    f"Запустите install_chinese_ocr.bat или команду в PowerShell от администратора: "
                    f"Add-WindowsCapability -Online -Name \"{missing_pkg}\""
                )
                log.warning(self._winocr_lang_warning)

        except ImportError:
            log.info("winocr/winrt не установлен — Windows OCR пропущен")
        except Exception as exc:
            log.warning("Windows OCR недоступен: %s", exc)

    def _run_winocr(self, image: Image.Image) -> str:
        import winocr

        self._announce_backend()

        lang = self._winocr_lang_tag or "en-US"
        result = winocr.recognize_pil_sync(image, lang)
        text = (result.get("text") or "") if isinstance(result, dict) else str(getattr(result, "text", ""))

        if self._winocr_lang_warning:
            self.signals.error.emit(self._winocr_lang_warning)
            self._winocr_lang_warning = None

        return text

    def _announce_backend(self) -> None:
        if self._backend_announced:
            return
        self._backend_announced = True
        log.info("Активный OCR-движок: %s", self._active_backend or "Tesseract/PaddleOCR")
        try:
            self.signals.backend_changed.emit(self._active_backend or "Tesseract")
        except Exception:
            pass

    # === PaddleOCR ===

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

    # === Tesseract ===

    def _ensure_tesseract_setup(self) -> None:
        if self._tesseract_checked:
            return
        self._tesseract_checked = True
        try:
            import pytesseract

            tess_exe = _find_tesseract_binary()
            if tess_exe:
                pytesseract.pytesseract.tesseract_cmd = tess_exe
                log.info("Используется Tesseract OCR: %s", tess_exe)

            try:
                langs = pytesseract.get_languages(config="")
                self._tesseract_installed_langs = set(langs)
                log.info("Доступные языки Tesseract: %s", ", ".join(langs))
            except Exception as e:
                log.warning("Не удалось получить список языков Tesseract: %s", e)
                self._tesseract_installed_langs = {"eng"}

            self._tesseract_available = True
        except ImportError:
            self._tesseract_available = False
            log.error("pytesseract не установлен")
        except Exception as exc:
            self._tesseract_available = False
            log.error("Ошибка инициализации Tesseract: %s", exc)

    def _run_tesseract(self, image: Image.Image, alt_lang: str | None = None) -> str:
        import pytesseract

        self._announce_backend()

        requested_lang = alt_lang or TESS_LANGS.get(self._lang, "eng")
        # Filter language components to only installed ones to avoid Tesseract errors
        if self._tesseract_installed_langs:
            parts = [p.strip() for p in requested_lang.split("+")]
            available_parts = [p for p in parts if p in self._tesseract_installed_langs]
            if available_parts:
                lang = "+".join(available_parts)
            elif "eng" in self._tesseract_installed_langs:
                lang = "eng"
            elif self._tesseract_installed_langs:
                lang = next(iter(self._tesseract_installed_langs))
            else:
                lang = "eng"
        else:
            lang = requested_lang

        try:
            return pytesseract.image_to_string(image, lang=lang, config="--psm 6")
        except Exception as e:
            log.warning("Ошибка Tesseract с lang=%s (%s), пробуем eng", lang, e)
            try:
                return pytesseract.image_to_string(image, lang="eng", config="--psm 6")
            except Exception as e2:
                log.error("Ошибка Tesseract: %s", e2)
                return ""

    # === Post-processing ===

    @staticmethod
    def _clean(text: str) -> str:
        lines: list[str] = []
        for raw in text.splitlines():
            line = _WS.sub(" ", raw).strip()
            # Drop lines made only of non-textual garbage (e.g. CJK mojibake boxes)
            stripped = _NOISE_RE.sub("", line)
            if len(line) >= 1 and stripped:
                lines.append(line)
        return "\n".join(lines)
