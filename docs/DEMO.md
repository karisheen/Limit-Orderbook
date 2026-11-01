# Headless capture and offline demo

The M2 entry point is `python -m src.workstation.cli`. Use the project's locked
Python environment (including PyArrow and DuckDB). No command requires a browser.
`demo`, `inspect`, and `replay` do not perform network discovery or exchange I/O.
Only `record --network` opts into public network access; no credentials or private
exchange channels are used.

## Immediate offline start

```sh
python -m src.workstation.cli demo
python -m src.workstation.cli inspect data/workstation/synthetic-demo-v2-180s --limit 10
python -m src.workstation.cli replay data/workstation/synthetic-demo-v2-180s --mode raw
python -m src.workstation.cli replay data/workstation/synthetic-demo-v2-180s --mode normalized --speed 10
```

The demo creates a versioned capture from the small committed
`tests/fixtures/workstation/demo-v2.json` template and compact generator. It passes
source-schema bytes through production Binance-global, Hyperliquid, Coinbase and Kraken adapters and the shared
recording/replay projection, not a separate toy book implementation. The command
compares its final source-projection state and book hashes with actual replay.
The output includes exact-wire Books, quality, cursor and hashes for a future M3
terminal consumer. There is no frontend in M2.

The default generates **180 virtual seconds at 1 Hz**, without waiting 180 wall
seconds. `demo --duration 2` makes a small smoke capture; `--duration 1800` creates
longer synthetic research input (maximum 86,400 virtual seconds). All 24 book/trade
streams are interleaved under one receive clock, keeping their latest observations
close without claiming exchange-time simultaneity. Offline generation can await
writer capacity; live registry callbacks never block to write capture files.

The fixture is original synthetic material dedicated under **CC0-1.0**. All
prices, sizes, times, update IDs and prints are invented. Its metadata is an
illustrative fixed source-schema subset, not current discovery, current trading
constraints, permission to execute, or a historical market-data sample. Source
schema documentation links and fixture provenance are included in the template;
the template's SHA256 and provenance are retained in the capture manifest.

BTC, ETH and SOL each have separate Binance-global spot-USDT and Hyperliquid
perpetual streams, with independent book and trade subscriptions. Hyperliquid
BTC/ETH/SOL are **USDT-denominated**, settled in the separately identified
HyperCore USDC asset with `quanto_no_fx`. This is the contract classification, not
an FX conversion or permission to merge the spot and perpetual books. Native
symbols/IDs and immutable metadata versions remain attached to all events.

The comparable cohort is **Coinbase + Kraken USD spot** for BTC/ETH/SOL. Those
six books retain real native routing symbols and explicitly synthetic instrument
metadata. Coinbase uses one initial snapshot followed by sequenced level2 deltas;
Kraken uses one initial snapshot followed by deltas with CRC32 values calculated
by its existing production checksum implementation. Sizes change and buy/sell
prints alternate at 1 Hz. These are not periodic snapshots mislabeled as exact
event flow. USD spot comparisons must not merge USDT with USD or spot with
Hyperliquid perpetuals; no FX equivalence or cross-market executable quote is invented.

The Binance fixture has a deliberately tiny synthetic full book and real diff
bootstrap mechanics. It makes **no claim about historical or real 5,000-level
depth**. Hyperliquid snapshots retain their bounded-depth capability. Duplicate
trades, an explicit unknown network interval, WS-to-REST fallback, and WS recovery
exercise dedup and source quality boundaries. A recorded continuity gap does not
mean recorded bytes were lost: the demo's `capture_lost` is false, and unknown
exchange history is never fabricated. REST snapshots are current observations
within the synthetic story, not backfilled deltas.

Generated captures are ignored local data, not committed fixtures. Session names
include fixture version and virtual duration. Reuse requires matching fixture
hash, stream metadata, configuration, duration, license and exact workstation
source-tree SHA256; code revisions explicitly hash working-tree bytes rather than
pretending uncommitted code belongs to Git HEAD. Both live and demo manifests carry
this code provenance. Use `--root PATH` for an independent capture directory.
Corrupt, incomplete or incompatible existing captures fail rather than being
silently reused or overwritten.

### Python helper ownership for terminal consumers

`await create_demo(root, duration_seconds=180)` returns a `DemoCapture` owning
one fully verified `RecordingReader`. Fresh captures are verified after
finalization; existing captures use that same verified reader for strict cache
compatibility checks. The CLI and terminal consumers replay through
`capture.reader`, retaining its validated caches rather than reopening and
reverifying the capture. Verification is never disabled.

