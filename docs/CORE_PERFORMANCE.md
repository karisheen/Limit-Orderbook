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

The implementation worker did not run measurements during concurrent source edits. Subsequent integration measured the same initial workload at snapshot **937.89 ms**, delta p50 **3.51 ms**, p95 **8.49 ms**, p99 **10.55 ms** (`m1-core-optimized-benchmark.json`). A larger finite workload then measured normal-load p95 **9.43 ms** and 2× burst p95 **30.08 ms** (`m1-core-benchmark.json`). These remain core-only results; cold snapshots and end-to-end queue/render/capture are separate.

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

## Finite managed-recording benchmark

`tools/benchmark_recording.py` complements the synthetic reducer benchmark. It reads an explicitly selected finalized managed capture and exercises the production `RecordingReader`, `Recorder` worker, `RecordingProjection`, and both raw and normalized `ReplaySession` paths. It does not import a toy reducer, open exchange connections, replace the source capture, or claim original network/publication latency.

Run with the repository's recording dependencies installed. Both source and output arguments are required; the source is a **session directory**, not its containing store. The output root must not exist and cannot be inside the source session. Each invocation creates its own `baseline`, `burst_2x`, and `unpaced_saturation` managed stores plus `report.json`. Choose a new root for every comparison; do not reuse or remove the evidence from an earlier run.

The two retained full-depth inputs can be exercised with:

```sh
.venv-upgrade/bin/python tools/benchmark_recording.py \
  --source data/m2-smoke/live-cli-gry1ld8s/captures/9b355ab1-b2a2-4dbb-ae67-a47710272382 \
  --output-root data/m3-profile/complete-recording-benchmark \
  --max-source-records 10000 --max-source-bytes 1073741824 \
  --max-load-seconds 180

.venv-upgrade/bin/python tools/benchmark_recording.py \
  --source data/m2-soak/run-1793513547763496000/store/51d3dc95-be2e-4210-ad4a-b1b29e84476c \
  --allow-incomplete \
  --output-root data/m3-profile/incomplete-prefix-benchmark \
  --max-source-records 10000 --max-source-bytes 1073741824 \
  --max-load-seconds 180
```

The retained complete source's manifest SHA-256 is `c1f2b35a0f5c897679cc9aa53a2e6bece6019945e094f1daabb34b67bc237cbf`. The failed soak prefix's manifest SHA-256 is `f970da57bf56929251420243cb46be2d901445d31622d194cfe31dffcbfadc64`: 609 raw frames, 522 normalized events, 432 batches, and 60 checkpoints. Its original `queue_overflow` makes it **incomplete**, regardless of whether every retained byte is subsequently copied. The long collector command duration is not a healthy capture duration. A missing retained Gap does not establish that no input was lost.

These are command recipes, not new measured results or a claim that the failure is fixed. Public capture files remain local/ignored and are not redistributable fixtures. Integration must run the commands after all production changes land and preserve both reports when comparing revisions.

Final-code streaming run: `data/m3-profile/final-complete-streaming/report.json`, retained as [`m3-recorder-final-streaming.json`](evidence/m3-recorder-final-streaming.json). All three finite loads retained **679 raw / 703 batches / 826 events**, with exact raw/normalized records, original final book/state, raw-versus-normalized replay, and strict checkpoint-seek parity. Effective periodic cadence1000 intentionally differs from the source's100; regenerated checkpoint bytes are therefore not identical. Required boundaries produced144 checkpoints. Hardware: MacBookPro14,1, Intel i5-7360U2.30GHz,4 logical CPUs,8GiB RAM; Python3.13.5,macOS13.7.8.

| Final streaming load | Scheduled span s | Producer s | Queue high-water | Writer projection p95 ms | Accepted→worker p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 25.016 | 25.036 | 51 | 5.979 | 337.588 |
| 2× | 12.508 | 12.598 | 27 | 4.830 | 11.715 |
| Unpaced finite input | — | 4.391 | 90 | 3.420 | 440.023 |

These are recorder-path timings, not receive→valid or live-network latency. Cold snapshots/checkpoint serialization produce long tails: baseline accepted→worker p99 **706.423 ms**, writer-event-inclusive p99 **381.948 ms**. Zero rejected/unoffered inputs does not establish low latency. Streaming generation limits the unpaced load, so peak sustainable throughput/saturation is **not demonstrated**. Full stage distributions, CPU/RSS, event/depth mix and hashes are retained in the report. The separate bounded predecoded arrival-isolation run is required before claiming the writer handles intended burst arrivals.

