# Public-market analytics

`workstation.analytics.AnalyticsEngine` is the single bounded analytics observer used by live processed-message delivery and M2 replay. It owns no connection, collector lock, storage, or execution capability. Its inputs are immutable `StreamSpec`s, a validated `AnalyticsConfig`, and complete `RecordedBatch` deliveries with their post-message `Book` projections. Rejected/staged book input does not become a book observation merely because an envelope contains a `BookUpdate`.

## Interface and result contract

```python
engine = AnalyticsEngine(tuple_of_stream_specs, AnalyticsConfig())
engine.on_batch(recorded_batch, tuple_of_current_books, replay_clock)
snapshot = engine.snapshot(instrument_id, ClockReading(clock_id, mono_ns), key=book_key)
checkpoint = engine.checkpoint()
engine.restore(checkpoint)
engine.reset()
state_hash = engine.state_hash
```

Live attachment may call `engine.initialize_books(current_books)` on a pristine observer: subscribe to processed callbacks, synchronously obtain the registry baseline before yielding, install that baseline, then consume the bounded buffered real deliveries. The method ignores unconfigured keys, rejects duplicate configured keys or mismatched metadata, and preserves native book quality/as-of exactly. It changes neither delivery position nor trade state and invents no events, OFI samples, displayed persistence or prior-window anchors. Static quote/depth metrics become available subject to the retained native quality; historical metrics remain warming until a subsequent real accepted book update establishes an observed anchor. Heartbeats alone cannot establish that history. A non-pristine observer rejects initialization; `reset()` permits a new baseline.

`key` is optional only when the instrument has one book reconstruction key. Multiple depths/aggregations never silently mix: omission returns `ambiguous_book_key`. Unknown instruments raise `ValueError`. Multiple trade stream identities for an instrument are likewise unavailable with `ambiguous_trade_stream`, rather than double-counted.

Every `MetricResult` carries name, exact `Decimal` value or `None`, unit, instrument and venue identity, source `EventEnvelopeRef` (or `None` before evidence), `MetricWindow`, native `Quality` or separate `TradeEvidence`, centralized `Eligibility` with reasons, formula version, configuration hash, and bounded supporting references. The `reasons` Python property exposes `eligibility.reasons`; JSON dataclass consumers read the latter. A result with failed eligibility never carries a numeric value. Empty/unavailable is not zero. `AnalyticsSnapshot` also contains labeled public tape, executed profile rows, timestamped displayed evidence and summary exclusions. Profile eligibility is the `executed_profile_base` metric; displayed rows are supporting observations, not an independent eligibility assertion.

All monetary arithmetic uses the shared precision-80, half-even context. Derived wire/checkpoint serialization explicitly opts into `DERIVED_DECIMAL_POLICY`; native `Instrument`, `Level`, `Trade`, `Quality` and capture field shapes retain their stricter existing validation. Economic values and integer timestamps cross JSON as strings. `reference_multiplier` is never a native token/contract multiplier. Base quantities use explicit instrument contract valuation: non-inverse base-denominated quantity times the native contract multiplier; explicitly specified linear base-value contracts; or explicitly specified inverse quote-value contracts divided by price. For inverse contracts, quote notional is directly native quantity times contract multiplier, never a rounded base quotient multiplied back by price. Unknown contract valuation returns unavailable, never a guessed spot conversion. These descriptive quantities do not certify financial order eligibility or perpetual margin/funding support.

## Quotes and depth

For best bid `b`, best ask `a`, and displayed quantities `qb`, `qa`:

- `best_bid = b`, `best_ask = a`.
- `mid = (b + a) / 2`.
- `spread_native = a - b`; `spread_bps = 10000 * (a - b) / mid`.
- `signed_imbalance = (qb - qa) / (qb + qa)`.
- `bid_share = qb / (qb + qa)`; this is separately named, not signed imbalance.
- `weighted_mid = (a * qb + b * qa) / (qb + qa)`.

The weighted mid is **not a calibrated Stoikov micro-price**. Zero denominators are unavailable. Price units identify exact native quote/base assets; no USD/USDT/USDC parity assumption exists.

Each configured depth band (default 5/10/25/50 bps) is measured around the midpoint, including exact boundary levels. Bid base depth sums quantities with `price >= mid*(1-band/10000)`; ask base depth sums quantities with `price <= mid*(1+band/10000)`. Quote depth sums each included level's native quote valuation: price times valued base for linear/base quantities, direct contract face value for inverse quantities.

Full-band coverage requires a genuinely full-depth source **or** an actual retained outer level reaching/passing that side's band boundary. Merely having N subscribed levels or one quote does not prove coverage. An incomplete band returns `incomplete_depth_band`, not the partial sum masquerading as complete depth. BBO itself can remain eligible while a wider band is unavailable.

