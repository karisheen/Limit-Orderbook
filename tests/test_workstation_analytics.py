"""Consumer-visible formulas, evidence boundaries, and real replay observers."""

import asyncio
from dataclasses import fields, replace
from decimal import Decimal as D, localcontext

import pytest

from src.workstation.analytics import AnalyticsConfig, AnalyticsEngine
from src.workstation.book import Book, BookReducer, Level
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
from src.workstation.instruments import synthetic_instrument
from src.workstation.precision import DECIMAL_CONTEXT, DERIVED_DECIMAL_POLICY
from src.workstation.quality import (
    Capabilities,
    ClockReading,
    Coverage,
    EligibilityPolicy,
    Quality,
    TradeEvidence,
    evaluate_trades,
)
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock, ReplaySession
from src.workstation.wire import dumps, loads


class Feed:
    def __init__(self, *, exact=True, config=None, instrument=None):
        self.instrument = instrument or synthetic_instrument()
        self.keys = {
            channel: SubscriptionKey.for_instrument(self.instrument, channel=channel)
            for channel in ("book", "trades")
        }
        self.streams = tuple(
            StreamSpec(channel, self.instrument, key, "test-v1", "test-config")
            for channel, key in self.keys.items()
        )
        self.config = config or AnalyticsConfig(
            window_ns=10,
            retention_ns=100,
            persistence_ns=2,
            policy=EligibilityPolicy(max_age_ns=1000),
        )
        self.engine = AnalyticsEngine(self.streams, self.config)
        self.clock = ReplayClock()
        self.book = None
        self.ordinal = 0
        self.exact = exact

    def event(self, payload, time, channel="trades", **overrides):
        self.ordinal += 1
        return synthetic_envelope(
            self.instrument,
            payload,
            ordinal=self.ordinal,
            mono_ns=time,
            key=self.keys[channel],
            stream_id=channel,
            source_mode="ws_delta" if self.exact else "ws_snapshot",
            **overrides,
        )

    def deliver(self, event):
        self.clock.advance(event)
        self.engine.on_batch(
            RecordedBatch(self.ordinal, (event,)),
            (self.book,) if self.book else (),
            self.clock,
        )

    def quote(
        self,
        time,
        bids=(("99", "1"),),
        asks=(("101", "2"),),
        *,
        full=True,
        snapshot=False,
    ):
        levels = tuple(
            LevelChange(side, D(price), D(quantity))
            for side, values in (("bid", bids), ("ask", asks))
            for price, quantity in values
        )
        event = self.event(
            BookUpdate(
                "snapshot"
                if snapshot or self.book is None or not self.exact
                else "delta",
                levels,
            ),
            time,
            "book",
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
                last_book_observation_mono_ns=time,
                last_healthy_observation_mono_ns=time,
                clock_id=event.clock_id,
                coverage=Coverage(
                    retained_bid_levels=len(bids),
                    retained_ask_levels=len(asks),
                    worst_bid=bid_levels[-1].price,
                    worst_ask=ask_levels[-1].price,
                    full_depth=full,
                ),
                capabilities=Capabilities(
                    deltas=self.exact, exact_event_flow=self.exact
                ),
                continuous_since_ordinal=1,
            ),
        )
        self.deliver(event)
        return event

    def trade(self, time, key, side="buy", quantity="1", price="100", history=False):
        event = self.event(
            Trade(
                key,
                key,
                D(price),
                D(quantity),
                taker_side=side,
                is_subscription_history=history,
            ),
            time,
        )
        self.deliver(event)
        return event

    def snapshot(self, time):
        return self.engine.snapshot(
            self.instrument.instrument_id, ClockReading("synthetic-clock", time)
        )

    def metrics(self, time):
        return {m.name: m for m in self.snapshot(time).metrics}


def test_exact_quotes_weighted_mid_and_signed_imbalance():
    feed = Feed()
    feed.quote(1)
    result = feed.metrics(1)
    assert result["mid"].value == D(100)
    assert result["spread_native"].value == D(2)
    assert result["spread_bps"].value == D(200)
    with localcontext(DECIMAL_CONTEXT):
        assert result["weighted_mid"].value == D(299) / 3
        assert result["signed_imbalance"].value == -D(1) / 3
        assert result["bid_share"].value == D(1) / 3
    metric = result["weighted_mid"]
    assert metric.unit == "USD/BTC"
    assert metric.asof == feed.book.asof
    assert metric.references == (feed.book.asof,)
    assert (
        loads(
            dumps(metric, decimal_policy=DERIVED_DECIMAL_POLICY),
            type(metric),
            decimal_policy=DERIVED_DECIMAL_POLICY,
        )
        == metric
    )
    with pytest.raises(ValueError):
        dumps(metric)


