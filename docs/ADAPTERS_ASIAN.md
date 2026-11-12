# Binance, Bybit, KuCoin, OKX and Bitget public adapters

These are the shared workstation adapters in `src/workstation/adapters`; both observation clients consume their output through the native API. The separate legacy UI adapters are retired. They perform public market-data operations only. No credentials, signing, testnet, order submission or execution eligibility is inferred.

## Implemented modes

| Adapter / stable venue | API family | Books | Public trades | Continuity limits |
| --- | --- | --- | --- | --- |
| `BinanceGlobalAdapter` / `binance-global` | `spot-v3` | Verified metadata: REST snapshot, 5,000 levels/side plus buffered `depth@100ms`; streaming-only metadata: independent top20 WS snapshots | `trade` stream and recent REST trades | Verified diff mode uses update-ID bridge/gap checks; top20 snapshot mode does not claim exact event flow |
| `BinanceGlobalAdapter` / `binance-global` | `usdm-v1` | USDⓈ-M USDT perpetual partial snapshots at 5/10/20 levels, 100 ms; REST fallback | `aggTrade` stream and recent REST aggregate trades | Independent bounded snapshots, not spot diff reconstruction or exact event flow |
| `BinanceUSAdapter` / `binance-us` | `spot-v3` | Independent partial snapshots at 5/10/20 levels, 100 ms; REST fallback | `trade` stream and recent REST trades | No diff continuity claim; never fed through global bootstrap |
| `BybitAdapter` / `bybit` | `v5-spot`, `v5-linear` | Spot and USDT linear crypto perpetual `orderbook.{depth}.{symbol}`, depths 1/50/200/1000; independent REST fallback at the requested depth | `publicTrade.{symbol}` and recent REST trades | Monotonic update/cross-sequence checks, not fictional contiguous IDs; depth 1 is snapshots only |
| `KuCoinAdapter` / `kucoin` | `classic-spot` | Public REST `level2_20` / `level2_100` snapshots | Public REST `histories` observations | No implemented WebSocket, no continuous history, no checksum or sequence continuity claim |
| `OKXAdapter` / `okx` | `v5-spot`, `v5-swap` | Spot and supported linear crypto perpetual `books5`, five-level WS replacement snapshots and REST fallback | WS `trades` and recent REST trades | Snapshot IDs do not establish incremental continuity; no checksum or exact event flow |
| `BitgetAdapter` / `bitget` | `v2-spot`, `v2-usdt-futures` | Spot and USDT crypto perpetual `books5`, five-level WS replacement snapshots and REST fallback | WS `trade` and recent REST fills | Bounded snapshots, no sequence/checksum or exact event-flow guarantee |

Synchronized Binance global **spot** diffs support **feed-message OFI over 100 ms aggregated updates** (`exact_event_flow=True` means continuity of received validated deltas, not every matching-engine order). Gap, initialization and source-mode gates remain mandatory. Bybit's monotonic counters do not establish gap-free continuity, so its exact-flow capability remains false. KuCoin, Binance USDⓈ-M and US snapshot differences are not event OFI. KuCoin snapshot reducers report `rest_snapshot`/`degraded`; snapshot coverage is bounded, never full-depth. UI display depth must not truncate reconstruction. Binance spot's initial unknown-depth boundary is preserved as described below.

Each subscription has a separate `book` or `trades` key. The catalog resolves implicit Binance spot depth to 20 for streaming-only descriptors and 5,000 for verified descriptors; pass the pinned Instrument to `canonical_key`. Unsupported channels, aggregation or depth fail rather than silently selecting another feed. Binance combined-stream envelopes retain exact stream/symbol identity. Endpoint URLs are checked against the configured request; symbol-less REST responses are bound to that symbol-specific request, never guessed from prices.

None of these adapters implements a `reference`/`context` channel or an OI/funding
history source. A venue offering those APIs does not make them integrated here.
Optional REST candle history covers Binance spot/USDⓈ-M, Binance.US spot, Bybit
spot/linear and KuCoin spot; it is not implemented for OKX or Bitget. Admitted
public trades can independently build session candles without inventing missing
pre-session history.

