"""
DotaHelper — VAC-Safe Dota 2 Companion
Main entry point coordinating GSI, Steamworks Coplay, LZT Market inventory evaluation,
OpenRouter Vision chat translation, and the Qt overlay.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from core.gsi_server import GSIServer, install_gsi_config
from core.steam_coplay import SteamCoplayManager
from core.free_evaluator import FreeInventoryEvaluator
from core.chat_translator import ChatVisionTranslator
from core.soundpad import SoundpadManager
from ui.overlay import OverlayWindow

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DotaHelper.Main")

CONFIG_FILE = Path("config.json")
DEFAULT_CONFIG: Dict[str, Any] = {
    "gsi": {
        "host": "127.0.0.1",
        "port": 32123,
        "auth_token": "DOTAHELPER_GSI_SECRET_KEY",
    },
    "openrouter": {
        "api_key": "",
        "model": "google/gemini-2.5-flash",
        "site_url": "https://github.com/dotahelper",
        "site_name": "DotaHelper",
    },
    "inventory_checker": {
        "proxy_url": "",
        "cloudflare_worker_url": "",
        "use_auto_free_proxies": False,
        "cache_ttl_hours": 12,
        "request_interval_seconds": 1.5,
        "min_item_price": 0.5,
        "currency": "USD",
    },
    "chat_vision": {
        "roi": {
            "left": 15,
            "top": 620,
            "right": 460,
            "bottom": 880,
        },
        "jpeg_quality": 85,
        "hotkey_translate": "f8",
        "hotkey_select_roi": "ctrl+shift+f8",
    },
    "overlay": {
        "opacity": 0.95,
        "always_on_top": True,
        "x": 60,
        "y": 60,
        "width": 520,
        "height": 740,
    },
    "soundpad": {
        "enabled": True,
        "sounds_dir": "sounds",
        "audio_device": "",
        "master_volume": 0.8,
        "voice_volume": 1.0,
        "monitor_enabled": True,
    },
}


def load_or_create_config(cfg_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load config.json or generate defaults if missing."""
    target_path = Path(cfg_path) if cfg_path else CONFIG_FILE
    if not target_path.exists():
        try:
            target_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Created default %s", target_path)
            return DEFAULT_CONFIG
        except Exception as e:
            logger.error("Could not write default config: %s", e)
            return DEFAULT_CONFIG

    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        logger.error("Failed to parse config from %s, using defaults: %s", target_path, e)
        return DEFAULT_CONFIG


