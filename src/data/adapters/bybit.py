"""Bybit adapter: REST v5 orderbook + public spot websocket (snapshot + deltas).

Note: Bybit geo-blocks some regions (including the US); the feed worker will
surface that as a disconnected feed rather than crashing.
"""
from __future__ import annotations

import json
from typing import Optional

from src.data.clients import FeedError, HttpClient
from src.data.adapters.base import ExchangeAdapter, LiveBook
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class BybitAdapter(ExchangeAdapter):
    name = "Bybit"
    supports_ws = True

    REST_URL = "https://api.bybit.com/v5/market/orderbook"
    WS_URL = "wss://stream.bybit.com/v5/public/spot"

    def __init__(self) -> None:
        self._book = LiveBook()

    def pair(self, base: str) -> str:
        return pair_for("bybit", base)

    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 50) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(
            self.REST_URL,
            {"category": "spot", "symbol": symbol, "limit": min(limit, 200)},
        )
        if data.get("retCode") != 0:
            raise FeedError(f"Bybit API error: {data.get('retCode')} {data.get('retMsg')}")
        result = data.get("result") or {}
        return make_snapshot(
            self.name,
            symbol,
            result.get("b", []),
            result.get("a", []),
            sequence=result.get("u"),
        )

    def ws_url(self, base: str) -> str:
        return self.WS_URL

    def ws_subscribe_message(self, base: str) -> Optional[str]:
        return json.dumps({"op": "subscribe", "args": [f"orderbook.50.{self.pair(base)}"]})

    def reset_ws_state(self) -> None:
        self._book = LiveBook()

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        msg = json.loads(raw)
        if not isinstance(msg, dict) or not str(msg.get("topic", "")).startswith("orderbook."):
            return None
        msg_type = msg.get("type")
        data = msg.get("data") or {}
        if msg_type not in ("snapshot", "delta"):
            return None
        self._book.apply(
            data.get("b", []),
            data.get("a", []),
            replace=(msg_type == "snapshot"),
        )
        return self._book.snapshot(self.name, self.pair(base), sequence=data.get("u"))
