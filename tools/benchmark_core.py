#!/usr/bin/env python3
"""Finite deterministic synthetic reducer benchmark; no exchange or UI traffic."""

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.workstation import book as book_module
from src.workstation.book import BookReducer
from src.workstation.events import (
    BookUpdate,
    EventEnvelope,
    LevelChange,
    SubscriptionKey,
)
from src.workstation.instruments import AssetIdentity, Instrument
from src.workstation.quality import Capabilities
from src.workstation.wire import dumps


def percentile(values, percent):
    ordered = sorted(values)
    return (
        ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]
        if ordered
        else None
    )


def distribution(values):
    return {
        "count": len(values),
        **{f"p{p}_ms": percentile(values, p) for p in (50, 95, 99)},
        "max_ms": max(values) if values else None,
    }


def dataset(depth, count):
    instrument = Instrument(
        "synthetic:spot:BTC-USD",
        "synthetic-v1",
        "synthetic",
        "benchmark-v1",
        "BTC-USD",
        AssetIdentity("BTC", "BTC"),
        AssetIdentity("USD", "USD"),
        Decimal("0.01"),
        Decimal("0.001"),
        metadata_source="deterministic-generator-v1",
    )
    key = SubscriptionKey.for_instrument(instrument)
    center = Decimal(depth + 1000)
    tick = Decimal("0.01")
    changes = tuple(
        LevelChange(side, center + direction * (i + 1) * tick, Decimal("1"))
        for side, direction in (("bid", -1), ("ask", 1))
        for i in range(depth)
    )

    def envelope(ordinal, update):
        return EventEnvelope(
            session_id="synthetic",
            segment_id="initial",
            stream_id="synthetic-book",
            key=key,
            instrument_metadata_version=instrument.metadata_version,
            connection_id="synthetic",
            connection_epoch=0,
            clock_id="synthetic-clock",
            ingest_ordinal=ordinal,
            receive_wall_ns=ordinal,
            receive_mono_ns=ordinal,
            payload=update,
            source_mode="replay",
        )

    events = [envelope(1, BookUpdate("snapshot", changes))]
    mix = Counter(snapshot=1)
    for index in range(count):
        # A four-message block updates, deletes, reinserts, then changes the same
        # price twice in one atomic message. It never deletes the best level.
        slot = 1 + (index // 4) % (depth - 1)
        side = "bid" if (index // 4) % 2 == 0 else "ask"
        price = center + (-1 if side == "bid" else 1) * (slot + 1) * tick
        kind = ("replace", "delete", "reinsert", "repeated_price")[index % 4]
        quantities = {
            "replace": ("2",),
            "delete": ("0",),
            "reinsert": ("1",),
            "repeated_price": ("3", "1"),
        }[kind]
        events.append(
            envelope(
                index + 2,
                BookUpdate(
                    "delta",
                    tuple(
                        LevelChange(side, price, Decimal(quantity))
                        for quantity in quantities
                    ),
                ),
            )
        )
        mix[kind] += 1
    digest = hashlib.sha256()
    for value in (instrument, *events):
        encoded = dumps(value)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return instrument, key, events, dict(mix), digest.hexdigest()


def run_load(name, rate, instrument, key, events):
    reducer = BookReducer(
        instrument,
        key,
        capabilities=Capabilities(snapshot=True, deltas=True),
        max_levels=max(100000, len(events[0].payload.changes)),
    )
    cpu_start = time.process_time()
    started = time.perf_counter()
    snapshot_start = time.perf_counter_ns()
    result = reducer.apply((events[0],))
    snapshot_ms = (time.perf_counter_ns() - snapshot_start) / 1_000_000
    if (
        result.recovery
        or result.book is None
        or result.book.quality.integrity != "valid"
    ):
        raise RuntimeError(f"synthetic snapshot rejected: {result.recovery}")
    schedule_start = time.perf_counter()
    durations, lateness = [], []
    valid = 0
    for index, event in enumerate(events[1:]):
        due = schedule_start + index / rate
        delay = due - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        before = time.perf_counter_ns()
        lateness.append(max(0, before / 1_000_000_000 - due) * 1000)
        result = reducer.apply((event,))
        durations.append((time.perf_counter_ns() - before) / 1_000_000)
        if (
            result.recovery
            or result.book is None
            or result.book.quality.integrity != "valid"
        ):
            raise RuntimeError(
                f"synthetic event {event.ingest_ordinal} rejected: {result.recovery}"
            )
        valid += 1
    delta_elapsed = time.perf_counter() - schedule_start
    elapsed = time.perf_counter() - started
    cpu = time.process_time() - cpu_start
    return {
        "name": name,
        "offered_delta_messages_per_second": rate,
        "completed_delta_messages": valid,
        "elapsed_seconds_including_snapshot": elapsed,
        "delta_elapsed_seconds": delta_elapsed,
        "snapshot_processing_ms": snapshot_ms,
        "delta_processing": distribution(durations),
        "synthetic_schedule_lateness": distribution(lateness),
        "achieved_delta_messages_per_second": valid / delta_elapsed,
        "processing_only_delta_messages_per_second": valid / (sum(durations) / 1000),
        "cpu_seconds": cpu,
        "cpu_percent_one_core_including_pacing": 100 * cpu / elapsed,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "final_bid_levels": len(result.book.bids),
        "final_ask_levels": len(result.book.asks),
        "reducer_recoveries": 0,
        "dropped_messages": 0,
        "processing_only_targets": {
            "p95_le_25ms": percentile(durations, 95) <= 25,
            "p99_le_100ms": percentile(durations, 99) <= 100,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--depth", type=int, default=20000, help="initial levels per side"
    )
    parser.add_argument(
        "--events", type=int, default=300, help="finite delta messages per load"
    )
    parser.add_argument("--normal-rate", type=float, default=100)
    parser.add_argument("--burst-multiplier", type=float, default=2)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evidence/m1-core-benchmark.json")
    )
    args = parser.parse_args()
    if not 2 <= args.depth <= 100000 or not 1 <= args.events <= 1000000:
        parser.error("depth must be 2..100000 and events 1..1000000")
    if not all(
        math.isfinite(v) and v > 0 for v in (args.normal_rate, args.burst_multiplier)
    ):
        parser.error("rates must be finite and positive")
    instrument, key, events, mix, digest = dataset(args.depth, args.events)
    report = {
        "schema_version": 1,
        "kind": "synthetic_core_processing_benchmark",
        "started_wall_ns": time.time_ns(),
        "runtime": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "core_source_sha256": hashlib.sha256(
            Path(book_module.__file__).read_bytes()
        ).hexdigest(),
        "dataset": {
            "generator": "deterministic-generator-v1",
            "sha256": digest,
            "hash_encoding": "length-prefixed canonical workstation wire instrument then envelopes; uint64 big-endian byte lengths",
            "third_party_data": False,
            "venue_symbol_count": 1,
            "venue": "synthetic",
            "symbol": "BTC-USD",
            "initial_levels_per_side": args.depth,
            "initial_total_levels": args.depth * 2,
            "event_mix": mix,
            "delta_message_count": args.events,
            "delta_level_change_count": sum(len(e.payload.changes) for e in events[1:]),
            "trade_events": 0,
        },
        "limitations": [
            "Reducer.apply only; input construction, decoding and dataset hashing excluded from timing.",
            "No network, receive-to-valid, UI/render, actual queue, or exchange timestamp-age measurement.",
            "Synthetic schedule lateness is serial open-loop deadline lag, not measured queue residence.",
            "RSS is process lifetime high-water including dataset preparation and both loads; not isolated allocation cost.",
            "Generic core invariants only; no venue checksum or protocol bootstrap workload.",
            "Finite runs do not establish peak sustainable production throughput.",
        ],
        "loads": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for name, rate in (
            ("normal", args.normal_rate),
            ("burst", args.normal_rate * args.burst_multiplier),
        ):
            report["loads"].append(run_load(name, rate, instrument, key, events))
        report["status"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_wall_ns"] = time.time_ns()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "dataset_sha256": digest,
            }
        )
    )
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
