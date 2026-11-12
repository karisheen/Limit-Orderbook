# Hyperliquid public adapter

`src.workstation.adapters.hyperliquid.HyperliquidAdapter()` uses only public
`https://api.hyperliquid.xyz/info` and `wss://api.hyperliquid.xyz/ws`. No wallet,
signing, private endpoints, or execution credentials are required or accepted.
Discovery uses the shared bounded-retry `fetch_public` transport; decoding and
metadata normalization preserve original decimal text. Offline fixtures are
synthetic, not recordings or evidence of live adapter verification.

## Identity and discovery

Discovery reads `spotMeta` and `meta`. All listed default perpetuals, including
BTC, ETH, SOL, HYPE and PURR when listed, and all metadata spot pairs are represented.
Delisted perpetuals retain delisted status. Data/execution access remain unknown
until independently established; successful metadata retrieval alone is not a
healthy book or permission to trade.

Spot tokens are resolved by the `tokens` indices into `spotMeta.tokens`.
`AssetIdentity.asset_id` includes the HyperCore `tokenId`, `native_token_id`
retains the integer token index, and any metadata EVM contract address is retained
without equating HyperCore and EVM assets. The spot instrument native asset ID is
`10000 + universe.index`, not the token index. Native subscription symbols are
`PURR/USDC` for pair zero and `@{index}` for other pairs. UBTC remains UBTC with its
own token identity; the frontend alias BTC does not make it Bitcoin. Likewise,
HYPE spot and HYPE perpetual remain different instruments. No equivalence or
wrapped-to-underlying conversion is inferred.

Default perpetual native IDs are universe positions. **Quote denomination is not
collateral identity.** The official default crypto-perpetual contract specification
denominates BTC, ETH, SOL and other ordinary default contracts in **USDT**; HYPE
and PURR are the documented **USDC** exceptions. Quote identities are the canonical
denomination (`USDT` or `USDC`), not invented HyperCore token mappings.
Settlement independently resolves the exact native token from `collateralToken`,
including its token ID, index, network and contract address. If `meta` omits that
field, discovery requests `metaAndAssetCtxs` and uses its metadata instead.
Unknown collateral references fail discovery rather than inventing a currency.

The default contract multiplier is one underlying base unit, explicitly recorded
with `contract_value_currency=base.asset_id` and `quantity_unit="base"`.
`contract_specification_source` records the official contract specification.
For USDT-denominated default perps, `settlement_conversion="quanto_no_fx"` means
USDT P&L is settled numerically in USDC **without applying a USDT/USDC exchange
rate**. This is a contract payoff convention, not an FX rate, stablecoin
equivalence, executable conversion, or permission to merge quote cohorts.
HYPE/PURR use `settlement_conversion="none"`; their canonical USDC quote remains
distinct from the independently discovered native settlement token identity.

The default DEX namespace is recorded as `default`. Optional
`HyperliquidAdapter(dex="xyz")` resolves the DEX index through `perpDexs`, requires
namespaced native symbols, and uses `100000 + dex_index * 10000 + universe_index`.
HIP-3 explicitly leaves oracle definition and contract specification to the
deployer: the default denomination and quanto rules **do not apply by inference**.
The current metadata endpoints do not establish a builder contract's quote
denomination. Its instrument therefore retains native settlement and namespace,
but uses a per-contract `hyperliquid:unknown-quote:{native_symbol}` identity with
ticker `UNKNOWN`, unknown settlement conversion and contract-value currency,
and unsupported data/execution access. Every channel rejects use with an explicit
unsupported-builder-denomination error, rather than publishing mislabeled prices.
Its contract specification source identifies HIP-3, not a verified deployer
contract. Builder reference assets remain namespace-scoped, not silently equivalent
to a default perpetual with a similar ticker. Global spot discovery and channels
remain available even on a builder-configured adapter.

Instrument IDs preserve venue/API/market/native symbol. The metadata payload hash
is SHA256 of the exact discovery payloads separated by NUL bytes. Metadata version
is `payload_hash:fetch_wall_ns:hyperliquid-denomination-v2`, preserving immutable
catalog versions and identifying the corrected normalization rules even for the
same discovery bytes. Timestamps record fetch completion. Existing catalogs with
the old incorrect quote identity must be rebuilt; stable-identity validation does
not silently reinterpret historical instruments or events.
Lot size and the smallest positive base size come from `szDecimals`. Price rules are variable:
maximum five significant figures and at most `6 - szDecimals` decimal places for
perpetuals or `8 - szDecimals` for spot. Integer prices are always allowed, even
above five significant figures. Trailing zeroes do not alter validity. The
adapter's `validate_price` callback performs this check without rounding or
relying on the caller's Decimal context; core reducer restore also uses it.

