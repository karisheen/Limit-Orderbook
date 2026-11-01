# Current Streamlit compatibility path

`crypto_lob.py` remains the runnable live-data UI during the workstation upgrade.
It uses `src/data`, `src/domain` and `src/ui`, with exact parsing shared from
`src/workstation/precision.py`. It is **not** the planned React/TypeScript terminal
or a completed integration of the workstation subscription/event pipeline.

The M1 exact core and the safeguards below are implemented; **full feed
integration and M1 acceptance remain pending**. **M2–M6 are not done**. This
interim status will be replaced by later milestone documentation. The
[full acceptance ledger](UPGRADE_BACKLOG.md), [handoff](OMP_UPGRADE_HANDOFF.md),
[contracts](CONTRACTS.md) and [architecture decisions](ARCHITECTURE.md) preserve
the remaining scope. Historical baseline audit findings are not current behavior
claims. Source docstrings and regression fixtures are authoritative for the
implementation described here; this document reports no test pass counts,
performance measurements or new live-venue validation.

## Startup and ownership

Use Python **3.13** (pinned development interpreter **3.13.5**) and uv **0.8.4**:

```sh
uv python install 3.13.5
uv sync --locked --python 3.13.5 --group dev
uv run --locked --offline --no-sync python -m streamlit run crypto_lob.py --server.address 127.0.0.1
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for environment-selection and lock details.
`--offline` applies to uv dependency management, **not market-data access**: the
app contacts public venues. No wallet, JWT or private exchange credentials are
required for this path. There is no implemented offline demo/replay launch implied
by this command.

The service is held in `st.session_state["legacy_orderbook_service"]`. Separate
browser sessions have independent subscriptions, workers, snapshots and history;
reruns within one session reuse its service. This fixes shared mutable browser
selection, but can duplicate upstream subscriptions across sessions. It is a
**temporary isolation strategy, not a shared reference-counted backend registry**.
It does not establish headless recorder ownership or automatic cleanup on every
browser disconnect.

Subscription replacement fences the old generation, clears its state, stops all
old workers and joins them before starting replacements. Stale callbacks and
wrong-instrument/venue publications are rejected. Selecting no exchanges stops
the service; restarting the same subscription is supported after stopping.

## Validation and precision

Prices and quantities are `Decimal` parsed from original text, integers or
existing Decimal values, never binary floats or booleans. Shared parsing rejects
nonfinite values and bounds text length, coefficient precision, exponent and
magnitude. JSON decoding preserves decimal scale, rejects duplicate keys and
nonfinite constants, and has an input-size limit. Incoming values are not rounded
to make invalid data fit.

Snapshots validate all rows before display truncation: malformed rows,
nonpositive prices/quantities, duplicate prices, empty sides and locked/crossed
books fail. Extra REST columns are ignored. Published snapshots contain immutable,
sorted Decimal levels. Delta quantities are absolute replacements; zero deletes,
and repeated price updates apply in wire order. `LiveBook` applies candidate
changes transactionally; adapters invalidate reconstruction on bad messages and
require a new snapshot rather than treating a later delta as initialization.

These legacy checks do **not** establish full venue product metadata, tick/lot
constraints or the workstation's complete instrument contracts. Snapshot times
are local receive wall times, not verified venue event times. Float conversion is
reserved for presentation boundaries; source book values remain Decimal.

## Venue semantics

### Kraken: depth 25 with top-10 CRC32

The public v2 WebSocket subscribes to `book` at **25 levels per side**. All
same-symbol payloads in a message are applied before final validation, then stored
state is truncated to the subscribed depth. This avoids retaining displaced
out-of-scope levels as ghost liquidity; the ladder slider does not set
reconstruction depth.

CRC32 uses exact fixed-point price/quantity text from the top 10 asks ascending,
then the top 10 bids descending, preserving scale before stripping decimal points
and leading zeros. A required checksum is checked against the completed book.
Missing/mismatching checksum or invalid reconstruction resets the book and
requires a fresh snapshot. Mixed-symbol payloads fail; unrelated channels/symbols
are ignored. This is **25-level reconstruction with top-10 checksum coverage**,
not full-depth verification. REST fallback uses Kraken depth snapshots, not a
checksum-verified continuous stream.

### Coinbase: public Advanced Trade WS; separate Exchange REST fallback

The WebSocket endpoint is `wss://advanced-trade-ws.coinbase.com`, with public
`level2` and `heartbeats` subscriptions and `l2_data` book events. No JWT is
required. `new_quantity` is absolute; zero deletes. Matching-product events in a
message are applied transactionally and validated as a completed book. Full
reconstruction is retained while publication is capped at 200 levels per side,
so display truncation does not discard levels needed after later deletions.

