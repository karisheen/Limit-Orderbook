"""Causal research evidence, exact financial exclusions and durable all-attempt history."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D, localcontext

import pytest

from src.workstation.book import Book, Level
from src.workstation.costs import FeeSchedule
from src.workstation.demo import create_demo
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.events import (
    BookUpdate,
    Gap,
    LevelChange,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import synthetic_instrument
from src.workstation.precision import DECIMAL_CONTEXT
from src.workstation.quality import Capabilities, Coverage, EligibilityPolicy, Quality
from src.workstation.recording import RecordedBatch
from src.workstation.recording import StreamSpec
from src.workstation.replay import ReplayClock, ReplaySourceDescriptor
from src.workstation.research import (
    HYPOTHESES,
    SECOND,
    ResearchConfig,
    ResearchScenario,
    ResearchStore,
    _ResearchObserver,
    _report,
    _split,
    run_research,
)


class ResearchFeed:
    """Real native book/analytics inputs; no mocked metrics or cost estimates."""

    def __init__(self, *, exact=True, config=None, fee=D(0)):
        self.instrument = synthetic_instrument()
        self.keys = {
            channel: SubscriptionKey.for_instrument(self.instrument, channel=channel)
            for channel in ("book", "trades")
        }
        self.streams = tuple(
            StreamSpec(channel, self.instrument, key, "test-v1", "test-config")
            for channel, key in self.keys.items()
        )
        self.config = config or ResearchConfig(
            feature_window_ns=2 * SECOND,
            depth_band_bps=D(250),
            horizons_ns=(SECOND,),
            thresholds=(D(0), D("0.5")),
            embargo_ns=2 * SECOND,
            min_samples=3,
            base_quantity=D(1),
            fees=(
                (
                    str(self.instrument.instrument_id),
                    FeeSchedule(fee, provenance="synthetic-test", asof_wall_ns=1),
                ),
            )
            if fee is not None
            else (),
            scenarios=(ResearchScenario(),),
            policy=EligibilityPolicy(max_age_ns=100 * SECOND),
        )
        self.observer = _ResearchObserver(self.streams, self.config)
        self.clock = ReplayClock()
        self.ordinal = 0
        self.book = None
        self.exact = exact

    def event(self, payload, seconds, channel="book"):
        self.ordinal += 1
        return synthetic_envelope(
            self.instrument,
            payload,
            ordinal=self.ordinal,
            mono_ns=seconds * SECOND,
            key=self.keys[channel],
            stream_id=channel,
            source_mode="ws_delta" if self.exact else "ws_snapshot",
        )

    def deliver(self, event):
        self.clock.advance(event)
        self.observer.on_batch(
            RecordedBatch(self.ordinal, (event,)),
            (self.book,) if self.book else (),
            self.clock,
        )

    def quote(self, seconds, *, mid=100, full=True, bid_size=9, ask_size=1):
        bids = ((D(mid) - 1, D(1)), (D(mid) - 2, D(bid_size)))
        asks = ((D(mid) + 1, D(1)), (D(mid) + 2, D(ask_size)))
        changes = tuple(
            LevelChange(side, price, size)
            for side, levels in (("bid", bids), ("ask", asks))
            for price, size in levels
        )
        event = self.event(
            BookUpdate(
                "snapshot" if self.book is None or not self.exact else "delta", changes
            ),
            seconds,
        )
        self.book = Book(
            self.instrument.instrument_id,
            self.instrument.metadata_version,
            self.keys["book"],
            event.segment_id,
            event.connection_epoch,
            self.ordinal,
            event.ref(),
            tuple(Level(*v) for v in bids),
            tuple(Level(*v) for v in asks),
            Quality(
                lifecycle="live",
                integrity="valid",
                transport_alive=True,
                source_mode=event.source_mode,
                last_book_observation_mono_ns=seconds * SECOND,
                last_healthy_observation_mono_ns=seconds * SECOND,
                clock_id=event.clock_id,
                coverage=Coverage(
                    retained_bid_levels=2,
                    retained_ask_levels=2,
                    worst_bid=bids[-1][0],
                    worst_ask=asks[-1][0],
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

    def trade(self, seconds, side="buy", *, history=False, identity=None):
        identity = identity or str(self.ordinal + 1)
        event = self.event(
            Trade(
                identity,
                identity,
                D(100),
                D(1),
                taker_side=side,
                is_subscription_history=history,
            ),
            seconds,
            "trades",
        )
        self.deliver(event)

    def gap(self, seconds):
        event = self.event(Gap("fixture_gap"), seconds)
        self.book = replace(
            self.book,
            quality=replace(
                self.book.quality, integrity="invalid", transport_alive=False
            ),
        )
        self.deliver(event)

    def feature(self, name):
        return next(
            f for f in self.observer.observations[-1].features if f.hypothesis == name
        )


def test_depth_band_is_not_best_quote_imbalance_and_partial_coverage_excludes():
    feed = ResearchFeed()
    feed.quote(0)
    # Best-quote quantities are equal, but genuine two-level depth is 10 vs 2.
    with localcontext(DECIMAL_CONTEXT):
        assert feed.feature("depth_band_imbalance").value == D(8) / D(12)
    partial = ResearchFeed()
    partial.quote(0, full=False)
    result = partial.feature("depth_band_imbalance")
    assert result.value is None
    assert "incomplete_depth_band" in result.reasons


def test_snapshot_flow_never_becomes_exact_ofi():
    feed = ResearchFeed(exact=False)
    for second in range(5):
        feed.quote(second, bid_size=9 + second)
    feature = feed.feature("event_ofi")
    assert feature.value is None
    assert "missing_exact_event_flow" in feature.reasons
    assert "no_event_continuity" in feature.reasons


def test_exact_ofi_and_admitted_trade_flow_use_independent_native_windows():
    feed = ResearchFeed()
    feed.quote(0)
    feed.trade(0, identity="anchor")
    feed.quote(1)
    feed.trade(1, identity="live")
    feed.trade(1, side="sell", history=True, identity="history")
    feed.quote(2)
    assert feed.feature("event_ofi").value == 0
    assert feed.feature("executed_trade_flow").value == 1
    feed.trade(2, side="unknown", identity="unknown")
    feed.quote(3)
    feature = feed.feature("executed_trade_flow")
    assert feature.value is None
    assert "unknown_aggressor_flow" in feature.reasons


def test_delayed_arrival_and_horizon_never_borrow_the_next_delivery_book():
    feed = ResearchFeed()
    config = replace(
        feed.config, scenarios=(ResearchScenario("late", latency_ns=5 * SECOND),)
    )
    feed = ResearchFeed(config=config)
    first = feed.quote(0)
    feed.quote(10, mid=200)
    label = next(label for label in feed.observer.labels if label.observation_id == 0)
    assert label.entry.mono_ns == 5 * SECOND
    assert label.exit.mono_ns == 6 * SECOND
    assert label.entry.delivery_index == label.exit.delivery_index == 1
    assert label.entry.book_ordinal == label.exit.book_ordinal == first.ingest_ordinal
    assert label.entry.buy_price == 101
    assert label.exit.sell_price == 99
    assert label.gross_long_bps == 0
    assert label.net_long_bps == -200


def test_prefix_features_and_matured_labels_do_not_change_with_future_suffix():
    left, right = ResearchFeed(), ResearchFeed()
    for second in range(8):
        left.quote(second, mid=100 + second)
        right.quote(second, mid=100 + second)
    prefix_observations = tuple(left.observer.observations)
    prefix_labels = tuple(left.observer.labels)
    left.quote(12, mid=150)
    right.quote(12, mid=70)
    assert (
        tuple(left.observer.observations[:8])
        == tuple(right.observer.observations[:8])
        == prefix_observations
    )
    assert (
        tuple(left.observer.labels[: len(prefix_labels)])
        == tuple(right.observer.labels[: len(prefix_labels)])
        == prefix_labels
    )
    for observation in prefix_observations:
        assert observation.delivery_index <= 8


def test_latency_fees_slippage_and_unknown_fees_are_economic_not_labels():
    template = ResearchFeed()
    scenarios = (
        ResearchScenario(),
        ResearchScenario("late", latency_ns=2 * SECOND),
        ResearchScenario("stress", extra_fee_bps=D(3), extra_slippage_bps=D(2)),
    )
    feed = ResearchFeed(config=replace(template.config, scenarios=scenarios))
    for second, mid in enumerate((100, 101, 110, 110, 110)):
        feed.quote(second, mid=mid)
    labels = {
        label.scenario: label
        for label in feed.observer.labels
        if label.observation_id == 0
    }
    assert labels["late"].entry.buy_price != labels["base"].entry.buy_price
    assert labels["late"].net_long_bps != labels["base"].net_long_bps
    assert labels["stress"].net_long_bps == labels["base"].net_long_bps - 10
    known, expensive, unknown = (
        ResearchFeed(),
        ResearchFeed(fee=D("0.01")),
        ResearchFeed(fee=None),
    )
    for item in (known, expensive, unknown):
        item.quote(0)
        item.quote(1, mid=101)
    assert (
        expensive.observer.labels[0].net_long_bps
        < known.observer.labels[0].net_long_bps
    )
    missing = unknown.observer.labels[0]
    assert missing.gross_long_bps is not None
    assert missing.net_long_bps is None
    assert missing.reasons


def test_native_order_size_partial_and_gap_labels_are_excluded():
    feed = ResearchFeed()
    partial = ResearchFeed(config=replace(feed.config, base_quantity=D(100)))
    partial.quote(0)
    partial.quote(1)
    assert partial.observer.labels[0].net_long_bps is None
    assert "partial_or_unfilled_order" in partial.observer.labels[0].reasons
    feed = ResearchFeed(config=replace(feed.config, horizons_ns=(5 * SECOND,)))
    feed.quote(0)
    feed.gap(1)
    feed.quote(2)
    feed.quote(6)
    label = next(label for label in feed.observer.labels if label.observation_id == 0)
    assert label.gross_long_bps is None
    assert label.net_long_bps is None
    assert "book_continuity_or_quality_break" in label.reasons


def test_closed_outcome_purge_and_half_open_embargo_boundaries():
    feed = ResearchFeed()
    feed.quote(0)
    observation = feed.observer.observations[0]
    config = replace(
        feed.config,
        horizons_ns=(SECOND, 5 * SECOND, 30 * SECOND, 60 * SECOND),
        scenarios=(ResearchScenario("late", latency_ns=SECOND),),
        embargo_ns=10 * SECOND,
    )
    train, validation = 100 * SECOND, 200 * SECOND
    expected = {
        38: "train",
        39: "purged",
        99: "purged",
        100: "embargoed",
        109: "embargoed",
        110: "validation",
        138: "validation",
        139: "purged",
        199: "purged",
        200: "embargoed",
        209: "embargoed",
        210: "test",
    }
    for second, split in expected.items():
        assert (
            _split(
                replace(observation, elapsed_ns=second * SECOND),
                config,
                train,
                validation,
            )
            == split
        )


def test_short_fixture_stays_insufficient_without_weakening_purge():
    feed = ResearchFeed(config=ResearchConfig(depth_band_bps=D(250)))
    for second in range(181):
        feed.quote(second)
    feed.observer._censor("end_of_source")
    descriptor = ReplaySourceDescriptor(
        "short", feed.streams, synthetic=True, provenance="test invented data"
    )
    report = _report("attempt", descriptor, "source-hash", 180 * SECOND, feed.observer)
    assert report.outcome == "insufficient_evidence"
    assert dict(report.split_counts)["purged"] > 0
    assert report.turnover is report.drawdown is None
    assert report.portfolio_unavailable_reasons
    assert {t.horizon_ns for t in report.trials} == set(feed.config.horizons_ns)
    assert {t.hypothesis for t in report.trials} == set(HYPOTHESES)
    assert {t.threshold for t in report.trials} == set(feed.config.thresholds)


def test_supported_negative_payoff_is_no_edge_and_test_cannot_select_threshold():
    feed = ResearchFeed()
    for second in range(301):
        feed.quote(second)
    feed.observer._censor("end_of_source")
    # Exercise report inference on declared nonsynthetic evidence; this fixture
    # itself remains invented and does not purport to be a durable market source.
    descriptor = ReplaySourceDescriptor(
        "report-inference", feed.streams, provenance="unit fixture"
    )
    report = _report("first", descriptor, "hash", 300 * SECOND, feed.observer)
    assert report.outcome == "no_reliable_edge"
    selections = report.selections
    boundary = report.validation_end_ns + feed.config.embargo_ns
    feed.observer.labels = [
        replace(label, net_long_bps=D(100000), net_short_bps=D(-100000))
        if feed.observer.observations[label.observation_id].elapsed_ns >= boundary
        and label.net_long_bps is not None
        else label
        for label in feed.observer.labels
    ]
    changed = _report("second", descriptor, "hash", 300 * SECOND, feed.observer)
    assert changed.selections == selections
    assert changed.report_hash != report.report_hash
    depth_train = [
        t
        for t in report.trials
        if t.hypothesis == "depth_band_imbalance"
        and t.split == "train"
        and t.group == "all"
    ]
    assert all(t.statistics.expected_net_payoff_bps < 0 for t in depth_train)
    assert all(t.statistics.matched_long_bps is not None for t in depth_train)


def test_positive_validation_selection_is_frozen_before_bad_test_outcomes():
    feed = ResearchFeed()
    for second in range(301):
        feed.quote(second, mid=100 + 3 * second)
    feed.observer._censor("end_of_source")
    descriptor = ReplaySourceDescriptor(
        "selection-fixture", feed.streams, provenance="unit fixture"
    )
    report = _report("first", descriptor, "hash", 300 * SECOND, feed.observer)
    assert any(s.threshold is not None for s in report.selections)
    boundary = report.validation_end_ns + feed.config.embargo_ns
    feed.observer.labels = [
        replace(label, net_long_bps=D(-10000), net_short_bps=D(10000))
        if feed.observer.observations[label.observation_id].elapsed_ns >= boundary
        and label.net_long_bps is not None
        else label
        for label in feed.observer.labels
    ]
    changed = _report("second", descriptor, "hash", 300 * SECOND, feed.observer)
    assert changed.selections == report.selections
    assert changed.report_hash != report.report_hash


def test_verified_durable_synthetic_capture_and_corruption_failure(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    try:
        with asyncio.run(
            create_demo(tmp_path / "captures", duration_seconds=3)
        ) as capture:
            reader = capture.reader
            path = reader.path
            config = ResearchConfig(
                stream_ids=(
                    str(
                        next(
                            s
                            for s in reader.manifest.streams
                            if s.key.channel == "book"
                        ).stream_id
                    ),
                ),
                horizons_ns=(SECOND,),
                scenarios=(ResearchScenario(),),
            )
            report = run_research(path, config, store)
            assert report.synthetic
            assert report.outcome == "insufficient_evidence"
            damaged = path / reader.manifest.files[0].path
        with damaged.open("ab") as handle:
            handle.write(b"intentional fixture corruption")
        with pytest.raises(Exception, match="verification|hash|size|checksum"):
            run_research(path, config, store)
        assert store.list_attempts()[0].status == "failed"
        assert store.get_report(report.attempt_id) == report
    finally:
        store.close()


@pytest.fixture(scope="module")
def real_pipeline_source():
    return MemoryReplaySource.demo()


def test_all_attempts_durable_reopened_and_identical_reports_hash_stably(
    tmp_path, real_pipeline_source
):
    source = real_pipeline_source
    book = next(
        spec for spec in source.descriptor.streams if spec.key.channel == "book"
    )
    config = ResearchConfig(
        stream_ids=(str(book.stream_id),),
        sample_interval_ns=10 * SECOND,
        horizons_ns=(SECOND,),
        thresholds=(D(0),),
        scenarios=(ResearchScenario(),),
        min_samples=3,
    )
    path = tmp_path / "research.sqlite3"
    store = ResearchStore(path)
    first = run_research(source, config, store)
    second = run_research(source, config, store)
    assert first.attempt_id != second.attempt_id
    assert first.report_hash == second.report_hash
    assert first.outcome == second.outcome == "insufficient_evidence"
    with pytest.raises((OSError, ValueError)):
        run_research(tmp_path / "missing-capture", config, store)
    with pytest.raises(ValueError, match="observation_budget"):
        run_research(source, replace(config, max_observations=1), store)
    failed_id = store.list_attempts()[0].attempt_id
    assert store.list_observations(failed_id)
    store.close()
    reopened = ResearchStore(path)
    try:
        attempts = reopened.list_attempts()
        assert len(attempts) == 4
        assert [a.status for a in attempts].count("failed") == 2
        assert [a.status for a in attempts].count("completed") == 2
        assert reopened.get_report(first.attempt_id) == first
        assert reopened.get_config(first.attempt_id) == config
        provenance = dict(reopened.get_source(failed_id))
        assert provenance["source_hash"] == first.source_hash
        assert provenance["synthetic"] == "True"
        assert reopened.get_report(failed_id) is None
        assert all(a.failure for a in attempts if a.status == "failed")
        assert len(reopened.list_observations(first.attempt_id, limit=1)) == 1
        assert len(reopened.list_labels(first.attempt_id, limit=1)) == 1
        with pytest.raises(ValueError):
            reopened.list_attempts(1001)
    finally:
        reopened.close()


def test_attempt_start_is_committed_before_source_access_and_failure_is_retained(
    tmp_path,
):
    path = tmp_path / "research.sqlite3"
    store = ResearchStore(path)

    class BrokenSource:
        @property
        def descriptor(self):
            other = ResearchStore(path)
            try:
                assert other.list_attempts()[0].status == "running"
            finally:
                other.close()
            raise ValueError("injected source opening failure")

    try:
        with pytest.raises(ValueError, match="injected source"):
            run_research(BrokenSource(), ResearchConfig(), store)
        attempt = store.list_attempts()[0]
        assert attempt.status == "failed"
        assert "injected source" in attempt.failure
    finally:
        store.close()


def test_native_derived_precision_and_configuration_resource_bounds():
    with pytest.raises(ValueError):
        ResearchConfig(base_quantity=D("NaN"))
    with pytest.raises(ValueError):
        ResearchConfig(max_observations=100_000)
    with pytest.raises(ValueError):
        ResearchScenario(extra_fee_bps=D("Infinity"))
