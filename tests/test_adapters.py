"""Adapter contract tests using canned exchange payloads (no network)."""
import json

import pytest

from src.data.adapters.binance import BinanceUSAdapter
from src.data.adapters.bybit import BybitAdapter
from src.data.adapters.coinbase import CoinbaseAdapter
from src.data.adapters.kraken import KrakenAdapter
from src.data.adapters.kucoin import KucoinAdapter
from src.data.clients import FeedError


class FakeHttp:
    """Stands in for HttpClient: returns queued payloads."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


class TestBinanceUS:
    def test_rest_snapshot(self):
        http = FakeHttp(
            {
                "lastUpdateId": 123,
                "bids": [["64000.10", "0.5"], ["63999.00", "1.2"]],
                "asks": [["64001.00", "0.4"], ["64002.50", "2.0"]],
            }
        )
        snap = BinanceUSAdapter().fetch_snapshot(http, "BTC")
        assert snap.exchange == "Binance.US"
        assert snap.symbol == "BTCUSDT"
        assert snap.best_bid == 64000.10
        assert snap.best_ask == 64001.00
        assert snap.sequence == 123
        url, params = http.calls[0]
        assert params["symbol"] == "BTCUSDT"

    def test_ws_partial_depth_message(self):
        adapter = BinanceUSAdapter()
        raw = json.dumps(
            {
                "lastUpdateId": 456,
                "bids": [["64000", "1"]],
                "asks": [["64010", "2"]],
            }
        )
        snap = adapter.handle_ws_message(raw, "BTC")
        assert snap is not None
        assert snap.best_bid == 64000.0
        assert snap.sequence == 456

    def test_ws_ignores_non_book_messages(self):
        assert BinanceUSAdapter().handle_ws_message(json.dumps({"result": None}), "BTC") is None


class TestKraken:
    def test_rest_snapshot_uses_first_result_key(self):
        http = FakeHttp(
            {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "bids": [["64000.0", "0.5", 1700000000]],
                        "asks": [["64010.0", "0.3", 1700000001]],
                    }
                },
            }
        )
        snap = KrakenAdapter().fetch_snapshot(http, "BTC")
        assert snap.symbol == "XBTUSD"
        assert snap.best_bid == 64000.0

    def test_rest_error_raises(self):
        http = FakeHttp({"error": ["EQuery:Unknown asset pair"]})
        with pytest.raises(FeedError):
            KrakenAdapter().fetch_snapshot(http, "BTC")

    def test_ws_snapshot_then_update(self):
        adapter = KrakenAdapter()
        adapter.reset_ws_state()
        snapshot_msg = json.dumps(
            {
                "channel": "book",
                "type": "snapshot",
                "data": [
                    {
                        "symbol": "BTC/USD",
                        "bids": [{"price": 64000.0, "qty": 1.0}, {"price": 63990.0, "qty": 2.0}],
                        "asks": [{"price": 64010.0, "qty": 1.5}],
                    }
                ],
            }
        )
        snap = adapter.handle_ws_message(snapshot_msg, "BTC")
        assert snap is not None and snap.best_bid == 64000.0

        # Update: remove the 64000 bid (qty 0) and improve the ask.
        update_msg = json.dumps(
            {
                "channel": "book",
                "type": "update",
                "data": [
                    {
                        "symbol": "BTC/USD",
                        "bids": [{"price": 64000.0, "qty": 0.0}],
                        "asks": [{"price": 64005.0, "qty": 0.7}],
                    }
                ],
            }
        )
        snap = adapter.handle_ws_message(update_msg, "BTC")
        assert snap is not None
        assert snap.best_bid == 63990.0
        assert snap.best_ask == 64005.0

    def test_ws_ignores_heartbeat(self):
        adapter = KrakenAdapter()
        assert adapter.handle_ws_message(json.dumps({"channel": "heartbeat"}), "BTC") is None


class TestCoinbase:
    def test_rest_snapshot_with_num_orders_column(self):
        http = FakeHttp(
            {
                "sequence": 789,
                "bids": [["64000.00", "0.8", 3]],
                "asks": [["64005.00", "0.6", 2]],
            }
        )
        snap = CoinbaseAdapter().fetch_snapshot(http, "BTC")
        assert snap.symbol == "BTC-USD"
        assert snap.best_bid == 64000.0
        assert snap.sequence == 789
        url, params = http.calls[0]
        assert "BTC-USD" in url


class TestKucoin:
    def test_rest_snapshot(self):
        http = FakeHttp(
            {
                "code": "200000",
                "data": {
                    "sequence": "161573503",
                    "bids": [["64000.0", "0.5"]],
                    "asks": [["64008.0", "0.2"]],
                },
            }
        )
        snap = KucoinAdapter().fetch_snapshot(http, "BTC")
        assert snap.symbol == "BTC-USDT"
        assert snap.best_ask == 64008.0
        assert snap.sequence == 161573503

    def test_error_code_raises(self):
        http = FakeHttp({"code": "400100", "msg": "symbol not found"})
        with pytest.raises(FeedError):
            KucoinAdapter().fetch_snapshot(http, "BTC")


class TestBybit:
    def test_rest_snapshot(self):
        http = FakeHttp(
            {
                "retCode": 0,
                "result": {
                    "b": [["64000.0", "1.0"]],
                    "a": [["64007.0", "0.9"]],
                    "u": 999,
                },
            }
        )
        snap = BybitAdapter().fetch_snapshot(http, "BTC")
        assert snap.symbol == "BTCUSDT"
        assert snap.best_bid == 64000.0
        assert snap.sequence == 999

    def test_rest_error_raises(self):
        http = FakeHttp({"retCode": 10001, "retMsg": "params error"})
        with pytest.raises(FeedError):
            BybitAdapter().fetch_snapshot(http, "BTC")

    def test_ws_snapshot_then_delta(self):
        adapter = BybitAdapter()
        adapter.reset_ws_state()
        snap_msg = json.dumps(
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "snapshot",
                "data": {"b": [["64000", "1"], ["63995", "2"]], "a": [["64010", "1"]], "u": 1},
            }
        )
        snap = adapter.handle_ws_message(snap_msg, "BTC")
        assert snap is not None and snap.best_bid == 64000.0

        delta_msg = json.dumps(
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "delta",
                "data": {"b": [["64000", "0"]], "a": [["64008", "0.5"]], "u": 2},
            }
        )
        snap = adapter.handle_ws_message(delta_msg, "BTC")
        assert snap is not None
        assert snap.best_bid == 63995.0
        assert snap.best_ask == 64008.0
        assert snap.sequence == 2

    def test_ws_ignores_subscribe_ack(self):
        adapter = BybitAdapter()
        ack = json.dumps({"success": True, "op": "subscribe"})
        assert adapter.handle_ws_message(ack, "BTC") is None