The legacy adapter tracks `sequence_num`, including heartbeat progression;
sequence gaps/duplicates or invalid messages reset reconstruction. This describes
the implemented checks, **not a claim of newly verified live sequence guarantees**.
Heartbeats do not publish a book or establish healthy book data.

Fallback deliberately uses **Coinbase Exchange REST**:
`https://api.exchange.coinbase.com/products/{symbol}/book?level=2`.
That is a different API family and parser from Advanced Trade. It is labeled
REST snapshot/degraded mode, with a nominal three-second polling interval plus
request/retry time; it does not restore event continuity. Exchange level 2 is
not made a bounded server response by the legacy adapter's display limit.

### Binance.US: independent partial snapshots

`depth20@1000ms` supplies independent top-20 snapshots at the configured
one-second cadence. Every accepted frame replaces the prior book; no stale
levels accumulate. `lastUpdateId` may be retained as snapshot metadata, but the
adapter does not infer a contiguous diff stream. Binance.US REST depth is also
snapshot mode. Binance.US is not Binance global and must not be relabeled or
used as evidence that the planned global snapshot/diff bridge is integrated.

### Bybit: ordered, not contiguous

The v5 public spot adapter requires the exact `orderbook.50.{symbol}` topic and
matching embedded symbol, retaining 50 levels per side. Normal deltas require
strictly increasing positive `u` values; **IDs need not be contiguous**. Optional
`seq` must increase when available on both observations. A fresh snapshot or
`u=1` replaces depth and resets IDs. Delta-before-snapshot, duplicate/backward
IDs or malformed data invalidates reconstruction. Regional restrictions may
prevent access; no present regional availability is certified here.

### KuCoin: REST only

The legacy adapter polls public `level2_100`, validates the API success code and
optional sequence, and rejects malformed sequence values instead of inventing
zero. It does not implement WebSocket token bootstrap. Its nominal polling
interval is two seconds plus request/retry time. Successful REST data remains
**degraded/snapshot**, not a continuous feed.

## Bounded retries, recovery and diagnostic display

The defaults in [clients.py](../src/data/clients.py) and
[orderbook_service.py](../src/domain/orderbook_service.py) are:

- HTTP: three total attempts, ten-second request timeout, exponential backoff
  starting at 0.5 seconds and capped at eight seconds, plus up to 25% jitter.
  Request failures, 429, 5xx and malformed JSON retry; other 4xx fail fast.
  `Retry-After` seconds/HTTP dates establish venue cooldown, including after a
  final failed attempt. Retry/poll waits are interruptible.
- WebSocket: ten-second connection timeout, one-second receive timeout,
  fifteen-second validated-book deadline and thirty-second transport-silence
  deadline. Ping/pong handling is separate from book validity. Acks/heartbeats
  do not renew the validated-book deadline. Parser failure closes the connection;
  reconnect resets adapter state.
- Three unsuccessful/short WebSocket sessions trigger REST fallback. A session
  resets the failure counter only after valid book data and at least fifteen
  seconds of operation. Reconnect backoff is capped at ten seconds before jitter.
