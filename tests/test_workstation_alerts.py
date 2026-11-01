"""Exact alert evidence, delivery-prefix commands, and bounded local retention."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D

import pytest

from src.workstation.alerts import (
    MAX_ALERT_COMMANDS,
    MAX_ALERT_EVENTS,
    MAX_ALERT_RULES,
    AlertEngine,
    AlertPutRequest,
    AlertRemoveRequest,
    AlertRule,
)
from src.workstation.analytics import AnalyticsConfig, AnalyticsEngine
from src.workstation.book import Book, Level
from src.workstation.costs import FeeSchedule
from src.workstation.events import (
    BookUpdate,
    Gap,
    LevelChange,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import synthetic_instrument
from src.workstation.quality import Capabilities, Coverage, EligibilityPolicy, Quality
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock


class Feed:
    """Native immutable books and actual public event admission, no mocked engine."""

    def __init__(self, *, instruments=None, exact=True, config=None):
        self.instruments = instruments or (synthetic_instrument(),)
        self.exact = exact
        self.keys = {
            (i.venue, channel): SubscriptionKey.for_instrument(i, channel=channel)
            for i in self.instruments
            for channel in ("book", "trades")
        }
        self.streams = tuple(
            StreamSpec(
                i.venue + ":" + channel,
                i,
                self.keys[i.venue, channel],
                "test-v1",
                "alerts-fixture",
            )
            for i in self.instruments
            for channel in ("book", "trades")
        )
        self.config = config or AnalyticsConfig(
            window_ns=10,
            retention_ns=100,
            persistence_ns=2,
            policy=EligibilityPolicy(max_age_ns=100, max_asof_skew_ns=50),
        )
        self.analytics = AnalyticsEngine(self.streams, self.config)
        self.alerts = AlertEngine(self.streams, self.analytics)
        self.clock = ReplayClock()
        self.books = {}
        self.ordinal = 0
        self.records = []

    def key(self, venue=None, channel="book"):
        return self.keys[venue or self.instruments[0].venue, channel]

    def event(self, payload, time, channel="book", venue=None, **options):
        venue = venue or self.instruments[0].venue
        instrument = next(i for i in self.instruments if i.venue == venue)
        self.ordinal += 1
        return synthetic_envelope(
            instrument,
            payload,
            ordinal=self.ordinal,
            mono_ns=time,
            key=self.key(venue, channel),
            stream_id=venue + ":" + channel,
            source_mode="ws_delta" if self.exact else "ws_snapshot",
            **options,
        )

    def deliver(self, *events):
        batch = RecordedBatch(len(self.records) + 1, events)
        books = tuple(self.books.values())
        self.records.append((batch, books))
        for event in events:
            self.clock.advance(event)
        self.analytics.on_batch(batch, books, self.clock)
        self.alerts.on_batch(batch, books, self.clock)
        return batch

    def quote(
        self,
        time,
        *,
        bid="99",
        ask="101",
        bid_qty="1",
        ask_qty="1",
        venue=None,
        full=True,
        quality=None,
        **options,
    ):
        venue = venue or self.instruments[0].venue
        instrument = next(i for i in self.instruments if i.venue == venue)
        key = self.key(venue)
        event = self.event(
            BookUpdate(
                "delta" if self.exact and key in self.books else "snapshot",
                (
                    LevelChange("bid", D(bid), D(bid_qty)),
                    LevelChange("ask", D(ask), D(ask_qty)),
                ),
            ),
            time,
            venue=venue,
            **options,
        )
        current_quality = Quality(
            lifecycle="live",
            integrity="valid",
            transport_alive=True,
            source_mode=event.source_mode,
            last_book_observation_mono_ns=time,
            last_healthy_observation_mono_ns=time,
            clock_id=event.clock_id,
            continuous_since_ordinal=1,
            coverage=Coverage(
                retained_bid_levels=1,
                retained_ask_levels=1,
                worst_bid=D(bid),
                worst_ask=D(ask),
                full_depth=full,
            ),
            capabilities=Capabilities(deltas=self.exact, exact_event_flow=self.exact),
        )
        if quality:
            current_quality = replace(current_quality, **quality)
        self.books[key] = Book(
            instrument.instrument_id,
            instrument.metadata_version,
            key,
            event.segment_id,
            event.connection_epoch,
            self.ordinal,
            event.ref(),
            (Level(D(bid), D(bid_qty)),),
            (Level(D(ask), D(ask_qty)),),
            current_quality,
        )
        return self.deliver(event)

    def trade(self, time, trade_id, quantity="1", *, history=False, venue=None):
        return self.deliver(
            self.event(
                Trade(
                    trade_id,
                    trade_id,
                    D(100),
                    D(quantity),
                    taker_side="buy",
                    is_subscription_history=history,
                ),
                time,
                "trades",
                venue,
            )
        )

    def rewind(self, count):
        self.analytics.reset()
        self.alerts.reset()
        self.clock.reset()
        for batch, books in self.records[:count]:
            for event in batch.events:
                self.clock.advance(event)
            self.analytics.on_batch(batch, books, self.clock)
            self.alerts.on_batch(batch, books, self.clock)

    def rule(self, kind="spread_shock", *, rule_id="r", **options):
        instrument = self.instruments[0]
        unit = (
            "bps"
            if kind == "spread_shock"
            else "ratio"
            if kind == "best_quote_imbalance"
            else str(instrument.base.asset_id)
            if kind == "depth_shock"
            else str(instrument.quote.asset_id)
            if kind == "net_cost"
            else instrument.quantity_unit + ":" + str(instrument.instrument_id)
        )
        values = dict(
            rule_id=rule_id,
            key=self.key(),
            kind=kind,
            direction="above",
            threshold=D(100),
            rearm_threshold=D(50),
            unit=unit,
            window_ns=10
            if kind in ("event_ofi", "persistent_displayed_liquidity")
            else 0,
        )
        values.update(options)
        return AlertRule(**values)

    def put(self, rule):
        return self.alerts.put_rule(rule, delivery_index=self.alerts.delivery_index)

    @property
    def evaluation(self):
        return self.alerts.snapshot().evaluations[0]

    @property
    def events(self):
        return self.alerts.snapshot().events


def test_above_hysteresis_rearms_during_cooldown_without_repeated_fires():
    feed = Feed()
    feed.put(feed.rule(cooldown_ns=10))
    feed.quote(1)
    assert len(feed.events) == 1
    assert feed.events[0].evaluation.value == 200
    assert feed.events[0].evaluation.metric.name == "spread_bps"
    assert feed.events[0].evaluation.book_evidence.best_bid == Level(D(99), D(1))
    assert feed.events[0].evaluation.book_evidence.best_ask == Level(D(101), D(1))
    feed.quote(2)
    assert len(feed.events) == 1
    feed.quote(3, bid="99.9", ask="100.1")
    assert feed.evaluation.armed
    assert feed.evaluation.cooldown_remaining_ns == 8
    feed.quote(5)
    assert feed.evaluation.condition_met
    assert len(feed.events) == 1
    feed.quote(11)
    assert len(feed.events) == 2
    assert feed.events[-1].evaluation.now.mono_ns == 11
    assert not feed.evaluation.armed


def test_below_depth_threshold_and_direction_aware_rearm():
    feed = Feed()
    feed.put(
        feed.rule(
            "depth_shock",
            direction="below",
            threshold=D("1.5"),
            rearm_threshold=D(2),
            cooldown_ns=10,
        )
    )
    feed.quote(1, bid="99.99", ask="100.01")
    assert feed.events[0].evaluation.value == 1
    assert feed.events[0].evaluation.metric.name == "depth_bid_10bps_base"
    feed.quote(2, bid="99.99", ask="100.01", bid_qty="3")
    assert feed.evaluation.armed
    feed.quote(3, bid="99.99", ask="100.01")
    assert len(feed.events) == 1
    feed.quote(11, bid="99.99", ask="100.01")
    assert len(feed.events) == 2


def test_invalid_data_does_not_rearm_a_latched_rule():
    feed = Feed()
    feed.put(feed.rule())
    feed.quote(1)
    feed.quote(2, bid="99.9", ask="100.1", quality={"integrity": "invalid"})
    assert feed.evaluation.value is None
    assert "integrity_invalid" in feed.evaluation.eligibility.reasons
    feed.quote(3)
    assert len(feed.events) == 1
    assert not feed.evaluation.armed


@pytest.mark.parametrize(
    "quality,reason",
    [
        ({"capture_complete": False}, "capture_incomplete"),
        ({"transport_alive": False}, "transport_dead"),
        ({"continuous_since_ordinal": None}, "no_event_continuity"),
    ],
)
def test_incomplete_or_invalid_flow_suppresses_numeric_signals(quality, reason):
    feed = Feed()
    feed.put(feed.rule("event_ofi", threshold=D(1), rearm_threshold=D(0)))
    feed.quote(1)
    feed.quote(11, bid_qty="3", quality=quality)
    assert not feed.events
    assert feed.evaluation.value is None
    assert reason in feed.evaluation.eligibility.reasons


def test_stale_book_and_partial_depth_are_unavailable_not_zero():
    feed = Feed()
    feed.put(
        feed.rule(
            "depth_shock", direction="below", threshold=D(2), rearm_threshold=D(3)
        )
    )
    feed.quote(1, bid="99.99", ask="100.01", full=False)
    assert feed.evaluation.value is None
    assert "incomplete_depth_band" in feed.evaluation.eligibility.reasons
    feed.trade(102, "later")
    assert "stale" in feed.evaluation.eligibility.reasons
    assert not feed.events


def test_exact_event_ofi_uses_native_formula_and_snapshot_flow_is_not_substituted():
    feed = Feed()
    feed.put(feed.rule("event_ofi", threshold=D(1), rearm_threshold=D(0)))
    feed.quote(1)
    assert "book_window_warming" in feed.evaluation.eligibility.reasons
    feed.quote(11, bid_qty="3")
    evaluation = feed.events[0].evaluation
    assert evaluation.value == 2
    assert evaluation.metric.name == "event_ofi"
    assert evaluation.metric.formula_version == "cont-best-quote-indicators-v1"
    assert evaluation.window.start_mono_ns == 1
    assert evaluation.references == (feed.books[feed.key()].asof,)
    snapshot_feed = Feed(exact=False)
    snapshot_feed.put(
        snapshot_feed.rule("event_ofi", threshold=D(1), rearm_threshold=D(0))
    )
    snapshot_feed.quote(1)
    snapshot_feed.quote(11, bid_qty="3")
    assert not snapshot_feed.events
    assert "missing_exact_event_flow" in snapshot_feed.evaluation.eligibility.reasons
    assert snapshot_feed.evaluation.value is None


def test_best_quote_imbalance_is_explicit_and_not_a_depth_band_metric():
    feed = Feed()
    feed.put(
        feed.rule("best_quote_imbalance", threshold=D("0.4"), rearm_threshold=D("0.1"))
    )
    feed.quote(1, bid_qty="3")
    evaluation = feed.events[0].evaluation
    assert evaluation.value == D("0.5")
    assert evaluation.rule.kind == "best_quote_imbalance"
    assert evaluation.metric.name == "signed_imbalance"
    assert evaluation.book_evidence.best_bid.quantity == 3
    assert evaluation.book_evidence.best_ask.quantity == 1
    assert evaluation.rule.unit == "ratio"


def test_displayed_persistence_requires_observed_duration_and_retains_level_evidence():
    feed = Feed()
    feed.put(
        feed.rule(
            "persistent_displayed_liquidity", threshold=D("1.5"), rearm_threshold=D(1)
        )
    )
    feed.quote(1)
    assert not feed.events
    feed.quote(11)
    evaluation = feed.events[0].evaluation
    assert evaluation.value == 2
    assert evaluation.persistence_ns == 2
    assert {d.side for d in evaluation.displayed} == {"bid", "ask"}
    assert all(d.kind == "displayed_persistence" for d in evaluation.displayed)
    assert all(
        (d.start_mono_ns, d.end_mono_ns) == (1, 11) for d in evaluation.displayed
    )
    assert all(len(d.references) == 2 for d in evaluation.displayed)


def test_large_prints_use_admitted_batch_and_never_refire_duplicates_or_history():
    feed = Feed()
    feed.put(feed.rule("large_executed_print", threshold=D(5), rearm_threshold=D(2)))
    feed.trade(1, "large", "8")
    assert len(feed.events) == 1
    assert feed.events[0].evaluation.prints[0].trade.quantity == 8
    assert feed.events[0].evaluation.trade_evidence.healthy
    feed.trade(2, "small", "1")
    assert feed.evaluation.armed
    feed.trade(3, "large", "8")
    assert len(feed.events) == 1
    assert "no_new_executed_print" in feed.evaluation.eligibility.reasons
    feed.trade(4, "history", "8", history=True)
    assert len(feed.events) == 1
    feed.trade(5, "genuine", "8")
    assert len(feed.events) == 2
    assert feed.events[-1].evaluation.references[0].receive_mono_ns == 5


def test_multiple_prints_in_atomic_batch_share_cooldown_and_respect_rearm_order():
    feed = Feed()
    feed.put(
        feed.rule(
            "large_executed_print", threshold=D(5), rearm_threshold=D(2), cooldown_ns=10
        )
    )
    events = tuple(
        feed.event(Trade(str(i), str(i), D(100), D(q), taker_side="buy"), 1, "trades")
        for i, q in enumerate((8, 1, 9))
    )
    feed.deliver(*events)
    assert len(feed.events) == 1
    assert feed.evaluation.value == 9
    assert feed.evaluation.armed
    assert feed.evaluation.cooldown_remaining_ns == 10


def test_admitted_batch_overflow_suppresses_even_a_large_retained_print():
    config = AnalyticsConfig(
        window_ns=10,
        retention_ns=100,
        max_trades=1,
        tape_limit=1,
        persistence_ns=2,
        policy=EligibilityPolicy(max_age_ns=100),
    )
    feed = Feed(config=config)
    feed.put(feed.rule("large_executed_print", threshold=D(5), rearm_threshold=D(2)))
    events = tuple(
        feed.event(Trade(str(i), str(i), D(100), D(8), taker_side="buy"), 1, "trades")
        for i in range(2)
    )
    feed.deliver(*events)
    assert not feed.events
    assert "admitted_tape_incomplete" in feed.evaluation.eligibility.reasons


def test_trade_gap_cannot_reuse_pre_gap_print_evidence():
    feed = Feed()
    feed.put(feed.rule("large_executed_print", threshold=D(5), rearm_threshold=D(2)))
    feed.trade(1, "small")
    feed.deliver(
        feed.event(
            Gap("capture_queue_overflow", affected_streams=("synthetic:trades",)),
            2,
            "trades",
        )
    )
    assert not feed.events
    assert not feed.evaluation.eligibility.eligible
    assert feed.evaluation.trade_evidence is not None
    assert not feed.evaluation.trade_evidence.healthy


def pair_feed(*, incompatible=False):
    return Feed(
        instruments=(
            synthetic_instrument(venue="a"),
            synthetic_instrument(venue="b", base="ETH" if incompatible else "BTC"),
        )
    )


def pair_rule(feed, **options):
    fee = FeeSchedule(D("0.001"), provenance="fixture explicit fee", asof_wall_ns=1)
    values = dict(
        sell_key=feed.key("b"),
        base_quantity=D(2),
        buy_fees=fee,
        sell_fees=fee,
        threshold=D(5),
        rearm_threshold=D(0),
    )
    values.update(options)
    return feed.rule("net_cost", **values)


def test_quantity_aware_net_cost_is_diagnostic_and_keeps_exact_cost_evidence():
    feed = pair_feed()
    feed.put(pair_rule(feed))
    feed.quote(1, venue="a", bid="99", ask="100", ask_qty="2")
    feed.quote(2, venue="b", bid="103", ask="104", bid_qty="2")
    evaluation = feed.events[0].evaluation
    assert evaluation.value == D("5.594")
    assert evaluation.pair.buy.notional_quote == 200
    assert evaluation.pair.sell.notional_quote == 206
    assert evaluation.pair.buy_fee == D("0.2")
    assert evaluation.pair.sell_fee == D("0.206")
    assert evaluation.pair.feasible_base_quantity == 2
    assert not evaluation.pair.execution_eligible
    assert "unknown_inventory_assumed_prefunded" in evaluation.pair.assumptions
    assert "no_market_impact" in evaluation.pair.assumptions
    assert len(evaluation.references) == 2


def test_unknown_fees_never_become_zero_cost():
    feed = pair_feed()
    feed.put(pair_rule(feed, buy_fees=FeeSchedule()))
    feed.quote(1, venue="a", ask="100", ask_qty="2")
    feed.quote(2, venue="b", bid="103", ask="104", bid_qty="2")
    assert not feed.events
    assert feed.evaluation.value is None
    assert feed.evaluation.pair.net is None
    assert any("fee" in reason for reason in feed.evaluation.eligibility.reasons)


def test_incompatible_venue_pair_cannot_create_a_net_cost_signal():
    feed = pair_feed(incompatible=True)
    feed.put(pair_rule(feed))
    feed.quote(1, venue="a", ask="100", ask_qty="2")
    feed.quote(2, venue="b", bid="103", ask="104", bid_qty="2")
    assert not feed.events
    assert "incompatible_instruments" in feed.evaluation.eligibility.reasons


def test_partial_quantity_net_cost_is_suppressed_not_scaled_to_a_profitable_subset():
    feed = pair_feed()
    feed.put(pair_rule(feed, threshold=D(1)))
    feed.quote(1, venue="a", ask="100", ask_qty="1")
    feed.quote(2, venue="b", bid="103", ask="104", bid_qty="2")
    assert not feed.events
    assert "requested_quantity_unfilled" in feed.evaluation.eligibility.reasons
    assert feed.evaluation.pair.feasible_base_quantity == 1


def test_rule_activation_removal_and_replacement_reconstruct_only_the_delivery_prefix():
    feed = Feed()
    feed.quote(1)
    feed.put(feed.rule())  # Active after delivery 1, never against delivery 1.
    assert not feed.events
    feed.quote(2)
    feed.put(feed.rule(threshold=D(300), rearm_threshold=D(250)))
    feed.quote(3)
    feed.alerts.remove_rule("r", delivery_index=3)
    feed.quote(4)
    original = feed.alerts.snapshot()
    assert original.rules == ()
    assert len(original.events) == 1
    feed.rewind(0)
    assert feed.alerts.snapshot().rules == ()
    assert feed.events == ()
    with pytest.raises(ValueError, match="fork_required"):
        feed.put(feed.rule(rule_id="future-edit"))
    feed.rewind(1)
    assert feed.alerts.snapshot().rules[0].threshold == 100
    assert feed.events == ()
    feed.rewind(2)
    assert feed.alerts.snapshot().rules[0].threshold == 300
    assert len(feed.events) == 1
    feed.rewind(4)
    assert feed.alerts.snapshot() == original


def test_checkpoint_restore_preserves_later_intent_but_not_future_fires():
    feed = Feed()
    feed.quote(1)
    state = feed.alerts.checkpoint()
    feed.quote(2)
    feed.put(feed.rule())
    feed.quote(3)
    assert len(feed.events) == 1
    feed.alerts.restore(state)
    assert feed.alerts.snapshot().rules == ()
    assert feed.events == ()
    feed.rewind(3)
    assert len(feed.events) == 1
    assert feed.events[0].evaluation.delivery_index == 3
    roundtrip = feed.alerts.checkpoint()
    before = feed.alerts.snapshot()
    fresh = AlertEngine(feed.streams, feed.analytics)
    fresh.restore(roundtrip)
    assert fresh.snapshot() == before


def test_live_replacement_keeps_fire_history_and_cooldown_but_invalidates_old_inputs():
    feed = Feed()
    feed.put(feed.rule(cooldown_ns=10))
    feed.quote(1)
    original = feed.events
    feed.alerts.replace_streams((), AnalyticsEngine((), feed.config))
    assert feed.events == original
    assert feed.evaluation.eligibility.reasons == ("instrument_not_observed",)
    assert not feed.evaluation.armed
    assert feed.evaluation.cooldown_remaining_ns == 10
    feed.analytics = AnalyticsEngine(feed.streams, feed.config)
    feed.alerts.replace_streams(feed.streams, feed.analytics)
    assert feed.evaluation.value is None
    assert feed.evaluation.eligibility.reasons == ("awaiting_observation",)
    feed.quote(2, bid="99.9", ask="100.1")
    feed.quote(3)
    assert len(feed.events) == 1
    feed.quote(11)
    assert len(feed.events) == 2
    assert feed.events[0] == original[0]


def test_clock_discontinuity_does_not_reuse_a_different_clocks_cooldown():
    feed = Feed()
    feed.put(feed.rule(cooldown_ns=1000))
    feed.quote(100)
    feed.quote(1, clock_id="new-clock")
    assert len(feed.events) == 1
    assert feed.evaluation.value is None
    assert "clock_discontinuity" in feed.evaluation.eligibility.reasons
    feed.quote(2, clock_id="new-clock")
    assert len(feed.events) == 2
    assert feed.events[-1].evaluation.now.clock_id == "new-clock"


def test_bounded_events_report_overflow_and_replay_reproduces_the_retained_suffix():
    feed = Feed()
    feed.put(feed.rule())
    for index in range(MAX_ALERT_EVENTS + 3):
        feed.quote(2 * index + 1)
        feed.quote(2 * index + 2, bid="99.9", ask="100.1")
    before = feed.alerts.snapshot()
    assert len(before.events) == MAX_ALERT_EVENTS
    assert before.dropped_events == 3
    assert before.events[0].event_id == 4
    assert "event_history_truncated" in before.reasons
    feed.rewind(len(feed.records))
    assert feed.alerts.snapshot() == before


def test_rule_and_command_bounds_reject_without_losing_configured_intent():
    feed = Feed()
    for index in range(MAX_ALERT_RULES):
        feed.put(feed.rule(rule_id=str(index)))
    before = feed.alerts.snapshot()
    with pytest.raises(ValueError, match="alert_rule_limit"):
        feed.put(feed.rule(rule_id="overflow"))
    assert feed.alerts.snapshot() == before
    feed = Feed()
    for _ in range(MAX_ALERT_COMMANDS):
        feed.put(feed.rule())
    with pytest.raises(ValueError, match="alert_command_history_limit"):
        feed.put(feed.rule())
    assert len(feed.alerts.snapshot().rules) == 1


def test_unsupported_window_and_depth_band_remain_explicitly_unavailable():
    feed = Feed()
    feed.put(feed.rule("event_ofi", window_ns=20, threshold=D(1), rearm_threshold=D(0)))
    feed.quote(1)
    assert "unsupported_window" in feed.evaluation.eligibility.reasons
    feed.put(feed.rule("depth_shock", depth_bps=D(7)))
    feed.quote(2)
    assert "unsupported_depth_band" in feed.evaluation.eligibility.reasons
    assert not feed.events


def test_request_validation_is_exact_immutable_and_direction_aware():
    feed = Feed()
    rule = feed.rule()
    assert AlertPutRequest(rule).rule is rule
    assert AlertRemoveRequest("r").rule_id == "r"
    with pytest.raises(FrozenInstanceError):
        rule.threshold = D(0)
    for threshold in (1.5, True, "NaN"):
        with pytest.raises(ValueError):
            replace(rule, threshold=threshold)
    with pytest.raises(ValueError):
        replace(rule, rearm_threshold=rule.threshold)
    with pytest.raises(ValueError):
        replace(rule, direction="below")
    with pytest.raises(ValueError, match="threshold_unit_mismatch"):
        feed.put(replace(rule, unit="USD"))
    with pytest.raises(ValueError, match="cutoff"):
        feed.alerts.put_rule(rule, delivery_index=1)
    with pytest.raises(ValueError):
        AlertPutRequest({"rule": "not a domain record"})


def test_empty_configuration_has_no_market_projection_or_fabricated_events():
    feed = Feed()
    feed.quote(1)
    feed.trade(2, "large", "10000")
    snapshot = feed.alerts.snapshot()
    assert snapshot.rules == snapshot.evaluations == snapshot.events == ()
    assert snapshot.delivery_index == 2
    assert snapshot.now.mono_ns == 2


def test_same_clock_analytics_replacement_cannot_refire_a_previously_seen_trade():
    feed = Feed()
    feed.put(feed.rule("large_executed_print", threshold=D(5), rearm_threshold=D(2)))
    feed.trade(1, "old-large", "8")
    feed.trade(2, "small", "1")
    assert feed.evaluation.armed
    feed.analytics = AnalyticsEngine(feed.streams, feed.config)
    feed.alerts.replace_streams(feed.streams, feed.analytics)
    feed.trade(3, "old-large", "8")
    assert len(feed.events) == 1
    assert "no_new_executed_print" in feed.evaluation.eligibility.reasons
    feed.trade(4, "new-large", "8")
    assert len(feed.events) == 2


def test_trade_identity_retention_exhaustion_is_explicit_and_never_evicts_into_refires():
    from src.workstation.alerts import MAX_ALERT_PRINT_IDENTITIES

    feed = Feed()
    feed.put(feed.rule("large_executed_print", threshold=D(5), rearm_threshold=D(2)))
    events = tuple(
        feed.event(Trade(str(i), str(i), D(100), D(1), taker_side="buy"), 1, "trades")
        for i in range(MAX_ALERT_PRINT_IDENTITIES)
    )
    feed.deliver(*events)
    assert feed.evaluation.eligibility.eligible
    feed.trade(2, "beyond-bound", "8")
    assert not feed.events
    assert "trade_dedup_history_incomplete" in feed.evaluation.eligibility.reasons
    assert "trade_dedup_history_incomplete" in feed.alerts.snapshot().reasons
    feed.analytics = AnalyticsEngine(feed.streams, feed.config)
    feed.alerts.replace_streams(feed.streams, feed.analytics)
    feed.trade(3, "0", "8")
    assert not feed.events
    assert "trade_dedup_history_incomplete" in feed.evaluation.eligibility.reasons


def test_same_id_rule_updates_preserve_both_latch_and_original_cooldown():
    feed = Feed()
    rule = feed.rule(cooldown_ns=10)
    feed.put(rule)
    feed.quote(1)
    updated = replace(rule, threshold=D(150))
    feed.put(updated)
    assert not feed.evaluation.armed
    assert feed.evaluation.cooldown_remaining_ns == 10
    feed.quote(2)
    assert len(feed.events) == 1
    feed.quote(3, bid="99.9", ask="100.1")
    assert feed.evaluation.armed
    feed.put(updated)
    assert feed.evaluation.armed
    assert feed.evaluation.cooldown_remaining_ns == 8
    feed.quote(4)
    assert len(feed.events) == 1
    feed.quote(11)
    assert len(feed.events) == 2
    original = feed.alerts.snapshot()
    feed.rewind(len(feed.records))
    assert feed.alerts.snapshot() == original


@pytest.mark.parametrize("count,eligible", [(200, True), (201, False)])
def test_event_ofi_nested_reference_bound_is_enforced_before_retention(count, eligible):
    config = AnalyticsConfig(
        window_ns=count,
        retention_ns=1000,
        max_references=400,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    feed.put(
        feed.rule("event_ofi", window_ns=count, threshold=D(1), rearm_threshold=D(0))
    )
    for time in range(1, count + 2):
        feed.quote(time, bid_qty=str(time))
    source = feed.analytics.snapshot(
        str(feed.instruments[0].instrument_id), feed.clock.reading, key=feed.key()
    )
    metric = next(m for m in source.metrics if m.name == "event_ofi")
    assert len(metric.references) == count
    assert feed.evaluation.eligibility.eligible is eligible
    if eligible:
        assert len(feed.evaluation.metric.references) == count
        assert len(feed.events) == 1
    else:
        assert feed.evaluation.metric is None
        assert feed.evaluation.value is None
        assert "metric_evidence_limit" in feed.evaluation.eligibility.reasons
        assert not feed.events


def test_unavailable_metric_does_not_retain_an_oversized_nested_history():
    config = AnalyticsConfig(
        window_ns=500,
        retention_ns=1000,
        max_references=400,
        policy=EligibilityPolicy(max_age_ns=1000),
    )
    feed = Feed(config=config)
    feed.put(
        feed.rule("event_ofi", window_ns=500, threshold=D(1), rearm_threshold=D(0))
    )
    for time in range(1, 203):
        feed.quote(time, bid_qty=str(time))
    assert feed.evaluation.value is None
    assert feed.evaluation.metric is None
    assert "book_window_warming" in feed.evaluation.eligibility.reasons
    assert "metric_evidence_limit" in feed.evaluation.eligibility.reasons


def test_nested_trade_and_display_references_cannot_bypass_record_bounds():
    from src.workstation.alerts import MAX_ALERT_EVIDENCE

    feed = Feed()
    feed.put(feed.rule())
    feed.quote(1)
    evaluation = feed.events[0].evaluation
    oversized = evaluation.references * (MAX_ALERT_EVIDENCE + 1)
    with pytest.raises(ValueError, match="metric_evidence_limit"):
        replace(
            evaluation, metric=replace(evaluation.metric, trade_references=oversized)
        )
    feed = Feed()
    feed.put(
        feed.rule(
            "persistent_displayed_liquidity", threshold=D("1.5"), rearm_threshold=D(1)
        )
    )
    feed.quote(1)
    feed.quote(11)
    evaluation = feed.events[0].evaluation
    displayed = replace(evaluation.displayed[0], references=oversized)
    with pytest.raises(ValueError, match="display_evidence_limit"):
        replace(evaluation, displayed=(displayed,))


def test_net_cost_walk_beyond_nested_evidence_bound_is_explicitly_unavailable():
    from src.workstation.alerts import MAX_ALERT_EVIDENCE

    feed = pair_feed()
    quantity = MAX_ALERT_EVIDENCE + 1
    feed.put(pair_rule(feed, base_quantity=D(quantity)))
    feed.quote(1, venue="a", ask="100")
    feed.quote(2, venue="b", bid="104", ask="105", bid_qty=str(quantity))
    levels = tuple(Level(D(100) + D(i) / 100, D(1)) for i in range(quantity))
    event = feed.event(
        BookUpdate(
            "snapshot",
            tuple(LevelChange("ask", level.price, level.quantity) for level in levels),
        ),
        3,
        venue="a",
    )
    book = feed.books[feed.key("a")]
    feed.books[feed.key("a")] = replace(
        book,
        asks=levels,
        asof=event.ref(),
        revision=book.revision + 1,
        quality=replace(
            book.quality,
            last_book_observation_mono_ns=3,
            last_healthy_observation_mono_ns=3,
            coverage=replace(
                book.quality.coverage,
                retained_ask_levels=quantity,
                worst_ask=levels[-1].price,
            ),
        ),
    )
    feed.deliver(event)
    assert not feed.events
    assert feed.evaluation.value is None
    assert feed.evaluation.pair is None
    assert "pair_evidence_limit" in feed.evaluation.eligibility.reasons
