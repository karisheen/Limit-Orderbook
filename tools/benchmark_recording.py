#!/usr/bin/env python3
"""Bounded offline benchmark of managed recording writer, projection and replay."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing, nullcontext
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import sys
import subprocess
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.workstation import wire
from src.workstation.events import BookUpdate, RawFrame
from src.workstation.recording import Recorder, RecorderLimits, RecordingReader, _size
from src.workstation.replay import ReplaySession


class Samples:
    """Deterministic bounded reservoir; exact count/sum/max, sampled quantiles."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.values = []
        self.count = 0
        self.total = 0
        self.maximum = 0
        self.random = random.Random(0)

    def add(self, ns):
        self.count += 1
        self.total += ns
        self.maximum = max(self.maximum, ns)
        if len(self.values) < self.capacity:
            self.values.append(ns)
        else:
            index = self.random.randrange(self.count)
            if index < self.capacity:
                self.values[index] = ns

    def report(self):
        ordered = sorted(self.values)
        return {
            "count": self.count,
            "retained_samples": len(ordered),
            "quantile_method": "nearest-rank deterministic reservoir"
            if self.count > len(ordered)
            else "nearest-rank all observations",
            "total_seconds": self.total / 1e9,
            "max_ms": self.maximum / 1e6 if self.count else None,
            **{
                f"p{p}_ms": ordered[math.ceil(len(ordered) * p / 100) - 1] / 1e6
                if ordered
                else None
                for p in (50, 95, 99)
            },
        }


class Metrics:
    def __init__(self, capacity):
        self.capacity = capacity
        self.stages = {}
        self.lock = threading.Lock()

    def add(self, name, ns):
        with self.lock:
            samples = self.stages.get(name)
            if samples is None:
                samples = self.stages[name] = Samples(self.capacity)
            samples.add(ns)

    def call(self, name, function, *args, **kwargs):
        start = time.perf_counter_ns()
        try:
            return function(*args, **kwargs)
        finally:
            self.add(name, time.perf_counter_ns() - start)

    def report(self):
        with self.lock:
            return {
                name: samples.report() for name, samples in sorted(self.stages.items())
            }


class MeasuredRecorder(Recorder):
    """Timing wrappers only: all queue, persistence and reduction logic is production."""

    def __init__(self, *args, metrics, **kwargs):
        self.metrics = metrics
        self.accepted = {}
        self.worker_cpu_ns = None
        self.worker_wall_ns = None
        super().__init__(*args, **kwargs)
        apply = self._projection.apply_batch
        self._projection.apply_batch = lambda batch: metrics.call(
            "worker_projection_apply", apply, batch
        )

    def _run(self):
        started = time.thread_time_ns()
        wall_started = time.perf_counter_ns()
        try:
            return super()._run()
        finally:
            self.worker_cpu_ns = time.thread_time_ns() - started
            self.worker_wall_ns = time.perf_counter_ns() - wall_started

    def _offer(self, kind, value, size):
        # Hold the production condition until the acceptance timestamp is stored;
        # dispatch cannot race timestamp registration. No queue capacity changes.
        with self._condition:
            result = self.metrics.call(
                "producer_queue_offer_" + kind, super()._offer, kind, value, size
            )
            self.accepted[id(value)] = time.perf_counter_ns()
            return result

    def dispatch(self, name, value, function):
        with self._condition:
            accepted = self.accepted.pop(id(value), None)
        if accepted is not None:
            self.metrics.add(
                "accepted_to_worker_dispatch_" + name, time.perf_counter_ns() - accepted
            )
        return self.metrics.call("worker_" + name, function, value)

    def _write_raw(self, frame):
        return self.dispatch("raw", frame, super()._write_raw)

    def _write_events(self, batch):
        return self.dispatch("events_inclusive", batch, super()._write_events)

    def _flush_events(self, *args, **kwargs):
        return self.metrics.call(
            "worker_flush_events", super()._flush_events, *args, **kwargs
        )

    def _save_projection(self, *args, **kwargs):
        return self.metrics.call(
            "worker_save_projection_inclusive",
            super()._save_projection,
            *args,
            **kwargs,
        )

    def _write_checkpoint_group(self, *args, **kwargs):
        return self.metrics.call(
            "worker_checkpoint_group", super()._write_checkpoint_group, *args, **kwargs
        )


