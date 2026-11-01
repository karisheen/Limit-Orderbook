# Durable recording (storage schema 2)

`src/workstation/recording.py` is the storage implementation. It uses the existing
exact wire codec, shared `RecordingProjection`, pinned PyArrow/Parquet and DuckDB,
and Python's SQLite/gzip libraries. This document describes implementation and
consumer contracts, not a claim that tests, live capture, or soak checks ran.

Schema 2 is a deliberate physical-format cutover: schema-1 captures are rejected,
not silently migrated. Public recorder/reader transaction APIs are unchanged.

## Public API

```python
StreamSpec(stream_id, instrument, key, adapter_revision, config_hash,
           adapter_config=())
Recorder(root, *, session_id, clock_id, streams=(), config=(),
         code_revision="unknown", provenance="public market data",
         license="unspecified", limits=RecorderLimits())

recorder.register_stream(spec)
recorder.append_raw(frame)
recorder.append_events(tuple_of_whole_source_message_events)
recorder.append_checkpoint(book_checkpoint, *, delivery_index=None,
                           observer_state=b"")
recorder.mark_incomplete(reason)
manifest = await recorder.finalize()

with RecordingReader(session_directory, verify=True,
                     allow_incomplete=False) as reader:
    reader.manifest
    reader.iter_batches(start_delivery=1, stop_delivery=None)
    reader.iter_raw()
    reader.raw_frame(stream_id, ordinal)
    reader.iter_checkpoints(before_delivery=None)
    reader.resolve_delivery(ordinal, event_index=0)
    reader.query_history(stream_id=None, start_ordinal=None,
                         end_ordinal=None, limit=1000)
    reader.verify()

recover_recordings(root)
```

Range endpoints are inclusive. `iter_batches` returns immutable
`RecordedBatch(delivery_index, events)`. `iter_checkpoints` returns immutable
`RecordedCheckpoint(delivery_index, checkpoint, observer_state)` in descending
delivery position. `raw_frame` is an indexed point lookup and returns `None` for
absent evidence. `resolve_delivery` requires a captured event position and
resolves to its entire atomic source message; it never selects a partial message.
`query_history` returns a bounded tuple of exact envelopes in delivery order.
`Recorder.path`, `session_id`, `clock_id`, `last_delivery_index`, and immutable
`health` are public. `SessionManifest.to_dict()` is JSON-ready and string-safe.

The session ID is a single safe path component (1–128 characters, ASCII letters,
digits, dots, underscores, hyphens, starting with a letter/digit). Existing session
directories are never overwritten. Construction validates storage and dependencies
before feed leases start. A `StreamSpec` contains the complete immutable
`Instrument`, actual canonical `SubscriptionKey`, adapter revision/configuration
identity, and public adapter options. The storage allowlist is `dex`,
`poll_interval`, `dedup_capacity`, `max_levels`; adapter construction independently
rejects unsupported options. Current venue constructors consume only Hyperliquid
`dex`. Supervisor configuration belongs in `Recorder.config` (immutable text pairs),
including `poll_interval` and `dedup_capacity` when not using defaults.

`code_revision`, `provenance`, `license`, and `clock_id` must be nonempty strings of
at most 4,096 characters. Initialization bounds the complete encoded metadata to
16 MiB before creating session files, then reserves its bytes plus 128 KiB for
initial SQLite/catalog pages and journaling against the disk policy before writing.

Stream registration precedes its raw and normalized data. Re-registering identical
metadata is idempotent; replacing a stream's metadata is an error. All frames and
envelopes must match the recorder's session/clock and registered metadata.

## Physical schema and ordering

A managed root contains `catalog.sqlite3` and one directory per session. The root
catalog has `sessions(session_id,path,state,created_ns,bytes)` and
`settings(key,value)` tables. Session files are:

- `.active`: advisory lock held exclusively by the writer. Readers hold a shared
  lock for their lifetime, also protecting open history from retention.
- `session.json`: immutable initialization/provenance journal. It is not the
  completion authority; dynamic stream metadata is sealed in the final manifest.