def test_reducer_to_analytics_preserves_sub_float_price_and_quantity_resolution():
    increment = "0.000000000000000001"
    feed = Feed(
        instrument=synthetic_instrument(tick_size=increment, lot_size=increment)
    )
    reducer = BookReducer(
        feed.instrument,
        feed.keys["book"],
        segment_id="synthetic-segment",
        capabilities=Capabilities(deltas=True, exact_event_flow=True),
    )
    bid = D("100.000000000000000001")
    ask = D("100.000000000000000002")
    quantity = D(increment)
    event = feed.event(
        BookUpdate(
            "snapshot",
            (
                LevelChange("bid", bid, quantity),
                LevelChange("bid", D("99"), quantity),
                LevelChange("ask", ask, quantity),
                LevelChange("ask", D("101"), quantity),
            ),
        ),
        1,
        "book",
    )
    with localcontext() as context:
        context.prec = 6
        reduced = reducer.apply((event,))
        assert reduced.recovery is None and reduced.book is not None
        feed.book = reduced.book
        feed.deliver(event)
        metrics = feed.metrics(1)
    assert feed.book.bids == (Level(bid, quantity), Level(D("99"), quantity))
    assert feed.book.asks == (Level(ask, quantity), Level(D("101"), quantity))
    expected = {
        "best_bid": bid,
        "best_ask": ask,
        "spread_native": quantity,
        "mid": D("100.0000000000000000015"),
        "weighted_mid": D("100.0000000000000000015"),
        "signed_imbalance": D("0"),
        "bid_share": D("0.5"),
        "depth_bid_5bps_base": quantity,
        "depth_ask_5bps_base": quantity,
    }
    for name, value in expected.items():
        metric = metrics[name]
        assert isinstance(metric.value, D)
        assert metric.value == value
        assert metric.asof == feed.book.asof
        assert (
            loads(
                dumps(metric, decimal_policy=DERIVED_DECIMAL_POLICY),
                type(metric),
                decimal_policy=DERIVED_DECIMAL_POLICY,
            )
            == metric
        )


def test_depth_includes_boundary_and_requires_genuine_band_coverage():
    feed = Feed()
    feed.quote(
        1,
        bids=(("99.99", "1"), ("99.95", "2"), ("99.94", "4")),
        asks=(("100.01", "3"), ("100.05", "5")),
        full=False,
    )
    metrics = feed.metrics(1)
    assert metrics["depth_bid_5bps_base"].value == D(3)
    assert metrics["depth_bid_5bps_quote"].value == D("299.89")
    assert metrics["depth_ask_5bps_base"].value == D(8)
    assert metrics["depth_bid_10bps_base"].value is None
    assert "incomplete_depth_band" in metrics["depth_bid_10bps_base"].reasons
    assert "incomplete_depth_band" in metrics["depth_ask_50bps_quote"].reasons


@pytest.mark.parametrize(
    "bids,asks,expected",
    [
        ((("100", "3"),), (("101", "2"),), "3"),
        ((("98", "3"),), (("101", "2"),), "-1"),
        ((("99", "3"),), (("101", "2"),), "2"),
        ((("99", "1"),), (("100", "4"),), "-4"),
        ((("99", "1"),), (("102", "4"),), "2"),
        ((("99", "1"),), (("101", "4"),), "-2"),
    ],
)
def test_cont_indicator_equation_all_quote_directions(bids, asks, expected):
    feed = Feed()
    feed.quote(1)
    feed.quote(11, bids=bids, asks=asks)
    metric = feed.metrics(11)["event_ofi"]
    assert metric.value == D(expected)
    assert metric.formula_version == "cont-best-quote-indicators-v1"
    assert metric.window.start_mono_ns == 1


