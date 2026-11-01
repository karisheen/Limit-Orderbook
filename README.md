# Crypto Limit Order Book

A local public-market research workstation with an exact Python core and a
React/TypeScript terminal: instrument-specific book/tape, quality-gated analytics,
size-aware costs, independent sessions and deterministic replay. The older
Streamlit dashboard remains runnable until the terminal's M4 visualization cutover.

## Upgrade progress

- **M0:** baseline audit, architecture decisions, contracts and acceptance backlog
  recorded. Historical audit observations describe the starting code, not all
  current behavior.
- **M1:** exact core, seven public venue adapters, supervised shared subscription
  ownership and legacy safety fixes implemented and merged. Live feed integrations
  have measured evidence below.
- **M2:** durable raw/normalized capture, verified manifests/checkpoints, deterministic
  replay and an explicitly synthetic offline demo implemented. Integration and
  Git delivery evidence are tracked in the handoff.
- **M3:** analytics, exact costs and the end-to-end terminal are implemented and
  verified locally. Recorder load/soak integration and milestone merge remain open.
- **M4–M6:** **not done**. Historical liquidity visualization, full workspace
  interactions, alerts/paper/journal/research and final operational acceptance remain.

This is an interim progress summary; later milestone documentation will replace
it as implementation and acceptance evidence land. See the
[full acceptance backlog](docs/UPGRADE_BACKLOG.md),
[delivery/evidence handoff](docs/OMP_UPGRADE_HANDOFF.md),
[architecture decisions](docs/ARCHITECTURE.md) and
[exact contracts](docs/CONTRACTS.md). Neither fixture tests nor successful access
probes establish complete live-feed correctness or trading readiness.

## Run the terminal

Use Python 3.13.5, uv 0.8.4 and Node 22.22.0/npm 10.9.4:

```sh
uv sync --locked --python 3.13.5 --group dev
npm --prefix frontend ci
npm --prefix frontend run build
uv run --locked --offline --no-sync python -m src.workstation.cli serve
```

Open `http://127.0.0.1:8000`. This default is an explicitly **synthetic offline
demo**, not a live feed or a recording. The full 180-second/24-stream scenario
reached usable HTTP data in **2.642 s** in one cold-process sample; no durable
capture is generated on startup. Health distinguishes process liveness from
synchronized session data.

Use `serve --capture data/workstation/SESSION_ID` for verified recorded replay,
or `serve --network` to opt into public live discovery/collection. Do not run a
second live collector beside a recorder. Unknown fees, missing depth, stale
inputs and incompatible quotes remain explicit exclusions. No order is sent
to an exchange. See [terminal controls](docs/TERMINAL.md),
[API](docs/API.md), [analytics](docs/ANALYTICS.md) and [costs](docs/COSTS.md).

## New headless feeds

The new backend is independent of Streamlit rendering. Run an explicit public
network probe (not a recorder and not an execution client):

```sh
uv run --locked python tools/probe_feeds.py --live --duration 60 --include-hype
```

Observed integration evidence, not blanket regional-access guarantees:

| Venue | Actual validation | Limitation |
| --- | --- | --- |
| Binance global | BTC/ETH/SOL metadata, REST depth/trades, synchronized diff books and live trades on official data-only hosts | Preferred over US only when required dependencies work |
| Coinbase | BTC/ETH/SOL Advanced Trade REST snapshots, full WS books and live trades | Public Advanced Trade schema; not the legacy Exchange REST API |
| Kraken | BTC/ETH/SOL REST snapshots, CRC-validated WS books and live trades | REST has no WS continuity/checksum claim |
| Hyperliquid | BTC/ETH/SOL/HYPE perpetual REST snapshots, bounded WS replacement books and live trades | No exact event OFI or invented depth beyond the subscription |
| Binance.US | BTC/ETH/SOL REST and partial WS books; ETH/SOL live prints | BTC produced no live print in the short observation window; distinct venue |
| KuCoin | BTC/ETH/SOL REST books and trade history | Degraded snapshots; unknown aggressor side, not continuous WS |
| Bybit | Offline protocol/reset fixtures | Metadata HTTP 403 blocked this machine; remaining live checks unrun |

The initial failed run and corrected run are retained in
[`docs/evidence/`](docs/evidence/); see [feed validation](docs/FEED_VALIDATION.md)
for exact commands and [contracts](docs/CONTRACTS.md) for loss, freshness,
ownership and source-transition semantics. No fixture data fills gaps in live
history. Streamlit still uses its temporary legacy service until terminal parity.

## Record, replay and offline demo

After the locked Python setup below:

```sh
# Offline: original CC0 synthetic BTC/ETH/SOL scenarios, 180 virtual seconds.
uv run --locked python -m src.workstation.cli demo

# Actual public data; both venues retain distinct market/quote identities.
uv run --locked python -m src.workstation.cli record --network \
  --venue binance-global --venue hyperliquid --assets BTC ETH SOL --duration 60

# Replace SESSION_ID with the capture directory reported by either command.
uv run --locked python -m src.workstation.cli inspect data/workstation/SESSION_ID
uv run --locked python -m src.workstation.cli replay data/workstation/SESSION_ID --mode raw
uv run --locked python -m src.workstation.cli replay data/workstation/SESSION_ID --interactive
```

Interactive replay supports `play SPEED`, `pause`, `step N`, `seek ORDINAL`,
`run` and `quit`. Raw and normalized paths share reconstruction and quality rules.
Incomplete/corrupt captures are not silently accepted. The recorder owns its
subscription leases independently of browser clients; capture starts before
synchronization, and CLI readiness labels that distinction.