## Metadata and identity

Discovery uses only the shared `transport.fetch_public` retry/rate-limit boundary, then `exact_json_loads` over original bytes:

- Binance global spot: `https://data-api.binance.vision/api/v3/exchangeInfo`.
- Binance global USDⓈ-M: `https://fapi.binance.com/fapi/v1/exchangeInfo`.
- Binance.US: `https://api.binance.us/api/v3/exchangeInfo`.
- Bybit mainnet spot: `https://api.bytick.com/v5/market/instruments-info?category=spot` (spot has no pagination).
- Bybit mainnet linear: `https://api.bytick.com/v5/market/instruments-info?category=linear&limit=1000`, following the returned cursor.
- KuCoin classic spot: `https://api.kucoin.com/api/v2/symbols`.

`api.bytick.com` remains the configured REST host for Bybit books, trades and candles. Metadata discovery starts there and permits one bounded peer attempt at `api.bybit.com` only after a transport/network failure or HTTP 500–599 service failure. Both hosts are listed in Bybit's [official integration guide](https://bybit-exchange.github.io/docs/v5/guide). The peer restarts the entire family, including linear pagination at page one; pages from different hosts/generations are never combined. HTTP 401/403/451 denial, 404/410 unsupported responses, 429 rate limits, other rejected statuses and malformed/schema/API-error responses do not authorize a peer attempt. An open circuit retains its triggering failure rather than turning a denial into retry permission. This is not an access-restriction bypass. Binance USDⓈ-M uses only the documented `fapi.binance.com` endpoint, not testnet, demo or an unofficial proxy.

Shared transport pacing honors `Retry-After` seconds or HTTP dates on retryable 429/5xx responses. Its per-origin deadline delays actual request starts, including other callers; a waiter rechecks an extended cooldown after waking. A delay above 120 seconds opens the origin circuit rather than retrying early. REST fallback also waits the existing HTTP retry deadline. These are bounded acquisition rules, not evidence that a host or product is currently reachable.

`parse_metadata(raw, fetch_wall_ns=...)` is the pure fixture/replay entry point; Binance accepts an explicit `api_family` for USDⓈ-M. Metadata versions retain payload-hash and fetch-wall provenance. Products preserve venue, API family, native symbol, native market type, exact tick/lot and available minimum constraints. Status is mapped explicitly to active/suspended/delisted/unavailable/unknown rather than treating every metadata row as tradable. Bybit spot ignores deprecated `minOrderQty`, `maxOrderQty`, and `maxOrderAmt`; active `minOrderAmt` remains quote-denominated. `maxLimitOrderQty` and `maxMarketOrderQty` populate separate `OrderConstraints.max_limit_base` / `max_market_base` fields, never a generic maximum. Missing ceilings stay `None`. `Instrument.validate_order(price, quantity, *, order_type="limit" | "market")` requires an explicit order type and applies only that type's ceiling alongside generic constraints. Post-only/RPI are not modeled as ordinary limit validation; unsupported order types fail.

Binance USDⓈ-M discovery includes `contractType=PERPETUAL` with USDT quote and requires USDT margin. Bybit linear discovery excludes `symbolType` values `stock`, `ETF`, `commodity` and `forex`, includes only `LinearPerpetual` with USDT quote, and requires USDT settlement. These perpetuals retain native `qtyStep`/lot filters, contract specifications and order-type ceilings; spot constraints are not substituted. Bybit linear pagination must complete before that family is published: repeated cursors, invalid pages or the 32-page bound fail the family rather than exposing a truncated catalog.

Binance `spot-v3` / `usdm-v1` and Bybit `v5-spot` / `v5-linear` discovery run with independent family deadlines (30 seconds by default). A complete successful family is published even when the other is blocked, unsupported, malformed or timed out. `discovery_evidence` retains each family's state and source or failure reason, including HTTP status when available. Native catalog dependencies retain `metadata:<api_family>` alongside aggregate `metadata`. Mixed success reports venue state `degraded`, aggregate metadata `partial`, and detail `metadata_partial`; it does not call the failed family available. If none succeeds, no new family snapshot is published: equal failure states are retained, differing failure states aggregate to `unavailable`, with `metadata_unavailable` detail. Missing products and precision are never invented, and another venue is never substituted.

