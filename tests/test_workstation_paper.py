"""Synthetic, deterministic accounting and cursor boundaries; no execution claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier

import pytest

from src.workstation.book import BookReducer
from src.workstation.costs import FeeSchedule, InventoryBalance, _validate_price
from src.workstation.events import (
    BookUpdate,
    LevelChange,
    SubscriptionKey,
    synthetic_envelope,
)
from src.workstation.instruments import OrderConstraints, synthetic_instrument
from src.workstation.paper import (
    MAX_DELAYED_BOOK_BYTES,
    PaperConfig,
    PaperEngine,
    PaperError,
    PaperFee,
    PaperOrderRequest,
    PaperStore,
)
from src.workstation.quality import ClockReading, EligibilityPolicy
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock

D = Decimal
FREE = FeeSchedule(
    D("0"), provenance="explicit synthetic fee assumption", asof_wall_ns=1
)
FEE = replace(FREE, rate=D("0.01"))


def streaming_instrument(instrument=None):
    return replace(
        instrument or synthetic_instrument(),
        metadata_version="synthetic-streaming-v1",
        metadata_status="streaming_only",
        status="unknown",
        tick_size=None,
        lot_size=None,
        constraints=OrderConstraints(
            price_rule="unconstrained", size_rule="unconstrained"
        ),
    )


def observation(
    instrument=None,
    *,
    mono=100,
    asks=(("100", "1"), ("102", "2")),
    bids=(("99", "2"), ("98", "3")),
    delivery=1,
):
    instrument = instrument or synthetic_instrument()
    depth = max(10, len(asks), len(bids))
    key = SubscriptionKey.for_instrument(instrument, depth=depth)
    event = synthetic_envelope(
        instrument,
        BookUpdate(
            "snapshot",
            tuple(
                LevelChange(side, D(price), D(size))
                for side, levels in (("bid", bids), ("ask", asks))
                for price, size in levels
            ),
            depth,
        ),
        key=key,
        mono_ns=mono,
        ordinal=delivery,
    )
    reducer = BookReducer(
        instrument, key, segment_id="synthetic-segment", price_validator=_validate_price
    )
    book = reducer.apply((event,)).book
    assert book is not None
    stream = StreamSpec(
        event.stream_id, instrument, key, "fixture-v1", "fixture-config"
    )
    return stream, book, RecordedBatch(delivery, (event,))


def deliver(engine, book, batch):
    clock = ReplayClock()
    clock.advance(batch.events[0])
    engine.on_batch(batch, (book,), clock)
    return ClockReading(
        batch.events[0].clock_id,
        batch.events[0].receive_mono_ns,
        batch.events[0].receive_wall_ns,
    )


def config(instrument, *, cash="1000", inventory="0", fee=FREE, **kwargs):
    return PaperConfig(
        (
            InventoryBalance(instrument.venue, instrument.quote.asset_id, D(cash)),
            InventoryBalance(instrument.venue, instrument.base.asset_id, D(inventory)),
        ),
        (PaperFee(instrument.instrument_id, fee),),
        policy=EligibilityPolicy(max_age_ns=10_000),
        **kwargs,
    )


def setup(tmp_path, *, cfg=None, instrument=None):
    stream, book, batch = observation(instrument)
    store = PaperStore(tmp_path / "paper.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:fixture-v1")
    now = deliver(engine, book, batch)
    engine.configure(cfg or config(stream.instrument), now=now, delivery_index=1)
    return store, engine, stream, book, now


def request(stream, quantity="1", side="buy", **kwargs):
    return PaperOrderRequest(
        stream.instrument.instrument_id, side, D(quantity), **kwargs
    )


def balances(engine, now):
    return {item.asset_id: item.available for item in engine.snapshot(now).balances}


def test_exact_notional_fees_and_average_cost_conserve_assets(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(tmp_path, cfg=config(instrument, fee=FEE))
    bought = engine.submit(request(stream, "3"), now=now, delivery_index=1)
    assert bought.status == "filled"
    snapshot = engine.snapshot(now)
    assert snapshot.fills[0].notional_quote == D("304")
    assert snapshot.fills[0].fee_amount == D("3.04")
    assert balances(engine, now) == {"BTC": D("3"), "USD": D("692.96")}
    position = snapshot.positions[0]
    assert position.cost_basis_quote == D("307.04")
    assert position.unrealized_pnl_quote == D("-10.04")
    sold = engine.submit(request(stream, "3", "sell"), now=now, delivery_index=1)
    assert sold.status == "filled"
    assert balances(engine, now) == {"BTC": D("0"), "USD": D("986.00")}
    position = engine.snapshot(now).positions[0]
    assert position.quantity == 0 and position.cost_basis_quote == 0
    assert position.realized_pnl_quote == D("-14.00")
    store.close()


def test_partial_fill_remainder_and_no_repeat_consumption(tmp_path):
    store, engine, stream, book, now = setup(tmp_path)
    order = engine.submit(request(stream, "4"), now=now, delivery_index=1)
    assert order.status == "partial" and order.filled_quantity == 3
    assert order.remaining_quantity == 1 and "ioc_remainder_cancelled" in order.reasons
    retry = engine.submit(request(stream), now=now, delivery_index=1)
    assert retry.filled_quantity == 0 and "no_unreserved_depth" in retry.reasons
    # Neither an unchanged resubscription nor a newer snapshot restores depth.
    engine.replace_streams((stream,), books=(book,), now=now)
    _, newer, batch = observation(stream.instrument, mono=110, delivery=2)
    now = deliver(engine, newer, batch)
    retry = engine.submit(request(stream), now=now, delivery_index=2)
    assert retry.filled_quantity == 0
    # Only the observed +1 at this price is newly available.
    _, larger, batch = observation(
        stream.instrument, mono=120, delivery=3, asks=(("100", "2"), ("102", "2"))
    )
    now = deliver(engine, larger, batch)
    replenished = engine.submit(request(stream, "2"), now=now, delivery_index=3)
    assert replenished.filled_quantity == 1
    assert sum(fill.quantity for fill in engine.snapshot(now).fills) == 4
    store.close()


def test_cash_and_inventory_include_fee_asset_and_never_go_negative(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, cash="101", fee=FEE)
    )
    order = engine.submit(request(stream, "2"), now=now, delivery_index=1)
    assert order.filled_quantity == 1 and "insufficient_cash" in order.reasons
    assert balances(engine, now) == {"BTC": D("1"), "USD": D("0")}
    sell = engine.submit(request(stream, "2", "sell"), now=now, delivery_index=1)
    assert sell.filled_quantity == 1 and "insufficient_inventory" in sell.reasons
    assert balances(engine, now)["USD"] == D("98.01")
    store.close()


def test_base_asset_fee_charges_received_or_additional_inventory(tmp_path):
    instrument = synthetic_instrument()
    fee = replace(FEE, asset_id=instrument.base.asset_id)
    store, engine, stream, _, now = setup(tmp_path, cfg=config(instrument, fee=fee))
    engine.submit(request(stream), now=now, delivery_index=1)
    assert balances(engine, now)["BTC"] == D("0.99")
    sell = engine.submit(request(stream, "1", "sell"), now=now, delivery_index=1)
    assert sell.filled_quantity == D("0.980")
    assert balances(engine, now)["BTC"] == D("0.00020")
    assert all(item.available >= 0 for item in engine.snapshot(now).balances)
    store.close()


def test_unmarketable_limit_is_cancelled_not_passive_and_native_lot_rejected(tmp_path):
    store, engine, stream, _, now = setup(tmp_path)
    order = engine.submit(
        request(stream, order_type="limit", limit_price=D("99")),
        now=now,
        delivery_index=1,
    )
    assert order.status == "cancelled" and order.filled_quantity == 0
    _, cheaper, batch = observation(
        stream.instrument,
        mono=110,
        asks=(("98", "1"),),
        bids=(("97", "1"),),
        delivery=2,
    )
    now = deliver(engine, cheaper, batch)
    assert not engine.snapshot(now).fills
    with pytest.raises(ValueError, match="increment"):
        engine.submit(request(stream, "0.0001"), now=now, delivery_index=2)
    store.close()


def test_minimum_notional_suppresses_small_partial_and_market_limit_caps(tmp_path):
    instrument = synthetic_instrument(
        constraints=OrderConstraints(
            min_notional=D("200"), min_notional_currency="USD", max_market_base=D("2")
        )
    )
    store, engine, stream, _, now = setup(
        tmp_path, instrument=instrument, cfg=config(instrument, cash="100")
    )
    with pytest.raises(PaperError, match="native_order_quantity_constraint"):
        engine.submit(request(stream, "3"), now=now, delivery_index=1)
    order = engine.submit(request(stream, "2"), now=now, delivery_index=1)
    assert order.filled_quantity == 0 and "below_order_minimum" in order.reasons
    assert balances(engine, now)["USD"] == 100
    store.close()


def test_unknown_future_and_third_asset_fees_never_fill(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(tmp_path)
    for fee, reason in (
        (FeeSchedule(), "unknown_fees"),
        (replace(FREE, asof_wall_ns=now.wall_ns + 1), "fee_date_in_future"),
        (replace(FREE, asset_id="OTHER"), "unsupported_fee_asset"),
    ):
        engine.configure(config(instrument, fee=fee), now=now, delivery_index=1)
        result = engine.submit(request(stream), now=now, delivery_index=1)
        assert result.filled_quantity == 0 and reason in result.reasons
    store.close()


def test_perpetual_contract_economics_are_explicitly_rejected(tmp_path):
    instrument = synthetic_instrument(market_type="perpetual")
    store, engine, stream, _, now = setup(tmp_path, instrument=instrument)
    with pytest.raises(PaperError, match="unsupported_perpetual"):
        engine.submit(request(stream), now=now, delivery_index=1)
    assert not engine.snapshot(now).orders
    store.close()


@pytest.mark.parametrize("market_type", ("spot", "perpetual"))
@pytest.mark.parametrize("order_type", ("market", "limit"))
@pytest.mark.parametrize("side", ("buy", "sell"))
def test_streaming_only_orders_reject_before_recording_or_accounting(
    tmp_path, market_type, order_type, side
):
    instrument = streaming_instrument(synthetic_instrument(market_type=market_type))
    store, engine, stream, _, now = setup(
        tmp_path, instrument=instrument, cfg=config(instrument, inventory="2")
    )
    before = engine.snapshot(now)
    options = {"order_type": order_type}
    if order_type == "limit":
        options["limit_price"] = D("100.123456789")
    with pytest.raises(PaperError, match="^instrument_metadata_unverified$"):
        engine.submit(
            request(stream, quantity="0.123456789", side=side, **options),
            now=now,
            delivery_index=1,
        )
    assert engine.snapshot(now) == before
    assert not before.orders and not before.fills
    store.close()


def test_streaming_only_inventory_has_no_mark_or_pnl_after_checkpoint_restore(
    tmp_path,
):
    instrument = streaming_instrument()
    store, engine, _, _, now = setup(
        tmp_path, instrument=instrument, cfg=config(instrument, inventory="2")
    )
    snapshot = engine.snapshot(now)
    assert len(snapshot.positions) == 1
    position = snapshot.positions[0]
    assert position.quantity == 2
    assert position.mark_price is None and position.unrealized_pnl_quote is None
    assert "instrument_metadata_unverified" in position.reasons
    checkpoint = engine.checkpoint()
    engine.restore(checkpoint)
    assert engine.snapshot(now) == snapshot
    archived = store.archived_snapshot(snapshot.run.run_id)
    assert archived is not None
    assert archived.run.instruments[0].metadata_status == "streaming_only"
    assert archived.run.instruments[0].lot_size is None
    store.close()


def test_pending_paper_order_cannot_fill_against_streaming_only_metadata(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, book, now = setup(
        tmp_path, cfg=config(instrument, order_latency_ns=20)
    )
    pending = engine.submit(request(stream), now=now, delivery_index=1)
    assert pending.status == "pending"
    before = balances(engine, now)
    unverified = streaming_instrument(stream.instrument)
    engine.replace_streams(
        (replace(stream, instrument=unverified),),
        books=(replace(book, metadata_version=unverified.metadata_version),),
        now=now,
    )
    engine.advance_time(pending.arrival_at)
    snapshot = engine.snapshot(pending.arrival_at)
    order = snapshot.orders[0]
    assert order.status == "cancelled"
    assert order.filled_quantity == 0 and order.remaining_quantity == 1
    assert "instrument_metadata_unverified" in order.reasons
    assert not snapshot.fills
    assert balances(engine, pending.arrival_at) == before
    store.close()


def test_pending_cancel_and_skipped_arrival_use_only_previous_book(tmp_path):
    instrument = synthetic_instrument()
    cfg = config(instrument, decision_latency_ns=10, order_latency_ns=10)
    store, engine, stream, _, now = setup(tmp_path, cfg=cfg)
    pending = engine.submit(request(stream), now=now, delivery_index=1)
    cancelled = engine.submit(request(stream), now=now, delivery_index=1)
    at_cancel = replace(now, mono_ns=110, wall_ns=now.wall_ns + 10)
    assert (
        engine.cancel(cancelled.order_id, now=at_cancel, delivery_index=1).status
        == "cancelled"
    )
    _, later, batch = observation(
        instrument, mono=200, asks=(("200", "20"),), delivery=2
    )
    now = deliver(engine, later, batch)
    snapshot = engine.snapshot(now)
    assert len(snapshot.fills) == 1
    fill = snapshot.fills[0]
    assert fill.order_id == pending.order_id and fill.at.mono_ns == 120
    assert fill.notional_quote == 100 and fill.book_asof.receive_mono_ns == 100
    assert snapshot.orders[1].status == "cancelled"
    store.close()


def test_feed_latency_does_not_let_later_delivery_backfill_arrival(tmp_path):
    instrument = synthetic_instrument()
    cfg = config(instrument, feed_latency_ns=30, order_latency_ns=10)
    store, engine, stream, _, now = setup(tmp_path, cfg=cfg)
    pending = engine.submit(request(stream), now=now, delivery_index=1)
    _, later, batch = observation(instrument, mono=200, delivery=2)
    now = deliver(engine, later, batch)
    order = engine.snapshot(now).orders[0]
    assert order.order_id == pending.order_id and order.filled_quantity == 0
    assert "instrument_not_observed" in order.reasons
    store.close()


def test_rewind_resume_reopened_store_and_explicit_fork_preserve_original(tmp_path):
    store, engine, stream, _, now = setup(tmp_path)
    engine.submit(request(stream), now=now, delivery_index=1)
    _, second_book, second = observation(stream.instrument, mono=200, delivery=2)
    second_now = deliver(engine, second_book, second)
    engine.submit(request(stream), now=second_now, delivery_index=2)
    original = engine.snapshot(second_now)
    run_id = original.run.run_id
    engine.reset()
    assert not engine.snapshot(None).orders and not engine.snapshot(None).balances
    _, first_book, first = observation(stream.instrument)
    first_now = deliver(engine, first_book, first)
    prefix = engine.snapshot(first_now)
    assert len(prefix.orders) == len(prefix.fills) == 1
    assert balances(engine, first_now)["BTC"] == 1
    with pytest.raises(PaperError, match="future_commands_fork_required"):
        engine.submit(request(stream), now=first_now, delivery_index=1)
    checkpoint = engine.checkpoint()
    deliver(engine, second_book, second)
    assert engine.snapshot(second_now) == original
    engine.restore(checkpoint)
    assert engine.snapshot(first_now) == prefix
    fork_id = engine.configure(
        config(stream.instrument, cash="500"), now=first_now, delivery_index=1
    )
    assert fork_id != run_id and engine.snapshot(first_now).run.parent_run_id == run_id
    assert not engine.snapshot(first_now).orders
    store.close()
    reopened = PaperStore(tmp_path / "paper.sqlite")
    resumed = PaperEngine((stream,), reopened, "synthetic:fixture-v1", run_id=run_id)
    assert not resumed.snapshot(None).orders and not resumed.snapshot(None).balances
    deliver(resumed, first_book, first)
    deliver(resumed, second_book, second)
    assert resumed.snapshot(second_now) == original
    assert {run.run_id for run in reopened.list_run_summaries()} == {run_id, fork_id}
    reopened.close()


@pytest.mark.parametrize("corrupt_part", ("config", "instruments"))
def test_run_summaries_cache_validated_payload_not_revision_or_resume_authority(
    tmp_path, monkeypatch, corrupt_part
):
    import json

    import src.workstation.paper as paper

    store, engine, stream, book, now = setup(tmp_path)
    run_id = engine.snapshot(now).run.run_id
    writer = PaperStore(tmp_path / "paper.sqlite")
    other = PaperEngine((stream,), writer, "synthetic:fixture-v1", run_id=run_id)
    _, _, initial = observation(stream.instrument)
    deliver(other, book, initial)
    decoded = []
    original_decode = paper._decode

    def counted_decode(payload, expected):
        decoded.append(expected)
        return original_decode(payload, expected)

    monkeypatch.setattr(paper, "_decode", counted_decode)
    try:
        summary = store.list_run_summaries()[0]
        assert summary.run_id == run_id
        assert summary.revision == 0
        assert not hasattr(summary, "config")
        assert not hasattr(summary, "instruments")
        assert store.list_run_summaries(source_id="synthetic:fixture-v1") == (summary,)
        assert store.list_run_summaries(source_id="synthetic:other") == ()
        assert decoded == [paper.PaperRun]
        other.submit(request(stream), now=now, delivery_index=1)
        revised = store.list_run_summaries()[0]
        assert revised == replace(summary, revision=1)
        assert decoded == [paper.PaperRun]
        # Resume still reads and validates full intent and every command.
        full, commands = store._load(run_id)
        assert full.config == engine.snapshot(now).run.config
        assert full.revision == len(commands) == 1
        assert decoded == [paper.PaperRun, paper.PaperRun, paper.PaperCommand]

        edited = paper._encode(replace(full, parent_run_id="changed-parent"))
        with writer._db:
            writer._db.execute(
                "UPDATE runs SET payload=? WHERE run_id=?", (edited, run_id)
            )
        refreshed = store.list_run_summaries()[0]
        assert refreshed.parent_run_id == "changed-parent"
        assert refreshed.revision == 1
        assert decoded.count(paper.PaperRun) == 3
        # The durable codec accepts both SQLite BLOB and UTF-8 TEXT storage.
        with writer._db:
            writer._db.execute(
                "UPDATE runs SET payload=? WHERE run_id=?",
                (edited.decode("utf-8"), run_id),
            )
        assert store.list_run_summaries()[0] == refreshed
        assert decoded.count(paper.PaperRun) == 3

        corrupt = json.loads(edited)
        omitted = (
            corrupt["value"]["config"]
            if corrupt_part == "config"
            else corrupt["value"]["instruments"][0]
        )
        omitted["unexpected_field"] = "must still reject"
        with writer._db:
            writer._db.execute(
                "UPDATE runs SET payload=? WHERE run_id=?",
                (json.dumps(corrupt).encode(), run_id),
            )
        for _ in range(2):
            with pytest.raises(ValueError):
                store.list_run_summaries()
            with pytest.raises(ValueError):
                store._load(run_id)
        for limit in (0, 1001, True):
            with pytest.raises(ValueError, match="query limit"):
                store.list_run_summaries(limit=limit)
    finally:
        writer.close()
        store.close()
    assert not store._run_summaries
    assert store._run_summary_bytes == 0


@pytest.mark.parametrize("bound", ("entries", "bytes", "oversized"))
def test_run_summary_cache_is_bounded_without_omitting_history(
    tmp_path, monkeypatch, bound
):
    import src.workstation.paper as paper

    store, engine, stream, _book, now = setup(tmp_path)
    first_id = engine.snapshot(now).run.run_id
    second_id = engine.configure(config(stream.instrument), now=now, delivery_index=1)
    if bound == "entries":
        monkeypatch.setattr(paper, "MAX_RUN_SUMMARY_CACHE_ENTRIES", 1)
    else:
        monkeypatch.setattr(
            paper, "MAX_RUN_SUMMARY_CACHE_BYTES", 8192 if bound == "bytes" else 1
        )
    try:
        summaries = store.list_run_summaries()
        assert [run.run_id for run in summaries] == [second_id, first_id]
        assert store.list_run_summaries(limit=1) == summaries[:1]
        assert store._run_summary_bytes <= paper.MAX_RUN_SUMMARY_CACHE_BYTES
        assert len(store._run_summaries) <= paper.MAX_RUN_SUMMARY_CACHE_ENTRIES
        assert len(store._run_summaries) == (0 if bound == "oversized" else 1)
    finally:
        store.close()


def test_historical_paper_store_is_read_only_and_never_creates_or_migrates(tmp_path):
    import sqlite3

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        PaperStore(missing, read_only=True)
    assert not missing.exists()
    writer, engine, _stream, _book, now = setup(tmp_path)
    path = tmp_path / "paper.sqlite"
    run_id = engine.snapshot(now).run.run_id
    reader = PaperStore(path, read_only=True)
    try:
        assert reader.list_run_summaries()[0].run_id == run_id
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader._db.execute("UPDATE runs SET revision=99")
        assert writer.list_run_summaries()[0].revision == 0
    finally:
        reader.close()
        writer.close()
    with sqlite3.connect(path) as legacy:
        legacy.execute("PRAGMA user_version=1")
    with pytest.raises(ValueError, match="unsupported paper schema"):
        PaperStore(path, read_only=True)
    with sqlite3.connect(path) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == 1


def test_cohort_removal_disables_pending_fills_and_marks_but_retains_reservations(
    tmp_path,
):
    store, engine, stream, book, now = setup(tmp_path)
    engine.submit(request(stream), now=now, delivery_index=1)
    before = balances(engine, now)
    engine.replace_streams((), books=(), now=now)
    snapshot = engine.snapshot(now)
    assert snapshot.positions[0].mark_price is None
    assert "instrument_not_observed" in snapshot.positions[0].reasons
    removed = engine.submit(request(stream), now=now, delivery_index=1)
    assert not removed.filled_quantity
    engine.replace_streams((stream,), books=(book,), now=now)
    result = engine.submit(request(stream), now=now, delivery_index=1)
    assert result.filled_quantity == 1
    assert engine.snapshot(now).fills[-1].notional_quote == 102
    assert balances(engine, now)["BTC"] == before["BTC"] + 1
    store.close()


def test_initial_inventory_unknown_basis_is_not_fabricated_profit(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, inventory="2")
    )
    engine.submit(request(stream, "1", "sell"), now=now, delivery_index=1)
    position = engine.snapshot(now).positions[0]
    assert position.quantity == 1
    assert (
        position.cost_basis_quote
        is position.realized_pnl_quote
        is position.unrealized_pnl_quote
        is None
    )
    assert "unknown_initial_cost_basis" in position.reasons
    store.close()


def test_stale_selected_run_discloses_reload_and_cannot_mutate(tmp_path):
    store, engine, stream, _, now = setup(tmp_path)
    run_id = engine.snapshot(now).run.run_id
    other_store = PaperStore(tmp_path / "paper.sqlite")
    other = PaperEngine((stream,), other_store, "synthetic:fixture-v1", run_id=run_id)
    _, book, batch = observation(stream.instrument)
    deliver(other, book, batch)
    engine.submit(request(stream), now=now, delivery_index=1)
    assert "stale_run_reload_required" in other.snapshot(now).reasons
    assert not other.snapshot(now).orders
    with pytest.raises(PaperError, match="stale_run_reload_required"):
        other.submit(request(stream), now=now, delivery_index=1)
    other_store.close()
    store.close()


def test_two_simultaneous_connections_cannot_append_from_same_revision(tmp_path):
    store, engine, stream, book, now = setup(tmp_path)
    run_id = engine.snapshot(now).run.run_id
    store.close()
    barrier = Barrier(2)

    def compete():
        connection = PaperStore(tmp_path / "paper.sqlite")
        worker = PaperEngine(
            (stream,), connection, "synthetic:fixture-v1", run_id=run_id
        )
        _, _, batch = observation(stream.instrument)
        deliver(worker, book, batch)
        barrier.wait(timeout=5)
        try:
            worker.submit(request(stream), now=now, delivery_index=1)
            return "accepted"
        except PaperError as error:
            return str(error)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: compete(), range(2)))
    assert sorted(results) == ["accepted", "stale_run_reload_required"]
    reopened = PaperStore(tmp_path / "paper.sqlite")
    assert reopened.list_run_summaries()[0].revision == 1
    reopened.close()


@pytest.mark.parametrize(
    ("feed_latency", "order_latency", "filled"),
    [(0, 0, True), (30, 10, False), (30, 40, True)],
)
def test_feed_latency_activation_reconstructs_after_prior_observations(
    tmp_path, feed_latency, order_latency, filled
):
    stream, first_book, first = observation()
    store = PaperStore(tmp_path / "paper.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:fixture-v1")
    deliver(engine, first_book, first)
    _, second_book, second = observation(stream.instrument, mono=200, delivery=2)
    now = deliver(engine, second_book, second)
    engine.configure(
        config(
            stream.instrument,
            feed_latency_ns=feed_latency,
            order_latency_ns=order_latency,
        ),
        now=now,
        delivery_index=2,
    )
    engine.submit(request(stream), now=now, delivery_index=2)
    _, third_book, third = observation(stream.instrument, mono=300, delivery=3)
    last = deliver(engine, third_book, third)
    expected = engine.snapshot(last)
    if filled:
        assert expected.orders[0].status == "filled"
        assert balances(engine, last) == {"BTC": D("1"), "USD": D("900")}
        assert expected.fills[0].book_asof == second_book.asof
        assert expected.fills[0].notional_quote == 100
    else:
        assert not expected.fills
        assert "instrument_not_observed" in expected.orders[0].reasons
    engine.reset()
    deliver(engine, first_book, first)
    deliver(engine, second_book, second)
    deliver(engine, third_book, third)
    assert engine.snapshot(last) == expected
    store.close()


def test_invalid_book_and_changed_subscription_depth_do_not_replenish(tmp_path):
    store, engine, stream, book, now = setup(tmp_path)
    engine.submit(request(stream, "3"), now=now, delivery_index=1)
    invalid = replace(
        book,
        asks=(),
        quality=replace(book.quality, integrity="invalid", lifecycle="resynchronizing"),
    )
    engine.replace_streams((stream,), books=(invalid,), now=now)
    engine.replace_streams((stream,), books=(book,), now=now)
    key = replace(stream.key, depth=20)
    engine.replace_streams(
        (replace(stream, key=key),), books=(replace(book, key=key),), now=now
    )
    order = engine.submit(request(stream), now=now, delivery_index=1)
    assert order.filled_quantity == 0 and "no_unreserved_depth" in order.reasons
    assert balances(engine, now)["BTC"] == 3
    store.close()


def test_pending_arrival_at_cohort_removal_cannot_use_removed_book(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, book, now = setup(
        tmp_path, cfg=config(instrument, order_latency_ns=20)
    )
    pending = engine.submit(request(stream), now=now, delivery_index=1)
    arrival = replace(now, mono_ns=120, wall_ns=now.wall_ns + 20)
    engine.replace_streams((), books=(), now=arrival)
    order = engine.snapshot(arrival).orders[0]
    assert order.order_id == pending.order_id and order.filled_quantity == 0
    assert "instrument_not_observed" in order.reasons
    engine.replace_streams((stream,), books=(book,), now=arrival)
    assert not engine.snapshot(arrival).fills
    store.close()


def test_stale_arrival_and_clock_discontinuity_never_fill(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, order_latency_ns=20_000)
    )
    engine.submit(request(stream), now=now, delivery_index=1)
    _, book, batch = observation(instrument, mono=30_000, delivery=2)
    last = deliver(engine, book, batch)
    assert "stale" in engine.snapshot(last).orders[0].reasons
    engine.submit(request(stream), now=last, delivery_index=2)
    clock = ReplayClock()
    event = replace(batch.events[0], clock_id="new-source-clock", receive_mono_ns=1)
    clock.advance(event)
    engine.on_batch(RecordedBatch(3, (event,)), (), clock)
    assert engine.snapshot(clock.reading).orders[1].reasons == ("clock_discontinuity",)
    assert not engine.snapshot(clock.reading).fills
    store.close()


def test_native_replay_frontier_without_wall_time_uses_known_fee_date_evidence(
    tmp_path,
):
    store, engine, stream, _, now = setup(tmp_path)
    frontier = ClockReading(now.clock_id, now.mono_ns)
    order = engine.submit(request(stream), now=frontier, delivery_index=1)
    assert order.status == "filled"
    assert order.arrival_at.wall_ns == now.wall_ns
    store.close()


def test_out_of_coverage_disappearance_is_not_observed_replenishment(tmp_path):
    store, engine, stream, _, now = setup(tmp_path)
    engine.submit(request(stream, "3"), now=now, delivery_index=1)
    _, truncated, second = observation(
        stream.instrument, mono=110, delivery=2, asks=(("100", "1"),)
    )
    now = deliver(engine, truncated, second)
    _, returned, third = observation(stream.instrument, mono=120, delivery=3)
    now = deliver(engine, returned, third)
    assert (
        engine.submit(request(stream), now=now, delivery_index=3).filled_quantity == 0
    )
    # A missing interior level is inside known coverage and really retires it.
    _, missing_inside, fourth = observation(
        stream.instrument, mono=130, delivery=4, asks=(("100", "1"), ("104", "1"))
    )
    now = deliver(engine, missing_inside, fourth)
    _, reappeared, fifth = observation(stream.instrument, mono=140, delivery=5)
    now = deliver(engine, reappeared, fifth)
    order = engine.submit(request(stream, "2"), now=now, delivery_index=5)
    assert order.filled_quantity == 2
    assert engine.snapshot(now).fills[-1].notional_quote == 204
    store.close()


def test_metadata_versions_are_causal_after_rewind_and_checkpoint_restore(tmp_path):
    store, engine, stream, book, now = setup(tmp_path)
    engine.submit(request(stream), now=now, delivery_index=1)
    original = engine.snapshot(now)
    checkpoint = engine.checkpoint()
    updated = replace(stream.instrument, metadata_version="synthetic-v2")
    updated_stream = replace(stream, instrument=updated)
    engine.replace_streams(
        (updated_stream,),
        books=(replace(book, metadata_version=updated.metadata_version),),
        now=now,
    )
    engine.reset()
    _, first_book, first = observation(stream.instrument)
    deliver(engine, first_book, first)
    assert engine.snapshot(now) == original
    engine.replace_streams(
        (updated_stream,),
        books=(replace(book, metadata_version=updated.metadata_version),),
        now=now,
    )
    engine.restore(checkpoint)
    assert engine.snapshot(now) == original
    store.close()


def test_source_clock_discontinuity_freezes_run_until_explicit_new_run(tmp_path):
    store, engine, stream, book, now = setup(tmp_path)
    engine.submit(request(stream), now=now, delivery_index=1)
    original_balances = engine.snapshot(now).balances
    original_run = engine.snapshot(now).run.run_id
    _, _, batch = observation(stream.instrument, mono=200, delivery=2)
    event = replace(batch.events[0], clock_id="different-clock")
    changed = replace(
        book,
        asof=replace(book.asof, clock_id="different-clock", receive_mono_ns=200),
        quality=replace(
            book.quality,
            clock_id="different-clock",
            last_healthy_observation_mono_ns=200,
            last_book_observation_mono_ns=200,
        ),
    )
    now = deliver(engine, changed, RecordedBatch(2, (event,)))
    snapshot = engine.snapshot(now)
    assert snapshot.balances == original_balances
    assert "run_source_clock_mismatch" in snapshot.reasons
    assert snapshot.positions[0].mark_price is None
    with pytest.raises(PaperError, match="run_source_clock_mismatch"):
        engine.submit(request(stream), now=now, delivery_index=2)
    # Returning to the original clock does not silently reactivate the old run.
    _, returned, third = observation(stream.instrument, mono=300, delivery=3)
    now = deliver(engine, returned, third)
    with pytest.raises(PaperError, match="run_source_clock_mismatch"):
        engine.submit(request(stream), now=now, delivery_index=3)
    new_run = engine.configure(config(stream.instrument), now=now, delivery_index=3)
    assert new_run != original_run
    assert engine.submit(request(stream), now=now, delivery_index=3).status == "filled"
    assert (
        next(
            run for run in store.list_run_summaries() if run.run_id == original_run
        ).revision
        == 1
    )
    store.close()


def test_hyperliquid_spot_significant_digit_limits_use_native_protocol_rule(tmp_path):
    instrument = synthetic_instrument(
        constraints=OrderConstraints(price_rule="hyperliquid_significant_digits"),
    )
    instrument = replace(instrument, tick_size=None)
    store, engine, stream, _, now = setup(tmp_path, instrument=instrument)
    filled = engine.submit(
        request(stream, order_type="limit", limit_price=D("100.01")),
        now=now,
        delivery_index=1,
    )
    assert filled.status == "filled" and filled.filled_quantity == 1
    with pytest.raises(ValueError, match="hyperliquid_variable_price_rule"):
        engine.submit(
            request(stream, order_type="limit", limit_price=D("100.001")),
            now=now,
            delivery_index=1,
        )
    store.close()


def test_nonmarketable_levels_do_not_create_false_cash_exclusions(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(tmp_path, cfg=config(instrument, cash="100"))
    order = engine.submit(
        request(stream, "2", order_type="limit", limit_price=D("100")),
        now=now,
        delivery_index=1,
    )
    assert order.filled_quantity == 1
    assert "limit_price_reached" in order.reasons
    assert "insufficient_cash" not in order.reasons
    assert balances(engine, now)["USD"] == 0
    store.close()


def test_sparse_reservations_track_only_consumed_prices_and_observed_increases(
    tmp_path,
):
    stream, book, batch = observation()
    store = PaperStore(tmp_path / "paper.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:fixture-v1")
    now = deliver(engine, book, batch)
    assert not engine._reservations
    engine.configure(config(stream.instrument), now=now, delivery_index=1)
    assert not engine._reservations
    engine.submit(request(stream), now=now, delivery_index=1)
    assert len(engine._reservations) == 1
    _, decreased, second = observation(
        stream.instrument, mono=110, delivery=2, asks=(("100", ".5"), ("102", "2"))
    )
    now = deliver(engine, decreased, second)
    _, increased, third = observation(
        stream.instrument, mono=120, delivery=3, asks=(("100", "1.5"), ("102", "2"))
    )
    now = deliver(engine, increased, third)
    filled = engine.submit(request(stream), now=now, delivery_index=3)
    assert filled.filled_quantity == 1
    assert engine.snapshot(now).fills[-1].notional_quote == 100
    assert balances(engine, now)["BTC"] == 2
    assert len(engine._reservations) == 1
    store.close()


def test_delayed_books_have_conservative_byte_bound_and_checkpoint_restores_charge(
    tmp_path,
):
    asks = tuple((str(D("100") + D(".01") * index), "1") for index in range(1000))
    bids = tuple((str(D("99") - D(".01") * index), "1") for index in range(1000))
    stream, book, batch = observation(asks=asks, bids=bids)
    store = PaperStore(tmp_path / "paper.sqlite")
    engine = PaperEngine((stream,), store, "synthetic:fixture-v1")
    now = deliver(engine, book, batch)
    engine.configure(
        config(stream.instrument, feed_latency_ns=100_000), now=now, delivery_index=1
    )
    checkpoint = engine.checkpoint()
    with pytest.raises(PaperError, match="feed_latency_byte_limit"):
        for index in range(2, 100):
            deliver(
                engine,
                replace(book, revision=index),
                RecordedBatch(index, batch.events),
            )
    assert 0 < engine._delayed_bytes <= MAX_DELAYED_BOOK_BYTES
    assert not engine.snapshot(now).fills
    engine.restore(checkpoint)
    assert engine.checkpoint() == checkpoint
    engine.replace_streams((), books=(), now=now)
    assert engine._delayed_bytes == 0
    store.close()


def test_live_time_settles_arrival_without_delivery_and_archives_exact_restart_state(
    tmp_path,
):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, order_latency_ns=20)
    )
    order = engine.submit(request(stream), now=now, delivery_index=1)
    pending_archive = store.archived_snapshot(order.run_id)
    assert pending_archive.orders[0].status == "pending"
    before = replace(now, mono_ns=119, wall_ns=now.wall_ns + 19)
    engine.advance_time(before)
    assert not engine.snapshot(before).fills
    arrival = replace(now, mono_ns=120, wall_ns=now.wall_ns + 20)
    engine.advance_time(arrival)
    active = engine.snapshot(arrival)
    archived = store.archived_snapshot(order.run_id)
    assert active.delivery_index == archived.delivery_index == 1
    assert active.fills == archived.fills
    assert active.balances == archived.balances
    assert archived.fills[0].at == arrival
    assert archived.fills[0].book_asof.receive_mono_ns == 100
    assert "archived_not_live_projection" in archived.reasons
    assert "archived_not_live_projection" not in active.reasons
    later = replace(now, mono_ns=200, wall_ns=now.wall_ns + 100)
    engine.advance_time(later)
    assert store.archived_snapshot(order.run_id) == archived
    with pytest.raises(PaperError, match="cursor_clock_mismatch"):
        engine.advance_time(replace(later, clock_id="another-owner-clock"))
    store.close()
    reopened = PaperStore(tmp_path / "paper.sqlite")
    assert reopened.archived_snapshot(order.run_id) == archived
    fresh = PaperEngine(
        (stream,), reopened, "synthetic:fixture-v1", run_id=order.run_id
    )
    assert not fresh.snapshot(None).orders and not fresh.snapshot(None).balances
    assert reopened.archived_snapshot("unknown") is None
    reopened.close()


def test_rewind_cannot_replace_later_transition_archive(tmp_path):
    store, engine, stream, _, now = setup(tmp_path)
    first_order = engine.submit(request(stream), now=now, delivery_index=1)
    _, second_book, second = observation(stream.instrument, mono=200, delivery=2)
    later = deliver(engine, second_book, second)
    engine.submit(request(stream), now=later, delivery_index=2)
    archived = store.archived_snapshot(first_order.run_id)
    engine.reset()
    _, first_book, first = observation(stream.instrument)
    deliver(engine, first_book, first)
    assert len(engine.snapshot(now).fills) == 1
    assert store.archived_snapshot(first_order.run_id) == archived
    assert len(archived.fills) == 2
    assert archived.delivery_index == 2
    store.close()


def test_stale_observer_cannot_overwrite_newer_archive(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, order_latency_ns=20)
    )
    order = engine.submit(request(stream), now=now, delivery_index=1)
    other_store = PaperStore(tmp_path / "paper.sqlite")
    other = PaperEngine(
        (stream,), other_store, "synthetic:fixture-v1", run_id=order.run_id
    )
    _, first_book, first = observation(instrument)
    deliver(other, first_book, first)
    cancellation = replace(now, mono_ns=110, wall_ns=now.wall_ns + 10)
    engine.cancel(order.order_id, now=cancellation, delivery_index=1)
    archived = store.archived_snapshot(order.run_id)
    _, second_book, second = observation(instrument, mono=200, delivery=2)
    later = deliver(other, second_book, second)
    assert "stale_run_reload_required" in other.snapshot(later).reasons
    assert other_store.archived_snapshot(order.run_id) == archived
    assert archived.orders[0].status == "cancelled"
    assert not archived.fills
    other_store.close()
    store.close()


def test_legacy_paper_schema_migrates_without_losing_commands(tmp_path):
    import sqlite3

    store, engine, stream, _, now = setup(tmp_path)
    order = engine.submit(request(stream), now=now, delivery_index=1)
    store.close()
    # Strip only the v2 addition, leaving the real v1 run/command tables intact.
    with sqlite3.connect(tmp_path / "paper.sqlite") as legacy:
        legacy.execute("DROP TABLE archives")
        legacy.execute("PRAGMA user_version=1")
    reopened = PaperStore(tmp_path / "paper.sqlite")
    assert reopened.list_run_summaries()[0].revision == 1
    assert reopened.archived_snapshot(order.run_id) is None
    resumed = PaperEngine(
        (stream,), reopened, "synthetic:fixture-v1", run_id=order.run_id
    )
    _, book, batch = observation(stream.instrument)
    deliver(resumed, book, batch)
    archived = reopened.archived_snapshot(order.run_id)
    assert archived.orders[0].status == "filled"
    assert archived.fills[0].quantity == 1
    reopened.close()


def test_live_frontier_does_not_regress_for_a_queued_older_receive_stamp(tmp_path):
    instrument = synthetic_instrument()
    store, engine, stream, _, now = setup(
        tmp_path, cfg=config(instrument, feed_latency_ns=30, order_latency_ns=36)
    )
    at140 = replace(now, mono_ns=140, wall_ns=now.wall_ns + 40)
    engine.advance_time(at140)
    pending = engine.submit(request(stream), now=at140, delivery_index=1)
    at150 = replace(now, mono_ns=150, wall_ns=now.wall_ns + 50)
    engine.advance_time(at150)
    _, queued, second = observation(
        instrument, mono=145, delivery=2, asks=(("200", "1"),)
    )
    deliver(engine, queued, second)
    assert engine.snapshot(None).at.mono_ns == 150
    arrival = replace(now, mono_ns=176, wall_ns=now.wall_ns + 76)
    engine.advance_time(arrival)
    fill = engine.snapshot(arrival).fills[0]
    assert fill.order_id == pending.order_id
    assert fill.notional_quote == 100
    assert fill.book_asof.receive_mono_ns == 100
    assert engine.snapshot(arrival).delivery_index == 2
    store.close()
