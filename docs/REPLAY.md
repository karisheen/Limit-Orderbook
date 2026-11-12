# Headless replay and recorder ownership

## Public interfaces

`RecordingReader(path)` verifies a completed capture before replay. Use
`ReplaySession(reader, mode="raw" | "normalized", observers=(),
checkpoint_policy="fallback" | "reject")` from `src.workstation.replay`.

- `step(count=1)` synchronously applies complete source-message transactions and
  returns delivered batches. It pauses paced playback first.
- `await play(speed=Decimal("1"))` runs until paused or finished. `pause()` wakes a
  pending pacing wait. `set_speed(Decimal(...))` changes pacing without changing
  reconstruction. Speeds must be positive finite Decimals.
- `await run_to_end()` runs unpaced, yielding to the event loop between batches;
  `await run_to_end(speed=Decimal("100"))` uses the actual paced path.
- `seek(ordinal, event_index=0)` pauses, clears future state, resolves the event to
  the **end of its complete delivered transaction**, then restores/recomputes up
  to that delivery. `seek(0)` returns to the empty initial projection. Receive
  ordinals can go backward between delivered batches from different streams;
  they are not replay order. `cursor.delivery_index` is the authoritative cursor.
- `seek_delivery(index)` selects an exact delivery transaction (zero clears the
  projection). The terminal uses this coordinate; CLI `seek`/`--seek` use the
  receive ordinal/event index above.
- `books()`, `book_hash`, `state_hash`, `cursor`, `status`, `checkpoint_status`, and
  `clock` expose the resulting state. `close()` releases the replay iterator;
  the caller owns the reader lifetime.

Only one task may own playback. Controls are single-event-loop operations, not
thread-safe mutation APIs. A paused presentation never pauses capture.

## One reconstruction implementation

Live ingestion and raw replay call the same `ingestion.decode_frame` and
`normalize_events` functions and the same built-in venue reducers. Raw replay
reads the original indexed `RawFrame`; it never reconstructs exchange JSON from
normalized events. It verifies the resulting exact normalized batch against the
stored batch, including checksum annotations and lexical Decimal scale.
Normalized replay bypasses source decoding, not reducer validation. Rejected,
ignored and bootstrap-staged source inputs still reach the reducer; a subsequent
recorded Gap remains a separate transaction.

Every public frame-derived envelope, including adapter acknowledgments,
heartbeats, rejections, resets and disconnects, carries `RawFrame.ref()`:
`RawRef(file_id="raw:<sha256(UTF8(stream_id))>", frame_ordinal=..., payload_sha256=...)`.
The identifier names a **logical per-stream raw dataset**, not a rotated file
path. Storage indexes resolve its session/stream/ordinal to physical bytes. The
reader and raw replay require the matching indexed frame, reference and receive
provenance; dropping raw bytes cannot turn a public control into a local event.
Observed local protocol-pong frames also require this raw linkage.

Genuinely local supervisor evidence uses `local_control_ref(stream_id, ordinal)`.
Its `local:<sha256(UTF8(stream_id))>` identifier is a reserved **local-control marker**, not an
existing raw file or an exchange payload. SHA256 of empty bytes is only the fixed
marker convention. Shared `is_local_control(event)` requires that exact marker,
matching ordinal, and a Gap or `source_change`, `clock_tick`, `reset`, or
`disconnect` control. These are the only events allowed without indexed raw.
A marker conflicting with indexed raw at the same stream/ordinal is rejected.
An absent reference does not certify either origin. The shared wrapping boundary
issues local markers only for supervisor-local controls; public adapter controls
always receive raw linkage.

`RecordingProjection` owns reducers, modes and bounded dedup state. Its
`process_raw(frame, segment_id=..., source_mode=...)` is also the offline demo's
source ingress; `apply_batch(RecordedBatch(...))` supplies normalized controls.
Local source changes and gaps are recorded evidence, not invented exchange
messages. Observed protocol pongs use the same live local-frame decode path.
A source change starts a fresh epoch/segment using the recorded mode. REST uses
`make_snapshot_reducer`, removing WebSocket bootstrap/sequence/checksum claims
while retaining the adapter's real price/depth rules and configured poll cadence.
Unknown history/depth is never filled.
When a capture attaches after an earlier live capture loss, source-change local
evidence carries `detail="capture_incomplete"`; the fresh projection preserves
that sticky quality flag instead of promoting the source to complete.