The live terminal calls `seed_streaming()` before asynchronous discovery. Bundled
crypto candidates for these four families have unknown listing status, null
tick/lot sizes and `metadata_status="streaming_only"`. REST metadata denial does
not prohibit direct public WS subscriptions. Valid messages, not candidates or
subscription acknowledgements, establish readable market evidence. Display/flow
and capture/replay are permitted; metadata-dependent financial paths are not.
Only a complete successful family can retire products absent from that family's authoritative result. Retirement marks the latest discovery descriptor `unavailable`, using that family's own source, payload hash and fetch time in a `missing:<hash>:<fetch_wall_ns>` version (with the identity-policy suffix when applicable); it is not a venue-wide deletion or proof of exchange delisting.
Failed families keep their candidates and explicit metadata failure reasons, without retirement from another family's success.
Existing shared streams and captures retain their pinned descriptor versions. The financial presentation of the selected instrument uses that pinned descriptor, while discovery choices may show newer metadata; no active stream or capture is silently upgraded.

For Bybit spot `symbolType=xstocks` only, `Instrument.reference_multiplier` retains positive exact `xstockMultiplier` (documented default `1` when omitted): `stock_price = token_price / reference_multiplier`, `stock_qty = token_qty * reference_multiplier`. This is reference metadata, not executable stock identity or fungibility evidence. The base remains the token identity, `base.reference_asset_id` stays unknown, and token book/trade prices, quantities, order amounts and `contract_multiplier=1` are unchanged. Non-xstock products have no reference multiplier. A supplied `symbolId` is retained as native metadata identity; no stable-Spot-rename guarantee is inferred from the documentation's separate TradFi-options warning.

The explicit shared canonical29 crypto policy, in discovery-priority order, is **BTC, ETH, SOL, XRP, DOGE, ADA, LTC, LINK, AVAX, DOT, BNB, TRX, HYPE, SUI, BCH, UNI, NEAR, APT, AAVE, ETC, ENA, ATOM, ARB, OP, INJ, FIL, SEI, TIA, WIF**. This appends 19 curated assets to the original ten and is a mapping/priority policy, not a list of invented venue pairs. Binance and Bybit use the shared identity path; Coinbase and Kraken use the same curated policy. USD, USDT and USDC also retain distinct common asset IDs, never parity or interchangeability.

Newly parsed descriptors involving one of the 19 added base, quote or settlement assets receive the `:canonical-assets-v2` metadata-version suffix while preserving source payload-hash and fetch-time provenance. Historical captured descriptors, including their older venue-scoped asset IDs, are not rewritten or reinterpreted. Native aliases stay the parser's responsibility; case differences, network-prefixed/wrapped tokens and unknown tickers are not generalized into canonical equivalence. Missing symbol/precision/filter metadata fails explicitly. Suspended products remain visible in metadata but cannot create active subscriptions. Native quote identity is never replaced with USD.

Metadata success alone leaves `data_access` and `execution_access` unknown. Supervision/access evidence must establish usable dependencies; execution permission remains separate. Exact-product probes retain `probed_instrument_id` and resolve `metadata:product` from that product's API family, separately from snapshot, trade and WebSocket evidence. A successful product probe can report that product available while another family remains blocked; it is not venue-wide or all-channel certification. Global and US instrument IDs are different. Selecting US after global access failure must create a distinct stream/segment with its original venue and quote cohort; the adapters never relabel or merge them. Preference/access evidence and source selection belong to supervision, not a fallback hidden inside a decoder.

Readiness is channel- and source-specific: a catalog candidate, verified pinned financial metadata, observed usable book and observed trades are separate evidence. A book observation does not establish trade readiness; a catalog entry or subscription acknowledgment establishes neither. Derivative quote/contract notional is exposure, not available cash, settlement collateral or margin. Exact pinned contract specifications and native settlement govern financial interpretation, regardless of aggregate display labels.

