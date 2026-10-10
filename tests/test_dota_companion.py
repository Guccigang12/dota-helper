import sys
import tempfile
import unittest
from pathlib import Path

from dota_companion.core.settings import Settings
from dota_companion.ocr.translator import Translator
from dota_companion.ocr.slang import enhance_text_with_slang
from dota_companion.ocr.capturer import ScreenCapturer


class TestDotaCompanion(unittest.TestCase):
    def test_settings_atomic_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "test_settings.json"
            s1 = Settings(
                ocr_enabled=True,
                translator_provider="gemini",
                gemini_api_key="test_key_123",
                master_volume=0.9,
            )
            s1.save(settings_path)

            self.assertTrue(settings_path.is_file())

            s2 = Settings()
            s2.load(settings_path)
            self.assertTrue(s2.ocr_enabled)
            self.assertEqual(s2.translator_provider, "gemini")
            self.assertEqual(s2.gemini_api_key, "test_key_123")
            self.assertAlmostEqual(s2.master_volume, 0.9)

    def test_settings_from_dict_type_safety(self):
        corrupted_data = {
            "ocr_enabled": "not_a_bool",  # bool("not_a_bool") -> True
            "font_size": "invalid_int",    # should not crash
            "master_volume": "invalid_float",  # should not crash
            "translator_provider": 12345,  # str(12345) -> "12345"
            "chat_region": {"x": 100, "y": 200, "width": 300, "height": 400},
        }
        s = Settings.from_dict(corrupted_data)
        self.assertEqual(s.chat_region.x, 100)
        self.assertEqual(s.chat_region.width, 300)
        # Invalid int and float fell back to defaults without raising runtime exception
        self.assertEqual(s.font_size, 14)
        self.assertEqual(s.master_volume, 0.8)
        self.assertEqual(s.translator_provider, "12345")

    def test_parse_vision_response(self):
        raw = "Оригинал: 中路推塔\nПеревод: Пушьте мид"
        orig, trans = Translator._parse_vision_response(raw)
        self.assertEqual(orig, "中路推塔")
        self.assertEqual(trans, "Пушьте мид")

        raw_multiline = "Original: b\nTranslation: Назад"
        orig, trans = Translator._parse_vision_response(raw_multiline)
        self.assertEqual(orig, "b")
        self.assertEqual(trans, "Назад")

    def test_slang_enhancement(self):
        text = "push mid fast"
        enhanced = enhance_text_with_slang(text)
        self.assertIsInstance(enhanced, str)
        self.assertTrue(len(enhanced) >= len(text))

    @unittest.skipUnless(sys.platform == "win32", "Требуется Windows GUI")
    def test_screen_capturer_thread_safety(self):
        capturer = ScreenCapturer()
        self.assertTrue(hasattr(capturer, "_lock"))
        capturer.close()


if __name__ == "__main__":
    unittest.main()
