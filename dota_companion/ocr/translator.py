from __future__ import annotations

import asyncio
import base64
import io
from typing import Literal

import aiohttp
from PIL import Image

from ..core.logger import get_logger
from .slang import (
    DOTA_SYSTEM_PROMPT,
    DOTA_VISION_PROMPT,
    enhance_text_with_slang,
)

log = get_logger("translator")

Provider = Literal[
    "google",
    "gemini",
    "groq",
    "ollama",
    "openai_compatible",
    "openrouter",
    "deepseek",
    "deepl",
    "libretranslate",
]

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
DEEPL_URL = "https://api-free.deepl.com/v2/translate"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"

GROQ_BASE = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

OLLAMA_BASE = "http://localhost:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class TranslationError(Exception):
    pass


class Translator:
    def __init__(
        self,
        provider: Provider = "google",
        deepl_api_key: str = "",
        libretranslate_url: str = "https://libretranslate.com/translate",
        source_lang: str = "auto",
        target_lang: str = "ru",
        gemini_api_key: str = "",
        gemini_model: str = DEFAULT_GEMINI_MODEL,
        custom_api_url: str = OLLAMA_BASE,
        custom_api_key: str = "",
        custom_model: str = DEFAULT_OLLAMA_MODEL,
        groq_api_key: str = "",
        groq_model: str = DEFAULT_GROQ_MODEL,
        openrouter_api_key: str = "",
        openrouter_model: str = DEFAULT_OPENROUTER_MODEL,
        deepseek_api_key: str = "",
        deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
        slang_enhancement: bool = True,
    ) -> None:
        self.provider = provider
        self.deepl_api_key = deepl_api_key
        self.libretranslate_url = libretranslate_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model or DEFAULT_GEMINI_MODEL

        self.custom_api_url = custom_api_url or OLLAMA_BASE
        self.custom_api_key = custom_api_key
        self.custom_model = custom_model or DEFAULT_OLLAMA_MODEL

        self.groq_api_key = groq_api_key
        self.groq_model = groq_model or DEFAULT_GROQ_MODEL

        self.openrouter_api_key = openrouter_api_key
        self.openrouter_model = openrouter_model or DEFAULT_OPENROUTER_MODEL

        self.deepseek_api_key = deepseek_api_key
        self.deepseek_model = deepseek_model or DEFAULT_DEEPSEEK_MODEL

        self.slang_enhancement = slang_enhancement
        self._session: aiohttp.ClientSession | None = None

    def update(
        self,
        provider: Provider,
        deepl_api_key: str,
        libretranslate_url: str,
        source_lang: str,
        target_lang: str,
        gemini_api_key: str = "",
        gemini_model: str = DEFAULT_GEMINI_MODEL,
        custom_api_url: str = OLLAMA_BASE,
        custom_api_key: str = "",
        custom_model: str = DEFAULT_OLLAMA_MODEL,
        groq_api_key: str = "",
        groq_model: str = DEFAULT_GROQ_MODEL,
        openrouter_api_key: str = "",
        openrouter_model: str = DEFAULT_OPENROUTER_MODEL,
        deepseek_api_key: str = "",
        deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
        slang_enhancement: bool = True,
    ) -> None:
        self.provider = provider
        self.deepl_api_key = deepl_api_key
        self.libretranslate_url = libretranslate_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model or DEFAULT_GEMINI_MODEL

        self.custom_api_url = custom_api_url or OLLAMA_BASE
        self.custom_api_key = custom_api_key
        self.custom_model = custom_model or DEFAULT_OLLAMA_MODEL

        self.groq_api_key = groq_api_key
        self.groq_model = groq_model or DEFAULT_GROQ_MODEL

        self.openrouter_api_key = openrouter_api_key
        self.openrouter_model = openrouter_model or DEFAULT_OPENROUTER_MODEL

        self.deepseek_api_key = deepseek_api_key
        self.deepseek_model = deepseek_model or DEFAULT_DEEPSEEK_MODEL

        self.slang_enhancement = slang_enhancement

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=25),
            )
        return self._session

    async def translate(
        self,
        text: str,
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> str:
        text = text.strip()
        if not text:
            return ""
        source = (source_lang or self.source_lang) or "auto"
        target = (target_lang or self.target_lang) or "ru"

        session = await self._ensure_session()
        try:
            if self.provider == "gemini":
                return await self._translate_gemini(session, text, target)
            elif self.provider == "groq":
                return await self._translate_openai_compatible(
                    session=session,
                    text=text,
                    target_lang=target,
                    base_url=GROQ_BASE,
                    model=self.groq_model or DEFAULT_GROQ_MODEL,
                    api_key=self.groq_api_key,
                    service_name="Groq",
                )
            elif self.provider == "openrouter":
                return await self._translate_openai_compatible(
                    session=session,
                    text=text,
                    target_lang=target,
                    base_url=OPENROUTER_BASE,
                    model=self.openrouter_model or DEFAULT_OPENROUTER_MODEL,
                    api_key=self.openrouter_api_key,
                    service_name="OpenRouter",
                )
            elif self.provider == "deepseek":
                return await self._translate_openai_compatible(
                    session=session,
                    text=text,
                    target_lang=target,
                    base_url=DEEPSEEK_BASE,
                    model=self.deepseek_model or DEFAULT_DEEPSEEK_MODEL,
                    api_key=self.deepseek_api_key,
                    service_name="DeepSeek",
                )
            elif self.provider in ("ollama", "openai_compatible"):
                return await self._translate_openai_compatible(
                    session=session,
                    text=text,
                    target_lang=target,
                    base_url=self.custom_api_url or OLLAMA_BASE,
                    model=self.custom_model or DEFAULT_OLLAMA_MODEL,
                    api_key=self.custom_api_key,
                    service_name="Ollama / OpenAI API",
                )
            elif self.provider == "deepl":
                return await self._translate_deepl(session, text, source, target)
            elif self.provider == "libretranslate":
                return await self._translate_libre(session, text, source, target)

            # Default Google Translate (enhanced with Dota 2 slang)
            return await self._translate_google(session, text, source, target)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TranslationError(f"Сетевая ошибка перевода: {exc}") from exc

    async def translate_gemini(self, text: str, *, target_lang: str | None = None) -> str:
        """Translate text specifically via Gemini (used for Chinese chat OCR)."""
        text = text.strip()
        if not text:
            return ""
        if not self.gemini_api_key:
            raise TranslationError("Не задан Gemini API-ключ")
        target = (target_lang or self.target_lang) or "ru"
        session = await self._ensure_session()
        try:
            return await self._translate_gemini(session, text, target)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TranslationError(f"Сетевая ошибка перевода: {exc}") from exc

    async def translate_google(
        self,
        text: str,
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> str:
        """Public fallback method to translate using Google Translate."""
        text = text.strip()
        if not text:
            return ""
        session = await self._ensure_session()
        try:
            return await self._translate_google(
                session,
                text,
                source_lang or self.source_lang or "auto",
                target_lang or self.target_lang or "ru",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TranslationError(f"Google Translate: сетевая ошибка: {exc}") from exc

    async def translate_with_fallback(
        self,
        text: str,
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
    ) -> str:
        """
        Translates via configured provider, falling back gracefully to Google Translate.
        """
        try:
            return await self.translate(text, source_lang=source_lang, target_lang=target_lang)
        except Exception as exc:
            log.warning("Основной переводчик не сработал (%s), fallback на Google Translate", exc)
            return await self.translate_google(text, source_lang=source_lang, target_lang=target_lang)

    def supports_vision(self) -> bool:
        """Returns True if the current provider can process images directly."""
        return self.provider in ("gemini", "ollama", "openai_compatible", "openrouter")

    async def translate_image(
        self,
        image: Image.Image,
        *,
        target_lang: str | None = None,
    ) -> tuple[str, str]:
        """
        Multimodal / Vision translation of game chat screenshot directly.
        Returns tuple: (original_text, translated_text).
        """
        target = (target_lang or self.target_lang) or "ru"
        session = await self._ensure_session()

        # Convert image to JPEG Base64
        buffered = io.BytesIO()
        image.convert("RGB").save(buffered, format="JPEG", quality=90)
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        try:
            if self.provider == "gemini":
                return await self._vision_gemini(session, img_b64, target)
            elif self.provider in ("ollama", "openai_compatible", "openrouter"):
                base_url = (
                    OPENROUTER_BASE
                    if self.provider == "openrouter"
                    else (self.custom_api_url or OLLAMA_BASE)
                )
                model = (
                    self.openrouter_model
                    if self.provider == "openrouter"
                    else (self.custom_model or "qwen2.5-vl")
                )
                api_key = (
                    self.openrouter_api_key
                    if self.provider == "openrouter"
                    else self.custom_api_key
                )
                return await self._vision_openai_compatible(
                    session, img_b64, target, base_url, model, api_key
                )
            raise TranslationError(f"Провайдер {self.provider} не поддерживает прямой перевод по фото")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise TranslationError(f"Сетевая ошибка анализа изображения: {exc}") from exc

    # === Gemini API ===

    async def _translate_gemini(
        self, session: aiohttp.ClientSession, text: str, target_lang: str
    ) -> str:
        if not self.gemini_api_key:
            raise TranslationError("Не задан Gemini API-ключ")

        model_name = self.gemini_model or DEFAULT_GEMINI_MODEL
        url = f"{GEMINI_BASE}/models/{model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.gemini_api_key,
        }
        payload = {
            "system_instruction": {
                "parts": [{"text": DOTA_SYSTEM_PROMPT.format(target_lang=target_lang)}]
            },
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 300,
            },
        }

        last_error: TranslationError | None = None
        for attempt in range(3):
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise TranslationError("Gemini вернул пустой ответ")
                    parts = candidates[0].get("content", {}).get("parts", [])
                    result = "".join(p.get("text", "") for p in parts).strip()
                    if not result:
                        raise TranslationError("Gemini вернул пустой перевод")
                    return result

                body = await resp.text()
                if resp.status in (429, 503):
                    last_error = TranslationError(f"Gemini -> {resp.status}: {body[:120]}")
                    log.warning("Gemini временно недоступен (%s), попытка %d", resp.status, attempt + 1)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                raise TranslationError(f"Gemini -> {resp.status}: {body[:200]}")

        raise TranslationError(f"Gemini недоступен: {last_error}")

    async def _vision_gemini(
        self, session: aiohttp.ClientSession, img_b64: str, target_lang: str
    ) -> tuple[str, str]:
        if not self.gemini_api_key:
            raise TranslationError("Не задан Gemini API-ключ")

        model_name = self.gemini_model or DEFAULT_GEMINI_MODEL
        url = f"{GEMINI_BASE}/models/{model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.gemini_api_key,
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": img_b64,
                            }
                        },
                        {
                            "text": DOTA_VISION_PROMPT.format(target_lang=target_lang)
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 400,
            },
        }

        last_error: TranslationError | None = None
        for attempt in range(3):
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise TranslationError("Gemini Vision вернул пустой ответ")
                    parts = candidates[0].get("content", {}).get("parts", [])
                    raw = "".join(p.get("text", "") for p in parts).strip()
                    if not raw:
                        raise TranslationError("Gemini Vision вернул пустой текст")
                    return self._parse_vision_response(raw)

                body = await resp.text()
                if resp.status in (429, 503):
                    last_error = TranslationError(f"Gemini Vision -> {resp.status}: {body[:120]}")
                    log.warning("Gemini Vision временно недоступен (%s), попытка %d", resp.status, attempt + 1)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                raise TranslationError(f"Gemini Vision -> {resp.status}: {body[:200]}")

        raise TranslationError(f"Gemini Vision недоступен: {last_error}")

    # === OpenAI-Compatible API (Ollama / Groq / OpenRouter / DeepSeek / LM Studio) ===

    async def _translate_openai_compatible(
        self,
        session: aiohttp.ClientSession,
        text: str,
        target_lang: str,
        base_url: str,
        model: str,
        api_key: str = "",
        service_name: str = "LLM API",
    ) -> str:
        clean_url = base_url.rstrip("/")
        if not clean_url.endswith("/chat/completions"):
            endpoint = f"{clean_url}/chat/completions"
        else:
            endpoint = clean_url

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif "openrouter" in clean_url or "groq" in clean_url or "deepseek" in clean_url:
            raise TranslationError(f"Для сервиса {service_name} требуется API-ключ")

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": DOTA_SYSTEM_PROMPT.format(target_lang=target_lang),
                },
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }

        async with session.post(endpoint, headers=headers, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise TranslationError(f"{service_name} -> {resp.status}: {body[:200]}")
            data = await resp.json(content_type=None)

        choices = data.get("choices") or []
        if not choices:
            raise TranslationError(f"{service_name} вернул пустой список ответов")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise TranslationError(f"{service_name} вернул пустой текст ответа")
        return content.strip()

    async def _vision_openai_compatible(
        self,
        session: aiohttp.ClientSession,
        img_b64: str,
        target_lang: str,
        base_url: str,
        model: str,
        api_key: str = "",
    ) -> tuple[str, str]:
        clean_url = base_url.rstrip("/")
        if not clean_url.endswith("/chat/completions"):
            endpoint = f"{clean_url}/chat/completions"
        else:
            endpoint = clean_url

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": DOTA_VISION_PROMPT.format(target_lang=target_lang),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        }

        async with session.post(endpoint, headers=headers, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise TranslationError(f"Vision API -> {resp.status}: {body[:200]}")
            data = await resp.json(content_type=None)

        choices = data.get("choices") or []
        if not choices:
            raise TranslationError("Vision API вернул пустой ответ")
        raw = choices[0].get("message", {}).get("content", "").strip()
        if not raw:
            raise TranslationError("Vision API вернул пустой текст")
        return self._parse_vision_response(raw)

    @staticmethod
    def _parse_vision_response(raw: str) -> tuple[str, str]:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        orig = ""
        trans = ""
        for line in lines:
            if line.lower().startswith("оригинал:") or line.lower().startswith("original:"):
                orig = line.split(":", 1)[1].strip()
            elif line.lower().startswith("перевод:") or line.lower().startswith("translation:"):
                trans = line.split(":", 1)[1].strip()

        if not trans and lines:
            trans = "\n".join(lines)
        if not orig:
            orig = trans

        return orig, trans

    # === Google Translate (Enhanced with Dota 2 Slang Engine) ===

    async def _translate_google(
        self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str
    ) -> str:
        # Pre-process text to handle common gaming slang terms
        query_text = text
        if self.slang_enhancement:
            query_text = enhance_text_with_slang(text)

        clients = ["dict-chrome-ex", "gtx"]
        last_error = None
        for client in clients:
            params = {
                "client": client,
                "sl": source_lang or "auto",
                "tl": target_lang or "ru",
                "dt": "t",
                "q": query_text,
            }
            try:
                async with session.get(GOOGLE_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        parts = []
                        for segment in data[0] if isinstance(data, list) and data else []:
                            if isinstance(segment, list) and segment and isinstance(segment[0], str):
                                parts.append(segment[0])
                        result = "".join(parts).strip()
                        if result:
                            return result
                    else:
                        last_error = f"Google Translate ({client}) -> {resp.status}"
            except Exception as e:
                last_error = str(e)
        raise TranslationError(f"Ошибка Google Translate: {last_error}")

    # === DeepL ===

    async def _translate_deepl(
        self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str
    ) -> str:
        if not self.deepl_api_key:
            raise TranslationError("Не задан DeepL API-ключ")
        form = {
            "auth_key": self.deepl_api_key,
            "text": text,
            "target_lang": target_lang.upper(),
        }
        if source_lang and source_lang != "auto":
            form["source_lang"] = source_lang.upper()
        async with session.post(DEEPL_URL, data=form) as resp:
            if resp.status != 200:
                raise TranslationError(f"DeepL -> {resp.status}")
            data = await resp.json(content_type=None)
        translations = data.get("translations") or []
        if not translations:
            raise TranslationError("DeepL вернул пустой результат")
        return translations[0].get("text", "").strip()

    # === LibreTranslate ===

    async def _translate_libre(
        self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str
    ) -> str:
        payload = {
            "q": text,
            "source": source_lang or "auto",
            "target": target_lang or "ru",
            "format": "text",
        }
        async with session.post(self.libretranslate_url, json=payload) as resp:
            if resp.status != 200:
                raise TranslationError(f"LibreTranslate -> {resp.status}")
            data = await resp.json(content_type=None)
        if isinstance(data, dict):
            translated = data.get("translatedText") or ""
        else:
            translated = str(data)
        translated = translated.strip()
        if not translated:
            raise TranslationError("LibreTranslate вернул пустой результат")
        return translated

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