Comparison, depth walking and paper fills fail on unverified pinned metadata.
Supported perpetual valuation and isolated one-way paper IOC additionally require
explicit contract economics, risk/funding assumptions and collateral; neither
notional exposure nor a public data observation supplies them. Linear, inverse
and documented quanto support in the financial engine does not imply that every
adapter implements every contract type. Missing constraints/fees/history remain
unknown, not zero. See [financial models](COSTS.md).

## Binance spot bootstrap and receive order

This REST bridge applies only to verified-metadata spot diff streams. Streaming-only
spot uses `symbol@depth20@100ms` on the same data-only WebSocket endpoint, initializes
from full bounded WS snapshots and never waits for a REST bootstrap. Its snapshot
reducer, depth and capabilities remain distinct in recording/replay.

The global spot snapshot host is `https://data-api.binance.vision`; the stream is `wss://data-stream.binance.vision:443/stream`. Supervision opens/receives the diff stream before fetching REST. It delivers every decoded receive transaction once, in original ingest-ordinal order. Bootstrap REST envelopes use `ws_delta` because they initialize that stream; standalone REST fallback uses `rest_snapshot` and a separately segmented snapshot reducer.

`BinanceGlobalReducer` buffers original immutable pre-snapshot envelopes, bounded to 4,096 events and 200,000 level changes. Overflow invalidates and requests recovery. A snapshot older than the first buffered `U` requests another snapshot while retaining the buffer. Obsolete updates (`u <= lastUpdateId`) are skipped. The first applicable range must bridge the snapshot (`U <= lastUpdateId + 1 <= u`); subsequent ranges must bridge the last applied ID. Absolute quantities replace, zero deletes, repeated same-price changes apply in message order, duplicates do not reapply, and a missing range invalidates the book.

A private candidate stages the snapshot and applies buffered updates in exchange reconstruction order, without changing any original envelope ordinal, timestamp or payload. Only that private candidate clears its receive cursor between the snapshot and earlier buffered envelopes. The published receive cursor and `Book.asof` remain at the actual later snapshot receipt. Normal incoming frames still pass the full identity/epoch/connection/clock/source/order fences. A checkpoint contains the entire buffered envelope tuple, sequence and complete core reducer state. No buffer or cursor is hidden in an uncheckpointed mutable closure. Before a bridge exists, a staged snapshot remains synchronizing and unusable.

A 5,000-level bootstrap side has a fixed known-price boundary. A later changed level outside that region does **not** reveal unchanged intervening orders. The reducer retains the original raw/normalized update and advances sequence evidence, but excludes out-of-bound levels from the usable reconstructed Book. Coverage therefore cannot expand from sparse observations. If known depth is exhausted, recovery requires a fresh snapshot. A side returned with fewer than the requested 5,000 rows is initially complete; both complete sides allow `full_depth=True`. Bounds are serialized in protocol state, validated atomically on restore and cleared on reset. Strategy `binance-global-bridge-frontier-v2` rejects old incompatible checkpoints.

The owner must recover from returned `RecoveryRequest`, reset/resegment on reconnect/source switches, and never treat acknowledgements as synchronization. Binance global server shutdown is a disconnect control. Server protocol PING frames require protocol PONG replies with the same payload. The documented global lifetime is 24 hours; global server pings are every 20 seconds with a one-minute timeout. Global client control traffic is limited to five messages/second including PING/PONG, and connection-attempt limits apply. US uses its independently documented ping/lifetime rules. Rate budgeting, bounded retry, session rotation and cancellation are the shared transport/supervisor's responsibility; decoders contain no second retry loop.

USDⓈ-M uses `https://fapi.binance.com/fapi/v1` REST and separate `wss://fstream.binance.com/public/stream` book / `wss://fstream.binance.com/market/stream` trade routes. Its top-N books replace bounded snapshots and never use the spot bootstrap algorithm. `aggTrade` observations are provider-aggregated trades, not a claim to every matching-engine fill.

## Bybit reset and trade semantics

