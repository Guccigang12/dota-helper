"""Обёртка над Steam Web API: IDOTA2MatchStats_570/GetRealtimeStats + ISteamUser."""

from __future__ import annotations

import aiohttp

from .match_state import Player, ANONYMOUS_ACCOUNT_ID, sanitize_name, STEAM_ID64_OFFSET
from ..core.logger import get_logger

log = get_logger("steam_api")

REALTIME_STATS_URL = (
    "https://api.steampowered.com/IDOTA2MatchStats_570/GetRealtimeStats/v1"
)
RESOLVE_VANITY_URL = (
    "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1"
)
GET_PLAYER_SUMMARIES_URL = (
    "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2"
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


async def resolve_vanity_url(
    session: aiohttp.ClientSession,
    vanity: str,
    api_key: str,
) -> int | None:
    """Пробует разрезолвить Steam vanity URL → account_id.

    Работает если игрок использует ник как кастомный URL профиля.
    Возвращает account_id (32-бит) или None.
    """
    if not api_key or not vanity:
        return None
    try:
        async with session.get(
            RESOLVE_VANITY_URL,
            params={"key": api_key, "vanityurl": vanity},
            timeout=aiohttp.ClientTimeout(total=4),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        r = data.get("response", {})
        if r.get("success") == 1:
            steam_id64 = int(r["steamid"])
            account_id = steam_id64 - STEAM_ID64_OFFSET
            if account_id > 0:
                log.info("ResolveVanityURL '%s' → account_id=%d", vanity, account_id)
                return account_id
    except Exception as exc:
        log.debug("ResolveVanityURL ошибка для '%s': %s", vanity, exc)
    return None


async def search_player_by_name(
    session: aiohttp.ClientSession,
    name: str,
    api_key: str,
) -> int | None:
    """Поиск account_id через Steam API (ResolveVanityURL) с несколькими вариантами ника.

    Порядок попыток:
    1. name — точный ник
    2. name.lower() — строчный вариант
    3. name без пробелов
    4. name.lower() без пробелов

    Возвращает account_id или None если не найдено.
    """
    if not api_key or not name:
        return None

    variants: list[str] = []
    for v in [name, name.lower(), name.replace(" ", ""), name.lower().replace(" ", "")]:
        if v and v not in variants:
            variants.append(v)

    for variant in variants:
        acc_id = await resolve_vanity_url(session, variant, api_key)
        if acc_id:
            return acc_id

    log.debug("Steam API: не найден игрок '%s' ни в одном варианте", name)
    return None
