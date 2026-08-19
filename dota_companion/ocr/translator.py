
   
from __future__ import annotations

import asyncio
from typing import Literal

import aiohttp

from ..core.logger import get_logger

log = get_logger("translator")

Provider = Literal["google", "deepl", "libretranslate", "gemini"]

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
DEEPL_URL = "https://api-free.deepl.com/v2/translate"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DotaCompanion/1.0"

GEMINI_SYSTEM_PROMPT = (
    "Ты — переводчик игрового чата Dota 2. Переводи сообщение игрока на язык «{target_lang}». "
    "Отвечай ТОЛЬКО переводом, без пояснений и кавычек. "
    "Переводи полно и точно, не теряя смысл; стиль — краткий игровой чат. "
    "Игровой сленг (gg, wp, nt, gl hf) оставляй без изменений. "
    "Если сообщение уже на языке «{target_lang}», верни его без изменений."
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
    ) -> None:
        self.provider = provider
        self.deepl_api_key = deepl_api_key
        self.libretranslate_url = libretranslate_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model or DEFAULT_GEMINI_MODEL

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
    ) -> None:
        self.provider = provider
        self.deepl_api_key = deepl_api_key
        self.libretranslate_url = libretranslate_url
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model or DEFAULT_GEMINI_MODEL

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=20),
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
            if self.provider == "deepl":
                return await self._translate_deepl(session, text, source, target)
            if self.provider == "libretranslate":
                return await self._translate_libre(session, text, source, target)
            if self.provider == "gemini":
                return await self._translate_gemini(session, text, target)
            return await self._translate_google(session, text, source, target)
        except aiohttp.ClientError as exc:
            raise TranslationError(f"Сетевая ошибка перевода: {exc}") from exc

    async def _translate_gemini(self, session: aiohttp.ClientSession, text: str, target_lang: str) -> str:
                                                                               
        if not self.gemini_api_key:
            raise TranslationError("Не задан Gemini API-ключ")

        url = f"{GEMINI_BASE}/models/{self.gemini_model}:generateContent"
        params = {"key": self.gemini_api_key}
        payload = {
            "system_instruction": {
                "parts": [{"text": GEMINI_SYSTEM_PROMPT.format(target_lang=target_lang)}]
            },
            "contents": [{"parts": [{"text": text}]}],
        }

        last_error: TranslationError | None = None
        for attempt in range(3):
            async with session.post(url, params=params, json=payload) as resp:
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

    async def _translate_google(self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str) -> str:
        params = {
            "client": "gtx",
            "sl": source_lang or "auto",
            "tl": target_lang or "ru",
            "dt": "t",
            "q": text,
        }
        async with session.get(GOOGLE_URL, params=params) as resp:
            if resp.status != 200:
                raise TranslationError(f"Google Translate -> {resp.status}")
            data = await resp.json(content_type=None)

                                                                                 
        parts = []
        for segment in data[0] if isinstance(data, list) and data else []:
            if isinstance(segment, list) and segment and isinstance(segment[0], str):
                parts.append(segment[0])
        result = "".join(parts).strip()
        if not result:
            raise TranslationError("Google Translate вернул пустой результат")
        return result

    async def _translate_deepl(self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str) -> str:
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
        translations = (data.get("translations") or [])
        if not translations:
            raise TranslationError("DeepL вернул пустой результат")
        return translations[0].get("text", "").strip()

    async def _translate_libre(self, session: aiohttp.ClientSession, text: str, source_lang: str, target_lang: str) -> str:
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