Spot uses `wss://stream.bybit.com/v5/public/spot`; linear uses `wss://stream.bybit.com/v5/public/linear`. REST requests retain the matching `category`, and API family remains part of exact subscription identity.

Exact topic and payload symbol must both match. `type=snapshot` always replaces the book, including a lower new update ID after service reset. `u=1` is also normalized as a replacement snapshot even when the outer type says delta. Missing initialization rejects a delta. Non-reset deltas require increasing `u` and cross `seq`; duplicates/stale generations are ignored, but skipped numeric IDs are not fabricated gaps. Core checkpoints preserve both counters (`SequenceInfo.last=u`, `first=seq`). A repeated level-1 snapshot is a healthy observation, not an invented delta. Requested-depth truncation is applied after a whole message.

Bybit public trade `S` is documented as taker side (`Buy`/`Sell`), not maker side or tick direction. Trade-channel `type=snapshot` is the protocol's live message type and does not mark every live print as historical. REST recent trades are historical observations. Trade `seq` is not used as a dedup ID because multiple messages can share it. Trade IDs are scoped by exact instrument identity.

Bybit application heartbeat uses `{"op":"ping"}` every 20 seconds in supervision; recognized pong replies are heartbeat controls only. Explicit subscription failure returns a rejection control, not a successful book observation.

## OKX and Bitget bounded public products

OKX discovers `https://www.okx.com/api/v5/public/instruments?instType=SPOT` and
`instType=SWAP`; Bitget discovers
`https://api.bitget.com/api/v2/spot/public/symbols` and
`https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES`.
Unlike the independent Binance/Bybit family acquisition above, each current
OKX/Bitget discovery method completes both requests before publishing its combined
result. Failure is not a partial-family success claim.

OKX excludes non-crypto instrument categories and inverse swaps. Supported linear
swaps require `ctValCcy` equal to the base and `settleCcy` equal to the quote.
Book/trade size, `lotSz`, `minSz`, `maxLmtSz` and swap `maxMktSz` stay in native
**contracts**; `ctVal` is the complete base face value per contract, not an extra
size multiplier to apply to native limits. Spot `maxMktSz` is not imported as a
base-size ceiling because it is quote-denominated. New swap descriptors use
`okx-v2-native-size`; historical `okx-v1` descriptors remain immutable and shared
`native_order_constraints` interprets their legacy size convention.

An OKX **unpublished SPOT preopen announcement** is skipped only when
`state="preopen"`, `instCategory=""`, and all of `baseCcy`, `quoteCcy`, `tickSz`,
`lotSz`, `minSz` are empty strings. It is not yet an instrument definition.
A complete preopen definition remains visible as `unavailable`; other incomplete
definitions fail rather than deriving currencies from `instId` or inventing
precision. `post_only`/`test` are unavailable and `suspend`/`rebase` suspended,
not active ordinary products.

OKX uses `wss://ws.okx.com:443/ws/v5/public`, `books5`/`trades`, and REST
`/api/v5/market/books?instId=...&sz=5` or recent trades. Each book replaces the
five-level window; repeated/reset snapshot IDs do not prove missing deltas.
Connection-wide notices are informational controls, never subscription
acknowledgments or market observations.

Bitget uses `wss://ws.bitget.com/v2/ws/public`, retaining `SPOT` versus
`USDT-FUTURES` subscription identity. Perpetual discovery excludes `isRwa=YES`
and non-perpetual `symbolType`, requires USDT quote and supported USDT margin,
and preserves base-coin `sizeMultiplier`/quantity rules (not integer contracts).
`restrictedAPI` retains an active public product but explicitly sets
`execution_access="blocked"`; `maintain`/`limit_open` are suspended, `off`
delisted and `listed` unavailable. Spot uses REST `spot/market/orderbook` and
`fills`; perpetuals use `mix/market/merge-depth` and `fills`, always under v2
with the matching product family. Five-level books never imply deeper coverage.

Neither adapter implements options, dated futures, private account access or
exchange order execution. HTTP denial stays explicit; endpoint support in code
is not proof of this environment's current access or legal/account eligibility.