## Best-quote flow

For successive complete valid continuous messages, the event contribution is the Cont/Kukanov/Stoikov best-quote indicator equation:

```text
e = 1[b >= b_prev] * qb - 1[b <= b_prev] * qb_prev
    - 1[a <= a_prev] * qa + 1[a >= a_prev] * qa_prev
OFI(start,end] = sum(e for messages in (start,end])
```

At an unchanged quote both indicators apply, yielding the signed size difference. A falling bid contributes **minus the old bid quantity**. The engine samples once per accepted source-message book revision, not once per level. Unit is native displayed quantity, explicitly marked `base:<instrument>` or `contracts:<instrument>`. It does not silently pretend contracts are base.

`event_ofi` requires synchronized delta input, `exact_event_flow`, an unchanged continuity anchor and a fully retained window. A snapshot transition inside the window invalidates event OFI. Snapshot-capable sources instead expose `snapshot_best_quote_flow` with the same best-quote differences explicitly labeled sampled, not event-complete. Source/segment/epoch/clock changes, gaps and invalid quality reset book anchors; heartbeats do not fabricate books. Feed-message completeness does not imply matching-engine completeness.

When a reset, source change, rejection, disconnect or scoped gap removes a book from the post-message projection, its prior analytical book, anchors and displayed evidence are discarded even though no replacement `Book` exists. Old quotes cannot survive that absence; independently healthy trade analytics remain separate.