Adapter construction is allowlisted by the native venue catalog. A capture
cannot name an executable class. Revision/configuration compatibility is checked
before reconstruction. The only current constructor option is Hyperliquid's
public `dex`; unsupported configurations are rejected.

Dedup uses the shared live scope and bounded insertion-order rules. Captures may
intentionally exclude other active streams, whose activity can evict the live
global dedup cache. Actual normalized `duplicate_trade` exclusions are therefore
authoritative recorded **local processing evidence**: raw replay verifies the
original decoded trade identity and complete receive provenance, runs the same
reducer, and permits only the verified duplicate-trade exclusion transform.
Missing/conflicting annotations fail exact-batch verification. Replay does not
invent unrecorded-stream history or re-deduplicate accepted recorded trades.
Checkpoint dedup state and the projection hash describe selected-capture
downstream state, not the full hidden registry history; no global seed is needed.

## Clock, identity and hashes

`ReplayClock.reading` is the eligibility clock: its monotonic time is the current
recorded clock's receive high-water mark, never an older cross-stream delivery.
Thus delivering receive-20 before receive-10 cannot make an already-observed book
spuriously appear to originate in the future. Its wall time is absent because no
alignment is inferred. `source_reading` preserves the most recently delivered
source's original clock ID, receive-monotonic time and receive-wall time; all
stored envelopes remain unchanged.

Virtual pacing advances by nonnegative differences against each recorded clock's
high-water mark. A new clock starts with zero inferred elapsed time. Clock
switches are counted, not converted into an invented synchronized timeline.
Exchange times never sort or pace global replay. Speed affects waiting only and
is excluded from hashes and eligibility. If recorded processing/delivery timing
is added later, it may refine this clock using that evidence, never wall playback
speed.

Books retain original source identity, quality, mode and receive provenance.
Replay is a presentation mode, not a rewrite of recorded source quality.
`book_hash` is SHA256 of the canonical wire tuple of books in stream-ID order.
`state_hash` includes canonical reducer state, protocol buffers, quality,
sequence/order and captured dedup state; the diagnostic last-event envelope is
excluded because accepted checksum annotations and duplicate exclusion payloads
legitimately differ before/after normalization. Immutable checkpoint payloads
remain complete and lossless, including that envelope. Registered observer
checkpoint bytes and version/identity extend the session state hash. The clock's
own `state_hash` captures source readings, virtual elapsed time and discontinuity
frontiers independently of playback speed.

## Checkpoints and bounded seek

The recorder's writer thread owns its own deterministic projection. It never
reads a mutable live reducer concurrently. Offers from live callbacks enqueue
immutable raw/event records; decoding remains live, while checkpoint
serialization, file compression and database work remain on the writer.
This deliberately pays a second reduction cost to obtain race-free, complete
protocol checkpoints. Checkpoint objects are cached until the associated stream
changes, avoiding repeated serialization when an unchanged stream participates
in a cross-stream checkpoint group. Performance measurements belong to the
integration evidence; no unmeasured throughput claim is implied here.

A checkpoint group has one actual delivery index and identical auxiliary bytes
containing the stream modes/epochs, complete-state hashes and dedup history.
Restore requires the entire group, compatible metadata/adapter/config/strategy,
and matching capacities/capabilities. Initial untouched reducers are recreated
from explicit auxiliary epoch/mode state. Partial groups are never accepted.
`checkpoint_status` reports restoration or the explicit incompatibility reason;
`fallback` reconstructs from the beginning, while `reject` raises. Clock frontiers
are also reconstructed from bounded history scans, so a restored reducer cannot
retain a future clock. Reader iterators and indexed raw lookup avoid full-capture
loading; seek can require linear processing but not an unbounded event list.

