# Strategy research backlog

Saved hypotheses for future experiments with the current workstation. These are research plans, not validated trading strategies or recommendations. No experiments were launched as part of saving this document; no profitability or readiness claim is implied.

## Scope and priorities

Start with one liquid spot instrument on one venue, using recorded public market data. Evaluate the three built-in families independently before combining signals. This is a proposed experiment scope, not the boundary of the paper engine: the current offline research engine measures independent, unfunded directional outcomes, while paper execution supports spot and supported isolated one-way perpetual market IOC and marketable limit IOC. Neither implements a live strategy runner or validates profitability.

| ID | Experiment | Status | Current support |
| --- | --- | --- | --- |
| S1 | Depth imbalance → short-term direction | Planned; no results recorded here | Built-in `depth_band_imbalance` family |
| S2 | Order-flow imbalance → continuation | Planned; no results recorded here | Built-in `event_ofi` family; requires eligible continuous data |
| S3 | Executed trade flow → continuation | Planned; no results recorded here | Built-in `executed_trade_flow` family; requires usable aggressor evidence |
| S4 | Absorption → reversal | Planned; no results recorded here | Manual replay first; combined rule/evaluator needed |
| S5 | Completed hourly trend + short-term flow | Planned; no results recorded here | Context inspection available; causal combined evaluator needed |
| S6 | Cost-aware execution filter | Planned; no results recorded here | Cost estimates and paper accounting available; policy/evaluator needed |

Recommended order: S1–S3 on comparable recordings, then S6 against a promising baseline, then S4/S5 if the evidence justifies the extra implementation. Unavailable metrics or insufficient evidence are valid outcomes, not reasons to substitute zeros or weaken eligibility rules.

### Implemented accounting versus strategy research

Paper supports explicit leverage, isolated collateral, settlement-denominated fees, funding and native realized/unrealized P&L for verified, pinned perpetual metadata with supported economics: quote-settled linear contracts, base-settled inverse quote-face contracts, and recognized official default-namespace Hyperliquid linear `quanto_no_fx` contracts. This is not arbitrary derivatives support: futures, unknown settlement conversion and unsupported/builder contracts are rejected. Native order quantities are base units or contracts according to the pinned descriptor; reference-asset multipliers never resize native orders. Spot has no borrowing or short-opening facility.

