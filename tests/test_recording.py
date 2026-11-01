"""Storage consumer contracts. All market values here are explicitly synthetic."""

import asyncio
from dataclasses import replace
from decimal import Decimal
import gzip
import json
import sqlite3
import threading
import time

import pytest

from src.workstation import recording
from src.workstation.adapters.hyperliquid import HyperliquidAdapter
from src.workstation.events import (
    BookUpdate,
    Control,
    Gap,
    LevelChange,
    StreamId,
    SubscriptionKey,
    synthetic_envelope,
    synthetic_frame,
)
from src.workstation.instruments import synthetic_instrument
from src.workstation.recording import (
    Recorder,
    RecorderLimits,
    RecordingError,
    RecordingReader,
    StreamSpec,
    recover_recordings,
)


def fixture_stream(stream="synthetic-stream"):
    instrument = synthetic_instrument(
        venue="hyperliquid", api_family="info", native_symbol="@1"
    )
    key = SubscriptionKey.for_instrument(instrument, depth=20)
    reducer = HyperliquidAdapter().new_reducer(instrument, key)
    return StreamSpec(
        StreamId(stream), instrument, key, reducer.adapter_revision, reducer.config_hash
    )


def fixture_message(spec, ordinal=1):
    raw = synthetic_frame(
        b'{"synthetic_price":"99.00","size":"1.000"}',
        ordinal=ordinal,
        mono_ns=ordinal,
        stream_id=spec.stream_id,
    )
    event = synthetic_envelope(
        spec.instrument,
        BookUpdate(
            "snapshot",
            (
                LevelChange("bid", Decimal("99.00"), Decimal("1.000")),
                LevelChange("ask", Decimal("101.00"), Decimal("2.000")),
            ),
            20,
        ),
        key=spec.key,
        stream_id=spec.stream_id,
        ordinal=ordinal,
        source_mode="ws_snapshot",
        raw_ref=raw.ref(),
    )
    return raw, event


def recorder_at(root, *, streams=None, **kwargs):
    return Recorder(
        root,
        session_id="synthetic-session",
        clock_id="synthetic-clock",
        streams=(fixture_stream(),) if streams is None else streams,
        provenance="generated storage consumer fixture",
        license="CC0-1.0",
        **kwargs,
    )


def capture(root):
    recorder = recorder_at(root)
    raw, event = fixture_message(fixture_stream())
    recorder.append_raw(raw)
    recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    return recorder.path, manifest, raw, event


def test_lossless_raw_parquet_checkpoint_catalog_and_duckdb(tmp_path):
    path, manifest, raw, event = capture(tmp_path)
    assert {f.kind for f in manifest.files} == {
        "raw",
        "normalized",
        "checkpoint",
        "checkpoint_aux",
        "catalog",
    }
    assert manifest.raw_count == manifest.event_count == manifest.batch_count == 1
    assert manifest.checkpoint_count == 1
    assert isinstance(manifest.to_dict()["created_wall_ns"], str)
    with RecordingReader(path) as reader:
        assert list(reader.iter_raw()) == [raw]
        assert reader.raw_frame(raw.stream_id, raw.ingest_ordinal) == raw
        assert reader.raw_frame(raw.stream_id, 999) is None
        (batch,) = reader.iter_batches()
        assert batch.delivery_index == 1
        assert batch.events == (event,)
        assert str(batch.events[0].payload.changes[0].price) == "99.00"
        assert str(batch.events[0].payload.changes[0].quantity) == "1.000"
        assert reader.query_history(limit=1) == (event,)
        assert reader.query_history(start_ordinal=2) == ()
        (checkpoint,) = reader.iter_checkpoints()
        assert checkpoint.delivery_index == 1
        assert checkpoint.observer_state
        assert b"99.00" in checkpoint.checkpoint.reducer_state
        assert reader.resolve_delivery(1) == 1
    raw_file = next(f for f in manifest.files if f.kind == "raw")
    assert gzip.decompress((path / raw_file.path).read_bytes()).endswith(b"\n")


