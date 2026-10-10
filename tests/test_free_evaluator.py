import gc
import os
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.free_evaluator import (
    FreeInventoryEvaluator,
    FreeEvaluator,
    DotaPriceManager,
    FreeProxyProvider,
    InventoryValueResult,
    normalize_steam_id64,
    build_inventory_url,
    normalize_proxy_dict,
)
from core.chat_translator import get_screen_dpi_scale


class TestFreeInventoryEvaluator(unittest.TestCase):
    def test_steam_id_normalization(self):
        # 64-bit ID stays untouched
        self.assertEqual(normalize_steam_id64("76561198012345678"), "76561198012345678")
        # 32-bit account ID converted to SteamID64
        sid32 = 12345678
        expected = str(12345678 + 76561197960265728)
        self.assertEqual(normalize_steam_id64(sid32), expected)
        self.assertEqual(normalize_steam_id64(str(sid32)), expected)

    def test_build_inventory_url(self):
        sid = "76561198012345678"
        # Standard Steam Community URL
        self.assertEqual(
            build_inventory_url(sid),
            f"https://steamcommunity.com/inventory/{sid}/570/2?l=english&count=2000",
        )
        # Cloudflare Worker URL
        worker = "https://my-worker.workers.dev"
        self.assertEqual(
            build_inventory_url(sid, worker),
            f"https://my-worker.workers.dev/inventory/{sid}/570/2?l=english&count=2000",
        )
        # Template URL
        template = "https://custom-proxy.com/api/{steam_id}/dota"
        self.assertEqual(
            build_inventory_url(sid, template),
            f"https://custom-proxy.com/api/{sid}/dota",
        )

    def test_proxy_normalization(self):
        self.assertIsNone(normalize_proxy_dict(""))
        # HTTP proxy
        p = normalize_proxy_dict("http://127.0.0.1:8080")
        self.assertEqual(p, {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"})
        # SOCKS5 proxy
        p_socks = normalize_proxy_dict("socks5://127.0.0.1:10808")
        self.assertEqual(p_socks, {"http": "socks5://127.0.0.1:10808", "https": "socks5://127.0.0.1:10808"})
        # Bare host:port
        p_bare = normalize_proxy_dict("127.0.0.1:8080")
        self.assertEqual(p_bare, {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"})

    def test_price_manager(self):
        pm = DotaPriceManager(currency="USD")
        prices = pm.load_prices()
        self.assertGreater(len(prices), 10000)
        self.assertGreater(pm.get_price("Dragonclaw Hook"), 50.0)
        self.assertEqual(pm.get_price("NonExistentItem12345"), 0.0)

    def test_inventory_calculation_and_05_dollar_filter(self):
        db_path = Path("test_inv_cache.db")
        if db_path.exists():
            try:
                os.remove(db_path)
            except Exception:
                pass

        evaluator = FreeInventoryEvaluator(
            currency="USD",
            min_item_price=0.5,
            db_path=db_path,
        )
        evaluator.price_manager._prices = {
            "Expensive Hook": 250.0,
            "Arcana Blade": 20.0,
            "Half Dollar Item": 0.50,
            "Cheap Trash": 0.20,
            "Pennies Trash": 0.03,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "assets": [
                {"classid": "1", "instanceid": "0", "amount": "1"},  # 250.0
                {"classid": "2", "instanceid": "0", "amount": "2"},  # 20.0 * 2 = 40.0
                {"classid": "3", "instanceid": "0", "amount": "4"},  # 0.50 * 4 = 2.0 (>= 0.50, included)
                {"classid": "4", "instanceid": "0", "amount": "10"}, # 0.20 * 10 (< 0.50, excluded)
                {"classid": "5", "instanceid": "0", "amount": "1"},  # non-tradable
            ],
            "descriptions": [
                {"classid": "1", "instanceid": "0", "market_hash_name": "Expensive Hook", "tradable": 1},
                {"classid": "2", "instanceid": "0", "market_hash_name": "Arcana Blade", "tradable": 1},
                {"classid": "3", "instanceid": "0", "market_hash_name": "Half Dollar Item", "tradable": 1},
                {"classid": "4", "instanceid": "0", "market_hash_name": "Cheap Trash", "tradable": 1},
                {"classid": "5", "instanceid": "0", "market_hash_name": "Expensive Hook", "tradable": 0},
            ],
        }
        session = MagicMock()
        session.get.return_value = mock_response

        res = evaluator._fetch_and_calculate_inventory(session, "76561198000000001")
        self.assertEqual(res.status, "OK")
        # 250*1 + 20*2 + 0.50*4 = 292.0
        # Items with price < 0.5 ($0.20 Cheap Trash) and non-tradable Hook are ignored
        self.assertEqual(res.value_usd, 292.0)
        self.assertEqual(res.valued_items_count, 1 + 2 + 4)
        self.assertEqual(res.items_count, 5)

        # Cache saving and retrieval test
        evaluator.save_cached_result(res)
        cached = evaluator.get_cached_result("76561198000000001")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, "CACHED")
        self.assertEqual(cached.value_usd, 292.0)
        self.assertTrue(cached.cached)

        # 403 privacy test -> [Скрыт]
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.ok = False
        session.get.return_value = mock_403
        res_403 = evaluator._fetch_and_calculate_inventory(session, "76561198000000002")
        self.assertEqual(res_403.status, "HIDDEN")
        self.assertEqual(res_403.display_text, "[Скрыт]")

        # Empty inventory test -> [Скрыт]
        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.ok = True
        mock_empty.json.return_value = {"assets": [], "descriptions": []}
        session.get.return_value = mock_empty
        res_empty = evaluator._fetch_and_calculate_inventory(session, "76561198000000003")
        self.assertEqual(res_empty.status, "HIDDEN")
        self.assertEqual(res_empty.display_text, "[Скрыт]")

        del evaluator
        gc.collect()
        if db_path.exists():
            try:
                os.remove(db_path)
            except Exception:
                pass

    def test_free_proxy_provider(self):
        provider = FreeProxyProvider()
        self.assertTrue(hasattr(provider, "get_next_proxy"))
        self.assertTrue(hasattr(provider, "fetch_proxies"))

    def test_dpi_scaling_helper(self):
        scale = get_screen_dpi_scale()
        self.assertIsInstance(scale, float)
        self.assertGreater(scale, 0.0)


if __name__ == "__main__":
    unittest.main()
