# Binance, Bybit and KuCoin public adapters

These are the shared workstation adapters in `src/workstation/adapters`; both observation clients consume their output through the native API. The separate legacy UI adapters are retired. They perform public market-data operations only. No credentials, signing, testnet, order submission or execution eligibility is inferred.

## Implemented modes

| Adapter / stable venue | API family | Books | Public trades | Continuity limits |
| --- | --- | --- | --- | --- |
| `BinanceGlobalAdapter` / `binance-global` | `spot-v3` | Official data-only REST snapshot, 5,000 levels per side, buffered `depth@100ms` updates | `trade` stream and recent REST trades | Update-ID bridge/gap validation; no checksum; batched updates do not establish every matching-engine event |
| `BinanceUSAdapter` / `binance-us` | `spot-v3` | Independent partial snapshots at 5/10/20 levels, 100 ms; REST fallback | `trade` stream and recent REST trades | No diff continuity claim; never fed through global bootstrap |
| `BybitAdapter` / `bybit` | `v5-spot` | `orderbook.{depth}.{symbol}`, depths 1/50/200/1000; independent REST fallback at the requested depth | `publicTrade.{symbol}` and recent REST trades | Monotonic update/cross-sequence checks, not fictional contiguous IDs; depth 1 is snapshots only |
| `KuCoinAdapter` / `kucoin` | `classic-spot` | Public REST `level2_20` / `level2_100` snapshots | Public REST `histories` observations | No implemented WebSocket, no continuous history, no checksum or sequence continuity claim |

Synchronized Binance global diffs support **feed-message OFI over 100 ms aggregated updates** (`exact_event_flow=True` means continuity of received validated deltas, not every matching-engine order). Gap, initialization and source-mode gates remain mandatory. Bybit's monotonic counters do not establish gap-free continuity, so its exact-flow capability remains false. KuCoin and US snapshot differences are not event OFI. KuCoin snapshot reducers report `rest_snapshot`/`degraded`; snapshot coverage is bounded, never full-depth. UI display depth must not truncate reconstruction. Binance's initial unknown-depth boundary is preserved as described below.

Each subscription has a separate `book` or `trades` key. Callers must select a supported reconstruction depth explicitly (global also permits `None`, retaining the documented 5,000-level bootstrap request). Unsupported channels, aggregation or depth fail rather than silently selecting another feed. Binance connections use combined-stream envelopes so even US partial snapshots have an exact stream/symbol identity. Endpoint URLs are checked against the exact configured request; symbol-less REST responses are bound to that symbol-specific request, not guessed from payload prices.

## Metadata and identity

Discovery uses only the shared `transport.fetch_public` retry/rate-limit boundary, then `exact_json_loads` over original bytes:

- Binance global: `https://data-api.binance.vision/api/v3/exchangeInfo`.
- Binance.US: `https://api.binance.us/api/v3/exchangeInfo`.
- Bybit mainnet: `https://api.bybit.com/v5/market/instruments-info?category=spot` (spot has no pagination).
- KuCoin classic spot: `https://api.kucoin.com/api/v2/symbols`.

`parse_metadata(raw, fetch_wall_ns=...)` is the pure fixture/replay entry point. Metadata versions combine the exact payload SHA256 and fetch-wall timestamp, keeping catalog versions immutable on repeated fetches. Products preserve venue, API family, native symbol, spot market, exact tick/lot and available minimum constraints. Status is mapped explicitly to active/suspended/delisted/unavailable/unknown rather than treating every metadata row as tradable. Bybit spot ignores deprecated `minOrderQty`, `maxOrderQty`, and `maxOrderAmt`; active `minOrderAmt` remains quote-denominated. `maxLimitOrderQty` and `maxMarketOrderQty` populate separate `OrderConstraints.max_limit_base` / `max_market_base` fields, never a generic maximum. Missing ceilings stay `None`. `Instrument.validate_order(price, quantity, *, order_type="limit" | "market")` requires an explicit order type and applies only that type's ceiling alongside generic constraints. Post-only/RPI are not modeled as ordinary limit validation; unsupported order types fail.