The documented spot minimum is **10 units of the actual quote token**, recorded
as `min_notional=10` and `min_notional_currency=quote.asset_id`, including non-USDC
spot quote tokens. It is an order constraint, not a minimum public book level or
trade size. Perpetual rejection documentation gives a `$10` minimum, and the
contract specification gives leverage-dependent maximum market values with
limit-order maxima ten times larger. Those dollar-symbol statements do not
unambiguously establish an asset identity for the constraint (USDT denomination
versus native USDC collateral), so perpetual notional minima/maxima remain
unknown rather than inventing USD, an FX conversion, or one universal maximum.
No unsupported maximum-size or account-dependent constraint is synthesized.

## Channels and bounded book evidence

Use `SubscriptionKey.for_instrument(instrument, channel=...)` with these channels:

| Channel | WebSocket | Info fallback | Normalized payload |
| --- | --- | --- | --- |
| `book` | `l2Book` | `l2Book` | `BookUpdate(kind="snapshot")` |
| `trades` | `trades` | None | `Trade` |
| `reference` | `allMids` | `allMids` | `ReferencePrice(kind="mid")` |
| `context` | `activeAssetCtx` | `metaAndAssetCtxs` / `spotMetaAndAssetCtxs` | `AssetContext` |

The default book is **slow20**, a bounded replacement of up to twenty levels per
side, block-triggered at least about 0.5 seconds apart. Depth may be omitted or
set to 20. Omitted depth resolves to the effective reconstruction bound of 20
without rewriting the physical subscription key. Normalized book payloads and
reducer depth validation use that same bound; mismatched payload depths still
fail closed. Events, published books and checkpoints retain the original key.
The reducer's coverage always records the actual bound, retained levels, worst
prices, `fast=false`, and expected slow cadence of 500,000,000 ns.
Cadence is descriptive, not a latency or availability guarantee. Each snapshot
replaces prior sides; absent levels are not carried forward. Empty, crossed,
locked, duplicate-price, off-lot, invalid-price and oversized books fail closed.
The feed promises neither a sequence nor checksum; no delta or exact-event-flow
capability is advertised. Central `event_ofi` eligibility therefore rejects it.
Snapshot-difference analytics must remain separately labeled estimates.

Explicit fast selection is `depth=5, aggregation=(("fast", "true"),)`. Depth may
also be omitted with `fast=true`, in which case reconstruction resolves it to
five while retaining the omitted-depth physical key. Fast book coverage is five
levels, with unknown cadence rather than an invented SLA. A five-level depth
without explicit fast selection is rejected. Info has no documented fast option,
so fast5 has **no REST fallback**, rather than silently
substituting slow20. Aggregation optionally accepts `nSigFigs` 2–5 and `mantissa`
1, 2 or 5 only with `nSigFigs=5`; both are preserved in the subscription and
coverage. Different source/depth/aggregation selections remain different keys.

The reducer uses core checkpoint/restore, source-change invalidation, explicit
connection-epoch reset and segment boundaries. Its reset hook restores coverage
configuration. Supervision owns connection recovery and emits gaps/resets; the
stateless decoder does not infer continuity from exchange wall time. REST books
must be wrapped as `rest_snapshot` and retain the variable-price validator.
Raw receive provenance is attached by shared `wrap_decoded`. `time` is the
exchange snapshot time, not a sequence, receive timestamp or scheduling clock.
Acknowledgements, pong and rejection controls cannot establish book health.
Book subscription acknowledgements compare effective options: omitted
`nSigFigs`/`mantissa` are equivalent to their nullable server echo, and omitted
`fast` is equivalent to `false`. Coin, channel and any requested nondefault
aggregation must still match; malformed option types and unknown fields fail.
These semantic defaults do not alter the original subscription key or raw frame.

## Trades and reconnect deduplication

Public trade `side="A"` means taker sell, `"B"` means taker buy; other nonempty
side strings remain `unknown`. `side_provenance` records the explicit node-provider
semantic source and original side string. The official WebSocket types establish
wire fields and specify the unique scope `(block_time, coin, tid)`; the Dwellir
node-provider documentation supplies the otherwise ambiguous taker-side mapping.
No private user-fill direction heuristic or buyer/seller-wallet guess is used.
Sizes are base units and must be exact multiples of the instrument's metadata
lot size; no rounding or cross-instrument precision assumption is applied.
Zero transaction hashes do not suppress valid executions.

`dedup_key` is the compact JSON tuple `[time, native_coin, tid]`, not `tid` alone.
Raw frames and normalized duplicates are intentionally retained. A consuming
analytics/tape scope must deduplicate those keys across connection epochs for the
same venue/API/instrument; reconnect is not a new trade identity. Replay rewind
must reset/rebuild that consumer's dedup state deterministically. Trade IDs can
repeat at another time or for another coin. The public trade array carries no
reliable subscription-history marker, so the adapter does not fabricate one or
claim historical completeness. Supervisor gap/segment anchors identify missing
history; deduplication does not fill a disconnect gap.