def test_receive_order_is_not_cross_stream_delivery_order_or_capture_gap(tmp_path):
    first, second = fixture_stream(), fixture_stream("second-stream")
    recorder = recorder_at(tmp_path, streams=(first, second))
    raw1, event1 = fixture_message(first, 1)
    raw2, event2 = fixture_message(second, 9)  # Other, unrecorded owners can use 2..8.
    recorder.append_raw(raw1)
    recorder.append_raw(raw2)
    recorder.append_events((event2,))
    recorder.append_events((event1,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert manifest.gaps == ()
    with RecordingReader(recorder.path) as reader:
        assert [r.ingest_ordinal for r in reader.iter_raw()] == [1, 9]
        assert [b.events[0].ingest_ordinal for b in reader.iter_batches()] == [9, 1]
        assert reader.resolve_delivery(1) == 2
        assert [e.ingest_ordinal for e in reader.query_history()] == [9, 1]


def test_dynamic_stream_registration_is_immutable_and_precedes_data(tmp_path):
    first, second = fixture_stream(), fixture_stream("dynamic-stream")
    recorder = recorder_at(tmp_path)
    raw, event = fixture_message(second)
    with pytest.raises(RecordingError, match="registered"):
        recorder.append_raw(raw)
    recorder.register_stream(second)
    recorder.register_stream(second)
    with pytest.raises(RecordingError, match="replaced"):
        recorder.register_stream(replace(second, config_hash="changed"))
    recorder.append_raw(raw)
    recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.streams == (first, second)
    with RecordingReader(recorder.path) as reader:
        assert next(reader.iter_batches()).events == (event,)


def test_full_source_transaction_is_preserved_and_seek_is_atomic(tmp_path):
    spec = fixture_stream()
    recorder = recorder_at(tmp_path)
    raw, event = fixture_message(spec)
    # Two immutable snapshots in one source callback remain one transaction.
    second = replace(event, event_index=1)
    recorder.append_raw(raw)
    recorder.append_events((event, second))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    with RecordingReader(recorder.path) as reader:
        assert reader.resolve_delivery(1, 1) == reader.resolve_delivery(1, 0) == 1
        assert len(next(reader.iter_batches()).events) == 2
        with pytest.raises(RecordingError, match="transaction"):
            reader.resolve_delivery(1, 2)


def test_capture_loss_is_sticky_manifest_evidence(tmp_path):
    recorder = recorder_at(tmp_path)
    spec = fixture_stream()
    gap = synthetic_envelope(
        spec.instrument,
        Gap("synthetic_loss", 3, 7, True, True, (spec.stream_id,)),
        key=spec.key,
        ordinal=8,
        source_mode="ws_snapshot",
    )
    recorder.append_events((gap,))
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert manifest.gaps == (gap,)
    with pytest.raises(RecordingError, match="incomplete"):
        RecordingReader(recorder.path)
    with RecordingReader(recorder.path, allow_incomplete=True) as reader:
        assert next(reader.iter_batches()).events == (gap,)


@pytest.mark.parametrize(
    "kind", ["raw", "normalized", "checkpoint", "checkpoint_aux", "catalog"]
)
@pytest.mark.parametrize("operation", ["truncate", "flip"])
def test_reader_rejects_corruption_and_truncation(tmp_path, kind, operation):
    path, manifest, _, _ = capture(tmp_path)
    file = path / next(f.path for f in manifest.files if f.kind == kind)
    data = file.read_bytes()
    file.write_bytes(
        data[:-1] if operation == "truncate" else bytes([data[0] ^ 1]) + data[1:]
    )
    with pytest.raises((RecordingError, recording.sqlite3.Error)):
        RecordingReader(path)


def test_manifest_unknown_schema_and_traversal_fail_closed(tmp_path):
    path, _, _, _ = capture(tmp_path)
    manifest_path = path / "manifest.json"
    original = json.loads(manifest_path.read_bytes())
    invalid = dict(original, schema_version="999")
    manifest_path.write_text(json.dumps(invalid))
    with pytest.raises(RecordingError, match="manifest"):
        RecordingReader(path)
    original["files"][0]["path"] = "../outside.gz"
    manifest_path.write_text(json.dumps(original))
    with pytest.raises(RecordingError):
        RecordingReader(path)


def test_reader_rejects_symlink_escape_even_when_bytes_match(tmp_path):
    root = tmp_path / "captures"
    path, manifest, _, _ = capture(root)
    file = path / next(f.path for f in manifest.files if f.kind == "raw")
    outside = tmp_path / "outside.gz"
    outside.write_bytes(file.read_bytes())
    file.unlink()
    file.symlink_to(outside)
    with pytest.raises(RecordingError, match="symlink"):
        RecordingReader(path)


def test_query_limits_and_indexed_rotation(tmp_path):
    spec = fixture_stream()
    recorder = recorder_at(
        tmp_path,
        limits=RecorderLimits(
            raw_rotation_bytes=1, parquet_batch_count=1, checkpoint_interval_batches=1
        ),
    )
    for ordinal in range(1, 5):
        raw, event = fixture_message(spec, ordinal)
        recorder.append_raw(raw)
        recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert len([f for f in manifest.files if f.kind == "raw"]) == 4
    with RecordingReader(recorder.path) as reader:
        assert reader.raw_frame(spec.stream_id, 4).ingest_ordinal == 4
        assert [
            e.ingest_ordinal
            for e in reader.query_history(start_ordinal=2, end_ordinal=3)
        ] == [2, 3]
        assert [
            b.delivery_index
            for b in reader.iter_batches(start_delivery=2, stop_delivery=3)
        ] == [2, 3]
        for limit in (0, -1, 10001):
            with pytest.raises(ValueError):
                reader.query_history(limit=limit)


def test_queue_byte_overflow_is_visible_and_incomplete(tmp_path):
    recorder = recorder_at(tmp_path, limits=RecorderLimits(queue_bytes=32))
    with pytest.raises(RecordingError, match="capacity"):
        recorder.append_raw(fixture_message(fixture_stream())[0])
    assert recorder.health.incomplete_reasons == ("queue_overflow",)
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert "queue_overflow" in manifest.incomplete_reasons


def test_blocked_writer_count_bound_and_finalize_deadline(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = Recorder._write_raw

    def blocked(self, frame):
        entered.set()
        release.wait(5)
        original(self, frame)

    monkeypatch.setattr(Recorder, "_write_raw", blocked)
    recorder = recorder_at(
        tmp_path, limits=RecorderLimits(queue_count=1, finalize_timeout=0.02)
    )
    recorder.append_raw(fixture_message(fixture_stream())[0])
    assert entered.wait(2)
    try:
        with pytest.raises(RecordingError, match="capacity"):
            recorder.append_raw(fixture_message(fixture_stream(), 2)[0])
        assert recorder.health.high_water_count == 1
        assert recorder.health.high_water_bytes > 0
        manifest = asyncio.run(recorder.finalize())
        assert not manifest.complete
        assert "finalize_timeout" in manifest.incomplete_reasons
        assert not (recorder.path / "manifest.json").exists()
        assert recover_recordings(tmp_path) == ()  # Still locked by blocked writer.
    finally:
        release.set()
        recorder._thread.join(5)
    recovered = recover_recordings(tmp_path)
    assert len(recovered) == 1
    assert recovered[0].name.startswith("quarantine-")


def test_writer_exception_propagates_to_future_offers_and_final_status(
    tmp_path, monkeypatch
):
    failed = threading.Event()

    def broken(self, frame):
        failed.set()
        raise OSError("synthetic disk fault")

    monkeypatch.setattr(Recorder, "_write_raw", broken)
    recorder = recorder_at(tmp_path)
    recorder.append_raw(fixture_message(fixture_stream())[0])
    assert failed.wait(2)
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert any(
        "writer_failed:OSError" in reason for reason in manifest.incomplete_reasons
    )
    with pytest.raises(RecordingError):
        recorder.append_raw(fixture_message(fixture_stream(), 2)[0])


def test_low_disk_and_managed_budget_fail_before_capture(tmp_path, monkeypatch):
    usage = recording.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        recording.shutil,
        "disk_usage",
        lambda path: type(usage)(usage.total, usage.used, 0),
    )
    with pytest.raises(RecordingError, match="low_disk"):
        recorder_at(tmp_path)


def test_total_budget_rejects_initialization(tmp_path):
    with pytest.raises(RecordingError, match="budget"):
        recorder_at(tmp_path, limits=RecorderLimits(managed_budget_bytes=1))


def test_retention_preserves_active_and_open_reader_sessions(tmp_path):
    first = Recorder(
        tmp_path,
        session_id="first",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=1),
    )
    assert asyncio.run(first.finalize()).complete
    reader = RecordingReader(first.path)
    second = Recorder(
        tmp_path,
        session_id="second",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=1),
    )
    assert first.path.exists()  # Open reader holds a shared retention lock.
    third = Recorder(
        tmp_path,
        session_id="third",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=1),
    )
    assert second.path.exists()  # An active writer cannot be evicted.
    reader.close()
    assert asyncio.run(second.finalize()).complete
    assert asyncio.run(third.finalize()).complete
    fourth = Recorder(
        tmp_path,
        session_id="fourth",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=1),
    )
    assert not first.path.exists()
    assert asyncio.run(fourth.finalize()).complete


