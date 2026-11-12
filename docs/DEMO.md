# Headless capture and offline demo

The entry point is `python -m src.workstation.cli`. Use the project's locked
Python environment (including PyArrow and DuckDB). Headless commands require no browser.
`demo`, `inspect`, `replay`, and `research` perform no network discovery or exchange I/O.
`record --network` and `serve --network` explicitly opt into public network access;
no credentials or private exchange channels are used.

## Immediate offline start

```sh
python -m src.workstation.cli demo
python -m src.workstation.cli inspect data/workstation/synthetic-demo-v3-180s --limit 10
python -m src.workstation.cli replay data/workstation/synthetic-demo-v3-180s --mode raw
python -m src.workstation.cli replay data/workstation/synthetic-demo-v3-180s --mode normalized --speed 10
```

The demo creates a versioned capture from the small committed
`tests/fixtures/workstation/demo-v3.json` template and compact generator. It passes
source-schema bytes through production Binance-global, Hyperliquid, Coinbase and Kraken adapters and the shared
recording/replay projection, not a separate toy book implementation. The command
compares its final source-projection state and book hashes with actual replay.
The output includes exact-wire Books, quality, cursor and hashes. The React
terminal starts directly from the same bounded in-memory generator with `serve`;
it does not wait for a durable demo capture to be written.

The default generates **180 virtual seconds at 1 Hz**, without waiting 180 wall
seconds. `demo --duration 2` makes a small smoke capture; `--duration 1800` creates
longer synthetic research input (maximum 86,400 virtual seconds). Native book,
trade and supported Hyperliquid reference/context streams are interleaved under
one receive clock, keeping their latest observations
close without claiming exchange-time simultaneity. Offline generation can await
writer capacity; live registry callbacks never block to write capture files.

The current v3 source contains **14 instruments and 38 channel streams**:
three Binance spot instruments, four Hyperliquid perpetuals plus HYPE spot,
and six Coinbase/Kraken USD spot instruments. Each has book/trades; the five
Hyperliquid instruments also have reference/context channels. Counts describe
the generator contract, not live venue coverage or historical benchmark input.
The explicit `positioning_scenario=ramp-signed-v1` configuration makes synthetic
perpetual open interest ramp up/down and funding step through negative, zero
and positive fractions every 30 virtual seconds. This is illustrative context,
not observed funding payments or the Paper funding model.

The fixture is original synthetic material dedicated under **CC0-1.0**. All
prices, sizes, times, update IDs, prints, funding and open interest are invented.
Hyperliquid metadata is pinned from public Info: the perpetual universe prefix
preserves native indices, and the HYPE/USDC spot subset preserves `@107`, asset
`10107`, token `150` and the actual native token identity. Other venue metadata
remains illustrative. Neither is current discovery, execution permission or
historical market activity. Source documentation, metadata snapshot time and
provenance are included; the complete fixture SHA256 is retained in manifests.

BTC, ETH and SOL each have separate Binance-global spot-USDT and Hyperliquid
perpetual streams, with independent book and trade subscriptions. Hyperliquid
BTC/ETH/SOL are **USDT-denominated**, settled in the separately identified
HyperCore USDC asset with `quanto_no_fx`. This is the contract classification, not
an FX conversion or permission to merge the spot and perpetual books. Native
symbols/IDs and immutable metadata versions remain attached to all events.

HYPE is included only on Hyperliquid: a perpetual and the discovered native
HYPE/USDC spot identity, not invented listings on other venues. Hyperliquid
streams additionally carry `allMids` and asset context; spot context uses the
observed `activeSpotAssetCtx` data channel. References are not executable quotes.
Native token USDC, USD and USDT are not interchangeable, and the fixture supplies
no invented FX rate.

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
pretending uncommitted code belongs to Git HEAD. Headless `record` and `demo`
manifests carry this code provenance. Use `--root PATH` for an independent
capture directory.
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

## Offline causal research

```sh
python -m src.workstation.cli research run --demo --duration 900 --state-dir data/workstation-state
python -m src.workstation.cli research list --state-dir data/workstation-state
python -m src.workstation.cli research show ATTEMPT_ID --state-dir data/workstation-state --limit 50 --offset 0
python -m src.workstation.cli research run --capture /absolute/path/to/capture --config research.json --state-dir data/workstation-state
```

The terminal's Research pane reads this same `research.sqlite3`; HTTP does not
start experiments. `run` requires either `--demo` or `--capture`. `--duration`
applies only to the synthetic source, defaulting to 900 virtual seconds. Memory
replay retains at most 64 MiB raw payload and 100,000 deliveries; use a durable
capture for longer input. Generation does not wait for virtual wall time.

Configuration is optional exact JSON, limited to 64 KiB, with native
`ResearchConfig` fields. Integer clocks accept exact integer strings; monetary
decimals remain exact. Unknown fields and invalid bounds fail. Fees default to
**unknown**, never zero. A native fee entry has this shape:

```json
{
  "fees": [
    ["coinbase:advanced-trade:spot:BTC-USD", {
      "rate": "0.0005",
      "asset_id": "USD",
      "provenance": "explicit operator assumption, not an observed account tier",
      "asof_wall_ns": "1793491200000000000",
      "assumed": true
    }]
  ]
}
```

`rate` is a fraction, so `0.0005` means 5 bps. Supply an actual sourced schedule
or clearly identified assumption, correct native instrument/fee asset and date.
Optional `stream_ids`, `thresholds`, `horizons_ns`, `scenarios`, split fractions,
embargo, sampling interval/window and observation bound use the native schema.
Scenarios declare `name`, `latency_ns`, `extra_fee_bps` and `extra_slippage_bps`.
Do not interpret an unfunded directional markout as a realizable portfolio return.

All begun attempts, including failures and negative/insufficient results, remain
in SQLite. `list` returns the latest bounded page (no older-history offset);
known older IDs remain addressable by `show`. Trial pages expose all candidate
statistics, exclusions and matched baselines; the stable report hash covers the
complete report, not just the displayed page. A previously exercised 900-second
synthetic run had 12,600 observations, 151,200 labels and 2,592 trials:
`insufficient_evidence`, with unknown fees explicitly excluding net results.
These are historical pipeline measurements, not current-head validation, a
guaranteed count for a new run, or evidence of a trading edge.


## Real public capture

```sh
# Explicit opt-in, finite duration. Discovery/probing happens before this timer.
python -m src.workstation.cli record --network --duration 30 --assets BTC

# Hyperliquid public books/trades/reference/context with discovered native metadata.
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

The headless `record --venue` choices are currently only `binance-global`,
`binance-us` and `hyperliquid`; the terminal's broader live catalog does not
expand this CLI option. Adapter support does not establish access from this
environment. A denied dependency check remains a failure, not permission to
substitute venues or bypass regional/provider restrictions.

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

CLI `--seek`/interactive `seek` use the receive **ordinal** and optional event
index. The terminal's **Delivery → Seek** instead uses the delivery index;
do not copy one coordinate into the other. `--seek` without `--steps` or
`--interactive` then runs to the end. Use `--steps N` with a positive count
to inspect a bounded post-seek prefix.

Exit codes: **0** means the requested operation completed on a complete capture;
**2** means explicit incomplete/interrupted capture (or argument usage errors from
argparse); **1** means operational failure, corruption, unsupported operation or
missing dependency. A paused/stepped replay can return 0 without being at EOF:
its reported replay status/cursor distinguish playback position from capture
completeness. No command labels unsupported evidence as passed.

Regression coverage is in `tests/test_workstation_cli.py`; checks are run and
reported by the integration owner, not claimed by this document.