Final bounded-predecoded run is retained in [`m3-recorder-final-predecoded.json`](evidence/m3-recorder-final-predecoded.json). The failed soak's original incompleteness remains sticky. Baseline and 2× both retained the entire available **609-raw/432-batch** prefix with exact raw/event/final book/state and checkpoint-seek parity. Baseline producer **6.785 s** versus scheduled **6.773 s**, queue high-water **228**; 2× producer **3.400 s** versus **3.387 s**, queue high-water **256**, synthetic lateness p95 **15.355 ms** / p99 **48.767 ms**. This confirms finite writer-arrival absorption, not a live-pipeline guarantee. Unpaced offers reached the unchanged512-item limit in **0.118 s**, with one rejected offer and **281 raw / 232 batches unoffered**. That truncated output is explicitly incomplete; raw/normalized replay still agreed on its retained prefix. Do not report the unpaced case as full-source parity.

### Workload and resource contract

All three loads use the source's exact session/clock identity, stream specifications, configuration, raw bytes, normalized transaction boundaries, and delivery ordering. The writer regenerates checkpoints through its normal projection. Production queue limits (512 items / 128 MiB), subscribed native depths, and required-event handling are unchanged. The report includes stream/assets/venue metadata, source-mode/event/update mix, maximum observed retained bid/ask levels by stream, receive-time spans, aligned-second peak rates, and measured achieved rates.

The baseline follows a **synthetic receive-monotonic-highwater schedule**. Raw frames are streamed in ingest-ordinal order ahead of the normalized batch that requires them; batches remain in callback delivery order, even when their receive ordinals regress. The schedule advances only to a new receive-time highwater. The 2x burst uses the same input/order with all schedule offsets divided by two. Old captures do not contain actual processing/publication timestamps, so neither schedule is a reconstruction of original callback publication timing. Input read/decoding cost and producer overhead can make deadlines late; that lateness is explicitly reported rather than hidden by per-item relative sleeps.

The third load offers the same finite source without pacing. It measures a finite saturation **attempt**, not a universal throughput limit. `queue_capacity_reached` is reported only for actual `queue_overflow`; otherwise saturation remains unproven because file generation, the producer, or short input may be limiting. A rejected offer ends that load without retrying, discarding required events, enlarging the queue, or silently slowing the source.

Admission defaults are 2 GiB of declared source files and 100,000 source records (the sum of raw frames, normalized batches, normalized events, and checkpoints). Larger sources are rejected before full verification, not silently truncated. After admission, the source receives full production verification. By default, processing retains only streaming iterator lookahead, production bounded reader caches/state, and a bounded timing reservoir (default 20,000 samples per stage; configurable with `--samples`). These are admission/history bounds, not a promise that decompressed resident memory equals compressed file size.

`--max-load-seconds` defaults to 180 seconds per producer schedule, followed by the production finalization deadline. It is checked between atomic operations and before sleeping to the next due item; it is not a hard process kill deadline. Source verification, hashing, replay/parity passes, a single synchronous decode, and file-system operations are outside that producer deadline. The CLI is finite because it traverses only the admitted recording; it is not a live collector or unbounded workload.

#### Bounded predecoded writer-arrival isolation

A reader-limited 2x run does not prove that the writer absorbed the intended 2x arrival rate. Preserve that streaming result, then use an explicitly separate writer-only arrival-isolation run:

```sh
.venv-upgrade/bin/python tools/benchmark_recording.py \
  --source data/m2-soak/run-1793513547763496000/store/51d3dc95-be2e-4210-ad4a-b1b29e84476c \
  --allow-incomplete \
  --output-root data/m3-profile/predecoded-cadence-1000 \
  --preload-byte-limit 134217728 --checkpoint-interval-batches 1000 \
  --max-source-records 10000 --max-source-bytes 1073741824 \
  --max-load-seconds 180
```

Repeat with `--checkpoint-interval-batches 100` and another fresh output root for a same-source comparison. `--preload-byte-limit` is opt-in and may not exceed **128 MiB**; the mode also enforces an independent **100,000 raw-frame-plus-batch item maximum**. The entire already-admitted source must fit both bounds. It is rejected rather than truncated when it does not. The tool accounts fixed list storage plus the production recorder's resident-size accounting for each retained immutable item, conservatively counting shared objects per item. Transient decoding allocations, production reader caches, and other process memory remain separate; this is an accounted retained-workload cap, not an RSS hard limit.

