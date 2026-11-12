"""Inclusive native retention floors are independent of pagination and health."""

from collections import deque
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.workstation import market_presentation as presentation
from src.workstation.analytics import AnalyticsEngine
from src.workstation.api import MarketDataRequest
from src.workstation.candles import session_candles
from src.workstation.events import (
    Control,
    Gap,
    SubscriptionKey,
    Trade,
    synthetic_envelope,
)
from src.workstation.instruments import AssetIdentity, Instrument
from src.workstation.recording import RecordedBatch, StreamSpec
from src.workstation.replay import ReplayClock


class RetainedFeed:
    def __init__(self):
        primary = Instrument(
            "test:BTCUSD",
            "1",
            "test",
            "public",
            "BTCUSD",
            AssetIdentity("BTC", "BTC"),
            AssetIdentity("USD", "USD"),
            Decimal("0.01"),
            Decimal("0.001"),
        )
        peer = replace(primary, instrument_id="peer:BTCUSD", venue="peer")
        self.products = (primary, peer)
        self.streams = tuple(
            StreamSpec(
                f"{index}-{channel}",
                product,
                SubscriptionKey.for_instrument(product, channel=channel),
                "test",
                "test",
            )
            for index, product in enumerate(self.products)
            for channel in ("book", "trades")
        )
        self.engine = AnalyticsEngine(self.streams)
        self.history = presentation.AdmittedTradeHistory(self.streams, self.engine)
        self.clock = ReplayClock()
        self.session = SimpleNamespace(
            id="feed",
            generation=1,
            replay=None,
            delivery=0,
            history_floor=1,
            history_floor_event=-1,
            history=deque(),
            trade_history=self.history,
            streams=self.streams,
            key=self.streams[0].key,
            live_books=(),
            _now=lambda: self.clock.reading,
            source_identity="retained-feed-source",
        )

    def event(self, key, *, stream="0-trades", payload=None, epoch=0):
        spec = next(item for item in self.streams if item.stream_id == stream)
        mono = self.session.delivery + 1
        return synthetic_envelope(
            spec.instrument,
            payload
            if payload is not None
            else Trade(key, key, Decimal("100.00"), Decimal("1.000"), "base", "buy"),
            key=spec.key,
            stream_id=spec.stream_id,
            ordinal=mono,
            mono_ns=mono,
            receive_wall_ns=1700000000000000000 + mono,
            epoch=epoch,
        )

    def deliver(self, *events):
        batch = RecordedBatch(
            self.session.delivery + 1,
            tuple(
                replace(event, event_index=index) for index, event in enumerate(events)
            ),
        )
        self.clock.advance(batch.events[-1])
        self.engine.on_batch(batch, (), self.clock)
        self.history.on_batch(batch, (), self.clock)
        self.session.delivery = batch.delivery_index
        self.session.history.append(batch)
        return batch

    def page(self, cursor=None, *, limit=100):
        cursor = cursor or {"delivery": "0", "event": -1}
        return presentation.market_data(
            self.session,
            MarketDataRequest(
                session_id=self.session.id,
                generation="1",
                after_delivery=cursor["delivery"],
                after_event=cursor["event"],
                limit=limit,
            ),
        )

    def trim_raw(self, count):
        # The live owner's raw-history contract: retire inclusive event offsets
        # only after raw batches have actually been removed.
        for _ in range(count):
            removed = self.session.history.popleft()
            self.session.history_floor = removed.delivery_index + 1
            self.session.history_floor_event = len(removed.events) - 1
        self.history.prune_through(
            self.session.history_floor - 1, self.session.history_floor_event
        )


def assert_accounting(history):
    assert len(history.trades) == len(history.sizes) == len(history.admitted)
    assert history.bytes == sum(history.sizes)
    assert list(history.sizes) == [
        len(repr(item).encode("utf-8")) for item in history.trades
    ]
    assert history.admitted == {
        (item.delivery, item.event_index): item for item in history.trades
    }
    assert all(cursor > history.floor_cursor for cursor in history.admitted)
    assert all(
        (item.delivery, item.event_index)
        > history.stream_floors.get(str(item.event.stream_id), (0, -1))
        for item in history.trades
    )


def trade_ids(page):
    return [item["trade"]["trade_id"] for item in page["trades"]]


