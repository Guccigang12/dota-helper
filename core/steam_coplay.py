"""
Steamworks SDK Coplay Module.
Interacts with steam_api64.dll via ctypes to retrieve coplay (recently played with) players,
computing the delta of the current lobby (9 players) completely VAC-safely without memory reading.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("DotaHelper.SteamCoplay")


@dataclass
class CoplayPlayer:
    steam_id: str
    name: str
    coplay_time: int
    is_delta: bool = True

    @property
    def profile_url(self) -> str:
        return f"https://steamcommunity.com/profiles/{self.steam_id}"


class SteamCoplayManager:
    """
    Interacts with official Steamworks SDK (steam_api64.dll) to track recently played with users.
    Identifies 9 lobby opponents/teammates via delta snapshot analysis.
    """

    def __init__(self, dll_path: Optional[str] = None) -> None:
        self.dll_path = self._resolve_dll_path(dll_path)
        self._dll: Optional[ctypes.CDLL] = None
        self._p_friends: Optional[int] = None
        self._p_user: Optional[int] = None
        self.is_initialized = False

        self.local_steam_id: Optional[str] = None
        self.baseline_snapshot: Dict[int, int] = {}  # {steam_id: coplay_time}
        self.recent_roster_history: List[Dict[str, Any]] = []

        self._lock = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._callback_stop_event = threading.Event()

        # Callbacks
        self.on_lobby_detected: Optional[Callable[[List[CoplayPlayer]], None]] = None

    def _resolve_dll_path(self, user_path: Optional[str]) -> Path:
        """Find steam_api64.dll in workspace, Dota folder, or current working directory."""
        candidates = []
        if user_path:
            candidates.append(Path(user_path))

        base_dir = Path(__file__).resolve().parent.parent
        candidates.append(base_dir / "steam_api64.dll")
        candidates.append(Path.cwd() / "steam_api64.dll")

        # Try to locate within Dota 2 binary directory as fallback
        from core.gsi_server import find_dota_cfg_directory
        dota_cfg = find_dota_cfg_directory()
        if dota_cfg:
            dota_bin_dll = dota_cfg.parent.parent / "bin" / "win64" / "steam_api64.dll"
            candidates.append(dota_bin_dll)

        for p in candidates:
            if p.exists():
                return p

        # Default fallback to project root
        return base_dir / "steam_api64.dll"

    def initialize(self) -> bool:
        """
        Initialize Steamworks API via steam_api64.dll.
        Requires steam_appid.txt containing '570' in current directory and Steam running.
        """
        if self.is_initialized:
            return True

        # Ensure steam_appid.txt exists
        appid_file = Path("steam_appid.txt")
        if not appid_file.exists():
            try:
                appid_file.write_text("570\n", encoding="utf-8")
            except Exception as e:
                logger.warning("Could not create steam_appid.txt: %s", e)

        if not self.dll_path.exists():
            logger.error("steam_api64.dll not found at: %s", self.dll_path)
            return False

        try:
            # Set DLL directory for dependencies
            dll_dir = str(self.dll_path.parent.resolve())
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(dll_dir)
                except Exception:
                    pass

            self._dll = ctypes.CDLL(str(self.dll_path.resolve()))

            # Initialize Steamworks SDK
            init_success = False
            if hasattr(self._dll, "SteamAPI_InitFlat"):
                self._dll.SteamAPI_InitFlat.restype = ctypes.c_int
                self._dll.SteamAPI_InitFlat.argtypes = [ctypes.c_void_p]
                err_buf = ctypes.create_string_buffer(1024)
                res = self._dll.SteamAPI_InitFlat(err_buf)
                if res == 0:
                    init_success = True
                else:
                    logger.warning("SteamAPI_InitFlat failed code=%d: %s", res, err_buf.value.decode(errors="ignore"))
            elif hasattr(self._dll, "SteamAPI_InitSafe"):
                self._dll.SteamAPI_InitSafe.restype = ctypes.c_bool
                init_success = bool(self._dll.SteamAPI_InitSafe())
            elif hasattr(self._dll, "SteamAPI_Init"):
                self._dll.SteamAPI_Init.restype = ctypes.c_bool
                init_success = bool(self._dll.SteamAPI_Init())

            if not init_success:
                logger.warning("Steamworks SDK failed to initialize. Make sure Steam client is running!")
                return False

            # Setup ISteamFriends interface
            if hasattr(self._dll, "SteamAPI_SteamFriends_v018"):
                self._dll.SteamAPI_SteamFriends_v018.restype = ctypes.c_void_p
                self._p_friends = self._dll.SteamAPI_SteamFriends_v018()
            else:
                logger.warning("SteamAPI_SteamFriends_v018 interface not found in %s! Incompatible DLL version?", self.dll_path)

            # Set function signatures for ISteamFriends
            if hasattr(self._dll, "SteamAPI_ISteamFriends_GetCoplayFriendCount"):
                self._dll.SteamAPI_ISteamFriends_GetCoplayFriendCount.restype = ctypes.c_int
                self._dll.SteamAPI_ISteamFriends_GetCoplayFriendCount.argtypes = [ctypes.c_void_p]

            if hasattr(self._dll, "SteamAPI_ISteamFriends_GetCoplayFriend"):
                self._dll.SteamAPI_ISteamFriends_GetCoplayFriend.restype = ctypes.c_uint64
                self._dll.SteamAPI_ISteamFriends_GetCoplayFriend.argtypes = [ctypes.c_void_p, ctypes.c_int]

            if hasattr(self._dll, "SteamAPI_ISteamFriends_GetFriendCoplayTime"):
                self._dll.SteamAPI_ISteamFriends_GetFriendCoplayTime.restype = ctypes.c_int
                self._dll.SteamAPI_ISteamFriends_GetFriendCoplayTime.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

            if hasattr(self._dll, "SteamAPI_ISteamFriends_GetFriendPersonaName"):
                self._dll.SteamAPI_ISteamFriends_GetFriendPersonaName.restype = ctypes.c_char_p
                self._dll.SteamAPI_ISteamFriends_GetFriendPersonaName.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

            if hasattr(self._dll, "SteamAPI_ISteamFriends_RequestUserInformation"):
                self._dll.SteamAPI_ISteamFriends_RequestUserInformation.restype = ctypes.c_bool
                self._dll.SteamAPI_ISteamFriends_RequestUserInformation.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint64,
                    ctypes.c_bool,
                ]

            if hasattr(self._dll, "SteamAPI_RunCallbacks"):
                self._dll.SteamAPI_RunCallbacks.restype = None

            # Setup ISteamUser to get local SteamID
            if hasattr(self._dll, "SteamAPI_SteamUser_v023"):
                self._dll.SteamAPI_SteamUser_v023.restype = ctypes.c_void_p
                self._p_user = self._dll.SteamAPI_SteamUser_v023()
                if self._p_user and hasattr(self._dll, "SteamAPI_ISteamUser_GetSteamID"):
                    self._dll.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
                    self._dll.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
                    local_id = self._dll.SteamAPI_ISteamUser_GetSteamID(self._p_user)
                    if local_id:
                        self.local_steam_id = str(local_id)
                        logger.info("Local SteamID: %s", self.local_steam_id)
            else:
                logger.warning("SteamAPI_SteamUser_v023 interface not found in %s! Incompatible DLL version?", self.dll_path)

            self.is_initialized = True
            logger.info("Steamworks SDK initialized successfully!")

            # Start background callbacks pump (every 300-500 ms)
            self._start_callback_pump()

            # Take initial baseline snapshot
            self.baseline_snapshot = self.get_coplay_snapshot()
            logger.info("Captured initial baseline coplay snapshot with %d entries.", len(self.baseline_snapshot))
            return True

        except Exception as e:
            logger.error("Exception during Steamworks initialization: %s", e)
            self.is_initialized = False
            return False

    def _start_callback_pump(self) -> None:
        """Start background daemon thread pumping SteamAPI_RunCallbacks periodically."""
        if self._callback_thread and self._callback_thread.is_alive():
            return
        self._callback_stop_event.clear()
        self._callback_thread = threading.Thread(
            target=self._callback_pump_worker,
            name="SteamCallbackPump",
            daemon=True,
        )
        self._callback_thread.start()
        logger.debug("Steamworks callback pump thread started.")

    def _stop_callback_pump(self) -> None:
        """Stop background callback pump thread."""
        self._callback_stop_event.set()
        if self._callback_thread and self._callback_thread.is_alive():
            self._callback_thread.join(timeout=1.0)
        self._callback_thread = None

    def _callback_pump_worker(self) -> None:
        """Periodically pump Steamworks callbacks (every 300-400 ms) so persona names resolve asynchronously."""
        while not self._callback_stop_event.is_set():
            self.run_callbacks()
            self._callback_stop_event.wait(0.35)

    def shutdown(self) -> None:
        """Shutdown Steamworks SDK cleanly."""
        self._stop_callback_pump()
        if self.is_initialized and self._dll and hasattr(self._dll, "SteamAPI_Shutdown"):
            try:
                self._dll.SteamAPI_Shutdown()
            except Exception as e:
                logger.warning("Error during SteamAPI_Shutdown: %s", e)
        self.is_initialized = False

    def run_callbacks(self) -> None:
        """Pump Steamworks event callbacks."""
        if self.is_initialized and self._dll and hasattr(self._dll, "SteamAPI_RunCallbacks"):
            try:
                self._dll.SteamAPI_RunCallbacks()
            except Exception:
                pass

    def get_coplay_snapshot(self) -> Dict[int, int]:
        """
        Retrieve snapshot of all coplay friends in Steam: {steam_id: coplay_time}.
        """
        if not self.is_initialized or not self._dll or not self._p_friends:
            return {}

        snapshot: Dict[int, int] = {}
        try:
            count = self._dll.SteamAPI_ISteamFriends_GetCoplayFriendCount(self._p_friends)
            for i in range(count):
                steam_id = self._dll.SteamAPI_ISteamFriends_GetCoplayFriend(self._p_friends, i)
                if not steam_id:
                    continue
                coplay_time = self._dll.SteamAPI_ISteamFriends_GetFriendCoplayTime(self._p_friends, steam_id)
                snapshot[steam_id] = coplay_time
        except Exception as e:
            logger.error("Error reading coplay snapshot: %s", e)
        return snapshot

    def get_player_persona_name(self, steam_id: int) -> str:
        """Get Steam persona name for the given SteamID."""
        if not self.is_initialized or not self._dll or not self._p_friends:
            return "Unknown"

        try:
            name_raw = self._dll.SteamAPI_ISteamFriends_GetFriendPersonaName(self._p_friends, steam_id)
            if name_raw:
                name = name_raw.decode("utf-8", errors="replace").strip()
                if name and name != "[unknown]":
                    return name

            # Request user information asynchronously from Steam servers if unknown
            if hasattr(self._dll, "SteamAPI_ISteamFriends_RequestUserInformation"):
                self._dll.SteamAPI_ISteamFriends_RequestUserInformation(self._p_friends, steam_id, True)
                self.run_callbacks()

            return "Unknown"
        except Exception:
            return "Unknown"

    def scan_lobby_delayed(self, local_steam_id: Optional[str] = None, delay_seconds: float = 3.5) -> None:
        """
        Initiate asynchronous 2-step snapshot scan triggered on HERO_SELECTION.
        Snapshot #1 taken immediately, Snapshot #2 taken after delay_seconds.
        """
        if self._scan_thread and self._scan_thread.is_alive():
            logger.info("Scan already in progress, skipping duplicate request.")
            return

        effective_local_id = local_steam_id or self.local_steam_id

        self._scan_thread = threading.Thread(
            target=self._scan_lobby_worker,
            args=(effective_local_id, delay_seconds),
            name="CoplayScanWorker",
            daemon=True,
        )
        self._scan_thread.start()

    def _scan_lobby_worker(self, local_steam_id: Optional[str], delay_seconds: float) -> None:
        """Worker thread executing the 2-step delta lobby detection."""
        logger.info("Starting lobby detection scan (delay: %.1fs)...", delay_seconds)

        # Snapshot #1 (immediately upon HERO_SELECTION)
        snap1 = self.get_coplay_snapshot()
        time.sleep(delay_seconds)

        # Snapshot #2 (after 3-4s delay)
        snap2 = self.get_coplay_snapshot()

        # Compute delta players comparing snap2 against snap1 and baseline
        lobby_players = self._compute_delta_lobby(snap1, snap2, local_steam_id)
        delta_count = sum(1 for p in lobby_players if p.is_delta)
        logger.info(
            "Lobby detection completed: %d players total (%d delta candidates, %d historical fillers).",
            len(lobby_players),
            delta_count,
            len(lobby_players) - delta_count,
        )

        if self.on_lobby_detected:
            try:
                self.on_lobby_detected(lobby_players)
            except Exception as e:
                logger.error("Error in on_lobby_detected callback: %s", e)

    def _compute_delta_lobby(
        self,
        snap1: Dict[int, int],
        snap2: Optional[Dict[int, int]] = None,
        local_steam_id: Optional[str] = None,
    ) -> List[CoplayPlayer]:
        """
        Compare snap2 and snap1 against baseline to determine the 9 players in current lobby.
        True delta candidates are players in snap2 who:
          - appeared / were updated between snap1 and snap2, OR
          - appeared / were updated relative to baseline.
        If fewer than 9 delta players are found, supplements with the most recent historical entries (is_delta=False).
        """
        # Backward compatibility if called as _compute_delta_lobby(snap2, local_steam_id)
        if snap2 is not None and not isinstance(snap2, dict):
            local_steam_id = str(snap2) if snap2 else None
            snap2 = snap1
            snap1 = {}
        elif snap2 is None:
            snap2 = snap1
            snap1 = {}

        with self._lock:
            local_id_int = int(local_steam_id) if local_steam_id and str(local_steam_id).isdigit() else None

            # Merge all available players across both snapshots
            all_candidates: Dict[int, int] = dict(snap1)
            all_candidates.update(snap2)

            delta_entries: List[Tuple[int, int]] = []
            filler_entries: List[Tuple[int, int]] = []

            for steam_id, coplay_time in all_candidates.items():
                if local_id_int and steam_id == local_id_int:
                    continue  # Exclude self

                # Check 1: Appeared / updated between snap1 and snap2
                is_new_in_snap2 = (steam_id not in snap1) or (snap2.get(steam_id, 0) > snap1.get(steam_id, 0))

                # Check 2: Appeared / updated relative to baseline
                is_new_vs_baseline = False
                if self.baseline_snapshot:
                    is_new_vs_baseline = (steam_id not in self.baseline_snapshot) or (coplay_time > self.baseline_snapshot.get(steam_id, 0))

                if self.baseline_snapshot:
                    is_delta = is_new_in_snap2 or is_new_vs_baseline
                else:
                    # Baseline was empty: delta is strictly what appeared between snap1 and snap2
                    is_delta = is_new_in_snap2 if snap1 else True

                if is_delta:
                    delta_entries.append((steam_id, coplay_time))
                else:
                    filler_entries.append((steam_id, coplay_time))

            # Sort both pools by coplay_time descending
            delta_entries.sort(key=lambda x: x[1], reverse=True)
            filler_entries.sort(key=lambda x: x[1], reverse=True)

            selected_entries: List[Tuple[int, int, bool]] = []
            for s, t in delta_entries[:9]:
                selected_entries.append((s, t, True))

            # Supplement with historical fillers if delta < 9
            if len(selected_entries) < 9:
                already_in = {s for s, _, _ in selected_entries}
                for s, t in filler_entries:
                    if s not in already_in:
                        selected_entries.append((s, t, False))
                    if len(selected_entries) >= 9:
                        break

            results: List[CoplayPlayer] = []
            for steam_id, coplay_time, is_delta in selected_entries:
                name = self.get_player_persona_name(steam_id)
                results.append(
                    CoplayPlayer(
                        steam_id=str(steam_id),
                        name=name,
                        coplay_time=coplay_time,
                        is_delta=is_delta,
                    )
                )

            return results

    def on_match_finished(self) -> None:
        """
        Called when a match finishes (POST_GAME).
        Updates the baseline to current state so the next match detects fresh delta.
        """
        with self._lock:
            current_snap = self.get_coplay_snapshot()
            if current_snap:
                self.baseline_snapshot = current_snap
                logger.info("Updated coplay baseline snapshot after match finish (%d entries).", len(self.baseline_snapshot))
