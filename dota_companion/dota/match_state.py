                                                                        
from __future__ import annotations

import re
from dataclasses import dataclass, field

ANONYMOUS_ACCOUNT_ID = 4294967295
STEAM_ID64_OFFSET = 76561197960265728

# Невидимые символы, которыми маскируют ники: zero-width пробелы, joiners и т.п.
_INVISIBLE_CHARS = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff]+"
)


def sanitize_name(name: str) -> str:
    """Убирает невидимые символы и пробелы из ника игрока."""
    return _INVISIBLE_CHARS.sub("", name or "").strip()


@dataclass
class Player:
                           

    account_id: int | None = None
    name: str = "Неизвестный игрок"
    hero_id: int | None = None
    hero_name: str = ""
    is_radiant: bool | None = None
    is_local: bool = False

                                       
    item_id: str = ""                                                         
    inventory_value: float | None = None
    currency: str = ""
    value_status: str = ""                                             
    value_error: str = ""                                                          

    @property
    def is_anonymous(self) -> bool:
        return self.account_id is None or self.account_id == ANONYMOUS_ACCOUNT_ID

    @property
    def steam_id64(self) -> int | None:
                                                                     
        if self.account_id is None or self.is_anonymous:
            return None
        return self.account_id + STEAM_ID64_OFFSET

    @property
    def profile_url(self) -> str:
                                             
        if self.steam_id64 is None:
            return ""
        return f"https://steamcommunity.com/profiles/{self.steam_id64}"


@dataclass
class MatchState:
                                                             

    match_id: int | None = None
    game_state: str = ""
    map_name: str = ""
    players: list[Player] = field(default_factory=list)

                                                                          

    @property
    def radiant(self) -> list[Player]:
        return [p for p in self.players if p.is_radiant]

    @property
    def dire(self) -> list[Player]:
        return [p for p in self.players if p.is_radiant is False]

    def player_by_account(self, account_id: int | None) -> Player | None:
        if account_id is None:
            return None
        for p in self.players:
            if p.account_id == account_id:
                return p
        return None

    def player_by_name(self, name: str) -> Player | None:
        if not name:
            return None
        target = sanitize_name(name).lower()
        for p in self.players:
            if p.name and sanitize_name(p.name).lower() == target:
                return p
        return None

    def set_players(self, players: list[Player]) -> None:
        if not self.players:
            self.players = players
            return

        existing_radiant = [p for p in self.players if p.is_radiant]
        existing_dire = [p for p in self.players if p.is_radiant is False]

        incoming_radiant = [p for p in players if p.is_radiant]
        incoming_dire = [p for p in players if p.is_radiant is False]

        def merge_group(old_group: list[Player], inc_group: list[Player]) -> list[Player]:
            res: list[Player] = []
            for i in range(max(len(old_group), len(inc_group))):
                old_p = old_group[i] if i < len(old_group) else None
                inc_p = inc_group[i] if i < len(inc_group) else None

                if not inc_p:
                    if old_p:
                        res.append(old_p)
                    continue

                if old_p:
                    inc_name_generic = inc_p.name.startswith("Игрок ") or inc_p.name.startswith("Player ") or not inc_p.name
                    old_name_custom = old_p.name and not old_p.name.startswith("Игрок ") and not old_p.name.startswith("Player ")
                    if inc_name_generic and old_name_custom:
                        inc_p.name = old_p.name

                    if (inc_p.account_id is None or inc_p.account_id <= 0) and old_p.account_id and old_p.account_id > 0:
                        inc_p.account_id = old_p.account_id

                    if old_p.value_status == "ok":
                        inc_p.item_id = old_p.item_id
                        inc_p.inventory_value = old_p.inventory_value
                        inc_p.currency = old_p.currency
                        inc_p.value_status = old_p.value_status
                        inc_p.value_error = old_p.value_error

                    if not inc_p.hero_id and old_p.hero_id:
                        inc_p.hero_id = old_p.hero_id
                        inc_p.hero_name = old_p.hero_name

                    if old_p.is_local:
                        inc_p.is_local = True

                res.append(inc_p)
            return res

        merged_rad = merge_group(existing_radiant, incoming_radiant if incoming_radiant else players[:5])
        merged_dire = merge_group(existing_dire, incoming_dire if incoming_dire else players[5:])
        self.players = merged_rad + merged_dire

    def sort_players(self) -> None:
                                                                           
        self.players.sort(key=lambda p: (0 if p.is_radiant else 1, p.name.lower()))
