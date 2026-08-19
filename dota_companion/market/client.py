
   
from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from .models import CURRENCIES, InventoryValue
from ..core.logger import get_logger

log = get_logger("market")

BASE_URL = "https://prod-api.lzt.market"
USER_AGENT = "DotaCompanion/1.0 (https://github.com/dota-companion)"


class MarketApiError(Exception):
    pass
                                     


class UnauthorizedError(MarketApiError):
    pass
                                                 


class ForbiddenError(MarketApiError):
    pass
                                


class RateLimitedError(MarketApiError):
                                        

    def __init__(self, message: str, retry_after: float = 5.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class MarketClient:
                                                                             

    def __init__(
        self,
        token: str,
        currency: str = "rub",
        app_id: int = 570,
        ignore_cache: bool = False,
        min_interval: float = 0.8,
        max_retries: int = 3,
        timeout: float = 15.0,
    ) -> None:
        self._token = token
        self._currency = currency if currency in CURRENCIES else "rub"
        self._app_id = app_id
        self._ignore_cache = ignore_cache
        self._min_interval = max(0.1, min_interval)
        self._max_retries = max_retries
        self._timeout = timeout

        self._session: aiohttp.ClientSession | None = None
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    def update_config(
        self,
        token: str | None = None,
        currency: str | None = None,
        app_id: int | None = None,
        ignore_cache: bool | None = None,
        min_interval: float | None = None,
    ) -> None:
                                                                
        if token is not None:
            self._token = token
        if currency is not None and currency in CURRENCIES:
            self._currency = currency
        if app_id is not None:
            self._app_id = app_id
        if ignore_cache is not None:
            self._ignore_cache = ignore_cache
        if min_interval is not None:
            self._min_interval = max(0.1, min_interval)

                                                                          

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers={"User-Agent": USER_AGENT},
            )
        return self._session

    async def _throttle(self) -> None:
                                                                
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

                                                                          

    async def get_inventory_value(self, item_id: int | str) -> InventoryValue:
                                                                        
        params = {
            "app_id": str(self._app_id),
            "currency": self._currency,
            "ignore_cache": str(self._ignore_cache).lower(),
        }
        return await self._request_value(f"{item_id}/inventory-value", params, item_id=int(item_id))

    async def get_inventory_value_by_link(self, steam_id64: int | str) -> InventoryValue:

           
        steam_id64 = str(steam_id64)
        params = {
            "link": f"https://steamcommunity.com/profiles/{steam_id64}",
            "app_id": str(self._app_id),
            "currency": self._currency,
            "ignore_cache": str(self._ignore_cache).lower(),
        }
        return await self._request_value("steam-value", params, item_id=0)

    async def _request_value(self, path: str, params: dict[str, str], item_id: int) -> InventoryValue:
        if not self._token or not self._token.strip():
            raise UnauthorizedError("Не указан API-токен Lolzteam Market (укажите в settings.json)")

        session = await self._ensure_session()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "accept": "application/json",
        }
        url = f"{BASE_URL}/{path}"
        last_error: MarketApiError | None = None

        for attempt in range(self._max_retries + 1):
            await self._throttle()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 401:
                        raise UnauthorizedError("Неверный или отсутствующий API-токен (401)")
                    if resp.status == 403:
                        detail = await self._error_text(resp)
                        raise ForbiddenError(detail or "Доступ запрещён (403)")
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait = float(retry_after) if retry_after else 5.0 * (attempt + 1)
                        raise RateLimitedError("Превышен лимит запросов (429)", retry_after=wait)
                    if resp.status >= 500:
                        raise MarketApiError(f"Серверная ошибка маркета ({resp.status})")
                    if resp.status != 200:
                        detail = await self._error_text(resp)
                        raise MarketApiError(detail or f"Ошибка API ({resp.status})")

                    payload = await resp.json(content_type=None)
                    amount = self._extract_amount(payload)
                    if amount is None:
                        log.warning("Не удалось распарсить ответ %s: %s", path, payload)
                        raise MarketApiError("Неожиданная структура ответа API")

                    currency = self._currency
                    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                        currency = payload["data"].get("currency") or currency
                    return InventoryValue(item_id=item_id, amount=amount, currency=currency, raw=payload)

            except RateLimitedError as exc:
                last_error = exc
                log.warning("Rate limit, ожидание %.1f с (попытка %d)", exc.retry_after, attempt + 1)
                if attempt < self._max_retries:
                    await asyncio.sleep(exc.retry_after)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = MarketApiError(f"Сетевая ошибка: {exc}")
                log.warning("Сетевая ошибка (попытка %d): %s", attempt + 1, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
            except MarketApiError:
                raise                                                            

        raise MarketApiError(f"Не удалось получить оценку инвентаря {path}: {last_error}")

    @staticmethod
    async def _error_text(resp: aiohttp.ClientResponse) -> str:
                                                                         
        try:
            data = await resp.json(content_type=None)
        except Exception:                
            return ""
        if isinstance(data, dict):
            errors = data.get("errors")
            if errors:
                return "; ".join(str(e) for e in errors)
        return ""

    @staticmethod
    def _extract_amount(payload: Any) -> float | None:
                                                                         
        candidates: list[Any] = []

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                for key in ("amount", "value", "total", "sum", "price", "inventory_value", "inventoryValue", "totalValue"):
                    if key in node:
                        candidates.append(node[key])
                for key in ("data", "result", "response", "body"):
                    if key in node:
                        collect(node[key])
            elif isinstance(node, list):
                for item in node[:5]:
                    collect(item)

        collect(payload)

        for c in candidates:
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                return float(c)
            if isinstance(c, str):
                try:
                    return float(c.replace(",", ".").replace(" ", "").replace("₽", "").replace("$", ""))
                except ValueError:
                    continue
        return None

                                                                          

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