The complete **durable** synthetic build took **16.307 s** on the measured
workstation; the terminal's independent memory-backed startup is described above.
See [recording](docs/RECORDING.md), [replay](docs/REPLAY.md), [demo/CLI](docs/DEMO.md)
and the handoff for exact evidence, budgets, recovery and source limitations.

## Run the current Streamlit app

Use **Python 3.13** (reproducible interpreter: **3.13.5**) and **uv 0.8.4**.
Python 3.14+ is not currently supported. From the repository root:

```sh
uv python install 3.13.5
uv sync --locked --python 3.13.5 --group dev
uv run --locked --offline --no-sync python -m streamlit run crypto_lob.py --server.address 127.0.0.1
```

Dependency setup may download packages. The launch command starts a **live-data
UI**, not an offline demo: it can contact public exchange endpoints even though
uv uses `--offline` for dependency management. No exchange credentials or wallet
are required. See [native development](docs/DEVELOPMENT.md) for environment
selection, lock policy and checks.

In the sidebar, choose an asset, venues, a primary venue, display depth and
panels. The UI refresh setting controls rendering, not background ingestion.
Each Streamlit session owns independent legacy subscriptions and histories;
this temporary isolation is **not** the planned shared subscription registry.

## Current legacy venue support

These are the adapters used by `crypto_lob.py`, not the full new-workstation
integration matrix. Listed BTC pairs illustrate the native quote identities;
availability varies by product, region and venue.

| Venue | Example pair | Public live source | REST source / limits |
| --- | --- | --- | --- |
| Binance.US | `BTCUSDT` | Partial depth20 WebSocket snapshots, not a diff stream | Binance.US depth snapshots; separate from Binance global |
| Kraken | `BTC/USD` | WebSocket v2 book, depth 25, exact-decimal top-10 CRC32 validation | Kraken depth snapshots |
| Coinbase | `BTC-USD` | Advanced Trade public WebSocket `level2` / `l2_data` plus heartbeats; no JWT required | **Coinbase Exchange** REST level 2 fallback, explicitly a different API family |
| KuCoin | `BTC-USDT` | REST polling only in the legacy adapter | Public level2_100 snapshots; no claim of event continuity |
| Bybit | `BTCUSDT` | v5 spot `orderbook.50`, snapshot/delta with reset and ordering checks | Bybit depth snapshots; access restrictions remain visible |

REST modes are labeled **degraded/snapshot**, and Binance.US partial books are
independent snapshots. **Snapshot modes are not continuous event streams and
cannot establish continuous OFI.** A successful transport connection or heartbeat
alone does not establish a valid book.

## Legacy safeguards and limitations

- Prices and quantities are validated as `Decimal`; malformed, nonfinite,
  negative, duplicate or crossed snapshot data is rejected rather than silently
  repaired. Exact JSON decimal decoding preserves Kraken checksum precision.
- Kraken applies an entire book message, retains the subscribed depth and checks
  CRC32. Bybit validates identity, reset and monotonic ordering semantics without
  inventing contiguous update IDs. Coinbase validates its Advanced Trade stream
  separately from its Exchange REST fallback.
- Feed workers use bounded transport attempts, deadlines and reconnect backoff.
  REST fallback periodically retries WebSocket recovery; only valid book data
  establishes recovery. Source transitions segment legacy history.
- Comparison uses **best observed comparable bid/ask**, not a market-wide NBBO.
  Conservative quality, freshness, quote identity and observation-skew exclusions
  are shown. USD and USDT are never silently combined; degraded REST and stale
  last-known books are diagnostic displays, not comparison inputs.
- Session-owned workers are stopped/joined and obsolete generations cannot
  publish into replacement subscriptions. This does not provide the future
  backend registry's cross-client leases or headless collection lifecycle.
- The legacy heatmap and float-based presentation are not the planned stable
  historical terminal or an exact analytics engine. No crash-free, complete
  liquidity, live venue validation or performance guarantee is made here.

[Legacy behavior and operational limits](docs/LEGACY.md) describes these
safeguards in detail. The source docstrings and regression fixtures define the
implemented behavior; the acceptance backlog defines remaining work.

## Repository layout

- `crypto_lob.py`: current Streamlit entrypoint.
- `src/data/`, `src/domain/`, `src/ui/`: legacy adapters, service and presentation.
- `src/workstation/`: exact core, public adapters and supervised shared feed registry;
  not yet connected to the legacy Streamlit UI.
- `tests/`: offline contracts/regressions and explicitly separate live checks.
- `docs/`: development instructions, contracts, acceptance and evidence.

## Checks and live access

After synchronizing the environment, use the documented offline gate:

```sh
./tools/check.sh
```

The gate and its environment-selection rules are explained in
[DEVELOPMENT.md](docs/DEVELOPMENT.md#offline-checks-and-quality-scope).
`tests/smoke_live.py` is a separate manual REST probe that contacts real services;
it is not synchronized WebSocket validation. Actual executed checks, access
observations and remaining blockers belong in the
[handoff](docs/OMP_UPGRADE_HANDOFF.md), not inferred pass counts in this README.

Public endpoints may rate-limit, block regional access, suspend products or
change their protocols. This is an educational/research project, not trading
advice. No live order execution, private credentials, signing or withdrawals are
implemented or authorized by this upgrade.