For Bybit `symbolType=xstocks` only, `Instrument.reference_multiplier` retains positive exact `xstockMultiplier` (documented default `1` when omitted): `stock_price = token_price / reference_multiplier`, `stock_qty = token_qty * reference_multiplier`. This is reference metadata, not executable stock identity or fungibility evidence. The base remains the token identity, `base.reference_asset_id` stays unknown, and token book/trade prices, quantities, order amounts and `contract_multiplier=1` are unchanged. Non-xstock products have no reference multiplier. A supplied `symbolId` is retained as native metadata identity; no stable-Spot-rename guarantee is inferred from the documentation's separate TradFi-options warning.

Products are ordered BTC, ETH, SOL, XRP, DOGE, ADA, LTC, LINK, AVAX, DOT, then other listed products. This is a discovery priority, not a list of invented pairs. Canonical base tickers and USD/USDT/USDC retain common asset IDs. Other tokens remain venue-scoped until their cross-venue equivalence is explicitly established. Missing symbol/precision/filter metadata fails explicitly. Suspended products remain visible in metadata but cannot create active subscriptions. Native quote identity is never replaced with USD.

Metadata success alone leaves `data_access` and `execution_access` unknown. Supervision/access evidence must establish usable dependencies; execution permission remains separate. Global and US instrument IDs are different. Selecting US after global access failure must create a distinct stream/segment with its original venue and quote cohort; the adapters never relabel or merge them. Preference/access evidence and source selection belong to supervision, not a fallback hidden inside a decoder.

## Binance bootstrap and receive order

The global snapshot host is `https://data-api.binance.vision`; the stream is `wss://data-stream.binance.vision:443/stream`. Supervision opens/receives the diff stream before fetching REST. It delivers every decoded receive transaction once, in original ingest-ordinal order. Bootstrap REST envelopes use `ws_delta` because they initialize that stream; standalone REST fallback uses `rest_snapshot` and a separately segmented snapshot reducer.

`BinanceGlobalReducer` buffers original immutable pre-snapshot envelopes, bounded to 4,096 events and 200,000 level changes. Overflow invalidates and requests recovery. A snapshot older than the first buffered `U` requests another snapshot while retaining the buffer. Obsolete updates (`u <= lastUpdateId`) are skipped. The first applicable range must bridge the snapshot (`U <= lastUpdateId + 1 <= u`); subsequent ranges must bridge the last applied ID. Absolute quantities replace, zero deletes, repeated same-price changes apply in message order, duplicates do not reapply, and a missing range invalidates the book.

A private candidate stages the snapshot and applies buffered updates in exchange reconstruction order, without changing any original envelope ordinal, timestamp or payload. Only that private candidate clears its receive cursor between the snapshot and earlier buffered envelopes. The published receive cursor and `Book.asof` remain at the actual later snapshot receipt. Normal incoming frames still pass the full identity/epoch/connection/clock/source/order fences. A checkpoint contains the entire buffered envelope tuple, sequence and complete core reducer state. No buffer or cursor is hidden in an uncheckpointed mutable closure. Before a bridge exists, a staged snapshot remains synchronizing and unusable.

A 5,000-level bootstrap side has a fixed known-price boundary. A later changed level outside that region does **not** reveal unchanged intervening orders. The reducer retains the original raw/normalized update and advances sequence evidence, but excludes out-of-bound levels from the usable reconstructed Book. Coverage therefore cannot expand from sparse observations. If known depth is exhausted, recovery requires a fresh snapshot. A side returned with fewer than the requested 5,000 rows is initially complete; both complete sides allow `full_depth=True`. Bounds are serialized in protocol state, validated atomically on restore and cleared on reset. Strategy `binance-global-bridge-frontier-v2` rejects old incompatible checkpoints.

The owner must recover from returned `RecoveryRequest`, reset/resegment on reconnect/source switches, and never treat acknowledgements as synchronization. Binance global server shutdown is a disconnect control. Server protocol PING frames require protocol PONG replies with the same payload. The documented global lifetime is 24 hours; global server pings are every 20 seconds with a one-minute timeout. Global client control traffic is limited to five messages/second including PING/PONG, and connection-attempt limits apply. US uses its independently documented ping/lifetime rules. Rate budgeting, bounded retry, session rotation and cancellation are the shared transport/supervisor's responsibility; decoders contain no second retry loop.

