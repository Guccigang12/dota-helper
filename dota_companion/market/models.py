                                                                    
from __future__ import annotations

from dataclasses import dataclass

CURRENCIES = ("rub", "usd", "eur", "uah", "kzt", "byn", "cny")

CURRENCY_SYMBOLS = {
    "rub": "₽",
    "usd": "$",
    "eur": "€",
    "uah": "₴",
    "kzt": "₸",
    "byn": "Br",
    "cny": "¥",
}


@dataclass
class InventoryValue:
                                                    

    item_id: int
    amount: float
    currency: str = "rub"
    raw: dict | None = None

    def formatted(self, digits: int | None = None) -> str:
        symbol = CURRENCY_SYMBOLS.get(self.currency, self.currency.upper())
        if digits is None:
            digits = 0 if self.amount >= 100 else 2
        return f"{self.amount:,.{digits}f} {symbol}"


def format_value(amount: float | None, currency: str = "rub") -> str:
                                                                        
    if amount is None:
        return "—"
    return InventoryValue(item_id=0, amount=amount, currency=currency).formatted()
