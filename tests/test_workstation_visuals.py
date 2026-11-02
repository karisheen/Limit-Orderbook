"""Consumer-visible visual evidence, exact economics, and real replay regressions."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D, localcontext
from types import SimpleNamespace

import pytest

from src.workstation import visuals
from src.workstation.analytics import AnalyticsConfig, AnalyticsEngine
from src.workstation.book import Book, Level
from src.workstation.demo import create_demo
from src.workstation.events import (
    BookUpdate,
    Control,
    Gap,
    LevelChange,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import (
    AssetIdentity,
    OrderConstraints,
    synthetic_instrument,
)
from src.workstation.precision import DECIMAL_CONTEXT
from src.workstation.quality import Capabilities, ClockReading, Coverage, Quality
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock, ReplaySession
from src.workstation.visuals import VisualHistory, grouped_ladder, proposal_risk

SECOND = 1_000_000_000


def view_request(**overrides):
    values = dict(
        start_ns="0",
        end_ns=None,
        price_step="1",
        price_offset_rows=0,
        recenter=False,
        bucket_ns=str(SECOND),
        rows=32,
        max_columns=180,
        after_column=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def risk_request(**overrides):
    values = dict(
        side="buy",
        entry_price="100",
        stop_price="90",
        base_quantity="2",
        risk_budget=None,
        fee_bps="10",
        fee_provenance="synthetic user assumption",
        fee_date="2020-01-01",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class VisualFeed:
    """Complete deliveries and immutable books, with a genuine shared observer."""

    def __init__(self, instrument=None, *, trade_channels=("trades",)):
        self.instrument = instrument or synthetic_instrument()
        self.keys = {
            channel: SubscriptionKey.for_instrument(self.instrument, channel=channel)
            for channel in ("book", *trade_channels)
        }
        self.streams = tuple(
            StreamSpec(channel, self.instrument, key, "test-v1", "test-config")
            for channel, key in self.keys.items()
        )
        self.history = VisualHistory(
            AnalyticsEngine(
                self.streams,
                AnalyticsConfig(window_ns=SECOND, retention_ns=60 * SECOND),
            ),
            self.keys["book"],
        )
        self.clock = ReplayClock()
        self.book = None
        self.ordinal = 0
        self.deliveries = []

    def event(self, payload, stamp, channel="book", **overrides):
        self.ordinal += 1
        return synthetic_envelope(
            self.instrument,
            payload,
            ordinal=self.ordinal,
            mono_ns=stamp,
            key=self.keys[channel],
            stream_id=channel,
            source_mode="ws_delta",
            **overrides,
        )

    def deliver(self, event):
        self.clock.advance(event)
        batch = RecordedBatch(self.ordinal, (event,))
        books = (self.book,) if self.book is not None else ()
        self.deliveries.append((batch, books, self.clock.reading))
        self.history.on_batch(batch, books, self.clock)
        return event

    def quote(
        self,
        stamp,
        bids=(("99", "1"), ("97", "3")),
        asks=(("101", "2"), ("103", "4")),
        *,
        full=True,
    ):
        changes = tuple(
            LevelChange(side, D(price), D(quantity))
            for side, levels in (("bid", bids), ("ask", asks))
            for price, quantity in levels
        )
        event = self.event(
            BookUpdate("snapshot" if self.book is None else "delta", changes), stamp
        )
        bid_levels = tuple(Level(D(p), D(q)) for p, q in bids)
        ask_levels = tuple(Level(D(p), D(q)) for p, q in asks)
        self.book = Book(
            self.instrument.instrument_id,
            self.instrument.metadata_version,
            self.keys["book"],
            event.segment_id,
            event.connection_epoch,
            self.ordinal,
            event.ref(),
            bid_levels,
            ask_levels,
            Quality(
                lifecycle="live",
                integrity="valid",
                transport_alive=True,
                source_mode=event.source_mode,
                last_book_observation_mono_ns=stamp,
                last_healthy_observation_mono_ns=stamp,
                clock_id=event.clock_id,
                coverage=Coverage(
                    retained_bid_levels=len(bids),
                    retained_ask_levels=len(asks),
                    worst_bid=bid_levels[-1].price,
                    worst_ask=ask_levels[-1].price,
                    full_depth=full,
                ),
                capabilities=Capabilities(deltas=True, exact_event_flow=True),
                continuous_since_ordinal=1,
            ),
        )
        return self.deliver(event)

    def trade(
        self,
        stamp,
        identity,
        *,
        side="buy",
        quantity="1",
        price="100",
        history=False,
        channel="trades",
    ):
        return self.deliver(
            self.event(
                Trade(
                    identity,
                    identity,
                    D(price),
                    D(quantity),
                    quantity_unit=self.instrument.quantity_unit,
                    taker_side=side,
                    is_subscription_history=history,
                ),
                stamp,
                channel,
            )
        )

    def heartbeat(self, stamp):
        return self.deliver(self.event(Control("heartbeat"), stamp))

    def query(self, **overrides):
        return self.history.query(view_request(**overrides), "7")


def column_at(view, stamp):
    return next(column for column in view["columns"] if column["time_ns"] == str(stamp))


def cell_at(view, column, price):
    return column["cells"][view["prices"].index(price)]


@pytest.mark.parametrize(
    "base,mid", [("BTC", "60000"), ("ETH", "3000"), ("SOL", "150")]
)
def test_initial_auto_fit_shows_sparse_depth_and_exact_trade_volume(base, mid):
    midpoint = D(mid)
    feed = VisualFeed(synthetic_instrument(base=base))
    bids = tuple(
        (str(midpoint - distance), quantity)
        for distance, quantity in ((1, "1.25"), (2, "2.75"))
    )
    asks = tuple(
        (str(midpoint + distance), quantity)
        for distance, quantity in ((1, "3.125"), (2, "4.875"))
    )
    feed.quote(0, bids=bids, asks=asks)
    native = feed.book
    feed.trade(SECOND // 5, "low", price=str(midpoint - D("3.25")), quantity="0.125")
    feed.trade(SECOND // 2, "high", price=str(midpoint + D("3.75")), quantity="0.375")
    feed.heartbeat(SECOND + 1)
    view = feed.query(price_step=None, rows=128, start_ns=None)
    low, step = D(view["price_min"]), D(view["price_step"])
    high = low + view["rows"] * step
    assert step > feed.instrument.tick_size
    assert low <= midpoint - D("3.25") < midpoint + D("3.75") < high
    column = column_at(view, SECOND)
    assert column["eligible"]
    assert sum(D(pair[0]) for pair in column["cells"]) == D("4")
    assert sum(D(pair[1]) for pair in column["cells"]) == D("8")
    assert sum(pair[0] != "0" for pair in column["cells"]) == 2
    assert sum(pair[1] != "0" for pair in column["cells"]) == 2
    candle = column_at(view, 0)["candle"]
    assert D(candle["low"]) >= low
    assert D(candle["high"]) < high
    assert candle["volume"] == "0.5"
    assert sum(D(row["buy_base"]) for row in view["profile"]) == D("0.5")
    assert feed.book is native
    assert len(native.bids) == len(native.asks) == 2


@pytest.mark.parametrize(
    "base,mid", [("BTC", "60000"), ("ETH", "3000"), ("SOL", "150")]
)
def test_initial_auto_fit_fine_tick_depth_conserves_native_quantities(base, mid):
    midpoint = D(mid)
    feed = VisualFeed(synthetic_instrument(base=base, tick_size="0.0001"))
    bids = tuple((str(midpoint - D(index) / 100), "0.125") for index in range(1, 33))
    asks = tuple((str(midpoint + D(index) / 100), "0.375") for index in range(1, 33))
    feed.quote(0, bids=bids, asks=asks)
    native = feed.book
    feed.heartbeat(SECOND + 1)
    view = feed.query(price_step=None, rows=128)
    column = column_at(view, SECOND)
    assert D(view["price_step"]) > feed.instrument.tick_size
    assert sum(D(pair[0]) for pair in column["cells"]) == D("4")
    assert sum(D(pair[1]) for pair in column["cells"]) == D("12")
    assert sum(pair[0] != "0" for pair in column["cells"]) >= 16
    assert sum(pair[1] != "0" for pair in column["cells"]) >= 16
    assert feed.book is native
    assert tuple(level.price for level in native.bids) == tuple(
        D(price) for price, _ in bids
    )


def test_auto_fit_waits_for_valid_bootstrap_and_freezes_until_recenter():
    feed = VisualFeed()
    empty = feed.query(price_step=None)
    assert empty["price_min"] == "0"
    assert feed.history.price_step is None
    feed.heartbeat(0)
    feed.heartbeat(SECOND + 1)
    unavailable = feed.query(price_step=None)
    assert all(pair == [None, None] for pair in column_at(unavailable, SECOND)["cells"])
    assert feed.history.price_step is None
    feed.quote(2 * SECOND, bids=(("59999", "2"),), asks=(("60001", "3"),))
    feed.heartbeat(3 * SECOND + 1)
    initial = feed.query(price_step=None)
    assert D(initial["price_min"]) <= 59999
    assert D(initial["prices"][-1]) >= 60001
    assert sum(D(pair[0]) for pair in column_at(initial, 3 * SECOND)["cells"]) == 2
    assert all(pair == [None, None] for pair in column_at(initial, SECOND)["cells"])
    feed.quote(4 * SECOND, bids=(("59900", "2"),), asks=(("60100", "3"),))
    stable = feed.query(price_step=None)
    assert stable["prices"] == initial["prices"]
    assert stable["grid_id"] == initial["grid_id"]
    panned = feed.query(price_step=None, price_offset_rows=10)
    assert D(panned["price_min"]) == D(initial["price_min"]) + 10 * D(
        initial["price_step"]
    )
    recentered = feed.query(price_step=None, recenter=True)
    assert D(recentered["price_min"]) <= 59900
    assert D(recentered["prices"][-1]) >= 60100
    assert D(recentered["price_step"]) > D(initial["price_step"])
    assert recentered["price_step"] in recentered["step_options"]
    explicit = feed.query(price_step="0.01", recenter=True)
    assert explicit["price_step"] == "0.01"
    assert D(explicit["prices"][-1]) - D(explicit["price_min"]) == D("0.31")


def test_auto_fit_large_quantum_is_selectable_and_ladder_conserves_native_depth():
    feed = VisualFeed()
    feed.quote(
        0,
        bids=(("59000", "1.25"), ("58000", "2.75")),
        asks=(("61000", "3"), ("62000", "5")),
    )
    feed.heartbeat(SECOND + 1)
    automatic = feed.query(price_step=None)
    assert D(automatic["price_step"]) > 10
    explicit = feed.query(price_step=automatic["price_step"])
    assert explicit["prices"] == automatic["prices"]
    ladder = grouped_ladder(
        feed.book,
        feed.instrument,
        feed.clock.reading,
        SimpleNamespace(price_step=automatic["price_step"], rows=20),
        "7",
    )
    assert ladder["eligible"]
    assert (
        sum(D(row["base_quantity"]) for row in ladder["rows"] if row["side"] == "bid")
        == 4
    )
    assert (
        sum(D(row["base_quantity"]) for row in ladder["rows"] if row["side"] == "ask")
        == 8
    )


def test_default_time_window_expands_from_real_subscription_not_clock_zero():
    feed = VisualFeed()
    origin = 900_000 * SECOND + SECOND // 4
    feed.quote(origin)
    feed.trade(origin + SECOND // 4, "observed", quantity="0.5")
    feed.heartbeat(origin + 2 * SECOND)
    initial = feed.query(price_step=None, start_ns=None)
    assert initial["start_ns"] == str(origin // SECOND * SECOND)
    assert int(initial["end_ns"]) - int(initial["start_ns"]) < 3 * SECOND
    assert initial["retained_start_ns"] == str((origin // SECOND + 1) * SECOND)
    assert "initial_partial_history" in initial["reasons"]
    assert "requested_history_unavailable" in initial["reasons"]
    assert [column["time_ns"] for column in initial["columns"]] == [
        str((origin // SECOND + 1) * SECOND),
        str((origin // SECOND + 2) * SECOND),
    ]
    explicit_start = origin - 100 * SECOND
    requested = feed.query(price_step=None, start_ns=str(explicit_start))
    assert requested["start_ns"] == str(explicit_start)
    assert "requested_history_unavailable" in requested["reasons"]
    feed.quote(origin + 200 * SECOND)
    expanded = feed.query(price_step=None, start_ns=None)
    assert int(expanded["end_ns"]) - int(expanded["start_ns"]) == 180 * SECOND


def test_default_window_follows_retained_depth_without_hiding_truncation(monkeypatch):
    monkeypatch.setattr(visuals, "MAX_COLUMNS", 4)
    feed = VisualFeed()
    origin = 1000 * SECOND
    for index in range(9):
        feed.quote(origin + index * SECOND)
    feed.heartbeat(origin + 8 * SECOND + 1)
    view = feed.query(start_ns=None, price_step=None)
    retained = int(view["retained_start_ns"])
    assert view["start_ns"] == str(retained)
    assert retained > origin
    assert view["truncated"]
    assert "history_retention_truncated" in view["reasons"]
    assert len(view["columns"]) == 4
    assert all(column["eligible"] for column in view["columns"])
    assert all(
        sum(D(pair[0]) for pair in column["cells"]) == 4 for column in view["columns"]
    )
    requested_start = retained - SECOND // 2
    explicit = feed.query(start_ns=str(requested_start), price_step=None)
    assert explicit["start_ns"] == str(requested_start)
    assert "requested_history_unavailable" in explicit["reasons"]


def test_composite_observer_closes_metrics_before_later_delivery_and_keeps_old_cells():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "anchor")
    feed.trade(SECOND // 2, "observed", quantity="2")
    quote = feed.quote(3 * SECOND // 4, bids=(("99", "4"), ("97", "3")))
    feed.trade(2 * SECOND, "later-boundary", quantity="1000")
    before = feed.query()
    old = column_at(before, SECOND)
    assert cell_at(before, old, "99") == ["4", "0"]
    assert old["asof"]["ingest_ordinal"] == str(quote.ingest_ordinal)
    assert old["cvd"]["value"] == "2"
    assert old["ofi"]["value"] == "3"
    assert old["ofi"]["formula_version"] == "cont-best-quote-indicators-v1"
    assert old["candle"] is None
    for metric in (old["cvd"], old["ofi"]):
        assert all(
            int(ref["receive_mono_ns"]) <= SECOND for ref in metric["references"]
        )

    feed.quote(
        3 * SECOND,
        bids=(("999999", "999999999"),),
        asks=(("1000001", "888888888"),),
    )
    feed.trade(3 * SECOND, "extreme", price="1000000", quantity="999999")
    feed.heartbeat(4 * SECOND + 1)
    after = feed.query()
    assert after["grid_id"] == before["grid_id"]
    assert after["price_min"] == before["price_min"]
    assert after["prices"] == before["prices"]
    assert column_at(after, SECOND) == old
    assert cell_at(after, column_at(after, 3 * SECOND), "99") == ["0", "0"]
    assert column_at(after, 3 * SECOND)["best_bid"] == "999999"
    assert column_at(after, 3 * SECOND)["candle"]["volume"] == "999999"


def test_missing_and_stale_depth_are_null_not_observed_zero():
    missing = VisualFeed()
    missing.heartbeat(0)
    missing.heartbeat(SECOND + 1)
    absent = column_at(missing.query(), SECOND)
    assert not absent["eligible"]
    assert "no_validated_book" in absent["reasons"]
    assert absent["asof"] is None
    assert all(pair == [None, None] for pair in absent["cells"])

    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(7 * SECOND + 1)
    view = feed.query()
    fresh = column_at(view, 5 * SECOND)
    assert fresh["eligible"]
    assert cell_at(view, fresh, "98") == ["0", "0"]
    stale = column_at(view, 6 * SECOND)
    assert not stale["eligible"] and "stale" in stale["reasons"]
    assert stale["best_bid"] is stale["best_ask"] is None
    assert all(pair == [None, None] for pair in stale["cells"])


def test_partial_depth_preserves_known_empty_cells_but_not_unobserved_tails():
    feed = VisualFeed(synthetic_instrument(tick_size="1"))
    feed.quote(0, full=False)
    feed.heartbeat(SECOND + 1)
    view = feed.query()
    column = column_at(view, SECOND)
    assert "partial_depth_coverage" in column["reasons"]
    assert column["eligible"]
    assert cell_at(view, column, "98")[0] == "0"
    assert cell_at(view, column, "99")[0] == "1"
    assert cell_at(view, column, "96")[0] is None
    assert cell_at(view, column, "102")[1] == "0"
    assert cell_at(view, column, "103")[1] == "4"
    assert cell_at(view, column, "104")[1] is None
    coarse = feed.query(price_step="5")
    # The 100..105 ask cell extends past retained ask depth, even though it
    # contains observed asks: reporting their sum as complete would be false.
    assert cell_at(coarse, column_at(coarse, SECOND), "100")[1] is None


def test_gap_breaks_segment_and_invalidates_only_unsupported_book_evidence():
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(SECOND + 1)
    before = column_at(feed.query(), SECOND)
    event = feed.event(Gap("sequence_gap"), 3 * SECOND // 2)
    feed.book = replace(
        feed.book,
        quality=replace(
            feed.book.quality,
            integrity="invalid",
            lifecycle="resynchronizing",
            continuous_since_ordinal=None,
        ),
    )
    feed.deliver(event)
    feed.quote(5 * SECOND // 2)
    feed.heartbeat(3 * SECOND + 1)
    view = feed.query()
    broken = column_at(view, 2 * SECOND)
    recovered = column_at(view, 3 * SECOND)
    assert column_at(view, SECOND) == before
    assert broken["segment_id"] != before["segment_id"]
    assert not broken["eligible"] and broken["reasons"]
    assert all(pair == [None, None] for pair in broken["cells"])
    assert recovered["eligible"]
    assert recovered["segment_id"] == broken["segment_id"]
    assert cell_at(view, recovered, "99")[0] == "1"


def test_gap_inside_coarse_bucket_never_merges_cross_segment_candle_or_footprint():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(SECOND // 2, "before-gap", quantity="2")
    feed.deliver(feed.event(Gap("trade_gap"), 3 * SECOND // 2, "trades"))
    feed.trade(5 * SECOND // 2, "after-gap", side="sell", quantity="3")
    feed.heartbeat(5 * SECOND + 1)
    view = feed.query(bucket_ns=str(5 * SECOND))
    assert len(view["trades"]) == 2
    assert view["trades"][0]["segment_id"] != view["trades"][1]["segment_id"]
    assert "interval_segment_discontinuity" in view["reasons"]
    assert not view["complete_window"]
    assert column_at(view, 0)["candle"] is None
    assert view["footprints"] == []
    assert view["profile"] == [
        {
            "price": "100",
            "buy_base": "2",
            "sell_base": "3",
            "unknown_base": "0",
        }
    ]


def test_trade_dedup_unknown_side_history_and_visible_window_have_exact_outputs():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(SECOND // 10, "historical", quantity="99", price="109", history=True)
    feed.trade(SECOND // 5, "buy", quantity="2", price="100")
    feed.trade(3 * SECOND // 10, "buy", quantity="2", price="100")
    feed.trade(2 * SECOND // 5, "sell", side="sell", quantity="0.5", price="102")
    feed.trade(SECOND // 2, "unknown", side="unknown", quantity="3", price="101")
    feed.heartbeat(SECOND + 1)
    view = feed.query()
    assert len(view["trades"]) == 4
    assert sum(item["is_subscription_history"] for item in view["trades"]) == 1
    assert view["profile"] == [
        {"price": "100", "buy_base": "2", "sell_base": "0", "unknown_base": "0"},
        {"price": "101", "buy_base": "0", "sell_base": "0", "unknown_base": "3"},
        {"price": "102", "buy_base": "0", "sell_base": "0.5", "unknown_base": "0"},
    ]
    assert view["footprints"] == [dict(time_ns="0", **row) for row in view["profile"]]
    candle = column_at(view, 0)["candle"]
    assert {key: value for key, value in candle.items() if key != "reasons"} == {
        "open": "100",
        "high": "102",
        "low": "100",
        "close": "101",
        "volume": "5.5",
        "eligible": False,
    }
    assert "no_trade_evidence" in candle["reasons"]
    assert "no_validated_trade_observation" in candle["reasons"]
    assert not view["complete_window"]
    assert column_at(view, SECOND)["candle"] is None
    recent = feed.query(start_ns=str(2 * SECOND // 5))
    assert len(recent["trades"]) == 2
    assert recent["profile"] == view["profile"][1:]
    assert not any(row["price"] == "109" for row in view["profile"])


def test_late_trade_uses_observed_highwater_without_rewriting_earlier_candle():
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(5 * SECOND)
    before = feed.query()
    old = column_at(before, SECOND)
    feed.trade(SECOND, "late", quantity="2")
    feed.heartbeat(6 * SECOND + 1)
    view = feed.query()
    assert column_at(view, SECOND) == old
    assert view["trades"][0]["time_ns"] == str(5 * SECOND)
    assert view["trades"][0]["asof"]["receive_mono_ns"] == str(SECOND)
    assert view["footprints"] == [
        {
            "time_ns": str(5 * SECOND),
            "price": "100",
            "buy_base": "2",
            "sell_base": "0",
            "unknown_base": "0",
        }
    ]
    assert column_at(view, 5 * SECOND)["candle"]["volume"] == "2"
    historical = feed.query(end_ns=str(4 * SECOND))
    assert (
        historical["trades"] == historical["profile"] == historical["footprints"] == []
    )


def test_reprojection_uses_retained_native_levels_and_pan_is_not_rebinning():
    feed = VisualFeed()
    feed.quote(
        0,
        bids=(("99.9", "1.25"), ("99.1", "2.75"), ("50", "7")),
        asks=(("100.1", "3"), ("100.9", "4"), ("150", "11")),
    )
    native = feed.book
    feed.heartbeat(SECOND + 1)
    fine = feed.query(price_step="0.1")
    coarse = feed.query(price_step="1")
    column = column_at(coarse, SECOND)
    assert cell_at(coarse, column, "99") == ["4", "0"]
    assert cell_at(coarse, column, "100") == ["0", "7"]
    assert coarse["grid_id"] != fine["grid_id"]
    assert feed.query(price_step="0.1")["columns"] == fine["columns"]
    panned = feed.query(price_step="1", price_offset_rows=-50)
    assert panned["grid_id"] == coarse["grid_id"]
    assert cell_at(panned, column_at(panned, SECOND), "50") == ["7", "0"]
    assert feed.book is native
    assert len(native.bids) == len(native.asks) == 3
    feed.quote(2 * SECOND, bids=(("199", "1"),), asks=(("201", "1"),))
    assert feed.query()["price_min"] == coarse["price_min"]
    recentered = feed.query(recenter=True)
    assert recentered["price_min"] == "184"
    assert recentered["grid_id"] == coarse["grid_id"]
    feed.history.reset()
    empty = feed.query()
    assert empty["price_min"] == "0"
    assert empty["columns"] == []
    for batch, books, reading in feed.deliveries[:2]:
        feed.history.on_batch(batch, books, SimpleNamespace(reading=reading))
    rebuilt = feed.query(price_step="1")
    assert rebuilt["price_min"] == coarse["price_min"]
    assert rebuilt["columns"] == coarse["columns"]


def test_incremental_frontier_exclusion_and_generation_replacement():
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(SECOND)
    before = feed.query()
    unchanged = feed.query(after_column=before["next_after"])
    assert not unchanged["replace"] and unchanged["columns"] == []
    feed.trade(SECOND // 2, "late-at-frontier")
    changed = feed.query(after_column=unchanged["next_after"])
    assert not changed["replace"]
    assert changed["columns"] == []
    assert changed["trades"] == []
    replacement = feed.history.query(
        view_request(after_column=changed["next_after"]), "8"
    )
    assert replacement["replace"]
    assert replacement["columns"] == before["columns"]
    feed.heartbeat(SECOND + 1)
    advanced = feed.query()
    assert advanced["trades"][0]["time_ns"] == str(SECOND)
    assert column_at(advanced, SECOND)["candle"]["volume"] == "1"


def test_closed_metric_provenance_isolated_from_query_mutation_and_checkpoint_restore():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "anchor")
    feed.trade(SECOND // 10, "observed-buy", quantity="2")
    feed.quote(SECOND)
    feed.heartbeat(2 * SECOND + 1)
    first = feed.query()
    pristine = deepcopy(first["columns"])
    column = column_at(first, SECOND)
    assert column["cvd"]["value"] == "2"
    column["cvd"]["value"] = "corrupted by consumer"
    column["cvd"]["references"].clear()
    column["ofi"]["eligibility"]["reasons"].append("consumer mutation")
    incremental = feed.query(after_column=first["next_after"])
    assert not incremental["replace"]
    assert incremental["columns"] == []
    replacement = feed.history.query(view_request(), "8")
    assert replacement["columns"] == pristine
    checkpoint = feed.history.checkpoint()
    feed.history.reset()
    feed.history.restore(checkpoint)
    assert feed.query()["columns"] == pristine


def test_query_snapshot_retains_its_prefix_after_eviction_and_rejects_clock_cutover(
    monkeypatch,
):
    monkeypatch.setattr(visuals, "MAX_COLUMNS", 4)
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "anchor")
    feed.trade(SECOND // 10, "observed", quantity="2")
    feed.quote(SECOND)
    feed.heartbeat(2 * SECOND + 1)
    snapshot = feed.history.query_snapshot()
    captured = snapshot.query(view_request(), "7")
    assert column_at(captured, SECOND)["cvd"]["value"] == "2"
    feed.quote(20 * SECOND, bids=(("99", "9"),))
    assert int(feed.query(start_ns=str(17 * SECOND))["retained_start_ns"]) > int(
        captured["end_ns"]
    )
    assert snapshot.query(view_request(), "7")["columns"] == captured["columns"]
    assert feed.history.accept_query(snapshot)
    assert feed.query(start_ns=str(17 * SECOND))["now_ns"] == str(20 * SECOND)
    feed.deliver(
        replace(
            feed.event(Control("heartbeat"), 21 * SECOND),
            clock_id="different-source-clock",
        )
    )
    assert not feed.history.accept_query(snapshot)


def test_column_and_cell_output_bounds_disclose_incomplete_history(monkeypatch):
    monkeypatch.setattr(visuals, "MAX_COLUMNS", 4)
    monkeypatch.setattr(visuals, "MAX_CELLS", 64)
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(10 * SECOND + 1)
    view = feed.query(start_ns=str(7 * SECOND), max_columns=10)
    assert len(view["columns"]) == 2
    assert sum(len(column["cells"]) for column in view["columns"]) == 64
    assert view["truncated"] and not view["complete_window"]
    assert "history_retention_truncated" in view["reasons"]
    assert "column_output_limit" in view["reasons"]
    assert int(view["retained_start_ns"]) > 0
    assert [column["time_ns"] for column in view["columns"]] == [
        str(9 * SECOND),
        str(10 * SECOND),
    ]


def test_byte_budget_evicts_old_evidence_and_retains_exact_recent_cells(monkeypatch):
    monkeypatch.setattr(visuals, "MAX_BYTES", 512 * 1024)
    feed = VisualFeed()
    for stamp in range(24):
        feed.quote(stamp * SECOND, bids=(("99", str(stamp + 1)),))
    native = feed.book
    view = feed.query()
    assert view["truncated"] and not view["complete_window"]
    assert "history_retention_truncated" in view["reasons"]
    assert int(view["retained_start_ns"]) > 0
    assert feed.history.accounted_bytes <= visuals.MAX_BYTES
    assert view["columns"]
    for column in view["columns"]:
        quantity = str(int(column["time_ns"]) // SECOND + 1)
        assert cell_at(view, column, "99")[0] == quantity
    assert feed.book is native
    assert native.bids == (Level(D("99"), D("24")),)


def test_trade_bound_discloses_lost_flow_instead_of_claiming_complete_candles(
    monkeypatch,
):
    monkeypatch.setattr(visuals, "MAX_TRADES", 3)
    feed = VisualFeed()
    feed.quote(0)
    for index in range(5):
        feed.trade((index + 1) * SECOND // 10, str(index), quantity=str(index + 1))
    feed.heartbeat(SECOND + 1)
    view = feed.query()
    assert len(view["trades"]) == 3
    assert [trade["base_quantity"] for trade in view["trades"]] == ["3", "4", "5"]
    assert view["profile"][0]["buy_base"] == "12"
    assert view["truncated"] and not view["complete_window"]
    assert "trade_history_truncated" in view["reasons"]
    candle = column_at(view, 0)["candle"]
    assert candle["volume"] == "12" and not candle["eligible"]
    assert "trade_history_truncated" in candle["reasons"]


def test_query_work_bound_returns_unknown_not_a_partial_sum(monkeypatch):
    monkeypatch.setattr(visuals, "MAX_QUERY_LEVELS", 2)
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(SECOND + 1)
    view = feed.query()
    assert view["truncated"] and not view["complete_window"]
    assert "query_level_work_limit" in view["reasons"]
    for column in view["columns"]:
        assert not column["eligible"]
        assert "query_level_work_limit" in column["reasons"]
        assert all(pair == [None, None] for pair in column["cells"])


@pytest.mark.parametrize(
    "options, message",
    [
        ({"price_step": "0.03"}, "unsupported display price quantum"),
        ({"bucket_ns": "2000000000"}, "unsupported time quantum"),
        ({"start_ns": str(2 * SECOND), "end_ns": "0"}, "visual range"),
    ],
)
def test_unsupported_grid_requests_are_rejected_without_silent_rebinning(
    options, message
):
    feed = VisualFeed()
    feed.quote(0)
    with pytest.raises(ValueError, match=message):
        feed.query(**options)


def test_future_window_is_clamped_to_observed_frontier():
    feed = VisualFeed()
    feed.quote(0)
    feed.heartbeat(SECOND + 1)
    view = feed.query(end_ns=str(100 * SECOND))
    assert view["end_ns"] == view["now_ns"] == str(SECOND + 1)
    assert all(int(column["time_ns"]) <= SECOND + 1 for column in view["columns"])
    assert "trailing_partial_history" in view["reasons"]


def test_adjacent_closed_windows_are_half_open_without_double_counting_boundary_trade():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "inside-first", quantity="2")
    feed.trade(SECOND, "at-boundary", side="sell", quantity="3")
    frontier = feed.query()
    assert [trade["base_quantity"] for trade in frontier["trades"]] == ["2"]
    assert frontier["complete_window"]
    feed.heartbeat(2 * SECOND + 1)
    first = feed.query(start_ns="0", end_ns=str(SECOND))
    second = feed.query(start_ns=str(SECOND), end_ns=str(2 * SECOND))
    assert first["complete_window"] and second["complete_window"]
    assert [column["time_ns"] for column in first["columns"]] == ["0"]
    assert [column["time_ns"] for column in second["columns"]] == [str(SECOND)]
    assert [trade["base_quantity"] for trade in first["trades"]] == ["2"]
    assert [trade["base_quantity"] for trade in second["trades"]] == ["3"]
    assert column_at(first, 0)["candle"]["volume"] == "2"
    assert column_at(second, SECOND)["candle"]["volume"] == "3"
    assert first["profile"] == [
        {
            "price": "100",
            "buy_base": "2",
            "sell_base": "0",
            "unknown_base": "0",
        }
    ]
    assert second["profile"] == [
        {
            "price": "100",
            "buy_base": "0",
            "sell_base": "3",
            "unknown_base": "0",
        }
    ]
    assert {trade["id"] for trade in first["trades"]}.isdisjoint(
        trade["id"] for trade in second["trades"]
    )


def test_configured_trade_channel_without_validated_evidence_cannot_prove_empty_flow():
    feed = VisualFeed()
    feed.quote(0)
    native = feed.book
    feed.deliver(feed.event(Control("heartbeat"), SECOND, "trades"))
    feed.heartbeat(2 * SECOND + 1)
    view = feed.query(start_ns=str(SECOND), end_ns=str(2 * SECOND))
    assert not view["complete_window"]
    assert {
        "trade_history_unanchored",
        "trade_channel_unhealthy",
        "no_validated_trade_observation",
    } <= set(view["reasons"])
    column = column_at(view, SECOND)
    assert column["eligible"] and column["candle"] is None
    assert cell_at(view, column, "99") == ["1", "0"]
    assert view["trades"] == view["profile"] == view["footprints"] == []
    assert feed.book is native and feed.history.samples[0].book is native
    initial = feed.query(end_ns=str(SECOND))
    assert "no_trade_evidence" in initial["reasons"]
    assert not initial["complete_window"]


def test_quiet_window_requires_endpoint_validation_not_only_fresh_channel_evidence():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "establish")
    feed.heartbeat(2 * SECOND + 1)
    options = dict(start_ns=str(SECOND), end_ns=str(2 * SECOND))
    quiet = feed.query(**options)
    assert "quiet_trade_interval_unproven" in quiet["reasons"]
    assert not quiet["complete_window"]
    assert column_at(quiet, SECOND)["eligible"]
    assert quiet["trades"] == quiet["profile"] == quiet["footprints"] == []

    validated = VisualFeed()
    validated.quote(0)
    validated.trade(0, "establish")
    validated.deliver(
        validated.event(
            Control("heartbeat", reason_code="trade_channel_healthy"),
            2 * SECOND - 1,
            "trades",
        )
    )
    validated.heartbeat(2 * SECOND + 1)
    proven = validated.query(**options)
    assert proven["complete_window"]
    assert "quiet_trade_interval_unproven" not in proven["reasons"]
    assert proven["trades"] == proven["profile"] == proven["footprints"] == []


def test_stale_flow_remains_unavailable_with_fresh_native_liquidity_after_recovery():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "establish")
    feed.quote(6 * SECOND)
    native = feed.book
    feed.heartbeat(7 * SECOND + 1)
    options = dict(start_ns=str(6 * SECOND), end_ns=str(7 * SECOND))
    stale = feed.query(**options)
    assert not stale["complete_window"]
    assert "stale_trade_channel" in stale["reasons"]
    assert column_at(stale, 6 * SECOND)["eligible"]
    assert stale["profile"] == stale["footprints"] == stale["trades"] == []
    feed.trade(7 * SECOND + 1, "recovered", quantity="2")
    feed.heartbeat(8 * SECOND + 1)
    assert feed.query(**options)["columns"] == stale["columns"]
    assert feed.query(**options)["reasons"] == stale["reasons"]
    assert feed.book is native


@pytest.mark.parametrize("control", ["disconnect", "reset", "rejection"])
def test_unhealthy_trade_channel_does_not_hide_resting_liquidity_or_rewrite_closed_flow(
    control,
):
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(0, "before", quantity="2")
    feed.quote(SECOND)
    before = feed.query(end_ns=str(SECOND))
    assert before["complete_window"]
    feed.deliver(feed.event(Control(control), SECOND, "trades"))
    native = feed.book
    feed.heartbeat(2 * SECOND + 1)
    unhealthy = feed.query(start_ns=str(SECOND), end_ns=str(2 * SECOND))
    assert not unhealthy["complete_window"]
    assert {"trade_channel_unhealthy", "trade_continuity_reset"} <= set(
        unhealthy["reasons"]
    )
    column = column_at(unhealthy, SECOND)
    assert column["eligible"] and column["candle"] is None
    assert cell_at(unhealthy, column, "99") == ["1", "0"]
    assert feed.book is native
    assert feed.query(end_ns=str(SECOND))["columns"] == before["columns"]
    assert feed.query(end_ns=str(SECOND))["complete_window"]


def test_ambiguous_matching_trade_streams_never_double_count_flow():
    feed = VisualFeed(trade_channels=("trades", "trade"))
    feed.quote(0)
    native = feed.book
    feed.trade(0, "same-execution", quantity="3")
    feed.trade(0, "same-execution", quantity="3", channel="trade")
    feed.heartbeat(SECOND + 1)
    view = feed.query(end_ns=str(SECOND))
    assert not view["complete_window"]
    assert "ambiguous_trade_stream" in view["reasons"]
    assert view["trades"] == view["profile"] == view["footprints"] == []
    column = column_at(view, 0)
    assert column["candle"] is None
    assert column["eligible"] and cell_at(view, column, "99") == ["1", "0"]
    assert feed.book is native
    assert (
        feed.history.engine.snapshot(
            str(feed.instrument.instrument_id),
            feed.clock.reading,
            key=feed.keys["book"],
        ).tape
        == ()
    )


def test_flow_quality_checkpoint_reset_and_reconstruction_preserve_closed_evidence():
    feed = VisualFeed()
    feed.quote(0)
    feed.trade(SECOND // 2, "first", quantity="2")
    feed.deliver(feed.event(Control("reset"), SECOND, "trades"))
    feed.heartbeat(2 * SECOND + 1)
    request = view_request(end_ns=str(2 * SECOND))
    prefix = feed.history.query(request, "7")
    assert not prefix["complete_window"]
    assert "no_trade_evidence" in prefix["reasons"]
    assert "trade_continuity_reset" in prefix["reasons"]
    assert not column_at(prefix, 0)["candle"]["eligible"]
    checkpoint = feed.history.checkpoint()
    boundary = len(feed.deliveries)
    feed.trade(3 * SECOND, "first", quantity="7")
    feed.quote(3 * SECOND)
    feed.heartbeat(4 * SECOND + 1)
    final_request = view_request(end_ns=str(4 * SECOND))
    final = feed.history.query(final_request, "7")
    assert column_at(final, 3 * SECOND)["candle"]["volume"] == "7"
    assert column_at(final, 3 * SECOND)["candle"]["eligible"]
    assert feed.history.query(request, "7")["columns"] == prefix["columns"]
    deliveries = tuple(feed.deliveries)
    feed.history.restore(checkpoint)
    assert feed.history.query(request, "7") == prefix
    for batch, books, reading in deliveries[boundary:]:
        feed.history.on_batch(batch, books, SimpleNamespace(reading=reading))
    assert feed.history.query(final_request, "7") == final
    feed.history.reset()
    empty = feed.history.query(final_request, "7")
    assert not empty["complete_window"]
    assert empty["columns"] == empty["trades"] == empty["profile"] == []
    for batch, books, reading in deliveries[:boundary]:
        feed.history.on_batch(batch, books, SimpleNamespace(reading=reading))
    assert feed.history.query(request, "7") == prefix
    for batch, books, reading in deliveries[boundary:]:
        feed.history.on_batch(batch, books, SimpleNamespace(reading=reading))
    assert feed.history.query(final_request, "7") == final


def test_real_replay_backward_seek_speed_parity_checkpoint_and_reprojection(tmp_path):
    async def scenario():
        with await create_demo(tmp_path, duration_seconds=3) as capture:
            streams = capture.reader.manifest.streams
            key = next(stream.key for stream in streams if stream.key.channel == "book")
            expected = None
            for mode in ("normalized", "raw"):
                for speed in (D(1), D(1000000)):
                    observer = VisualHistory(AnalyticsEngine(streams), key)
                    session = ReplaySession(
                        capture.reader, mode=mode, observers=(observer,)
                    )
                    try:
                        session.step(5)
                        cursor = session.cursor
                        request = view_request(start_ns=None, price_step=None)
                        prefix = observer.query(request, "1")
                        # The first five bootstrap deliveries precede the selected
                        # trade subscription: a later seek must restore missing
                        # evidence, not inherit the final healthy trade source.
                        assert "no_trade_evidence" in prefix["reasons"]
                        assert not prefix["complete_window"]
                        assert prefix["trades"] == prefix["profile"] == []
                        await session.run_to_end(speed)
                        final = observer.query(request, "1")
                        assert final["columns"]
                        if expected is None:
                            expected = final
                        assert final == expected
                        step = next(
                            option
                            for option in final["step_options"]
                            if option != final["price_step"]
                        )
                        reprojected = observer.query(
                            view_request(start_ns=None, price_step=step), "1"
                        )
                        assert reprojected["grid_id"] != final["grid_id"]
                        assert (
                            observer.query(
                                view_request(
                                    start_ns=None, price_step=final["price_step"]
                                ),
                                "1",
                            )
                            == final
                        )
                        restored = VisualHistory(AnalyticsEngine(streams), key)
                        restored.restore(observer.checkpoint())
                        assert restored.query(request, "1") == final
                        session.seek(cursor.ordinal, cursor.event_index)
                        rewound = observer.query(request, "1")
                        assert rewound == prefix
                        assert all(
                            int(trade["time_ns"]) <= int(prefix["now_ns"])
                            for trade in rewound["trades"]
                        )
                        await session.run_to_end(D(1000000))
                        assert observer.query(request, "1") == final
                        session.seek(0)
                        empty = observer.query(request, "1")
                        assert (
                            empty["columns"]
                            == empty["trades"]
                            == empty["profile"]
                            == []
                        )
                    finally:
                        session.close()

    asyncio.run(scenario())


def contract_instrument(*, inverse=False, **overrides):
    metadata = dict(
        lot_size="1",
        market_type="perpetual",
        quantity_unit="contracts",
        inverse=inverse,
        contract_multiplier=D("100") if inverse else D("0.1"),
        contract_value_currency="USD" if inverse else "BTC",
        settlement=AssetIdentity("BTC", "BTC")
        if inverse
        else AssetIdentity("USD", "USD"),
        settlement_conversion="none",
        contract_specification_source="synthetic explicit specification",
    )
    metadata.update(overrides)
    return synthetic_instrument(**metadata)


def ladder(feed, **overrides):
    request = SimpleNamespace(price_step="1", rows=100)
    request.__dict__.update(overrides)
    return grouped_ladder(feed.book, feed.instrument, feed.clock.reading, request, "7")


def test_grouped_ladder_exact_native_conversion_outward_rounding_and_cumulative_totals():
    feed = VisualFeed(contract_instrument())
    feed.quote(
        0,
        bids=(("99.9", "2"), ("99.1", "3"), ("98.9", "7")),
        asks=(("100.1", "4"), ("100.9", "5"), ("101.1", "6")),
    )
    result = ladder(feed)
    assert result["eligible"] and result["unit"] == "BTC"
    assert result["quote_unit"] == "USD"
    assert result["tick_size"] == "0.01"
    assert result["step"] in result["step_options"]
    assert result["summary"] == {
        "best_bid": "99.9",
        "best_ask": "100.1",
        "midpoint": "100",
        "spread": "0.2",
        "spread_ticks": "20",
        "spread_bps": "20",
    }
    assert result["rows"] == [
        {
            "side": "bid",
            "price": "99",
            "base_quantity": "0.5",
            "cumulative_base": "0.5",
            "quote_notional": "49.71",
            "cumulative_quote": "49.71",
        },
        {
            "side": "bid",
            "price": "98",
            "base_quantity": "0.7",
            "cumulative_base": "1.2",
            "quote_notional": "69.23",
            "cumulative_quote": "118.94",
        },
        {
            "side": "ask",
            "price": "101",
            "base_quantity": "0.9",
            "cumulative_base": "0.9",
            "quote_notional": "90.49",
            "cumulative_quote": "90.49",
        },
        {
            "side": "ask",
            "price": "102",
            "base_quantity": "0.6",
            "cumulative_base": "1.5",
            "quote_notional": "60.66",
            "cumulative_quote": "151.15",
        },
    ]
    assert ladder(feed, rows=1)["rows"] == [result["rows"][0], result["rows"][2]]
    assert D(result["rows"][0]["price"]) < D(result["rows"][2]["price"])
    coarse = ladder(feed, price_step="10")
    assert coarse["summary"] == result["summary"]
    for side in ("bid", "ask"):
        native_total = sum(
            D(row["quote_notional"]) for row in result["rows"] if row["side"] == side
        )
        grouped = next(row for row in coarse["rows"] if row["side"] == side)
        assert D(grouped["quote_notional"]) == native_total
        assert D(grouped["cumulative_quote"]) == native_total
        assert D(grouped["price"]) * D(grouped["base_quantity"]) != native_total


def test_inverse_ladder_converts_each_native_price_before_grouping():
    feed = VisualFeed(contract_instrument(inverse=True))
    feed.quote(
        0,
        bids=(("100", "2"), ("50", "3")),
        asks=(("125", "5"), ("200", "8")),
    )
    result = ladder(feed, price_step="10")
    assert result["eligible"]
    assert result["rows"] == [
        {
            "side": "bid",
            "price": "100",
            "base_quantity": "2",
            "cumulative_base": "2",
            "quote_notional": "200",
            "cumulative_quote": "200",
        },
        {
            "side": "bid",
            "price": "50",
            "base_quantity": "6",
            "cumulative_base": "8",
            "quote_notional": "300",
            "cumulative_quote": "500",
        },
        {
            "side": "ask",
            "price": "130",
            "base_quantity": "4",
            "cumulative_base": "4",
            "quote_notional": "500",
            "cumulative_quote": "500",
        },
        {
            "side": "ask",
            "price": "200",
            "base_quantity": "4",
            "cumulative_base": "8",
            "quote_notional": "800",
            "cumulative_quote": "1300",
        },
    ]
    grouped_ask = next(
        row for row in ladder(feed, price_step="100")["rows"] if row["side"] == "ask"
    )
    assert grouped_ask == {
        "side": "ask",
        "price": "200",
        "base_quantity": "8",
        "cumulative_base": "8",
        "quote_notional": "1300",
        "cumulative_quote": "1300",
    }


def test_ladder_partial_depth_is_explicit_but_unknown_units_and_stale_are_unavailable():
    feed = VisualFeed()
    feed.quote(0, full=False)
    partial = ladder(feed)
    assert partial["eligible"] and partial["rows"]
    assert "partial_depth_coverage" in partial["reasons"]
    assert partial["summary"] is not None
    assert partial["coverage"] == {
        "subscribed_depth": None,
        "retained_bid_levels": "2",
        "retained_ask_levels": "2",
        "full_depth": False,
    }
    assert partial["source_mode"] == "ws_delta"
    feed.heartbeat(6 * SECOND)
    stale = ladder(feed)
    assert not stale["eligible"] and stale["rows"] == []
    assert "stale" in stale["reasons"]
    assert stale["summary"] is None
    assert stale["coverage"] == partial["coverage"]
    unknown = VisualFeed(contract_instrument(contract_specification_source=None))
    unknown.quote(0)
    unavailable = ladder(unknown)
    assert not unavailable["eligible"] and unavailable["rows"] == []
    assert "unknown_contract_valuation" in unavailable["reasons"]
    assert unavailable["summary"] is None


def test_ladder_unknown_tick_reports_display_steps_without_inventing_spread_ticks():
    instrument = replace(
        synthetic_instrument(),
        tick_size=None,
        constraints=OrderConstraints(price_rule="unconstrained"),
    )
    feed = VisualFeed(instrument)
    feed.quote(0)
    result = ladder(feed)
    assert result["eligible"] and result["tick_size"] is None
    assert "display_quantum_not_native_tick" in result["reasons"]
    assert result["step_options"] == ["0.01", "0.1", "1", "10", "100"]
    assert result["summary"] == {
        "best_bid": "99",
        "best_ask": "101",
        "midpoint": "100",
        "spread": "2",
        "spread_ticks": None,
        "spread_bps": "200",
    }


@pytest.mark.parametrize("missing_side", ["bids", "asks"])
def test_ladder_one_sided_evidence_never_produces_top_of_book_summary(missing_side):
    feed = VisualFeed()
    feed.quote(0)
    feed.book = replace(
        feed.book,
        quality=replace(feed.book.quality, integrity="invalid"),
        **{missing_side: ()},
    )
    result = ladder(feed)
    assert not result["eligible"] and result["rows"] == []
    assert result["summary"] is None
    assert "empty_side" in result["reasons"]


def test_ladder_missing_evidence_keeps_metadata_without_book_claims():
    feed = VisualFeed()
    result = ladder(feed)
    assert not result["eligible"] and result["rows"] == []
    assert result["summary"] is result["coverage"] is result["source_mode"] is None
    assert result["tick_size"] == "0.01"
    assert result["quote_unit"] == "USD"
    assert result["step"] in result["step_options"]


def test_ladder_work_bound_never_exposes_partial_bucket_or_summary(monkeypatch):
    feed = VisualFeed()
    feed.quote(0)
    monkeypatch.setattr(visuals, "MAX_QUERY_LEVELS", 1)
    result = ladder(feed, price_step="10")
    assert not result["eligible"] and result["rows"] == []
    assert result["summary"] is None
    assert "query_level_work_limit" in result["reasons"]


def test_ladder_preserves_sub_float_price_spread_and_notional_precision():
    feed = VisualFeed()
    feed.quote(
        0,
        bids=(("9007199254740992.01", "0.125"),),
        asks=(("9007199254740992.03", "0.375"),),
    )
    native_book = feed.book
    with localcontext() as context:
        context.prec = 6
        result = ladder(feed)
    assert result["eligible"]
    assert feed.book is native_book
    summary = result["summary"]
    assert summary["best_bid"] == "9007199254740992.01"
    assert summary["best_ask"] == "9007199254740992.03"
    assert summary["midpoint"] == "9007199254740992.02"
    assert summary["spread"] == "0.02"
    assert summary["spread_ticks"] == "2"
    with localcontext(DECIMAL_CONTEXT):
        assert D(summary["spread_bps"]) == D("0.02") / D(summary["midpoint"]) * 10000
        for row, level in zip(result["rows"], (*native_book.bids, *native_book.asks)):
            assert D(row["quote_notional"]) == level.price * level.quantity
            assert row["cumulative_quote"] == row["quote_notional"]


@pytest.mark.parametrize("offset", [0, 1000])
def test_unknown_contract_units_never_become_zero_even_outside_native_book(offset):
    feed = VisualFeed(contract_instrument(contract_specification_source=None))
    feed.quote(0)
    feed.trade(SECOND // 2, "unknown-quantity", quantity="3")
    feed.heartbeat(SECOND + 1)
    view = feed.query(price_offset_rows=offset)
    assert view["quantity_unit"] == "unavailable"
    assert view["trades"][0]["base_quantity"] is None
    assert "unknown_trade_quantity" in view["reasons"]
    assert not view["complete_window"]
    assert view["profile"] == view["footprints"] == []
    for column in view["columns"]:
        assert not column["eligible"]
        assert "unknown_contract_valuation" in column["reasons"]
        assert all(pair == [None, None] for pair in column["cells"])
        assert column["candle"] is None


def risk(instrument=None, **overrides):
    return proposal_risk(
        instrument or synthetic_instrument(),
        ClockReading("synthetic-clock", SECOND, 1_700_000_000 * SECOND),
        risk_request(**overrides),
        "7",
    )


@pytest.mark.parametrize(
    "side, stop, gross, fee, total",
    [("buy", "90", "20", "0.38", "20.38"), ("sell", "110", "20", "0.42", "20.42")],
)
def test_proposal_loss_uses_both_legs_exact_fees_and_explains_non_guarantees(
    side, stop, gross, fee, total
):
    result = risk(side=side, stop_price=stop)
    assert result["eligible"]
    assert result["base_quantity"] == "2"
    assert result["gross_loss"] == gross
    assert result["fee_cost"] == fee
    assert result["estimated_loss"] == total
    assert {
        "hypothetical_entry_and_stop_not_fills",
        "stop_liquidity_unknown",
        "gaps_and_slippage_can_exceed_estimate",
        "not_a_guaranteed_loss_bound",
    } <= set(result["assumptions"])


def test_unknown_fees_preserve_gross_but_never_net_or_risk_budget_size():
    unknown = dict(fee_bps=None, fee_provenance=None, fee_date=None)
    result = risk(**unknown)
    assert result["gross_loss"] == "20"
    assert result["fee_cost"] is result["estimated_loss"] is None
    assert not result["eligible"] and "unknown_fees" in result["reasons"]
    sized = risk(base_quantity=None, risk_budget="100", **unknown)
    assert sized["suggested_base_quantity"] is sized["base_quantity"] is None
    assert sized["estimated_loss"] is None and not sized["eligible"]
    explicit_zero = risk(fee_bps="0")
    assert explicit_zero["eligible"]
    assert explicit_zero["fee_cost"] == "0" and explicit_zero["estimated_loss"] == "20"


def test_budget_size_rounds_down_native_lots_including_fees():
    instrument = synthetic_instrument(lot_size="0.1")
    result = risk(instrument, base_quantity=None, risk_budget="25")
    assert result["eligible"]
    assert result["suggested_base_quantity"] == result["base_quantity"] == "2.4"
    assert result["gross_loss"] == "24"
    assert result["fee_cost"] == "0.456"
    assert result["estimated_loss"] == "24.456"
    assert D(result["estimated_loss"]) <= D(25)
    assert D("2.5") * D("10.19") > D(25)
    too_small = risk(instrument, base_quantity=None, risk_budget="0.1")
    assert not too_small["eligible"] and too_small["suggested_base_quantity"] is None
    assert "risk_budget_below_minimum_lot" in too_small["reasons"]


def test_contract_risk_uses_fixed_native_position_and_native_lot_caps():
    instrument = contract_instrument(constraints=OrderConstraints(max_market_base=D(3)))
    result = risk(instrument, base_quantity=None, risk_budget="100")
    assert result["eligible"] and result["suggested_base_quantity"] == "0.3"
    assert result["gross_loss"] == "3" and result["fee_cost"] == "0.057"
    inverse = risk(
        contract_instrument(inverse=True),
        entry_price="200",
        stop_price="100",
        base_quantity="1",
    )
    assert inverse["eligible"]
    assert inverse["gross_loss"] == "100"
    # Two fixed $100 contracts pay $0.20 per leg, not fees on a shrinking
    # hypothetical base position re-sized at the stop.
    assert inverse["fee_cost"] == "0.4" and inverse["estimated_loss"] == "100.4"


@pytest.mark.parametrize(
    "constraints, quantity, reason",
    [
        (
            OrderConstraints(min_base=D("1")),
            "0.5",
            "order_outside_instrument_constraints",
        ),
        (
            OrderConstraints(max_base=D("1")),
            "2",
            "order_outside_instrument_constraints",
        ),
        (
            OrderConstraints(max_limit_base=D("1")),
            "2",
            "order_outside_instrument_constraints",
        ),
        (
            OrderConstraints(max_market_base=D("1")),
            "2",
            "order_outside_instrument_constraints",
        ),
        (
            OrderConstraints(min_notional=D("95"), min_notional_currency="USD"),
            "1",
            "order_outside_instrument_constraints",
        ),
        (
            OrderConstraints(max_notional=D("190"), min_notional_currency="USD"),
            "2",
            "order_outside_instrument_constraints",
        ),
        (OrderConstraints(size_rule="variable"), "2", "unsupported_size_rule"),
    ],
)
def test_explicit_proposal_quantity_obeys_all_native_order_constraints(
    constraints, quantity, reason
):
    result = risk(synthetic_instrument(constraints=constraints), base_quantity=quantity)
    assert not result["eligible"]
    assert reason in result["reasons"]


def test_budget_size_caps_notional_at_both_entry_and_stop_and_rounds_conservatively():
    instrument = synthetic_instrument(
        lot_size="0.1",
        constraints=OrderConstraints(
            max_notional=D("205"), min_notional_currency="USD"
        ),
    )
    result = risk(
        instrument, side="sell", stop_price="110", base_quantity=None, risk_budget="100"
    )
    assert result["eligible"] and result["suggested_base_quantity"] == "1.8"
    assert result["gross_loss"] == "18" and result["estimated_loss"] == "18.378"
    assert D(result["base_quantity"]) * D(110) <= D(205)
    assert D("1.9") * D(110) > D(205)


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"stop_price": "100"}, "stop_must_be_below_buy_or_above_sell_entry"),
        ({"stop_price": "101"}, "stop_must_be_below_buy_or_above_sell_entry"),
        (
            {"side": "sell", "stop_price": "90"},
            "stop_must_be_below_buy_or_above_sell_entry",
        ),
        ({"entry_price": "100.001"}, "unsupported_or_invalid_price_precision"),
        ({"base_quantity": "0.0001"}, "quantity_off_native_lot"),
        ({"fee_date": "2099-01-01"}, "fee_date_in_future"),
    ],
)
def test_invalid_proposal_is_never_eligible(overrides, reason):
    result = risk(**overrides)
    assert not result["eligible"] and reason in result["reasons"]
    assert result["suggested_base_quantity"] is None


def test_visual_and_risk_economics_ignore_ambient_decimal_precision():
    feed = VisualFeed(contract_instrument())
    feed.quote(0, bids=(("99.9", "12345"),), asks=(("100.1", "67890"),))
    expected_ladder = ladder(feed)
    expected_risk = risk(base_quantity="123.456")
    with localcontext() as context:
        context.prec = 6
        assert ladder(feed) == expected_ladder
        assert risk(base_quantity="123.456") == expected_risk
    with localcontext(DECIMAL_CONTEXT):
        assert D(expected_risk["estimated_loss"]) == D("123.456") * D("10.19")
