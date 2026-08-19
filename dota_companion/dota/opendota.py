
from __future__ import annotations

import asyncio

import aiohttp

from .match_state import Player, ANONYMOUS_ACCOUNT_ID, sanitize_name
from ..core.logger import get_logger

log = get_logger("opendota")

BASE_URL = "https://api.opendota.com/api"
USER_AGENT = "DotaCompanion/1.0 (desktop companion tool)"

FALLBACK_HEROES: dict[int, str] = {
    1: "Anti-Mage", 2: "Axe", 3: "Bane", 4: "Bloodseeker", 5: "Crystal Maiden",
    6: "Drow Ranger", 7: "Earthshaker", 8: "Juggernaut", 9: "Mirana", 10: "Morphling",
    11: "Shadow Fiend", 12: "Phantom Lancer", 13: "Puck", 14: "Pudge", 15: "Razor",
    16: "Sand King", 17: "Storm Spirit", 18: "Sven", 19: "Tiny", 20: "Vengeful Spirit",
    21: "Windranger", 22: "Zeus", 23: "Kunkka", 25: "Lina", 26: "Lion", 27: "Shadow Shaman",
    28: "Slardar", 29: "Tidehunter", 30: "Witch Doctor", 31: "Lich", 32: "Riki", 33: "Enigma",
    34: "Tinker", 35: "Sniper", 36: "Necrophos", 37: "Warlock", 38: "Beastmaster", 39: "Queen of Pain",
    40: "Venomancer", 41: "Faceless Void", 42: "Wraith King", 43: "Death Prophet", 44: "Phantom Assassin",
    45: "Pugna", 46: "Templar Assassin", 47: "Viper", 48: "Luna", 49: "Dragon Knight", 50: "Dazzle",
    51: "Clockwerk", 52: "Leshrac", 53: "Nature's Prophet", 54: "Lifestealer", 55: "Dark Seer", 56: "Clinkz",
    57: "Omniknight", 58: "Enchantress", 59: "Huskar", 60: "Night Stalker", 61: "Broodmother", 62: "Bounty Hunter",
    63: "Weaver", 64: "Jakiro", 65: "Batrider", 66: "Chen", 67: "Spectre", 68: "Ancient Apparition",
    69: "Doom", 70: "Ursa", 71: "Spirit Breaker", 72: "Gyrocopter", 73: "Alchemist", 74: "Invoker",
    75: "Silencer", 76: "Outworld Destroyer", 77: "Lycan", 78: "Brewmaster", 79: "Shadow Demon",
    80: "Lone Druid", 81: "Chaos Knight", 82: "Meepo", 83: "Treant Protector", 84: "Ogre Magi",
    85: "Undying", 86: "Rubick", 87: "Disruptor", 88: "Nyx Assassin", 89: "Naga Siren", 90: "Keeper of the Light",
    91: "Io", 92: "Visage", 93: "Slark", 94: "Medusa", 95: "Troll Warlord", 96: "Centaur Warrunner",
    97: "Magnus", 98: "Timbersaw", 99: "Bristleback", 100: "Tusk", 101: "Skywrath Mage", 102: "Abaddon",
    103: "Elder Titan", 104: "Legion Commander", 105: "Techies", 106: "Ember Spirit", 107: "Earth Spirit",
    108: "Underlord", 109: "Terrorblade", 110: "Phoenix", 111: "Oracle", 112: "Winter Wyvern",
    113: "Arc Warden", 114: "Monkey King", 119: "Dark Willow", 120: "Pangolier", 121: "Grimstroke",
    123: "Hoodwink", 126: "Void Spirit", 128: "Snapfire", 129: "Mars", 135: "Dawnbreaker", 136: "Marci",
    137: "Primal Beast", 138: "Muerta", 145: "Ringmaster", 146: "Kez"
}


class OpenDotaError(Exception):
    pass