- `raw/00000001.jsonl.gz`, etc.: rotated gzip JSONL, one independent gzip member
  per original `RawFrame`. The wire payload includes base64 original bytes, full
  receive/connection/endpoint provenance and payload SHA256. Exchange JSON is
  never parsed and reserialized by raw storage. Member offsets and compressed
  lengths permit bounded random access without decompressing earlier frames.
- `normalized/<stream-id-sha256-prefix>/00000001.parquet`, etc.: stream-partitioned
  Zstd Parquet. One row represents an entire delivered callback transaction;
  bounded row groups combine up to `parquet_batch_count` rows from the same stream.
  Per-stream buffering avoids writing tiny files at each cross-stream flush.
  Typed columns are `delivery_index`/`event_count` (int64), `payload`
  (binary), and text `stream_id`, `instrument_id`, `venue`, `ingest_ordinal`,
  `receive_wall_ns`, `receive_mono_ns`, `source_mode`, `segment_id`, `payload_kind`.
  The binary payload is the complete exact tuple of envelopes, including rejected,
  staged and local evidence; `payload_kind` lists the contained payload classes.
- `checkpoints/00000001.jsonl.gz`, etc.: bounded gzip JSONL blocks containing exact
  wire tuples of delivery position and complete `BookCheckpoint`. Every checkpoint
  remains a separate indexed row; grouping does not remove source boundaries.
- `checkpoints/aux-<sha256>.json.gz`: content-addressed exact projection auxiliary
  bytes stored once and shared by a checkpoint group, rather than copied into every
  stream checkpoint. `checkpoint_aux` file descriptors and all references are
  verified. No pickle or executable configuration is loaded.
- `index.sqlite3`: immutable per-session metadata/index. `raw` indexes
  `(stream,ordinal)` to path/member offset/member length/payload hash; `batches`
  indexes delivery position to stream/ordinal/event-index range/file/row group/row
  offset, count and receive clocks; `checkpoints` indexes delivery/stream to file,
  row offset and auxiliary-state file. `files` stores descriptor bytes; `metadata`
  stores session and clock IDs. Inserts are
  batched on the writer, not performed synchronously per producer callback.
- `manifest.json`: completion authority with storage/wire schema, full stream
  metadata, config, adapter/code revisions, provenance/license, creation/finalization
  wall clocks, explicit selected-stream capture scope, merge-order declaration,
  immutable gap evidence, sticky incomplete reasons, total counts, limits, and
  every sealed file's SHA256, byte length, row count, ordinal/delivery ranges and
  stream partition. The catalog's own hash is in the manifest, not recursively
  inside itself. `manifest.complete` is not inferred from file existence.

Raw ordinals represent session-global receive order. `delivery_index` starts at
one and increases for each normalized callback offer. It preserves actual
cross-stream decoding/completion order, which can differ from receive order.
Exchange time is never used as a global sort key. Missing ordinal integers can
belong to unselected streams and do not imply loss. Explicit `Gap` envelopes carry
known stream-scoped missing ranges; `capture_lost=True` makes capture completeness
sticky false. A continuity gap without local capture loss does not claim dropped
capture bytes.

Every public-frame-derived envelope must carry the exact `RawFrame.ref()`:
`file_id="raw:"+sha256(UTF8(stream_id))`, receive ordinal, and original payload
SHA256. This is a logical per-stream raw dataset identifier, not a rotated filename.
Observed protocol pongs retain raw evidence too. Only `events.is_local_control` permits
missing indexed raw: an allowed supervisor control/gap with the exact reserved
`"local:"+sha256(UTF8(stream_id))` origin marker, matching ordinal and fixed SHA256
of empty bytes. Hashing supports full-length valid stream IDs without truncation.
That marker is not a claim of a captured exchange payload or existing raw file.
It cannot coexist with indexed raw at the same stream/ordinal. Missing public raw
or conflicting local origin makes the writer's manifest incomplete, and readers
reject the inconsistent evidence even with `allow_incomplete=True`.

