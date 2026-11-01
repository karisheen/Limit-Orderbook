# Core reducer performance

## Measured starting point

The recorded baseline is [m1-core-initial-benchmark.json](evidence/m1-core-initial-benchmark.json): Python 3.13.5 on macOS 13.7.8 x86_64, a synthetic 40,000-level book (20,000 per side), and 30 single-price delta transactions.

| Measurement | Milliseconds |
| --- | ---: |
| Initial snapshot | 2229.482293 |
| Delta p50 | 39.861841 |
| Delta p95 | 75.126779 |
| Delta p99 | 95.285309 |

The baseline dataset SHA-256 is `24a7a27e39a718517ddc7449010266ac52c016dba82b286112b4a381b1f7e131`. This is core-only synthetic evidence, not ingest-to-publication or browser latency. The normal-workload p95 target is **25 ms or less**; the recorded baseline does not meet it. Thirty samples provide only preliminary tail-latency evidence.

No profiler, benchmark, test, type checker, or formatter was run during this source optimization. There is no measured post-change speedup or claim that the latency target is now met.

## Source-level diagnosis and changes

Source inspection, rather than a sampled profile, identified redundant work in `BookReducer.apply`:

- Both full dictionaries were copied before determining whether either side changed, including for controls and ignored updates.
- Both sides were sorted to validate a candidate, sorted again to commit it, and the prior maps were sorted again to detect economic changes: up to six full-side sorts per accepted transaction.
- Both committed dictionaries were rebuilt even when already complete and ordered candidate tuples were available.
- Public `Book` construction rescanned every level's type and ordering. Quality-only replacement repeated that scan despite unchanged immutable economics.
- New `Level` construction repeated bounded Decimal validation already established by `LevelChange`, instrument/protocol validation, and incremental-result validation. This was per changed level, **not** a full-book Decimal reparse on every delta.

The reducer now copies only a side receiving a write, once per source message. Untouched dictionary ownership remains with the reducer; no dictionary is exposed through a `Book`. Sorted immutable tuples are cached with the committed maps, reused for untouched sides, checkpoint creation, change comparison, and quality-only publication. A touched side is sorted once, and the exact candidate supplied to checksum and candidate hooks is published. The candidate dictionary itself becomes committed state after validation; it is rebuilt only if venue-opt-in truncation actually discards levels.

Unmodified `Level` objects are shared safely because both the dataclass and its Decimal fields are immutable. Changed-side copying, sorting, and tuple comparison still scale with depth; this is not a claim of constant-time updates. No incremental sorting structure, mutable public view, native rewrite, or second precision convention was introduced. Instrument validators still run for every incoming change, even when its price/quantity is numerically unchanged.

## Trusted construction invariants

Public `Level(...)` and `Book(...)` constructors retain their validation. Wire decoding and checkpoint restoration still validate public values. Only the reducer uses private trusted construction:

1. A `LevelChange` has already established a bounded, finite, positive Decimal price and the absolute/incremental quantity policy. Instrument or protocol price checks and instrument quantity/lot checks still run for each change.
2. Incremental arithmetic uses the dedicated Decimal context and revalidates the **result**, including magnitude, precision, and nonnegativity. Zero deletes and never becomes a `Level`; every retained quantity is strictly positive.
3. Candidate maps provide unique Decimal price keys; sorting creates strictly ordered tuples. Latest lexical price and quantity values remain in the stored `Level`, even when an equal dictionary key has an older lexical form.
4. Every event in a message is processed before nonempty/noncrossing, checksum, and candidate checks. Copies are not committed on corruption, sequence failure, or hook rejection. Rejection can publish invalid quality on the old economics, but cannot alter a previously published book.
5. Book identity and epoch come from validated reducer configuration; revision is a nonnegative counter; provenance comes from the accepted envelope. All published levels, tuples, and quality values are immutable.
6. A quality-only replacement retains previously checked sides and provenance. Reset clears both maps and tuple caches. Restore populates caches from validated checkpoint state tuples, preserving exact lexical values; it performs all compatibility/precision checks before changing reducer state.

Consumer tests in `tests/test_workstation_core.py` cover old-book immutability, untouched-side identity and economics, reuse of unchanged levels, public-constructor rejection, trusted-path use, both-side rollback, negative/overflow incremental results, checksum lexical values and failure, full-depth retention, venue truncation, checkpoint parity, full-snapshot versus delta reconstruction, and existing sequence/epoch/protocol-hook contracts.

## Reproducible follow-up benchmark

Run from the repository root using the project's configured Python environment. The following creates a deterministic workload with the baseline's dimensions and prints its own dataset hash. It is a new reproducible fixture, not a claim to recover the original baseline's exact bytes. Event construction and hashing are outside the timed reducer calls; the results therefore exclude decode, network, checkpoint serialization, and UI costs. Snapshot and delta calls are checked for recovery rather than silently timing rejected input.

```python
from decimal import Decimal as D
from hashlib import sha256
from math import ceil
import json
import platform
from time import perf_counter_ns

from src.workstation.book import BookReducer
from src.workstation.events import BookUpdate, LevelChange, SubscriptionKey, synthetic_envelope
from src.workstation.instruments import synthetic_instrument
from src.workstation.wire import dumps

instrument = synthetic_instrument()
key = SubscriptionKey.for_instrument(instrument)
reducer = BookReducer(instrument, key, segment_id="synthetic-segment")
levels_per_side = 20_000
changes = tuple(
    LevelChange("bid", D(30_000 - index), D("1.000"))
    for index in range(levels_per_side)
) + tuple(
    LevelChange("ask", D(30_001 + index), D("1.000"))
    for index in range(levels_per_side)
)
snapshot = synthetic_envelope(
    instrument, BookUpdate("snapshot", changes), key=key, ordinal=1
)
deltas = tuple(
    synthetic_envelope(
        instrument,
        BookUpdate("delta", (LevelChange("bid", D("30000"), D(index + 2)),)),
        key=key,
        ordinal=index + 2,
        mono_ns=index + 2,
    )
    for index in range(30)
)
dataset_hash = sha256(dumps((snapshot,) + deltas)).hexdigest()

start = perf_counter_ns()
result = reducer.apply((snapshot,))
snapshot_ms = (perf_counter_ns() - start) / 1_000_000
assert result.recovery is None and result.book is not None
samples = []
for event in deltas:
    start = perf_counter_ns()
    result = reducer.apply((event,))
    samples.append((perf_counter_ns() - start) / 1_000_000)
    assert result.recovery is None and result.book is not None
    assert len(result.book.bids) == len(result.book.asks) == levels_per_side
samples.sort()
print(json.dumps({
    "scope": "synthetic core-only transaction, not ingest/UI benchmark",
    "platform": platform.platform(),
    "python": platform.python_version(),
    "levels_per_side": levels_per_side,
    "updates": len(samples),
    "snapshot_ms": snapshot_ms,
    "delta_ms": {
        label: samples[ceil(fraction * len(samples)) - 1]
        for label, fraction in (("p50", .50), ("p95", .95), ("p99", .99))
    },
    "percentile_method": "nearest rank",
    "dataset_sha256": dataset_hash,
}, indent=2))
```

Integration should run the core consumer suite and core type checks once after concurrent changes land, then run this benchmark in the same environment before deciding whether further data-structure work is justified. Preserve new measurements separately from the original evidence. A convincing latency acceptance result additionally requires representative live workloads and a larger sample population.
