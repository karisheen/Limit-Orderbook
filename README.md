# Crypto Limit Order Book

A runnable **Streamlit** dashboard for public cryptocurrency order books, with a
ladder, depth chart, spread history, depth heatmap and quality-gated venue
comparison. The existing app is being upgraded into a local market-data research
workstation; the planned terminal is **not** the current Streamlit application.

## Upgrade progress

- **M0:** baseline audit, architecture decisions, contracts and acceptance backlog
  recorded. Historical audit observations describe the starting code, not all
  current behavior.
- **M1:** exact core, seven public venue adapters, supervised shared subscription
  ownership and legacy safety fixes implemented. Live feed integrations have
  measured evidence below; final review/merge is tracked in the handoff.
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
