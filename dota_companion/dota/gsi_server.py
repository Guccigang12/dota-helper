
from __future__ import annotations

import json

from aiohttp import web
from PyQt6.QtCore import QObject, pyqtSignal

from ..core.logger import get_logger

log = get_logger("gsi")


class GsiSignals(QObject):
                                                                        

    # match_id — число больше 2^31, pyqtSignal(int) обрезает его до 32 бит,
    # поэтому используем object (Python int сохраняется целиком)
    match_started = pyqtSignal(object)                
    match_ended = pyqtSignal()                             
    game_state_changed = pyqtSignal(str)                    
    player_update = pyqtSignal(dict)                             
    roster_update = pyqtSignal(dict)                             
    server_error = pyqtSignal(str)
    server_steam_id_found = pyqtSignal(str)


class GsiServer:
                                                    

    def __init__(self, loop, port: int = 34567, auth_token: str = "") -> None:
        self._loop = loop
        self._port = port
        self._auth_token = auth_token
        self.signals = GsiSignals()

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._current_match_id: int | None = None
        self._last_state = ""
        self._last_roster_key: tuple | None = None

                                                                          

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="127.0.0.1", port=self._port)
        try:
            await self._site.start()
        except OSError as exc:
            self.signals.server_error.emit(f"Не удалось запустить GSI-сервер на порту {self._port}: {exc}")
            raise
        log.info("GSI-сервер слушает 127.0.0.1:%s", self._port)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()

                                                                          

    async def _handle(self, request: web.Request) -> web.Response:
        if self._auth_token:
            auth = request.headers.get("Authorization", "")
            if auth != f"Token {self._auth_token}":
                return web.Response(status=401, text="unauthorized")

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.Response(status=400, text="bad json")

        self._process(data)
        return web.Response(status=200, text="ok")

    def _process(self, data: dict) -> None:
        provider = data.get("provider", {})
        match = data.get("map", {})

        # Dota 2 присылает matchid строкой (напр. "7051987233"), а не числом
        try:
            match_id = int(match.get("matchid"))
        except (TypeError, ValueError):
            match_id = 0
        if match_id > 0 and match_id != self._current_match_id:
            self._current_match_id = match_id
            log.info("Новый матч обнаружен: %s", match_id)
            self.signals.match_started.emit(match_id)
        elif match_id == 0 and self._current_match_id is not None:
            # игра закончилась / возврат в меню — сбрасываем текущий матч
            log.info("Матч завершён (matchid сброшен в 0)")
            self._current_match_id = None
            self.signals.match_ended.emit()

        # server_steam_id — уникальный ID игрового сервера для Steam Web API
        server_steam_id = str(match.get("server_steam_id") or "").strip()
        if server_steam_id and server_steam_id != "0":
            self.signals.server_steam_id_found.emit(server_steam_id)

        state = match.get("game_state", "")
        if state and state != self._last_state:
            self._last_state = state
            self.signals.game_state_changed.emit(state)

        player = data.get("player", {})
        hero = data.get("hero", {})
        items = data.get("items", {})
        if player or hero or items:
            self.signals.player_update.emit(
                {
                    "provider": provider,
                    "player": player,
                    "hero": hero,
                    "items": items,
                    "map": match,
                }
            )

        self._emit_roster(data)

    # ------------------------------------------------------------------
    # Ростер всех 10 игроков в реальном времени
    #
    # Dota 2 присылает в GSI данные всех игроков матча:
    #   player.team2.player0..4  -> Radiant (силы света)
    #   player.team3.player5..9  -> Dire   (силы тьмы)
    # каждый со своим steamid и name; герой — в hero.teamX.playerN.id.
    # В рейтинговых матчах steamid/name скрыты до DOTA_GAMERULES_STATE_STRATEGY_TIME.
    # ------------------------------------------------------------------

    def _emit_roster(self, data: dict) -> None:
        roster = self._parse_roster(data)
        key = tuple(
            (p["steam_id64"], p["name"], p["hero_id"], p["is_radiant"]) for p in roster
        )
        if key == self._last_roster_key:
            return
        self._last_roster_key = key
        if not roster:
            return
        try:
            local_steam_id64 = int(data.get("provider", {}).get("steamid"))
        except (TypeError, ValueError):
            local_steam_id64 = None
        log.info("GSI-ростер: %d игроков", len(roster))
        self.signals.roster_update.emit(
            {"local_steam_id64": local_steam_id64, "players": roster}
        )

    @staticmethod
    def _parse_roster(data: dict) -> list[dict]:
        players = data.get("player", {}) or {}
        heroes = data.get("hero", {}) or {}
        provider = data.get("provider", {}) or {}

        local_name = str(players.get("name") or "").strip()
        try:
            local_steam_id64 = int(provider.get("steamid") or players.get("steamid") or 0)
        except (TypeError, ValueError):
            local_steam_id64 = 0

        result: list[dict] = []

        team2_players = players.get("team2", {}) if isinstance(players, dict) else {}
        team3_players = players.get("team3", {}) if isinstance(players, dict) else {}

        team2_heroes = heroes.get("team2", {}) if isinstance(heroes, dict) else {}
        team3_heroes = heroes.get("team3", {}) if isinstance(heroes, dict) else {}

        has_teams = (
            isinstance(team2_players, dict) and bool(team2_players)
        ) or (
            isinstance(team3_players, dict) and bool(team3_players)
        ) or (
            isinstance(team2_heroes, dict) and bool(team2_heroes)
        ) or (
            isinstance(team3_heroes, dict) and bool(team3_heroes)
        )

        if has_teams:
            # Radiant slots: player0..player4
            for i in range(5):
                slot_key = f"player{i}"
                p_info = team2_players.get(slot_key, {}) if isinstance(team2_players, dict) else {}
                h_info = team2_heroes.get(slot_key, {}) if isinstance(team2_heroes, dict) else {}

                steam_id64 = 0
                if isinstance(p_info, dict):
                    try:
                        steam_id64 = int(p_info.get("steamid") or 0)
                    except (TypeError, ValueError):
                        steam_id64 = 0

                name = ""
                if isinstance(p_info, dict):
                    name = str(p_info.get("name") or "").strip()

                if local_steam_id64 > 0 and (steam_id64 == local_steam_id64 or (local_name and name == local_name)):
                    steam_id64 = local_steam_id64

                hero_id = None
                if isinstance(h_info, dict):
                    hero_id = h_info.get("id")
                    if isinstance(hero_id, str) and hero_id.isdigit():
                        hero_id = int(hero_id)

                if not name:
                    name = f"Игрок {i+1}"

                result.append(
                    {
                        "steam_id64": steam_id64,
                        "name": name,
                        "hero_id": hero_id if isinstance(hero_id, int) else None,
                        "is_radiant": True,
                        "slot": i,
                    }
                )

            # Dire slots: player5..player9 (или player0..player4)
            for i in range(5):
                slot_key_alt = f"player{i+5}"
                slot_key_def = f"player{i}"
                p_info = {}
                if isinstance(team3_players, dict):
                    p_info = team3_players.get(slot_key_alt) or team3_players.get(slot_key_def) or {}

                h_info = {}
                if isinstance(team3_heroes, dict):
                    h_info = team3_heroes.get(slot_key_alt) or team3_heroes.get(slot_key_def) or {}

                steam_id64 = 0
                if isinstance(p_info, dict):
                    try:
                        steam_id64 = int(p_info.get("steamid") or 0)
                    except (TypeError, ValueError):
                        steam_id64 = 0

                name = ""
                if isinstance(p_info, dict):
                    name = str(p_info.get("name") or "").strip()

                if local_steam_id64 > 0 and (steam_id64 == local_steam_id64 or (local_name and name == local_name)):
                    steam_id64 = local_steam_id64

                hero_id = None
                if isinstance(h_info, dict):
                    hero_id = h_info.get("id")
                    if isinstance(hero_id, str) and hero_id.isdigit():
                        hero_id = int(hero_id)

                if not name:
                    name = f"Игрок {i+6}"

                result.append(
                    {
                        "steam_id64": steam_id64,
                        "name": name,
                        "hero_id": hero_id if isinstance(hero_id, int) else None,
                        "is_radiant": False,
                        "slot": i + 5,
                    }
                )
        else:
            # Плоский формат (матчмейкинг / только локальный игрок):
            local_name = str(players.get("name") or "Вы").strip()
            is_local_radiant = players.get("team_name") != "dire"

            local_hero_id = heroes.get("id")
            if isinstance(local_hero_id, str) and local_hero_id.isdigit():
                local_hero_id = int(local_hero_id)
            if not isinstance(local_hero_id, int):
                local_hero_id = None

            for i in range(5):
                if is_local_radiant and i == 0:
                    result.append(
                        {
                            "steam_id64": local_steam_id64,
                            "name": local_name,
                            "hero_id": local_hero_id,
                            "is_radiant": True,
                            "slot": 0,
                        }
                    )
                else:
                    result.append(
                        {
                            "steam_id64": 0,
                            "name": f"Игрок {i+1}",
                            "hero_id": None,
                            "is_radiant": True,
                            "slot": i,
                        }
                    )

            for i in range(5):
                if not is_local_radiant and i == 0:
                    result.append(
                        {
                            "steam_id64": local_steam_id64,
                            "name": local_name,
                            "hero_id": local_hero_id,
                            "is_radiant": False,
                            "slot": 5,
                        }
                    )
                else:
                    result.append(
                        {
                            "steam_id64": 0,
                            "name": f"Игрок {i+6}",
                            "hero_id": None,
                            "is_radiant": False,
                            "slot": i + 5,
                        }
                    )

        return result
