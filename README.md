# Crypto Limit Order Book

A runnable **Streamlit** dashboard for public cryptocurrency order books, with a
ladder, depth chart, spread history, depth heatmap and quality-gated venue
comparison. The existing app is being upgraded into a local market-data research
workstation; the planned terminal is **not** the current Streamlit application.

## Upgrade progress

- **M0:** baseline audit, architecture decisions, contracts and acceptance backlog
  recorded. Historical audit observations describe the starting code, not all
  current behavior.
- **M1:** exact core implemented, with legacy feed-integrity and session-safety
  safeguards. **Full feed integration and M1 acceptance remain pending**; core
  implementation is not evidence that every venue is integrated or validated live.
- **M2–M6:** **not done**. Durable recording/replay, shared analytics and cost
  models, the new React/TypeScript terminal, alerts/paper trading/research, and
  final operational/performance acceptance remain milestone work.

This is an interim progress summary; later milestone documentation will replace
it as implementation and acceptance evidence land. See the
[full acceptance backlog](docs/UPGRADE_BACKLOG.md),
[delivery/evidence handoff](docs/OMP_UPGRADE_HANDOFF.md),
[architecture decisions](docs/ARCHITECTURE.md) and
[exact contracts](docs/CONTRACTS.md). Neither fixture tests nor successful access
probes establish complete live-feed correctness or trading readiness.

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
- `src/workstation/`: exact M1 core and new adapter work; not a claim of full
  end-to-end feed integration into the current UI.
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
