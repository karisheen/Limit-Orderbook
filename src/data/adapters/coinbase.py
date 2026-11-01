"""Public Advanced Trade level2 WebSocket with Exchange REST snapshot fallback.

The API families are deliberately separate: REST uses Exchange's aggregated
book rows; WebSocket subscribes to Advanced Trade level2 and receives l2_data.
No JWT or private endpoint is used.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from src.data.clients import HttpClient
from src.data.adapters.base import ExchangeAdapter, LiveBook
from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for
from src.workstation.precision import exact_json_loads, nonnegative_int


class CoinbaseAdapter(ExchangeAdapter):
    name = "Coinbase"
    supports_ws = True
    # level=2 returns the full aggregated book (a large payload), so poll
    # a little less aggressively than the default.
    rest_poll_interval = 3.0

    REST_BASE = "https://api.exchange.coinbase.com"
    WS_URL = "wss://advanced-trade-ws.coinbase.com"

    def __init__(self) -> None:
        self.reset_ws_state()

    def pair(self, base: str) -> str:
        return pair_for("coinbase", base)

    def fetch_snapshot(
        self, http: HttpClient, base: str, limit: int = 100
    ) -> OrderBookSnapshot:
        symbol = self.pair(base)
        data = http.get_json(f"{self.REST_BASE}/products/{symbol}/book", {"level": 2})
        return make_snapshot(
            self.name,
            symbol,
            data["bids"],
            data["asks"],
            sequence=data.get("sequence"),
        )

    def ws_url(self, base: str) -> str:
        return self.WS_URL

    def ws_subscribe_message(self, base: str) -> str:
        return json.dumps(
            {"type": "subscribe", "channel": "level2", "product_ids": [self.pair(base)]}
        )

    def ws_subscribe_messages(self, base: str) -> Tuple[str, ...]:
        heartbeat = json.dumps(
            {
                "type": "subscribe",
                "channel": "heartbeats",
                "product_ids": [self.pair(base)],
            }
        )
        return self.ws_subscribe_message(base), heartbeat

    def reset_ws_state(self) -> None:
        # Advanced Trade delivers the full book. Display truncation must never
        # discard levels whose subsequent deletions/replacements still matter.
        self._book = LiveBook(max_levels=None)
        self._sequence: Optional[int] = None

    def handle_ws_message(self, raw: str, base: str) -> Optional[OrderBookSnapshot]:
        try:
            msg = exact_json_loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("Coinbase message must be an object")
            if msg.get("type") == "error":
                raise ValueError(
                    f"Coinbase subscription rejected: {msg.get('message')}"
                )
            sequence = msg.get("sequence_num")
            if sequence is not None:
                sequence = nonnegative_int(sequence, "Coinbase sequence")
                if self._sequence is not None and sequence != self._sequence + 1:
                    raise ValueError("Coinbase sequence gap or out-of-order message")
            if msg.get("channel") != "l2_data":
                if sequence is not None:
                    self._sequence = sequence
                return None
            if sequence is None:
                raise ValueError("Coinbase l2_data missing sequence")
            events = msg["events"]
            if not isinstance(events, list) or not events:
                raise ValueError("empty Coinbase events")
            batches = []
            for event in events:
                if event["product_id"] != self.pair(base):
                    continue
                kind = event["type"]
                if kind not in ("snapshot", "update"):
                    raise ValueError("unknown Coinbase book event")
                bids, asks = [], []
                updates = event["updates"]
                if not isinstance(updates, list):
                    raise ValueError("invalid Coinbase updates")
                for update in updates:
                    side = update["side"]
                    if side not in ("bid", "offer"):
                        raise ValueError("unknown Coinbase book side")
                    (bids if side == "bid" else asks).append(
                        (update["price_level"], update["new_quantity"])
                    )
                batches.append((bids, asks, kind == "snapshot"))
            if not batches:
                self._sequence = sequence
                return None
            self._book.apply_batch(batches)
            self._sequence = sequence
            return self._book.snapshot(self.name, self.pair(base), sequence=sequence)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            self.reset_ws_state()
            raise ValueError(f"invalid Coinbase book message: {exc}") from exc
