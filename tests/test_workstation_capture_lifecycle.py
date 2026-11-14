"""Real recorder ownership across cancelled drain, disk seal and publication."""

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace

import anyio
import pytest

from src.workstation.events import SubscriptionKey
from src.workstation.recording import RecordingReader
from src.workstation.runtime import TerminalRuntime
from test_replay_binding import assert_complete_drain, registry_fixture, snapshot


def capture_runtime(tmp_path):
    registry, instruments = registry_fixture(tmp_path)
    runtime = TerminalRuntime(registry=registry, state_dir=tmp_path / "state")
    return runtime, registry, instruments


async def start_capture(runtime, registry, instrument, owner="browser"):
    session = SimpleNamespace(
        id=owner,
        mode="live",
        instrument_id=str(instrument.instrument_id),
        live_keys=frozenset((SubscriptionKey.for_instrument(instrument),)),
    )
    result = await runtime.captures.start(session)
    capture_id = result["recording_id"]
    active = runtime.captures.active[capture_id]
    feed = registry._entries[active.binding.leases[0].key].supervisor
    await asyncio.wait_for(feed.started.get(), 5)
    return session, capture_id, active, feed


@pytest.mark.parametrize("stage", ("drain", "finalize", "catalog"))
@pytest.mark.parametrize("cancellation", ("asyncio", "anyio"))
@pytest.mark.parametrize("operation", ("stop", "close_session", "close"))
def test_cancelled_capture_stop_owns_drain_finalization_and_publication(
    tmp_path, monkeypatch, stage, cancellation, operation
):
    async def scenario():
        runtime, registry, instruments = capture_runtime(tmp_path)
        entered = asyncio.Event()
        release = threading.Event()
        request = None
        feed = None
        loop = asyncio.get_running_loop()
        try:
            session, capture_id, active, feed = await start_capture(
                runtime, registry, instruments["BTC"]
            )
            raw = await feed.emit(snapshot())
            if stage == "drain":
                feed.allow_stop.clear()
                entered = feed.stopping
            else:
                original = (
                    active.recorder._finish
                    if stage == "finalize"
                    else runtime._capture_descriptor
                )

                def held(*args):
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(10):
                        raise TimeoutError("capture boundary was not released")
                    return original(*args)

                monkeypatch.setattr(
                    active.recorder if stage == "finalize" else runtime,
                    "_finish" if stage == "finalize" else "_capture_descriptor",
                    held,
                )

            cancelled = asyncio.Event()
            scopes = []

            async def stop():
                if operation == "stop":
                    await runtime.captures.stop(session, capture_id)
                elif operation == "close_session":
                    await runtime.captures.close_session(session.id)
                else:
                    await runtime.captures.close()

            async def invoke():
                if cancellation == "asyncio":
                    await stop()
                else:
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        try:
                            await stop()
                        except asyncio.CancelledError:
                            cancelled.set()
                            raise

            request = asyncio.create_task(invoke())
            await asyncio.wait_for(entered.wait(), 5)
            stop_task = active.stop_task
            assert stop_task is not None
            for _ in range(2):
                if cancellation == "asyncio":
                    request.cancel()
                else:
                    scopes[0].cancel()
                await asyncio.sleep(0)
                assert not request.done()
                assert not stop_task.done()
                assert runtime.captures.lock.locked()
                assert runtime.captures.active[capture_id] is active
                (row,) = runtime.captures.listing()["recordings"]
                assert row["session_id"] == session.id
                assert row["status"] == "recording"
                assert row["source_id"] is None
                assert not runtime.capture_map

            feed.allow_stop.set()
            release.set()
            if cancellation == "asyncio":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request, 5)
            else:
                await asyncio.wait_for(request, 5)
                assert cancelled.is_set()
            assert not runtime.captures.active
            assert not runtime.captures.lock.locked()
            assert active.stop_task is stop_task
            assert stop_task.done() and not stop_task.cancelled()
            (row,) = runtime.captures.listing()["recordings"]
            assert row["status"] == "complete"
            assert row["session_id"] is None
            assert runtime.capture_map[row["source_id"]] == active.recorder.path
            assert runtime.sources[row["source_id"]]["status"] == "ready"
            manifest = await active.binding.stop()
            assert_complete_drain(active.recorder, manifest, (raw,), instruments["BTC"])
            # Repeated lifecycle cleanup does not re-register or refinalize.
            await runtime.captures.close_session(session.id)
            await runtime.captures.close()
            assert len(runtime.capture_map) == 1
        finally:
            release.set()
            if feed is not None:
                feed.allow_stop.set()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_cancelled_service_close_finishes_every_owned_capture(tmp_path):
    async def scenario():
        runtime, registry, instruments = capture_runtime(tmp_path)
        closing = None
        feeds = []
        try:
            captures = []
            for symbol in ("BTC", "ETH"):
                capture = await start_capture(
                    runtime, registry, instruments[symbol], owner=symbol
                )
                captures.append(capture)
                feeds.append(capture[3])
                await capture[3].emit(snapshot(symbol=symbol))
            feeds[0].allow_stop.clear()
            closing = asyncio.create_task(runtime.captures.close())
            await asyncio.wait_for(feeds[0].stopping.wait(), 5)
            for _ in range(2):
                closing.cancel()
                await asyncio.sleep(0)
                assert not closing.done()
                assert len(runtime.captures.active) == 2
                assert runtime.captures.lock.locked()
            feeds[0].allow_stop.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(closing, 5)
            assert not runtime.captures.active
            assert len(runtime.capture_map) == 2
            assert all(
                row["status"] == "complete"
                for row in runtime.captures.listing()["recordings"]
            )
            for _session, _capture_id, active, _feed in captures:
                with RecordingReader(active.recorder.path) as reader:
                    assert reader.manifest.raw_count == 1
                assert not active.binding._unsubscribe
        finally:
            for feed in feeds:
                feed.allow_stop.set()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_coalesced_capture_stop_preserves_incomplete_evidence(tmp_path):
    async def scenario():
        runtime, registry, instruments = capture_runtime(tmp_path)
        requests = []
        feed = None
        try:
            _session, capture_id, active, feed = await start_capture(
                runtime, registry, instruments["BTC"]
            )
            await feed.emit(snapshot())
            active.recorder.mark_incomplete("required_input_missing")
            feed.allow_stop.clear()
            requests.append(asyncio.create_task(runtime.captures._stop(capture_id)))
            await asyncio.wait_for(feed.stopping.wait(), 5)
            owned = active.stop_task
            requests.append(asyncio.create_task(runtime.captures._stop(capture_id)))
            await asyncio.sleep(0)
            assert active.stop_task is owned
            for _ in range(2):
                requests[0].cancel()
                await asyncio.sleep(0)
                assert not requests[0].done()
                assert not requests[1].done()
            feed.allow_stop.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(requests[0], 5)
            await asyncio.wait_for(requests[1], 5)
            assert not runtime.captures.active
            (row,) = runtime.captures.listing()["recordings"]
            assert row["status"] == "incomplete"
            assert row["reasons"] == ["required_input_missing"]
            assert row["source_id"] is None
            assert not runtime.capture_map
            with RecordingReader(active.recorder.path, allow_incomplete=True) as reader:
                assert not reader.manifest.complete
                assert reader.manifest.raw_count == 1
                assert reader.manifest.incomplete_reasons == ("required_input_missing",)
        finally:
            if feed is not None:
                feed.allow_stop.set()
            await asyncio.gather(*requests, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


def test_timed_out_writer_keeps_capture_owned_until_actual_exit(tmp_path, monkeypatch):
    async def scenario():
        runtime, registry, instruments = capture_runtime(tmp_path)
        entered = asyncio.Event()
        release = threading.Event()
        stopping = None
        loop = asyncio.get_running_loop()
        try:
            session, capture_id, active, feed = await start_capture(
                runtime, registry, instruments["BTC"]
            )
            binding = active.binding
            write_raw = active.recorder._write_raw

            def held_write_raw(frame):
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(10):
                    raise TimeoutError("writer was not released")
                return write_raw(frame)

            # Establish the native writer barrier before starting finalization.
            # _finish is too late: the deadline includes queue drain and disk
            # sealing, and an abandoned writer deliberately skips _finish.
            monkeypatch.setattr(active.recorder, "_write_raw", held_write_raw)
            await feed.emit(snapshot())
            await asyncio.wait_for(entered.wait(), 5)
            active.recorder.limits = replace(
                active.recorder.limits, finalize_timeout=0.01
            )
            assert active.recorder._thread.is_alive()
            stopping = asyncio.create_task(runtime.captures.stop(session, capture_id))
            async with asyncio.timeout(5):
                while (
                    "finalize_timeout" not in active.recorder.health.incomplete_reasons
                ):
                    await asyncio.sleep(0.001)
            manifest = await binding.stop()
            assert not manifest.complete
            assert "finalize_timeout" in manifest.incomplete_reasons
            stop_task = active.stop_task
            assert stop_task is not None
            for _ in range(2):
                stopping.cancel()
                await asyncio.sleep(0)
                assert not stopping.done()
                assert not stop_task.done()
                assert runtime.captures.lock.locked()
                assert active.recorder._thread.is_alive()
                assert runtime.captures.active[capture_id] is active
                assert active.binding is binding
                assert binding.recorder is active.recorder
                (row,) = runtime.captures.listing()["recordings"]
                assert row["status"] == "error"
                assert row["session_id"] == session.id
                assert row["source_id"] is None
                assert not runtime.capture_map
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(stopping, 5)
            assert not active.recorder._thread.is_alive()
            assert not runtime.captures.active
            assert not runtime.captures.lock.locked()
            assert active.stop_task is stop_task
            assert stop_task.done() and not stop_task.cancelled()
            assert await binding.stop() is manifest
            assert not (active.recorder.path / "manifest.json").exists()
            (row,) = runtime.captures.listing()["recordings"]
            assert row["status"] == "incomplete"
            assert row["session_id"] is None
            assert row["source_id"] is None
            assert "finalize_timeout" in row["reasons"]
            assert not runtime.capture_map
        finally:
            release.set()
            if stopping is not None:
                await asyncio.gather(stopping, return_exceptions=True)
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reselect", (False, True))
def test_capture_keeps_native_deliveries_through_analytical_overflow(
    tmp_path, reselect
):
    async def scenario():
        runtime, registry, instruments = capture_runtime(tmp_path)
        runtime._start_live_source()
        try:
            instrument = instruments["BTC"]
            session = await runtime.create(
                "live", "live", str(instrument.instrument_id)
            )
            started = await runtime.recording_action(
                SimpleNamespace(
                    session_id=session.id,
                    generation=str(session.generation),
                    scope="instrument",
                )
            )
            capture_id = started["recording_id"]
            active = runtime.captures.active[capture_id]
            key = next(
                lease.key
                for lease in active.binding.leases
                if lease.key.channel == "book"
            )
            feed = registry._entries[key].supervisor
            frozen_streams = frozenset(active.binding._streams)
            raws = []
            unsubscribe = registry.subscribe_raw(raws.append)
            try:
                # Stall only the analytical consumer; source decoding and the
                # independently leased recorder keep their complete native input.
                async with session.lock:
                    for index in range(session.live_queue.maxsize + 1):
                        await feed.emit(snapshot(price=60000 + index))
                assert session.status == "unavailable"
                assert "analytical_queue_overflow" in session.error
                assert active.recorder.health.incomplete_reasons == ()
                assert runtime.captures.active[capture_id] is active
                if reselect:
                    async with session.actions:
                        await runtime.select(
                            session, str(session.generation), session.instrument_id
                        )
                    assert session.status == "live"
                    assert frozenset(active.binding._streams) == frozen_streams
                await feed.emit(snapshot(price=61000))
                stopped = await runtime.recording_action(
                    SimpleNamespace(
                        session_id=session.id,
                        generation=str(session.generation),
                        id=capture_id,
                    ),
                    stop=True,
                )
            finally:
                unsubscribe()
            (row,) = stopped["recordings"]
            assert row["status"] == "complete"
            assert row["scope"] == "instrument"
            assert row["instruments"] == [str(instrument.instrument_id)]
            assert row["session_id"] is None
            assert row["source_id"] in runtime.capture_map
            manifest = await active.binding.stop()
            assert_complete_drain(active.recorder, manifest, raws, instrument)
        finally:
            await runtime.close()
            await registry.close()

    asyncio.run(scenario())