All persisted envelopes, checkpoints and metadata use `canonical=False`.
`Decimal("99.00")` stays `"99.00"`; integer timestamps/ordinals and Decimal values
remain strings on JSON surfaces. Parquet ordinal/time text avoids int64 narrowing.
The wire allowlist, stable record type hints and immutable field descriptors are
cached. Each wire conversion also keeps a bounded 8,192-entry Decimal-text memo:
it validates each retained lexical spelling once without equating `"1.00"` with
`"1.000"`, and releases the memo after the call. Mutable encoded records are not
cached. These optimizations change neither allowed schemas nor lexical payloads.

An advancing source change checkpoints the complete preceding projection at
`delivery_index - 1` before resetting it. The source-change transaction itself is
always retained. A completely pristine post-reset reducer has no native event
identity and therefore no invented position-bearing checkpoint; seeking applies
the recorded reset after the preceding checkpoint and exposes no old-epoch book.
Post-boundary checkpoints are also retained when native states remain indexable,
including other streams. Gap/reset/disconnect checkpoints retain their existing
post-transaction position. This preserves schema 2 and existing reducer hashes.

## Writer bounds and checkpoint ownership

Offers are synchronous count/byte-bounded enqueue operations. They perform no
serialization, compression, filesystem writes or database work. Immutable event
resident size is conservatively counted by walking fields; raw accounting includes
the original bytes and all provenance fields. A bounded 128-type cache retains
immutable field names, not event instances, encoded values, or computed sizes.
Each occurrence of a shared field is still counted, preserving the queue's exact
accounting and rejection boundary. Both queued and currently in-flight items count
against the queue limits. Overflow raises `RecordingError`, records `queue_overflow`
out of band, and prevents further required offers. No loss-intolerant stream is
coalesced. `health` exposes state, queue counts/bytes, count/byte high-water marks,
written sealed-file bytes and incomplete reasons.

The read-only `health.terminal_failure` distinguishes a dead required sink
(`queue_overflow`, `writer_failed:*`, `finalize_timeout`, or `finalization_failed:*`)
from truthful but nonterminal source gaps. Supervisors must stop a terminal capture rather than
reconnect sources against the failed sink. A source capture-loss gap alone keeps
`terminal_failure=False`: subsequent raw and normalized evidence remains writable,
but cannot make the manifest complete again. If the bounded 64-reason diagnostic
list is already full, its last entry is replaced by the first terminal reason so
a dead sink cannot be concealed by earlier source diagnostics.
Manifest/catalog/final sealing failures are also terminal: no readable finalized capture exists. This classification is retained even when earlier source diagnostics already occupy all64 reason slots.

One daemon worker owns compression, SQLite, file writes and the shared deterministic
projection. It applies delivered normalized transactions rather than reading the
live supervisor's mutable reducer. Periodic and source-change/reset/disconnect/gap
boundaries serialize all available complete reducer checkpoints at the same
position with the same projection auxiliary state. Untouched reducers have no
position-bearing checkpoint. Finalization captures the last projected position.
Reducer restore checks strategy, adapter/config, metadata and state hashes;
storage checks envelope/checkpoint provenance and schema. This independent
projection costs an additional reducer pass, but avoids asynchronous checkpoint
races and does not clone venue algorithms.

Full-depth writer profiling must include the owned worker: profiling only
`append_events` misses checkpoint materialization and encoding. The preserved
incomplete 12-stream capture
`9ec8ec8a-5cd8-4fe3-807c-33b5549f746a` contains 440 raw frames, 445 atomic
normalized batches (533 events), and 60 complete logical checkpoints. Its
5000-level Binance books exposed repeated exact-wire conversion and reducer
checkpoint/restore work, rather than gzip checkpoint I/O, as the dominant writer
cost. That earlier codec/native-state correction did not change queue capacities,
full book depth, checkpoint cadence or source boundaries. Its offline profiling remains
explicitly incomplete and preserves its source loss reasons; it cannot establish
live capture throughput or repair the missing source evidence.

