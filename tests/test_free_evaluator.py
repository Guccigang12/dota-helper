import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.free_evaluator import (
    FreeInventoryEvaluator,
    DotaPriceManager,
    FreeProxyProvider,
    normalize_steam_id64,
    build_inventory_url,
    normalize_proxy_dict,
)
from core.chat_translator import ChatVisionTranslator, get_screen_dpi_scale
from core.steam_coplay import SteamCoplayManager


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

    @patch("requests.get")
    def test_price_manager(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "success": True,
            "items": [
                {"market_hash_name": "Dragonclaw Hook", "price": "145.50"},
                {"market_hash_name": "Arcana Blade", "price": "29.99"},
            ],
        }
        mock_get.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "prices_cache.json"
            pm = DotaPriceManager(currency="USD", cache_path=cache_file)
            prices = pm.load_prices()
            self.assertEqual(len(prices), 2)
            self.assertGreater(pm.get_price("Dragonclaw Hook"), 50.0)
            self.assertEqual(pm.get_price("Arcana Blade"), 29.99)
            self.assertEqual(pm.get_price("NonExistentItem12345"), 0.0)
            # Ensure mock was called because cache did not exist initially
            mock_get.assert_called_once()

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

    @unittest.skipUnless(sys.platform == "win32", "Требуется Windows GUI")
    def test_dpi_scaling_helper(self):
        scale = get_screen_dpi_scale()
        self.assertIsInstance(scale, float)
        self.assertGreater(scale, 0.0)

    def test_parse_json_messages(self):
        # 1. Clean pure JSON
        clean_json = '{"messages": [{"source": "中单", "kind": "Сленг", "translation": "мидер"}]}'
        msgs = ChatVisionTranslator._parse_json_messages(clean_json)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].source, "中单")
        self.assertEqual(msgs[0].kind, "Сленг")
        self.assertEqual(msgs[0].translation, "мидер")

        # 2. Markdown fenced code block
        md_json = """Here is the translation:
```json
{
  "messages": [
    {"source": "bkb", "kind": "Сленг", "translation": "БКБ"},
    {"source": "ff", "kind": "Сленг", "translation": "сдаемся"}
  ]
}
```
Thanks!"""
        msgs_md = ChatVisionTranslator._parse_json_messages(md_json)
        self.assertEqual(len(msgs_md), 2)
        self.assertEqual(msgs_md[0].source, "bkb")
        self.assertEqual(msgs_md[0].translation, "БКБ")
        self.assertEqual(msgs_md[1].source, "ff")
        self.assertEqual(msgs_md[1].translation, "сдаемся")

        # 3. Broken JSON containing braces
        broken_json = '{"messages": [ invalid syntax }'
        msgs_broken = ChatVisionTranslator._parse_json_messages(broken_json)
        self.assertEqual(msgs_broken, [])

        # 4. Fallback: plaintext without JSON braces
        plain_text = "Пушьте мид быстрее"
        msgs_plain = ChatVisionTranslator._parse_json_messages(plain_text)
        self.assertEqual(len(msgs_plain), 1)
        self.assertEqual(msgs_plain[0].source, "Чат")
        self.assertEqual(msgs_plain[0].kind, "Текст")
        self.assertEqual(msgs_plain[0].translation, "Пушьте мид быстрее")

        # 5. Empty content
        self.assertEqual(ChatVisionTranslator._parse_json_messages(""), [])

    def test_compute_delta_lobby(self):
        manager = SteamCoplayManager(dll_path="nonexistent.dll")
        # Baseline contains old players from prior match
        manager.baseline_snapshot = {
            1001: 1000,
            1002: 1000,
            1003: 1000,
        }

        # snap1 at HERO_SELECTION: player 1004 has joined with newer timestamp
        snap1 = {
            1001: 1000,
            1002: 1000,
            1003: 1000,
            1004: 2000,
        }

        # snap2 after 3.5s delay: player 1005 has joined
        snap2 = {
            1001: 1000,
            1002: 1000,
            1003: 1000,
            1004: 2000,
            1005: 2050,
        }

        # Local player is 1001 (must be excluded from lobby candidates)
        players = manager._compute_delta_lobby(snap1, snap2, local_steam_id="1001")

        # Verify self is excluded
        self.assertNotIn("1001", [p.steam_id for p in players])

        # Players 1004 and 1005 are true delta
        delta_players = [p for p in players if p.is_delta]
        self.assertEqual({p.steam_id for p in delta_players}, {"1004", "1005"})

        # Historical players (1002, 1003) added as fillers have is_delta=False
        fillers = [p for p in players if not p.is_delta]
        self.assertEqual({p.steam_id for p in fillers}, {"1002", "1003"})

        # Empty baseline test: delta is determined strictly between snap1 and snap2
        manager.baseline_snapshot = {}
        snap_a = {2001: 500, 2002: 500}
        snap_b = {2001: 500, 2002: 500, 2003: 600}
        res_empty_base = manager._compute_delta_lobby(snap_a, snap_b, local_steam_id="9999")
        # 2003 was added in snap_b relative to snap_a -> is_delta=True
        delta_ids = [p.steam_id for p in res_empty_base if p.is_delta]
        filler_ids = [p.steam_id for p in res_empty_base if not p.is_delta]
        self.assertIn("2003", delta_ids)
        self.assertIn("2001", filler_ids)
        self.assertIn("2002", filler_ids)


if __name__ == "__main__":
    unittest.main()