The preload/read/accounting pass runs once before any load timer and is reported separately with wall/CPU/RSS, item/byte totals and stage observations. Its reader is closed before the writer loads. Each load reuses those exact immutable raw frames and batches in the same order, without file generation on the timed arrival path. Public append validation/sizing, production queue limits, writer projection/persistence, output verification, and raw/normalized/checkpoint parity are unchanged. The report labels this mode `bounded_predecoded_writer_isolation`, not end-to-end capture. The same receive-highwater schedule is still **synthetic**; producer overhead/GIL contention can still produce measured lateness. Inspect scheduled span versus actual producer duration and lateness before drawing an absorption conclusion. Keep streaming and predecoded results separate, and do not edit benchmark/production source while a comparison run captures its hashes.


### What is actually timed

The JSON contains runtime/OS/CPU details, dependency versions, invocation/bounds, the tool hash, loaded workstation source-file hashes, source manifest/file hashes, and logical evidence hashes. Record hashing uses length-prefixed workstation wire bytes, preserving raw payload bytes and lexical normalized/checkpoint content while remaining independent of compressed file grouping. No Git state is altered.

| Report stage | Included work |
| --- | --- |
| `source_verification_and_hashing` | Managed integrity verification and streaming logical evidence hashing, outside load timing |
| `reader_batch_next`, `reader_raw_lookup` | Actual production reader calls, including bounded caches, file access and decoding where incurred |
| `load_generator_next_inclusive` | Streaming interleaving/reader work by default; traversal of retained immutable items in explicitly labeled predecoded mode |
| `producer_append_*_inclusive` | Public append validation, resident-size accounting and queue offer; **not** worker completion |
| `producer_queue_offer_*` | Actual production `_offer` call under its existing condition |
| `accepted_to_worker_dispatch_*` | Accepted item timestamp to writer method entry, including the worker's pre-dispatch disk check; not exact deque residence |
| `worker_raw`, `worker_events_inclusive` | Actual production write methods; event work includes nested projection/checkpoint/flush operations |
| `worker_projection_apply` | Actual writer-side projection application, not a copied implementation |
| `worker_flush_events`, `worker_save_projection_inclusive`, `worker_checkpoint_group` | Production Parquet flush/checkpoint stages; nested stages overlap |
| `replay_step_inclusive` | Production replay step including input lookup, projection, provenance/parity work and cursor updates |
| `replay_projection_apply`, `replay_raw_decode_and_projection` | Production normalized projection or raw decoding/projection respectively |
| `finalize_drain_and_seal_seconds` | Queue drain and managed output sealing, outside producer elapsed time |
| `worker_thread_wall_seconds`, `worker_thread_cpu_seconds` | Actual production worker lifetime and thread CPU; worker wall includes waiting for input and finalization, not just busy service |

Observed distributions report count, retained sample count, exact sum/max, and nearest-rank p50/p95/p99. All observations are retained until the reservoir cap; thereafter quantiles use a deterministic reservoir and are explicitly labeled sampled. Iterator/step timings include their final end-of-input call. Missing observations are not fabricated. Timing wrappers, locks and sampling perturb the benchmark; nested timings must **not** be summed as independent work.

Each load reports wall time, whole-process CPU, worker-thread CPU when the worker has terminated, process-lifetime peak RSS, queue count/byte highwaters, written/accepted/unoffered/rejected counts, terminal failure, gaps and incomplete reasons. RSS is not isolated per-load allocation; process CPU includes producer and worker. The three loads run sequentially in the same process, without evicting OS caches. Original capture drop counts and queue highwaters are unavailable from the manifest and are reported as unknown, not zero.

### Parity and incomplete evidence

The source is replayed from the beginning in normalized and raw modes. Each finalized benchmark output is independently verified, hashed, replayed in both modes, and sought to its end with strict checkpoint restoration (`checkpoint_policy="reject"`). Reports distinguish:

- Exact retained raw evidence and normalized delivery evidence.
- Exact regenerated checkpoint evidence, including observer state and checkpoint positions.
- Final source/output book and state parity, and raw/normalized replay state parity.
- Checkpoint-restored final state versus that output's own linear replay.

