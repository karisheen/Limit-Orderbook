# Known-depth costs and venue comparisons

`src/workstation/costs.py` is the shared headless engine consumed by the terminal runtime. It does not submit orders, read accounts, transfer funds, simulate queue priority, or guarantee fills. Synthetic numerical fixtures live in `tests/test_workstation_costs.py`; execution evidence belongs in the milestone handoff, not in this document.

## Public interface

```python
walk_book(book, instrument, side, *,
          base_quantity=None, quote_budget=None,
          fees: FeeSchedule, policy: EligibilityPolicy, now: ClockReading,
          order_type="market", limit_price=None) -> ExecutionEstimate

compare_pair(buy_book, buy_instrument, sell_book, sell_instrument, *,
             base_quantity, buy_fees, sell_fees, policy, now,
             numeraire=None, fx_rates=(), inventory=(),
             stress=StressAssumptions()) -> PairEstimate

compare_matrix(entries: tuple[ComparisonEntry, ...], *,
               base_quantity, policy, now, numeraire=None,
               fx_rates=(), inventory=(),
               stress=StressAssumptions()) -> ComparisonMatrix

best_observed(entries, *, now, policy) -> tuple[BestObserved, ...]

convert_fx(amount, source_asset_id, target_asset_id, *, rates, now, policy,
           required_input=False, reference_mono_ns=None) -> FXConversion
```

All economic inputs and results are Decimal, not floats. Money arithmetic uses `DECIMAL_CONTEXT` (`decimal-v1-p80-half-even`); result models validate `DERIVED_DECIMAL_POLICY`. Use `dumps/loads(..., decimal_policy=DERIVED_DECIMAL_POLICY)` for derived wire round trips. Nested native Instrument/Book/Level and fee/rate/balance inputs retain their stricter native bounds. DTOs retain exact strings; economic values must not make a round trip through JavaScript Number.

All public models are frozen dataclasses with tuple containers. Their complete typed fields are in `costs.py`. Core walk/pair helpers and cohort containers retain concrete domain types; optional price, fee, and FX amounts are narrowed before accounting, never replaced with zero to satisfy typing. The formula version is `costs-v1-p80`.

### Fee, conversion, inventory, and scenario inputs

- `FeeSchedule(rate=None, asset_id=None, provenance="unknown", asof_wall_ns=None, assumed=True)`: rate is a fraction, not bps. A known rate, including zero, requires a non-unknown provenance string and a date. `asset_id=None` means the instrument's native quote asset. Known rates must be nonnegative and below one; negative rebate schedules are not silently accepted as taker costs. Unknown fees retain gross diagnostics but make fee/effective/net values unavailable. A future fee date relative to supplied wall time is rejected as evidence, not used. There is no fabricated default exchange fee tier.
- `FXRate(base_asset_id, quote_asset_id, bid, ask, asof: ClockReading, source, conversion_fee_rate=None, assumed=False)`: an identified, two-sided, noncrossed conversion quote and an explicit conversion-fee assumption. Omitted conversion fees remain unknown and make conversion unavailable; pass an explicit Decimal zero for an assumed free conversion. Currency IDs, not ticker resemblance, determine orientation. The fee is charged as a fraction of converted output. Depth/capacity is not supplied by this model and remains an explicit capacity assumption.
- `InventoryBalance(venue, asset_id, available, provenance="user supplied prefunded balance", asof=None)`: available funds on that exact venue. A dated balance must pass the supplied clock/freshness policy. An undated user balance is an assumption, not a private-account observation. Duplicate balances for one venue/asset fail. Once a venue has a supplied inventory record, its omitted required assets mean zero; a wholly absent venue remains unknown.
- `StressAssumptions(latency_bps=0, rebalance_cost=0, provenance="user assumption")`: adverse movement bps applies to **each leg's** observed notional. Rebalance is an explicit amount in the selected common numeraire. Zero scenario inputs are disclosed assumptions, not measured zero market risk.

## Single-leg depth walk

Exactly one nonnegative base quantity or native-quote budget is required. Zero is valid and returns no fills, zero summed notional and known zero fees, but no VWAP/effective price. Buy walks asks ascending; sell walks bids descending.

A quote budget caps **gross native quote notional, excluding fees**. For sells it caps gross proceeds; it is not a cash-spending request. The result explicitly labels this convention. Pair inventory feasibility, unlike this gross-budget control, includes known quote fees in the cash cap.

Native lot increments are rounded down, never up. Native `min_base`, generic `max_base`, and the selected `max_market_base`/`max_limit_base` constrain the native order quantity; for contract instruments these metadata quantities are contract counts. Maxima intersect rather than overwrite one another. Quote-notional constraints require an explicit matching constraint currency. A known maximum may reduce a fill and leaves an explicit remainder; a resulting fill below a minimum is unavailable as a proposed executable order. Unknown constraints remain unknown, not invented zero bounds. An unsupported size rule fails explicitly.

