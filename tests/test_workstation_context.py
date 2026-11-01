"""Synthetic deterministic context evidence; no live execution or price claims."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D, localcontext

import pytest

from src.workstation.adapters.base import wrap_decoded
from src.workstation.adapters.hyperliquid import FUNDING_INTERVAL_NS, HyperliquidAdapter
from src.workstation.analytics import AnalyticsEngine
from src.workstation.book import Book, Level
from src.workstation.context import ContextEngine, MAX_REFERENCE_STREAMS
from src.workstation.costs import FXRate
from src.workstation.demo import demo_source
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.events import (
    AssetContext,
    BookUpdate,
    Control,
    Gap,
    ReferencePrice,
    SegmentId,
    SubscriptionKey,
    synthetic_envelope,
    synthetic_frame,
)
from src.workstation.instruments import AssetIdentity, synthetic_instrument
from src.workstation.precision import DECIMAL_CONTEXT
from src.workstation.quality import ClockReading, Quality
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock, ReplaySession, ReplaySourceDescriptor
from src.workstation.visuals import VisualHistory

SECOND = 1_000_000_000


def perpetual(symbol="BTC"):
    adapter = HyperliquidAdapter()
    metadata = adapter.parse_metadata(
        {"collateralToken": 0, "universe": [{"name": symbol, "szDecimals": 2}]},
        {
            "tokens": [
                {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
            ],
            "universe": [],
        },
        fetch_wall_ns=1,
        payload_hash="synthetic-context",
    )
    return replace(metadata.instruments[0], data_access="available")


def spec(instrument, channel, stream_id=None):
    key = SubscriptionKey.for_instrument(instrument, channel=channel)
    if instrument.venue == "hyperliquid":
        reducer = HyperliquidAdapter().new_reducer(instrument, key)
        revision, config = reducer.adapter_revision, reducer.config_hash
    else:
        revision, config = "synthetic-v1", "synthetic-config"
    return StreamSpec(stream_id or channel, instrument, key, revision, config)


class Feed:
    def __init__(self, *, spot=None, perp=None):
        self.spot = spot or synthetic_instrument(quote="USDT")
        self.perp = perp or perpetual()
        self.streams = (
            spec(self.spot, "book"),
            spec(self.perp, "reference"),
            spec(self.perp, "context"),
        )
        self.engine = ContextEngine(self.streams)
        self.clock = ReplayClock()
        self.batches = []
        self.books = ()

    def send(self, channel, payload, stamp=SECOND, **overrides):
        stream = next(s for s in self.streams if s.key.channel == channel)
        event = synthetic_envelope(
            stream.instrument,
            payload,
            ordinal=len(self.batches) + 1,
            mono_ns=stamp,
            stream_id=stream.stream_id,
            key=stream.key,
            **overrides,
        )
        self.clock.advance(event)
        batch = RecordedBatch(len(self.batches) + 1, (event,))
        self.batches.append(batch)
        self.engine.on_batch(batch, self.books, self.clock)
        return event

    def book(self, stamp=SECOND):
        event = self.send("book", BookUpdate("snapshot", ()), stamp)
        self.books = (
            Book(
                self.spot.instrument_id,
                self.spot.metadata_version,
                event.key,
                event.segment_id,
                event.connection_epoch,
                event.ingest_ordinal,
                event.ref(),
                (Level(D("99"), D("2")),),
                (Level(D("101"), D("2")),),
                Quality(
                    lifecycle="live",
                    integrity="valid",
                    transport_alive=True,
                    source_mode="demo",
                    clock_id=event.clock_id,
                    last_book_observation_mono_ns=stamp,
                    last_healthy_observation_mono_ns=stamp,
                ),
            ),
        )

    def context(self, price="102", stamp=SECOND, **fields):
        values = dict(
            mark=D(price),
            oracle=D("101"),
            funding_rate=D("0.0001"),
            funding_interval_ns=FUNDING_INTERVAL_NS,
            open_interest=D("123.45"),
            open_interest_unit="base",
        )
        values.update(fields)
        return self.send("context", AssetContext(**values), stamp)

    def mid(self, stamp=SECOND):
        return self.send(
            "reference",
            ReferencePrice("mid", D("101.5"), self.perp.quote.asset_id),
            stamp,
        )

    def snapshot(self, *, now=None, fx_rates=(), selected=None):
        return self.engine.snapshot(
            selected or self.perp.instrument_id,
            books=self.books,
            now=now or self.clock.reading,
            fx_rates=fx_rates,
        )


def mark(snapshot):
    return next(row for row in snapshot.basis if row.reference_kind == "mark")


def test_missing_channels_and_values_do_not_create_execution_quotes():
    feed = Feed()
    missing = feed.snapshot()
    assert not missing.watchlist[0].eligibility.eligible
    assert "clock_unavailable" in missing.contexts[0].eligibility.reasons
    assert "missing_mark" in dict(missing.contexts[0].field_reasons)["mark"]
    feed.mid()
    result = feed.snapshot()
    assert result.watchlist[0].eligibility.eligible
    assert result.watchlist[0].label == "reference_only_not_executable"
    assert feed.books == ()
    assert "missing_spot_book" in mark(result).reasons
    only_book = ContextEngine(feed.streams[:1]).snapshot(
        feed.spot.instrument_id, books=(), now=None
    )
    assert "context_channel_not_subscribed" in only_book.reasons
    assert "reference_channel_not_subscribed" in only_book.reasons


def test_actual_hourly_funding_and_raw_open_interest_units():
    feed = Feed()
    # Exercise the actual native adapter normalization, not a renamed USD fixture.
    value = HyperliquidAdapter._context(
        {
            "markPx": "102",
            "oraclePx": "101",
            "funding": "-0.0001",
            "openInterest": "123.45",
        },
        feed.perp,
    )
    feed.send("context", value)
    result = feed.snapshot().contexts[0]
    assert result.value.funding_rate == D("-0.0001")
    assert result.value.funding_interval_ns == 3_600_000_000_000
    assert result.funding_rate_unit == "fraction_per_funding_interval"
    assert result.value.open_interest == D("123.45")
    assert result.value.open_interest_unit == "base"
    assert (
        result.open_interest_unit_provenance
        == "adapter_declared_not_independently_verified"
    )
    feed.context(funding_interval_ns=None)
    assert (
        "missing_funding_interval_ns"
        in dict(feed.snapshot().contexts[0].field_reasons)["funding_rate"]
    )


def test_stale_heartbeat_reset_and_channel_independence():
    feed = Feed()
    feed.mid()
    feed.context()
    feed.send("context", Control("reset", "connection_lost"), SECOND + 1)
    result = feed.snapshot()
    assert result.watchlist[0].eligibility.eligible
    assert "context_continuity_reset" in result.contexts[0].eligibility.reasons
    feed.send("context", Control("heartbeat"), SECOND + 2)
    assert not feed.snapshot().contexts[0].eligibility.eligible
    feed.context(stamp=2 * SECOND)
    assert feed.snapshot().contexts[0].eligibility.eligible
    feed.send("context", Control("heartbeat"), 8 * SECOND)
    assert "stale_context_channel" in feed.snapshot().contexts[0].eligibility.reasons
    assert "stale_context_channel" in feed.snapshot().watchlist[0].eligibility.reasons


def test_gap_targets_and_book_controls_remain_independent():
    feed = Feed()
    feed.mid()
    feed.context()
    feed.send("book", Control("reset", "book_reset"))
    assert feed.snapshot().contexts[0].eligibility.eligible
    feed.send("book", Gap("capture_gap", affected_streams=("reference",)))
    result = feed.snapshot()
    assert not result.watchlist[0].eligibility.eligible
    assert result.contexts[0].eligibility.eligible


def test_clock_mismatch_regression_and_epoch_reanchoring():
    feed = Feed()
    feed.context(stamp=2 * SECOND)
    feed.context(price="900", stamp=SECOND)
    result = feed.snapshot().contexts[0]
    assert result.value.mark == D("102")
    assert "context_clock_regression" in result.eligibility.reasons
    feed.send("context", Control("heartbeat"), 3 * SECOND, epoch=1)
    assert feed.snapshot().contexts[0].value is None
    feed.context(stamp=4 * SECOND, open_interest=None, open_interest_unit=None)
    assert feed.snapshot().contexts[0].eligibility.eligible
    foreign = feed.snapshot(now=ClockReading("another-clock", 4 * SECOND))
    assert "clock_mismatch" in foreign.contexts[0].eligibility.reasons
    future = feed.snapshot(now=ClockReading("synthetic-clock", SECOND))
    assert "clock_regression" in future.contexts[0].eligibility.reasons


def test_valid_btc_usdt_reference_basis_keeps_quanto_settlement_separate():
    feed = Feed()
    feed.book()
    feed.context()
    row = mark(feed.snapshot())
    assert not row.reasons
    assert row.spot_mid == D("100")
    assert row.proceeds_basis_bps == row.funding_basis_bps == D("200")
    assert row.quote_asset_id == row.perpetual_quote_asset_id == "USDT"
    assert feed.perp.settlement.asset_id == "hyperliquid:hypercore:token:0xusdc"
    assert feed.perp.settlement_conversion == "quanto_no_fx"
    assert row.label == "reference_basis_not_executable_arbitrage"


def test_native_adapter_asset_identity_permits_canonical_reference_basis():
    source = demo_source()
    instruments = {
        spec.instrument.venue: spec.instrument
        for spec in source.streams
        if spec.key.channel == "book" and spec.instrument.base.asset_id == "BTC"
    }
    spot, perp = instruments["binance-global"], instruments["hyperliquid"]
    assert spot.base.native_token_id != perp.base.native_token_id
    feed = Feed(spot=spot, perp=perp)
    feed.book()
    feed.context()
    row = mark(feed.snapshot())
    assert not row.reasons
    assert row.proceeds_basis_bps == row.funding_basis_bps == D("200")


@pytest.mark.parametrize(
    "symbol,quote",
    [
        ("BTC", "USDT"),
        ("ETH", "USDT"),
        ("SOL", "USDT"),
        ("HYPE", "USDC"),
        ("PURR", "USDC"),
    ],
)
def test_native_price_denomination_survives_context_snapshot(symbol, quote):
    feed = Feed(perp=perpetual(symbol))
    feed.context()
    instrument = feed.snapshot().contexts[0].instrument
    assert instrument.quote.asset_id == quote
    assert instrument.settlement.asset_id != instrument.quote.asset_id


def test_mismatched_quote_requires_explicit_costed_conversion():
    feed = Feed(spot=synthetic_instrument(quote="USDC"))
    feed.book()
    feed.context()
    row = mark(feed.snapshot())
    assert any(reason.startswith("missing_fx:") for reason in row.reasons)
    assert row.proceeds_basis_bps is None and row.funding_basis_bps is None
    rate = FXRate(
        "USDT", "USDC", D("0.98"), D("1.02"), feed.clock.reading, "synthetic", None
    )
    assert "unknown_fx_fees" in mark(feed.snapshot(fx_rates=(rate,))).reasons


@pytest.mark.parametrize("reverse", [False, True])
def test_converted_basis_preserves_bid_ask_fee_orientation(reverse):
    feed = Feed(spot=synthetic_instrument(quote="USDC"))
    feed.book()
    feed.context()
    base, quote = ("USDC", "USDT") if reverse else ("USDT", "USDC")
    rate = FXRate(
        base, quote, D("0.98"), D("1.02"), feed.clock.reading, "synthetic", D("0.01")
    )
    row = mark(feed.snapshot(fx_rates=(rate,)))
    assert not row.reasons
    with localcontext(DECIMAL_CONTEXT):
        proceeds = (
            D(102) / D("1.02") * D("0.99")
            if reverse
            else D(102) * D("0.98") * D("0.99")
        )
        funding = (
            D(102) / D("0.98") / D("0.99")
            if reverse
            else D(102) * D("1.02") / D("0.99")
        )
        assert row.proceeds_in_spot_quote == proceeds
        assert row.funding_cost_in_spot_quote == funding
        assert row.proceeds_basis_bps == (proceeds / 100 - 1) * 10_000
        assert row.funding_basis_bps == (funding / 100 - 1) * 10_000
    assert row.proceeds_conversion.rate is rate
    assert row.funding_conversion.required_input


@pytest.mark.parametrize(
    "clock,reason",
    [
        (ClockReading("synthetic-clock", 0), "fx_asof_skew"),
        (ClockReading("synthetic-clock", 3 * SECOND), "fx_clock_regression"),
        (ClockReading("other", 2 * SECOND), "fx_clock_mismatch"),
    ],
)
def test_fx_clock_and_pair_skew_are_not_silently_ignored(clock, reason):
    feed = Feed(spot=synthetic_instrument(quote="USDC"))
    feed.book(stamp=2 * SECOND)
    feed.context(stamp=2 * SECOND)
    rate = FXRate("USDT", "USDC", D(1), D(1), clock, "synthetic", D(0))
    row = mark(feed.snapshot(fx_rates=(rate,)))
    assert reason in row.reasons
    assert row.proceeds_basis_bps is None


def test_stale_fx_and_skewed_spot_perpetual_observations_suppress_basis():
    feed = Feed(spot=synthetic_instrument(quote="USDC"))
    feed.book(stamp=10 * SECOND)
    feed.context(stamp=10 * SECOND)
    rate = FXRate(
        "USDT",
        "USDC",
        D(1),
        D(1),
        ClockReading("synthetic-clock", SECOND),
        "synthetic",
        D(0),
    )
    row = mark(feed.snapshot(fx_rates=(rate,)))
    assert "stale_fx" in row.reasons and row.proceeds_basis_bps is None
    feed = Feed()
    feed.book()
    feed.context(stamp=3 * SECOND)
    row = mark(feed.snapshot())
    assert "asof_skew" in row.reasons and row.proceeds_basis_bps is None


def test_reference_currency_mismatch_cannot_establish_reference_health():
    feed = Feed()
    feed.send("reference", ReferencePrice("mid", D(100), "USDC"))
    row = feed.snapshot().watchlist[0]
    assert row.value is None
    assert "reference_currency_mismatch" in row.eligibility.reasons


def test_wrapped_native_token_ticker_and_reference_link_are_not_fungibility():
    spot = replace(
        synthetic_instrument(quote="USDT"),
        base=AssetIdentity(
            "hyperliquid:hypercore:token:wrapped",
            "BTC",
            native_token_id="9",
            network="hypercore",
            reference_asset_id="BTC",
        ),
    )
    feed = Feed(spot=spot)
    feed.book()
    feed.context()
    row = mark(feed.snapshot())
    assert "incompatible_underlying_identity" in row.reasons
    assert row.proceeds_basis_bps is None


def test_checkpoint_reset_and_prefix_reconstruction_exclude_future_values():
    feed = Feed()
    feed.context()
    feed.mid()
    prefix = feed.engine.checkpoint()
    expected = feed.snapshot()
    feed.context(price="999", stamp=2 * SECOND)
    feed.engine.restore(prefix)
    assert (
        feed.engine.snapshot(feed.perp.instrument_id, books=(), now=expected.now)
        == expected
    )
    feed.engine.reset()
    assert feed.snapshot().contexts[0].value is None
    clock = ReplayClock()
    for batch in feed.batches[:2]:
        clock.advance(batch.events[0])
        feed.engine.on_batch(batch, (), clock)
    assert feed.engine.checkpoint() == prefix
    with pytest.raises(ValueError, match="must advance"):
        feed.engine.on_batch(feed.batches[1], (), clock)


def test_real_replay_seek_and_speed_parity_for_context_observations():
    async def scenario():
        instrument = perpetual()
        streams = (spec(instrument, "context"), spec(instrument, "reference"))
        frames = {}
        batches = []
        adapter = HyperliquidAdapter()
        for i, price in ((1, "100"), (2, "101"), (3, "900")):
            stream = streams[1] if i == 2 else streams[0]
            message = (
                {"channel": "allMids", "data": {"mids": {"BTC": price}}}
                if i == 2
                else {
                    "channel": "activeAssetCtx",
                    "data": {
                        "coin": "BTC",
                        "ctx": {
                            "markPx": price,
                            "oraclePx": "100",
                            "funding": "0.0001",
                            "openInterest": "123",
                        },
                    },
                }
            )
            raw = synthetic_frame(
                json.dumps(message).encode(),
                ordinal=i,
                mono_ns=i * 1_000,
                stream_id=stream.stream_id,
            )
            decoded = adapter.decode(raw, instrument, stream.key)
            assert decoded.error is None
            events = wrap_decoded(
                raw,
                instrument,
                stream.key,
                decoded,
                SegmentId("synthetic-segment"),
                "demo",
            )
            batches.append(RecordedBatch(i, events))
            frames[(str(stream.stream_id), i)] = raw
        batches.append(
            RecordedBatch(
                4,
                (
                    synthetic_envelope(
                        instrument,
                        Gap("reference_gap"),
                        ordinal=4,
                        mono_ns=4_000,
                        stream_id=streams[1].stream_id,
                        key=streams[1].key,
                    ),
                ),
            )
        )
        source = MemoryReplaySource(
            ReplaySourceDescriptor("synthetic-session", streams, synthetic=True),
            tuple(batches),
            frames,
            {(i, 0): i for i in range(1, 5)},
            4_000,
        )
        final = None
        for mode, speed in (
            ("raw", D(1)),
            ("normalized", D(1)),
            ("raw", D(1000000)),
            ("normalized", D(1000000)),
        ):
            observer = ContextEngine(streams)
            session = ReplaySession(source, mode=mode, observers=(observer,))
            try:
                session.step(2)
                prefix = observer.checkpoint()
                await session.run_to_end(speed)
                current = observer.checkpoint()
                if final is None:
                    final = current
                assert current == final
                session.seek(2)
                assert observer.checkpoint() == prefix
                snapshot = observer.snapshot(
                    instrument.instrument_id, books=(), now=session.clock.reading
                )
                assert snapshot.contexts[0].value.mark == D(100)
                assert snapshot.watchlist[0].eligibility.eligible
                session.seek(0)
                assert (
                    observer.snapshot(instrument.instrument_id, books=(), now=None)
                    .contexts[0]
                    .value
                    is None
                )
            finally:
                session.close()

    asyncio.run(scenario())


def test_watchlist_bound_rejects_instead_of_silently_truncating():
    instrument = perpetual()
    with pytest.raises(ValueError, match="channel bound"):
        ContextEngine(
            tuple(
                spec(instrument, "reference", f"ref-{i}")
                for i in range(MAX_REFERENCE_STREAMS + 1)
            )
        )


@pytest.mark.parametrize("channel", ["reference", "context"])
@pytest.mark.parametrize("control", [Control("reset", "reset"), Gap("gap")])
def test_context_controls_do_not_segment_healthy_visual_history(channel, control):
    instrument = synthetic_instrument()
    streams = tuple(
        spec(instrument, name) for name in ("book", "trades", "reference", "context")
    )
    history = VisualHistory(AnalyticsEngine(streams), streams[0].key)
    clock = ReplayClock()
    book_event = synthetic_envelope(
        instrument, BookUpdate("snapshot", ()), stream_id="book", key=streams[0].key
    )
    book = Book(
        instrument.instrument_id,
        instrument.metadata_version,
        book_event.key,
        book_event.segment_id,
        0,
        1,
        book_event.ref(),
        (Level(D(99), D(1)),),
        (Level(D(101), D(1)),),
        Quality(
            lifecycle="live",
            integrity="valid",
            transport_alive=True,
            source_mode="demo",
            clock_id=book_event.clock_id,
            last_book_observation_mono_ns=1,
            last_healthy_observation_mono_ns=1,
        ),
    )
    clock.advance(book_event)
    history.on_batch(RecordedBatch(1, (book_event,)), (book,), clock)
    segment = history.segment
    # Exercise both constructor and selection relevance sets.
    history.select(streams[0].key)
    event = synthetic_envelope(
        instrument,
        control,
        ordinal=2,
        stream_id=channel,
        key=next(s.key for s in streams if s.key.channel == channel),
    )
    clock.advance(event)
    history.on_batch(RecordedBatch(2, (event,)), (book,), clock)
    assert history.segment == segment
    assert not history.discontinuities
    targeted = replace(
        event,
        ingest_ordinal=3,
        receive_mono_ns=3,
        payload=Gap("book_loss", affected_streams=("book",)),
    )
    clock.advance(targeted)
    history.on_batch(RecordedBatch(3, (targeted,)), (book,), clock)
    assert history.segment != segment
    assert tuple(history.discontinuities) == (3,)


@pytest.mark.parametrize("channel", ["book", "trades"])
def test_actual_market_channel_controls_still_segment_visuals(channel):
    instrument = synthetic_instrument()
    streams = tuple(spec(instrument, name) for name in ("book", "trades", "context"))
    history = VisualHistory(AnalyticsEngine(streams), streams[0].key)
    clock = ReplayClock()
    for index in (1, 2):
        stream = next(s for s in streams if s.key.channel == channel)
        event = synthetic_envelope(
            instrument,
            Control("reset", "market_reset"),
            ordinal=index,
            stream_id=stream.stream_id,
            key=stream.key,
        )
        clock.advance(event)
        history.on_batch(RecordedBatch(index, (event,)), (), clock)
    assert history.segment_number == 2
    assert tuple(history.discontinuities) == (2,)
