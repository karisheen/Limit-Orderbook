"""Canonical cursor and durable managed capture consumer regressions, no network."""

import asyncio
from collections import deque
from dataclasses import replace
from decimal import Decimal
import json

from fastapi.testclient import TestClient
import pytest

from src.workstation.adapter_catalog import AdapterCatalog
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.api import (
    MarketDataRequest,
    RecordingStart,
    RecordingStop,
    create_app,
)
from src.workstation.demo_memory import MemoryReplaySource
from src.workstation.events import AssetContext, ReferencePrice, SubscriptionKey, Trade
from src.workstation.ingestion import FeedSupervisor
from src.workstation.recording import RecordedBatch, RecordingReader
from src.workstation.runtime import ClientSession, RuntimeFailure, TerminalRuntime
from src.workstation.subscriptions import SubscriptionRegistry


@pytest.fixture(scope="module")
def source():
    return MemoryReplaySource.demo()


def request(session, cursor=None, limit=2):
    cursor = cursor or {"delivery": "0", "event": -1}
    return MarketDataRequest(
        session_id=session.id,
        generation=str(session.generation),
        after_delivery=cursor["delivery"],
        after_event=cursor["event"],
        limit=limit,
    )


def replay_runtime(source, tmp_path):
    runtime = TerminalRuntime(state_dir=tmp_path)
    runtime.demo = source
    runtime.demo_id = source.descriptor.source_id
    runtime.demo_ready.set()
    runtime.sources[runtime.demo_id] = {
        "id": runtime.demo_id,
        "mode": "demo",
        "label": "Synthetic test source",
        "status": "ready",
        "synthetic": True,
        "recording": False,
        "duration_ns": None,
        "streams": len(source.descriptor.streams),
        "reasons": [],
    }
    return runtime


def test_market_cursor_drains_every_event_in_one_batch_and_reports_eviction(
    source, tmp_path
):
    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, "live", "live")
        runtime.sessions[session.id] = session
        session.status = "live"
        session.streams = source.descriptor.streams
        event = next(
            event
            for batch in source.iter_batches()
            for event in batch.events
            if isinstance(event.payload, Trade)
        )
        session.choose(str(event.key.instrument_id))
        session.live_clock.advance(event)
        events = tuple(
            replace(
                event,
                event_index=index,
                payload=replace(
                    event.payload, trade_id=str(index), dedup_key=str(index)
                ),
            )
            for index in range(11)
        )
        session.history = deque([RecordedBatch(1, events)])
        session.delivery = 1
        try:
            cursor = None
            trades = []
            while True:
                page = await runtime.market_data(request(session, cursor))
                assert page["gap"] is False
                trades.extend(page["trades"])
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            assert [item["trade"]["trade_id"] for item in trades] == list(
                map(str, range(11))
            )
            assert [item["cursor"]["event"] for item in trades] == list(range(11))
            assert cursor == {"delivery": "1", "event": 10}
            assert not (await runtime.market_data(request(session, cursor)))["trades"]
            assert all(isinstance(item["base_quantity"], str) for item in trades)

            # The only retained delivery can also be oversized and entirely lost.
            session.history.clear()
            session.history_floor = 2
            session.history_floor_event = 10
            lost = await runtime.market_data(
                request(session, {"delivery": "1", "event": 3})
            )
            assert lost["gap"] is True
            assert lost["cursor"] == cursor
            assert not (await runtime.market_data(request(session, lost["cursor"])))[
                "gap"
            ]
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_market_replay_never_crosses_current_cursor_and_reset_fences_old_generation(
    source, tmp_path
):
    async def scenario():
        runtime = replay_runtime(source, tmp_path)
        try:
            session = await runtime.create()
            first_request = request(session, limit=2000)
            page = await runtime.market_data(first_request)
            highwater = int(page["highwater"])
            expected = [
                (batch.delivery_index, index)
                for batch in source.iter_batches(stop_delivery=highwater)
                for index, event in enumerate(batch.events)
                if isinstance(event.payload, Trade)
                and str(event.key.instrument_id)
                in {item["instrument_id"] for item in page["instruments"]}
            ]
            actual = [
                (int(item["cursor"]["delivery"]), item["cursor"]["event"])
                for item in page["trades"]
            ]
            assert actual == expected
            for book in page["books"]:
                assert "eligibility" in book
                assert "depth_limit" in book["presentation"]
                for level in book["bids"] + book["asks"]:
                    assert (
                        level["base_quantity"] is None
                        or Decimal(level["base_quantity"]) >= 0
                    )
            async with session.actions:
                await session.control(str(session.generation), "reset")
            with pytest.raises(RuntimeFailure, match="obsolete"):
                await runtime.market_data(first_request)
            reset = await runtime.market_data(request(session, page["cursor"]))
            assert reset["gap"] is True
            assert reset["highwater"] == "0"
            assert reset["cursor"] == {"delivery": "0", "event": -1}
            assert reset["trades"] == reset["books"] == []
        finally:
            await runtime.close()

    asyncio.run(scenario())