def test_snapshot_flow_never_mislabeled_event_ofi():
    feed = Feed(exact=False)
    feed.quote(1)
    feed.quote(11, bids=(("99", "4"),))
    metrics = feed.metrics(11)
    assert metrics["snapshot_best_quote_flow"].value == D(3)
    assert metrics["event_ofi"].value is None
    assert "missing_exact_event_flow" in metrics["event_ofi"].reasons


def test_trade_only_dedup_unknown_side_and_history_exclusion():
    feed = Feed()
    feed.trade(1, "historical", quantity="99", history=True)
    assert "trade_history_unanchored" in feed.metrics(1)["cvd_base"].reasons
    feed.trade(2, "anchor")
    feed.trade(3, "buy", quantity="2")
    feed.trade(4, "buy", quantity="2")
    feed.trade(5, "sell", "sell", quantity="0.5")
    feed.trade(12, "unknown", "unknown", quantity="3")
    result = feed.metrics(12)
    assert result["trade_buy_notional"].value == D(200)
    assert result["trade_sell_notional"].value == D(50)
    assert result["trade_unknown_notional"].value == D(300)
    assert result["cvd_base"].value == D("1.5")
    assert result["cvd_base"].quality.__class__ is TradeEvidence
    assert result["best_bid"].value is None
    tape = feed.snapshot(12).tape
    assert len(tape) == 5
    assert tape[0].trade.is_subscription_history
    profile = feed.snapshot(12).profile[0]
    assert (profile.buy_base, profile.sell_base, profile.unknown_base) == (
        D(2),
        D("0.5"),
        D(3),
    )


def test_positive_partial_observations_are_explicit_not_complete_history():
    feed = Feed()
    feed.trade(5, "first", quantity="2")
    metrics = feed.metrics(5)
    assert metrics["trade_buy_notional"].value is None
    assert "trade_window_warming" in metrics["trade_buy_notional"].reasons
    assert metrics["observed_trade_buy_notional"].value == D(200)
    assert (
        "not_complete_history"
        in metrics["observed_trade_buy_notional"].window.definition
    )
    assert metrics["executed_profile_base"].value == D(2)


def test_generic_transport_activity_cannot_establish_or_prove_quiet_trades():
    feed = Feed()
    feed.deliver(feed.event(Control("heartbeat", "ws_pong"), 1))
    assert feed.metrics(1)["trade_buy_notional"].value is None
    feed.trade(2, "first")
    feed.deliver(feed.event(Control("heartbeat", "ws_pong"), 20))
    metric = feed.metrics(20)["trade_buy_notional"]
    assert metric.value is None
    assert "quiet_trade_interval_unproven" in metric.reasons
    feed.deliver(feed.event(Control("heartbeat", "trade_channel_healthy"), 21))
    assert feed.metrics(21)["trade_buy_notional"].value == 0


def test_independent_book_and_trade_gaps_and_reset_anchors():
    feed = Feed()
    feed.quote(1)
    feed.trade(1, "anchor")
    feed.trade(11, "recent", quantity="2")
    event = feed.event(Gap("sequence_gap"), 11, "book")
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
    metrics = feed.metrics(11)
    assert metrics["event_ofi"].value is None
    assert metrics["cvd_base"].value == D(2)
    feed.deliver(feed.event(Gap("trade_gap"), 12))
    assert "trade_continuity_reset" in feed.metrics(12)["cvd_base"].reasons
    assert not feed.snapshot(12).tape
    feed.trade(13, "new-anchor")
    assert "trade_window_warming" in feed.metrics(13)["cvd_base"].reasons


