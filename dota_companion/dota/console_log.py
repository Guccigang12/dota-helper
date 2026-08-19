
   
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core.logger import get_logger

log = get_logger("console_log")

                                                               
CHAT_RE = re.compile(
    r"^\s*\[(ALL|TEAM|SPECTATORS|GENERAL|GAME|All|Team|Spectators|General|Game)\]\s*"
    r"(.+?)\s*:\s*(.+)$"
)
STEAMID64_RE = re.compile(r"\b(7656119\d{10})\b")
STEAMID3_RE = re.compile(r"\[U:1:(\d{5,10})\]")
ACCOUNT_ID_RE = re.compile(r"\bAccount\s*ID\s*[:=]\s*(\d{5,10})\b", re.IGNORECASE)
STATUS_PLAYER_A = re.compile(r"\[Client\]\s+(\d{1,2})\s+.*?'([^']+)'")
STATUS_PLAYER_B = re.compile(r"\[Client\]\s*(?:#\s*)?(\d{1,2})?\s*\d*\s+[\"']([^\"']+)[\"']\s+\[U:1:(\d{5,10})\]")
STATUS_PLAYER_C = re.compile(r"\[Client\]\s*(?:#\s*)?(\d{1,2})?\s*\d*\s+[\"']([^\"']+)[\"']\s+(7656119\d{10})")
MATCH_ID_RE = re.compile(r"Lobby MatchID:\s*(\d{8,12})")
PROFILE_RE = re.compile(r"steamcommunity\.com/(?:profiles|id)/([A-Za-z0-9_\-]+)")
STEAM_ID64_OFFSET = 76561197960265728

POLL_INTERVAL = 0.4


class ConsoleLogSignals(QObject):
    chat_line = pyqtSignal(str, str, str)
    steam_ids = pyqtSignal(list)
    status_players = pyqtSignal(object)
    status = pyqtSignal(str, bool)


class ConsoleLogTailer(QThread):
    def __init__(
        self,
        path_provider: Callable[[], str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.signals = ConsoleLogSignals()
        self._path_provider = path_provider
        self._stop_event = threading.Event()
        self._offset: int | None = None
        self._warned_missing = False
        self._status_buffer: list[dict] = []
        self._last_status_key: tuple | None = None

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(3000)

    def reset(self) -> None:
        """Сброс состояния парсера при старте нового матча."""
        self._status_buffer.clear()
        self._last_status_key = None
        self._offset = None
        self._current_match_id = None

    def run(self) -> None:
        while not self._stop_event.is_set():
            path = self._path_provider()
            if path:
                self._tail(path)
            else:
                if not self._warned_missing:
                    self._warned_missing = True
                    self.signals.status.emit("console.log не найден (нужен запуск Dota 2 с -condebug)", True)
            time.sleep(POLL_INTERVAL)

    def _tail(self, path: str) -> None:
        file_path = Path(path)
        if not file_path.is_file():
            if not self._warned_missing:
                self._warned_missing = True
                self.signals.status.emit(f"console.log не найден: {file_path}", True)
            return
        self._warned_missing = False

        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
                if self._offset is None:
                    # Читаем последние 512 КБ лога, чтобы захватить вывод status
                    file_size = file_path.stat().st_size
                    max_read = 512 * 1024
                    if file_size > max_read:
                        fh.seek(file_size - max_read)
                        fh.readline()  # пропускаем незавершённую строку
                    else:
                        fh.seek(0)
                    self._offset = fh.tell()
                else:
                    fh.seek(self._offset)

                while not self._stop_event.is_set():
                    line = fh.readline()
                    if not line:
                        break
                    self._offset = fh.tell()
                    self._parse_line(line.rstrip("\r\n"))

                if self._offset is None:
                    self._offset = 0
        except OSError as exc:
            log.debug("Ошибка чтения console.log: %s", exc)
            self._offset = None

    def _parse_line(self, line: str) -> None:
        try:
            self._do_parse_line(line)
        except Exception as exc:
            log.error("Ошибка обработки строки лога: %s", exc)

    def _do_parse_line(self, line: str) -> None:
        chat = CHAT_RE.match(line)
        if chat:
            channel, name, text = chat.group(1), chat.group(2).strip(), chat.group(3).strip()
            if name and text:
                self.signals.chat_line.emit(channel.upper(), name, text)

        # Парсим вывод команды status в Source 2
        m_match = MATCH_ID_RE.search(line)
        if m_match:
            try:
                self._current_match_id = int(m_match.group(1))
            except ValueError:
                pass

        mA = STATUS_PLAYER_A.search(line)
        mB = STATUS_PLAYER_B.search(line)
        mC = STATUS_PLAYER_C.search(line)

        if mB:
            name = mB.group(2).strip()
            acc_id = int(mB.group(3))
            slot = int(mB.group(1)) if mB.group(1) else (len(self._status_buffer) + 1)
            if 1 <= slot <= 10 and name and name != "SourceTV":
                self._status_buffer.append({"slot": slot, "name": name, "account_id": acc_id, "is_radiant": slot <= 5})
        elif mC:
            name = mC.group(2).strip()
            steam_id64 = int(mC.group(3))
            acc_id = steam_id64 - STEAM_ID64_OFFSET
            slot = int(mC.group(1)) if mC.group(1) else (len(self._status_buffer) + 1)
            if 1 <= slot <= 10 and name and name != "SourceTV":
                self._status_buffer.append({"slot": slot, "name": name, "account_id": acc_id, "is_radiant": slot <= 5})
        elif mA:
            slot = int(mA.group(1))
            name = mA.group(2).strip()
            if 1 <= slot <= 10 and name and name != "SourceTV":
                self._status_buffer.append({"slot": slot, "name": name, "account_id": None, "is_radiant": slot <= 5})

        if self._status_buffer and (len(self._status_buffer) >= 10 or "#end" in line or "Official Valve Server" in line):
            current_id = getattr(self, "_current_match_id", None)
            key = (current_id, tuple((p["slot"], p["name"]) for p in self._status_buffer))
            if key != self._last_status_key:
                self._last_status_key = key
                self.signals.status_players.emit({
                    "match_id": current_id,
                    "players": list(self._status_buffer),
                })
            self._status_buffer.clear()

        found_ids: set[str] = set()

        for m in STEAMID64_RE.finditer(line):
            found_ids.add(m.group(1))

        for m in STEAMID3_RE.finditer(line):
            acc_id = int(m.group(1))
            found_ids.add(str(acc_id + STEAM_ID64_OFFSET))

        for m in ACCOUNT_ID_RE.finditer(line):
            acc_id = int(m.group(1))
            found_ids.add(str(acc_id + STEAM_ID64_OFFSET))

        if found_ids:
            self.signals.steam_ids.emit(list(found_ids))

        profile = PROFILE_RE.search(line)
        if profile:
            log.debug("Найден профиль Steam: %s", profile.group(1))