async def fetch_player_name(session: aiohttp.ClientSession, account_id: int) -> str | None:
    """Получение ника игрока по account_id через OpenDota API."""
    url = f"{BASE_URL}/players/{account_id}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status == 200:
                data = await resp.json()
                profile = data.get("profile", {}) or {}
                name = profile.get("personaname") or profile.get("name")
                if name:
                    return sanitize_name(name)
    except Exception as exc:
        log.debug("Ошибка получения ника для %d: %s", account_id, exc)
    return None


async def fetch_player_by_search(session: aiohttp.ClientSession, query: str) -> int | None:
    """Поиск account_id игрока по нику через OpenDota Search API."""
    import urllib.parse
    url = f"{BASE_URL}/search?q={urllib.parse.quote(query)}"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and data:
                    acc_id = data[0].get("account_id")
                    if isinstance(acc_id, int) and acc_id > 0:
                        return acc_id
    except Exception as exc:
        log.debug("Ошибка поиска по нику '%s': %s", query, exc)
    return None


async def fetch_heroes(session: aiohttp.ClientSession) -> dict[int, str]:
    url = f"{BASE_URL}/heroes"
    headers = {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {int(h["id"]): h.get("localized_name", f"Hero {h['id']}") for h in data}
    except Exception as exc:
        log.warning("Не удалось загрузить героев с OpenDota (%s), использую локальный справочник", exc)
    return FALLBACK_HEROES.copy()


async def fetch_match(
    session: aiohttp.ClientSession,
    match_id: int,
    heroes: dict[int, str],
    retries: int = 1,
    backoff: float = 0.5,
) -> list[Player]:
    url = f"{BASE_URL}/matches/{match_id}"
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    players = _parse_match(data, heroes)
                    if players:
                        return players
        except Exception as exc:
            log.debug("Попытка %d fetch_match %s: %s", attempt + 1, match_id, exc)

        if attempt < retries - 1:
            await asyncio.sleep(backoff)

    return []


async def fetch_live_match(
    session: aiohttp.ClientSession,
    match_id: int,
    heroes: dict[int, str],
) -> list[Player]:
    """Запрос текущего активного матча из OpenDota Live Games API."""
    url = f"{BASE_URL}/live"
    headers = {"User-Agent": USER_AGENT}
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        if resp.status != 200:
            raise OpenDotaError(f"GET /live -> {resp.status}")
        data = await resp.json()

    target_id_str = str(match_id)
    match_info = None
    if isinstance(data, list):
        for m in data:
            if isinstance(m, dict) and str(m.get("match_id")) == target_id_str:
                match_info = m
                break

    if not match_info:
        raise OpenDotaError(f"Матч {match_id} ещё не отображается в Live API")

    players: list[Player] = []
    for p in match_info.get("players", []):
        account_id = p.get("account_id")
        if account_id is None or account_id == ANONYMOUS_ACCOUNT_ID:
            continue
        hero_id = p.get("hero_id")
        name = (
            sanitize_name(p.get("name"))
            or sanitize_name(p.get("personaname"))
            or f"Player {account_id}"
        )
        is_radiant = p.get("team") == 0
        players.append(
            Player(
                account_id=account_id,
                name=name,
                hero_id=hero_id,
                hero_name=heroes.get(hero_id, "") if hero_id else "",
                is_radiant=is_radiant,
            )
        )
    return players


def _parse_match(data: dict, heroes: dict[int, str]) -> list[Player]:
    players: list[Player] = []
    for p in data.get("players", []):
        account_id = p.get("account_id")
        if account_id is None or account_id == ANONYMOUS_ACCOUNT_ID:
            continue
        hero_id = p.get("hero_id")
        name = (
            sanitize_name(p.get("personaname"))
            or sanitize_name(p.get("name"))
            or f"Player {account_id}"
        )
        players.append(
            Player(
                account_id=account_id,
                name=name,
                hero_id=hero_id,
                hero_name=heroes.get(hero_id, f"Hero {hero_id}") if hero_id else "",
                is_radiant=bool(p.get("isRadiant")),
            )
        )
    return players
