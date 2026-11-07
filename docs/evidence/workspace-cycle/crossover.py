#!/usr/bin/env python3
"""Alternate verified baseline/current reducer timings; emit JSON to stdout."""

import json
from pathlib import Path
import runpy
import statistics
import sys
import time
from dataclasses import replace
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.workstation.book import BookReducer
from src.workstation.events import BookUpdate, LevelChange
from src.workstation.quality import Capabilities
from tools.benchmark_core import dataset

baseline = runpy.run_path(str(Path(__file__).with_name("core-before.py")))
original = baseline["load_baseline"]("src.workstation._baseline_book")


def dist(samples):
    samples = sorted(samples)
    return {
        "count": len(samples),
        "p50_ms": statistics.median(samples),
        "p95_ms": samples[max(0, (len(samples) * 95 + 99) // 100 - 1)],
    }


def values(book):
    return [
        [(str(x.price), str(x.quantity)) for x in side]
        for side in (book.bids, book.asks)
    ]


report = []
for depth, widths in [
    (20, [1, 20]),
    (128, [1, 16, 128]),
    (4096, [1, 16, 64, 65, 128, 512, 1024, 4096]),
    (20000, [1, 64, 2048, 2500]),
]:
    instrument, key, events, _, _ = dataset(depth, 1)
    for width in widths:
        reducers = [
            original.BookReducer(
                instrument, key, capabilities=Capabilities(snapshot=True, deltas=True)
            ),
            BookReducer(
                instrument, key, capabilities=Capabilities(snapshot=True, deltas=True)
            ),
        ]
        snapshot_times = []
        for reducer in reducers:
            start = time.perf_counter_ns()
            result = reducer.apply((events[0],))
            snapshot_times.append((time.perf_counter_ns() - start) / 1e6)
            assert result.recovery is None
        inputs = []
        for index in range(24):
            side = "bid" if index % 2 == 0 else "ask"
            direction = -1 if side == "bid" else 1
            changes = tuple(
                LevelChange(
                    side,
                    Decimal(depth + 1000) + direction * (j + 1) * Decimal("0.01"),
                    Decimal(index + 2),
                )
                for j in range(width)
            )
            inputs.append(
                replace(
                    events[1],
                    ingest_ordinal=index + 2,
                    receive_mono_ns=index + 2,
                    receive_wall_ns=index + 2,
                    payload=BookUpdate("delta", changes),
                )
            )
        samples = [[], []]
        cpu = [[], []]
        for index, event in enumerate(inputs):
            for variant in [0, 1] if index % 2 == 0 else [1, 0]:
                start = time.perf_counter_ns()
                cs = time.process_time_ns()
                result = reducers[variant].apply((event,))
                cpu[variant].append((time.process_time_ns() - cs) / 1e6)
                samples[variant].append((time.perf_counter_ns() - start) / 1e6)
                assert result.recovery is None
        assert values(reducers[0].book) == values(reducers[1].book)
        report.append(
            {
                "depth": depth,
                "touched_per_message": width,
                "snapshot_ms": snapshot_times,
                "before": dist(samples[0]),
                "after": dist(samples[1]),
                "before_cpu": dist(cpu[0]),
                "after_cpu": dist(cpu[1]),
                "exact_final_levels": True,
            }
        )
print(json.dumps(report, indent=2))
