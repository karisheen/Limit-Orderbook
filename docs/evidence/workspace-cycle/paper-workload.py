#!/usr/bin/env python3
"""Measure 30 independent SQLite spot IOC fixture cases; emit JSON to stdout."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "paper_fixture", ROOT / "tests/test_workstation_paper.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

timings = []
with tempfile.TemporaryDirectory() as directory:
    for index in range(30):
        path = Path(directory) / str(index)
        path.mkdir()
        store, engine, stream, book, now = m.setup(path)
        start = time.perf_counter_ns()
        order = engine.submit(m.request(stream, "4"), now=now, delivery_index=1)
        timings.append((time.perf_counter_ns() - start) / 1e6)
        assert (
            order.status == "partial"
            and order.filled_quantity == 3
            and order.remaining_quantity == 1
        )
        assert (
            engine.submit(m.request(stream), now=now, delivery_index=1).filled_quantity
            == 0
        )
        store.close()

samples = sorted(timings)
print(
    json.dumps(
        {
            "scenario": "30 isolated funded synthetic spot IOC submits; durable SQLite; 2 ask levels; quantity 4 fills 3, cancels 1; repeat consumes zero",
            "count": len(samples),
            "p50_ms": samples[14],
            "p95_ms": samples[28],
            "p99_ms": samples[29],
            "min_ms": samples[0],
            "max_ms": samples[-1],
        }
    )
)