class MeasuredReader(RecordingReader):
    def __init__(self, *args, metrics, **kwargs):
        self.metrics = metrics
        super().__init__(*args, **kwargs)

    def iter_batches(self, **kwargs):
        iterator = super().iter_batches(**kwargs)
        try:
            while True:
                batch = self.metrics.call("reader_batch_next", next, iterator, None)
                if batch is None:
                    return
                yield batch
        finally:
            iterator.close()

    def raw_frame(self, *args):
        return self.metrics.call("reader_raw_lookup", super().raw_frame, *args)


class Digest:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.count = 0
        self.bytes = 0

    def add(self, value):
        encoded = wire.dumps(value, canonical=False)
        self.digest.update(len(encoded).to_bytes(8, "big"))
        self.digest.update(encoded)
        self.count += 1
        self.bytes += len(encoded)

    def report(self):
        return {
            "sha256": self.digest.hexdigest(),
            "records": self.count,
            "encoded_bytes": self.bytes,
        }


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def usage(start, cpu):
    elapsed = time.perf_counter() - start
    consumed = time.process_time() - cpu
    return {
        "wall_seconds": elapsed,
        "cpu_seconds": consumed,
        "cpu_percent_one_core": 100 * consumed / elapsed if elapsed else None,
        "process_lifetime_peak_rss_bytes": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
    }


def manifest_summary(manifest):
    return {
        "complete": manifest.complete,
        "incomplete_reasons": manifest.incomplete_reasons,
        "raw_count": manifest.raw_count,
        "batch_count": manifest.batch_count,
        "event_count": manifest.event_count,
        "checkpoint_count": manifest.checkpoint_count,
        "gap_count": len(manifest.gaps),
        "gap_reasons": dict(
            Counter(event.payload.reason_code for event in manifest.gaps)
        ),
        "capture_lost_gap_count": sum(
            event.payload.capture_lost for event in manifest.gaps
        ),
        "limits": asdict(manifest.limits),
    }


