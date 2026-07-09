"""Binance.US adapter: REST depth snapshots + partial-book websocket stream."""
from __future__ import annotations

import json
from typing import Optional

from src.data.clients import HttpClient
from src.data.adapters.base import ExchangeAdapter
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class BinanceUSAdapter(ExchangeAdapter):
    name = "Binance.US"
    supports_ws = True

    REST_URL = "https://api.binance.us/api/v3/depth"
    WS_BASE = "wss://stream.binance.us:9443/ws"

    def pair(self, base: str) -> str:
        return pair_for("binanceus", base)

    def fetch_snapshot(self, http: HttpClient, base: str, limit: int = 100) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(self.REST_URL, {"symbol": symbol, "limit": limit})
        return make_snapshot(
            self.name,
            symbol,
            data["bids"],
            data["asks"],
            sequence=data.get("lastUpdateId"),
        )

    def ws_url(self, base: str) -> str:
        # Partial book depth stream: pushes a full top-20 snapshot every second,
        # so no diff bookkeeping is needed.
        return f"{self.WS_BASE}/{self.pair(base).lower()}@depth20@1000ms"

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        msg = json.loads(raw)
        if not isinstance(msg, dict) or "bids" not in msg or "asks" not in msg:
            return None
        return make_snapshot(
            self.name,
            self.pair(base),
            msg["bids"],
            msg["asks"],
            sequence=msg.get("lastUpdateId"),
        )
