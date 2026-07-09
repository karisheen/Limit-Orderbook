"""Tests for the normalized order book model and symbol registry."""
import pytest

from src.domain.models import make_snapshot
from src.domain.symbols import CANONICAL_ASSETS, pair_for


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

    def test_drops_malformed_and_nonpositive_rows(self):
        snap = make_snapshot(
            "TestEx",
            "BTCUSD",
            bids=[["100", "1"], ["bad", "1"], ["-5", "1"], ["99", "0"]],
            asks=[["101", "1"], [None, None]],
        )
        assert snap.bids == ((100.0, 1.0),)
        assert snap.asks == ((101.0, 1.0),)

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
        assert snap.spread == pytest.approx(2.0)
        assert snap.mid_price == pytest.approx(100.0)
        assert snap.spread_bps == pytest.approx(200.0)
        # bid volume 4, ask volume 2 -> imbalance 4/6
        assert snap.imbalance() == pytest.approx(4 / 6)


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

    def test_unknown_venue_falls_back_to_usd_pair(self):
        assert pair_for("unknown_venue", "BTC") == "BTCUSD"

    def test_canonical_assets_nonempty(self):
        assert "BTC" in CANONICAL_ASSETS and len(CANONICAL_ASSETS) >= 5
