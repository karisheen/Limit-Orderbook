"""Binance.US adapter: REST depth snapshots + partial-book websocket stream."""

from __future__ import annotations

from typing import Optional

from src.data.clients import HttpClient
from src.data.adapters.base import ExchangeAdapter
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for
from src.workstation.precision import exact_json_loads


class BinanceUSAdapter(ExchangeAdapter):
    name = "Binance.US"
    supports_ws = True

    REST_URL = "https://api.binance.us/api/v3/depth"
    WS_BASE = "wss://stream.binance.us:9443/ws"

    def pair(self, base: str) -> str:
        return pair_for("binanceus", base)

    def fetch_snapshot(
        self, http: HttpClient, base: str, limit: int = 100
    ) -> OrderBookSnapshot:
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
        msg = exact_json_loads(raw)
        if not isinstance(msg, dict):
            raise ValueError("Binance.US message must be an object")
        if "code" in msg:
            raise ValueError(f"Binance.US subscription rejected: {msg.get('msg')}")
        if "s" in msg and msg["s"] != self.pair(base):
            return None
        if "bids" not in msg and "asks" not in msg:
            return None
        if "bids" not in msg or "asks" not in msg:
            raise ValueError("incomplete Binance.US partial snapshot")
        return make_snapshot(
            self.name,
            self.pair(base),
            msg["bids"],
            msg["asks"],
            sequence=msg.get("lastUpdateId"),
        )