Perpetual risk and funding are dated user models, not inferred exchange risk tiers or imported context rates. Funding must explicitly be `none`, `periodic`, or a finite `events` schedule; missing funding is unavailable, not zero. Marks use eligible executable-side book proxies (long bid/short ask), not exchange mark prices. Protective liquidation is modeled reduce-only IOC against remaining observed depth, with trading fees and a separate modeled penalty. It may remain partial/unresolved or leave isolated debt; it is not a guarantee of complete closure, insurance-fund recovery or real exchange liquidation behavior. Cross-margin, passive fills, market impact and exchange order submission remain unsupported. See [the native paper contract](CONTRACTS.md#supported-perpetual-economics-and-isolated-accounting) for exact formulas and bounded failure behavior.

The offline research runner does not drive that funded ledger: its observations are independent taker markouts without sequential shared inventory, collateral, funding or portfolio simulation. A bearish research outcome does not establish borrow or margin access; the paper engine's ability to model a perpetual short does not retrofit those economics into a research trial. A strategy requires its own causal entry/exit/sizing/exposure rules and evaluator. Neither funding-arbitrage nor automated market-making strategies are implemented by these accounting primitives.

## S1 — Depth imbalance → short-term direction

**Hypothesis:** A larger share of displayed bid liquidity near the midpoint precedes upward price movement, with the reverse relationship for ask liquidity.

**Test plan:**
- Use `depth_band_imbalance`, keeping the price band fixed rather than comparing arbitrary row counts.
- Start with the existing threshold grid and 1, 5, 30, and 60 second horizons. Treat these as exploratory settings, not proven optimal values.
- Record band coverage, eligible observations, exclusions, and outcomes by threshold and horizon.
- Compare signed outcomes with unconditional price drift over the same periods; disclose any additional analysis needed beyond the built-in report.

**Failure modes:** Displayed liquidity disappears, depth coverage is incomplete, or a directional relationship is smaller than execution costs.

**Advance only if:** Held-out results remain useful with sufficient support and credible fee, depth-cost, and latency assumptions across multiple recordings.

## S2 — Order-flow imbalance → continuation

**Hypothesis:** Changes in best bid/ask prices and displayed quantities provide directional information beyond a static imbalance snapshot.

**Test plan:**
- Use `event_ofi` only where source continuity and metric eligibility support it. Snapshot best-quote flow is a different metric, not a replacement.
- Evaluate the existing thresholds and horizons; inspect gap/reset exclusions.
- Compare with S1 on comparable eligible observations. If the built-in reports do not expose a matched sample, perform an explicit supplementary analysis rather than comparing unmatched averages as though equivalent.

**Failure modes:** Feed gaps, source-specific aggregation, or a reaction that occurs before a practical entry could arrive.

**Advance only if:** The relationship survives held-out evaluation and delayed execution assumptions; claims of improvement over S1 require a matched comparison.

## S3 — Executed trade flow → continuation

**Hypothesis:** Sustained aggressive buying or selling precedes further movement in the same direction.

**Test plan:**
- Use `executed_trade_flow` and retain the distinction between known and unknown aggressor sides.
- Compare short and longer horizons to identify continuation, decay, or reversal.
- Inspect representative successes and failures in Time & sales and footprint views without selecting only compelling examples.
- Keep the observation window and source clock explicit. Rolling CVD is not lifetime flow, a position, or net cash entering the asset.

**Failure modes:** Price already incorporates the trade burst, aggressor evidence is incomplete, or apparent gains disappear after entry delay and fees.

**Advance only if:** Useful outcomes remain after realistic execution assumptions and are not confined to contemporaneous price changes.

## S4 — Absorption → reversal

**Hypothesis:** Heavy aggressive selling with little downward price response and repeated displayed bid replenishment may precede an upward reversal; investigate the mirrored case separately.

**Test plan:**
- Begin with manual replay and journal observations at explicit decision cutoffs.
- Before systematic testing, define aggressive-flow strength, the observation window, maximum adverse price response, replenishment evidence, confirmation, entry timing, and invalidation.
- Implement a causal combined feature/evaluator before claiming an automated strategy result. Existing liquidity heuristics alone do not implement this strategy.
- Separate descriptive absorption evidence from the future reversal outcome.

**Failure modes:** Receive-time coincidence is mistaken for exchange causality; hidden liquidity or trader intent is inferred without evidence; examples are chosen after the reversal.

**Advance only if:** A frozen rule generalizes out of sample and supports actionable entries after costs. Do not label the result spoof detection or proof of a particular participant's intent.

## S5 — Completed hourly trend + short-term flow

**Hypothesis:** Short-term flow conditions are more useful when aligned with a slower price regime than when traded indiscriminately.

**Test plan:**
- Define the hourly regime using only completed candles available at the decision cutoff. Freeze the trend formula before evaluating held-out periods.
- Combine it with one separately evaluated family from S1–S3, not an unrestricted search over many signal combinations.
- Collect enough overlapping candle/order-flow history to cover warmup, multiple regimes, and chronological evaluation partitions.
- Compare the filtered policy with the same unfiltered baseline, reporting both outcomes and the reduction in opportunities.

**Implementation boundary:** The built-in runner does not automatically combine hourly context and order-flow signals. A causal joined evaluator is needed. Historical live candles do not reconstruct past books or trades; replay candles use retained observed trades and may be partial.

**Failure modes:** Using the eventual close of an unfinished candle, insufficient overlapping history, or a filter that merely selects a favorable market period.

**Advance only if:** The incremental benefit survives held-out testing and costs without relying on future candle information.

## S6 — Cost-aware execution filter

**Hypothesis:** Rejecting otherwise valid entries when spread, depth costs, or estimated slippage are unfavorable improves a defined policy's economic outcomes. This is an execution-policy experiment, not a standalone directional signal.

**Test plan:**
- Start from a frozen baseline signal and keep its candidate opportunities fixed.
- At each decision cutoff, estimate execution at a fixed size with explicit, dated fee assumptions and valid observed depth.
- Define rejection thresholds using training/validation evidence, then compare filtered and unfiltered held-out outcomes, opportunity counts, and exclusions.
- Stress latency, fees, slippage, partial fills, and inventory constraints. Use paper runs to inspect accounting and arrival behavior.
- For paired paper comparisons, use explicit runs/forks with equivalent stated funds and source positions. Do not reuse consumed liquidity as though it were replenished.

**Failure modes:** Assuming best-price execution for large orders, treating unknown fees as zero, ignoring missed opportunities, or confusing a depth estimate with guaranteed execution.

**Advance only if:** The benefit persists under disclosed stress assumptions. Any portfolio-return claim requires a sequential funded policy with explicit entry, exit, sizing, overlapping-signal, and exposure rules.

## Shared experiment protocol

1. **Freeze the question:** Record the experiment ID, instrument/venue, hypothesis, feature definitions, decision clock, candidate thresholds/horizons, and success/rejection criteria before inspecting held-out outcomes.
2. **Collect evidence:** Record multiple real sessions spanning quiet, directional, and volatile conditions. Synthetic data validates machinery, not trading edge. Live viewing alone does not record a session. Stop an existing network-enabled server before running a standalone collector.
3. **Preserve provenance:** Keep capture path/identity, configuration, source revision, fee source/date or explicit assumption, latency/slippage assumptions, and eligibility/exclusion reasons.
4. **Split chronologically:** Use train/validation/test separation with purging and embargo safeguards. Select settings using train/validation only. A test partition repeatedly consulted for tuning is no longer an untouched holdout.
5. **Report support and uncertainty:** Retain all attempted candidates, failed attempts, sample counts, exclusions, and per-session variation. Overlapping outcomes are dependent; do not treat their count as independent evidence. Any confidence analysis must respect that dependence.
6. **Review economics:** A favorable midpoint markout is not an executable return. Include both sides of any proposed round trip, known-depth costs, fees, and delay assumptions where the evaluation supports them; otherwise label the missing economics.
7. **Inspect replay:** Review successes and failures using only evidence available at the decision cutoff. Later journal notes do not become historical knowledge.
8. **Define a complete policy before portfolio claims:** Specify exits, sizing, cooldowns, overlap handling, maximum exposure, and behavior on unavailable evidence. For unlevered spot, bearish evidence means avoiding buys or reducing existing inventory, not opening an unsupported short.
9. **Keep the outcome:** Mark the experiment supported, rejected, or inconclusive with evidence links. A negative result is useful; do not discard it and report only successful variants.

## How to resume

- [Demo, recording, replay, and research CLI/configuration](DEMO.md)
- [Terminal controls, Paper, Journal, and Research](TERMINAL.md)
- [Analytics formulas, coverage, and quality boundaries](ANALYTICS.md)
- [Execution-cost and comparison assumptions](COSTS.md)
- [Replay clocks, determinism, and seek behavior](REPLAY.md)

Research runs start through the CLI; the browser Research tab displays persisted attempts. Use the same `--state-dir` for the server and research commands. Capture paths and attempt IDs should come from actual command output, not invented examples.

For each future experiment, append or link a result record containing: experiment ID, research attempt IDs where applicable, capture/config/source identities, sample coverage, assumptions, held-out outcomes and variability, exclusions, replay/journal evidence, and the supported/rejected/inconclusive decision. No results are recorded in this backlog yet.

## Deferred directions

- Passive market making: no queue-position or passive-fill model.
- Cross-exchange arbitrage: observed differences do not establish executable access, inventory availability, atomicity, or transfer feasibility.
- Funding arbitrage: isolated perpetual funding accounting exists under explicit models, but there is no hedged multi-leg strategy, automatic context-to-funding-model conversion, borrow/transfer model or validated executable arbitrage.
- Spoof-detection strategies: aggregate public books cannot establish trader intent.
- Complex machine learning: establish simple, interpretable baselines and cost-aware held-out evidence first.

This backlog authorizes no private exchange integration, live orders, credentials, wallets, or real funds.
