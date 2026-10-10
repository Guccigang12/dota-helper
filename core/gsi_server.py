"""
Dota 2 Game State Integration (GSI) HTTP Server.
Handles incoming game state payloads from Dota 2 and triggers state transitions.
Includes automatic detection of Dota 2 installation and GSI configuration generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("DotaHelper.GSI")

# Known Valve Game Rules States
STATE_MAP = {
    "DOTA_GAMERULES_STATE_INIT": "INIT",
    "DOTA_GAMERULES_STATE_WAIT_FOR_PLAYERS_TO_LOAD": "WAIT_FOR_PLAYERS",
    "DOTA_GAMERULES_STATE_HERO_SELECTION": "HERO_SELECTION",
    "DOTA_GAMERULES_STATE_STRATEGY_TIME": "STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_PRE_GAME": "PRE_GAME",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS": "GAME_IN_PROGRESS",
    "DOTA_GAMERULES_STATE_POST_GAME": "POST_GAME",
    "DOTA_GAMERULES_STATE_DISCONNECT": "DISCONNECT",
}


def normalize_game_state(raw_state: str) -> str:
    """Normalize raw Dota game state string to simplified uppercase token."""
    if not raw_state:
        return "UNKNOWN"
    return STATE_MAP.get(raw_state, raw_state.replace("DOTA_GAMERULES_STATE_", ""))


def find_steam_path() -> Optional[Path]:
    """Find Steam root installation directory via Windows Registry."""
    reg_locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]

    for root_key, subkey, val_name in reg_locations:
        try:
            with winreg.OpenKey(root_key, subkey) as key:
                val, _ = winreg.QueryValueEx(key, val_name)
                if val and os.path.exists(val):
                    return Path(val)
        except OSError:
            continue

    # Fallback to standard drive locations
    for standard_path in [
        r"C:\Program Files (x86)\Steam",
        r"C:\Steam",
        r"D:\Steam",
        r"E:\Steam",
    ]:
        if os.path.exists(standard_path):
            return Path(standard_path)

    return None


def find_dota_cfg_directory() -> Optional[Path]:
    """
    Locate the 'dota 2 beta/game/dota/cfg' folder by checking Steam library folders.
    """
    steam_path = find_steam_path()
    candidate_libraries: list[Path] = []

    if steam_path and steam_path.exists():
        candidate_libraries.append(steam_path)
        vdf_path = steam_path / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                # Parse all path entries from libraryfolders.vdf
                paths = re.findall(r'"path"\s+"([^"]+)"', content)
                for raw_p in paths:
                    clean_p = Path(raw_p.replace(r"\\", "\\"))
                    if clean_p.exists() and clean_p not in candidate_libraries:
                        candidate_libraries.append(clean_p)
            except Exception as e:
                logger.warning("Error parsing libraryfolders.vdf: %s", e)

    # Check common game library paths on connected drives as fallback
    for drive in ["C:", "D:", "E:", "F:", "G:"]:
        for extra in ["SteamLibrary", "Games/SteamLibrary", "Steam"]:
            p = Path(f"{drive}\\{extra}")
            if p.exists() and p not in candidate_libraries:
                candidate_libraries.append(p)

    for lib in candidate_libraries:
        dota_cfg = lib / "steamapps" / "common" / "dota 2 beta" / "game" / "dota" / "cfg"
        if dota_cfg.exists():
            logger.info("Found Dota 2 cfg directory: %s", dota_cfg)
            return dota_cfg

        # Check via appmanifest_570.acf existence
        manifest = lib / "steamapps" / "appmanifest_570.acf"
        if manifest.exists():
            cfg_via_manifest = lib / "steamapps" / "common" / "dota 2 beta" / "game" / "dota" / "cfg"
            if cfg_via_manifest.exists():
                logger.info("Found Dota 2 cfg directory: %s", cfg_via_manifest)
                return cfg_via_manifest
            # Only return path if dota game directory physically exists (no side-effects / mkdir)
            dota_game_dir = lib / "steamapps" / "common" / "dota 2 beta" / "game" / "dota"
            if dota_game_dir.exists():
                return cfg_via_manifest

    return None


def install_gsi_config(auth_token: str = "DOTAHELPER_GSI_SECRET_KEY", port: int = 32123) -> Optional[Path]:
    """
    Generate and install 'gamestate_integration_dotahelper.cfg' in Dota 2's cfg directory.
    Returns the path to the installed cfg file if successful, otherwise None.
    """
    cfg_dir = find_dota_cfg_directory()
    if not cfg_dir:
        logger.warning("Could not find Dota 2 installation folder to install GSI config.")
        return None

    try:
        # Create directory explicitly during installation, not during discovery
        cfg_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("Could not create Dota 2 cfg directory %s: %s", cfg_dir, e)
        return None

    cfg_file = cfg_dir / "gamestate_integration_dotahelper.cfg"
    cfg_content = f""""Dota 2 Game State Integration Configuration"
{{
    "uri"           "http://127.0.0.1:{port}/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.2"
    "heartbeat"     "30.0"
    "data"
    {{
        "buildings"     "0"
        "provider"      "1"
        "map"           "1"
        "player"        "1"
        "hero"          "1"
        "abilities"     "0"
        "items"         "0"
        "draft"         "1"
        "wearables"     "0"
    }}
    "auth"
    {{
        "token"         "{auth_token}"
    }}
}}
"""
    try:
        cfg_file.write_text(cfg_content, encoding="utf-8")
        logger.info("Successfully installed GSI config at %s", cfg_file)
        return cfg_file
    except Exception as e:
        logger.error("Failed to write GSI configuration: %s", e)
        return None


class GSIRequestHandler(BaseHTTPRequestHandler):
    """Handles POST requests sent by Dota 2 Game State Integration."""

    server: GSIServer

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_response(400)
                self.end_headers()
                return

            # Limit incoming body read to 64KB (Dota GSI payloads never exceed 32KB)
            to_read = min(content_length, 65536)
            body = self.rfile.read(to_read)
            payload = json.loads(body.decode("utf-8", errors="replace"))

            # Token validation: reject if server token not set, do not leak received token in log
            server_token = (self.server.auth_token or "").strip()
            if not server_token:
                logger.warning("GSI payload rejected: server auth_token is empty or not configured")
                self.send_response(403)
                self.end_headers()
                return

            auth_token = payload.get("auth", {}).get("token")
            if auth_token != server_token:
                logger.warning("Unauthorized GSI payload received: token mismatch")
                self.send_response(401)
                self.end_headers()
                return

            # Process state
            self.server.process_payload(payload)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            logger.error("Error processing GSI POST: %s", e)
            self.send_response(500)
            self.end_headers()

    def do_GET(self) -> None:
        """Health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"DotaHelper GSI Server is Running")

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs
        pass