def test_recovery_quarantines_partial_files_without_claiming_success(tmp_path):
    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    (interrupted / ".active").touch()
    (interrupted / "raw.jsonl.gz.partial").write_bytes(b"not a gzip")
    (recovered,) = recover_recordings(tmp_path)
    assert recovered.name.startswith("quarantine-interrupted-")
    assert (recovered / "raw.jsonl.gz.partial").read_bytes() == b"not a gzip"
    assert not (recovered / "manifest.json").exists()


def test_concurrent_finalize_has_single_immutable_result(tmp_path):
    recorder = recorder_at(tmp_path)

    async def finish():
        results = await asyncio.gather(recorder.finalize(), recorder.finalize())
        assert results[0] is results[1]
        return results[0]

    assert asyncio.run(finish()).complete


def test_invalid_session_paths_are_rejected_without_side_effects(tmp_path):
    for session in ("../escape", "/absolute", "a/b", "."):
        with pytest.raises(ValueError):
            Recorder(tmp_path, session_id=session, clock_id="clock")
    assert list(tmp_path.iterdir()) == []


def test_serialization_runs_only_on_owned_worker(tmp_path, monkeypatch):
    spec = fixture_stream()
    raw, event = fixture_message(spec)
    recorder = recorder_at(tmp_path)
    original = recording.wire.dumps
    threads = []

    def observed(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(recording.wire, "dumps", observed)
    recorder.append_raw(raw)
    recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert threads
    assert all(name.startswith("recorder-") for name in threads)


def test_oversized_row_fails_writer_without_claiming_complete(tmp_path):
    recorder = recorder_at(tmp_path, limits=RecorderLimits(max_row_bytes=16))
    recorder.append_raw(fixture_message(fixture_stream())[0])
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert any("oversized raw row" in reason for reason in manifest.incomplete_reasons)


def test_reader_rejects_incompatible_manifest_adapter_revision(tmp_path):
    path, _, _, _ = capture(tmp_path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["streams"][0]["adapter_revision"] = "unsupported-future-revision"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RecordingError, match="verification"):
        RecordingReader(path)


@pytest.mark.parametrize(
    ("missing_reference", "forged_connection"),
    [(True, False), (True, True), (False, True)],
)
def test_reader_requires_source_reference_and_receive_provenance(
    tmp_path, missing_reference, forged_connection
):
    recorder = recorder_at(tmp_path)
    raw, event = fixture_message(fixture_stream())
    event = replace(
        event,
        raw_ref=None if missing_reference else raw.ref(),
        connection_id="forged" if forged_connection else event.connection_id,
    )
    recorder.append_raw(raw)
    recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    reason = (
        "raw event reference" if missing_reference else "receive provenance mismatch"
    )
    with pytest.raises(RecordingError, match=reason):
        RecordingReader(recorder.path)


@pytest.mark.parametrize("kind", ["book", "ack", "heartbeat", "rejection"])
def test_missing_public_raw_never_verifies_as_complete(tmp_path, kind):
    recorder = recorder_at(tmp_path)
    _, event = fixture_message(fixture_stream())
    if kind != "book":
        event = replace(event, payload=Control(kind, "synthetic_public"))
    recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert "missing_required_raw_evidence" in manifest.incomplete_reasons
    with pytest.raises(RecordingError, match="incomplete"):
        RecordingReader(recorder.path)
    with pytest.raises(RecordingError, match="missing required raw evidence"):
        RecordingReader(recorder.path, allow_incomplete=True)


def test_local_origin_cannot_override_indexed_public_raw(tmp_path):
    spec = fixture_stream()
    recorder = recorder_at(tmp_path)
    raw, _ = fixture_message(spec)
    local = synthetic_envelope(
        spec.instrument,
        Gap("synthetic_local", None, None, True, False, (spec.stream_id,)),
        key=spec.key,
        source_mode="ws_snapshot",
    )
    recorder.append_raw(raw)
    recorder.append_events((local,))
    manifest = asyncio.run(recorder.finalize())
    assert not manifest.complete
    assert "conflicting_local_raw_origin" in manifest.incomplete_reasons
    with pytest.raises(RecordingError, match="local origin conflicts"):
        RecordingReader(recorder.path, allow_incomplete=True)


@pytest.mark.parametrize("field", ["code_revision", "provenance", "license"])
@pytest.mark.parametrize("value", [True, None, {}, b"bytes", "", "x" * 4097])
def test_invalid_capture_metadata_is_rejected_before_filesystem_writes(
    tmp_path, field, value
):
    root = tmp_path / "not-created"
    with pytest.raises(ValueError, match=field):
        Recorder(root, session_id="metadata", clock_id="clock", **{field: value})
    assert not root.exists()


def test_initial_metadata_is_bounded_before_session_creation(tmp_path, monkeypatch):
    root = tmp_path / "not-created"
    monkeypatch.setattr(recording, "_MANIFEST_LIMIT", 1024)
    with pytest.raises(ValueError, match="oversized initial session metadata"):
        Recorder(
            root,
            session_id="metadata",
            clock_id="clock",
            config=(("large", "x" * 4096),),
        )
    assert not root.exists()


def test_initial_metadata_reservation_respects_managed_budget(tmp_path):
    root = tmp_path / "captures"
    with pytest.raises(RecordingError, match="budget"):
        Recorder(
            root,
            session_id="metadata",
            clock_id="clock",
            provenance="x" * 4096,
            limits=RecorderLimits(managed_budget_bytes=128 * 1024),
        )
    assert not (root / "metadata" / "session.json").exists()
    assert sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) <= 128 * 1024


