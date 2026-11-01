"""Real storage/adapter/reducer/replay CLI regressions; no live network required."""

import asyncio
from contextlib import ExitStack
import fcntl
from decimal import Decimal
import json
import socket

import pytest

from src.workstation import cli
from src.workstation.demo import (
    DEMO_CLOCK,
    DEMO_SESSION,
    create_demo,
    demo_source,
    demo_session_id,
)
from src.workstation.events import BookUpdate, Control, Gap, RawFrame, Trade
from src.workstation.projection import RecordingProjection
from src.workstation.recording import RecordedBatch, Recorder, RecordingReader
from src.workstation.replay import ReplaySession


def report(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.fixture
def demo_capture(tmp_path):
    with asyncio.run(create_demo(tmp_path, duration_seconds=2)) as capture:
        yield capture


def test_demo_is_offline_real_capture_and_replay(tmp_path, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline command attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert (
        cli.main(["demo", "--root", str(tmp_path), "--duration", "2"]) == cli.COMPLETE
    )
    normalized = report(capsys)
    assert normalized["synthetic"] is True
    assert normalized["live_projection_equivalent"] is True
    assert len(normalized["books"]) == 12
    assert (
        cli.main(["demo", "--root", str(tmp_path), "--duration", "2", "--mode", "raw"])
        == cli.COMPLETE
    )
    raw = report(capsys)
    assert raw["state_hash"] == normalized["state_hash"]
    assert raw["book_hash"] == normalized["book_hash"]
    capture = tmp_path / demo_session_id(2)
    assert cli.main(["inspect", str(capture), "--limit", "5"]) == cli.COMPLETE
    inspected = report(capsys)
    assert inspected["verified"] is True
    assert len(inspected["history"]) == 5
    with RecordingReader(capture) as reader:
        assert reader.manifest.complete
        assert {item.kind for item in reader.manifest.files} >= {
            "raw",
            "normalized",
            "checkpoint",
            "catalog",
        }
        assert reader.manifest.license == "CC0-1.0"
        assert reader.manifest.checkpoint_count > 0
        assert len(reader.manifest.streams) == 24
        instruments = {
            spec.instrument.instrument_id: spec.instrument
            for spec in reader.manifest.streams
        }
        assert {item.base.ticker for item in instruments.values()} == {
            "BTC",
            "ETH",
            "SOL",
        }
        for item in instruments.values():
            assert item.quote.ticker == (
                "USD" if item.venue in ("coinbase", "kraken") else "USDT"
            )
            if item.venue == "hyperliquid":
                assert item.settlement.ticker == "USDC"
                assert item.settlement.asset_id != item.quote.asset_id
                assert item.settlement_conversion == "quanto_no_fx"
        batches = tuple(reader.iter_batches())
        events = tuple(event for batch in batches for event in batch.events)
        assert any(isinstance(event.payload, Gap) for event in events)
        assert any(event.source_mode == "rest_snapshot" for event in events)
        assert any(isinstance(event.payload, Trade) for event in events)
        assert any(
            isinstance(event.payload, Control)
            and event.payload.reason_code == "duplicate_trade"
            for event in events
        )
        assert any(b"9007199254740993" in frame.payload for frame in reader.iter_raw())
        usd_deltas = [
            event
            for event in events
            if event.key.venue in ("coinbase", "kraken")
            and isinstance(event.payload, BookUpdate)
            and event.payload.kind == "delta"
        ]
        assert {event.key.venue for event in usd_deltas} == {"coinbase", "kraken"}
        assert all(
            event.sequence is not None
            for event in usd_deltas
            if event.key.venue == "coinbase"
        )
        assert all(
            event.checksum.result == "match"
            for event in usd_deltas
            if event.key.venue == "kraken"
        )
        assert reader.manifest.code_revision.startswith("workstation-source-sha256:")


def test_source_projection_matches_each_raw_and_normalized_transaction(demo_capture):
    capture = demo_capture
    source = demo_source(duration_seconds=2)
    projection = RecordingProjection(source.streams)
    positions = {}
    expected = []
    delivery = 0
    for item in source.inputs():
        if isinstance(item, RawFrame):
            segment, mode = positions[item.stream_id]
            events = projection.process_raw(item, segment_id=segment, source_mode=mode)
        else:
            first = item[0]
            positions[first.stream_id] = first.segment_id, first.source_mode
            events = projection.apply_batch(RecordedBatch(delivery + 1, item))
        if events:
            delivery += 1
            expected.append((projection.state_hash, projection.book_hash))
    reader = capture.reader
    for mode in ("raw", "normalized"):
        session = ReplaySession(reader, mode=mode)
        try:
            for hashes in expected:
                assert len(session.step()) == 1
                assert (session.state_hash, session.book_hash) == hashes
            assert not session.step()
            assert session.state_hash == capture.state_hash
            assert session.book_hash == capture.book_hash
        finally:
            session.close()


def test_replay_speed_seek_and_scripted_controls_are_real(
    demo_capture, capsys, monkeypatch
):
    capture = demo_capture
    for mode, speed in (("raw", "1000"), ("normalized", "10000")):
        assert (
            cli.main(
                ["replay", str(capture.reader.path), "--mode", mode, "--speed", speed]
            )
            == cli.COMPLETE
        )
        result = report(capsys)
        assert result["state_hash"] == capture.state_hash
        assert result["book_hash"] == capture.book_hash
    reader = capture.reader
    batches = tuple(reader.iter_batches())
    selected = batches[8]
    fresh = ReplaySession(reader)
    try:
        fresh.step(9)
        selected_hashes = fresh.state_hash, fresh.book_hash
    finally:
        fresh.close()
    for mode in ("raw", "normalized"):
        session = ReplaySession(reader, mode=mode)
        try:
            asyncio.run(session.run_to_end(speed=Decimal("1000")))
            first = selected.events[0]
            session.seek(first.ingest_ordinal, first.event_index)
            assert (session.state_hash, session.book_hash) == selected_hashes
            asyncio.run(session.run_to_end())
            assert session.state_hash == capture.state_hash
        finally:
            session.close()
    commands = iter(
        ["step 2", "status", "play 1000", "pause", "seek 1", "run 1000", "quit"]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(commands))
    assert cli.main(["replay", str(reader.path), "--interactive"]) == cli.COMPLETE
    result = report(capsys)
    assert result["state_hash"] == capture.state_hash


@pytest.mark.parametrize("kind", ["raw", "normalized", "catalog"])
def test_inspect_and_replay_reject_truncated_storage(demo_capture, capsys, kind):
    capture = demo_capture
    path = capture.reader.path
    damaged = path / next(
        item.path for item in capture.reader.manifest.files if item.kind == kind
    )
    capture.close()
    original = damaged.read_bytes()
    damaged.write_bytes(original[: len(original) // 2])
    for command in ("inspect", "replay"):
        assert cli.main([command, str(path)]) == cli.FAILURE
        error = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert error["complete"] is False
        assert error["status"] == "failure"


def test_incomplete_capture_has_explicit_nonzero_diagnostic_status(tmp_path, capsys):
    source = demo_source()
    recorder = Recorder(
        tmp_path, session_id=DEMO_SESSION, clock_id=DEMO_CLOCK, streams=source.streams
    )
    first = next(source.inputs())
    assert isinstance(first, tuple)
    recorder.append_events(first)
    recorder.mark_incomplete("deliberately_interrupted_fixture")
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert cli.main(["inspect", str(recorder.path)]) == cli.FAILURE
    capsys.readouterr()
    assert (
        cli.main(["inspect", str(recorder.path), "--allow-incomplete"])
        == cli.INCOMPLETE
    )
    inspected = report(capsys)
    assert inspected["manifest"]["complete"] is False
    assert (
        cli.main(["replay", str(recorder.path), "--allow-incomplete"]) == cli.INCOMPLETE
    )
    assert report(capsys)["capture_complete"] is False


def test_record_requires_network_optin_before_dependencies_or_discovery(capsys):
    assert cli.main(["record", "--duration", "1"]) == cli.FAILURE
    error = json.loads(capsys.readouterr().err)
    assert "requires --network" in error["error"]


def test_fixture_version_rejected_without_capture_creation(tmp_path):
    fixture = tmp_path / "bad-source.json"
    fixture.write_text('{"schema_version": 999, "fixture_id": "unsupported"}')
    with pytest.raises(ValueError, match="unsupported demo fixture"):
        asyncio.run(create_demo(tmp_path / "captures", fixture_path=fixture))
    assert not (tmp_path / "captures").exists()


def test_demo_duration_identity_and_incompatible_cache_are_explicit(
    tmp_path, monkeypatch
):
    from src.workstation import demo

    with ExitStack() as captures:
        one = captures.enter_context(
            asyncio.run(create_demo(tmp_path, duration_seconds=1))
        )
        two = captures.enter_context(
            asyncio.run(create_demo(tmp_path, duration_seconds=2))
        )
        assert one.reader.path != two.reader.path
        assert two.reader.manifest.batch_count - one.reader.manifest.batch_count == 24
        same = captures.enter_context(
            asyncio.run(create_demo(tmp_path, duration_seconds=1))
        )
        assert same.reader.manifest == one.reader.manifest
        assert same.state_hash == one.state_hash
        monkeypatch.setattr(
            demo,
            "source_tree_revision",
            lambda: "workstation-source-sha256:" + "0" * 64,
        )
        with pytest.raises(ValueError, match="different fixture/configuration"):
            asyncio.run(create_demo(tmp_path, duration_seconds=1))


def test_unsupported_interactive_control_is_not_reported_success(
    demo_capture, capsys, monkeypatch
):
    capture = demo_capture
    commands = iter(["unsupported_operation", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(commands))
    assert (
        cli.main(["replay", str(capture.reader.path), "--interactive"]) == cli.FAILURE
    )
    captured = capsys.readouterr()
    assert json.loads(captured.err)["status"] == "invalid_control"


def test_demo_reader_retention_lifetime_and_failed_cache_cleanup(tmp_path, monkeypatch):
    for _ in range(2):  # Fresh generation, then reuse of the finalized capture.
        with asyncio.run(create_demo(tmp_path, duration_seconds=1)) as capture:
            session = ReplaySession(capture.reader)
            try:
                asyncio.run(session.run_to_end())
                assert session.state_hash == capture.state_hash
            finally:
                session.close()
            active_path = capture.reader.path / ".active"
            with active_path.open("rb") as active:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capture.close()
        with active_path.open("rb") as active:
            fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(active, fcntl.LOCK_UN)
    from src.workstation import demo

    monkeypatch.setattr(
        demo, "source_tree_revision", lambda: "workstation-source-sha256:" + "0" * 64
    )
    with pytest.raises(ValueError):
        asyncio.run(create_demo(tmp_path, duration_seconds=1))
    with active_path.open("rb") as active:
        fcntl.flock(active, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(active, fcntl.LOCK_UN)