## Non-executable references and context

`allMids` emits only a labeled reference for the selected exact native coin. The
venue may substitute the last trade when a book is empty; it is never a bid or
ask and does not make a reducer executable or synchronized. No exchange timestamp
is invented for untimed references or context.
Reference prices use the corrected instrument quote currency. Context mark/oracle
prices inherit that same denomination through the envelope's immutable instrument
metadata version; neither values nor funding rates undergo a stablecoin conversion.

Perpetual `activeAssetCtx` and Info contexts retain mark, oracle, signed funding
rate with the actual **one-hour interval**, and open interest in **base units**.
The raw `funding` value is preserved unchanged: no division/multiplication by
eight and no annualization. The official formula is described on an eight-hour
basis, with hourly payments at one eighth of that computed rate. The Info context
example's `0.0000125` matches the funding documentation's 0.00125% hourly base
interest. The normalized interval describes payment frequency, not an eight-hour
formula or an annualized percentage.
Info context arrays are matched to their response's metadata universe by native
symbol, with equal-length and unique-match validation, not a cached positional
assumption. Context prices are exact reference values, not executable limit
prices, and are not subjected to book tick constraints. Spot context is distinct:
mark only in the current normalized model, without fabricated oracle, funding or
open interest; perpetual-only fields on a spot context are rejected. Capabilities
reflect this distinction. Neither context nor references mutate the book.

### Observed OI/funding history and financial gates

`PositioningHistory` retains admitted perpetual context observations in bounded
one-minute bars (up to 10,080 observed minutes per stream), aggregated onto the
same receive-clock UTC axis and interval boundaries as session candles. Among
the implemented adapters, Hyperliquid supplies this context channel; there is no
venue OI/funding-history backfill. Missing minutes and fields remain missing,
not zero or carried-forward values. A time-closed bar is not proof of complete
coverage, and replay exposes only the admitted prefix, never future context.
An incomparable source clock or changed OI unit clears incompatible history.

Series retain their actual venue, instrument, quote, funding interval and OI
unit. A same-canonical-base perpetual can be displayed as
`related_underlying`, never relabeled as the selected venue/product; wrapped
spot assets do not become canonical underlying assets through ticker similarity.
OI units are adapter-declared, not independently verified. This history is
venue-reported positioning, not account positions, executable liquidity or a
funding-arbitrage strategy.

`quality.evaluate_context` qualifies context independently of books and trades,
using validated receive-clock evidence and an explicit age policy. A fresh mark
or funding observation cannot establish usable depth; slow20/fast5 cadence is
not a freshness guarantee. Financial consumers additionally require exact
session-pinned verified metadata and supported contract valuation. Default
perpetuals can support the isolated one-way paper IOC model with explicit risk,
funding and collateral assumptions; metadata/context availability does not supply
those assumptions or exchange execution permission. HIP-3's unknown denomination
remains unsupported. See [financial models](COSTS.md) for the model and
exclusions.

## Sources and verification boundary

- [Official subscriptions and wire types](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [Official Info endpoint, coin names and UBTC remapping](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Perpetual metadata and contexts](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
- [Spot metadata and contexts](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot)
- [Asset ID arithmetic](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids)
- [Variable tick and lot rule](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size)
- [Dwellir public trade semantic documentation](https://www.dwellir.com/docs/hyperliquid/trades)
- [Funding payments](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
- [Default contract denominations, quanto settlement and order maxima](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications)
- [HIP-3 deployer-defined contracts and collateral](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-3-builder-deployed-perpetuals)
- [Order rejection minimums, including spot quote-token units](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/error-responses)

`tests/test_workstation_hyperliquid.py` provides offline identity, collateral/DEX,
USDT versus USDC denomination, quanto provenance and metadata/event wire identity,
unsupported builder denomination, sourced size/notional units, precision,
depth/aggregation, snapshot replacement, malformed/wrong-coin, trade-side/dedup-key,
context/reference, checkpoint/reset and exact-flow-gate contracts.
`tests/test_workstation_positioning.py` covers sparse same-axis aggregation,
related-underlying disclosure, clock/unit transitions and replay-prefix history.
These are offline contracts, not live-access evidence. The historical
[2026-11-01 HTTP snapshot](evidence/m0-hyperliquid-access.json) measured endpoint
access only, before adapter integrity validation; its successful recent-trade
HTTP observations do not create an implemented REST trade fallback here.
No external live revalidation was performed for this documentation audit.
