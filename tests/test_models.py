"""Tests for the normalized order book model and symbol registry."""

from decimal import Decimal

import pytest

from src.domain.models import OrderBookSnapshot, make_snapshot
from src.domain.symbols import pair_for


class TestMakeSnapshot:
    def test_sorts_and_coerces_strings(self):
        snap = make_snapshot(
            "TestEx",
            "BTCUSD",
            bids=[["100.5", "2"], ["101.0", "1"], ["99.0", "3"]],
            asks=[["103.0", "1"], ["102.0", "2"], ["104.5", "0.5"]],
        )
        assert snap.bids[0] == (101.0, 1.0)  # highest bid first
        assert snap.asks[0] == (102.0, 2.0)  # lowest ask first
        assert snap.best_bid == 101.0
        assert snap.best_ask == 102.0

    def test_ignores_extra_row_fields(self):
        # Kraken REST rows carry timestamps; Coinbase carries num_orders.
        snap = make_snapshot(
            "TestEx",
            "BTCUSD",
            bids=[["100", "1", 1699999999]],
            asks=[["101", "2", 4]],
        )
        assert snap.bids == ((100.0, 1.0),)
        assert snap.asks == ((101.0, 2.0),)

    @pytest.mark.parametrize(
        "row",
        [
            ["bad", "1"],
            ["-5", "1"],
            ["99", "0"],
            [None, None],
            ["NaN", "1"],
            ["Infinity", "1"],
            ["99", "-1"],
            ["99", "Infinity"],
            ["1e100", "1"],
            ["99", "1e-100"],
            [99.1, "1"],
            [True, "1"],
            ["99"],
        ],
    )
    def test_rejects_malformed_and_nonpositive_rows(self, row):
        with pytest.raises(ValueError):
            make_snapshot("TestEx", "BTCUSD", [["100", "1"], row], [["101", "1"]])

    def test_empty_side_raises(self):
        with pytest.raises(ValueError):
            make_snapshot("TestEx", "BTCUSD", bids=[], asks=[["101", "1"]])
        with pytest.raises(ValueError):
            make_snapshot("TestEx", "BTCUSD", bids=[["x", "y"]], asks=[["101", "1"]])

    def test_derived_metrics(self):
        snap = make_snapshot(
            "TestEx",
            "BTCUSD",
            bids=[["99", "3"], ["98", "1"]],
            asks=[["101", "1"], ["102", "1"]],
        )
        assert snap.spread == Decimal("2")
        assert snap.mid_price == Decimal("100")
        assert snap.spread_bps == Decimal("200")
        assert abs(snap.imbalance() - Decimal(2) / 3) < Decimal("1e-27")

    def test_precision_survives_normalization(self):
        snap = make_snapshot(
            "TestEx",
            "BTCUSD",
            [["100.000000000000000001", "0.000000000000000001"]],
            [["100.000000000000000002", "2"]],
        )
        assert snap.best_bid == Decimal("100.000000000000000001")
        assert snap.spread == Decimal("0.000000000000000001")
        assert snap.bids[0][1] == Decimal("0.000000000000000001")

    @pytest.mark.parametrize(
        "bids,asks",
        [
            ([["100", "1"], ["100.0", "2"]], [["101", "1"]]),
            ([["100", "1"]], [["101", "1"], ["101.0", "2"]]),
            ([["101", "1"]], [["101", "1"]]),
            ([["102", "1"]], [["101", "1"]]),
        ],
    )
    def test_rejects_ambiguous_or_crossed_books(self, bids, asks):
        with pytest.raises(ValueError):
            make_snapshot("TestEx", "BTCUSD", bids, asks)

    def test_invalid_rows_outside_display_depth_are_rejected(self):
        with pytest.raises(ValueError):
            make_snapshot(
                "TestEx",
                "BTCUSD",
                [["100", "1"], ["99", "NaN"]],
                [["101", "1"]],
                max_levels=1,
            )

    @pytest.mark.parametrize(
        "bids,asks",
        [
            ((), ((Decimal("101"), Decimal("1")),)),
            (((Decimal("100"), Decimal("1")),), ()),
            (((100.0, 1.0),), ((Decimal("101"), Decimal("1")),)),
            (((Decimal("101"), Decimal("1")),), ((Decimal("101"), Decimal("1")),)),
            (
                ((Decimal("99"), Decimal("1")), (Decimal("100"), Decimal("1"))),
                ((Decimal("101"), Decimal("1")),),
            ),
        ],
    )
    def test_direct_snapshot_construction_cannot_bypass_validation(self, bids, asks):
        with pytest.raises(ValueError):
            OrderBookSnapshot("TestEx", "BTCUSD", bids, asks, 1000.0)

    @pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1, True])
    def test_invalid_receive_timestamp_rejected(self, timestamp):
        with pytest.raises(ValueError):
            make_snapshot(
                "TestEx", "BTCUSD", [["100", "1"]], [["101", "1"]], timestamp=timestamp
            )


class TestSymbolRegistry:
    def test_known_venues(self):
        assert pair_for("binanceus", "BTC") == "BTCUSDT"
        assert pair_for("coinbase", "BTC") == "BTC-USD"
        assert pair_for("kucoin", "SOL") == "SOL-USDT"
        assert pair_for("bybit", "ETH") == "ETHUSDT"

    def test_kraken_btc_rename(self):
        assert pair_for("kraken_rest", "BTC") == "XBTUSD"
        assert pair_for("kraken_rest", "ETH") == "ETHUSD"
        assert pair_for("kraken_ws", "BTC") == "BTC/USD"

    @pytest.mark.parametrize(
        "venue,base", [("unknown_venue", "BTC"), ("coinbase", "UNKNOWN")]
    )
    def test_unknown_mapping_fails_explicitly(self, venue, base):
        with pytest.raises(ValueError):
            pair_for(venue, base)