The resulting source correction is in the shared codec and Binance bootstrap
handoff, not a new storage format: the latter transfers validated native state
without an internal wire encode/decode round trip. Persisted checkpoints still
carry complete state, hashes and the same public schema. On this exact preserved
workload, owned-worker cProfile elapsed time changed from 31.688 to 19.714 seconds;
all raw, normalized and checkpoint/auxiliary aggregate hashes matched the original
capture, with the same 440/533/445/60 counts and limits. These instrumented offline
measurements include offer accounting and imports and are not a live latency
guarantee. Local profile artifacts are under
`data/m2-profile-writer/{before-isolated,after-union}/`. Committed source/config/file
hashes, comparisons and the executed scratch workload text are preserved in
`docs/evidence/m2-full-depth-profile.json`; the scratch files were removed after
verification. To reproduce, extract a `workloads` entry to its recorded relative
path and run the recorded command with a new output label on a chosen revision.
This requires the retained local capture, which is not redistributed. A label
does not restore historical code; new runs report the current source. Writer
profiling output remains explicitly incomplete, like the original source prefix.

The later failed full-scope soak retained only a prefix:
`51d3dc95-be2e-4210-ad4a-b1b29e84476c`, manifest SHA256
`f970da57bf56929251420243cb46be2d901445d31622d194cfe31dffcbfadc64`.
Its 609 raw frames span 6.083854537 seconds of receive time; 432 normalized
batches span 6.773025957 seconds including initial local controls. Peak rolling
one-second counts are 243 raw frames and 145 batches. Its 522 events comprise
24 controls, 157 book updates and 341 trades; full 5,000-level-per-side Binance
bootstrap books remain present. The command's roughly 30-minute lifetime is not
30 minutes of healthy capture, and the retained prefix has no `Gap` envelope
that could retrospectively account for the missing history.

For this same prefix, owned-worker cProfile observed 22.848 seconds before and
15.991 seconds after the resident-accounting change, with identical raw,
normalized and checkpoint/auxiliary hashes and 609/522/432/60 counts. This total
change is **not** attributed solely to sizing: unchanged codec/checkpoint stages
also varied with runtime contention. A separate paired experiment alternated old
and new sizing six times over all 1,041 offered objects, excluding source reads:
median thread CPU was 0.199886 versus 0.129981 seconds, a 34.97% reduction, with
exactly the same 15,869,406 summed resident bytes and per-item sizing hash.
It establishes cheaper producer accounting, not sustained capture capacity.
Exact profiles, source/output hashes and executed scratch workloads are retained
under `data/m3-profile-writer/`; raw public input is not redistributed.

`tools/benchmark_recording.py` is the reusable finite actual-path workload; see
`docs/CORE_PERFORMANCE.md` for invocation and stage definitions. It separates
reader/load-generation work, offers, queue dispatch, worker projection/checkpoint/
flush work and native raw/normalized replay. Incomplete-source benchmarking
requires explicit opt-in and never turns an available prefix into complete
history. Synthetic receive-highwater schedules do not recreate absent original
processing or publication clocks, and a late file-backed generator cannot prove
that the requested burst rate reached the queue.

An explicit 100-versus-1,000-batch cadence experiment used the same 1,041
predecoded raw/batch objects (15,910,622 conservatively accounted bytes), admitted
under a 128 MiB retained-byte cap and 100,000-item cap. The separate source
admission cap was 2,000 records/128 MiB of declared files. No source was truncated,
no book depth was reduced, and the queue stayed at 512 items/128 MiB.
Predecoding happened before load timing: these measurements isolate the actual
writer and producer offer/size checks, not upstream adapter decode or networking.
The file-backed mode was also measured, but missed its requested burst timing
because load generation competed with the worker.