## Trade provenance, failures and limitations

Binance spot raw trade IDs remain exact strings, including integers above JavaScript's safe range; USDⓈ-M retains exact aggregate-trade IDs. `m=true` (buyer is maker) means the taker sold; `false` means the taker bought. Missing maker information stays unknown. WS and REST use the same instrument-scoped dedup key. Original duplicate raw frames and decoded observations are retained so downstream deduplication is deterministic and replayable.

KuCoin classic recent-history `sequence` becomes an instrument-scoped observation dedup key, not an invented trade ID or continuous history counter. Its documented REST side has not been established here as taker-side evidence, so aggressor side remains unknown with explicit provenance, even when the source says buy/sell. Polls are recent subsets, not complete trade history; all are marked historical observations. Missing trade history anchors must remain visible in downstream analytics.

Exchange timestamps are preserved in exact nanoseconds according to each documented millisecond/nanosecond field. No timestamp is synthesized when absent. Original lexical decimal precision survives decode. Malformed JSON, duplicate keys, nonfinite values, malformed arrays, invalid increments, wrong topic/symbol/endpoint and endpoint error objects return an atomic `DecodeError` with original raw SHA256 and no partial events. One bad trade in a batch fails the whole batch. Candidate-book invariant failures are handled atomically by the shared reducer; empty/crossed/duplicate snapshot levels cannot become usable.

## Evidence and protocol sources

`tests/test_workstation_asian.py` contains synthetic offline fixtures for metadata/status/priority, endpoint and schema failures, exact trade IDs/sides/unknown sides, dedup identity, bootstrap buffering/checkpoint restoration/bridge/gap/overflow, receive-order fencing, US replacement snapshots and cohort distinctions, Bybit non-contiguous/stale/reset/depth semantics, and KuCoin degraded capability. These fixtures are not live captures. Executed runtime evidence is recorded separately by the integration owner; adapter presence and earlier host reachability do not certify every family, decoding, synchronization or sustained availability.

`tests/test_workstation_okx.py` covers unpublished-announcement versus complete
preopen handling, native swap size limits, notices, bounded snapshots and trade
identity. `tests/test_workstation_bitget.py` covers spot/perpetual metadata,
`restrictedAPI`, native quantity rules and snapshot/trade decoding.
`tests/test_workstation_public_boundary.py` and
`tests/test_workstation_transport.py` cover the credential-free request boundary
and origin-wide retry pacing. No tests or external live revalidation were run
for this documentation audit; older dated access evidence remains historical.

Primary protocol references (not a fresh live-access check):

- [Binance global stream protocol and local-book algorithm](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md).
- [Binance data-only hosts](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only).
- [Binance.US independent stream specification](https://github.com/binance-us/binance-us-api-docs/blob/master/web-socket-streams.md).
- [Bybit official mainnet hosts and access restrictions](https://bybit-exchange.github.io/docs/v5/guide).
- [Bybit v5 spot and linear instrument metadata](https://bybit-exchange.github.io/docs/v5/market/instrument).
- [Bybit v5 WebSocket orderbook/reset/depth rules](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook).
- [Bybit v5 public trade taker semantics](https://bybit-exchange.github.io/docs/v5/websocket/public/trade).
- [Bybit v5 REST orderbook, current 1–1000 spot depth](https://bybit-exchange.github.io/docs/v5/market/orderbook).
- [KuCoin classic public partial orderbook](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-part-orderbook).
- [KuCoin classic public recent trade history](https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-trade-history).
- [Binance USDⓈ-M partial book streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Partial-Book-Depth-Streams).
- [OKX public instrument definitions](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments).
- [OKX market-data book and trade channels](https://www.okx.com/docs-v5/en/#order-book-trading-market-data).
- [OKX connection notices](https://www.okx.com/docs-v5/en/#overview-websocket-notification).
- [Bitget spot depth channel](https://www.bitget.com/docs/classic/websocket/spot/public/Depth-Channel).
- [Bitget classic perpetual contract metadata and status](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market).