def test_caught_up_rollover_reports_retirement_without_false_gap(monkeypatch):
    monkeypatch.setattr(presentation, "MAX_TRADE_ROWS", 2)
    feed = RetainedFeed()
    feed.deliver(feed.event("first"))
    feed.deliver(feed.event("second"))
    before = feed.page()
    assert trade_ids(before) == ["first", "second"]
    feed.deliver(feed.event("third"))
    after = feed.page(before["cursor"])
    assert trade_ids(after) == ["third"]
    assert not after["gap"] and not after["has_more"]
    assert after["retention"] == {
        "floor": {"delivery": "1", "event": 0},
        "stream_floors": {},
    }
    drained = feed.page(after["cursor"])
    assert drained["trades"] == [] and not drained["gap"]
    assert drained["retention"] == after["retention"]
    assert trade_ids(feed.page()) == ["second", "third"]
    candles = session_candles(feed.history, feed.products[0], "1m", 300)
    assert candles["retention"]["retained_trades"] == 2
    assert Decimal(candles["candles"][0]["volume"]) == Decimal("2")
    assert_accounting(feed.history)


def test_same_batch_floor_and_pages_never_resurrect_prefix(monkeypatch):
    monkeypatch.setattr(presentation, "MAX_TRADE_ROWS", 2)
    feed = RetainedFeed()
    feed.deliver(*(feed.event(key) for key in ("zero", "one", "two")))
    first = feed.page(limit=1)
    assert first["gap"] and first["has_more"]
    assert first["retention"]["floor"] == {"delivery": "1", "event": 0}
    assert first["cursor"] == {"delivery": "1", "event": 1}
    assert trade_ids(first) == ["one"]
    feed.history.prune_through(1, 1)
    second = feed.page(first["cursor"], limit=1)
    assert not second["gap"] and not second["has_more"]
    assert trade_ids(second) == ["two"]
    assert second["retention"]["floor"] == first["cursor"]
    assert trade_ids(feed.page(limit=1)) == ["two"]
    assert_accounting(feed.history)


def test_stream_retirement_preserves_peers_and_independent_book_health():
    feed = RetainedFeed()
    feed.deliver(feed.event("primary"), feed.event("peer", stream="1-trades"))
    before = feed.page()
    feed.deliver(feed.event("book-reset", stream="0-book", payload=Control("reset")))
    assert trade_ids(feed.page()) == ["primary", "peer"]
    feed.deliver(feed.event("gap", payload=Gap("missing_trades")))
    page = feed.page(before["cursor"])
    assert not page["gap"]
    assert [item["kind"] for item in page["boundaries"]] == ["gap"]
    assert page["retention"]["floor"] == {"delivery": "0", "event": -1}
    assert page["retention"]["stream_floors"]["0-trades"] == {
        "delivery": "3",
        "event": 0,
    }
    assert "1-trades" not in page["retention"]["stream_floors"]
    assert trade_ids(feed.page()) == ["peer"]
    feed.deliver(feed.event("new-primary"))
    assert trade_ids(feed.page()) == ["peer", "new-primary"]
    saved = feed.history.checkpoint()
    feed.history.reset()
    feed.history.restore(saved)
    assert feed.history.checkpoint() == saved
    assert trade_ids(feed.page()) == ["peer", "new-primary"]
    assert_accounting(feed.history)


def test_source_transition_floor_keeps_transition_print_and_peer():
    feed = RetainedFeed()
    feed.deliver(feed.event("old"), feed.event("peer", stream="1-trades"))
    before = feed.page()
    feed.deliver(feed.event("new-epoch", epoch=1))
    page = feed.page(before["cursor"])
    assert not page["gap"]
    assert trade_ids(page) == ["new-epoch"]
    assert page["retention"]["stream_floors"]["0-trades"] == {
        "delivery": "2",
        "event": -1,
    }
    assert trade_ids(feed.page()) == ["peer", "new-epoch"]
    saved = feed.history.checkpoint()
    feed.history.restore(saved)
    assert trade_ids(feed.page()) == ["peer", "new-epoch"]
    assert_accounting(feed.history)


def test_pruning_raw_floor_preserves_exact_accounting_checkpoint_and_rewind():
    feed = RetainedFeed()
    empty = feed.history.checkpoint()
    feed.deliver(feed.event("first"), feed.event("second"))
    prefix = feed.history.checkpoint()
    feed.deliver(feed.event("retained"))
    feed.trim_raw(1)
    saved = feed.history.checkpoint()
    assert feed.history.floor_cursor == (1, 1)
    assert feed.history.truncated
    assert trade_ids(feed.page()) == ["retained"]
    assert feed.page()["retention"]["floor"] == {"delivery": "1", "event": 1}
    assert_accounting(feed.history)
    feed.history.prune_through(1, 0)
    feed.history.prune_through(1, 1)
    assert feed.history.checkpoint() == saved
    for cursor in ((-1, 0), (3, 0), (1, -2)):
        with pytest.raises(ValueError, match="retention cursor"):
            feed.history.prune_through(*cursor)
        assert feed.history.checkpoint() == saved
    feed.history.restore(prefix)
    assert feed.history.floor_cursor == (0, -1)
    assert len(feed.history.trades) == 2
    feed.history.on_batch(feed.session.history[-1], (), feed.clock)
    feed.history.prune_through(1, 1)
    assert feed.history.checkpoint() == saved
    feed.history.reset()
    feed.history.restore(saved)
    assert feed.history.checkpoint() == saved
    assert_accounting(feed.history)
    feed.history.restore(empty)
    assert feed.history.checkpoint() == empty
    assert_accounting(feed.history)