def test_recovery_reconciles_manifest_before_catalog_crash(tmp_path):
    path, manifest, _, _ = capture(tmp_path)
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        db.execute(
            "UPDATE sessions SET state='active',bytes=0 WHERE session_id=?",
            (manifest.session_id,),
        )
    assert recover_recordings(tmp_path) == ()
    actual_size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        row = db.execute(
            "SELECT path,state,bytes FROM sessions WHERE session_id=?",
            (manifest.session_id,),
        ).fetchone()
    assert row == (path.name, "complete", actual_size)
    successor = Recorder(
        tmp_path,
        session_id="successor",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=1),
    )
    assert not path.exists()
    assert asyncio.run(successor.finalize()).complete


def test_retention_counts_actual_files_not_catalog_estimates(tmp_path):
    path, _, _, _ = capture(tmp_path)
    # Ancillary managed bytes are intentionally absent from manifest file totals.
    (path / "operator-diagnostic.bin").write_bytes(b"x" * (1024 * 1024))
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        db.execute("UPDATE sessions SET bytes=0")
    actual_root_bytes = sum(
        p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()
    )
    successor = Recorder(
        tmp_path,
        session_id="successor",
        clock_id="clock",
        limits=RecorderLimits(managed_budget_bytes=actual_root_bytes - 1),
    )
    assert not path.exists()
    assert asyncio.run(successor.finalize()).complete