| Bounded predecoded workload | Cadence 100 | Cadence 1,000 |
| --- | ---: | ---: |
| Baseline worker thread CPU / queue high-water items | 4.750 s / 311 | 2.780 s / 246 |
| 2x producer duration (requested 3.386513 s) | 3.409 s | 3.398 s |
| 2x worker thread wall / CPU | 5.646 s / 4.173 s | 4.102 s / 2.270 s |
| 2x queue high-water items | 512, overflow | 274, no rejected offers |
| 2x retained raw / normalized batches | 609 / 430 | 609 / 432 |
| 2x synthetic schedule lateness p99 | 42.856 ms | 55.668 ms |
| Full-prefix logical checkpoints | 60 | 12 |
| Full-prefix sealed data bytes | 4,673,512 | 1,768,232 |

With cadence 1,000, both paced schedules retained all 609 raw frames, 432 atomic
batches and 522 events. Raw bytes, normalized evidence, native raw-versus-normalized
final book/state, and strict checkpoint-seek-versus-linear state matched. Overall
checkpoint hashes intentionally differ: cadence 100 has twelve checkpoints at
deliveries 100, 200, 300, 400 and 432, while cadence 1,000 has twelve at final
delivery 432. Recovery boundaries and finalization still force checkpoints
regardless of the periodic interval. The default is now explicitly 1,000 batches
as a measured storage/seek tradeoff. For compatible observer-free checkpoint
seeks, this permits up to 999 rather than 99 transactions after the nearest
periodic checkpoint, with intervening recovery checkpoints reducing that work.
Observers requiring unpersisted history still recompute from the beginning.
Existing schema-2 captures retain their declared cadence and remain readable.
Both unpaced workloads still reached the queue limit and explicitly rejected an
offer; no unlimited/sustained capacity is claimed.
Worker wall includes scheduled idle/drain time; CPU is measured on the worker
thread. The 2x lateness values are synthetic scheduling observations, not network
latency. Reports and exact hashes are retained in
`data/m3-profile-writer/{cadence100,cadence1000,predecoded100,predecoded1000}/report.json`.
All outputs remain incomplete because the original source is incomplete. This
finite experiment is not a corrected live-soak claim.
The combined `data/m3-profile-writer/evidence.json` retains both streaming and
predecoded observations, failed loads, exact commands, tool/source hashes and
the source text of removed scratch profiling workloads.

| Limit | Default |
| --- | --- |
| Queue items / resident bytes (including in-flight) | 512 / 128 MiB |
| Raw rotation target | 16 MiB compressed |
| Parquet rows per stream group / global pending rows / serialized bytes | 128 / 4,096 / 16 MiB |
| Checkpoint rows per block / block target | 64 / 16 MiB |
| Each reader LRU cache: entries / accounted resident bytes | 8,192 / 32 MiB |
| Maximum serialized/decompressed row | 256 MiB |
| Maximum files / registered streams | 100,000 / 1,024 |
| Free disk floor | 64 MiB |
| Managed root budget | 10 GiB |
| Retained finalized sessions | 100 |
| Finalization deadline | 30 seconds |
| Periodic checkpoint cadence | 1,000 batches |
| History query result ceiling | 10,000 envelopes |
| Manifest / retained gap evidence | 16 MiB / 1,024 gaps |

Reader caches separately bound raw frames, Parquet row groups, decoded source
batches, checkpoint row blocks and auxiliary state (five caches, at most 160 MiB
accounted total with defaults). Oversized individual rows remain readable within
the decompression bound but are not cached. Key/map overhead is included in cache
accounting. Every access still checks path components for symlinks, including cache
hits; explicit `verify()` clears cached evidence before rescanning hashes and
semantics. `close()` releases caches and the retention lock idempotently.

A single atomic source row may exceed the rotation or Parquet batching target; it
is not split, and is still bounded by the maximum row and input queue limits.
Parquet's aggregate decompression bound is separate from the per-source-row bound:
multi-row groups allow twice `parquet_batch_bytes`, and single-row groups allow
twice `max_row_bytes`, each plus 1 MiB for encoding/page overhead. The second
payload-sized allowance covers the duplicate typed query columns. Writers enforce
the corresponding Arrow-table bound before sealing; readers check group metadata
before materializing it. `parquet_batch_bytes` cannot exceed 256 MiB. Thus two
individually valid rows need not fit into one row's byte limit to remain readable.
Pending serialized batches and projection state are separately bounded from the
producer queue. Decoder/reducer limits bound retained books/protocol buffers.
Oversized metadata, events, checkpoints or manifests fail explicitly. These are
safety capacities, not claims that worst-case buffers fit inside the queue byte
budget or that slow disks sustain any particular ingest rate.

