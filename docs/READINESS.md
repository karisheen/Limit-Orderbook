# Local workstation readiness and recovery

This is a local public-market-data, replay and simulated-workflow application.
It is not an exchange execution system. Tests, a complete recording and responsive
rendering establish different properties; none establishes a profitable signal.

## Readiness dimensions

| Dimension | Established evidence | Boundary |
| --- | --- | --- |
| Native correctness | M5 final gate:1,166 tests; exact raw/normalized/checkpoint contracts, source/quote identity, causal analytics, alerts, paper ledger and journal | Fixtures do not establish current exchange availability or complete upstream data |
| UI and local operations |23 actual-backend browser scenarios; actual independent clients,12 repeated switches, stale-generation/input rejection, slow-socket eviction and process restart | Finite observed behavior, not a no-crash or unlimited-client guarantee |
| Sustained recording | Requested1,800 seconds,18 Binance-global/Hyperliquid channels; all846 files verified, all83,344 events replayed with raw/normalized/context parity and strict native checkpoint restore |24 hours has **not run**; previous failed soaks remain failed evidence |
| Research | Chronological purged/embargoed experiments, baselines, costs/latency stress and multiple-experiment accounting; retained900-second synthetic experiment reports `insufficient_evidence` | No predictive edge or profitability claim; unknown fees are not zero |
| Execution | Unsupported by design | No private APIs, exchange orders, wallets, signing, withdrawals or unattended trading |

Normal-load processing, rendering, full fresh-capture replay and operational
fault observations passed their declared checks. Finite replay/writer capacity
and the deliberately incomplete saturation result are documented separately below.
[The handoff](OMP_UPGRADE_HANDOFF.md) records exact commits, remote gates and merges.

## Start locally

Use Python3.13.5, uv0.8.4 and Node22.22.0/npm10.9.4:

```sh
uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --offline --no-sync python -m src.workstation.cli serve
```

Open `http://127.0.0.1:8000`. The default is the explicitly labelled original
synthetic demo, generated in memory, not a historical recording. Public data
requires `serve --network`. Replay requires `serve --capture PATH_TO_SESSION`.
Use one backend worker; share its registry rather than launching duplicate
collectors. The Streamlit observation client uses the same native API and now
also defaults its own server listener to127.0.0.1 through project configuration.
The Vite development server binds127.0.0.1. Explicit operator overrides are not
a security sandbox.

A fresh isolated locked Python install, native2-second durable demo/replay,
`npm ci` and production build succeeded without changing any lock. The first
strictly offline install could not find the pinned PyArrow wheel in the cache;
retrying dependency acquisition with package-network access succeeded. Setup
may need package downloads; the normal test gate remains exchange-network-free.
The pinned ESLint9.39.2 install emitted its unsupported-version warning; this
was retained rather than hidden by an unreviewed dependency refresh.

Evidence: [initial install](evidence/m6-locked-setup-20261101T182042Z.json),
[completed install](evidence/m6-locked-setup-complete.json),
[actual legacy bind regression](evidence/m6-legacy-loopback.json).

## Public venues and quality

Binance **global**, not Binance.US, is the preferred accessible primary spot
source on the measured workstation. The fresh sustained run included BTC/ETH/SOL
Binance-global books/trades and Hyperliquid perpetual books/trades/reference/context.
Earlier actual integration also exercised Coinbase and Kraken BTC/ETH/SOL;
Binance.US and KuCoin retain their separately documented partial capabilities.
Bybit metadata returned HTTP403 on this machine; downstream live validation is
not claimed. Availability is a dated observation, not an access guarantee.

Hyperliquid's bounded replacement books do not establish exact event OFI or
unsubscribed depth. BTC/ETH/SOL contract quote identity, separate USDC collateral
and `quanto_no_fx` are not an invented USDT/USDC conversion. Mark/oracle/funding/OI
are reference context, not executable bids/offers or realizable basis profit;
OI denomination remains adapter-declared rather than independently verified.

See [feed evidence](FEED_VALIDATION.md), [Hyperliquid](ADAPTER_HYPERLIQUID.md),
[USD adapters](ADAPTERS_USD.md), [other adapters](ADAPTERS_ASIAN.md),
[quality and exact contracts](CONTRACTS.md), [analytics](ANALYTICS.md) and
[costs](COSTS.md). Unknown fees, missing depth, gaps and stale state remain visible.
The historical workspace explicitly hatches unavailable/truncated history;
it does not interpolate missing evidence into observed liquidity.

