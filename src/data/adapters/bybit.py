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
from src.workstation.precision import exact_json_loads, nonnegative_int


class BybitAdapter(ExchangeAdapter):
    name = "Bybit"
    supports_ws = True

    REST_URL = "https://api.bybit.com/v5/market/orderbook"
    WS_URL = "wss://stream.bybit.com/v5/public/spot"

    def __init__(self) -> None:
        self.reset_ws_state()

    def pair(self, base: str) -> str:
        return pair_for("bybit", base)

    def fetch_snapshot(
        self, http: HttpClient, base: str, limit: int = 50
    ) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(
            self.REST_URL,
            {"category": "spot", "symbol": symbol, "limit": min(limit, 200)},
        )
        if data.get("retCode") != 0:
            raise FeedError(
                f"Bybit API error: {data.get('retCode')} {data.get('retMsg')}"
            )
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
        return json.dumps(
            {"op": "subscribe", "args": [f"orderbook.50.{self.pair(base)}"]}
        )

    def reset_ws_state(self) -> None:
        self._book = LiveBook(50)
        self._update_id: Optional[int] = None
        self._cross_sequence: Optional[int] = None

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        try:
            msg = exact_json_loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("Bybit message must be an object")
            if msg.get("success") is False:
                raise ValueError(f"Bybit subscription rejected: {msg.get('ret_msg')}")
            if msg.get("topic") != f"orderbook.50.{self.pair(base)}":
                return None
            kind = msg["type"]
            if kind not in ("snapshot", "delta"):
                raise ValueError("unknown Bybit book message type")
            data = msg["data"]
            if data["s"] != self.pair(base):
                return None
            update_id = nonnegative_int(data["u"], "Bybit update ID")
            if update_id == 0:
                raise ValueError("Bybit update ID must be positive")
            cross_sequence = (
                nonnegative_int(data["seq"], "Bybit cross sequence")
                if "seq" in data
                else None
            )
            # A fresh snapshot or u=1 replaces all prior state, including IDs.
            replace = kind == "snapshot" or update_id == 1
            if not replace:
                if self._update_id is None:
                    raise ValueError("Bybit delta before snapshot")
                if update_id <= self._update_id:
                    raise ValueError("stale Bybit update ID")
                if (
                    cross_sequence is not None
                    and self._cross_sequence is not None
                    and cross_sequence <= self._cross_sequence
                ):
                    raise ValueError("stale Bybit cross sequence")
            self._book.apply(data["b"], data["a"], replace=replace)
            self._update_id = update_id
            self._cross_sequence = cross_sequence
            return self._book.snapshot(self.name, self.pair(base), sequence=update_id)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            self.reset_ws_state()
            raise ValueError(f"invalid Bybit book message: {exc}") from exc