Disk checks occur on the worker, except initialization preflight. Before writes it
reserves a conservative estimate against managed-byte and free-space ledgers,
ordinarily refreshed from filesystem usage at most once per second. Initialization
and finalization explicitly reconcile actual usage after their files are settled,
so unused SQLite/compression reservations cannot falsely exhaust the manifest's
budget. Reservations reduce the remaining free-space ledger immediately, avoiding
a `statvfs` call per tiny raw row. External writers can change disk space between
refreshes; OS write failures remain capture failures.
Retention runs before starting a new capture, comparing actual managed filesystem
bytes (including manifests, initialization metadata and root catalog overhead),
plus the new session's initialization reservation, rather than trusting catalog
estimates. It removes oldest eligible finalized managed sessions until count/budget
policy permits. Catalog session byte counts include ancillary session files.
Retention commits a `deleting` catalog state before removing an eligible directory,
fsyncs the root after removal, then deletes and commits the catalog row before
rescanning. Recovery finishes these durable intents (including partial removals),
honors any surviving reader/writer lock, and prunes finalized catalog rows whose
directories are missing. It neither resurrects a deletion intent as a completed
capture nor counts a vanished directory toward the session cap. Committing before
rescanning also prevents transient SQLite rollback journals from causing an
unnecessary second eviction. Retention never removes an active writer, open reader,
quarantine directory or arbitrary unregistered path. During an active capture,
exhausting budget fails capture rather than deleting its data.

## Finalization, verification and recovery

Sealed data files and the manifest are written through `.partial`, flushed,
fsynced and renamed. The worker finishes the per-session catalog before hashing it,
then seals the manifest and updates root catalog status. `finalize()` closes offers
and asynchronously waits for this worker within the configured deadline. It is
idempotent, and concurrent callers share one immutable result. It does not put an
unbounded `thread.join` into the default asyncio executor.

A timeout returns an incomplete manifest with `finalize_timeout`, leaves the
writer lock held until that worker actually exits, and does not expose a completed
readable session. Once the blocked operation returns, the abandoned worker removes
any completion manifest and releases its lock. The daemon cannot be force-killed
safely while inside a filesystem/compression call. Restart recovery quarantines
this interrupted directory. Worker exceptions become `writer_failed:<category>`,
fail subsequent offers, and prevent a complete manifest. Finalization/storage
failures are likewise explicit; some incomplete sessions contain unreadable
partials and are deliberately not presented as replayable histories.

The headless `record` command checks `RecorderHealth.terminal_failure` approximately
every 250 ms and stops/finalizes a failed required sink instead of spending the
remaining capture duration reconnecting against it. This is an event-loop polling
interval, not a hard real-time deadline: scheduling and atomic work can delay it.
It emits periodic `recording_health` JSON on stderr approximately every five
seconds, an explicit `recording_failed` diagnostic on terminal failure, and a
`terminal_recorder_failure` field in the final result. Source-only capture gaps do
not trigger this early stop. Source shutdown/drain and recorder finalization still
use their configured bounds; an unreadable timeout-abandoned directory is not
reported as a healthy capture.

`RecordingReader` verifies by default before trusting a session: catalog integrity
and provenance; declared paths and absence of symlink escapes; SHA256/size of every
file; catalog/manifest descriptors and counts; Parquet schema/row groups; indexed
raw coverage without hidden trailing members or offset gaps/overlap; batch delivery
continuity, event-index transactions and metadata; raw-reference payload hashes;
mandatory raw-origin references and indexed raw/normalized connection/receive-clock
provenance, with only explicitly marked local controls/gaps exempt from raw capture;
checkpoint payload hashes/provenance/positions; per-file ordinal/delivery ranges;
manifest totals and gap evidence. Both gzip CRC/truncation and exact payload hashes
are checked. Local file hashes prove consistency, not third-party authenticity.
`verify=False` is an explicit trusted-local fast path, not a claim of validated
capture; individual reads still enforce row, schema, path and identity bounds.