An output copied from an incomplete source is marked `benchmark_source_incomplete`. Exact original source reasons are stored separately in manifest configuration as `benchmark_source_incomplete_reason_count` and indexed `benchmark_source_incomplete_reason_00`… entries; the new writer's bounded64 diagnostic slots remain available for its own failures. Original source configuration pairs remain unchanged. Inputs whose combined configuration/provenance exceeds256 pairs, or already uses this reserved namespace, are rejected before writer creation rather than losing metadata. An old fatal source reason does not falsely mark the new writer terminal, and successful copying never upgrades an incomplete source. A failure or deadline produces an explicitly incomplete output; unoffered and accepted-but-not-written counts remain visible.

Earlier retained benchmark reports used `source:`-prefixed diagnostic reasons. Their source had fewer than64 reasons, so those recorded measurements remain intact. Final review exposed loss of the64th source reason at maximum capacity; the separate manifest-configuration representation fixes that boundary without changing market inputs, queue/depth limits or successful writer processing. Measurements remain tied to the exact code/tool hashes in each report.

Exit zero means the baseline and 2x loads completed with the required evidence/state/checkpoint parity and without a new terminal recorder failure. It does not mean the input was complete, prove saturation, or certify a live soak. Saturation rejection is an observation and does not by itself fail successful paced-load acceptance. A paced failure or parity discrepancy returns nonzero with `report.json` retained.

For an explicit checkpoint storage/seek tradeoff experiment, append `--checkpoint-interval-batches 100` or `--checkpoint-interval-batches 1000` and use distinct fresh output roots. Omitting the flag uses the production default of **1000 batches**; this tool never changes that default. The report records the optional override, actual output limits, source interval, effective writer interval, and whether those intervals match. The comparison uses the **effective output cadence**, whether selected by the production default or an explicit override. If it differs from the source (including older 100-batch captures), checkpoint byte/position identity is still reported truthfully but is not an acceptance requirement. Matching cadence still requires exact regenerated checkpoint evidence. Raw/normalized evidence, final book/state parity, and strict output checkpoint-to-linear state parity remain mandatory in both cases. Compare file bytes/checkpoint counts and seek time as well as worker throughput when evaluating the tradeoff. Recovery-boundary checkpoints remain governed by production code.

No tests, builds, linters, formatters, live collection, or benchmark acceptance runs were executed as part of implementing this tool. Integration owns those gates and the corrected live soak.

## Terminal rendering benchmark

Build the frontend first, then run the owned-server benchmark while other local
collectors, test suites and verification browsers are stopped:

```sh
.tools/node/bin/node tools/benchmark_terminal.mjs \
  --url http://127.0.0.1:8010 --seconds 30 --speeds 1,2 \
  --instrument binance-global:spot-v3:spot:BTCUSDT \
  --viewport 1440x900 --output data/render-demo-1440.json
```

Repeat at `--viewport 1280x800` with a fresh output path. For an actual managed
capture, add `--manifest /absolute/path/to/manifest.json` and an appropriate
`--start-delivery`; the finite source must contain the entire measured interval
at the requested speed. The demo is explicitly synthetic, not captured traffic.
The selected viewport, full source catalog/identity, native depth, visible
liquidity/trade coverage and exact display controls are retained in the report.

The tool rejects occupied ports, starts and reaps its own Python API child,
disables inherited Python paths and stale bytecode reuse, and verifies the
served frontend bytes against the declared build. `benchmark_runtime.py` hashes
the resolved Python executable, shared Python library, loaded module files and
installed distribution versions before each cohort and at shutdown. Hashing is
outside measured cohorts. Runtime/module, project, dependency-manifest, built
asset or dataset drift prevents certification. Evidence files and screenshots
are exclusive-create, not overwritten by a later attempt.

ACK samples are selected by **request issue time**, not response arrival.
Responses drain for up to 15 seconds after the interval; failed, missing,
duplicate or malformed ACK evidence is not silently discarded. The only
permitted obsolete-generation HTTP rejection is a canonical older generation
of the pinned session, issued strictly before the cohort. Post-cohort HTTP
errors do not become setup exceptions. A canceled preparation ACK is retained
separately and accepted only with that same strictly older canonical generation
and pre-cohort issue fence; no measured ACK cancellation is excused.

The cohort pins session, generation, source, native instrument and playing
speed. It requires advancing replay delivery, selected-book receive-monotonic
time and chart windows across the interval, with no observed progress stall
over three seconds at 1x or faster (scaled inversely for slower playback).
The selected-book source-time rate must be within 10% of
the requested speed; its sampled achieved rate is reported, not inferred from
the speed selector. Minimum evidence counts, API errors, queue/evidence
overflows, frame ACK eviction and early source exhaustion remain separate
acceptance checks.