class DotaHelperApp:
    """Core controller gluing together all modules."""

    def __init__(self, qapp: QApplication, config_path: Optional[Path | str] = None) -> None:
        self.qapp = qapp
        self.config_path = Path(config_path) if config_path else CONFIG_FILE
        self.config = load_or_create_config(self.config_path)

        # 1. Initialize UI Overlay
        self.overlay = OverlayWindow(config_path=self.config_path)

        # 2. Setup GSI Server & Auto-install Dota 2 cfg
        gsi_cfg = self.config.get("gsi", {})
        self.gsi_port = gsi_cfg.get("port", 32123)
        self.gsi_token = gsi_cfg.get("auth_token", "DOTAHELPER_GSI_SECRET_KEY")

        installed_cfg = install_gsi_config(auth_token=self.gsi_token, port=self.gsi_port)
        if installed_cfg:
            logger.info("Dota 2 GSI configuration active at: %s", installed_cfg)
        else:
            logger.warning("Could not auto-install GSI cfg into Dota 2. You may need to copy it manually.")

        self.gsi_server = GSIServer(
            host=gsi_cfg.get("host", "127.0.0.1"),
            port=self.gsi_port,
            auth_token=self.gsi_token,
        )

        # 3. Setup Steamworks Coplay Manager
        self.coplay_manager = SteamCoplayManager()

        # 4. Setup Free Inventory Evaluator (100% Free, no paid APIs)
        checker_cfg = self.config.get("inventory_checker", self.config.get("pricing", {}))
        self.inventory_evaluator = FreeInventoryEvaluator(
            currency=checker_cfg.get("currency", "USD"),
            min_item_price=checker_cfg.get("min_item_price", 0.5),
            cache_ttl_hours=checker_cfg.get("cache_ttl_hours", 12),
            request_interval_seconds=checker_cfg.get("request_interval_seconds", 1.5),
            proxy_url=checker_cfg.get("proxy_url", checker_cfg.get("proxy", "")),
            cloudflare_worker_url=checker_cfg.get("cloudflare_worker_url", checker_cfg.get("reverse_proxy_url", "")),
            use_auto_free_proxies=checker_cfg.get("use_auto_free_proxies", True),
        )

        # 5. Setup Chat Vision Translator
        or_cfg = self.config.get("openrouter", {})
        self.chat_translator = ChatVisionTranslator(
            api_key=or_cfg.get("api_key", ""),
            model=or_cfg.get("model", "google/gemini-2.5-flash"),
            config_path=CONFIG_FILE,
        )

        # 6. Setup Soundpad Manager
        sp_cfg = self.config.get("soundpad", {})
        sounds_dir = Path(sp_cfg.get("sounds_dir", "sounds"))
        self.soundpad_manager = SoundpadManager(
            sounds_dir=sounds_dir,
            device_name=sp_cfg.get("audio_device", ""),
            master_volume=sp_cfg.get("master_volume", 0.8),
            voice_volume=sp_cfg.get("voice_volume", 1.0),
            monitor_enabled=sp_cfg.get("monitor_enabled", True),
        )

        # 7. Wire Callbacks and Signals
        self._wire_connections()

        # 8. Setup Global Hotkeys
        self._setup_hotkeys()

    def _wire_connections(self) -> None:
        """Connect all module callbacks and UI signals."""
        # GSI -> UI & Steam Coplay
        self.gsi_server.on_state_change = lambda state, match_id, raw: self.overlay.signals.gsi_state_changed.emit(state, match_id)
        self.gsi_server.on_hero_selection = self._handle_hero_selection
        self.gsi_server.on_post_game = self._handle_post_game

        # Steam Coplay -> UI & Free Inventory Evaluator
        self.coplay_manager.on_lobby_detected = self._handle_lobby_detected

        # Free Inventory Evaluator -> UI
        self.inventory_evaluator.on_player_evaluated = lambda res: self.overlay.signals.player_evaluated.emit(res)

        # Chat Vision Translator -> UI
        self.chat_translator.on_translation_success = lambda msgs: self.overlay.signals.chat_translated.emit(msgs)
        self.chat_translator.on_status_change = lambda st: self.overlay.signals.chat_status_changed.emit(st)
        self.chat_translator.on_error = lambda err: self.overlay.signals.chat_error.emit(err)

        # Overlay UI actions
        self.overlay.on_settings_saved = self._on_settings_saved
        self.overlay.force_scan_requested.connect(self._manual_coplay_scan)
        self.overlay.translate_chat_requested.connect(self.chat_translator.capture_and_translate_async)
        self.overlay.select_roi_requested.connect(self.overlay.signals.open_roi_selector.emit)

        # Soundpad UI actions
        self.overlay.sound_play_requested.connect(self._on_sound_play)
        self.overlay.sound_stop_requested.connect(self.soundpad_manager.stop)
        self.overlay.sound_device_changed.connect(self._on_sound_device_changed)
        self.overlay.sound_volume_changed.connect(self._on_sound_volume_changed)
        self.overlay.sound_monitor_toggled.connect(self.soundpad_manager.audio.set_monitor_enabled)
        self.overlay.sound_refresh_requested.connect(self._refresh_sounds_in_ui)

        # ROI Selector overlay result
        self.overlay.signals.open_roi_selector.connect(self._bind_roi_selector)

    def _on_sound_play(self, path: str) -> None:
        self.soundpad_manager.play(path)

    def _on_sound_device_changed(self, device_name: str) -> None:
        clean_dev = "" if "Default" in device_name else device_name
        self.soundpad_manager.audio.set_device(clean_dev)
        if "soundpad" not in self.config:
            self.config["soundpad"] = {}
        self.config["soundpad"]["audio_device"] = clean_dev
        try:
            CONFIG_FILE.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _on_sound_volume_changed(self, volume: float) -> None:
        self.soundpad_manager.audio.set_volumes(volume, 1.0)

    def _refresh_sounds_in_ui(self) -> None:
        sounds = self.soundpad_manager.scan_sounds()
        self.overlay.update_sounds_list(sounds)

    def _on_settings_saved(self, cfg: Dict[str, Any]) -> None:
        """Apply new configuration dynamically without app restart."""
        self.config = cfg
        checker_cfg = cfg.get("inventory_checker", cfg.get("pricing", {}))
        new_or_key = cfg.get("openrouter", {}).get("api_key", "")
        new_model = cfg.get("openrouter", {}).get("model", "google/gemini-2.5-flash")

        self.inventory_evaluator.update_settings(
            currency=checker_cfg.get("currency", "USD"),
            min_item_price=checker_cfg.get("min_item_price", 0.5),
            proxy_url=checker_cfg.get("proxy_url", checker_cfg.get("proxy", "")),
            cloudflare_worker_url=checker_cfg.get("cloudflare_worker_url", checker_cfg.get("reverse_proxy_url", "")),
            use_auto_free_proxies=checker_cfg.get("use_auto_free_proxies", True),
            request_interval_seconds=checker_cfg.get("request_interval_seconds", 1.5),
        )
        self.chat_translator.set_api_key(new_or_key)
        self.chat_translator.set_model(new_model)
        logger.info("Runtime inventory checker settings and model dynamically updated from Settings dialog.")

    def _bind_roi_selector(self) -> None:
        """Hook save_roi when ROI selector emits."""
        if hasattr(self.overlay, "_roi_selector") and self.overlay._roi_selector:
            self.overlay._roi_selector.roi_selected.connect(self.chat_translator.save_roi)

    def _handle_hero_selection(self, match_id: str, local_player_steam_id: str) -> None:
        """Triggered automatically when draft / hero selection begins."""
        logger.info("Hero Selection detected for match %s. Initiating coplay scan...", match_id or "N/A")
        self.coplay_manager.scan_lobby_delayed(
            local_steam_id=local_player_steam_id or self.coplay_manager.local_steam_id,
            delay_seconds=3.5,
        )

    def _handle_post_game(self, match_id: str) -> None:
        """Triggered when match ends."""
        logger.info("Match finished (%s). Refreshing Steam baseline.", match_id or "N/A")
        self.coplay_manager.on_match_finished()

    def _handle_lobby_detected(self, players: list) -> None:
        """Called when 9 lobby players are detected."""
        logger.info("Lobby detected with %d players. Updating UI and evaluating inventories...", len(players))
        # 1. Update UI cards immediately with player names and IDs
        self.overlay.signals.lobby_detected.emit(players)

        # 2. Queue player inventories for evaluation
        steam_ids = [p.steam_id for p in players]
        self.inventory_evaluator.evaluate_players(steam_ids)

    def _manual_coplay_scan(self) -> None:
        """Manually trigger coplay scan from UI button."""
        logger.info("Manual coplay scan triggered from UI.")
        self.coplay_manager.scan_lobby_delayed(
            local_steam_id=self.coplay_manager.local_steam_id,
            delay_seconds=1.0,
        )

    def _setup_hotkeys(self) -> None:
        """Register global hotkeys (F8 for translation, Ctrl+Shift+F8 for ROI selection)."""
        try:
            import keyboard

            hotkey_trans = self.config.get("chat_vision", {}).get("hotkey_translate", "f8")
            hotkey_roi = self.config.get("chat_vision", {}).get("hotkey_select_roi", "ctrl+shift+f8")
            hotkey_toggle = self.config.get("overlay", {}).get("hotkey_toggle", "f9")

            keyboard.add_hotkey(hotkey_trans, self.chat_translator.capture_and_translate_async, suppress=False)
            keyboard.add_hotkey(hotkey_roi, self.overlay.signals.open_roi_selector.emit, suppress=False)
            keyboard.add_hotkey(hotkey_toggle, self.overlay.signals.toggle_visibility.emit, suppress=False)

            logger.info(
                "Registered global hotkeys: %s (Translate), %s (Select Area), %s (Toggle Overlay)",
                hotkey_trans.upper(),
                hotkey_roi.upper(),
                hotkey_toggle.upper(),
            )
        except Exception as e:
            logger.warning("Could not register global hotkeys (may need administrator privileges): %s", e)

    def start(self) -> None:
        """Start all services and show overlay UI."""
        # 1. Start GSI server
        if self.gsi_server.start():
            logger.info("GSI Server started.")
        else:
            logger.warning("GSI Server failed to start on port %d", self.gsi_port)

        # 2. Start Steamworks SDK
        steam_ok = self.coplay_manager.initialize()
        self.overlay.signals.steam_status_changed.emit(steam_ok, self.coplay_manager.local_steam_id or "")

        # 3. Start inventory evaluator background worker
        self.inventory_evaluator.start()

        # 4. Populate Soundpad in UI
        devices = self.soundpad_manager.audio.list_output_devices()
        cur_dev = self.config.get("soundpad", {}).get("audio_device", "")
        self.overlay.set_soundpad_devices(devices, cur_dev)
        self._refresh_sounds_in_ui()

        # 5. Show UI Overlay
        self.overlay.show()
        logger.info("DotaHelper Overlay started successfully. Ready for Dota 2!")

    def shutdown(self) -> None:
        """Clean graceful shutdown."""
        logger.info("Shutting down DotaHelper...")
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

        self.gsi_server.stop()
        self.coplay_manager.shutdown()
        self.inventory_evaluator.stop()
        self.soundpad_manager.close()
        logger.info("DotaHelper terminated cleanly.")


def main() -> int:
    parser = argparse.ArgumentParser(description="DotaHelper — VAC-Safe Dota 2 Companion")
    parser.add_argument(
        "--companion",
        action="store_true",
        help="Launch in standalone Dota Companion mode (OCR translator & Soundpad tabbed UI)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Path to custom configuration file (default: config.json)",
    )
    args, unknown = parser.parse_known_args()

    if args.companion:
        logger.info("Launching standalone Dota Companion mode (--companion)...")
        from dota_companion.__main__ import main as run_companion
        return run_companion()

    # High DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Dota Helper")
    app.setOrganizationName("DotaHelper")

    helper_app = DotaHelperApp(app, config_path=args.config)
    app.aboutToQuit.connect(helper_app.shutdown)

    helper_app.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