`allow_incomplete=True` permits inspection/replay only of structurally valid
incomplete captures; it does not bypass corruption checks. Reader iterators and
point lookup do not load the full capture. DuckDB queries accept only bounded
structured filters (no caller SQL), use a single thread and 128 MiB memory limit,
prune partitions/ranges, select delivery positions, and load bounded exact rows.
Ordinal comparisons use text length plus lexical order, not a lossy numeric cast.

`recover_recordings(root)` reconciles interrupted unlocked directories by verifying
finished manifests or atomically renaming untrustworthy sessions to
`quarantine-<session>-<wall-ns>`. It never infers success from `.partial` files or
silently truncates/repairs evidence. Active locks are skipped. The root catalog is
updated to `quarantined`; original bytes remain for diagnosis. A capture directory
with a valid final manifest remains available, and recovery upserts its verified
complete/incomplete status, path, creation time and actual session byte count into
the root catalog. This repairs a crash between manifest rename and catalog update.
Recovery runs at recorder startup and is also public for explicit operator use.

## Measured storage amplification

An isolated macOS x64/Python 3.13.5 profiling workload used the unchanged default
180-second synthetic source: 4,410 transactions, 24 streams and 1,452 logical
checkpoints. Schema-1 versus final schema-2 sealed output was:

| Evidence | Schema 1 | Schema 2 |
| --- | ---: | ---: |
| Normalized files / bytes | 840 / 41,979,989 | 48 / 774,577 |
| Checkpoint files / bytes | 1,452 / 14,519,617 | 84 / 1,794,128 |
| Shared auxiliary files / bytes | embedded above | 84 / 447,603 |
| Raw bytes | 2,204,109 | 2,204,109 |
| Index bytes | 2,744,320 | 1,994,752 |
| Total sealed bytes | 61,448,035 | 7,215,169 |

The 88.26% byte reduction retains every source transaction/checkpoint. Profiling
identified repeated realpath walks, repeated raw/normalized decoding, one-row
Parquet groups and repeated cross-stream checkpoint auxiliary state. In isolated
`cProfile` reader runs, mandatory verification took 33.887 seconds before and
16.797 seconds after; these include substantial profiler overhead.

A separate **unprofiled** final workload measured 7.248 seconds generation,
7.358 seconds mandatory verification, and 0.576 seconds subsequent cached batch
iteration. These are observations, not latency guarantees. Generation was timed
before the separate DemoCapture reader-ownership change, so it excludes the
separately reported verification phase. Do not compare profiler wall times with
normal startup latency, or claim test/live-smoke success from these measurements.


## Consumer coverage and integration checks

`tests/test_recording.py` supplies lossless raw/normalized/checkpoint round trips,
DuckDB range/limit consumers, reversed cross-stream delivery, scoped ordinal holes,
dynamic immutable metadata, complete-message positions, capture-loss evidence,
raw/Parquet/checkpoint/catalog corruption and truncation, traversal/symlink rejection,
rotation/index lookup, count/byte overflow, blocked-writer finalization, worker
exceptions, low disk/budget, active/open-reader retention, interrupted recovery,
grouped-row atomicity, shared checkpoint auxiliary state, warm-cache symlink faults,
cache limits, schema cutover, rollback-journal retention, idempotent finalization,
exact shared-field queue-byte boundaries, and fatal diagnostics after the bounded
reason list fills. CLI regressions distinguish terminal writer failure from a
nonfatal source capture gap.
The integration owner should run these with replay and CLI tests, then real
fixture/live-capture/corruption smoke. No test-gate result is asserted here.