Pan (`ArrowLeft`) and zoom (wheel) must change their respective declared
geometry and reach an observed painted view. Control-to-paint and subsequent
Follow recovery are measured separately before the steady-streaming cohort.
Cursor responsiveness is sampled during streaming. Browser paint opportunity,
Canvas draw time, renderer CPU/heap observations and server publication-to-ACK
round-trip upper bounds are distinct; none is a GPU presentation timestamp.
The original 250 ms p95 publication-to-render budget is retained as a
conservative publication-to-ACK upper-bound check. A failed upper-bound check
does not prove one-way/GPU latency exceeded 250 ms.

Reports from earlier tool revisions remain evidence for their declared
measurement scope, not certification under these stronger lifecycle and runtime
checks. Current acceptance results belong in the milestone handoff alongside
failed/inconclusive attempts; implementing this tool alone is not a benchmark
pass.

### M4 measured acceptance and retained limits

The corrected absolute-deadline replay passed the declared synthetic180-second,
24-stream workload at both laptop sizes. Each speed had a30-second measured
cohort; all four cohorts passed lifecycle, achieved-rate, evidence, API and
provenance checks.

| Viewport | Requested speed | Achieved source seconds / wall second | Publish-to-ACK p95 ms |
| --- | ---: | ---: | ---: |
| 1440×900 | 1× | 1.0046 | 150.971301 |
| 1440×900 | 2× | 2.0350 | 203.463948 |
| 1280×800 | 1× | 1.0040 | 158.288256 |
| 1280×800 | 2× | 1.9909 | 196.094903 |

