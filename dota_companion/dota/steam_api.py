"""Обёртка над Steam Web API: IDOTA2MatchStats_570/GetRealtimeStats."""

from __future__ import annotations

import aiohttp

from .match_state import Player, ANONYMOUS_ACCOUNT_ID, sanitize_name
from ..core.logger import get_logger

log = get_logger("steam_api")

REALTIME_STATS_URL = (
    "https://api.steampowered.com/IDOTA2MatchStats_570/GetRealtimeStats/v1"
)


async def fetch_realtime_stats(
    session: aiohttp.ClientSession,
    server_steam_id: str,
    api_key: str,
) -> list[Player]:
    """Запрашивает GetRealtimeStats и возвращает список Player с account_id, name, hero_id, is_radiant."""

    if not api_key:
        log.warning("steam_api_key не задан — GetRealtimeStats недоступен")
        return []

    params = {"server_steam_id": server_steam_id, "key": api_key}
    async with session.get(
        REALTIME_STATS_URL,
        params=params,
        timeout=aiohttp.ClientTimeout(total=5),
    ) as resp:
        if resp.status != 200:
            log.warning("GetRealtimeStats HTTP %s", resp.status)
            return []
        data = await resp.json()

    teams = data.get("teams", [])
    if not teams:
        log.debug("GetRealtimeStats: нет данных teams")
        return []

    players: list[Player] = []
    for team_idx, team in enumerate(teams):
        is_radiant = team.get("team_number", team_idx) == 0
        for p in team.get("players", []):
            account_id = p.get("accountid")
            if account_id is None or account_id == ANONYMOUS_ACCOUNT_ID or account_id <= 0:
                continue
            hero_id = p.get("heroid")
            if isinstance(hero_id, str) and hero_id.isdigit():
                hero_id = int(hero_id)
            name = sanitize_name(p.get("name") or "") or f"Player {account_id}"
            players.append(
                Player(
                    account_id=account_id,
                    name=name,
                    hero_id=hero_id if isinstance(hero_id, int) else None,
                    is_radiant=is_radiant,
                )
            )

    log.info("GetRealtimeStats: получено %d игроков (server_steam_id=%s)", len(players), server_steam_id)
    return players