## Derived observers and no future leakage

`ReplayObserver` defines `observer_id`, `version`, `reset()`,
`on_batch(batch, books, clock)`, `checkpoint() -> bytes`, and `restore(bytes)`.
The terminal registers its actual analytical/paper/history observers at this
boundary. With any observers attached, current seek resets **every** observer
and recomputes the requested prefix from the beginning; it does not restore
book checkpoints while retaining future derived history. Current persisted
recorder checkpoints contain projection state, not these observer histories.
The observer checkpoint/restore interface does not imply that runtime seek
currently restores saved observer checkpoints.

`DeliveryDigestObserver` is a concrete bounded audit consumer: it counts batches
and events and hashes exact delivered evidence in merge order. It has validated
checkpoint restoration and provides deterministic no-future-state regression
coverage; it is not presented as trading analytics.

Paper can freeze at its last valid accounting prefix (for example, a bounded
funding catch-up failure) while replay continues delivering market data to the
other observers. A later market cursor therefore does not certify later paper
accounting. Seek/reset reconstructs the prefix; it is not automatic continuation
of the frozen ledger. Opening a persisted paper audit is read-only. Explicit
resume first reconstructs and validates a matching demo/recording prefix before
replacing active accounting; live archives cannot resume into a new owner.
See [Paper freeze, archive and resume](TERMINAL.md#freeze-archive-replay-and-resume).

## Durable recorder leases

`await RecorderBinding.start(registry, recorder, keys, client_id=None)` from
`subscriptions` acquires independent recorder leases and registers canonical
stream descriptors before offering any data. Construct `Recorder` first using
`registry.session_id`, `registry.clock_id`, and `registry.recording_config`.
The binding retains the recorder after browser/client leases close.
`await binding.stop()` joins source ownership, detaches sinks, releases recorder
leases and finalizes the capture. The registry remains caller-owned.

Attaching to an already active owner explicitly joins and restarts that owner at
a fresh source epoch, rather than pretending the recording contains an earlier
bootstrap. Intentional attach, stop, final lease release and registry close use
the same graceful ownership boundary: `request_stop()` quiesces producer tasks
and new receive offers; the owner finishes its in-flight normalization, joins
transport producers and drains already-captured queued frames **inside** the
WebSocket context, before waiting for its close handshake. A slow peer close
cannot consume the required-input drain budget first. After transport cleanup,
the owner emits one final boundary and detaches required sinks; the completed
drain result is reused, not attempted again after close. Remaining browser leases
then restart their owner. Cached/presented old books are invalidated during these
boundaries. This intentionally trades a visible reconnect for truthful capture
scope, but ordinary stopping does not discard required input.

`SupervisorConfig.stop_drain_timeout` defaults to five seconds and is recorded
in registry capture configuration. It bounds the drain budget from the stop
request, including in-flight work. A decoder already running in a thread must
still be joined before another epoch can own the reducer; Python cannot safely
terminate that thread. Its completed normalization is retained even after the
deadline. If that was the last required input, capture remains complete and
`shutdown_slow` reports slowness separately. Only queued work actually abandoned
at the deadline produces known-loss Gap evidence and an incomplete capture.
Actual decoder, queue, transport and sink faults retain their normal invalidation
and loss behavior. Graceful shutdown never relabels a failed capture complete.

Client disconnect retires registry identity locks/generation state only when all
in-flight or waiting client operations, leases and queues are gone. Repeated
short-lived distinct clients do not consume lifetime capacity, and concurrent
operations never create competing locks for one client identity.

## Validation entry points

The integration owner runs `pytest tests/test_replay.py
 tests/test_replay_binding.py tests/test_replay_shutdown.py tests/test_workstation_ingestion.py` together with
recording/CLI regressions. `python -m src.workstation.cli demo`, `inspect`, and
`replay` exercise the actual headless interfaces; see `DEMO.md` for CLI options.
No execution result is claimed by this document.