Marketable-limit estimates require `order_type="limit"` and `limit_price`; market estimates reject an unrelated limit price. Native prices use existing instrument validation, including the existing Hyperliquid significant-digit validator. No separate price-rule implementation is introduced. A limit stops at the first nonmarketable known price.

Quantity valuation:

| Native convention | Base quantity | Native quote notional |
| --- | --- | --- |
| `quantity_unit="base"`, unit contract multiplier | native quantity | native quantity × price |
| Linear contracts, value currency = native base | contracts × contract multiplier | base quantity × price |
| Inverse contracts, value currency = native quote | contracts × contract multiplier / price | contracts × contract multiplier |

Unknown value currency, inverse base-unit quantities, and nonunit contract multipliers with already-base quantities are explicit ambiguities, not guessed conversions. `reference_multiplier` is never used in any of these formulas: a token/reference-stock ratio does not multiply a native token order.

`ExecutionEstimate` retains the request, filled/unfilled base and applicable native quantities, gross quote budget remainder, summed `notional_quote`, VWAP, best price, slippage amount/bps, fee amount/asset/quote-equivalent, effective price, exact consumed levels, source `asof`, native coverage, centralized eligibility, reasons, assumptions, and derivative settlement-notional evidence. Inverse requested native counts are absent because the requested base quantity has no single price-independent contract count. Budget requests do not invent an unfilled base amount.

The summed per-level notional is the accounting input. It is never reconstructed as rounded VWAP times quantity. VWAP is `summed_notional / filled_base`. Adverse slippage is actual notional minus best-price reference for buys, and the reverse for sells. Quote-fee effective price is `(notional ± fee) / base`. Base-fee effective price is `notional / (base ∓ fee)`, using reduced acquired base on buy and increased disposed base on sell. Third-asset fees require a separate valuation and are unavailable, not zero.

Partial known-depth results are explicit and never extrapolate. `coverage.full_depth` remains the source's coverage claim, not an assertion that a requested order can fill. Stale, invalid, unsynchronized, blocked, or wrong-clock books do not produce diagnostic simulated fills: `quality.evaluate(..., "depth_walk", ...)` is the shared gate.

For derivatives, notional is exposure, **not principal cash movement or a margin calculation**. Known inverse base settlement and linear quote settlement retain their units. Hyperliquid `quanto_no_fx` preserves the contract's settlement denomination; this is not an executable USDT/USDC conversion. An unknown settlement conversion remains unknown.

## Same-size pair accounting

Each leg first computes its feasible known-depth/order-constraint/inventory size. The engine then finds the largest shared native-lot-feasible base size and rewalks **both** books at that same size. It does not take VWAPs from unrelated sizes or merely choose the smaller of two differently rounded quantities.

The discrete lot solver intersects piecewise native lot grids using exact integer congruences. Inverse grids require rational lot indices: three contracts of quote value one at price three have base quantity one, not three times a rounded one-third. Rational values exist only inside discrete native-lot selection; both final legs, notionals, FX, fees, P&L components, and wire results use the declared Decimal context. The engine checks exact agreement of the final Decimal base fills, never an epsilon. An unrepresentable shared final amount is explicitly unavailable (`no_exact_common_quantity`), never a mismatched-quantity edge.

Ordinary spot/base and linear-contract pairs use a direct finite scaled-integer common-lot path, not rational book machinery. The inverse solver is bounded to 256 consumed levels per leg and 4,096-bit numerators, denominators, and common-grid intermediates. Exceeding a bound returns `lot_grid_complexity_limit` with no purported feasible execution; it never silently switches to approximate quantities.

The result exposes requested, feasible, and unfilled base quantities, both complete leg estimates, original size/constraint `feasibility_reasons`, the common numeraire, scenario provenance, FX conversions, inventory evidence, eligibility, and these components:

1. `gross_reference`: shared size times the difference between sell best bid and buy best ask, both valued at identified FX **mid reference** rates.
2. `depth_slippage`: both adverse depth costs in that same mid-reference numeraire.
3. `buy_fee`, `sell_fee`: separately valued known fees, including their leg schedules/provenance/date.
4. `fx_cost`: actual side-oriented funding/proceeds costs relative to that reference, including supplied conversion fees. This is not an additional VWAP slippage charge.
5. `rebalance_cost`: user scenario amount in the numeraire.
6. `latency_stress`: `(buy_mid_notional + sell_mid_notional) × latency_bps / 10000`.
7. `net`: actual converted sell proceeds after fees minus required buy funding after fees, minus rebalance and latency stress. `net_bps` divides net by actual required buy funding.

