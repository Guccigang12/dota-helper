"""
OpenRouter Vision Chat Translator Module.
Captures designated Dota 2 chat screen area, compresses to JPEG Base64,
and translates player speech (Chinese pinyin, Spanish, English slang) to Russian via OpenRouter Vision API.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from PIL import ImageGrab
import requests

logger = logging.getLogger("DotaHelper.ChatTranslator")

SYSTEM_PROMPT = """Ты — профессиональный эксперт-переводчик внутриигрового чата Dota 2. Твоя задача: анализировать скриншот области игрового чата и переводить реплики игроков на живой русский язык.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. Игнорируй ЛЮБЫЕ системные сообщения игры (убийства, паузы, покупка предметов, дисконнекты, подсказки интерфейса, фразы диктора).
2. Извлекай ТОЛЬКО текстовые сообщения реальных игроков (союзников или соперников).
3. Тщательно распознавай китайский язык (иероглифы и игровой пиньинь-сленг: nmsl, tmd, sb, nt, 666, nb, gkd, wdnmd, fw, dbq, cq, djb, cnm, zzz, 2333, gj, jb, 414, 555).
4. Распознавай южноамериканский / испанский игровой сленг (tmre, ctm, asco, chucha, rata, ptm, oe, mrd, bot, tarao, agg, serrano).
5. Распознавай английский сленг и токсичность (diff, ff, end, grief, bot, dog, noob, ez, report, go afk, throw, tilted).
6. Переводи и адаптируй смысл естественно на живой русский язык с сохранением эмоциональной окраски и контекста Dota 2.
7. Формат ответа СТРОГО в валидном формате JSON без markdown-блоков:
{"messages": [{"source": "исходный текст", "kind": "Китайский / Английский / Испанский / Сленг", "translation": "русский перевод"}]}
Если на скриншоте нет сообщений игроков, верни: {"messages": []}."""


@dataclass
class TranslatedMessage:
    source: str
    kind: str
    translation: str
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class ChatVisionTranslator:
    """
    Vision-based chat translator using OpenRouter Multimodal API.
    Handles screen capture of designated ROI, compression, anti-spam queuing, and translation.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "google/gemini-2.5-flash",
        config_path: Path = Path("config.json"),
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "google/gemini-2.5-flash"
        self.config_path = config_path

        # Default ROI (left, top, right, bottom)
        self.roi = {"left": 15, "top": 620, "right": 460, "bottom": 880}
        self.jpeg_quality = 85
        self.queue_busy = False
        self._lock = threading.Lock()

        # Callbacks
        self.on_translation_success: Optional[Callable[[List[TranslatedMessage]], None]] = None
        self.on_status_change: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

        self._load_roi_from_config()

    def set_api_key(self, key: str) -> None:
        """Update OpenRouter API Key."""
        self.api_key = key.strip()

    def set_model(self, model: str) -> None:
        """Update OpenRouter Model."""
        self.model = model.strip()

    def _load_roi_from_config(self) -> None:
        """Load ROI settings from config.json."""
        if not self.config_path.exists():
            return
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            chat_vision = cfg.get("chat_vision", {})
            if "roi" in chat_vision:
                self.roi.update(chat_vision["roi"])
            if "jpeg_quality" in chat_vision:
                self.jpeg_quality = chat_vision["jpeg_quality"]
        except Exception as e:
            logger.warning("Error loading chat ROI from config: %s", e)

    def save_roi(self, left: int, top: int, right: int, bottom: int) -> None:
        """Save new ROI coordinates to memory and config.json."""
        # Ensure left < right and top < bottom
        x1, x2 = min(left, right), max(left, right)
        y1, y2 = min(top, bottom), max(top, bottom)

        # Minimum dimension sanity check
        if (x2 - x1) < 20 or (y2 - y1) < 20:
            logger.warning("Selected ROI is too small (%dx%d), ignored.", x2 - x1, y2 - y1)
            return

        self.roi = {"left": x1, "top": y1, "right": x2, "bottom": y2}
        logger.info("Updated chat ROI coordinates: %s", self.roi)

        try:
            if self.config_path.exists():
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            else:
                cfg = {}

            if "chat_vision" not in cfg:
                cfg["chat_vision"] = {}

            cfg["chat_vision"]["roi"] = self.roi
            self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Saved ROI to config.json")
        except Exception as e:
            logger.error("Failed to save ROI to config.json: %s", e)

    def capture_and_translate_async(self) -> bool:
        """
        Trigger async capture and translation in background thread.
        Guarded by queue_busy flag to prevent spam.
        """
        with self._lock:
            if self.queue_busy:
                logger.info("Chat translation already in progress, request ignored.")
                if self.on_status_change:
                    self.on_status_change("⏳ Перевод уже выполняется...")
                return False

            self.queue_busy = True

        thread = threading.Thread(
            target=self._worker_capture_and_translate,
            name="ChatTranslationWorker",
            daemon=True,
        )
        thread.start()
        return True

    def _worker_capture_and_translate(self) -> None:
        """Worker thread executing capture, base64 encoding, and OpenRouter API request."""
        try:
            if self.on_status_change:
                self.on_status_change("📸 Захват области экрана...")

            # 1. Capture screen ROI
            bbox = (self.roi["left"], self.roi["top"], self.roi["right"], self.roi["bottom"])
            try:
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            except Exception as e:
                logger.error("Screen capture failed: %s", e)
                if self.on_error:
                    self.on_error(f"Ошибка захвата экрана: {e}")
                return

            if img.mode != "RGB":
                img = img.convert("RGB")

            # 2. Compress to JPEG Base64
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=self.jpeg_quality)
            jpeg_bytes = buffer.getvalue()
            b64_str = base64.b64encode(jpeg_bytes).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64_str}"

            if not self.api_key:
                logger.warning("OpenRouter API key is not set.")
                if self.on_error:
                    self.on_error("OpenRouter API ключ не указан в config.json!")
                return

            if self.on_status_change:
                self.on_status_change("🤖 Отправка в нейросеть (Vision)...")

            # 3. Call OpenRouter API
            messages = self._send_openrouter_vision(data_url)

            if self.on_translation_success:
                self.on_translation_success(messages)

            if self.on_status_change:
                count = len(messages)
                self.on_status_change(f"✅ Переведено сообщений: {count}" if count > 0 else "ℹ️ Сообщений в чате не обнаружено")

        except Exception as e:
            logger.error("Exception during chat translation: %s", e)
            if self.on_error:
                self.on_error(f"Ошибка перевода: {str(e)}")
            if self.on_status_change:
                self.on_status_change("❌ Ошибка перевода")
        finally:
            with self._lock:
                self.queue_busy = False

    def _send_openrouter_vision(self, image_data_url: str) -> List[TranslatedMessage]:
        """Send multimodal request to OpenRouter and parse JSON response."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/dotahelper",
            "X-Title": "DotaHelper",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Переведи сообщения игроков из чата на скриншоте строго в указанном JSON-формате.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                            },
                        },
                    ],
                },
            ],
            "temperature": 0.2,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=20.0)

        if not response.ok:
            error_detail = response.text
            try:
                err_json = response.json()
                error_detail = err_json.get("error", {}).get("message", response.text)
            except Exception:
                pass
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {error_detail}")

        res_json = response.json()
        choices = res_json.get("choices", [])
        if not choices:
            return []

        raw_content = choices[0].get("message", {}).get("content", "").strip()
        logger.debug("OpenRouter raw response: %s", raw_content)

        return self._parse_json_messages(raw_content)

    @staticmethod
    def _parse_json_messages(content: str) -> List[TranslatedMessage]:
        """Extract and parse JSON array of messages from LLM output."""
        cleaned = content.strip()

        # Strip markdown ```json ... ``` tags
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
            if match:
                cleaned = match.group(1).strip()

        # Find outer JSON braces if extra text is present
        match_obj = re.search(r"\{[\s\S]*\}", cleaned)
        if match_obj:
            cleaned = match_obj.group(0)

        results: List[TranslatedMessage] = []
        try:
            data = json.loads(cleaned)
            msg_list = data.get("messages", [])
            for item in msg_list:
                src = str(item.get("source", "")).strip()
                kind = str(item.get("kind", "Сленг")).strip()
                trans = str(item.get("translation", "")).strip()
                if trans or src:
                    results.append(TranslatedMessage(source=src, kind=kind, translation=trans))
        except Exception as e:
            logger.warning("Could not parse JSON from LLM: %s. Raw: %s", e, content)
            # Fallback: if model answered with plaintext translation
            if content and "{" not in content:
                results.append(TranslatedMessage(source="Чат", kind="Текст", translation=content.strip()))

        return results