class GSIServer:
    """
    Local HTTP server for receiving Dota 2 Game State Integration updates.
    Dispatches hero selection, game start, and match end events.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 32123, auth_token: str = "DOTAHELPER_GSI_SECRET_KEY") -> None:
        self.host = host
        self.port = port
        self.auth_token = auth_token

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.last_game_state: str = "UNKNOWN"
        self.last_match_id: str = "0"
        self.local_player_steam_id: str = ""

        # Callbacks
        self.on_hero_selection: Optional[Callable[[str, str], None]] = None  # (match_id, player_steamid)
        self.on_post_game: Optional[Callable[[str], None]] = None            # (match_id)
        self.on_state_change: Optional[Callable[[str, str, Dict[str, Any]], None]] = None  # (state, match_id, raw)

    def start(self) -> bool:
        """Start the GSI server in a background daemon thread."""
        if self._running:
            return True

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), GSIRequestHandler)
            self._httpd.server = self  # Attach reference for handler
            self._running = True

            self._thread = threading.Thread(target=self._httpd.serve_forever, name="GSIServerThread", daemon=True)
            self._thread.start()
            logger.info("GSI server listening on http://%s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Failed to start GSI server on %s:%d - %s", self.host, self.port, e)
            self._running = False
            return False

    def stop(self) -> None:
        """Stop the GSI server."""
        self._running = False
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception as e:
                logger.warning("Error closing GSI server: %s", e)
            self._httpd = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
            self._thread = None
        logger.info("GSI server stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    def process_payload(self, payload: Dict[str, Any]) -> None:
        """Parse GSI payload, update state and trigger callbacks."""
        map_data = payload.get("map", {})
        player_data = payload.get("player", {})

        raw_state = map_data.get("game_state", "")
        current_state = normalize_game_state(raw_state)

        raw_match_id = str(map_data.get("matchid", "0")).strip()
        match_id = raw_match_id if raw_match_id != "0" else ""

        steam_id = str(player_data.get("steamid", "")).strip()
        if steam_id:
            self.local_player_steam_id = steam_id

        prev_state = self.last_game_state
        self.last_game_state = current_state
        self.last_match_id = match_id

        # Notify general state change if state changed
        if current_state != prev_state:
            logger.info("Game state changed: %s -> %s (Match ID: %s, SteamID: %s)",
                        prev_state, current_state, match_id or "N/A", steam_id or "N/A")
            if self.on_state_change:
                try:
                    self.on_state_change(current_state, match_id, payload)
                except Exception as e:
                    logger.error("Error in on_state_change callback: %s", e)

            # Trigger HERO_SELECTION transition
            if current_state == "HERO_SELECTION":
                logger.info("Triggering Hero Selection event (Match ID: %s)", match_id or "N/A")
                if self.on_hero_selection:
                    try:
                        self.on_hero_selection(match_id, self.local_player_steam_id)
                    except Exception as e:
                        logger.error("Error in on_hero_selection callback: %s", e)

            # Trigger POST_GAME transition
            elif current_state == "POST_GAME":
                logger.info("Triggering Post Game event (Match ID: %s)", match_id or "N/A")
                if self.on_post_game:
                    try:
                        self.on_post_game(match_id)
                    except Exception as e:
                        logger.error("Error in on_post_game callback: %s", e)