class SourceSupervisor(FeedSupervisor):
    """Production decoder/reducer with explicit source-schema input, no sockets."""

    async def run(self):
        self._running = True
        self._start_epoch(False)
        try:
            if self.key.channel == "book":
                payload = {
                    "channel": "l2Book",
                    "data": {
                        "coin": self.instrument.native_symbol,
                        "time": 1700000000000,
                        "levels": [
                            [{"px": "60000", "sz": "1", "n": 1}],
                            [{"px": "60001", "sz": "2", "n": 1}],
                        ],
                    },
                }
            elif self.key.channel == "reference":
                payload = {
                    "channel": "allMids",
                    "data": {"mids": {self.instrument.native_symbol: "60000.00000001"}},
                }
            elif self.key.channel == "context":
                payload = {
                    "channel": "activeAssetCtx",
                    "data": {
                        "coin": self.instrument.native_symbol,
                        "ctx": {
                            "markPx": "60000.00000001",
                            "oraclePx": "60000",
                            "funding": "0.00001",
                            "openInterest": "1000",
                        },
                    },
                }
            else:
                payload = {
                    "channel": "trades",
                    "data": [
                        {
                            "coin": self.instrument.native_symbol,
                            "side": "B",
                            "px": "60001",
                            "sz": "0.1",
                            "time": 1700000000000,
                            "hash": "capture-test",
                            "tid": 1,
                        }
                    ],
                }
            self.fixture_payload = json.dumps(payload).encode()
            self._receive(
                self.fixture_payload,
                "ws",
                "wss://api.hyperliquid.xyz/ws",
                self.epoch,
            )
            frame = self.queue.get_nowait()
            self.queued_bytes -= len(frame.payload)
            await self._process(frame)
            await asyncio.Event().wait()
        finally:
            await self._shutdown()


def capture_runtime(tmp_path):
    adapter = HyperliquidAdapter()
    metadata = adapter.parse_metadata(
        {
            "collateralToken": 0,
            "universe": [
                {"name": "BTC", "szDecimals": 5},
                {"name": "ETH", "szDecimals": 4},
            ],
        },
        {
            "tokens": [
                {"name": "USDC", "index": 0, "tokenId": "0xusdc", "szDecimals": 8}
            ],
            "universe": [],
        },
        fetch_wall_ns=123,
        payload_hash="synthetic-managed-capture",
    )
    catalog = AdapterCatalog()
    catalog.adapters["hyperliquid"] = adapter
    catalog.instruments.apply_metadata(metadata)
    registry = SubscriptionRegistry(
        catalog, supervisor_factory=SourceSupervisor, lock_path=tmp_path / "collector"
    )
    runtime = TerminalRuntime(registry=registry, state_dir=tmp_path / "state")
    session = ClientSession(runtime, "live", "live")
    runtime.sessions[session.id] = session
    session.status = "live"
    session.instrument_id = str(metadata.instruments[0].instrument_id)
    session.live_keys = frozenset(
        registry.catalog.canonical_key(
            SubscriptionKey.for_instrument(instrument, channel=channel)
        )
        for instrument in metadata.instruments
        for channel in ("book", "trades", "reference", "context")
    )
    return runtime, registry, session


