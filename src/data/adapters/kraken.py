"""Kraken adapter: REST depth + v2 websocket book channel (snapshot + deltas)."""

from __future__ import annotations

import json
import zlib
from typing import Optional

from src.data.clients import FeedError, HttpClient
from src.data.adapters.base import ExchangeAdapter, LiveBook
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for
from src.workstation.precision import exact_json_loads, nonnegative_int


class KrakenAdapter(ExchangeAdapter):
    name = "Kraken"
    supports_ws = True

    REST_URL = "https://api.kraken.com/0/public/Depth"
    WS_URL = "wss://ws.kraken.com/v2"

    DEPTH = 25

    def __init__(self) -> None:
        self.reset_ws_state()

    def rest_pair(self, base: str) -> str:
        return pair_for("kraken_rest", base)

    def ws_pair(self, base: str) -> str:
        return pair_for("kraken_ws", base)

    def fetch_snapshot(
        self, http: HttpClient, base: str, limit: int = 100
    ) -> OrderBookSnapshot:
        data = http.get_json(
            self.REST_URL, {"pair": self.rest_pair(base), "count": limit}
        )
        errors = data.get("error") or []
        if errors:
            raise FeedError(f"Kraken API error: {errors}")
        result = data.get("result") or {}
        if not result:
            raise FeedError("Kraken API returned empty result")
        # Kraken keys results by its internal pair name (e.g. XXBTZUSD).
        book = next(iter(result.values()))
        return make_snapshot(
            self.name, self.rest_pair(base), book["bids"], book["asks"]
        )

    def ws_url(self, base: str) -> str:
        return self.WS_URL

    def ws_subscribe_message(self, base: str) -> Optional[str]:
        return json.dumps(
            {
                "method": "subscribe",
                "params": {
                    "channel": "book",
                    "symbol": [self.ws_pair(base)],
                    "depth": self.DEPTH,
                },
            }
        )

    def reset_ws_state(self) -> None:
        self._book = LiveBook(self.DEPTH)

    @staticmethod
    def _checksum(book: LiveBook) -> int:
        parts = []
        for side, reverse in ((book.asks, False), (book.bids, True)):
            for price in sorted(side, reverse=reverse)[:10]:
                for value in (price, side[price]):
                    parts.append(format(value, "f").replace(".", "").lstrip("0"))
        return zlib.crc32("".join(parts).encode("ascii")) & 0xFFFFFFFF

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        try:
            msg = exact_json_loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("Kraken message must be an object")
            if msg.get("success") is False:
                raise ValueError(f"Kraken subscription rejected: {msg.get('error')}")
            if msg.get("channel") != "book":
                return None
            kind = msg.get("type")
            if kind not in ("snapshot", "update"):
                raise ValueError("unknown Kraken book message type")
            payloads = msg["data"]
            if not isinstance(payloads, list) or not payloads:
                raise ValueError("empty Kraken book message")
            if all(payload["symbol"] != self.ws_pair(base) for payload in payloads):
                return None
            bids, asks = [], []
            checksum = None
            for payload in payloads:
                if payload["symbol"] != self.ws_pair(base):
                    raise ValueError("mixed Kraken book symbols")
                if kind == "snapshot" and (
                    "bids" not in payload or "asks" not in payload
                ):
                    raise ValueError("incomplete Kraken snapshot")
                bids.extend(
                    (level["price"], level["qty"]) for level in payload.get("bids", [])
                )
                asks.extend(
                    (level["price"], level["qty"]) for level in payload.get("asks", [])
                )
                checksum = nonnegative_int(payload["checksum"], "checksum")
                if checksum > 0xFFFFFFFF:
                    raise ValueError("invalid Kraken CRC32")
            self._book.apply(bids, asks, replace=kind == "snapshot")
            if self._checksum(self._book) != checksum:
                raise ValueError("Kraken checksum mismatch; resynchronization required")
            return self._book.snapshot(self.name, self.ws_pair(base))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            self.reset_ws_state()
            raise ValueError(f"invalid Kraken book message: {exc}") from exc