def test_empty_trade_history_still_tracks_raw_floor_and_caught_up_cursor():
    feed = RetainedFeed()
    feed.deliver(feed.event("trade"))
    before = feed.page()
    feed.deliver(feed.event("heartbeat", payload=Control("heartbeat")))
    feed.trim_raw(2)
    assert feed.history.floor_cursor == (2, 0)
    assert not feed.history.trades and feed.history.bytes == 0
    page = feed.page(before["cursor"])
    assert page["gap"] and page["trades"] == []
    assert (
        page["cursor"]
        == page["retention"]["floor"]
        == {
            "delivery": "2",
            "event": 0,
        }
    )
    assert not feed.page(page["cursor"])["gap"]
    saved = feed.history.checkpoint()
    feed.history.restore(saved)
    assert feed.history.checkpoint() == saved
    assert_accounting(feed.history)


def test_oversized_trade_retires_cursor_even_without_stored_rows(monkeypatch):
    feed = RetainedFeed()
    monkeypatch.setattr(presentation, "MAX_TRADE_BYTES", 1)
    feed.deliver(feed.event("oversized"))
    assert feed.history.floor_cursor == (1, 0)
    assert feed.history.truncated
    assert feed.page()["retention"]["floor"] == {"delivery": "1", "event": 0}
    assert feed.page()["trades"] == []
    assert_accounting(feed.history)


def test_same_batch_stream_floor_does_not_retire_peer_at_that_cursor():
    feed = RetainedFeed()
    feed.deliver(feed.event("old"))
    feed.deliver(
        feed.event("peer", stream="1-trades"),
        feed.event("transition", epoch=1),
    )
    page = feed.page(limit=1)
    assert not page["gap"]
    assert page["retention"]["stream_floors"]["0-trades"] == {
        "delivery": "2",
        "event": 0,
    }
    assert trade_ids(page) == ["peer"]
    next_page = feed.page(page["cursor"], limit=1)
    assert trade_ids(next_page) == ["transition"]
    assert next_page["cursor"] == {"delivery": "2", "event": 1}
    saved = feed.history.checkpoint()
    feed.history.restore(saved)
    assert trade_ids(feed.page()) == ["peer", "transition"]
    assert_accounting(feed.history)


def test_byte_rollover_retires_only_the_evicted_prefix(monkeypatch):
    feed = RetainedFeed()
    feed.deliver(feed.event("first"))
    feed.deliver(feed.event("second"))
    # Current exact row accounting supplies the budget, rather than assuming
    # Python repr widths or Decimal canonicalization.
    monkeypatch.setattr(presentation, "MAX_TRADE_BYTES", feed.history.bytes)
    before = feed.page()
    feed.deliver(feed.event("third"))
    page = feed.page(before["cursor"])
    assert not page["gap"]
    assert page["retention"]["floor"] == {"delivery": "1", "event": 0}
    assert trade_ids(feed.page()) == ["second", "third"]
    assert feed.history.bytes <= presentation.MAX_TRADE_BYTES
    assert_accounting(feed.history)


