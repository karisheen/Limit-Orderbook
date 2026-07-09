"""Coinbase Exchange adapter: public REST level-2 book (polling).

Coinbase's level2 websocket channel now requires authentication, so this
venue runs on REST snapshots only.
"""
from __future__ import annotations

from src.data.clients import HttpClient
from src.data.adapters.base import ExchangeAdapter
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class CoinbaseAdapter(ExchangeAdapter):
    name = "Coinbase"
    supports_ws = False
    # level=2 returns the full aggregated book (a large payload), so poll
    # a little less aggressively than the default.
    rest_poll_interval = 3.0

    REST_BASE = "https://api.exchange.coinbase.com"

    def pair(self, base: str) -> str:
        return pair_for("coinbase", base)

    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 100) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(f"{self.REST_BASE}/products/{symbol}/book", {"level": 2})
        return make_snapshot(
            self.name,
            symbol,
            data["bids"],
            data["asks"],
            sequence=data.get("sequence"),
        )
