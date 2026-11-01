"""Adapter contract tests using canned exchange payloads (no network)."""

import json
import zlib
from decimal import Decimal

import pytest

from src.data.adapters.base import LiveBook
from src.data.adapters.binance import BinanceUSAdapter
from src.data.adapters.bybit import BybitAdapter
from src.data.adapters.coinbase import CoinbaseAdapter
from src.data.adapters.kraken import KrakenAdapter
from src.data.adapters.kucoin import KucoinAdapter
from src.data.clients import FeedError, HttpClient


class FakeHttp(HttpClient):
    """Stands in for HttpClient: returns queued payloads without touching the network."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


def kraken_checksum(bids, asks):
    """Independent fixture CRC from complete post-message depth, not deltas."""
    fields = []
    for rows, reverse in ((asks, False), (bids, True)):
        for price, qty in sorted(
            rows, key=lambda row: Decimal(str(row[0])), reverse=reverse
        )[:10]:
            fields.extend(
                str(value).replace(".", "").lstrip("0") for value in (price, qty)
            )
    return zlib.crc32("".join(fields).encode("ascii")) & 0xFFFFFFFF


def kraken_frame(kind, bids, asks, *, final_bids=None, final_asks=None):
    return json.dumps(
        {
            "channel": "book",
            "type": kind,
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bids": [{"price": p, "qty": q} for p, q in bids],
                    "asks": [{"price": p, "qty": q} for p, q in asks],
                    "checksum": kraken_checksum(
                        bids if final_bids is None else final_bids,
                        asks if final_asks is None else final_asks,
                    ),
                }
            ],
        }
    )


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
        assert snap.best_bid == Decimal("64000.10")
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
        assert (
            BinanceUSAdapter().handle_ws_message(json.dumps({"result": None}), "BTC")
            is None
        )


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
        bids = [("64000.0", "1.0"), ("63990.0", "2.0")]
        asks = [("64010.0", "1.5")]
        snap = adapter.handle_ws_message(kraken_frame("snapshot", bids, asks), "BTC")
        assert snap is not None and snap.best_bid == 64000
        update = kraken_frame(
            "update",
            [("64000.0", "0.0")],
            [("64005.0", "0.7")],
            final_bids=[("63990.0", "2.0")],
            final_asks=[("64005.0", "0.7"), ("64010.0", "1.5")],
        )
        snap = adapter.handle_ws_message(update, "BTC")
        assert snap is not None
        assert snap.best_bid == 63990.0
        assert snap.best_ask == 64005.0

    def test_ws_ignores_heartbeat(self):
        adapter = KrakenAdapter()
        assert (
            adapter.handle_ws_message(json.dumps({"channel": "heartbeat"}), "BTC")
            is None
        )


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
                "data": {
                    "s": "BTCUSDT",
                    "b": [["64000", "1"], ["63995", "2"]],
                    "a": [["64010", "1"]],
                    "u": 1,
                },
            }
        )
        snap = adapter.handle_ws_message(snap_msg, "BTC")
        assert snap is not None and snap.best_bid == 64000.0

        delta_msg = json.dumps(
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "delta",
                "data": {
                    "s": "BTCUSDT",
                    "b": [["64000", "0"]],
                    "a": [["64008", "0.5"]],
                    "u": 2,
                },
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


class TestLiveBookIntegrity:
    def test_whole_delta_rolls_back_on_malformed_second_side(self):
        book = LiveBook()
        book.apply([["100", "1"]], [["101", "1"]], replace=True)
        with pytest.raises(ValueError):
            book.apply([["99", "2"]], [["102", "-1"]])
        assert book.bids == {Decimal("100"): Decimal("1")}
        assert book.asks == {Decimal("101"): Decimal("1")}

    def test_snapshot_duplicates_reject_but_delta_repeats_apply_in_order(self):
        book = LiveBook()
        with pytest.raises(ValueError):
            book.apply([["100", "1"], ["100.0", "2"]], [["101", "1"]], replace=True)
        assert not book.initialized
        book.apply([["100", "1"]], [["101", "1"]], replace=True)
        book.apply([["100", "2"], ["100", "0"], ["100", "3"]], [])
        assert book.bids[Decimal("100")] == Decimal("3")

    def test_delta_before_snapshot_rejected(self):
        with pytest.raises(ValueError):
            LiveBook().apply([["100", "1"]], [["101", "1"]])


class TestKrakenIntegrity:
    def test_official_checksum_fixture(self):
        # Kraken's published v2 checksum example, not a calculated expectation.
        bids = [
            ("45283.5", "0.10000000"),
            ("45283.4", "1.54582015"),
            ("45282.1", "0.10000000"),
            ("45281.0", "0.10000000"),
            ("45280.3", "1.54592586"),
            ("45279.0", "0.07990000"),
            ("45277.6", "0.03310103"),
            ("45277.5", "0.30000000"),
            ("45277.3", "1.54602737"),
            ("45276.6", "0.15445238"),
        ]
        asks = [
            ("45285.2", "0.00100000"),
            ("45286.4", "1.54571953"),
            ("45286.6", "1.54571109"),
            ("45289.6", "1.54560911"),
            ("45290.2", "0.15890660"),
            ("45291.8", "1.54553491"),
            ("45294.7", "0.04454749"),
            ("45296.1", "0.35380000"),
            ("45297.5", "0.09945542"),
            ("45299.5", "0.18772827"),
        ]
        assert kraken_checksum(bids, asks) == 3310070434
        adapter = KrakenAdapter()
        assert (
            adapter.handle_ws_message(kraken_frame("snapshot", bids, asks), "BTC")
            is not None
        )

    def test_unquoted_json_decimals_preserve_checksum_scale(self):
        checksum = kraken_checksum(
            [("100.000000000000000001", "1.00000000")],
            [("101.000000000000000001", "2.00000000")],
        )
        raw = (
            '{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
            '"bids":[{"price":100.000000000000000001,"qty":1.00000000}],'
            '"asks":[{"price":101.000000000000000001,"qty":2.00000000}],'
            f'"checksum":{checksum}' + "}]}"
        )
        snap = KrakenAdapter().handle_ws_message(raw, "BTC")
        assert snap.best_bid == Decimal("100.000000000000000001")
        assert snap.bids[0][1].as_tuple().exponent == -8

    @pytest.mark.parametrize(
        "failure", ["checksum", "missing_checksum", "bad_row", "duplicate", "crossed"]
    )
    def test_invalid_message_invalidates_until_fresh_snapshot(self, failure):
        adapter = KrakenAdapter()
        initial = kraken_frame("snapshot", [("100", "1")], [("101", "1")])
        adapter.handle_ws_message(initial, "BTC")
        msg = json.loads(
            kraken_frame(
                "update", [], [], final_bids=[("100", "1")], final_asks=[("101", "1")]
            )
        )
        payload = msg["data"][0]
        if failure == "checksum":
            payload["checksum"] ^= 1
        elif failure == "missing_checksum":
            del payload["checksum"]
        elif failure == "bad_row":
            payload["bids"] = [
                {"price": "99", "qty": "2"},
                {"price": "98", "qty": "NaN"},
            ]
        elif failure == "duplicate":
            msg["type"] = "snapshot"
            payload["bids"] = [{"price": "100", "qty": "1"}] * 2
            payload["asks"] = [{"price": "101", "qty": "1"}]
        else:
            payload["bids"] = [{"price": "102", "qty": "1"}]
        with pytest.raises(ValueError):
            adapter.handle_ws_message(json.dumps(msg), "BTC")
        assert adapter._book.snapshot("Kraken", "BTC/USD") is None
        with pytest.raises(ValueError):
            adapter.handle_ws_message(
                kraken_frame("update", [("100", "1")], [("101", "1")]), "BTC"
            )
        assert adapter.handle_ws_message(initial, "BTC") is not None

    def test_full_message_validated_after_all_payloads(self):
        adapter = KrakenAdapter()
        adapter.handle_ws_message(
            kraken_frame("snapshot", [("100", "1")], [("101", "1")]), "BTC"
        )
        checksum = kraken_checksum([("102", "2")], [("103", "1")])
        msg = {
            "channel": "book",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bids": [
                        {"price": "100", "qty": "0"},
                        {"price": "102", "qty": "1"},
                        {"price": "102", "qty": "2"},
                    ],
                    "checksum": checksum,
                },
                {
                    "symbol": "BTC/USD",
                    "asks": [
                        {"price": "101", "qty": "0"},
                        {"price": "103", "qty": "1"},
                    ],
                    "checksum": checksum,
                },
            ],
        }
        snap = adapter.handle_ws_message(json.dumps(msg), "BTC")
        assert snap.bids == ((Decimal("102"), Decimal("2")),)
        assert snap.best_ask == 103

    def test_wrong_symbol_does_not_initialize(self):
        adapter = KrakenAdapter()
        raw = kraken_frame("snapshot", [("100", "1")], [("101", "1")]).replace(
            "BTC/USD", "ETH/USD"
        )
        assert adapter.handle_ws_message(raw, "BTC") is None
        assert not adapter._book.initialized


def bybit_frame(kind="snapshot", update_id=10, bids=None, asks=None, **extra):
    data = {
        "s": "BTCUSDT",
        "b": [["100", "1"]] if bids is None else bids,
        "a": [["101", "1"]] if asks is None else asks,
        "u": update_id,
    }
    data.update(extra)
    return json.dumps({"topic": "orderbook.50.BTCUSDT", "type": kind, "data": data})


class TestBybitIntegrity:
    def test_noncontiguous_ids_and_restart_replace(self):
        adapter = BybitAdapter()
        adapter.handle_ws_message(bybit_frame(seq=100), "BTC")
        snap = adapter.handle_ws_message(
            bybit_frame("delta", 28, bids=[["99", "2"]], asks=[], seq=155), "BTC"
        )
        assert snap.sequence == 28 and len(snap.bids) == 2
        snap = adapter.handle_ws_message(
            bybit_frame("delta", 1, bids=[["98", "3"]], seq=1), "BTC"
        )
        assert snap.bids == ((Decimal("98"), Decimal("3")),)

    @pytest.mark.parametrize(
        "update_id,seq", [(10, 101), (9, 101), (11, 100), (11, 99)]
    )
    def test_nonmonotonic_delta_invalidates(self, update_id, seq):
        adapter = BybitAdapter()
        adapter.handle_ws_message(bybit_frame(seq=100), "BTC")
        with pytest.raises(ValueError):
            adapter.handle_ws_message(bybit_frame("delta", update_id, seq=seq), "BTC")
        assert not adapter._book.initialized

    def test_new_snapshot_resets_ids_and_replaces_depth(self):
        adapter = BybitAdapter()
        adapter.handle_ws_message(bybit_frame(bids=[["100", "1"], ["99", "1"]]), "BTC")
        snap = adapter.handle_ws_message(
            bybit_frame(update_id=2, bids=[["98", "1"]]), "BTC"
        )
        assert snap.sequence == 2 and len(snap.bids) == 1

    @pytest.mark.parametrize(
        "topic,symbol",
        [
            ("orderbook.1.BTCUSDT", "BTCUSDT"),
            ("orderbook.50.ETHUSDT", "ETHUSDT"),
            ("orderbook.50.BTCUSDT.extra", "BTCUSDT"),
            ("orderbook.50.BTCUSDT", "ETHUSDT"),
        ],
    )
    def test_requires_exact_topic_and_symbol(self, topic, symbol):
        msg = json.loads(bybit_frame())
        msg["topic"], msg["data"]["s"] = topic, symbol
        adapter = BybitAdapter()
        assert adapter.handle_ws_message(json.dumps(msg), "BTC") is None
        assert not adapter._book.initialized

    def test_delta_before_snapshot_rejected(self):
        with pytest.raises(ValueError):
            BybitAdapter().handle_ws_message(bybit_frame("delta", 2), "BTC")


def coinbase_frame(sequence=0, kind="snapshot", updates=None):
    return json.dumps(
        {
            "channel": "l2_data",
            "sequence_num": sequence,
            "events": [
                {
                    "type": kind,
                    "product_id": "BTC-USD",
                    "updates": updates
                    if updates is not None
                    else [
                        {"side": "bid", "price_level": "100", "new_quantity": "1"},
                        {"side": "offer", "price_level": "101", "new_quantity": "2"},
                    ],
                }
            ],
        }
    )


class TestCoinbaseAdvancedTrade:
    def test_public_subscription_and_absolute_quantity(self):
        adapter = CoinbaseAdapter()
        requests = [
            json.loads(message) for message in adapter.ws_subscribe_messages("BTC")
        ]
        assert {request["channel"] for request in requests} == {"level2", "heartbeats"}
        assert all("jwt" not in request for request in requests)
        adapter.handle_ws_message(coinbase_frame(), "BTC")
        snap = adapter.handle_ws_message(
            coinbase_frame(
                1,
                "update",
                [
                    {"side": "bid", "price_level": "100", "new_quantity": "3"},
                ],
            ),
            "BTC",
        )
        assert snap.bids[0][1] == Decimal("3")

    def test_heartbeat_advances_sequence_without_publishing_book(self):
        adapter = CoinbaseAdapter()
        adapter.handle_ws_message(coinbase_frame(), "BTC")
        assert (
            adapter.handle_ws_message(
                json.dumps(
                    {
                        "channel": "heartbeats",
                        "sequence_num": 1,
                        "events": [],
                    }
                ),
                "BTC",
            )
            is None
        )
        assert (
            adapter.handle_ws_message(coinbase_frame(2, "update"), "BTC").sequence == 2
        )

    @pytest.mark.parametrize("sequence", [0, 2])
    def test_sequence_gap_or_duplicate_requires_snapshot(self, sequence):
        adapter = CoinbaseAdapter()
        adapter.handle_ws_message(coinbase_frame(), "BTC")
        with pytest.raises(ValueError):
            adapter.handle_ws_message(coinbase_frame(sequence, "update"), "BTC")
        assert not adapter._book.initialized
        with pytest.raises(ValueError):
            adapter.handle_ws_message(coinbase_frame(0, "update"), "BTC")

    def test_full_message_transaction_rejects_late_malformed_event(self):
        adapter = CoinbaseAdapter()
        adapter.handle_ws_message(coinbase_frame(), "BTC")
        msg = json.loads(coinbase_frame(1, "update"))
        msg["events"].append(
            {
                "type": "update",
                "product_id": "BTC-USD",
                "updates": [
                    {"side": "unknown", "price_level": "99", "new_quantity": "1"},
                ],
            }
        )
        with pytest.raises(ValueError):
            adapter.handle_ws_message(json.dumps(msg), "BTC")
        assert not adapter._book.initialized

    def test_full_reconstruction_survives_display_truncation(self):
        adapter = CoinbaseAdapter()
        updates = [
            {"side": "bid", "price_level": str(300 - index), "new_quantity": "1"}
            for index in range(201)
        ]
        updates.append({"side": "offer", "price_level": "301", "new_quantity": "1"})
        snap = adapter.handle_ws_message(coinbase_frame(updates=updates), "BTC")
        assert len(snap.bids) == 200 and len(adapter._book.bids) == 201
        snap = adapter.handle_ws_message(
            coinbase_frame(
                1,
                "update",
                [
                    {"side": "bid", "price_level": "300", "new_quantity": "0"},
                ],
            ),
            "BTC",
        )
        assert len(snap.bids) == 200 and snap.bids[-1][0] == 100

    def test_wrong_product_does_not_initialize(self):
        adapter = CoinbaseAdapter()
        assert (
            adapter.handle_ws_message(
                coinbase_frame().replace("BTC-USD", "ETH-USD"), "BTC"
            )
            is None
        )
        assert not adapter._book.initialized

    def test_multiple_events_validate_only_final_book(self):
        adapter = CoinbaseAdapter()
        adapter.handle_ws_message(coinbase_frame(), "BTC")
        msg = json.loads(
            coinbase_frame(
                1,
                "update",
                [
                    {"side": "bid", "price_level": "100", "new_quantity": "0"},
                    {"side": "bid", "price_level": "102", "new_quantity": "1"},
                ],
            )
        )
        msg["events"].append(
            {
                "product_id": "BTC-USD",
                "type": "update",
                "updates": [
                    {"side": "offer", "price_level": "101", "new_quantity": "0"},
                    {"side": "offer", "price_level": "103", "new_quantity": "1"},
                ],
            }
        )
        snap = adapter.handle_ws_message(json.dumps(msg), "BTC")
        assert snap.best_bid == 102 and snap.best_ask == 103


@pytest.mark.parametrize(
    "adapter_cls,initial",
    [
        (KrakenAdapter, kraken_frame("snapshot", [("100", "1")], [("101", "1")])),
        (BybitAdapter, bybit_frame()),
        (CoinbaseAdapter, coinbase_frame()),
    ],
)
@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"channel":"book","value":NaN}',
        '{"channel":"book","channel":"heartbeat"}',
    ],
)
def test_malformed_json_invalidates_initialized_adapter(adapter_cls, initial, raw):
    adapter = adapter_cls()
    adapter.handle_ws_message(initial, "BTC")
    with pytest.raises(ValueError):
        adapter.handle_ws_message(raw, "BTC")
    assert not adapter._book.initialized


def test_binance_partial_snapshots_replace_instead_of_accumulating():
    adapter = BinanceUSAdapter()
    first = json.dumps(
        {"lastUpdateId": 1, "bids": [["100", "1"], ["99", "1"]], "asks": [["101", "1"]]}
    )
    second = json.dumps(
        {"lastUpdateId": 2, "bids": [["98", "2"]], "asks": [["102", "1"]]}
    )
    adapter.handle_ws_message(first, "BTC")
    snap = adapter.handle_ws_message(second, "BTC")
    assert snap.bids == ((Decimal("98"), Decimal("2")),)


def test_kucoin_malformed_sequence_is_not_invented_zero():
    http = FakeHttp(
        {
            "code": "200000",
            "data": {
                "sequence": "bad",
                "bids": [["100", "1"]],
                "asks": [["101", "1"]],
            },
        }
    )
    with pytest.raises(ValueError):
        KucoinAdapter().fetch_snapshot(http, "BTC")