def test_display_frontier_closes_native_candle_on_tick_without_new_prints():
    feed = RetainedFeed()
    assert feed.page()["display_frontier_ns"] is None
    trade = feed.event("first")
    feed.deliver(trade)
    before = feed.page()
    assert before["display_frontier_ns"] == str(trade.receive_wall_ns)
    candles = session_candles(feed.history, feed.products[0], "1m", 300)
    assert len(candles["candles"]) == 1
    assert not candles["candles"][0]["closed"]
    boundary = int(candles["candles"][0]["end_ns"])
    tick = replace(
        feed.event("tick", payload=Control("clock_tick")),
        receive_mono_ns=trade.receive_mono_ns + boundary - trade.receive_wall_ns,
        receive_wall_ns=boundary,
    )
    feed.deliver(tick)
    page = feed.page(before["cursor"])
    assert page["display_frontier_ns"] == str(boundary)
    assert page["trades"] == [] and page["boundaries"] == []
    assert page["cursor"] == {"delivery": "2", "event": 0}
    assert page["highwater"] == "2"
    assert not page["gap"] and not page["has_more"]
    assert page["retention"] == before["retention"]
    closed = session_candles(feed.history, feed.products[0], "1m", 300)
    assert len(closed["candles"]) == 1
    assert closed["candles"][0]["closed"]
    assert closed["candles"][0]["volume"] == candles["candles"][0]["volume"]
    drained = feed.page(page["cursor"])
    assert drained["display_frontier_ns"] == str(boundary)
    assert drained["cursor"] == page["cursor"] and drained["trades"] == []
    feed.trim_raw(2)
    retired = feed.page(page["cursor"])
    assert retired["display_frontier_ns"] == str(boundary)
    assert retired["retention"]["floor"] == page["cursor"]
    assert retired["trades"] == [] and not retired["gap"]
    assert_accounting(feed.history)


def test_display_frontier_is_source_projected_on_every_paginated_page():
    feed = RetainedFeed()
    first = feed.event("first")
    feed.deliver(first)
    feed.deliver(feed.event("second"))
    projected = first.receive_wall_ns + 60_000_000_123
    tick = replace(
        feed.event("tick", payload=Control("clock_tick")),
        receive_mono_ns=first.receive_mono_ns + 60_000_000_123,
        # A receive-wall correction must not replace the original clock anchor.
        receive_wall_ns=projected + 3_600_000_000_000,
    )
    feed.deliver(tick)
    first_page = feed.page(limit=1)
    second_page = feed.page(first_page["cursor"], limit=1)
    tick_page = feed.page(second_page["cursor"], limit=1)
    caught_up = feed.page(tick_page["cursor"], limit=1)
    assert trade_ids(first_page) == ["first"]
    assert trade_ids(second_page) == ["second"]
    assert tick_page["trades"] == caught_up["trades"] == []
    assert first_page["has_more"] and second_page["has_more"]
    assert not tick_page["has_more"] and not caught_up["has_more"]
    for page in (first_page, second_page, tick_page, caught_up):
        assert page["display_frontier_ns"] == str(projected)
        assert page["highwater"] == "3"
        assert not page["gap"]
        assert page["retention"] == first_page["retention"]
        assert "receive_clock_discontinuity" in page["display_time_reasons"]
    assert_accounting(feed.history)


def test_display_frontier_restores_resets_and_changes_with_source_clock():
    feed = RetainedFeed()
    empty = feed.history.checkpoint()
    first = feed.event("first")
    feed.deliver(first)
    prefix = feed.history.checkpoint()
    tick = replace(
        feed.event("tick", payload=Control("clock_tick")),
        receive_mono_ns=first.receive_mono_ns + 60_000_000_123,
        receive_wall_ns=first.receive_wall_ns + 60_000_000_123,
    )
    feed.deliver(tick)
    saved = feed.history.checkpoint()
    assert feed.page()["display_frontier_ns"] == str(tick.receive_wall_ns)
    feed.history.restore(prefix)
    assert feed.page()["display_frontier_ns"] == str(first.receive_wall_ns)
    feed.history.restore(saved)
    assert feed.page()["display_frontier_ns"] == str(tick.receive_wall_ns)
    feed.history.reset()
    assert feed.page()["display_frontier_ns"] is None
    feed.history.restore(empty)
    assert feed.page()["display_frontier_ns"] is None
    feed.history.restore(saved)
    regressed = replace(
        feed.event("regressed", payload=Control("clock_tick")),
        receive_mono_ns=tick.receive_mono_ns - 1,
        receive_wall_ns=tick.receive_wall_ns + 99,
    )
    feed.deliver(regressed)
    assert feed.page()["display_frontier_ns"] == str(tick.receive_wall_ns)
    changed = replace(
        feed.event("changed", payload=Control("clock_tick")),
        clock_id="new-source-clock",
        receive_mono_ns=1,
        receive_wall_ns=first.receive_wall_ns - 60_000_000_000,
    )
    feed.deliver(changed)
    page = feed.page()
    assert page["display_frontier_ns"] == str(changed.receive_wall_ns)
    assert page["trades"] == []
    assert "source_clock_changed" in page["display_time_reasons"]
    assert page["retention"]["floor"] == {"delivery": "4", "event": -1}
    changed_checkpoint = feed.history.checkpoint()
    feed.history.reset()
    feed.history.restore(changed_checkpoint)
    assert feed.page()["display_frontier_ns"] == str(changed.receive_wall_ns)
    assert_accounting(feed.history)