Thus, subject only to declared p80 division rounding:

```text
net = gross_reference - depth_slippage - buy_fee - sell_fee
      - fx_cost - rebalance_cost - latency_stress
```

The net ledger calculation uses accumulated actual notional and actual required-input/proceeds conversions, not reconstructed VWAP cash. Slippage is charged once. Base-asset fees are exposed on each leg but make cash-only pair net unavailable until their residual base exposure has an explicit rebalance conversion; a VWAP mark is not an actual rebalance. Unknown fees likewise leave net unavailable, even when gross reference values can be shown.

Both books must pass centralized comparison eligibility, identity, freshness and receive-as-of-skew gates. Spot pairs require identical native base identity and market type; differing quote currencies require explicit FX. Wrapped/reference assets do not merge. Derivative pairs additionally require matching economic/settlement contracts and expiry. Spot/perpetual is not an executable comparison cohort. Derivative quote-price differences are labeled opening-price/notional comparisons, not realized settled P&L or a collateral/margin model. Quanto pair net remains unavailable without a settlement-P&L model; the quanto rule never supplies missing market FX.

`eligible` means a conditional comparable financial estimate is available. `execution_eligible` additionally requires supplied prefunded balances, available execution access, and spot instruments; it still does not certify actual execution or atomicity. Public feed accessibility is not account trading permission.

## FX orientation, timing, and depegs

No implicit USD=USDT=USDC equivalence exists. Exact identity conversions need no rate; distinct assets require one unambiguous direct supplied rate. Missing, stale, future, wrong-clock, or book-skewed FX is unavailable with reasons. Multiple candidate direct rates require caller selection rather than silent source preference. Multi-hop routes are not inferred.

For FX base X / quote Y with bid `b`, ask `a`, output fee fraction `f`:

| Action | Formula |
| --- | --- |
| Sell input X to receive Y | `X × b × (1-f)` |
| Spend input Y to receive X | `Y / a × (1-f)` |
| Required input Y to fund exact target X | `X × a / (1-f)` |
| Required input X to fund exact target Y | `Y / b / (1-f)` |

`convert_fx(..., required_input=True)` interprets its amount in **target currency**, returns required source amount, and reports conversion cost in source currency. Proceeds mode interprets the amount in source currency and reports cost in target currency. Pair buy funding uses required-input mode; sell proceeds uses proceeds mode. A 0.90/0.91 USDT/USD book means 100 USDT costs 91 USD to fund, not 90 USD and not 100 USD. Mid is only the decomposition reference.

For spot pairs, inventory remains prefunded on the exact trading venue, in its required native quote/base assets. Supplying USD on a different venue does not fund a USDT order, even when a conversion rate is supplied. The rate does not establish a venue-local executable FX market or authorize instant transfers. Unknown spot inventory is prominently informational/assumed-prefunded. These spot principal/base caps **do not apply to derivatives**: supplied settlement collateral remains evidence on the result but does not determine contract size without an explicit margin model. Derivative comparisons retain `derivative_collateral_margin_unavailable` and remain non-execution-eligible whether or not balances are supplied. In particular, USDC collateral does not become a USDT principal requirement or spot BTC inventory for a Hyperliquid perpetual. Non-atomic leg risk, no-market-impact limitations, and rebalance/latency assumptions remain visible on every pair.

## Best-observed cohorts and matrix

`best_observed` groups exact native quote/market/contract/settlement cohorts. It first removes centrally ineligible books, then aligns surviving source receive-as-of times to the newest eligible member within the configured skew. Excluded members retain reasons. Duplicate instrument IDs with multiple book keys are ambiguous; no depth/aggregation selection is silently made. The output is **best observed comparable bid/ask**, never a consolidated or protected NBBO. Different stablecoin quotes remain separate cohorts even when a converted pair view is requested.

`compare_matrix` returns every directed N×N pair, including explicit unavailable diagonal/same-venue cells and incompatible/stale/FX-missing cells. All cells use the same requested size and shared engine. No client-side financial implementation or fake fallback is necessary.

## Parent verification commands

The implementation task intentionally does not execute gates. The integration owner should run:

```sh
python -m pytest tests/test_workstation_costs.py
python -m pytest tests/test_workstation_api*.py
```

Run the repository's union formatter/linter/type/test gates and actual runtime/browser size/fee/cohort workflows after all slices land. Numerical fixtures cover sides, summed cash, partial/zero/budget/lot/min/max/limit orders, fee provenance/unknown/base fees, exact linear/inverse contracts, recurring inverse lot endpoints, shared quantity, inventory/cash-with-fees, age/skew/clock, FX funding orientation/conversion fees/depeg, full matrix exclusions, spot/perpetual/quanto boundaries, derived precision and wire round trips.
