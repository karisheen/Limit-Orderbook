# PR 1 · Design tokens and shell frame

**Scope:** the owner accepted the bounded PR 1 implementation and approved publication of `6d83e67a5b` in [PR #27](https://github.com/karisheen/Limit-Orderbook/pull/27). Its required remote `offline` check failed during named-grid editing. The [local remediation](verification.md#named-grid-ci-remediation) stabilizes desktop edit feedback and strengthens its browser regression. Both named-grid cases pass; the complete local browser run is **not green** because a separate narrow-screen replay seek timed out before editing. This commit is for owner review, not approved for publication or merge. The [equivalence proof](equivalence.md) identifies the differences from the original publication. No release or PR 2 work is included.

## What changed

Graphite (`dark-dull`) is the default only when there is no restored theme. Paper retains `light-dull`; Midnight, Daylight, Forest and Neon keep their existing IDs and character. The [token map](tokens.md) gives the exact colors and the Daylight contrast correction. The existing shell, status bar and panels use compact controls, identifying edges, visible keyboard focus and tabular numerals. The same plot, grid, text and financial roles reach Lightweight Charts, native liquidity and display-study canvases without replacing a renderer or its paint scheduler.

A fresh profile still opens **Footprint**. The editable grid, five presets, named layouts, both preference stores, exact native values and one session per document remain in place. Switching theme is neither a source change nor a seek. The side book and tape on the Order flow grid are *display projections* of native observations; the separate exact **Native book & tape** view remains available. Navigation, lens defaults, adaptive layout and configurable density were not implemented.

## Compare the production surfaces

Same Coinbase BTC-USD spot primary, USD price/BTC quantity, `synthetic-demo-v3-180s`, paused at delivery **3485/6970**, Order flow grid, 1m **Price action → Session trades**. The two flat observed buckets are genuine native demo output; they were not replaced with the design specimen. Images below are the original captured bytes, selected from the final historical runs, not new renders. The [public manifest](evidence/captures.json) distinguishes final captures, ancillary inspections and the one deliberately retained failing frame.

| Surface | Before | After |
|---|---|---|
| Graphite · 1920×1080 | [Main](evidence/before/before-dark-dull-1920x1080-dpr1-price.jpg) | [PR 1](evidence/after/after-dark-dull-1920x1080-dpr1-price.jpg) |
| Paper · 1920×1080 | [Main](evidence/before/before-light-dull-1920x1080-dpr1-price.jpg) | [PR 1](evidence/after/after-light-dull-1920x1080-dpr1-price.jpg) |
| Graphite · 1280×800 | [Main](evidence/before/before-dark-dull-1280x800-dpr1-price.jpg) | [PR 1](evidence/after/after-dark-dull-1280x800-dpr1-price.jpg) |
| Paper · 1280×800 | [Main](evidence/before/before-light-dull-1280x800-dpr1-price.jpg) | [PR 1](evidence/after/after-light-dull-1280x800-dpr1-price.jpg) |

**All six at 1440×900:** [Graphite](evidence/after/after-dark-dull-1440x900-dpr1-price.jpg) · [Paper](evidence/after/after-light-dull-1440x900-dpr1-price.jpg) · [Midnight](evidence/after/after-dark-1440x900-dpr1-price.jpg) · [Daylight](evidence/after/after-light-1440x900-dpr1-price.jpg) · [Forest](evidence/after/after-forest-1440x900-dpr1-price.jpg) · [Neon](evidence/after/after-futuristic-1440x900-dpr1-price.jpg).

**View and scale:** [Native Liquidity](evidence/after/after-dark-dull-1440x900-dpr1-liquidity.jpg) · [display Footprint](evidence/after/after-dark-dull-1440x900-dpr1-footprint.jpg) · [2560×1440](evidence/after/after-dark-dull-2560x1440-dpr1-price.jpg) · [3840×2160](evidence/after/after-dark-dull-3840x2160-dpr1-price.jpg) · [390×844 analytical field](evidence/after/after-light-dull-390x844-dpr1-price.jpg) · [1440×900 at DPR 2](evidence/after/after-dark-dull-1440x900-dpr2-price.jpg).

## Independent review and limits

The [visual reviewer](review-visual.md) initially rejected the clipped laptop bid and Price field. The [technical reviewer](review-technical.md) challenged book scroll ownership and the evidence for preference migration. Both accepted the repaired local scope. The [failing frame](evidence/regressions/clipped-bid-1280.png) and [path-normalized assertion](evidence/regressions/clipped-touch.txt) are retained so the fixes can be judged against the original failure. The [verification record](verification.md) includes the failed browser runs, corrections, final results and reproduction steps. At 1280×800 the repaired balanced candle field is still only 76 CSS pixels high; the existing Price focus preset remains the roomier choice.

The accepted implementation's full offline run passed **2,221 Python tests and 279 frontend tests**. Its final local Chromium run passed **116**, with **three live-only skips**. These [historical receipts](evidence/gates/accepted.json) and [publication smoke](evidence/gates/publication-fast.txt) are not a result for the subsequent remote failure or its local correction. See the [remediation record](verification.md#named-grid-ci-remediation) for the separate results. Offline evidence does not establish live throughput, strategy performance, physical 4K readability or full accessibility certification.

## Proposed GitHub pull request

**Title:** Adopt B.2 Graphite and Paper tokens in the existing terminal shell

**Description at publication:** Adopt the B.2 Graphite and Paper palettes on their existing theme IDs and compact the existing production shell. Preserve saved theme selection, chart ownership, native financial readouts and layouts. Use the matched production captures and original independent reviews above to assess the change. The owner accepted PR 1 only. The required remote `offline` check must pass on the exact head before merge.

## Rollback and next gate

On the topic branch, the implementation is isolated at `80b80214de`; the evidence-only publication commit need not be reversed to change application behavior. If later squash-merged, use the resulting main commit as the rollback reference. Restore/rebuild the previous frontend at a safe operator boundary. **Do not** reset theme/layout storage, recordings or ledgers, replay an ambiguous action, or reload an active recording owner merely to change styling.

The [approved decisions and eight deferred refinements](decision.md), [checklist](checklist.md), [complete public file inventory](inventory.txt), [differences from the retained private review](review-differences.tsv), and [retention policy](retention.md) constitute the handoff. **PR 2 has not started.** If separately authorized later, reuse the clean production worktree from verified post-merge main. Its first navigation review must preserve one native session, the five presets and named-layout scope, keyboard return focus and frozen evidence; opening a destination must not select a source or trigger a financial action. No rail, command palette or destination model is included here.