```python
from src.workstation.demo import create_demo
from src.workstation.replay import ReplaySession

async def load_demo(root):
    with await create_demo(root, duration_seconds=180) as capture:
        session = ReplaySession(capture.reader)
        try:
            await session.run_to_end()
            assert session.state_hash == capture.state_hash
            assert session.book_hash == capture.book_hash
            return session.books()
        finally:
            session.close()
```

The reader exposes `capture.reader.path` and `capture.reader.manifest`; the
capture separately retains independent source-projection `state_hash` and
`book_hash` evidence. Keep the capture open for the terminal's entire replay
lifetime, close its replay sessions first, then leave the context or call
`capture.close()` explicitly. Close is idempotent and releases the catalog,
decoded caches and shared retention lock. Failed generation, verification or
compatibility checks close any acquired reader before propagating the error.

## Real public capture

```sh
# Explicit opt-in, finite duration. Discovery/probing happens before this timer.
python -m src.workstation.cli record --network --duration 30 --assets BTC

# Hyperliquid public l2Book and trades, with discovered native metadata.
python -m src.workstation.cli record --network --venue hyperliquid --duration 30 --assets BTC ETH SOL

# Distinct sources, not venue fallback that relabels an existing stream.
python -m src.workstation.cli record --network --venue binance-global --venue hyperliquid --duration 60

# A roughly 30-minute capture; this command is not evidence it has been run.
python -m src.workstation.cli record --network --venue binance-global --venue hyperliquid --duration 1800
```

The default venue is Binance **global**, selected only after exact selected-product
metadata, REST snapshot/trade and synchronized WS book/trade dependency checks.
A partial dependency probe is not a pass. Binance.US requires explicit
`--venue binance-us`; it remains a different venue with different depth/quality.
Hyperliquid uses its documented public WS trade channel, not an invented REST
trade-history endpoint. Only requested venues/products are discovered; demo
prices are never overlaid into a live capture.

The recorder owns registry leases independently of browser clients. Raw/event
sinks are installed before feed acquisition, stream descriptors use the registry's
actual canonical lease keys, and shutdown joins feed owners before draining and
finalizing the recorder. The emitted JSON gives the capture path and durable
manifest. Capture loss, interruption, failed source setup, and missing usable
book/trade evidence are not reported as a successful complete capture. An empty
trade interval remains explicitly unproven rather than falsely certifying the
trade channel.

After attaching capture leases, `record` flushes a structured
`recording_started` event to stderr with the session, capture path, source-code
revision and requested duration. This marks attachment, **not synchronized-feed
readiness**; that distinction is explicit in the event. Final result JSON remains
on stdout. Supervisors and the writer continue independently of presentation.

## Inspection and replay controls

```sh
python -m src.workstation.cli inspect CAPTURE_PATH --limit 20
python -m src.workstation.cli replay CAPTURE_PATH --mode raw --speed 2
python -m src.workstation.cli replay CAPTURE_PATH --seek 20 --steps 3
python -m src.workstation.cli replay CAPTURE_PATH --interactive
```

`inspect` verifies storage before returning the manifest (including files, hashes,
counts, scope and provenance) and a bounded DuckDB query over Parquet. It does not
turn a missing venue checksum or unsupported exact-event-flow capability into a
passed integrity check. `--allow-incomplete` permits diagnosis of a valid but
explicitly incomplete capture; it does not waive corruption verification.

Interactive commands operate the real replay engine:

```text
step 3
status
play 2
pause
seek 10 0
run 20
status
quit
```

`play` runs in an asynchronous task so `pause` remains actionable. `step` counts
whole source transactions. `seek ORDINAL [EVENT_INDEX]` resolves to a recorded
transaction boundary, not a half-applied exchange message. `run [SPEED]` runs to
the end; omitted speed is unpaced. Replay preserves recorded source modes and
quality separately from display mode. Speeds change wall-clock pacing, never
economic state or receive identity. JSON decimals, timestamps, ordinals and
large IDs are strings.

Exit codes: **0** means the requested operation completed on a complete capture;
**2** means explicit incomplete/interrupted capture (or argument usage errors from
argparse); **1** means operational failure, corruption, unsupported operation or
missing dependency. A paused/stepped replay can return 0 without being at EOF:
its reported replay status/cursor distinguish playback position from capture
completeness. No command labels unsupported evidence as passed.

Regression coverage is in `tests/test_workstation_cli.py`; checks are run and
reported by the integration owner, not claimed by this document.