def hardware():
    result = {
        "processor": platform.processor(),
        "machine": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
    }
    if sys.platform == "darwin":
        names = ("machdep.cpu.brand_string", "hw.model", "hw.memsize")
        try:
            completed = subprocess.run(
                ["sysctl", "-n", *names],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            result["sysctl"] = dict(zip(names, completed.stdout.splitlines()))
        except (OSError, subprocess.SubprocessError) as exc:
            result["hardware_detail_unavailable"] = str(exc)
    return result


def evidence(reader):
    """Streaming exact logical evidence hashes, independent of file regrouping."""
    raw, batches, checkpoints = Digest(), Digest(), Digest()
    low = high = frontier = second = None
    count_in_second = peak = payload_bytes = 0
    for frame in reader.iter_raw():
        raw.add(frame)
        payload_bytes += len(frame.payload)
        stamp = frame.receive_mono_ns
        low = stamp if low is None else min(low, stamp)
        high = stamp if high is None else max(high, stamp)
        frontier = stamp if frontier is None else max(frontier, stamp)
        bucket = frontier // 1_000_000_000
        if bucket != second:
            peak = max(peak, count_in_second)
            second, count_in_second = bucket, 0
        count_in_second += 1
    for batch in reader.iter_batches():
        batches.add((batch.delivery_index, batch.events))
    for record in reader.iter_checkpoints():
        checkpoints.add(
            (record.delivery_index, record.checkpoint, record.observer_state)
        )
    span = (high - low) / 1e9 if low is not None else 0
    return {
        "raw": raw.report(),
        "normalized": batches.report(),
        "checkpoints": checkpoints.report(),
        "raw_receive": {
            "span_seconds": span,
            "payload_bytes": payload_bytes,
            "average_frames_per_second": raw.count / span if span else None,
            "peak_frames_per_receive_highwater_aligned_second": max(
                peak, count_in_second
            ),
        },
    }


def replay_pass(path, allow_incomplete, capacity, mode, *, describe=False):
    metrics = Metrics(capacity)
    start, cpu = time.perf_counter(), time.process_time()
    event_mix, updates, source_modes, epochs = (
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    depths = {}
    first_mono = last_mono = highwater = None
    peak_second, second_count, current_second = 0, 0, None
    with MeasuredReader(
        path, allow_incomplete=allow_incomplete, verify=False, metrics=metrics
    ) as reader:
        session = ReplaySession(reader, mode=mode)
        apply = session.projection.apply_batch
        process = session.projection.process_raw
        session.projection.apply_batch = lambda batch: metrics.call(
            "replay_projection_apply", apply, batch
        )
        session.projection.process_raw = lambda *a, **kw: metrics.call(
            "replay_raw_decode_and_projection", process, *a, **kw
        )
        try:
            while True:
                delivered = metrics.call("replay_step_inclusive", session.step)
                if not delivered:
                    break
                batch = delivered[0]
                if describe:
                    stamp = batch.events[0].receive_mono_ns
                    first_mono = stamp if first_mono is None else min(first_mono, stamp)
                    last_mono = stamp if last_mono is None else max(last_mono, stamp)
                    highwater = stamp if highwater is None else max(highwater, stamp)
                    second = highwater // 1_000_000_000
                    if second != current_second:
                        peak_second = max(peak_second, second_count)
                        current_second, second_count = second, 0
                    second_count += 1
                    for event in batch.events:
                        event_mix[type(event.payload).__name__] += 1
                        source_modes[event.source_mode] += 1
                        epochs[str(event.stream_id)] = max(
                            epochs[str(event.stream_id)], event.connection_epoch
                        )
                        if isinstance(event.payload, BookUpdate):
                            updates[event.payload.kind] += 1
                            updates["level_changes"] += len(event.payload.changes)
                    for stream, reducer in session.projection.reducers.items():
                        book = reducer.book
                        if book is None:
                            continue
                        value = depths.setdefault(
                            str(stream), {"max_bid_levels": 0, "max_ask_levels": 0}
                        )
                        value["max_bid_levels"] = max(
                            value["max_bid_levels"], len(book.bids)
                        )
                        value["max_ask_levels"] = max(
                            value["max_ask_levels"], len(book.asks)
                        )
            result = {
                "mode": mode,
                "deliveries": session.cursor.delivery_index,
                "book_sha256": session.book_hash,
                "state_sha256": session.state_hash,
                "clock_sha256": session.clock.state_hash,
            }
            if describe:
                span = (last_mono - first_mono) / 1e9 if first_mono is not None else 0
                result["dataset"] = {
                    "event_mix": dict(event_mix),
                    "book_updates": dict(updates),
                    "source_modes": dict(source_modes),
                    "maximum_connection_epoch": dict(epochs),
                    "native_retained_depths": depths,
                    "receive_mono_span_seconds": span,
                    "average_batches_per_receive_span_second": session.cursor.delivery_index
                    / span
                    if span
                    else None,
                    "peak_batches_per_receive_highwater_aligned_second": max(
                        peak_second, second_count
                    ),
                }
        finally:
            session.close()
    result.update(usage(start, cpu))
    result["stages"] = metrics.report()
    return result


def checkpoint_seek(path, allow_incomplete, expected, capacity):
    start, cpu = time.perf_counter(), time.process_time()
    with RecordingReader(
        path, verify=False, allow_incomplete=allow_incomplete
    ) as reader:
        session = ReplaySession(reader, checkpoint_policy="reject")
        try:
            session.seek_delivery(reader.manifest.batch_count)
            result = {
                "checkpoint_status": session.checkpoint_status,
                "book_sha256": session.book_hash,
                "state_sha256": session.state_hash,
                "book_exact": session.book_hash == expected["book_sha256"],
                "state_exact": session.state_hash == expected["state_sha256"],
            }
        except (ValueError, KeyError, TypeError) as exc:
            result = {
                "checkpoint_status": session.checkpoint_status,
                "error": str(exc),
                "book_exact": False,
                "state_exact": False,
            }
        finally:
            session.close()
    result.update(usage(start, cpu))
    return result


def ordered_input(reader):
    """Two-item lookahead: raw ordinal order, normalized callback delivery order."""
    raw = reader.iter_raw()
    frame = next(raw, None)
    try:
        for batch in reader.iter_batches():
            while (
                frame is not None
                and frame.ingest_ordinal <= batch.events[0].ingest_ordinal
            ):
                yield frame
                frame = next(raw, None)
            yield batch
        while frame is not None:
            yield frame
            frame = next(raw, None)
    finally:
        raw.close()


def preload_source(args, manifest):
    """Retain the entire admitted workload only under explicit accounting caps."""
    start, cpu = time.perf_counter(), time.process_time()
    metrics = Metrics(args.samples)
    expected = manifest.raw_count + manifest.batch_count
    if expected > 100_000:
        raise ValueError("predecoded source exceeds 100000 raw-frame-plus-batch items")
    slot_bytes = sys.getsizeof([None]) - sys.getsizeof([])
    if sys.getsizeof([]) + expected * slot_bytes > args.preload_byte_limit:
        raise ValueError("preload container exceeds explicit byte limit")
    items = [None] * expected
    accounted = sys.getsizeof(items)
    count = 0
    with MeasuredReader(
        args.source,
        verify=False,
        allow_incomplete=args.allow_incomplete,
        metrics=metrics,
    ) as reader:
        with closing(ordered_input(reader)) as iterator:
            while True:
                value = metrics.call(
                    "preload_generator_next_inclusive", next, iterator, None
                )
                if value is None:
                    break
                size = metrics.call("preload_production_resident_size", _size, value)
                if count >= expected or accounted + size > args.preload_byte_limit:
                    raise ValueError(
                        "entire admitted source does not fit preload record/byte bounds; no truncated workload was run"
                    )
                items[count] = value
                count += 1
                accounted += size
    if count != expected:
        raise ValueError("preload count differs from verified manifest")
    return items, {
        **usage(start, cpu),
        "records": count,
        "accounted_retained_bytes": accounted,
        "byte_limit": args.preload_byte_limit,
        "item_limit": 100_000,
        "accounting": "list storage plus sum of production recording._size(item); shared objects counted per item; excludes transient decoder/reader caches and whole-process RSS",
        "stages": metrics.report(),
    }


def benchmark_config(manifest):
    """Retain source reasons outside the new writer's bounded fault list."""
    if manifest.complete:
        return manifest.config
    prefix = "benchmark_source_incomplete_"
    if any(key.startswith(prefix) for key, _ in manifest.config):
        raise ValueError("source already uses reserved benchmark provenance metadata")
    provenance = (
        (prefix + "reason_count", str(len(manifest.incomplete_reasons))),
    ) + tuple(
        (prefix + f"reason_{index:02d}", reason)
        for index, reason in enumerate(manifest.incomplete_reasons)
    )
    if len(manifest.config) + len(provenance) > 256:
        raise ValueError(
            "source configuration and complete failure provenance exceed metadata capacity"
        )
    return manifest.config + provenance


def writer_load(args, manifest, name, speed, preloaded):
    metrics = Metrics(args.samples)
    start, cpu = time.perf_counter(), time.process_time()
    root = args.output_root / name
    limits = RecorderLimits()
    if args.checkpoint_interval_batches is not None:
        limits = replace(
            limits, checkpoint_interval_batches=args.checkpoint_interval_batches
        )
    recorder = MeasuredRecorder(
        root,
        session_id=manifest.session_id,
        clock_id=manifest.clock_id,
        streams=manifest.streams,
        config=benchmark_config(manifest),
        code_revision="benchmark-source-sha256:" + file_hash(Path(__file__)),
        provenance=manifest.provenance,
        license=manifest.license,
        limits=limits,
        metrics=metrics,
    )
    if not manifest.complete:
        recorder.mark_incomplete("benchmark_source_incomplete")
    first = highwater = None
    offered_raw = offered_batches = offered_events = rejected = 0
    failure = None
    schedule_start = time.perf_counter()
    try:
        source_context = (
            MeasuredReader(
                args.source,
                verify=False,
                allow_incomplete=args.allow_incomplete,
                metrics=metrics,
            )
            if preloaded is None
            else nullcontext()
        )
        with source_context as reader:
            iterator = (
                ordered_input(reader)
                if preloaded is None
                else (value for value in preloaded)
            )
            try:
                while True:
                    value = metrics.call(
                        "load_generator_next_inclusive", next, iterator, None
                    )
                    if value is None:
                        break
                    event = value if isinstance(value, RawFrame) else value.events[0]
                    stamp = event.receive_mono_ns
                    first = stamp if first is None else first
                    highwater = stamp if highwater is None else max(highwater, stamp)
                    due = (
                        schedule_start + (highwater - first) / 1e9 / speed
                        if speed
                        else time.perf_counter()
                    )
                    if (
                        due - schedule_start > args.max_load_seconds
                        or time.perf_counter() - schedule_start > args.max_load_seconds
                    ):
                        failure = "benchmark_load_deadline"
                        recorder.mark_incomplete(failure)
                        break
                    if speed:
                        delay = due - time.perf_counter()
                        if delay > 0:
                            time.sleep(delay)
                        metrics.add(
                            "synthetic_schedule_lateness",
                            max(0, int((time.perf_counter() - due) * 1e9)),
                        )
                    try:
                        if isinstance(value, RawFrame):
                            metrics.call(
                                "producer_append_raw_inclusive",
                                recorder.append_raw,
                                value,
                            )
                            offered_raw += 1
                        else:
                            metrics.call(
                                "producer_append_events_inclusive",
                                recorder.append_events,
                                value.events,
                            )
                            offered_batches += 1
                            offered_events += len(value.events)
                    except Exception as exc:
                        rejected += 1
                        failure = type(exc).__name__ + ":" + str(exc)
                        recorder.mark_incomplete("benchmark_offer_rejected")
                        break
            finally:
                iterator.close()
    except Exception as exc:
        failure = type(exc).__name__ + ":" + str(exc)
        recorder.mark_incomplete("benchmark_input_failure")
    producer_elapsed = time.perf_counter() - schedule_start
    finalize_start = time.perf_counter()
    output_manifest = asyncio.run(recorder.finalize())
    finalize_seconds = time.perf_counter() - finalize_start
    result = {
        "name": name,
        "speed_multiplier": speed,
        "input_mode": "bounded_predecoded_writer_isolation"
        if preloaded is not None
        else "streaming_reader_and_writer",
        "visited_prefix_scheduled_span_seconds": (highwater - first) / 1e9 / speed
        if speed and first is not None
        else None,
        "schedule": "synthetic receive-mono highwater; raw ordinal merge before normalized callback delivery"
        if speed
        else "unpaced finite saturation attempt; predecoded input"
        if preloaded is not None
        else "unpaced finite saturation attempt; file-generation limited",
        "source_publication_timestamps_available": False,
        "source_local_processing_latency": None,
        "output_path": str(recorder.path),
        "offered_raw": offered_raw,
        "offered_batches": offered_batches,
        "offered_events": offered_events,
        "rejected_offer_count": rejected,
        "unoffered_raw": manifest.raw_count - offered_raw,
        "unoffered_batches": manifest.batch_count - offered_batches,
        "accepted_but_not_written_raw": offered_raw - output_manifest.raw_count,
        "accepted_but_not_written_batches": offered_batches
        - output_manifest.batch_count,
        "intentional_drops": 0,
        "failure": failure,
        "producer_seconds": producer_elapsed,
        "finalize_drain_and_seal_seconds": finalize_seconds,
        "worker_thread_cpu_seconds": recorder.worker_cpu_ns / 1e9
        if recorder.worker_cpu_ns is not None
        else None,
        "worker_thread_wall_seconds": recorder.worker_wall_ns / 1e9
        if recorder.worker_wall_ns is not None
        else None,
        "achieved_offered_batches_per_second": offered_batches / producer_elapsed
        if producer_elapsed
        else None,
        "capture": manifest_summary(output_manifest),
        "checkpoint_cadence": {
            "source_interval_batches": manifest.limits.checkpoint_interval_batches,
            "effective_writer_interval_batches": output_manifest.limits.checkpoint_interval_batches,
            "matches_source": output_manifest.limits.checkpoint_interval_batches
            == manifest.limits.checkpoint_interval_batches,
        },
        "health": {
            **asdict(recorder.health),
            "terminal_failure": recorder.health.terminal_failure,
        },
        "stages": metrics.report(),
    }
    result.update(usage(start, cpu))
    result["written_batches_per_second_including_startup_drain_seal"] = (
        output_manifest.batch_count / result["wall_seconds"]
    )
    if not speed:
        result["saturation_observation"] = (
            "queue_capacity_reached"
            if "queue_overflow" in recorder.health.incomplete_reasons
            else "not_demonstrated; finite input or reader/producer may limit throughput"
        )
    return result


def compare_output(args, load, source_evidence, source_replay):
    path = Path(load["output_path"])
    if not (path / "manifest.json").is_file():
        return {
            "error": "no finalized manifest; output is not a readable complete capture"
        }
    start, cpu = time.perf_counter(), time.process_time()
    try:
        with RecordingReader(path, allow_incomplete=True) as reader:
            output_evidence = evidence(reader)
        normalized = replay_pass(path, True, args.samples, "normalized")
        raw = replay_pass(path, True, args.samples, "raw")
        return {
            "manifest_sha256": file_hash(path / "manifest.json"),
            "evidence": output_evidence,
            "raw_exact": output_evidence["raw"] == source_evidence["raw"],
            "normalized_exact": output_evidence["normalized"]
            == source_evidence["normalized"],
            "regenerated_checkpoints_exact": output_evidence["checkpoints"]
            == source_evidence["checkpoints"],
            "normalized_replay": normalized,
            "raw_replay": raw,
            "source_final_state_exact": normalized["state_sha256"]
            == source_replay["state_sha256"],
            "source_final_book_exact": normalized["book_sha256"]
            == source_replay["book_sha256"],
            "raw_normalized_state_exact": raw["state_sha256"]
            == normalized["state_sha256"],
            "checkpoint_seek": checkpoint_seek(path, True, normalized, args.samples),
            "verification_and_parity_usage": usage(start, cpu),
        }
    except Exception as exc:
        return {
            "error": type(exc).__name__ + ":" + str(exc),
            "verification_and_parity_usage": usage(start, cpu),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="finalized managed session directory, not store root",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        type=Path,
        help="fresh non-existing directory; report and three independent stores",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="explicitly benchmark available incomplete evidence without claiming completeness",
    )
    parser.add_argument("--max-source-bytes", type=int, default=2 * 1024**3)
    parser.add_argument(
        "--max-source-records",
        type=int,
        default=100_000,
        help="sum of raw, normalized batches, events and checkpoints; reject oversized source",
    )
    parser.add_argument(
        "--max-load-seconds",
        type=float,
        default=180,
        help="producer scheduling deadline per load, plus production finalize deadline",
    )
    parser.add_argument(
        "--samples", type=int, default=20_000, help="maximum timing samples per stage"
    )
    parser.add_argument(
        "--checkpoint-interval-batches",
        type=int,
        default=None,
        help="explicit storage/seek experiment; omitted uses unchanged production default",
    )
    parser.add_argument(
        "--preload-byte-limit",
        type=int,
        default=None,
        help="opt-in whole admitted source predecode cap; maximum128MiB/100000 items; isolates writer arrivals from file generation",
    )
    args = parser.parse_args()
    if (
        not 1 <= args.samples <= 100_000
        or not 1 <= args.max_source_records <= 1_000_000
        or not 1 <= args.max_source_bytes <= 20 * 1024**3
    ):
        parser.error("invalid sample/record/byte bound")
    if (
        args.checkpoint_interval_batches is not None
        and not 1 <= args.checkpoint_interval_batches <= 1_000_000
    ):
        parser.error("checkpoint-interval-batches must be 1..1000000")
    if (
        args.preload_byte_limit is not None
        and not 1 <= args.preload_byte_limit <= 128 * 1024**2
    ):
        parser.error("preload-byte-limit must be 1..134217728")
    if (
        not math.isfinite(args.max_load_seconds)
        or not 0 < args.max_load_seconds <= 3600
    ):
        parser.error("max-load-seconds must be finite and in (0, 3600]")
    supplied_source = args.source.expanduser()
    if supplied_source.is_symlink():
        parser.error("source must not be a symlink")
    args.source = supplied_source.resolve(strict=True)
    supplied_output = args.output_root.expanduser()
    if supplied_output.is_symlink() or supplied_output.exists():
        parser.error("output-root must be fresh and non-existing")
    args.output_root = supplied_output.resolve()
    if args.output_root == args.source or args.source in args.output_root.parents:
        parser.error("output-root cannot be within the source capture")
    report = {
        "schema_version": 1,
        "kind": "managed_recording_offline_benchmark",
        "started_wall_ns": time.time_ns(),
        "runtime": sys.version,
        "checkpoint_interval_batches_override": args.checkpoint_interval_batches,
        "input_mode": "bounded_predecoded_writer_isolation"
        if args.preload_byte_limit is not None
        else "streaming_reader_and_writer",
        "preload_byte_limit": args.preload_byte_limit,
        "tool_sha256": file_hash(__file__),
        "hardware": hardware(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "packages": {
            name: importlib.metadata.version(name) for name in ("pyarrow", "duckdb")
        },
        "command": sys.argv,
        "source_path": str(args.source),
        "bounds": {
            key: value
            for key, value in vars(args).items()
            if key.startswith("max_") or key == "samples"
        },
        "hash_encoding": "logical evidence: uint64 big-endian length-prefixed noncanonical workstation wire; raw ordinal order, normalized delivery order, checkpoints descending delivery then stream",
        "limitations": [
            "No network or UI. Original processing/publication timestamps are absent; receive-highwater schedules are synthetic, not original callback timing or network latency.",
            "Default input streams with two-item lookahead. Explicit predecode mode admits the entire bounded source under its production resident-accounting cap; decoding transients/caches and whole-process RSS are separate. OS caches are not evicted.",
            "Timing wrappers perturb the workload. Nested worker/replay stages overlap and must not be summed. Accepted-to-dispatch includes pre-dispatch disk checks, not exact deque residence.",
            "Process CPU includes producer and worker; RSS is process lifetime highwater, not per-load allocation. Baseline, 2x burst, unpaced are sequential in the same process.",
            "No required event is deliberately dropped, retried or backpressured. A rejected offer terminates that load and its unfinished output remains incomplete.",
            "Source checkpoint bytes are compared with regenerated output; changed checkpoint cadence/schema may differ. Strict output checkpoint seek must reproduce its own linear state.",
            "Unpaced finite measurement is not proof of sustained maximum capacity or a healthy live soak; if the queue never fills, saturation is not demonstrated.",
            "Record/file byte limits bound admitted input, not total decompressed work; production decoder limits remain active. Deadlines are checked between atomic operations, not hard process termination.",
        ],
    }
    args.output_root.mkdir(parents=True, exist_ok=False)
    try:
        # Parse bounded manifest before expensive full verification, then enforce
        # whole-source admission limits. Never silently truncate a larger source.
        start, cpu = time.perf_counter(), time.process_time()
        with RecordingReader(
            args.source, verify=False, allow_incomplete=args.allow_incomplete
        ) as reader:
            manifest = reader.manifest
            records = (
                manifest.raw_count
                + manifest.batch_count
                + manifest.event_count
                + manifest.checkpoint_count
            )
            declared_bytes = sum(item.size_bytes for item in manifest.files)
            if (
                records > args.max_source_records
                or declared_bytes > args.max_source_bytes
            ):
                raise ValueError(
                    "source exceeds explicit record/byte bound; choose a bounded source or raise the declared limit"
                )
            reader.verify()
            report["source"] = {
                **manifest_summary(manifest),
                "session_id": manifest.session_id,
                "manifest_sha256": file_hash(args.source / "manifest.json"),
                "code_revision": manifest.code_revision,
                "declared_file_bytes": declared_bytes,
                "files": [asdict(item) for item in manifest.files],
                "original_dropped_event_count": None,
                "original_queue_highwaters": None,
                "streams": [asdict(spec) for spec in manifest.streams],
                "evidence": evidence(reader),
            }
        report["source_verification_and_hashing"] = usage(start, cpu)
        report["source_normalized_replay"] = replay_pass(
            args.source,
            args.allow_incomplete,
            args.samples,
            "normalized",
            describe=True,
        )
        report["source_raw_replay"] = replay_pass(
            args.source, args.allow_incomplete, args.samples, "raw"
        )
        report["source_raw_normalized_state_exact"] = (
            report["source_raw_replay"]["state_sha256"]
            == report["source_normalized_replay"]["state_sha256"]
        )
        report["source_checkpoint_seek"] = checkpoint_seek(
            args.source,
            args.allow_incomplete,
            report["source_normalized_replay"],
            args.samples,
        )
        # Hash loaded production modules, including adapter/precision/projection
        # dependencies, rather than a guessed subset or mutable Git label.
        report["source_files_sha256"] = {
            str(
                Path(module.__file__)
                .resolve()
                .relative_to(Path(__file__).resolve().parents[1])
            ): file_hash(module.__file__)
            for name, module in sorted(sys.modules.items())
            if name.startswith("src.workstation")
            and getattr(module, "__file__", None)
            and Path(module.__file__).suffix == ".py"
        }
        preloaded = None
        if args.preload_byte_limit is not None:
            preloaded, report["preload"] = preload_source(args, manifest)
        report["loads"] = []
        for name, speed in (
            ("baseline", 1),
            ("burst_2x", 2),
            ("unpaced_saturation", 0),
        ):
            load = writer_load(args, manifest, name, speed, preloaded)
            load["parity"] = compare_output(
                args,
                load,
                report["source"]["evidence"],
                report["source_normalized_replay"],
            )
            report["loads"].append(load)
            # Preserve completed finite observations even if a later load fails.
            (args.output_root / "report.json").write_text(
                json.dumps(report, indent=2, default=str, allow_nan=False) + "\n"
            )
        paced_ok = report["source_raw_normalized_state_exact"] and all(
            load["failure"] is None
            and not load["health"]["terminal_failure"]
            and all(
                load["parity"].get(key, False)
                for key in (
                    "raw_exact",
                    "normalized_exact",
                    "source_final_state_exact",
                    "source_final_book_exact",
                    "raw_normalized_state_exact",
                )
            )
            and (
                not load["checkpoint_cadence"]["matches_source"]
                or load["parity"].get("regenerated_checkpoints_exact", False)
            )
            and load["parity"].get("checkpoint_seek", {}).get("state_exact", False)
            for load in report["loads"][:2]
        )
        report["status"] = (
            "completed" if paced_ok else "paced_load_failed_or_parity_mismatch"
        )
        report["source_remains_incomplete"] = not manifest.complete
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = type(exc).__name__ + ":" + str(exc)
    (args.output_root / "report.json").write_text(
        json.dumps(report, indent=2, default=str, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(args.output_root / "report.json"),
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