## Bybit reset and trade semantics

Exact topic and payload symbol must both match. `type=snapshot` always replaces the book, including a lower new update ID after service reset. `u=1` is also normalized as a replacement snapshot even when the outer type says delta. Missing initialization rejects a delta. Non-reset deltas require increasing `u` and cross `seq`; duplicates/stale generations are ignored, but skipped numeric IDs are not fabricated gaps. Core checkpoints preserve both counters (`SequenceInfo.last=u`, `first=seq`). A repeated level-1 snapshot is a healthy observation, not an invented delta. Requested-depth truncation is applied after a whole message.

Bybit public trade `S` is documented as taker side (`Buy`/`Sell`), not maker side or tick direction. Trade-channel `type=snapshot` is the protocol's live message type and does not mark every live print as historical. REST recent trades are historical observations. Trade `seq` is not used as a dedup ID because multiple messages can share it. Trade IDs are scoped by exact instrument identity.

Bybit application heartbeat uses `{"op":"ping"}` every 20 seconds in supervision; recognized pong replies are heartbeat controls only. Explicit subscription failure returns a rejection control, not a successful book observation.

## Trade provenance, failures and limitations

Binance raw trade IDs remain exact strings, including integers above JavaScript's safe range. `m=true` (buyer is maker) means the taker sold; `false` means the taker bought. Missing maker information stays unknown. WS and REST use the same instrument-scoped dedup key. Original duplicate raw frames and decoded observations are retained so downstream deduplication is deterministic and replayable.

KuCoin classic recent-history `sequence` becomes an instrument-scoped observation dedup key, not an invented trade ID or continuous history counter. Its documented REST side has not been established here as taker-side evidence, so aggressor side remains unknown with explicit provenance, even when the source says buy/sell. Polls are recent subsets, not complete trade history; all are marked historical observations. Missing trade history anchors must remain visible in downstream analytics.

Exchange timestamps are preserved in exact nanoseconds according to each documented millisecond/nanosecond field. No timestamp is synthesized when absent. Original lexical decimal precision survives decode. Malformed JSON, duplicate keys, nonfinite values, malformed arrays, invalid increments, wrong topic/symbol/endpoint and endpoint error objects return an atomic `DecodeError` with original raw SHA256 and no partial events. One bad trade in a batch fails the whole batch. Candidate-book invariant failures are handled atomically by the shared reducer; empty/crossed/duplicate snapshot levels cannot become usable.

## Evidence and protocol sources

`tests/test_workstation_asian.py` contains synthetic offline fixtures for metadata/status/priority, endpoint and schema failures, exact trade IDs/sides/unknown sides, dedup identity, bootstrap buffering/checkpoint restoration/bridge/gap/overflow, receive-order fencing, US replacement snapshots and cohort distinctions, Bybit non-contiguous/stale/reset/depth semantics, and KuCoin degraded capability. These fixtures are not live captures. No tests, formatting, type checks, live probes or network integration were run by this adapter implementation worker; the integration owner records executed evidence separately. Earlier host reachability evidence does not certify this implementation's decoding or synchronization.

Inspected primary references:

- [Binance global stream protocol and local-book algorithm](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).
- [Binance data-only hosts](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only).
- [Binance.US independent stream specification](https://github.com/binance-us/binance-us-api-docs/blob/master/web-socket-streams.md).
- [Bybit v5 spot instrument metadata](https://bybit-exchange.github.io/docs/v5/market/instrument).
- [Bybit v5 WebSocket orderbook/reset/depth rules](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook).
- [Bybit v5 public trade taker semantics](https://bybit-exchange.github.io/docs/v5/websocket/public/trade).
- [Bybit v5 REST orderbook, current 1–1000 spot depth](https://bybit-exchange.github.io/docs/v5/market/orderbook).
- [KuCoin classic public partial orderbook](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-part-orderbook).
- [KuCoin classic public recent trade history](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-trade-history).
