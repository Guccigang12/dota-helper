"""
Free Steam Inventory Evaluator Module for Dota 2.
100% Free inventory checker without API keys or paid subscriptions.

Key components:
1. DotaPriceManager:
   - Fetches public Market Dota 2 price dump:
     https://market.dota2.net/api/v2/prices/USD.json (or RUB.json).
   - Local JSON cache (prices_cache.json) with 6-hour TTL.
   - O(1) in-memory price lookup: {market_hash_name: float(price)}.
2. FreeProxyProvider:
   - Automatically loads live HTTPS proxies from Proxyscrape API:
     https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&ssl=yes
   - Validates proxy responses (rejects HTML block pages).
3. Steam Community Inventory Client:
   - Fetches https://steamcommunity.com/inventory/{steam_id}/570/2?l=english&count=2000
   - Pagination support up to 5 pages via more_items & last_assetid.
   - Guaranteed official Steam 403 protection (proxy 403 does not pollute cache as HIDDEN).
   - Cloudflare Worker and HTTP/SOCKS5 proxy support.
4. Pricing & Filtering:
   - Only counts tradable items (tradable == 1).
   - Filters out items below min_item_price ($0.5 default).
5. Queue & Caching:
   - SQLite cache with composite cache_key ({steam_id}_{currency}, 12-hour TTL).
   - Rate limit 429 backoff with max 3 retries.
   - Clean _in_queue_set synchronization.
   - Streaming on_player_evaluated callback per player.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import requests

logger = logging.getLogger("DotaHelper.FreeEvaluator")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "WebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

STEAM_ID64_BASE = 76561197960265728
DEFAULT_PRICE_CACHE_PATH = Path("prices_cache.json")
DEFAULT_PRICE_CACHE_TTL_HOURS = 6.0
DEFAULT_INVENTORY_CACHE_DB = Path("inventory_cache.db")
DEFAULT_INVENTORY_CACHE_TTL_HOURS = 12.0
DEFAULT_MIN_ITEM_PRICE = 0.5  # Items cheaper than $0.5 are excluded
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.5


def normalize_steam_id64(steam_id: str | int) -> str:
    """Normalize 32-bit Account ID or 64-bit Steam ID into SteamID64 string."""
    try:
        val = int(str(steam_id).strip())
        if val < STEAM_ID64_BASE:
            val += STEAM_ID64_BASE
        return str(val)
    except (ValueError, TypeError):
        return str(steam_id).strip()


def build_inventory_url(steam_id: str, custom_url: str = "") -> str:
    """
    Construct Steam Community inventory URL or Cloudflare Worker endpoint:
    {worker_url}/inventory/{steam_id}/570/2?l=english&count=2000
    """
    sid64 = normalize_steam_id64(steam_id)
    if not custom_url or not custom_url.strip():
        return f"https://steamcommunity.com/inventory/{sid64}/570/2?l=english&count=2000"

    clean = custom_url.strip()
    if "{steam_id}" in clean:
        return clean.format(steam_id=sid64)

    base = clean.rstrip("/")
    if base.endswith("/inventory"):
        return f"{base}/{sid64}/570/2?l=english&count=2000"
    return f"{base}/inventory/{sid64}/570/2?l=english&count=2000"


def normalize_proxy_dict(proxy_str: str) -> Optional[Dict[str, str]]:
    """Format proxy string into requests proxies dictionary."""
    if not proxy_str or not proxy_str.strip():
        return None
    p = proxy_str.strip()
    if p.startswith("http://") or p.startswith("https://"):
        if "workers.dev" in p or "/inventory" in p:
            return None
    if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://") or p.startswith("socks5h://")):
        p = f"http://{p}"
    return {"http": p, "https": p}


@dataclass
class InventoryValueResult:
    steam_id: str
    value_usd: Optional[float]
    status: str  # "OK", "HIDDEN", "CACHED", "QUEUED", "ERROR", "RATE_LIMIT"
    cached: bool = False
    error_message: Optional[str] = None
    updated_at: float = field(default_factory=time.time)
    currency: str = "USD"
    items_count: int = 0
    valued_items_count: int = 0
    top_items: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        symbol = "₽" if self.currency.upper() == "RUB" else "$"
        if self.status in ("OK", "CACHED"):
            if self.value_usd is not None:
                if symbol == "$":
                    return f"${self.value_usd:,.2f}"
                return f"{self.value_usd:,.2f} ₽"
            return f"{symbol}0.00" if symbol == "$" else f"0.00 {symbol}"
        elif self.status == "HIDDEN":
            return "[Скрыт]"
        elif self.status == "QUEUED":
            return "⏳ В очереди..."
        elif self.status in ("ERROR", "RATE_LIMIT"):
            return "[Ошибка]"
        elif self.status == "NO_TOKEN":
            return "[Без токена]"
        return "—"


class DotaPriceManager:
    """
    Downloads and caches Market Dota 2 price dump (USD.json or RUB.json).
    Maintains O(1) in-memory dictionary of prices.
    Refreshes cache if missing or older than 6 hours.
    """

    def __init__(
        self,
        currency: str = "USD",
        cache_path: Path = DEFAULT_PRICE_CACHE_PATH,
        cache_ttl_hours: float = DEFAULT_PRICE_CACHE_TTL_HOURS,
    ) -> None:
        self.currency = currency.upper().strip() or "USD"
        self.cache_path = cache_path
        self.cache_ttl_seconds = cache_ttl_hours * 3600.0
        self._prices: Dict[str, float] = {}
        self._last_loaded: float = 0.0
        self._lock = threading.Lock()

    def set_currency(self, currency: str) -> None:
        """Switch currency (USD/RUB) and reload prices."""
        clean = currency.upper().strip() or "USD"
        if clean != self.currency:
            self.currency = clean
            self.load_prices(force_refresh=True)

    def get_price(self, market_hash_name: str) -> float:
        """Fast O(1) in-memory price lookup."""
        return self._prices.get(market_hash_name, 0.0)

    @property
    def total_cached_items(self) -> int:
        return len(self._prices)

    def load_prices(self, force_refresh: bool = False) -> Dict[str, float]:
        """Load prices from local cache or download fresh dump if expired."""
        with self._lock:
            # 1. Try local cache if not forcing refresh
            if not force_refresh and self.cache_path.exists():
                try:
                    mtime = self.cache_path.stat().st_mtime
                    age = time.time() - mtime
                    if age < self.cache_ttl_seconds:
                        raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                        cached_curr = raw.get("currency", "USD").upper()
                        if cached_curr == self.currency:
                            prices = raw.get("prices", {})
                            if prices:
                                self._prices = prices
                                self._last_loaded = time.time()
                                logger.info(
                                    "Loaded %d Dota 2 prices from local cache (%s, age %.1f hours).",
                                    len(self._prices),
                                    self.currency,
                                    age / 3600.0,
                                )
                                return self._prices
                except Exception as e:
                    logger.warning("Failed reading prices_cache.json: %s. Re-downloading dump...", e)

            # 2. Download fresh dump from Market Dota 2
            logger.info("Fetching fresh Market Dota 2 price dump (%s)...", self.currency)
            downloaded = self._download_price_dump()
            if downloaded:
                self._prices = downloaded
                self._last_loaded = time.time()
                self._save_cache_file(downloaded)
                return self._prices

            # 3. Fallback to existing stale cache if network failed
            if self.cache_path.exists() and not self._prices:
                try:
                    raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    prices = raw.get("prices", {})
                    if prices:
                        self._prices = prices
                        logger.warning(
                            "Network download failed; using stale cache (%d items).",
                            len(self._prices),
                        )
                        return self._prices
                except Exception:
                    pass

            return self._prices

    def _download_price_dump(self) -> Dict[str, float]:
        """Execute GET request to Market Dota 2 API v2 price dump."""
        url = f"https://market.dota2.net/api/v2/prices/{self.currency}.json"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15.0)
            if not resp.ok:
                logger.error("Market Dota 2 prices returned HTTP %s", resp.status_code)
                return {}

            data = resp.json()
            items = data.get("items", [])
            if not items:
                logger.warning("Empty items list received from Market Dota 2.")
                return {}

            prices: Dict[str, float] = {}
            for it in items:
                name = it.get("market_hash_name")
                raw_p = it.get("price")
                if name and raw_p is not None:
                    try:
                        prices[name] = float(raw_p)
                    except (ValueError, TypeError):
                        pass

            logger.info("Successfully downloaded %d prices for %s.", len(prices), self.currency)
            return prices

        except Exception as e:
            logger.error("Error downloading Market Dota 2 prices dump: %s", e)
            return {}

    def _save_cache_file(self, prices: Dict[str, float]) -> None:
        """Save parsed prices with metadata to prices_cache.json."""
        try:
            payload = {
                "currency": self.currency,
                "updated_at": time.time(),
                "items_count": len(prices),
                "prices": prices,
            }
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
            logger.info("Saved %d prices to %s.", len(prices), self.cache_path.name)
        except Exception as e:
            logger.error("Failed to write %s: %s", self.cache_path.name, e)


class FreeProxyProvider:
    """
    Fetches and rotates free live HTTPS proxies from Proxyscrape API.
    Used when direct Steam Community requests time out due to ISP blocks.
    """

    PROXYSCRAPE_URL = (
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&ssl=yes"
    )

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._proxies: List[str] = []
        self._last_fetched: float = 0.0
        self._current_idx: int = 0
        self._lock = threading.Lock()

    def fetch_proxies(self, force: bool = False) -> List[str]:
        with self._lock:
            now = time.time()
            if not force and self._proxies and (now - self._last_fetched < self.ttl_seconds):
                return list(self._proxies)

            try:
                logger.info("Fetching free proxy list from Proxyscrape...")
                resp = requests.get(self.PROXYSCRAPE_URL, timeout=8.0)
                if resp.ok:
                    lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
                    if lines:
                        self._proxies = lines
                        self._last_fetched = now
                        self._current_idx = 0
                        logger.info("Fetched %d free proxies from Proxyscrape.", len(lines))
                        return list(self._proxies)
            except Exception as e:
                logger.warning("Failed to fetch proxies from Proxyscrape: %s", e)

            return list(self._proxies)

    def get_next_proxy(self) -> Optional[str]:
        need_fetch = False
        with self._lock:
            if not self._proxies or (time.time() - self._last_fetched > self.ttl_seconds):
                need_fetch = True

        if need_fetch:
            self.fetch_proxies(force=True)

        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._current_idx % len(self._proxies)]
            self._current_idx += 1
            if not (proxy.startswith("http://") or proxy.startswith("https://")):
                return f"http://{proxy}"
            return proxy


class FreeInventoryEvaluator:
    """
    100% Free Steam inventory evaluator without API keys or paid subscriptions.
    Evaluates player inventories using Market Dota 2 price database and Steam Community API.
    Features:
    - 6-hour prices dump cache (prices_cache.json).
    - SQLite 12-hour inventory cache with currency-composite key (inventory_cache.db).
    - Steam pagination loop (up to 5 pages) via more_items & last_assetid.
    - Official Steam 403 validation (proxy 403 errors are not cached as HIDDEN).
    - Rate limit 429 backoff with max 3 retries.
    - Cloudflare Worker and HTTP/SOCKS5 proxy support.
    - Tradable items filtering and threshold >= $0.5.
    """

    def __init__(
        self,
        currency: str = "USD",
        min_item_price: float = DEFAULT_MIN_ITEM_PRICE,
        cache_ttl_hours: float = DEFAULT_INVENTORY_CACHE_TTL_HOURS,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
        proxy_url: str = "",
        cloudflare_worker_url: str = "",
        use_auto_free_proxies: bool = False,
        db_path: Path = DEFAULT_INVENTORY_CACHE_DB,
        prices_cache_path: Path = DEFAULT_PRICE_CACHE_PATH,
        # Backward-compatibility kwargs:
        proxy: str = "",
        reverse_proxy_url: str = "",
        rate_limit_delay: Optional[float] = None,
    ) -> None:
        self.currency = currency.upper().strip() or "USD"
        self.min_item_price = float(min_item_price)
        self.cache_ttl_seconds = cache_ttl_hours * 3600.0
        self.request_interval_seconds = float(rate_limit_delay if rate_limit_delay is not None else request_interval_seconds)
        self.proxy_url = (proxy_url or proxy).strip()
        self.cloudflare_worker_url = (cloudflare_worker_url or reverse_proxy_url).strip()
        self.use_auto_free_proxies = bool(use_auto_free_proxies)
        self.db_path = db_path

        # Price database manager
        self.price_manager = DotaPriceManager(
            currency=self.currency,
            cache_path=prices_cache_path,
        )

        # Free proxy provider
        self.proxy_provider = FreeProxyProvider()

        self._queue: queue.Queue[str] = queue.Queue()
        self._in_queue_set: set[str] = set()
        self._retry_counts: Dict[str, int] = {}
        self._lock = threading.Lock()

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._last_request_time = 0.0

        # Streaming callback per player: Callable[[InventoryValueResult], None]
        self.on_player_evaluated: Optional[Callable[[InventoryValueResult], None]] = None

        self._init_database()

    # Aliases for backward compatibility
    @property
    def proxy(self) -> str:
        return self.proxy_url

    @proxy.setter
    def proxy(self, val: str) -> None:
        self.proxy_url = val.strip()

    @property
    def reverse_proxy_url(self) -> str:
        return self.cloudflare_worker_url

    @reverse_proxy_url.setter
    def reverse_proxy_url(self, val: str) -> None:
        self.cloudflare_worker_url = val.strip()

    @property
    def rate_limit_delay(self) -> float:
        return self.request_interval_seconds

    @rate_limit_delay.setter
    def rate_limit_delay(self, val: float) -> None:
        self.request_interval_seconds = float(val)

    def update_settings(
        self,
        currency: Optional[str] = None,
        min_item_price: Optional[float] = None,
        proxy_url: Optional[str] = None,
        cloudflare_worker_url: Optional[str] = None,
        use_auto_free_proxies: Optional[bool] = None,
        request_interval_seconds: Optional[float] = None,
        # backward compatibility:
        proxy: Optional[str] = None,
        reverse_proxy_url: Optional[str] = None,
        rate_limit_delay: Optional[float] = None,
    ) -> None:
        """Update evaluator configuration at runtime without restarts."""
        with self._lock:
            if currency is not None:
                new_curr = currency.upper().strip() or "USD"
                if new_curr != self.currency:
                    self.currency = new_curr
                    self.price_manager.set_currency(new_curr)
            if min_item_price is not None:
                self.min_item_price = float(min_item_price)
            if proxy_url is not None:
                self.proxy_url = proxy_url.strip()
            elif proxy is not None:
                self.proxy_url = proxy.strip()
            if cloudflare_worker_url is not None:
                self.cloudflare_worker_url = cloudflare_worker_url.strip()
            elif reverse_proxy_url is not None:
                self.cloudflare_worker_url = reverse_proxy_url.strip()
            if use_auto_free_proxies is not None:
                self.use_auto_free_proxies = bool(use_auto_free_proxies)
            if request_interval_seconds is not None:
                self.request_interval_seconds = float(request_interval_seconds)
            elif rate_limit_delay is not None:
                self.request_interval_seconds = float(rate_limit_delay)

            logger.info(
                "Updated FreeInventoryEvaluator settings: currency=%s, min_item_price=%.2f, proxy=%s, worker=%s, auto_proxies=%s",
                self.currency,
                self.min_item_price,
                self.proxy_url or "none",
                self.cloudflare_worker_url or "none",
                self.use_auto_free_proxies,
            )

    def set_api_token(self, _token: str) -> None:
        """Compatibility dummy."""
        pass

    @property
    def api_token(self) -> str:
        """Compatibility dummy."""
        return "FREE_NO_TOKEN_REQUIRED"

    @api_token.setter
    def api_token(self, _val: str) -> None:
        pass

    def _init_database(self) -> None:
        """Initialize SQLite cache database with composite cache_key."""
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(inventory_cache)")
                cols = {col[1] for col in cursor.fetchall()}
                if cols and "cache_key" not in cols:
                    # Upgrade legacy table where steam_id alone was primary key
                    cursor.execute("DROP TABLE IF EXISTS inventory_cache")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS inventory_cache (
                        cache_key TEXT PRIMARY KEY,
                        steam_id TEXT,
                        currency TEXT,
                        value_usd REAL,
                        status TEXT,
                        error_message TEXT,
                        updated_at REAL,
                        items_count INTEGER DEFAULT 0,
                        valued_items_count INTEGER DEFAULT 0
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("Failed to initialize SQLite cache db: %s", e)

    def get_cached_result(self, steam_id: str, currency: Optional[str] = None) -> Optional[InventoryValueResult]:
        """Fetch result from SQLite cache if not expired (< 12 hours) matching currency."""
        sid64 = normalize_steam_id64(steam_id)
        curr = (currency or self.currency).upper()
        cache_key = f"{sid64}_{curr}"
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT steam_id, value_usd, status, error_message, updated_at,
                           currency, items_count, valued_items_count
                    FROM inventory_cache WHERE cache_key = ?
                    """,
                    (cache_key,),
                )
                row = cursor.fetchone()
                if row:
                    sid, val, status, err, updated_at, c, it_count, val_count = row
                    age = time.time() - updated_at
                    if age < self.cache_ttl_seconds:
                        return InventoryValueResult(
                            steam_id=sid,
                            value_usd=val,
                            status="CACHED" if status == "OK" else status,
                            cached=True,
                            error_message=err,
                            updated_at=updated_at,
                            currency=c or curr,
                            items_count=it_count or 0,
                            valued_items_count=val_count or 0,
                        )
            finally:
                conn.close()
        except Exception as e:
            logger.error("Error reading cache for %s: %s", cache_key, e)
        return None

    def save_cached_result(self, result: InventoryValueResult) -> None:
        """Store evaluated result into SQLite cache."""
        sid64 = normalize_steam_id64(result.steam_id)
        curr = (result.currency or self.currency).upper()
        cache_key = f"{sid64}_{curr}"
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO inventory_cache (
                        cache_key, steam_id, currency, value_usd, status, error_message, updated_at,
                        items_count, valued_items_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cache_key,
                        sid64,
                        curr,
                        result.value_usd,
                        result.status,
                        result.error_message,
                        result.updated_at,
                        result.items_count,
                        result.valued_items_count,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("Error saving cache for %s: %s", cache_key, e)

    def start(self) -> None:
        """Start price database loading and background queue worker."""
        if self._running:
            return

        def _init_prices() -> None:
            self.price_manager.load_prices()

        threading.Thread(target=_init_prices, name="DotaPriceLoader", daemon=True).start()

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="FreeEvaluatorWorker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("FreeInventoryEvaluator background queue worker started.")

    def stop(self) -> None:
        """Stop background worker cleanly."""
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._queue.put("__STOP__")
            self._worker_thread.join(timeout=2.5)
            self._worker_thread = None
        logger.info("FreeInventoryEvaluator stopped.")

    def evaluate_players(self, steam_ids: List[str]) -> None:
        """
        Evaluate a list of player SteamIDs (e.g. 9 lobby players).
        Cached entries fire the callback immediately.
        Uncached entries are queued and processed with soft rate-limiting (1.5s).
        """
        for raw_sid in steam_ids:
            if not raw_sid:
                continue

            sid64 = normalize_steam_id64(raw_sid)

            # 1. Check local SQLite cache first (instant)
            cached = self.get_cached_result(sid64)
            if cached:
                logger.info("Using cached inventory for %s: %s", sid64, cached.display_text)
                if self.on_player_evaluated:
                    try:
                        self.on_player_evaluated(cached)
                    except Exception as e:
                        logger.error("Error in on_player_evaluated callback: %s", e)
                continue

            # 2. Add to queue
            with self._lock:
                if sid64 not in self._in_queue_set:
                    self._in_queue_set.add(sid64)
                    self._queue.put(sid64)

                    queued_res = InventoryValueResult(
                        steam_id=sid64,
                        value_usd=None,
                        status="QUEUED",
                        currency=self.currency,
                    )
                    if self.on_player_evaluated:
                        try:
                            self.on_player_evaluated(queued_res)
                        except Exception as e:
                            logger.error("Error in on_player_evaluated callback: %s", e)

    def _worker_loop(self) -> None:
        """Queue processor enforcing rate-limit delay between Steam requests."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })

        while self._running:
            try:
                sid64 = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if sid64 == "__STOP__":
                break

            try:
                # Ensure price database has been loaded
                if not self.price_manager.total_cached_items:
                    self.price_manager.load_prices()

                # Rate-limiting delay between requests (default 1.5s)
                elapsed = time.time() - self._last_request_time
                if elapsed < self.request_interval_seconds:
                    time.sleep(self.request_interval_seconds - elapsed)

                # Configure proxies dynamically per request
                proxies = normalize_proxy_dict(self.proxy_url)
                session.proxies = proxies if proxies else {}

                # Execute inventory fetch & price calculation
                result = self._fetch_and_calculate_inventory(session, sid64)
                self._last_request_time = time.time()

                # Handle 429 Too Many Requests: backoff pause and retry (up to 3 times)
                if result.status == "RATE_LIMITED":
                    retries = self._retry_counts.get(sid64, 0) + 1
                    self._retry_counts[sid64] = retries
                    if retries <= 3:
                        backoff = (2.0 ** retries) * 2.0  # 4s, 8s, 16s
                        logger.warning(
                            "Steam rate-limit (429) hit for %s. Retry %d/3 after %.1fs...",
                            sid64,
                            retries,
                            backoff,
                        )
                        time.sleep(backoff)
                        with self._lock:
                            self._queue.put(sid64)
                        continue
                    else:
                        logger.error("Max retries exceeded for %s on 429 rate limit.", sid64)
                        result.status = "RATE_LIMIT"
                        result.error_message = "Превышен лимит запросов Steam (429)"
                        self._retry_counts.pop(sid64, None)

                # Reset retry counter on completion or final status
                self._retry_counts.pop(sid64, None)

                # Save to cache: only OK or genuine official Steam HIDDEN
                if result.status in ("OK", "HIDDEN"):
                    self.save_cached_result(result)

                # Stream result to UI via callback
                if self.on_player_evaluated:
                    try:
                        self.on_player_evaluated(result)
                    except Exception as e:
                        logger.error("Error in on_player_evaluated callback: %s", e)

            except Exception as e:
                logger.error("Unexpected error in FreeInventoryEvaluator worker for %s: %s", sid64, e)
                self._retry_counts.pop(sid64, None)
            finally:
                with self._lock:
                    if sid64 not in self._retry_counts:
                        self._in_queue_set.discard(sid64)
                self._queue.task_done()

    def _fetch_single_page(
        self,
        session: requests.Session,
        url: str,
        steam_id: str,
    ) -> tuple[Optional[requests.Response], bool]:
        """
        Executes request for an inventory page.
        Returns (response, is_guaranteed_steam).
        Validates proxy responses, rejecting HTML error pages.
        """
        response = None
        is_guaranteed_steam = False

        # Attempt 1: direct or configured proxy
        try:
            logger.info("Fetching Steam inventory page for %s: %s", steam_id, url)
            response = session.get(url, timeout=12.0)
            if not self.proxy_url:
                is_guaranteed_steam = True
            else:
                ct = response.headers.get("Content-Type", "").lower()
                if response.status_code == 200 or "steam" in response.text.lower() or "application/json" in ct:
                    is_guaranteed_steam = True
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            logger.warning("Steam request failed for %s (%s).", steam_id, exc)

        # Attempt 2: fallback to auto free proxies if enabled
        if (response is None or not response.ok) and self.use_auto_free_proxies:
            for attempt in range(3):
                free_proxy = self.proxy_provider.get_next_proxy()
                if not free_proxy:
                    break
                logger.info("Retrying %s via free proxy: %s (attempt %d)...", steam_id, free_proxy, attempt + 1)
                try:
                    temp_proxies = {"http": free_proxy, "https": free_proxy}
                    resp = session.get(url, proxies=temp_proxies, timeout=8.0)
                    if resp and resp.ok:
                        # Check if proxy returned HTML block page instead of JSON
                        ct = resp.headers.get("Content-Type", "").lower()
                        sample = resp.text[:200].lower()
                        if "text/html" in ct or "<html" in sample or "<!doctype" in sample:
                            logger.warning("Proxy %s returned HTML error page, rejecting...", free_proxy)
                            continue
                        response = resp
                        is_guaranteed_steam = True
                        logger.info("Success using free proxy %s for %s!", free_proxy, steam_id)
                        break
                    elif resp and resp.status_code == 403:
                        ct = resp.headers.get("Content-Type", "").lower()
                        if "text/html" in ct or "<html" in resp.text[:200].lower():
                            logger.warning("Proxy %s returned proxy 403 HTML page, rejecting...", free_proxy)
                            continue
                except Exception:
                    continue

        return response, is_guaranteed_steam

    def _fetch_and_calculate_inventory(
        self,
        session: requests.Session,
        steam_id: str,
    ) -> InventoryValueResult:
        """
        Fetch Steam Community inventory with pagination, parse items and calculate value.
        Supports Cloudflare Worker, custom proxy, and auto-fallback to Proxyscrape proxies.
        """
        target_base_url = build_inventory_url(
            steam_id=steam_id,
            custom_url=self.cloudflare_worker_url or (self.proxy_url if "workers.dev" in self.proxy_url else ""),
        )

        all_assets: List[dict] = []
        all_descriptions: List[dict] = []
        start_assetid: Optional[str] = None
        MAX_PAGES = 5

        try:
            for page_idx in range(MAX_PAGES):
                page_url = target_base_url
                if start_assetid:
                    sep = "&" if "?" in page_url else "?"
                    page_url = f"{page_url}{sep}start_assetid={start_assetid}"

                response, is_guaranteed_steam = self._fetch_single_page(session, page_url, steam_id)

                if response is None:
                    if page_idx == 0:
                        return InventoryValueResult(
                            steam_id=steam_id,
                            value_usd=None,
                            status="ERROR",
                            error_message="Таймаут Steam (укажите прокси или worker)",
                            currency=self.currency,
                        )
                    break

                # 1. Rate-limiting check
                if response.status_code == 429:
                    return InventoryValueResult(
                        steam_id=steam_id,
                        value_usd=None,
                        status="RATE_LIMITED",
                        error_message="Рейт-лимит Steam (429)",
                        currency=self.currency,
                    )

                # 2. Privacy check: 403 Forbidden
                if response.status_code == 403:
                    if is_guaranteed_steam:
                        return InventoryValueResult(
                            steam_id=steam_id,
                            value_usd=0.0,
                            status="HIDDEN",
                            error_message="Инвентарь скрыт настройками приватности",
                            currency=self.currency,
                        )
                    else:
                        # Proxy error 403: do NOT cache as HIDDEN
                        return InventoryValueResult(
                            steam_id=steam_id,
                            value_usd=None,
                            status="ERROR",
                            error_message="Ошибка прокси (403)",
                            currency=self.currency,
                        )

                if not response.ok:
                    return InventoryValueResult(
                        steam_id=steam_id,
                        value_usd=None,
                        status="ERROR",
                        error_message=f"HTTP {response.status_code}",
                        currency=self.currency,
                    )

                # 3. Parse JSON response
                try:
                    data = response.json()
                except Exception:
                    text_low = response.text.lower()
                    if ("private" in text_low or "hidden" in text_low) and is_guaranteed_steam:
                        return InventoryValueResult(
                            steam_id=steam_id,
                            value_usd=0.0,
                            status="HIDDEN",
                            currency=self.currency,
                        )
                    return InventoryValueResult(
                        steam_id=steam_id,
                        value_usd=None,
                        status="ERROR",
                        error_message="Некорректный JSON от Steam",
                        currency=self.currency,
                    )

                # Check if JSON payload indicates privacy or error
                if isinstance(data, dict):
                    if data.get("success") is False or data.get("error"):
                        err_msg = str(data.get("error", "")).lower()
                        if ("private" in err_msg or "hidden" in err_msg or "null" in err_msg) and is_guaranteed_steam:
                            return InventoryValueResult(
                                steam_id=steam_id,
                                value_usd=0.0,
                                status="HIDDEN",
                                currency=self.currency,
                            )
                        return InventoryValueResult(
                            steam_id=steam_id,
                            value_usd=None,
                            status="ERROR",
                            error_message=data.get("error", "Steam error"),
                            currency=self.currency,
                        )

                page_assets = data.get("assets") or []
                page_desc = data.get("descriptions") or []

                # If first page has 0 items, inventory is empty / hidden
                if page_idx == 0 and not page_assets:
                    return InventoryValueResult(
                        steam_id=steam_id,
                        value_usd=0.0,
                        status="HIDDEN",
                        currency=self.currency,
                        items_count=0,
                        valued_items_count=0,
                    )

                all_assets.extend(page_assets)
                all_descriptions.extend(page_desc)

                # Check pagination
                more_items = data.get("more_items")
                last_assetid = data.get("last_assetid")
                if not more_items or not last_assetid:
                    break
                start_assetid = str(last_assetid)
                time.sleep(0.3)

            # 4. Map descriptions by (classid, instanceid) and fallback by classid
            desc_map: Dict[tuple[str, str], dict] = {}
            class_map: Dict[str, dict] = {}
            for d in all_descriptions:
                cid = str(d.get("classid", ""))
                iid = str(d.get("instanceid", "0"))
                desc_map[(cid, iid)] = d
                if cid not in class_map:
                    class_map[cid] = d

            total_val = 0.0
            items_count = len(all_assets)
            valued_items_count = 0
            top_items: List[Dict[str, Any]] = []

            # 5. Calculate valuation
            for a in all_assets:
                cid = str(a.get("classid", ""))
                iid = str(a.get("instanceid", "0"))
                desc = desc_map.get((cid, iid)) or class_map.get(cid)
                if not desc:
                    continue

                # Only tradable items (tradable == 1)
                tradable = desc.get("tradable")
                if tradable != 1 and str(tradable) != "1":
                    continue

                market_hash_name = (
                    desc.get("market_hash_name")
                    or desc.get("market_name")
                    or desc.get("name")
                )
                if not market_hash_name:
                    continue

                price = self.price_manager.get_price(market_hash_name)

                # Skip items below minimum price threshold ($0.5 default)
                if price < self.min_item_price:
                    continue

                try:
                    amount = int(a.get("amount", 1))
                except (ValueError, TypeError):
                    amount = 1

                item_total = price * amount
                total_val += item_total
                valued_items_count += amount
                top_items.append({
                    "name": market_hash_name,
                    "price": price,
                    "amount": amount,
                    "total": item_total,
                })

            top_items.sort(key=lambda x: x["total"], reverse=True)

            logger.info(
                "Calculated inventory for %s: %s (counted %d items >= %.2f, total items: %d across pages)",
                steam_id,
                f"${total_val:,.2f}" if self.currency == "USD" else f"{total_val:,.2f} {self.currency}",
                valued_items_count,
                self.min_item_price,
                items_count,
            )

            return InventoryValueResult(
                steam_id=steam_id,
                value_usd=round(total_val, 2),
                status="OK",
                currency=self.currency,
                items_count=items_count,
                valued_items_count=valued_items_count,
                top_items=top_items[:10],
            )

        except Exception as e:
            logger.error("Error evaluating inventory for %s: %s", steam_id, e)
            return InventoryValueResult(
                steam_id=steam_id,
                value_usd=None,
                status="ERROR",
                error_message=f"Ошибка обработки: {e}",
                currency=self.currency,
            )


# Backward compatibility aliases
FreeEvaluator = FreeInventoryEvaluator
InventoryEvaluator = FreeInventoryEvaluator
LZTEvaluator = FreeInventoryEvaluator
