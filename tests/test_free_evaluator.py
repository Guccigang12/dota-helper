import gc
import os
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from core.free_evaluator import (
    FreeEvaluator,
    DotaPriceManager,
    InventoryValueResult,
    normalize_steam_id64,
    build_inventory_url,
    normalize_proxy_dict,
)
from core.lzt_evaluator import LZTEvaluator


class TestFreeEvaluator(unittest.TestCase):
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

    def test_inventory_calculation_and_filtering(self):
        db_path = Path("test_inv_cache.db")
        if db_path.exists():
            try:
                os.remove(db_path)
            except Exception:
                pass

        evaluator = FreeEvaluator(
            currency="USD",
            min_item_price=1.0,
            db_path=db_path,
        )
        evaluator.price_manager._prices = {
            "Expensive Hook": 250.0,
            "Arcana Blade": 20.0,
            "Cheap Item": 0.50,
            "Trash Item": 0.05,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "assets": [
                {"classid": "1", "instanceid": "0", "amount": "1"},
                {"classid": "2", "instanceid": "0", "amount": "2"},
                {"classid": "3", "instanceid": "0", "amount": "5"},  # cheap (<1.0)
                {"classid": "4", "instanceid": "0", "amount": "1"},  # non-tradable
            ],
            "descriptions": [
                {"classid": "1", "instanceid": "0", "market_hash_name": "Expensive Hook", "tradable": 1},
                {"classid": "2", "instanceid": "0", "market_hash_name": "Arcana Blade", "tradable": 1},
                {"classid": "3", "instanceid": "0", "market_hash_name": "Cheap Item", "tradable": 1},
                {"classid": "4", "instanceid": "0", "market_hash_name": "Expensive Hook", "tradable": 0},
            ],
        }
        session = MagicMock()
        session.get.return_value = mock_response

        res = evaluator._fetch_and_calculate_inventory(session, "76561198000000001")
        self.assertEqual(res.status, "OK")
        # 250*1 + 20*2 = 290.0 (Cheap Item $0.50 and untradable Hook are ignored)
        self.assertEqual(res.value_usd, 290.0)
        self.assertEqual(res.valued_items_count, 3)
        self.assertEqual(res.items_count, 4)

        # Cache saving and retrieval test
        evaluator.save_cached_result(res)
        cached = evaluator.get_cached_result("76561198000000001")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, "CACHED")
        self.assertEqual(cached.value_usd, 290.0)
        self.assertTrue(cached.cached)

        # 403 privacy test
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.ok = False
        session.get.return_value = mock_403
        res_403 = evaluator._fetch_and_calculate_inventory(session, "76561198000000002")
        self.assertEqual(res_403.status, "HIDDEN")
        self.assertEqual(res_403.display_text, "[Скрыт]")

        del evaluator
        gc.collect()
        if db_path.exists():
            try:
                os.remove(db_path)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