Reference: [Cont, Kukanov & Stoikov, *The Price Impact of Order Book Events*, section 2.1](https://arxiv.org/html/1011.6402v3), [published article](https://academic.oup.com/jfec/article/12/1/47/816163). These contemporaneous descriptive quantities are not evidence of future predictive edge. The paper's section 3.2 warning about price-changing events applies; a future predictive experiment must place targets strictly after feature cutoff.

## Trades, clocks and continuity

Only normalized public `Trade` payloads contribute executed flow. Tape preserves native trade ID/dedup key, aggressor side and provenance, price/quantity, original source reference and subscription-history flag. Unknown aggressor stays unknown. Neither book depletion nor transport traffic creates executions.

The default rolling window is source **receive-monotonic time `(now-window, now]`**, not exchange event time and not process replay time. Default width is 60 seconds. The first non-history validated public trade establishes the trade anchor. Subscription history remains visible on tape but does not establish newly executed flow, contribute to rolling notional/CVD/profile, or receive a fabricated exchange timestamp.

- `trade_buy_notional`, `trade_sell_notional`, `trade_unknown_notional` sum retained native quote notional separately, using the exact contract valuation above.
- `cvd_base` is rolling known-aggressor buy base minus sell base; `cvd_quote` is corresponding quote notional difference. Neither is lifetime CVD or an account position. Unknown flow is reported separately, not assigned a direction.
- `observed_trade_{buy,sell,unknown}_notional` and `observed_cvd_base` expose positive observed-window evidence during initial warmup. Their window definition explicitly says `not_complete_history`; they still reject gaps, stale evidence, overflow and unsupported valuation. A zero side total here means no such side among the **observed prints**, not a complete-channel assertion.
- Executed profile aggregates exact native execution prices into buy/sell/unknown base and total quote notional. It is an **observed** profile governed by `executed_profile_base`, not an inferred complete venue volume profile. No bin rounding is hidden.

Book and trade state/anchors are independent. A book gap cannot erase an independently healthy trade CVD. A trade gap/reset/disconnect/source change clears its analytical history and rewarms its anchor. A scoped gap can invalidate every explicitly affected trade stream.

`TradeEvidence` and `evaluate_trades` extend the centralized quality policy without adding fields to native `Quality`. Discovery access `unknown` is resolved only by validated evidence for the exact instrument and metadata version; explicit `blocked`/`unsupported` remains blocked. Clock mismatch/regression, stale public evidence and missing anchors are explicit exclusions.

An observed interval exceeding the eligibility freshness bound expires the continuity anchor and clears pre-expiry tape/dedup history. A later trade starts a new anchor rather than retroactively filling the silent interval; a heartbeat cannot re-establish an expired anchor. Same-stream receive-clock regression or a quantity-unit mismatch invalidates and clears the trade history. After a mismatch the next valid trade must establish a new anchor and warm a full window; observed metrics/profile include only post-reset executions.

Quiet intervals deserve special care. Generic websocket pong, Hyperliquid pong, subscription ACK, supervisor clock tick and Coinbase transport heartbeat do **not** refresh analytical trade continuity. Only a non-history validated trade or an explicitly channel-validated `Control('heartbeat', 'trade_channel_healthy')` can do so, and the latter cannot establish an initial anchor. Current production adapters do not emit that special tag; this observer does not infer it from transport. Without such evidence a wholly quiet rolling interval is `quiet_trade_interval_unproven`, not a made-up zero. A dedicated connection can remain operational under supervisor liveness policy without certifying analytical absence of trades. Even fresh public-channel evidence does not promise exchange matching-engine completeness.

Dedup keys are retained with bounded tape history. Existing production normalization also deduplicates public prints before delivery; its duplicate ACKs never enter flow. Observer-level dedup is an extra bounded guard, not a claim of unlimited lifetime duplicate detection.

Capacity eviction of excluded subscription-history prints bounds presentation but does not advance the analytical overflow frontier. Eviction of a contributing non-history execution still invalidates the affected flow window, including observed diagnostics, until that execution falls outside the window.

## Displayed evidence and heuristics

The engine tracks up to 100 displayed levels per side by default, with source timestamps and supporting prior/current references. `displayed_persistence` sums currently tracked native quantity at prices observed continuously for at least `persistence_ns` (default 5 seconds), measured between actual observations, not extended merely because wall time passed. Quantity changes do not reset persistence of the displayed **price level**; no order identity or queue priority is claimed.

`displayed_depletion` and `displayed_replenishment` sum observed decreases/increases at tracked prices over the rolling window. A price falling outside known retained depth or the tracking cap is not fabricated depletion. A newly tracked price establishes an anchor rather than fabricated historical replenishment. These are displayed quantities, not trade classifications.

`absorption_evidence_heuristic` sums `min(displayed_replenishment, matching aggressive executed base)` where public executions match price, resting side and the receive-time observation interval, and exceed `evidence_min_base` (default 1e-8). `pulling_evidence_heuristic` sums `max(0, displayed_depletion - matching aggressive executed base)`. Both require complete retained book/trade analytical windows and eligible source quality; native base quantity with multiplier one is required for these particular formulas. They retain the public coincidence formula identifier and bounded references.

Heuristic `MetricResult.quality` and `references` retain book quality and the relevant replenishment/depletion observations. Supplemental `trade_evidence`, `trade_eligibility`, and `trade_references` expose the independent full trade-window gate and matched public executions. Both reference collections are separately bounded by `max_references`; a matched print remains referenced even if presentation `tape_limit` hides it. If no trade matched, the latest validated trade-channel source reference supports the trade gate instead. Bounded references are supporting samples, not a claim to embed the entire capture.

These are deliberately conservative **heuristics**, not spoofing intent, trader identity, hidden iceberg, cancellation truth, fill probability or queue-position inference. Receive times do not prove exchange causality; public feeds can aggregate/reorder evidence. No predictive or execution-readiness claim follows from a positive heuristic.

## Bounds, replay and limitations

Defaults: 300-second retention, 20,000 trades, 20,000 book samples, 20,000 displayed changes per stream/key; 100 tape rows per snapshot; at most 32 primary supporting references per metric plus at most 32 supplemental trade references for a heuristic. Profiles have at most one row per retained trade price. State is bounded by both retained time and counts. Evicting required history records an invalidity frontier until the discarded observation lies outside the requested rolling window. Overflow cannot silently turn a partial window into an eligible result. Presentation tape truncation alone does not imply engine history loss.

Book-window caches retain only immutable book-derived band totals and frozen standing evidence. A new book object invalidates depth totals; book observations invalidate standing evidence; reset/restore discards both caches. Every query still evaluates source clocks, freshness, quality, rolling windows and independent trade evidence. Caching neither extends continuity nor changes formulas or canonical checkpoints.

`checkpoint()` serializes every future-affecting analytical state, prior book, samples, displayed anchors, trade dedup/tape, continuity/source identity, overflow frontier, delivery position and configuration/stream hashes. `restore()` checks versions/configuration/stream identity and capacity bounds. `state_hash` is SHA-256 of the canonical explicit-derived-policy checkpoint. Playback speed and process wall time never enter those economics. M2 observer seek resets/recomputes from the source prefix; no future windows survive backward seek. Snapshot queries before current source evidence return clock-regression/unavailable results and filter future tape/profile entries.

The permanent consumer suite is `tests/test_workstation_analytics.py`: indicator directions, exact quote/depth boundaries, quality exclusions, trade-only and history behavior, independent gaps, overflow and retention, native serialization contract, derived precision roundtrip, and actual M2 demo raw/normalized replay with speed/seek/checkpoint comparisons. Run it through the parent union verification; implementation work does not itself claim execution results.
