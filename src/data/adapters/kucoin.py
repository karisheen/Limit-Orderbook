"""KuCoin adapter: public REST level-2 book (polling).

KuCoin's websocket requires a token bootstrap handshake; the public REST
level2_100 endpoint is used instead for simplicity and reliability.
"""
from __future__ import annotations

from src.data.clients import FeedError, HttpClient
from src.data.adapters.base import ExchangeAdapter
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class KucoinAdapter(ExchangeAdapter):
    name = "KuCoin"
    supports_ws = False
    rest_poll_interval = 2.0

    REST_URL = "https://api.kucoin.com/api/v1/market/orderbook/level2_100"

    def pair(self, base: str) -> str:
        return pair_for("kucoin", base)

    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 100) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(self.REST_URL, {"symbol": symbol})
        if data.get("code") != "200000":
            raise FeedError(f"KuCoin API error: code={data.get('code')} msg={data.get('msg')}")
        book = data.get("data") or {}
        return make_snapshot(
            self.name,
            symbol,
            book.get("bids", []),
            book.get("asks", []),
            sequence=_safe_int(book.get("sequence")),
        )


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