This is **not** full-depth recorded-load acceptance. The90-second real
Binance-global/Hyperliquid BTC/ETH/SOL capture (12 book/trade streams, manifest
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`)
retained functional/provenance correctness but measured only0.7685×/0.7320×
under requested1×/2×, with publication-to-ACK p95
438.695485ms/381.673751ms. Both cohorts are **inconclusive for the requested
rate and fail the original upper-bound budget**. The250ms threshold and10%
rate tolerance are unchanged. No depth or source events were removed.

`evidence/m4-real-replay-profile.json` separates production projection/reader
work from visualization/analytics on the same600 deliveries spanning9.176036
source seconds. Under cProfile these took2.958s/6.126s. The added observation
work is measurable, but this isolated profile does not explain all the
HTTP/browser workload slowdown; frame serialization, queries and owner
contention remain separate profiling targets. Full-depth performance and
sustained-capture acceptance remain open for M6, not silently replaced by the
passing synthetic result.

## Measured checkpoint serialization correction

`evidence/m4-wire-capacity-comparison.json` compares the pinned pre-correction
converter with the current converter on the same restored real checkpoints
from the retained incomplete30-minute capture. Three interleaved forced-cold
rounds per variant retained identical serialized and compressed hashes.

| Delivery | Baseline median total ms | Corrected median total ms | Corrected traced Python peak bytes |
| --- | ---: | ---: | ---: |
| 1000 | 601.81 | 601.26 | 18,785,098 |
| 65000 | 762.64 | 718.55 | 30,635,688 |
| 78000 | 763.56 | 715.89 | 34,972,222 |

The bounded per-call Decimal memo now accommodates more native checkpoint
values, and lexical encoding avoids a redundant conversion after validation.
Native-depth, queue and capture-fidelity limits are unchanged. These are
checkpoint construction/serialization/gzip diagnostics, not receive-to-valid
latency, disk/fsync capacity or a successful sustained-capture claim. Three
samples do not establish latency percentiles; the separate tracemalloc pass
perturbs timing and measures Python allocations rather than process RSS.
The original queue-overflow evidence remains failed. Full pipeline/soak
acceptance remains required in M6.

### Full-depth frame transport and observer correction

The production-frame profile `evidence/m4-real-presentation-profile.json` shows DTO conversion dominating frames carrying roughly10,000 native levels and growing display-evidence detail. The terminal already uses separate bounded ladder/history endpoints. Frame transport now publishes100 levels/side,200 profile rows and100 evidence records with explicit omitted counts; metrics, cost walks, native coverage and recording remain full-fidelity. Exact trade-evidence matching is indexed by price/taker side, preserving order and `(start,end]` matching.

| Same real12-stream capture, bounded frame | Requested speed | Achieved source/wall rate | Publish→ACK p95 ms | Workload certification |
| --- | ---: | ---: | ---: | --- |
| 1440×900 | 1× | 1.043115× | 122.492967 | Passed |
| 1440×900 | 2× | 1.521061× | 113.046112 | Inconclusive: under requested rate |

These30-second cohorts keep the original250ms budget and ±10% requested-rate tolerance. Functional, sample, runtime/assets/source and API checks passed, but the2× rate still fails certification; a responsive browser is not proof of sufficient native playback throughput. Raw local evidence: `data/m4-benchmark/bounded-presentation-real-1440.json`.

The subsequent2500-delivery profile covers44.476source seconds. Projection/reader took12.853profiled seconds; adding visualization/analytics took30.456. `_book_event` accounted for11.670cumulative seconds, including236,382 persistence-record constructions. `evidence/m4-real-replay-bounded-profile.json` retains the complete source hashes and call attribution. This motivates binary membership lookup over already validated sorted native levels and private mutable standing-evidence state, frozen only at public snapshot/checkpoint boundaries. Profiling overhead is material; actual browser rate and exact state-hash parity must verify the correction.

The same2500-delivery profile after the observer correction took19.437seconds with visualization/analytics; projection-only stayed12.929seconds. Both modes retained identical native book hashes, exact complete observer/state hashes and final delivery2800 against the before profile. `evidence/m4-real-replay-indexed-profile.json` retains the after code hashes and attribution. This is actual deterministic parity, not inferred from tests; browser-rate certification remains a separate gate.

### Certified real-capture render cohorts

After that correction, both laptop sizes passed actual1×/2× source-rate, functional, sample, API, lifecycle, loaded-runtime and served-asset checks on the same12-stream capture. All30-second cohorts retained the original250ms budget and ±10% rate tolerance.

| Viewport | Requested speed | Achieved source/wall rate | Publication→ACK p95 ms |
| --- | ---: | ---: | ---: |
| 1440×900 | 1× | 1.024933× | 221.510819 |
| 1440×900 | 2× | 2.065725× | 124.768204 |
| 1280×800 | 1× | 1.004050× | 102.686728 |
| 1280×800 | 2× | 2.046775× | 127.937354 |

`evidence/m4-render-attempts.json` retains these results beside every earlier failed/inconclusive attempt, exact commands/hashes/hardware, distributions and controls. Both `m4-real-render-{1440,1280}-2x.png` surfaces were visually inspected. The conservative historical32MiB budget truncates deep-book history well before some requested windows; missing regions remain hatched and disclosed. A busy owner can temporarily publish labeled last-completed diagnostic evidence. Neither condition is hidden by the benchmark. These results close finite playback/render acceptance, not sustained recorder or live-execution readiness.

## M5 measured recorder burst capacity

The merged-M4 public soak again reached the original512-item count bound;
`evidence/m5-merged-m4-soak.json` retains the incomplete capture and verification.
The queue was empty in41/54 sampled health observations. Retained receive-time
bursts reached1,114 raw-plus-normalized items in0.5s and1,566 in2s. Exact live
callback/checkpoint overlap was not measured and remains an inference.

`evidence/m5-recorder-capacity.json` retains the declared hardware/source pins,
original and2× finite schedules, writer/producer CPU, service profiles, queue
samples, drain measurements and exact raw/native/checkpoint parity. At2×,512
slots overflowed while2,048 retained all8,258 offered items, peaked at591 items /
9,226,391 accounted bytes and drained/sealed in0.711s. The independent128MiB
cap, native depth, required-event fidelity and checkpoint cadence are unchanged.
This measured finite-burst correction does not establish a30-minute or24-hour
soak pass. Historical512-item measurements above remain unchanged.

## Later M5 service correction and M6 acceptance

The preceding512/2048-slot results remain historical. A later live run exhausted
2048 recorder slots while the first Arrow table conversion initialized lazily.
Production now prepares an empty table under the real schema before admission
and uses4096 items with the unchanged128MiB byte cap. No raw input, required
normalized transaction, native depth or checkpoint cadence was discarded to
make this pass. The retained failed run, measured correction and fresh complete
18-channel30-minute capture are linked from [current readiness](READINESS.md).

The terminal's separate required analytical sink uses256 items/128MiB, counting
detached in-flight ownership until completion. It submits up to32 already-
admitted deliveries per worker call in original atomic order; it does not
coalesce analytical evidence. Per-immutable-book depth totals and frozen
standing-liquidity tuples are reused only while their native book identity is
unchanged. Freshness, clocks, trade windows, eligibility and checkpoints remain
independently evaluated.

The isolated5,000-bid/5,000-ask,100-clock-query diagnostic measured:

| Implementation | Wall seconds | Process CPU seconds |
| --- | ---: | ---: |
| Before immutable-book cache |3.415724936 |2.935197 |
| After immutable-book cache |0.094367886 |0.091319 |

Both the analytical snapshot hash
`d500b09b8b93c6ff8ebac290dbfc79644bb760bc554fafbf03dc248270af5957`
and checkpoint/state hash
`45f1baad1bf2dd28ac1e48a79545fb6d06abbd500a4fb6d0d59bb94656322c42`
were unchanged. The before profile exposed four million repeated valuations;
the fixed input/clock/script and original failure evidence are retained in
`evidence/m5-depth-service-before.json`, `m5-depth-service-after.json` and
`m5-service-integration.json`. The earlier asynchronous live profile also
included unrelated event-loop work and is not exclusive owner CPU attribution.

Current M6 native receive-to-validation and four real1x/2x render cohorts,
including startup failures, causal measurement windows, limited burst margin,
all commands/hashes and screenshots, are reported in [readiness](READINESS.md).
Do not substitute reducer-only timing, a synthetic scheduling queue, original
recording timestamp age or browser draw submission for those boundaries.
Measured normal-load budgets currently justify retaining Python rather than
introducing an unmeasured cross-language component.

## M6 complete-source finite replay

[Full report and embedded harness](evidence/m6-pipeline-90s.json) measured the
unchanged final native implementation on macOS13.7.8/Darwin22.6.0,
MacBookPro14,1, Intel i5-7360U2.30GHz,4 logical CPUs,8GiB RAM, Python3.13.5.
The complete12-stream Binance-global/Hyperliquid BTC/ETH/SOL source manifest is
`d12553c14ee31de1e66db3d2e9247000b81a976826f4f3da8ae072d4622ee5f5`:
4,252 raw frames,4,276 batches,4,523 events,192 checkpoints. Event mix is
2,371 book updates,2,068 trades,72 controls and12 explicit `owner_stopped` gaps;
zero gaps declare capture loss. Original dropped counts/queue highwaters were
not recorded and remain unknown. Neither venue supplies a book checksum in
these streams; sequence/shape validation is not a checksum claim.

All native depth was retained: Hyperliquid20 levels per side; observed Binance
maxima by book were4834/5000,5016/5002 and5008/5015 bid/ask levels. Native
retained depth is not complete venue-global liquidity. The source's89.207064s
raw receive span averaged47.664387 frames/s and50.702263 events/s; the peak
receive-highwater-aligned one-second bucket had193 raw frames.

Both paced runs decoded every raw frame exactly once, matched every normalized
transaction byte-for-byte and matched final book/state/clock. Scheduling follows
receive highwater in recorded delivery order, preserving994 disclosed receive
regressions rather than reordering source evidence. This is a bounded512-token
metadata queue feeding serial native raw replay, **not** the live feed queue.

| Finite load | Scheduled batches/s | Wall s | CPU s | Peak RSS bytes | Metadata queue highwater | Rejected / remaining |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
|1x|45.004662|97.658637|26.283855|227897344|111|0 / 0|
|2x|90.009325|49.766759|23.790763|217563136|228|0 / 0|

Every one of2,297 committed valid incremental transactions contributes to each
row below. Cold/recovery bootstrap, replacement/reset snapshots, staged updates,
trades and controls/gaps remain separate populations in the full report.

| Load / boundary | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: |
|1x native decode|0.511210|1.518246|4.004929|
|1x native reducer apply/validation|3.155741|7.287113|9.461381|
|1x complete raw replay step, including reader/parity/normalization|6.429200|12.744653|16.911342|
|1x synthetic metadata queue wait|0.269738|59.241244|1292.972496|
|2x native decode|0.463126|1.293993|3.935874|
|2x native reducer apply/validation|2.953460|5.884393|8.815897|
|2x complete raw replay step, including reader/parity/normalization|5.898796|11.143244|17.340272|
|2x synthetic metadata queue wait|4.555066|1754.379602|2354.861307|

Nested stages overlap and must not be added. These queue tails are retained,
not called live-latency passes. The original historical recording predates the
final source: its receive-to-normalized upper bound for valid incremental
transactions was p9567.236081ms/p99989.912102ms, exceeding the initial budgets.
It cannot separate original decode/validation or establish exact receive-to-valid
latency. Original timestamps remain unchanged. The separately captured current
native live measurement in [readiness](READINESS.md) establishes only its declared
finite normal-load window, not an unconditional latency SLO.

## M6 recorder throughput and explicit saturation

[Complete writer report](evidence/m6-recording-90s.json) uses the same hardware,
complete source and unchanged production checkpoint cadence1000. All8,528
raw/batch inputs were predecoded under a128MiB admission limit; production
resident accounting charged38,146,891 bytes. Reader/decode transients and process
RSS are separate. Runs were sequential, without a concurrent collector/browser.

| Load | Offered batches/s | Written batches/s including startup/drain/seal | Load wall s | CPU s | Process lifetime peak RSS bytes | Queue highwater items / bytes | Complete |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
|1x|45.001767|43.609867|98.051204|20.164639|258768896|161 / 8181261|yes|
|2x|89.998083|83.540845|51.184543|20.358186|335446016|229 / 8376926|yes|
|Unpaced finite saturation attempt|7784.556587|326.744213|6.319928|6.140228|346251264|4096 / 24957093|**no**|

The highest tested **complete paced** input sustained89.998083 batches/s for
47.512123 producer seconds, with83.540845 written batches/s over its entire
51.184543-second load. This is a finite demonstrated rate, not the absolute
maximum sustainable throughput or a long-run guarantee. Both paced outputs
preserved all raw/normalized/checkpoint logical bytes, all counts and final
book/state; raw/normalized replay and strict output checkpoint seek matched.
Both drained fully with zero rejection, zero accepted-but-unwritten records and
no deliberate drops. Their12 source gaps are explicit owner stops, not capture loss.

The unpaced attempt reached the real4,096-item production limit and rejected one
offer. It retained2,054 raw frames/2,065 batches, left2,198 raw frames/2,211 batches
unoffered and sealed **incomplete** with `queue_overflow` and
`benchmark_offer_rejected`. All accepted records drained; its retained-prefix
replay/checkpoint parity passed, but it does not match the complete source.
The326.744213 partial-write rate is not a healthy sustainable-capacity result.
No retry, hidden backpressure, increased queue or payload clipping converted
this overload into a pass.

| Load / measured boundary | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: |
|1x accepted normalized batch to worker dispatch|1.672749|77.358578|479.059693|
|1x worker event handling inclusive|1.896960|5.899325|14.765342|
|2x accepted normalized batch to worker dispatch|3.776684|394.746105|573.686811|
|2x worker event handling inclusive|1.817219|7.152962|14.229982|
|Unpaced accepted normalized batch to worker dispatch|3342.832984|4671.625415|5331.296837|
|Unpaced worker event handling inclusive|0.269659|3.997970|9.306110|

Dispatch includes pre-dispatch disk checks; inclusive nested stages overlap.
These are writer observations, not live receive-to-valid or browser latency.
Original capture dropped counts/queue highwaters remain unknown. The retained
version1 report incorrectly described original processing/publication stamps as
absent: this writer benchmark **did not analyze them**. The finite pipeline
report above does analyze available original stamps. The tool now emits version2
with `source_processing_and_publication_timestamps_analyzed=false`, not an
availability claim. Original reports were not rewritten. The corrected actual
tool completed all three native2-second demo loads with full source parity;
[smoke evidence](evidence/m6-recording-disclosure-smoke.json), Ruff lint and
format checks passed. Native production source and measurement code were unchanged.

The original complete source was copied non-destructively out of its temporary
worktree into ignored local data; all77 regular files matched SHA256:
[retention receipt](evidence/m6-source-retention.json). Large recordings are not
published to Git. On this workstation, reproduce with a fresh output directory:

```sh
uv run --locked --offline --no-sync python tools/benchmark_recording.py \
  --source data/m6-reference/4498db3b-f4a2-4fff-a32e-a97221fa21ee \
  --output-root data/recording-benchmark-reproduction \
  --max-load-seconds 180 --preload-byte-limit 134217728
```

Other machines must supply a verified source or generate the licensed synthetic
demo; they must not label a different dataset as this measured source.