def test_schema_two_groups_rows_without_splitting_transactions(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    first, second = fixture_stream(), fixture_stream("second-stream")
    recorder = recorder_at(
        tmp_path,
        streams=(first, second),
        limits=RecorderLimits(parquet_batch_count=4, parquet_buffer_count=6),
    )
    expected = []
    for ordinal in range(1, 11):
        spec = first if ordinal % 2 else second
        raw, event = fixture_message(spec, ordinal)
        recorder.append_raw(raw)
        recorder.append_events((event,))
        expected.append(event)
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert manifest.schema_version == 2
    files = [f for f in manifest.files if f.kind == "normalized"]
    assert len(files) == 4
    assert all(1 < f.row_count <= 4 for f in files)
    reads = []
    original = pq.ParquetFile.read_row_group

    def observed(self, index, *args, **kwargs):
        reads.append(index)
        return original(self, index, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read_row_group", observed)
    with RecordingReader(recorder.path) as reader:
        assert len(reads) == len(files)
        assert [b.events[0] for b in reader.iter_batches()] == expected
        assert len(reads) == len(files)
        assert reader.query_history(limit=2) == tuple(expected[:2])
        with sqlite3.connect(recorder.path / "index.sqlite3") as db:
            assert db.execute("SELECT max(row_offset) FROM batches").fetchone()[0] > 0


@pytest.mark.parametrize("group_count", [1, 8])
def test_checkpoint_groups_share_exact_auxiliary_state(tmp_path, group_count):
    streams = fixture_stream(), fixture_stream("second-stream")
    recorder = recorder_at(
        tmp_path,
        streams=streams,
        limits=RecorderLimits(checkpoint_group_count=group_count),
    )
    for ordinal, spec in enumerate(streams, 1):
        raw, event = fixture_message(spec, ordinal)
        recorder.append_raw(raw)
        recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert manifest.checkpoint_count == 2
    assert len([f for f in manifest.files if f.kind == "checkpoint_aux"]) == 1
    assert len([f for f in manifest.files if f.kind == "checkpoint"]) == (
        2 if group_count == 1 else 1
    )
    with RecordingReader(recorder.path) as reader:
        checkpoints = tuple(reader.iter_checkpoints())
        assert len(checkpoints) == 2
        assert checkpoints[0].observer_state is checkpoints[1].observer_state
        assert {c.checkpoint.stream_id for c in checkpoints} == {
            s.stream_id for s in streams
        }


@pytest.mark.parametrize("kind", ["raw", "normalized", "checkpoint", "checkpoint_aux"])
def test_warm_reader_cache_does_not_bypass_symlink_boundaries(tmp_path, kind):
    path, manifest, raw, _ = capture(tmp_path / "captures")
    with RecordingReader(path) as reader:
        target = path / next(f.path for f in manifest.files if f.kind == kind)
        outside = tmp_path / "outside"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
        with pytest.raises(RecordingError, match="symlink"):
            if kind == "raw":
                reader.raw_frame(raw.stream_id, raw.ingest_ordinal)
            elif kind == "normalized":
                next(reader.iter_batches())
            else:
                next(reader.iter_checkpoints())


def test_reader_caches_obey_count_and_byte_limits(tmp_path):
    recorder = recorder_at(
        tmp_path,
        limits=RecorderLimits(reader_cache_count=2, reader_cache_bytes=256 * 1024),
    )
    for ordinal in range(1, 6):
        raw, event = fixture_message(fixture_stream(), ordinal)
        recorder.append_raw(raw)
        recorder.append_events((event,))
    assert asyncio.run(recorder.finalize()).complete
    with RecordingReader(recorder.path) as reader:
        assert len(tuple(reader.iter_batches())) == 5
        assert len(tuple(reader.iter_raw())) == 5
        for cache in (
            reader._raw_cache,
            reader._group_cache,
            reader._batch_cache,
            reader._checkpoint_cache,
            reader._aux_cache,
        ):
            assert len(cache.entries) <= 2
            assert cache.used_bytes <= 256 * 1024


def test_schema_one_capture_is_explicitly_rejected(tmp_path):
    path, _, _, _ = capture(tmp_path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["schema_version"] = "1"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RecordingError, match="invalid manifest"):
        RecordingReader(path)


def test_retention_commits_deletion_before_budget_rescan(tmp_path):
    paths = []
    for session in ("first", "second"):
        recorder = Recorder(tmp_path, session_id=session, clock_id="clock")
        assert asyncio.run(recorder.finalize()).complete
        (recorder.path / "diagnostic.bin").write_bytes(b"x" * (256 * 1024))
        paths.append(recorder.path)
    total = sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file())
    oldest = sum(p.stat().st_size for p in paths[0].rglob("*") if p.is_file())
    initial_payload = recording._json(
        {
            "schema_version": str(recording.STORAGE_SCHEMA_VERSION),
            "session_id": "third",
            "clock_id": "clock",
            "streams": [],
            "config": [],
            "code_revision": "unknown",
            "provenance": "public market data",
            "license": "unspecified",
            "created_wall_ns": str(time.time_ns()),
        }
    )
    reservation = len(initial_payload) + 128 * 1024
    third = Recorder(
        tmp_path,
        session_id="third",
        clock_id="clock",
        limits=RecorderLimits(managed_budget_bytes=total - oldest + reservation + 1024),
    )
    assert not paths[0].exists()
    assert paths[1].exists(), (
        "transient rollback journal must not evict a second session"
    )
    manifest = asyncio.run(third.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    with RecordingReader(third.path) as reader:
        assert reader.manifest == manifest


def test_multiple_large_atomic_rows_share_a_readable_parquet_group(tmp_path):
    import pyarrow.parquet as pq

    limits = RecorderLimits(max_row_bytes=8 * 1024 * 1024)
    recorder = recorder_at(tmp_path, limits=limits)
    expected = []
    for ordinal in (1, 2):
        raw, event = fixture_message(fixture_stream(), ordinal)
        event = replace(event, payload=Control("ack", detail="x" * 4096))
        sample_bytes = len(recording.wire.dumps((event,), canonical=False))
        count = 6 * 1024 * 1024 // sample_bytes
        events = tuple(replace(event, event_index=index) for index in range(count))
        row_bytes = len(recording.wire.dumps(events, canonical=False))
        assert 5 * 1024 * 1024 < row_bytes < limits.max_row_bytes
        expected.append(events)
        recorder.append_raw(raw)
        recorder.append_events(events)
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    (normalized,) = (file for file in manifest.files if file.kind == "normalized")
    metadata = pq.ParquetFile(recorder.path / normalized.path).metadata
    assert metadata.num_row_groups == 1
    group = metadata.row_group(0)
    assert group.num_rows == 2
    assert group.total_byte_size > limits.max_row_bytes + 1024 * 1024
    with RecordingReader(recorder.path) as reader:
        assert [batch.events for batch in reader.iter_batches()] == expected


@pytest.mark.parametrize("interruption_stage", ["before", "partial", "after"])
def test_retention_recovers_durable_deletion_without_evicting_newer_session(
    tmp_path, monkeypatch, interruption_stage
):
    paths = []
    for session in ("older", "newer"):
        recorder = Recorder(tmp_path, session_id=session, clock_id="clock")
        assert asyncio.run(recorder.finalize()).complete
        paths.append(recorder.path)
    original_rmtree = recording.shutil.rmtree

    def interrupted_removal(path):
        if interruption_stage == "partial":
            (path / ".active").unlink()
            (path / "manifest.json").unlink()
        elif interruption_stage == "after":
            original_rmtree(path)
        raise OSError("synthetic interruption at retention filesystem boundary")

    limits = RecorderLimits(retention_max_sessions=2)
    with monkeypatch.context() as patch:
        patch.setattr(recording.shutil, "rmtree", interrupted_removal)
        with pytest.raises(OSError, match="synthetic interruption"):
            Recorder(
                tmp_path, session_id="interrupted", clock_id="clock", limits=limits
            )
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        assert db.execute(
            "SELECT state FROM sessions WHERE session_id='older'"
        ).fetchone() == ("deleting",)
    assert paths[0].exists() == (interruption_stage != "after")
    assert paths[1].exists()
    if interruption_stage == "before":
        with RecordingReader(paths[0]):
            assert recover_recordings(tmp_path) == ()
            assert paths[0].exists()
            with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
                assert db.execute(
                    "SELECT state FROM sessions WHERE session_id='older'"
                ).fetchone() == ("deleting",)
    assert recover_recordings(tmp_path) == ()
    assert not paths[0].exists()
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        assert db.execute("SELECT session_id,state FROM sessions").fetchall() == [
            ("newer", "complete")
        ]
    successor = Recorder(
        tmp_path, session_id="successor", clock_id="clock", limits=limits
    )
    manifest = asyncio.run(successor.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    with RecordingReader(paths[1]) as reader:
        assert reader.manifest.session_id == "newer"


def test_recovery_discards_missing_finalized_catalog_rows(tmp_path):
    paths = []
    for session in ("missing", "retained"):
        recorder = Recorder(tmp_path, session_id=session, clock_id="clock")
        assert asyncio.run(recorder.finalize()).complete
        paths.append(recorder.path)
    # Simulate a missing directory with a still-finalized catalog row, not a
    # successful current-protocol deletion. It must not consume retention count.
    recording.shutil.rmtree(paths[0])
    assert recover_recordings(tmp_path) == ()
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as db:
        assert db.execute("SELECT session_id FROM sessions").fetchall() == [
            ("retained",)
        ]
    successor = Recorder(
        tmp_path,
        session_id="successor",
        clock_id="clock",
        limits=RecorderLimits(retention_max_sessions=2),
    )
    assert asyncio.run(successor.finalize()).complete
    with RecordingReader(paths[1]) as reader:
        assert reader.manifest.complete


def test_full_depth_periodic_checkpoints_preserve_lexical_levels_and_restore(tmp_path):
    from src.workstation.adapters.binance import BinanceGlobalAdapter
    from src.workstation.projection import RecordingProjection

    instrument = synthetic_instrument(
        venue="binance-global", api_family="spot-v3", native_symbol="BTCUSDT"
    )
    key = SubscriptionKey.for_instrument(instrument, depth=5000)
    reducer = BinanceGlobalAdapter().new_reducer(instrument, key)
    spec = StreamSpec(
        StreamId("full-depth"),
        instrument,
        key,
        reducer.adapter_revision,
        reducer.config_hash,
    )
    recorder = recorder_at(
        tmp_path,
        streams=(spec,),
        limits=RecorderLimits(checkpoint_interval_batches=2),
    )
    changes = tuple(
        LevelChange(
            side,
            Decimal(f"{price}.00"),
            Decimal("1.000" if price % 2 == 0 else "1.00"),
        )
        for side, prices in (
            ("bid", range(10_000, 5_000, -1)),
            ("ask", range(10_001, 15_001)),
        )
        for price in prices
    )
    expected_raw, expected_events = [], []
    for ordinal in range(1, 4):
        raw = synthetic_frame(
            f"synthetic full-depth source {ordinal}".encode(),
            stream_id=spec.stream_id,
            ordinal=ordinal,
            mono_ns=ordinal,
        )
        event = synthetic_envelope(
            instrument,
            BookUpdate("snapshot", changes, 5000) if ordinal == 1 else Control("ack"),
            key=key,
            stream_id=spec.stream_id,
            ordinal=ordinal,
            source_mode="rest_snapshot",
            raw_ref=raw.ref(),
        )
        expected_raw.append(raw)
        expected_events.append((event,))
        recorder.append_raw(raw)
        recorder.append_events((event,))
    manifest = asyncio.run(recorder.finalize())
    assert manifest.complete, manifest.incomplete_reasons
    assert manifest.checkpoint_count == 2
    with RecordingReader(recorder.path) as reader:
        assert list(reader.iter_raw()) == expected_raw
        assert [batch.events for batch in reader.iter_batches()] == expected_events
        checkpoints = list(reader.iter_checkpoints())
        assert [item.delivery_index for item in checkpoints] == [3, 2]
        for item in checkpoints:
            book = item.checkpoint.book
            assert book is not None
            assert len(book.bids) == len(book.asks) == 5000
            assert str(book.bids[-1].price) == "5001.00"
            assert str(book.asks[-1].price) == "15000.00"
            assert str(book.bids[0].quantity) == "1.000"
            assert str(book.bids[1].quantity) == "1.00"
            projection = RecordingProjection((spec,))
            projection.restore((item.checkpoint,), item.observer_state)
            assert projection.books() == (book,)
            assert projection.checkpoints() == (item.checkpoint,)
            assert projection.checkpoint_state() == item.observer_state