- REST failure backoff is capped at thirty seconds before any larger venue
  cooldown. WebSocket-capable workers periodically retry WebSocket recovery with
  a **sixty-second target**, fresh reconstruction and valid-data acceptance.
  This is not permanent fallback and **not a hard recovery SLA**: in-flight
  requests, polling waits, retries and `Retry-After` may delay the probe. Request
  timeouts are not an end-to-end recovery guarantee.

All accepted REST books are explicitly degraded with no event continuity.
Errors retain a last-known book for diagnostic display when one exists; without
a book the feed is disconnected. More than fifteen seconds since a monotonic
accepted observation makes data stale/degraded. Future or old receive timestamps
cannot establish health. The UI warns when a primary book is last-known/REST
rather than comparison-eligible.

Spread history is bounded to 1,200 points; depth history to 300 snapshots sampled
at a two-second interval. Errors clear histories, and source-mode changes or
stale observation gaps reset history before subsequent samples. This bounds
those history buffers, **not total application memory**: Coinbase intentionally
retains its full reconstruction. The legacy heatmap is not the future stable
historical time/price grid or a durable recording.

**REST polling and partial snapshot streams are not continuous OFI inputs.**
Bounded spread/depth samples do not recover missing events, and the UI's
“Bid share (top 10)” is static depth imbalance, not order-flow imbalance. This
legacy path does not implement the planned continuous-event analytics pipeline.
There is no no-crash or universal availability guarantee.

## Conservative comparison exclusions

Comparison reports **best observed comparable bid/ask**, not NBBO or guaranteed
executable prices. Known native-symbol mappings define separate base/quote
cohorts: Coinbase/Kraken USD and Binance.US/KuCoin/Bybit USDT. There is no implicit
USD/USDT/USDC parity, conversion or combined mixed-quote result.

Eligibility requires matching venue identity and known product mapping,
`CONNECTED` status, and finite, nonfuture status/snapshot receive times within
the default fifteen-second age bound. Within each cohort, observations more than
the default two seconds behind its newest snapshot are excluded. Bounds are
configurable. Unknown identity, absent/unhealthy status, stale/future data and
excessive skew produce visible exclusion reasons. Service-produced REST books
are degraded and excluded even when useful for an individual diagnostic ladder.

Skew is based on **local receive timestamps**, not synchronized exchange event
times. Crossed observations are labeled; these observations do not establish a
simultaneously executable arbitrage, fee-adjusted edge or size-aware fill.

## Source and acceptance references

Implemented behavior is described in:

- [Adapter base](../src/data/adapters/base.py),
  [Kraken](../src/data/adapters/kraken.py),
  [Coinbase](../src/data/adapters/coinbase.py),
  [Binance.US](../src/data/adapters/binance.py),
  [Bybit](../src/data/adapters/bybit.py) and
  [KuCoin](../src/data/adapters/kucoin.py).
- [Snapshot validation](../src/domain/models.py),
  [session/recovery/comparison service](../src/domain/orderbook_service.py),
  [UI components](../src/ui/components.py) and
  [Streamlit ownership](../crypto_lob.py).
- [Adapter fixtures](../tests/test_adapters.py),
  [model fixtures](../tests/test_models.py),
  [resilience fixtures](../tests/test_resilience.py) and
  [upgrade regressions](../tests/test_upgrade_regressions.py).

Fixtures document expected behavior; their presence is not an execution result.
The manual REST smoke script is not WebSocket synchronization validation. Read
[DEVELOPMENT.md](DEVELOPMENT.md) for check commands and the
[handoff](OMP_UPGRADE_HANDOFF.md) for actually exercised evidence. All remaining
milestone obligations, including full feed integration, shared subscriptions,
recording/replay, analytics, terminal parity and operational acceptance, remain
in the [complete backlog](UPGRADE_BACKLOG.md).