def test_count_overflow_invalidates_required_window_and_recovers_after_boundary():
    config = AnalyticsConfig(
        window_ns=10,
        retention_ns=100,
        max_trades=2,
        tape_limit=2,
        max_book_samples=1,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    feed.trade(1, "anchor")
    feed.trade(2, "lost")
    feed.trade(3, "kept")
    feed.trade(11, "latest")
    assert "trade_history_overflow" in feed.metrics(11)["cvd_base"].reasons
    feed.trade(13, "boundary")
    assert feed.metrics(13)["cvd_base"].value == D(2)
    feed.quote(20)
    feed.quote(25, bids=(("99", "2"),))
    feed.quote(30, bids=(("99", "3"),))
    assert "book_history_overflow" in feed.metrics(30)["event_ofi"].reasons


def test_time_retention_and_backward_query_do_not_leak_future_evidence():
    feed = Feed()
    feed.trade(1, "old")
    feed.trade(102, "future")
    assert [p.trade.dedup_key for p in feed.snapshot(102).tape] == ["future"]
    assert not feed.snapshot(1).tape
    assert feed.metrics(1)["cvd_base"].value is None
    original = feed.engine.state_hash
    restored = AnalyticsEngine(feed.streams, feed.config)
    restored.restore(feed.engine.checkpoint())
    assert restored.state_hash == original
    restored.reset()
    assert not restored.snapshot(
        feed.instrument.instrument_id, ClockReading("synthetic-clock", 102)
    ).tape
    incompatible = AnalyticsEngine(feed.streams, replace(feed.config, window_ns=11))
    with pytest.raises(ValueError, match="incompatible"):
        incompatible.restore(feed.engine.checkpoint())


def test_display_persistence_depletion_replenishment_have_timestamps():
    feed = Feed()
    feed.quote(1)
    feed.quote(6, bids=(("99", "3"),))
    feed.quote(11, bids=(("99", "2"),))
    snapshot = feed.snapshot(11)
    metrics = {m.name: m for m in snapshot.metrics}
    assert metrics["displayed_replenishment"].value == D(2)
    assert metrics["displayed_depletion"].value == D(1)
    assert metrics["displayed_persistence"].value == D(4)
    replenished = next(
        e for e in snapshot.display_evidence if e.kind == "displayed_replenishment"
    )
    assert (replenished.start_mono_ns, replenished.end_mono_ns) == (1, 6)
    assert len(replenished.references) == 2
    assert metrics["absorption_evidence_heuristic"].value is None


def test_unknown_access_resolves_only_actual_trade_evidence_and_block_stays_blocked():
    feed = Feed(instrument=synthetic_instrument(data_access="unknown"))
    feed.trade(1, "public")
    evidence = feed.metrics(1)["observed_cvd_base"].quality
    now = ClockReading("synthetic-clock", 1)
    assert evaluate_trades(evidence, feed.instrument, now).eligible
    assert not evaluate_trades(
        evidence, replace(feed.instrument, data_access="blocked"), now
    ).eligible
    assert not evaluate_trades(
        evidence, replace(feed.instrument, metadata_version="other"), now
    ).eligible


def test_native_quality_shape_and_decimal_validation_remain_unchanged():
    assert tuple(f.name for f in fields(Quality)) == (
        "lifecycle",
        "integrity",
        "transport_alive",
        "source_mode",
        "recorded_source_mode",
        "last_transport_mono_ns",
        "last_book_observation_mono_ns",
        "last_book_change_mono_ns",
        "last_healthy_observation_mono_ns",
        "clock_id",
        "coverage",
        "capabilities",
        "continuous_since_ordinal",
        "reason_codes",
        "capture_complete",
    )
    assert loads(dumps(Quality()), Quality) == Quality()
    with pytest.raises(ValueError):
        Level(D("1." + "1" * 40), D(1))


def test_ambiguous_book_keys_require_explicit_selection():
    instrument = synthetic_instrument()
    keys = tuple(SubscriptionKey.for_instrument(instrument, depth=d) for d in (5, 20))
    streams = tuple(
        StreamSpec(str(i), instrument, key, "v1", "cfg") for i, key in enumerate(keys)
    )
    engine = AnalyticsEngine(streams)
    snapshot = engine.snapshot(
        instrument.instrument_id, ClockReading("synthetic-clock", 1)
    )
    assert "ambiguous_book_key" in snapshot.reasons
    selected = engine.snapshot(
        instrument.instrument_id, ClockReading("synthetic-clock", 1), key=keys[1]
    )
    assert selected.key == keys[1]
    assert "ambiguous_book_key" not in selected.reasons


def test_real_m2_demo_raw_normalized_speed_seek_and_checkpoint(tmp_path):
    async def scenario():
        with await create_demo(tmp_path, duration_seconds=2) as capture:
            expected = None
            for mode in ("raw", "normalized"):
                for speed in (D(1), D(1000000)):
                    observer = AnalyticsEngine(capture.reader.manifest.streams)
                    session = ReplaySession(
                        capture.reader, mode=mode, observers=(observer,)
                    )
                    try:
                        session.step(5)
                        prefix = observer.checkpoint()
                        cursor = session.cursor
                        await session.run_to_end(speed)
                        final = observer.state_hash
                        if expected is None:
                            expected = final
                        assert final == expected
                        session.seek(cursor.ordinal, cursor.event_index)
                        assert observer.checkpoint() == prefix
                        await session.run_to_end()
                        assert observer.state_hash == final
                        restored = AnalyticsEngine(capture.reader.manifest.streams)
                        restored.restore(observer.checkpoint())
                        assert restored.state_hash == final
                        session.seek(0)
                        assert observer.delivery_index == 0
                    finally:
                        session.close()

    asyncio.run(scenario())


def test_display_heuristics_require_matching_public_executions():
    feed = Feed()
    feed.quote(1)
    feed.trade(1, "anchor", side="sell", price="99")
    feed.trade(5, "aggressive", side="sell", quantity="2", price="99")
    feed.quote(6, bids=(("99", "3"),))
    feed.quote(11, bids=(("99", "1"),))
    metrics = feed.metrics(11)
    assert metrics["absorption_evidence_heuristic"].value == D(2)
    assert metrics["pulling_evidence_heuristic"].value == D(2)


@pytest.mark.parametrize("side", ("bid", "ask"))
def test_tracking_cap_does_not_fabricate_depletion(side):
    config = AnalyticsConfig(
        window_ns=10,
        retention_ns=100,
        max_display_levels=1,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    feed.quote(1)
    feed.quote(
        11,
        **(
            {"bids": (("100", "3"), ("99", "1"))}
            if side == "bid"
            else {"asks": (("100", "3"), ("101", "2"))}
        ),
    )
    assert feed.metrics(11)["displayed_depletion"].value == 0
    assert not any(
        d.kind == "displayed_depletion" for d in feed.snapshot(11).display_evidence
    )
    feed.quote(21)
    assert feed.metrics(21)["displayed_depletion"].value == D(3)


def test_unaccepted_book_message_does_not_create_quote_flow():
    feed = Feed()
    feed.quote(1)
    feed.deliver(
        feed.event(BookUpdate("delta", (LevelChange("bid", D(99), D(8)),)), 11, "book")
    )
    assert feed.metrics(11)["event_ofi"].value == 0
    assert (
        feed.metrics(11)["signed_imbalance"].value
        == feed.metrics(1)["signed_imbalance"].value
    )


def test_source_epoch_reset_rewarms_trade_history():
    feed = Feed()
    feed.trade(1, "old")
    feed.trade(11, "past")
    feed.deliver(
        feed.event(Trade("new", "new", D(100), D(2), taker_side="buy"), 12, epoch=1)
    )
    assert [p.trade.dedup_key for p in feed.snapshot(12).tape] == ["new"]
    assert "trade_window_warming" in feed.metrics(12)["cvd_base"].reasons


def test_missing_trade_metrics_never_borrow_book_provenance():
    feed = Feed()
    feed.quote(1)
    result = feed.metrics(1)["trade_buy_notional"]
    assert result.asof is None
    assert result.quality is None
    assert result.references == ()


def test_stale_trade_channel_rewarms_instead_of_claiming_missing_interval():
    config = AnalyticsConfig(
        window_ns=10, retention_ns=100, policy=EligibilityPolicy(max_age_ns=5)
    )
    feed = Feed(config=config)
    feed.trade(1, "before-silence")
    feed.trade(12, "after-silence")
    metrics = feed.metrics(12)
    assert "trade_window_warming" in metrics["cvd_base"].reasons
    assert metrics["observed_cvd_base"].value == D(1)
    feed.deliver(feed.event(Control("heartbeat", "trade_channel_healthy"), 25))
    assert "trade_continuity_expired" in feed.metrics(25)["cvd_base"].reasons


def test_clock_regression_invalidates_trade_continuity():
    feed = Feed()
    feed.trade(5, "first")
    feed.trade(4, "regressed")
    result = feed.metrics(5)["cvd_base"]
    assert result.value is None
    assert "trade_clock_regression" in result.reasons


@pytest.mark.parametrize("kind", ["reset", "source_change", "rejection", "disconnect"])
def test_removed_book_on_reset_cannot_leave_old_quotes_or_future_anchors(kind):
    feed = Feed()
    feed.quote(1)
    feed.trade(1, "anchor")
    feed.trade(11, "independent", quantity="2")
    event = feed.event(Control(kind, "source_boundary"), 11, "book")
    feed.book = None
    feed.deliver(event)
    snapshot = feed.snapshot(11)
    metrics = {metric.name: metric for metric in snapshot.metrics}
    assert metrics["best_bid"].value is None
    assert metrics["best_bid"].asof is None
    assert "no_book" in metrics["best_bid"].reasons
    assert not snapshot.display_evidence
    assert metrics["cvd_base"].value == D(2)
    checkpoint = feed.engine.checkpoint()
    restored = AnalyticsEngine(feed.streams, feed.config)
    restored.restore(checkpoint)
    assert (
        restored.snapshot(
            feed.instrument.instrument_id, ClockReading("synthetic-clock", 11)
        )
        == snapshot
    )
    feed.quote(12)
    assert "book_window_warming" in feed.metrics(12)["event_ofi"].reasons


def test_scoped_gap_removes_absent_affected_book():
    feed = Feed()
    feed.quote(1)
    event = feed.event(
        Gap("scoped_capture_loss", affected_streams=("book",)), 11, "trades"
    )
    feed.book = None
    feed.deliver(event)
    assert feed.metrics(11)["best_bid"].value is None
    assert "no_book" in feed.metrics(11)["best_bid"].reasons


def test_expiry_inside_rolling_window_discards_pre_gap_executions():
    config = AnalyticsConfig(
        window_ns=10, retention_ns=100, policy=EligibilityPolicy(max_age_ns=5)
    )
    feed = Feed(config=config)
    feed.trade(5, "before-gap", quantity="2")
    feed.trade(11, "new-anchor", quantity="1")
    snapshot = feed.snapshot(11)
    metrics = {metric.name: metric for metric in snapshot.metrics}
    assert metrics["observed_trade_buy_notional"].value == D(100)
    assert metrics["observed_cvd_base"].value == D(1)
    assert metrics["executed_profile_base"].value == D(1)
    assert snapshot.profile[0].buy_base == D(1)
    assert [item.trade.dedup_key for item in snapshot.tape] == ["new-anchor"]
    assert "trade_window_warming" in metrics["cvd_base"].reasons
    restored = AnalyticsEngine(feed.streams, feed.config)
    restored.restore(feed.engine.checkpoint())
    assert (
        restored.snapshot(
            feed.instrument.instrument_id, ClockReading("synthetic-clock", 11)
        )
        == snapshot
    )


def test_quantity_unit_mismatch_discards_flow_and_restarts_warmup():
    feed = Feed()
    feed.trade(1, "old-anchor", quantity="3")
    feed.trade(5, "old-execution", quantity="2")
    invalid = feed.event(
        Trade(
            "invalid",
            "invalid",
            D(100),
            D(7),
            quantity_unit="contracts",
            taker_side="buy",
        ),
        6,
    )
    feed.deliver(invalid)
    assert "trade_quantity_unit_mismatch" in feed.metrics(6)["cvd_base"].reasons
    assert not feed.snapshot(6).tape
    feed.trade(11, "new-anchor")
    metrics = feed.metrics(11)
    assert metrics["cvd_base"].value is None
    assert "trade_window_warming" in metrics["cvd_base"].reasons
    assert metrics["observed_cvd_base"].value == D(1)
    assert feed.snapshot(11).profile[0].buy_base == D(1)
    feed.trade(21, "mature")
    assert feed.metrics(21)["cvd_base"].value == D(1)


def test_subscription_history_eviction_does_not_poison_live_flow():
    config = AnalyticsConfig(
        window_ns=10,
        retention_ns=100,
        max_trades=2,
        tape_limit=2,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    for timestamp in (1, 2, 3):
        feed.trade(timestamp, f"history-{timestamp}", quantity="9", history=True)
    feed.trade(4, "live-first")
    metrics = feed.metrics(4)
    assert metrics["observed_trade_buy_notional"].value == D(100)
    assert metrics["observed_cvd_base"].value == D(1)
    assert "trade_history_overflow" not in metrics["cvd_base"].reasons
    assert feed.snapshot(4).profile[0].buy_base == D(1)
    feed.trade(5, "live-second")
    assert feed.metrics(5)["observed_cvd_base"].value == D(2)
    feed.trade(6, "live-third")
    assert "trade_history_overflow" in feed.metrics(6)["observed_cvd_base"].reasons


def test_heuristics_keep_book_and_matched_trade_evidence_beyond_visible_tape():
    config = AnalyticsConfig(
        window_ns=10,
        retention_ns=100,
        tape_limit=1,
        max_references=1,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    feed.quote(1)
    feed.trade(1, "anchor", side="sell", price="99")
    absorption_trade = feed.trade(5, "absorbed", side="sell", quantity="2", price="99")
    replenishment = feed.quote(6, bids=(("99", "3"),))
    pulling_trade = feed.trade(
        8, "partly-executed", side="sell", quantity="1", price="99"
    )
    depletion = feed.quote(11, bids=(("99", "1"),))
    feed.trade(11, "visible-unrelated", side="unknown", price="100")
    snapshot = feed.snapshot(11)
    metrics = {metric.name: metric for metric in snapshot.metrics}
    assert [item.trade.dedup_key for item in snapshot.tape] == ["visible-unrelated"]
    for name, value, trade, book_event in (
        ("absorption_evidence_heuristic", D(2), absorption_trade, replenishment),
        ("pulling_evidence_heuristic", D(1), pulling_trade, depletion),
    ):
        metric = metrics[name]
        assert metric.value == value
        assert metric.quality == feed.book.quality
        assert metric.references == (book_event.ref(),)
        assert metric.trade_references == (trade.ref(),)
        assert metric.trade_evidence == metrics["cvd_base"].quality
        assert metric.trade_eligibility.eligible
        assert (
            loads(
                dumps(metric, decimal_policy=DERIVED_DECIMAL_POLICY),
                type(metric),
                decimal_policy=DERIVED_DECIMAL_POLICY,
            )
            == metric
        )
    feed.deliver(feed.event(Gap("trade_loss"), 12, "trades"))
    unavailable = feed.metrics(12)["absorption_evidence_heuristic"]
    assert unavailable.value is None
    assert not unavailable.trade_eligibility.eligible
    assert "trade_continuity_reset" in unavailable.trade_eligibility.reasons


def test_live_book_baseline_exposes_quotes_without_fabricated_history():
    feed = Feed()
    baseline_event = feed.quote(1)
    feed.engine = AnalyticsEngine(feed.streams, feed.config)
    feed.engine.initialize_books((feed.book,))
    snapshot = feed.snapshot(11)
    metrics = {metric.name: metric for metric in snapshot.metrics}
    assert metrics["mid"].value == D(100)
    assert metrics["mid"].asof == baseline_event.ref()
    assert metrics["mid"].quality == feed.book.quality
    assert metrics["event_ofi"].value is None
    assert "book_window_warming" in metrics["event_ofi"].reasons
    assert not snapshot.tape
    assert not snapshot.display_evidence
    assert feed.engine.delivery_index == 0
    restored = AnalyticsEngine(feed.streams, feed.config)
    restored.restore(feed.engine.checkpoint())
    assert (
        restored.snapshot(
            feed.instrument.instrument_id, ClockReading("synthetic-clock", 11)
        )
        == snapshot
    )
    with pytest.raises(ValueError, match="pristine"):
        feed.engine.initialize_books((feed.book,))
    feed.deliver(feed.event(Control("heartbeat", "ws_pong"), 11, "book"))
    assert "book_window_warming" in feed.metrics(11)["event_ofi"].reasons
    feed.quote(12, bids=(("99", "4"),))
    assert "book_window_warming" in feed.metrics(12)["event_ofi"].reasons
    assert not feed.snapshot(12).display_evidence
    feed.quote(22, bids=(("99", "6"),))
    assert feed.metrics(22)["event_ofi"].value == D(2)
    with pytest.raises(ValueError, match="pristine"):
        feed.engine.initialize_books(())


def test_live_book_baseline_preserves_invalid_quality_and_checks_metadata():
    feed = Feed()
    feed.quote(1)
    engine = AnalyticsEngine(feed.streams, feed.config)
    with pytest.raises(ValueError, match="metadata"):
        engine.initialize_books((replace(feed.book, metadata_version="other"),))
    invalid = replace(
        feed.book,
        quality=replace(
            feed.book.quality, integrity="invalid", lifecycle="resynchronizing"
        ),
    )
    engine.initialize_books((invalid,))
    metrics = {
        metric.name: metric
        for metric in engine.snapshot(
            feed.instrument.instrument_id, ClockReading("synthetic-clock", 1)
        ).metrics
    }
    assert metrics["best_bid"].value is None
    assert "integrity_invalid" in metrics["best_bid"].reasons
    engine.reset()
    engine.initialize_books((feed.book,))
    assert (
        engine.snapshot(
            feed.instrument.instrument_id, ClockReading("synthetic-clock", 1)
        ).asof
        == feed.book.asof
    )


def test_execution_evidence_matching_preserves_price_side_and_open_interval():
    feed = Feed()
    feed.quote(1, bids=(("99", "5"),), asks=(("101", "5"),))
    feed.trade(1, "at-start", side="sell", quantity="100", price="99")
    feed.trade(2, "wrong-price", side="sell", quantity="100", price="98")
    feed.trade(3, "wrong-side", side="buy", quantity="100", price="99")
    bid_match = feed.trade(4, "bid-match", side="sell", quantity="2", price="99")
    feed.quote(5, bids=(("99", "2"),), asks=(("101", "5"),))
    feed.trade(5, "ask-start", side="buy", quantity="100", price="101")
    feed.trade(6, "after-bid-interval", side="sell", quantity="100", price="99")
    ask_match = feed.trade(7, "ask-match", side="buy", quantity="1", price="101")
    feed.quote(8, bids=(("99", "2"),), asks=(("101", "7"),))
    feed.quote(11, bids=(("99", "2"),), asks=(("101", "7"),))
    metrics = feed.metrics(11)
    absorption = metrics["absorption_evidence_heuristic"]
    pulling = metrics["pulling_evidence_heuristic"]
    assert absorption.value == D(1)
    assert pulling.value == D(1)
    assert absorption.trade_references == (ask_match.ref(),)
    assert pulling.trade_references == (bid_match.ref(),)


def test_published_persistence_is_immutable_and_checkpoint_replays_exactly():
    feed = Feed()
    feed.quote(1, bids=(("99", "5"),))
    observed = feed.quote(4, bids=(("99", "5"),))
    prefix = feed.snapshot(4)
    persistence = next(
        item
        for item in prefix.display_evidence
        if item.kind == "displayed_persistence" and item.side == "bid"
    )
    checkpoint = feed.engine.checkpoint()
    future = feed.quote(8, bids=(("99", "2"),))
    final = feed.snapshot(8)
    final_hash = feed.engine.state_hash
    assert persistence.quantity == D(5)
    assert (persistence.start_mono_ns, persistence.end_mono_ns) == (1, 4)
    assert persistence.references[-1] == observed.ref()
    current = next(
        item
        for item in final.display_evidence
        if item.kind == "displayed_persistence" and item.side == "bid"
    )
    assert current.quantity == D(2)
    assert current.end_mono_ns == 8
    feed.engine.restore(checkpoint)
    assert feed.snapshot(4) == prefix
    feed.deliver(future)
    assert feed.snapshot(8) == final
    assert feed.engine.state_hash == final_hash


def test_depth_evidence_changes_with_book_but_not_query_freshness_or_restore():
    config = AnalyticsConfig(
        depth_bps=(D("200"),),
        window_ns=10,
        retention_ns=100,
        persistence_ns=2,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    asks = (("101", "3"), ("102", "4"))
    feed.quote(1, bids=(("99", "1"), ("98", "2")), asks=asks)
    first = feed.snapshot(1)
    first_metrics = {metric.name: metric for metric in first.metrics}
    assert first_metrics["depth_bid_200bps_base"].value == D(3)
    assert first_metrics["depth_ask_200bps_quote"].value == D(711)
    checkpoint = feed.engine.checkpoint()
    stale = feed.metrics(1002)["depth_bid_200bps_base"]
    assert stale.value is None
    assert not stale.eligibility.eligible
    assert feed.snapshot(1) == first
    feed.quote(4, bids=(("99", "5"), ("98", "2")), asks=asks)
    assert feed.metrics(4)["depth_bid_200bps_base"].value == D(7)
    feed.quote(8, bids=(("99", "9"),), asks=asks, full=False)
    partial = feed.metrics(8)["depth_bid_200bps_base"]
    assert partial.value is None
    assert "incomplete_depth_band" in partial.reasons
    feed.engine.restore(checkpoint)
    assert feed.snapshot(1) == first
