from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QImage
from PyQt6.QtWidgets import QApplication
from PIL import Image

from .core.async_worker import AsyncWorker
from .core.hotkeys import HotkeyManager
from .core.logger import configure_logging, get_logger
from .core.settings import ChatRegion, Settings, get_config_dir, get_data_dir
from .ocr.capturer import ScreenCapturer
from .ocr.engine import OcrEngine
from .ocr.translator import Translator
from .soundpad.audio import AudioBackend
from .soundpad.triggers import autofill_triggers, default_triggers
from .ui.chat_overlay import ChatOverlay
from .ui.chat_tab import ChatTab
from .ui.crop_overlay import CropOverlay
from .ui.main_window import MainWindow
from .ui.soundpad_overlay import SoundPadOverlay
from .ui.soundpad_tab import SoundPadTab
from .ui.theme import apply_theme
from .ui.translator_tab import TranslatorTab

log = get_logger("app")


class _ResultBridge(QObject):
    done = pyqtSignal(object)


class DotaCompanionApplication(QObject):
    def __init__(self, qapp: QApplication) -> None:
        super().__init__()
        self.qapp = qapp
        apply_theme(qapp)

        self.settings = Settings()
        self.settings.load()
        configure_logging(get_config_dir())

        self.worker = AsyncWorker(self)
        self.worker.task_failed.connect(self._on_task_failed)

        self.audio = AudioBackend(
            device_name=self.settings.audio_device,
            master_volume=self.settings.master_volume,
            voice_volume=self.settings.voice_volume,
            monitor_enabled=getattr(self.settings, "soundpad_monitor", True),
        )
        self.capturer = ScreenCapturer()
        self.engine = OcrEngine(lang=self.settings.ocr_lang)
        self.translator = Translator(
            provider=self.settings.translator_provider,
            deepl_api_key=self.settings.deepl_api_key,
            libretranslate_url=self.settings.libretranslate_url,
            source_lang=self.settings.source_lang,
            target_lang=self.settings.target_lang,
            gemini_api_key=self.settings.gemini_api_key,
            gemini_model=self.settings.gemini_model,
            custom_api_url=self.settings.custom_api_url,
            custom_api_key=self.settings.custom_api_key,
            custom_model=self.settings.custom_model,
            groq_api_key=self.settings.groq_api_key,
            groq_model=self.settings.groq_model,
            openrouter_api_key=self.settings.openrouter_api_key,
            openrouter_model=self.settings.openrouter_model,
            deepseek_api_key=self.settings.deepseek_api_key,
            deepseek_model=self.settings.deepseek_model,
            slang_enhancement=self.settings.slang_enhancement,
        )
        self.hotkeys = HotkeyManager(self)

        self._last_ocr_text = ""
        self._last_notify_ts = 0.0
        self._bridges: set[_ResultBridge] = set()

        self.chat_tab = ChatTab(self.settings)
        self.translator_tab = TranslatorTab(self.settings)
        self.soundpad_tab = SoundPadTab(self.settings, self.audio.list_output_devices)
        self.window = MainWindow(
            self.settings, self.chat_tab, self.translator_tab, self.soundpad_tab
        )

        self.chat_overlay = ChatOverlay(self.settings)
        self.soundpad_overlay = SoundPadOverlay(self.settings)
        self.crop_overlays: list[CropOverlay] = []

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self.settings.save)

        self._wire_signals()
        self._reload_triggers()

    def _wire_signals(self) -> None:
        self.hotkeys.calibrate_region.connect(self.open_crop_overlay)
        self.hotkeys.translate_chat.connect(self.translate_chat_region)
        self.hotkeys.exit_app.connect(self._on_exit)

        self.chat_tab.translate_requested.connect(self.translate_chat_region)
        self.chat_tab.region_requested.connect(self.open_crop_overlay)
        self.chat_tab.settings_changed.connect(self._on_settings_changed)

        self.translator_tab.translate_requested.connect(self._on_manual_translate)
        self.translator_tab.settings_changed.connect(self._on_settings_changed)

        self.soundpad_tab.play_requested.connect(self.audio.play)
        self.soundpad_tab.stop_requested.connect(self.audio.stop)
        self.soundpad_tab.refresh_requested.connect(self._reload_triggers)
        self.soundpad_tab.settings_changed.connect(self._on_settings_changed)
        self.soundpad_tab.show_overlay_requested.connect(self._show_soundpad_overlay)

        self.soundpad_overlay.play_requested.connect(self.audio.play)
        self.soundpad_overlay.stop_requested.connect(self.audio.stop)
        self.soundpad_overlay.refresh_requested.connect(self._reload_triggers)
        self.soundpad_overlay.volume_changed.connect(self._on_overlay_volume)
        self.soundpad_overlay.scale_changed.connect(self._on_overlay_scale)
        self.soundpad_overlay.triggers_changed.connect(self._on_overlay_triggers_changed)
        self.soundpad_overlay.close_requested.connect(self._hide_soundpad_overlay)

        self.window.quit_requested.connect(self._on_exit)
        self.window.translate_chat_requested.connect(self.translate_chat_region)
        self.window.crop_requested.connect(self.open_crop_overlay)
        self.window.soundpad_overlay_toggled.connect(self._set_soundpad_overlay_visible)

        self.engine.signals.text_ready.connect(self._on_ocr_text)
        self.engine.signals.error.connect(lambda msg: self.window.set_status(msg))
        self.engine.signals.backend_changed.connect(
            lambda name: self.window.set_status(f"OCR-движок: {name}")
        )

    def _get_active_model_name(self) -> str:
        p = self.settings.translator_provider
        if p == "ollama":
            return self.settings.custom_model
        elif p == "groq":
            return self.settings.groq_model
        elif p == "openrouter":
            return self.settings.openrouter_model
        elif p == "deepseek":
            return self.settings.deepseek_model
        elif p == "gemini":
            return self.settings.gemini_model
        return ""

    def start(self) -> None:
        log.info("Запуск Dota Companion (Soundpad + Переводчик)")

        self.worker.start()
        if not self.worker.wait_ready(10):
            log.error("Event loop не запустился")
            return

        self.engine.start()
        self.hotkeys.start()

        self.chat_overlay.apply_settings()
        if self.settings.soundpad_overlay_visible:
            self.soundpad_overlay.show()

        self.window.show()
        self.window.set_status(
            "Dota Companion готов. Хоткеи: F8 — перевести чат, Ctrl+Shift+F8 — задать область, F10 — выход"
        )

    def shutdown(self) -> None:
        log.info("Завершение работы")
        self._close_crop_overlays()
        try:
            self.settings.save()
        except Exception:
            pass

        self.engine.stop()
        self.hotkeys.stop()
        self.capturer.close()
        try:
            self.audio.close()
        except Exception:
            pass

        if self.worker.isRunning():
            self.worker.submit("close-sessions", self._close_sessions())
        self.worker.stop()
        self.window.force_close()

    async def _close_sessions(self) -> None:
        await self.translator.close()

    def translate_chat_region(self) -> None:
        """Мгновенный разовый перевод сохранённой области чата по хоткею (F8 / Ctrl+F8) или кнопке."""
        region: ChatRegion = self.settings.chat_region
        if not region.valid:
            self.window.set_status("Сначала задайте область чата (Ctrl+Shift+F8)")
            if self.settings.overlay_over_game:
                self.chat_overlay.show_message("⚠️ Сначала задайте область чата (Ctrl+Shift+F8)")
            return
        monitors = self.capturer.monitors()
        if not (1 <= region.monitor < len(monitors)):
            return
        mon = monitors[region.monitor]
        bbox = {
            "left": mon["left"] + region.x,
            "top": mon["top"] + region.y,
            "width": region.width,
            "height": region.height,
        }
        try:
            image = self.capturer.grab(bbox)
        except Exception as exc:
            log.warning("Не удалось захватить область чата: %s", exc)
            return

        self._on_crop_area_captured(image, region.monitor, bbox)

    def _on_ocr_text(self, text: str) -> None:
        if not text or text == self._last_ocr_text:
            return
        self._last_ocr_text = text
        self.chat_tab.add_message("OCR", text)
        if self.settings.quick_translation:
            self._translate_and_show("OCR", text)

    def _translate_and_show(self, source: str, text: str) -> None:
        self._submit(
            "translate",
            self.translator.translate(text),
            lambda ok, translated: self._on_translated(source, text, translated) if ok else None,
        )

    def _on_translated(self, source: str, original: str, translated: str) -> None:
        self.chat_tab.add_message(f"{source}→перевод", original, translated)
        if self.settings.overlay_over_game:
            self.chat_overlay.show_message(translated)
        if self.settings.notifications_enabled:
            now = time.monotonic()
            if now - self._last_notify_ts > 4.0:
                self._last_notify_ts = now
                self.window.show_tray_message("Dota Companion · чат", translated[:120])

    def _close_crop_overlays(self) -> None:
        overlays = list(self.crop_overlays)
        self.crop_overlays.clear()
        for ov in overlays:
            try:
                ov.blockSignals(True)
                ov.close()
            except (RuntimeError, ReferenceError, Exception):
                pass

    def open_crop_overlay(self) -> None:
        if self.crop_overlays:
            return
        screens = self.qapp.screens()
        if not screens:
            return

        cursor = QCursor.pos()
        active_screen = next((s for s in screens if s.geometry().contains(cursor)), self.qapp.primaryScreen())
        monitors = self.capturer.monitors()

        for s_idx, screen in enumerate(screens):
            sg = screen.geometry()
            matched_idx = 0
            for idx, mon in enumerate(monitors[1:], start=1):
                if mon.get("left") == sg.x() and mon.get("top") == sg.y():
                    matched_idx = idx
                    break
            if matched_idx == 0:
                matched_idx = min(s_idx + 1, len(monitors) - 1) if len(monitors) > 1 else 1

            screenshot = None
            pix = None
            # 1. Надежный прямой захват экрана через QScreen (DWM, без ошибок GDI BitBlt)
            try:
                pix = screen.grabWindow(0)
                if not pix.isNull():
                    qimg = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                    w, h = qimg.width(), qimg.height()
                    ptr = qimg.bits()
                    ptr.setsize(h * w * 4)
                    screenshot = Image.frombuffer("RGBA", (w, h), bytes(ptr), "raw", "RGBA", 0, 1).convert("RGB")
            except Exception as exc:
                log.warning("Не удалось сделать скриншот через QScreen для экрана %s: %s", screen.name(), exc)

            # 2. Резервный захват через ScreenCapturer (mss)
            if screenshot is None and 1 <= matched_idx < len(monitors):
                try:
                    screenshot = self.capturer.grab(monitors[matched_idx])
                except Exception as e:
                    log.warning("Не удалось сделать скриншот для монитора %s: %s", matched_idx, e)

            overlay = CropOverlay(screen, matched_idx, screenshot, pixmap=pix)
            overlay.area_captured.connect(self._on_crop_area_captured)
            overlay.region_selected.connect(self._on_region_selected)
            overlay.cancelled.connect(self._on_crop_cancelled)
            overlay.destroyed.connect(self._on_crop_destroyed)

            self.crop_overlays.append(overlay)
            overlay.show()
            overlay.raise_()
            if screen == active_screen:
                overlay.activateWindow()

    def _on_crop_area_captured(self, image, monitor_index: int, bbox: dict) -> None:
        self._close_crop_overlays()
        log.info("Захвачена область для перевода: размер %s, bbox=%s", getattr(image, "size", "?"), bbox)
        self.window.set_status("Распознавание и перевод выделенной области...")

        async def do_crop_and_translate():
            # If provider supports direct Vision translation and user has enabled it
            if self.settings.use_vision_for_crop and self.translator.supports_vision():
                try:
                    orig, trans = await self.translator.translate_image(
                        image, target_lang=self.settings.target_lang
                    )
                    if trans:
                        log.debug("Vision перевод успешен: %r -> %r", orig, trans)
                        return ("Vision", orig, trans)
                except Exception as exc:
                    log.warning("Vision перевод не удался (%s), переключаемся на стандартный OCR", exc)

            # Fallback to local OCR + Translator
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self.engine.recognize_image, image)
            log.debug("OCR распознал текст: %r", text)
            if not text:
                return ("Выделение", "", "")

            translated = await self.translator.translate_with_fallback(
                text,
                source_lang=self.settings.source_lang,
                target_lang=self.settings.target_lang,
            )
            return ("Выделение", text, translated)

        def on_done(ok: bool, result):
            if not ok:
                log.error("Ошибка при переводе выделенной области: %s", result)
                self.window.set_status(f"Ошибка перевода: {result}")
                return
            source_tag, text, translated = result
            if not text and not translated:
                log.warning("Текст в выделенной области не обнаружен (размер: %s)", getattr(image, "size", "?"))
                self.window.set_status("В выделенной области текст не обнаружен")
                if self.settings.overlay_over_game:
                    self.chat_overlay.show_message("⚠️ Текст не обнаружен")
                return

            self._on_translated(source_tag, text, translated)
            self.window.set_status(f"Переведено: {translated[:80]}")

        self._submit("area-translate", do_crop_and_translate(), on_done)

    def _on_region_selected(self, monitor_index: int, bbox: dict) -> None:
        self.settings.chat_region = ChatRegion(
            monitor=monitor_index,
            x=bbox["left"],
            y=bbox["top"],
            width=bbox["width"],
            height=bbox["height"],
        )
        self.chat_tab.refresh_region_label()
        self._schedule_save()

    def _on_crop_cancelled(self) -> None:
        self._close_crop_overlays()
        self.window.set_status("Выбор области отменён")

    def _on_crop_destroyed(self) -> None:
        cleaned = []
        for ov in self.crop_overlays:
            try:
                _ = ov.isVisible()
                cleaned.append(ov)
            except (RuntimeError, ReferenceError):
                pass
        self.crop_overlays = cleaned

    def _reload_triggers(self) -> None:
        sound_dir = self.settings.sound_dir or str(get_data_dir() / "sounds")
        Path(sound_dir).mkdir(parents=True, exist_ok=True)
        if not self.settings.sound_dir:
            self.settings.sound_dir = sound_dir
        current = self.settings.sound_triggers or default_triggers()
        self.settings.sound_triggers = autofill_triggers(
            current, sound_dir, ignored_sounds=getattr(self.settings, "ignored_sounds", [])
        )
        self.soundpad_tab.set_triggers(self.settings.sound_triggers)
        self.soundpad_overlay.set_triggers(self.settings.sound_triggers)
        self._schedule_save()
        # Фоновая предзагрузка звуков в память для мгновенного первого воспроизведения
        valid_paths = [t.path for t in self.settings.sound_triggers if t.available and t.path]
        if valid_paths:
            self.audio.preload(valid_paths)

    def _on_overlay_triggers_changed(self) -> None:
        """Синхронизация при скрытии/удалении звука прямо из плавающего оверлея."""
        self.soundpad_tab.set_triggers(self.settings.sound_triggers)
        self._schedule_save()

    def _on_overlay_volume(self, volume: float) -> None:
        self.settings.master_volume = volume
        self.audio.set_volumes(volume, self.settings.voice_volume)
        self._schedule_save()

    def _on_overlay_scale(self, scale: float) -> None:
        self.settings.soundpad_overlay_scale = scale
        self._schedule_save()

    def _show_soundpad_overlay(self) -> None:
        self._set_soundpad_overlay_visible(True)

    def _hide_soundpad_overlay(self) -> None:
        self._set_soundpad_overlay_visible(False)

    def _set_soundpad_overlay_visible(self, visible: bool) -> None:
        self.settings.soundpad_overlay_visible = visible
        if visible:
            self.soundpad_overlay.show()
            self.soundpad_overlay.raise_()
        else:
            self.soundpad_overlay.hide()
        self.window.set_soundpad_overlay_checked(visible)
        self._schedule_save()

    def _on_settings_changed(self) -> None:
        s = self.settings
        self.translator.update(
            provider=s.translator_provider,
            deepl_api_key=s.deepl_api_key,
            libretranslate_url=s.libretranslate_url,
            source_lang=s.source_lang,
            target_lang=s.target_lang,
            gemini_api_key=s.gemini_api_key,
            gemini_model=s.gemini_model,
            custom_api_url=s.custom_api_url,
            custom_api_key=s.custom_api_key,
            custom_model=s.custom_model,
            groq_api_key=s.groq_api_key,
            groq_model=s.groq_model,
            openrouter_api_key=s.openrouter_api_key,
            openrouter_model=s.openrouter_model,
            deepseek_api_key=s.deepseek_api_key,
            deepseek_model=s.deepseek_model,
            slang_enhancement=s.slang_enhancement,
        )
        self.audio.set_volumes(s.master_volume, s.voice_volume)
        self.audio.set_device(s.audio_device)
        self.audio.set_monitor_enabled(getattr(s, "soundpad_monitor", True))
        self.engine.set_lang(s.ocr_lang)
        self.chat_overlay.apply_settings()
        self.soundpad_overlay.set_triggers(s.sound_triggers)
        self.translator_tab.set_provider_info(s.translator_provider, self._get_active_model_name())
        if not s.overlay_over_game:
            self.chat_overlay.hide()
        self._schedule_save()

    def _schedule_save(self) -> None:
        if not self._save_timer.isActive():
            self._save_timer.start()

    def _on_manual_translate(self, text: str, source: str, target: str) -> None:
        self.translator_tab.set_busy(True)
        self._submit(
            "manual-translate",
            self.translator.translate(text, source_lang=source, target_lang=target),
            lambda ok, result: self._on_manual_done(ok, result),
        )

    def _on_manual_done(self, ok: bool, result) -> None:
        self.translator_tab.set_busy(False)
        if ok:
            self.translator_tab.show_result(str(result))
        else:
            self.translator_tab.show_error(str(result))

    def _on_task_failed(self, name: str, error: str) -> None:
        log.error("Задача %s упала: %s", name, error)
        self.window.set_status(f"Ошибка: {error}")

    def _submit(self, name: str, coro, on_done) -> None:
        async def runner():
            try:
                return (True, await coro)
            except Exception as exc:
                log.exception("Задача %s упала", name)
                return (False, exc)

        bridge = _ResultBridge(self)
        bridge.done.connect(lambda res: self._deliver_bridge(bridge, on_done, res))
        self._bridges.add(bridge)
        self.worker.submit(name, runner(), lambda res: bridge.done.emit(res))

    def _deliver_bridge(self, bridge: _ResultBridge, on_done, result: tuple) -> None:
        self._bridges.discard(bridge)
        try:
            on_done(*result)
        except Exception:
            log.exception("Ошибка в колбэке фоновой задачи")

    def _on_exit(self) -> None:
        log.info("Выход по запросу")
        self.shutdown()
        self.qapp.quit()