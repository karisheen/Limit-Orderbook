"""Symbol mapping registry.

Canonical assets are plain base symbols ("BTC"). Each venue formats its own
pair string; venue-specific base renames (e.g. Kraken's XBT) live here so
adapters stay declarative.
"""

from __future__ import annotations

from typing import Callable, Dict, Tuple

CANONICAL_ASSETS: Tuple[str, ...] = (
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "LTC",
    "LINK",
    "AVAX",
    "DOT",
)


def _kraken_base(base: str) -> str:
    return "XBT" if base == "BTC" else base


# venue key -> canonical base -> venue pair string
PAIR_RULES: Dict[str, Callable[[str], str]] = {
    "binanceus": lambda b: f"{b}USDT",
    "coinbase": lambda b: f"{b}-USD",
    "kraken_rest": lambda b: f"{_kraken_base(b)}USD",
    "kraken_ws": lambda b: f"{b}/USD",
    "kucoin": lambda b: f"{b}-USDT",
    "bybit": lambda b: f"{b}USDT",
}


def pair_for(venue_key: str, base: str) -> str:
    """Return the venue-native pair string for a canonical base asset."""
    rule = PAIR_RULES.get(venue_key)
    if rule is None:
        raise ValueError(f"Unknown venue mapping: {venue_key}")
    if base not in CANONICAL_ASSETS:
        raise ValueError(f"Unknown canonical asset: {base}")
    return rule(base)