## Exact native processing measurement

A separate requested60-second18-channel native CLI recording retained every
existing `ProcessingObservation` without changing native decoding, reduction,
queue admission or recording. All2,145 persisted raw receipts matched exactly
one observation by stream, ordinal, clock and receive time; file verification
passed. The first class-wide wrapper also observed six discovery-probe clocks:
329 probe observations and four cancelled probe operations are not observations
of the managed capture. No captured receipt or capture processing error was
discarded to correct that scope.

Normal load starts at the maximum first valid-book publication time across all
six required books and ends at the first explicit source-stop boundary. This is
a causal lifecycle boundary, not a percentile-selected trim. It began3.449303042
seconds after the first captured receipt and lasted55.700506613 seconds. Each
source remained in one epoch. The18 recorded `owner_stopped` gaps are terminal
boundaries with no capture loss or missing ordinal, each after its source's last
raw receipt; they are not unexpected mid-run disconnects.

| Book processing cohort | Observations | Receive-to-valid p50 ms | p95 ms | p99 ms | Maximum ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold bootstrap |6 |39.644087 |1574.440861 |1574.440861 |1574.440861 |
| Already-valid books while the cohort was still bootstrapping |48 |1065.639622 |1748.375558 |1950.415240 |1950.415240 |
| Synchronized normal-load window |1403 |4.994096 |12.724556 |52.890941 |196.755258 |

The unchanged normal-load p95<=25ms/p99<=100ms budgets passed this finite window.
Startup does not meet them: the unsegmented warm-book population had p95
40.821948ms/p991244.458043ms. That original failed comparison remains retained.
Validation ends at native `validation_end_mono_ns`; normalization, executor wait,
queue residence, publication and browser paint are separately reported. The
publication-start boundary can include callback/backlog work; it is not a
browser-readiness or future statistical-SLO claim.

Evidence: [all original observations](evidence/m6-live-latency-20261101T190710Z.json),
[first analysis with an over-strict zero-gap check](evidence/m6-live-latency-source-analysis.json),
and [verified source/lifecycle analysis](evidence/m6-live-latency-source-analysis-v2.json).
No live recollection was used to search for a passing result. Python remains
the implementation: measured normal budgets pass, and the profiled M5 cache/
service corrections precede any consideration of a narrow native component.

## Measured rendering

Current native source and actual built assets were pinned before/after each run.
Both viewports used the same complete90-second,12-stream BTC/ETH/SOL recording,
30-second cohorts, requested1x/2x replay, actual pan/zoom/Follow interactions and
an owned native backend plus Chromium. Other collectors/tests/browsers were
stopped. The tool checks real source-clock progress, not only the speed control.

| Viewport | Requested speed | Observed source seconds / wall second | Publish-to-ACK p95 ms | ACK samples |
| --- | ---: | ---: | ---: | ---: |
|1440x900 |1 |1.036858 |126.935740 |27 |
|1440x900 |2 |2.072725 |249.670420 |28 |
|1280x800 |1 |1.003131 |117.124927 |27 |
|1280x800 |2 |2.068446 |136.118471 |27 |

All four finite cohorts met the unchanged250ms p95 conservative round-trip
upper bound and actual-rate/sample-sufficiency checks. The1440x900 burst has
little p95 margin and a435.666095ms p99; no no-outlier or general performance
SLO is claimed. ACK is not synchronized one-way GPU completion. Browser paint
opportunities, JavaScript draw submission, processing and source age are
separately retained; they must not be added or relabelled as the same latency.

Evidence: [1440x900](evidence/m6-render-1440.json),
[1280x800](evidence/m6-render-1280.json), actual
[1440x900 burst surface](evidence/m6-render-1440-2x.png) and
[1280x800 burst surface](evidence/m6-render-1280-2x.png).
Reproduction and provenance contracts: [performance](CORE_PERFORMANCE.md).

## Finite replay and recorder capacity

The complete12-stream Binance-global/Hyperliquid BTC/ETH/SOL source was replayed
at1x and2x with every native transaction and final state preserved. Independent
production-writer loads at both rates preserved raw, normalized and regenerated
checkpoint bytes, with no rejection or accepted-but-unwritten records.
The highest tested complete paced input was89.998083 batches/s over47.512123
producer seconds; this is not an absolute sustainable maximum.

