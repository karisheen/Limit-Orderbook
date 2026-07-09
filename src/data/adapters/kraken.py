"""Kraken adapter: REST depth + v2 websocket book channel (snapshot + deltas)."""
from __future__ import annotations

import json
from typing import Optional

from src.data.clients import FeedError, HttpClient
from src.data.adapters.base import ExchangeAdapter, LiveBook
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class KrakenAdapter(ExchangeAdapter):
    name = "Kraken"
    supports_ws = True

    REST_URL = "https://api.kraken.com/0/public/Depth"
    WS_URL = "wss://ws.kraken.com/v2"

    def __init__(self) -> None:
        self._book = LiveBook()

    def rest_pair(self, base: str) -> str:
        return pair_for("kraken_rest", base)

    def ws_pair(self, base: str) -> str:
        return pair_for("kraken_ws", base)

    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 100) -> OrderBookSnapshot:
        data = http.get_json(self.REST_URL, {"pair": self.rest_pair(base), "count": limit})
        errors = data.get("error") or []
        if errors:
            raise FeedError(f"Kraken API error: {errors}")
        result = data.get("result") or {}
        if not result:
            raise FeedError("Kraken API returned empty result")
        # Kraken keys results by its internal pair name (e.g. XXBTZUSD).
        book = next(iter(result.values()))
        return make_snapshot(self.name, self.rest_pair(base), book["bids"], book["asks"])

    def ws_url(self, base: str) -> str:
        return self.WS_URL

    def ws_subscribe_message(self, base: str) -> Optional[str]:
        return json.dumps(
            {
                "method": "subscribe",
                "params": {"channel": "book", "symbol": [self.ws_pair(base)], "depth": 25},
            }
        )

    def reset_ws_state(self) -> None:
        self._book = LiveBook()

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        msg = json.loads(raw)
        if not isinstance(msg, dict) or msg.get("channel") != "book":
            return None
        msg_type = msg.get("type")
        if msg_type not in ("snapshot", "update"):
            return None
        payload = (msg.get("data") or [None])[0]
        if not payload:
            return None
        bids = [(level["price"], level["qty"]) for level in payload.get("bids", [])]
        asks = [(level["price"], level["qty"]) for level in payload.get("asks", [])]
        self._book.apply(bids, asks, replace=(msg_type == "snapshot"))
        return self._book.snapshot(self.name, self.ws_pair(base))