async def wait_capture(runtime, capture_id):
    active = runtime.captures.active[capture_id]
    async with asyncio.timeout(5):
        while active.recorder.last_delivery_index < 2 * len(active.binding.leases):
            await asyncio.sleep(0)


@pytest.mark.parametrize("scope", ["instrument", "cohort"])
@pytest.mark.parametrize("incomplete", [False, True])
def test_recording_lifecycle_scope_restart_and_real_replay(tmp_path, incomplete, scope):
    async def scenario():
        runtime, registry, session = capture_runtime(tmp_path)
        try:
            action = RecordingStart(session_id=session.id, generation="1", scope=scope)
            expected_keys = frozenset(
                key
                for key in session.live_keys
                if scope == "cohort" or str(key.instrument_id) == session.instrument_id
            )
            started = await runtime.recording_action(action)
            capture_id = started["recording_id"]
            assert started["recordings"][0]["session_id"] == session.id
            started_row = started["recordings"][0]
            assert started_row["scope"] == scope
            assert started_row["instruments"] == sorted(
                {str(key.instrument_id) for key in expected_keys}
            )
            assert started_row["channels"] == [
                {"venue": venue, "instrument_id": instrument_id, "stream": stream}
                for venue, instrument_id, stream in sorted(
                    (key.venue, str(key.instrument_id), key.channel)
                    for key in expected_keys
                )
            ]
            await wait_capture(runtime, capture_id)
            with pytest.raises(RuntimeFailure, match="already"):
                await runtime.recording_action(action)
            active = runtime.captures.active[capture_id]
            assert {lease.key for lease in active.binding.leases} == expected_keys
            expected_raw = {
                registry._entries[key].supervisor.fixture_payload
                for key in expected_keys
            }
            # Selection changes update the session, not the independent capture leases.
            session.instrument_id = next(
                str(key.instrument_id)
                for key in session.live_keys
                if str(key.instrument_id) != session.instrument_id
            )
            session.live_keys = frozenset(
                key
                for key in session.live_keys
                if str(key.instrument_id) == session.instrument_id
            )
            assert {lease.key for lease in active.binding.leases} == expected_keys
            frozen = runtime.captures.listing()["recordings"][0]
            for field in ("scope", "instruments", "channels"):
                assert frozen[field] == started_row[field]
            assert active.recorder.path.is_relative_to(runtime.state_dir / "recordings")
            if incomplete:
                active.recorder.mark_incomplete("synthetic_missing_raw")
            stopped = await runtime.recording_action(
                RecordingStop(session_id=session.id, generation="1", id=capture_id),
                stop=True,
            )
            row = stopped["recordings"][0]
            assert row["status"] == ("incomplete" if incomplete else "complete")
            assert row["session_id"] is None
            if incomplete:
                assert row["source_id"] is None
                assert "synthetic_missing_raw" in row["reasons"]
            else:
                with RecordingReader(active.recorder.path) as reader:
                    assert reader.manifest.raw_count == len(expected_keys)
                    assert {
                        spec.key for spec in reader.manifest.streams
                    } == expected_keys
                    assert {
                        frame.payload for frame in reader.iter_raw()
                    } == expected_raw
                    payloads = [
                        event.payload
                        for batch in reader.iter_batches()
                        for event in batch.events
                    ]
                    assert any(isinstance(value, ReferencePrice) for value in payloads)
                    assert any(isinstance(value, AssetContext) for value in payloads)
                replay = await runtime.create("replay", row["source_id"])
                assert {spec.key for spec in replay.streams} == expected_keys
                assert {
                    str(item.instrument_id)
                    for item in runtime.source_instruments[row["source_id"]]
                } == set(started_row["instruments"])
                market = await runtime.market_data(request(replay))
                assert len(market["trades"]) == 1
                assert market["trades"][0]["base_quantity"] == "0.1"
        finally:
            await runtime.close()
            await registry.close()
        restarted = TerminalRuntime(state_dir=tmp_path / "state")
        try:
            await restarted.captures.load()
            persisted = restarted.captures.listing()["recordings"][0]
            assert persisted == row
            if not incomplete:
                replay = await restarted.create("replay", persisted["source_id"])
                assert (await restarted.market_data(request(replay)))["trades"]
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_cohort_missing_context_raw_evidence_is_not_registered(tmp_path, monkeypatch):
    from src.workstation.subscriptions import RecorderBinding

    original = RecorderBinding._raw

    def lose_context_raw(binding, frame):
        if json.loads(frame.payload)["channel"] != "activeAssetCtx":
            original(binding, frame)

    monkeypatch.setattr(RecorderBinding, "_raw", lose_context_raw)

    async def scenario():
        runtime, registry, session = capture_runtime(tmp_path)
        try:
            started = await runtime.recording_action(
                RecordingStart(session_id=session.id, generation="1", scope="cohort")
            )
            capture_id = started["recording_id"]
            await wait_capture(runtime, capture_id)
            stopped = await runtime.recording_action(
                RecordingStop(session_id=session.id, generation="1", id=capture_id),
                stop=True,
            )
            (row,) = stopped["recordings"]
            assert row["scope"] == "cohort"
            assert len(row["channels"]) == 8
            assert row["status"] == "incomplete"
            assert "missing_required_raw_evidence" in row["reasons"]
            assert row["source_id"] is None
            assert not runtime.capture_map
        finally:
            await runtime.close()
            await registry.close()
        restarted = TerminalRuntime(state_dir=tmp_path / "state")
        try:
            await restarted.captures.load()
            (row,) = restarted.captures.listing()["recordings"]
            assert row["status"] in ("incomplete", "error")
            assert row["source_id"] is None
            assert not restarted.capture_map
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_recording_scope_bounds_are_enforced_before_attachment(tmp_path):
    from src.workstation.capture_service import MAX_CAPTURE_CHANNELS

    async def scenario():
        runtime, registry, session = capture_runtime(tmp_path)
        key = next(iter(session.live_keys))
        session.instrument_id = str(key.instrument_id)
        session.live_keys = frozenset(
            replace(key, channel=f"extra-{index}")
            for index in range(MAX_CAPTURE_CHANNELS + 1)
        )
        try:
            with pytest.raises(RuntimeFailure, match="native bounds"):
                await runtime.recording_action(
                    RecordingStart(
                        session_id=session.id, generation="1", scope="cohort"
                    )
                )
            assert not runtime.captures.active
            assert not runtime.captures.rows
            assert not runtime.captures.root.exists()
            assert not registry._raw_sinks and not registry._event_sinks
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_session_close_finalizes_recording_and_foreign_owner_cannot_stop(tmp_path):
    async def scenario():
        runtime, registry, session = capture_runtime(tmp_path)
        try:
            started = await runtime.recording_action(
                RecordingStart(session_id=session.id, generation="1")
            )
            capture_id = started["recording_id"]
            await wait_capture(runtime, capture_id)
            other = ClientSession(runtime, "live", "live")
            other.status = "live"
            runtime.sessions[other.id] = other
            with pytest.raises(RuntimeFailure, match="not found"):
                await runtime.recording_action(
                    RecordingStop(session_id=other.id, generation="1", id=capture_id),
                    stop=True,
                )
            await runtime.remove(session.id)
            assert runtime.captures.listing()["recordings"][0]["status"] == "complete"
            assert not runtime.captures.active
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_recording_http_rejects_paths_and_untrusted_origin(tmp_path):
    runtime = TerminalRuntime(state_dir=tmp_path)
    client = TestClient(create_app(runtime), base_url="http://127.0.0.1")
    action = {"session_id": "a" * 32, "generation": "1"}
    try:
        for scope in ("session", "all", "", None):
            assert (
                client.post(
                    "/api/v1/recordings/start", json={**action, "scope": scope}
                ).status_code
                == 422
            )
        assert RecordingStart(**action).scope == "instrument"
        assert (
            client.post(
                "/api/v1/recordings/start", json={**action, "path": "/tmp/escape"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/recordings/stop", json={**action, "id": "../escape"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/market-data", json={**action, "after_delivery": "-1"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/recordings", headers={"origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/v1/recordings").json() == {
            "version": "1",
            "recordings": [],
        }
    finally:
        client.close()
        asyncio.run(runtime.close())


def test_restart_catalogs_interrupted_capture_without_following_symlinks(tmp_path):
    capture_id = "a" * 32
    directory = tmp_path / "recordings" / capture_id
    directory.mkdir(parents=True)
    (directory / "capture.json").write_text(
        json.dumps(
            {"session_id": "native-session", "instrument_id": "native-instrument"}
        )
    )
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "recordings" / ("b" * 32)).symlink_to(
        external, target_is_directory=True
    )

    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        try:
            await runtime.captures.load()
            rows = runtime.captures.listing()["recordings"]
            assert len(rows) == 1
            assert rows[0]["id"] == capture_id
            assert rows[0]["status"] == "incomplete"
            assert rows[0]["reasons"] == ["capture_interrupted"]
            assert rows[0]["source_id"] is None
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("inverse", "documented", "base", "quote"),
    [
        (False, True, "20", "1000000"),
        (True, True, "0.0004", "20"),
        (True, False, None, None),
    ],
)
def test_market_contract_valuation_and_unknown_side_are_not_fabricated(
    source, tmp_path, inverse, documented, base, quote
):
    async def scenario():
        runtime = TerminalRuntime(state_dir=tmp_path)
        session = ClientSession(runtime, "live", "live")
        runtime.sessions[session.id] = session
        session.status = "live"
        original = next(
            event
            for batch in source.iter_batches()
            for event in batch.events
            if isinstance(event.payload, Trade)
        )
        original_spec = next(
            spec
            for spec in source.descriptor.streams
            if spec.stream_id == original.stream_id
        )
        instrument = replace(
            original_spec.instrument,
            market_type="perpetual",
            quantity_unit="contracts",
            inverse=inverse,
            contract_multiplier=Decimal("10"),
            contract_value_currency=(
                original_spec.instrument.quote.asset_id
                if inverse
                else original_spec.instrument.base.asset_id
            ),
            contract_specification_source="synthetic contract documentation"
            if documented
            else None,
        )
        session.streams = tuple(
            replace(spec, instrument=instrument)
            for spec in source.descriptor.streams
            if spec.instrument.instrument_id == instrument.instrument_id
        )
        session.choose(str(instrument.instrument_id))
        event = replace(
            original,
            payload=replace(
                original.payload,
                price=Decimal("50000"),
                quantity=Decimal("2"),
                quantity_unit="contracts",
                taker_side="unknown",
                side_provenance="unknown",
            ),
        )
        session.live_clock.advance(event)
        session.delivery = 1
        session.history.append(RecordedBatch(1, (event,)))
        try:
            (trade,) = (await runtime.market_data(request(session)))["trades"]
            assert trade["base_quantity"] == base
            assert trade["quote_notional"] == quote
            assert trade["trade"]["quantity"] == "2"
            assert trade["trade"]["taker_side"] == "unknown"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_cancelled_capture_start_joins_attachment_before_session_shutdown(
    tmp_path, monkeypatch
):
    from src.workstation.subscriptions import RecorderBinding

    async def scenario():
        runtime, registry, session = capture_runtime(tmp_path)
        attached, release = asyncio.Event(), asyncio.Event()
        original = RecorderBinding.start

        async def delayed(*args, **kwargs):
            binding = await original(*args, **kwargs)
            attached.set()
            await release.wait()
            return binding

        monkeypatch.setattr(RecorderBinding, "start", delayed)
        operation = asyncio.create_task(
            runtime.recording_action(
                RecordingStart(session_id=session.id, generation="1")
            )
        )
        try:
            await asyncio.wait_for(attached.wait(), 5)
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            (capture_id,) = runtime.captures.active
            await wait_capture(runtime, capture_id)
            await runtime.remove(session.id)
            assert runtime.captures.listing()["recordings"][0]["status"] == "complete"
            assert not runtime.captures.active
            assert not registry._raw_sinks and not registry._event_sinks
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())