The unpaced attempt reached the actual4,096-item queue bound, rejected one offer,
drained accepted records and sealed **incomplete**. It is overload evidence,
not a throughput pass. Synthetic replay queues and writer dispatch tails reached
seconds; they are not substituted for live receive-to-valid measurements.
Historical timestamps and failed results remain unchanged.

[Full performance tables, provenance, reproduction and report-disclosure correction](CORE_PERFORMANCE.md#m6-complete-source-finite-replay)
include p50/p95/p99, event mix/depth, queue highwaters, CPU/RSS, exact source hashes
and explicit limits. Large raw sources remain local/git-ignored; the complete
benchmark input has a verified byte-exact retained copy outside temporary storage.

## Recording and the unrun24-hour command

The fresh30-minute recording exited0 and was reaped. Its manifest has81,095 raw
frames,81,131 normalized batches,83,344 events,1,782 checkpoints and846 files.
Recorder high-water was1,072 items/12,223,120 bytes within the production
4,096-item/128MiB bound; its final queue was empty. Native process high-water RSS
was220,700,672 bytes; user/system CPU563.456296/67.384502 seconds over1831.818404
wall seconds. These are this process's observations, not universal resource caps.
Other finite setup/operational work ran on the workstation during this capture;
it is not an isolated-host processing-latency benchmark.

The native source hash remained
`workstation-source-sha256:1a09935b23d1094ec90ce20d61ba5512a586785402189854d993dc6c78759e17`.
All18 final pre-shutdown source-status observations were in epoch1 with
`capture_complete=true`; some still had queued input at that observation.
Only the finalized recorder's empty queue is a shutdown result. Full file/replay
verification is not inferred from these status snapshots.

[Complete capture/process evidence](evidence/m6-sustained-capture-20261101T180952Z.json).
Earlier queue-overflow and incomplete soaks remain in the handoff/evidence;
they are not reclassified as passes after the later correction.

Full verification subsequently passed all12 checks without sampling:
81,131 deliveries and83,344 events through both raw and normalized native replay,
matching book, projection, context-inclusive and context hashes. All three spot
assets had eligible reference-basis observations during replay; these are not
executable arbitrage. Strict stored-checkpoint restore matched the native
projection. Context observers without persisted matching state recompute history.
[Complete replay evidence and embedded harness](evidence/m6-sustained-replay-integrity-complete.json).

This proof took821.123014 wall seconds,766.445242 CPU seconds and326,868,992 bytes
peak RSS. Raw replay took335.776005s; normalized replay203.852244s. The strict
final-delivery checkpoint seek took **65.135486s**: no instant/subsecond seek claim
for this30-minute source. Manifest SHA256:
`cf671ec3b6e629c0c213e210858bffd104a1dc2a6a0a84abcde02dc1965ef95a`.

This runnable command requests24 hours, **not executed during this upgrade**:

```sh
uv run --locked --offline --no-sync python -m src.workstation.cli record --network \
  --venue binance-global --venue hyperliquid --assets BTC ETH SOL \
  --duration 86400 --root data/24h
```

Keep adequate free disk space and retain stdout/stderr, the final status and
session directory. A requested duration is not evidence that it completed.
Inspect the resulting directory before replay:

```sh
uv run --locked --offline --no-sync python -m src.workstation.cli inspect data/24h/SESSION_ID
uv run --locked --offline --no-sync python -m src.workstation.cli replay data/24h/SESSION_ID --mode raw
```

Do not run duplicate live collectors to make a capture appear healthy. Recorded
replay needs no exchange access. Production byte/count admission limits are
finite headroom, not permission to ignore overload or drop required events.

## Health, failure and recovery

- `/health/live` establishes only process liveness. `/health/ready` returns503
  until a usable source and synchronized session exist. A healthy empty server
  can therefore be live but not ready. The actual cold-process-to-usable-HTTP
  sample was6.955602 seconds with the independent recorder running, not a UI
  startup guarantee.
- CLI recording emits structured `recording_started`/`recording_health` JSON,
  writer count/byte high-waters, persisted bytes and terminal failure. Native
  stream snapshots expose epoch, lifecycle, queue occupancy/high-waters,
  processed-frame count and same-clock receive/queue/decode/validation/normalize/
  publication timestamps. `tools/probe_feeds.py --live` additionally reports
  event rates, checksum/gap/reconnect and source observations. Frame telemetry
  separates client lag, coalescing and ACK retention from dropped market input.
- Treat a gap, checksum failure or resynchronizing source as ineligible; do not
  trade on the last visible book. The owner establishes a new epoch/segment and
  a valid bootstrap before eligibility returns. Retained stale/history data is
  not silently attached to the replacement source.
- A disconnected/slow browser is not a recorder owner. Native send deadlines and
  bounded client queues remove slow clients without blocking required feed or
  recorder callbacks. The actual constrained receive-window socket was evicted;
  another client's selection/cursor remained unchanged. Final session cleanup
  and process restart were observed, not inferred from an empty UI.
- Stale generation returns409; unknown fields422; oversized request413;
  rejected Origin403; wrong Host400; session-capacity exhaustion503. Do not
  blindly retry mutations. Reconcile accepted paper/journal state through its
  query API after an uncertain response. All orders here are simulations.
- On interrupted/disk-failed recording, retain the entire session and inspect
  its incompleteness reasons. Follow [managed recovery](RECORDING.md); do not
  delete locks or rewrite a manifest to claim completeness. Incomplete input is
  an explicit diagnostic option, never normal verified replay.
- Stop the backend before copying its complete local state directory. A restart
  creates new terminal sessions; persisted paper runs/journals remain audited
  state, not automatic live resumption. Source-bound replay adoption is explicit;
  live-clock identity cannot be forged into a resumable recording.
- Owned native `serve` processes interrupted with Ctrl-C were reaped with exit2
  (`interrupted`), including clean benchmark cleanup. Do not relabel that as an
  exit0, or confuse liveness/readiness with recorder finalization.

Actual operation evidence: [initial phase](evidence/m6-operations-20261101T182915Z.json)
and [successful remaining continuation](evidence/m6-operations-20261101T183107Z.json).
The initial harness expected429 instead of the correct capacity503 and stopped;
its successful earlier checks remain evidence, but that overall attempt is not
a pass. The continuation fixed only that expectation and exercised the remaining
slow-socket/cleanup/restart steps. API and persistence contracts: [API](API.md),
[terminal](TERMINAL.md), [recording](RECORDING.md), [replay](REPLAY.md).

## Deliberately slow and disconnected public feed

The independent fault probe established all12 requested BTC/ETH/SOL book/trade
streams on Binance global and Hyperliquid. It held one genuine Hyperliquid BTC
book websocket message for8 seconds, released it unchanged, then explicitly
closed only that owned socket. The native watchdog did **not** reconnect during
the hold; no such recovery is claimed. All11 unrelated streams progressed in
the sampled hold interval.

After explicit close, a processed valid Hyperliquid book was observed in epoch2
with a new segment, rather than inferred from a reconnect counter. Sampler,
recorder binding, registry and all13 opened sockets joined; final owner/task/
active-socket counts were zero, the recorder thread was stopped, and the child
was reaped normally with exit0. Source hashes were unchanged. The persisted
native capture is complete as received, not evidence of uninterrupted coverage,
venue-global completeness or retention of bytes before the native boundary.

[Actual fault, recovery and cleanup evidence](evidence/m6-venue-fault-20261101T191543904982Z.json)
includes the exact wrapper, native transitions, per-source progress and local
artifact hashes. The delay is an explicit local transport injection, not a
claim about exchange-side latency.

## Provenance and limits

No third-party chart/font/icon implementation or market-data fixture was copied
into the frontend. Its bundled React/ReactDOM/Scheduler MIT notice is retained
at `frontend/public/THIRD_PARTY_NOTICES.txt`; package dependencies retain their
own notices in the locked distributions. That notice does not license this
repository. The original synthetic fixture is CC0-1.0 with pinned source/metadata
provenance; public exchange data does not acquire that fixture license.
See [baseline/reference audit](BASELINE_AUDIT.md) and [demo provenance](DEMO.md).

Bounded tests and finite live observations replace the impossible claim that
the application cannot crash. The24-hour run, blocked Bybit live paths,
independently verified funding/OI semantics, profitable strategies and every
future live-execution capability remain unclaimed.
