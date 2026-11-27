# PR1 production theme tokens

The canonical design specimen is **local-only**: `ce9e5a91f217fab5d63ee235f8d69c96aaad435a:docs/terminal-redesign/b2/tokens.css`. The tables below give the production mapping without requiring that Git object. The existing [six-theme engine](../../frontend/src/orderflow/lib/theme.ts) publishes the selected map as root CSS properties through `applyTheme`; no prototype stylesheet or theme engine is imported.

## Graphite and Paper

The existing IDs `dark-dull` (Graphite) and `light-dull` (Paper) receive the approved B.2 palette. Previously saved selections of either ID receive the same updated palette; there are no new theme IDs.

| Production role / property | Canonical property | Graphite | Paper |
| --- | --- | --- | --- |
| Workspace canvas `--bg-base` | `--canvas` | `#090909` | `#e8e8e8` |
| Plot `--bg-chart` | `--chart` | `#0c0c0c` | `#fafafa` |
| Panel `--bg-panel` | `--panel` | `#111111` | `#f4f4f4` |
| Toolbar `--bg-surface` | `--toolbar` | `#141414` | `#eeeeee` |
| Raised surface `--bg-panel-alt` | `--raised` | `#191919` | `#ffffff` |
| Hover `--bg-hover` | `--hover` | `#242424` | `#dedede` |
| Neutral selection `--bg-active` | `--selected` | `#242424` | `#dedede` |
| Decorative divider `--border` | `--border` | `#383838` | `#c4c4c4` |
| Plot grid `--grid` | `--grid` | `#292929` | `#e0e0e0` |
| Primary text `--text-primary` | `--text` | `#ededed` | `#202020` |
| Secondary text `--text-secondary` | `--secondary` | `#bdbdbd` | `#454545` |
| Muted text `--text-muted` | `--muted` | `#a3a3a3` | `#555555` |
| Accent `--accent`, `--text-accent` | `--accent` | `#ededed` | `#252525` |
| Text on accent `--on-accent` | `--on-accent` | `#181818` | `#ffffff` |
| Keyboard focus `--focus` | `--focus` | `#f4f4f4` | `#202020` |
| Required control boundary `--control-edge` | `--control-edge` | `#858585` | `#737373` |
| Buy/bid `--buy` | `--bid` | `#76c9a6` | `#126044` |
| Sell/ask `--sell` | `--ask` | `#f19595` | `#962f37` |
| Dim buy mark `--buy-dim` | `--bid-dim` | `#57977c` | `#43856c` |
| Dim sell mark `--sell-dim` | `--ask-dim` | `#b56f6f` | `#b7646a` |
| Buy tint `--buy-bg` | `--bid-soft` | `rgb(118 201 166 / 12%)` | `rgb(18 96 68 / 9%)` |
| Sell tint `--sell-bg` | `--ask-soft` | `rgb(241 149 149 / 12%)` | `rgb(150 47 55 / 9%)` |
| Unknown / non-directional `--neutral` | `--unknown` | `#bdbdbd` | `#454545` |
| Warning `--warn` | `--warning` | `#e3bd73` | `#765000` |
| Critical severity `--critical` | `--critical` | `#ff9696` | `#a0222b` |
| Open interest `--oi` | `--oi` | `#d2c092` | `#6b581f` |
| Sign-neutral funding `--funding` | `--funding` | `#bdbdbd` | `#454545` |

Critical severity is not a synonym for selling. Funding uses one color for either sign; the existing number and sign carry direction. Unknown/missing data is neutral and must not be represented as zero. Inspection and selection use neutral marks, not buy/sell colors.

Required edges use `--control-edge`, not the decorative divider or low-opacity financial tint. Focus uses a 2px `--focus` ring. Critical text and required boundaries target at least 4.5:1 and 3:1 respectively on their actual surfaces; this contract is not a claim that every legacy color or translucent overlay meets those ratios.

## Native roles without an identical canonical property

These are intentional adaptations, not additional prototype tokens:

| Existing production property | Graphite | Paper | Mapping / scope |
| --- | --- | --- | --- |
| `--border-muted` | `#292929` | `#e0e0e0` | Canonical grid color reused for subtle decorative separators, never required edges. |
| `--highlight` | `#ededed` | `#252525` | Canonical neutral accent for inspection marks. |
| `--highlight-dim` | `#242424` | `#dedede` | Canonical selected surface, opaque rather than the old tinted alpha fill. |
| `--buy-border` | `rgba(118,201,166,0.32)` | `rgba(18,96,68,0.32)` | Canonical bid RGB with the existing native 32% decorative-border opacity. |
| `--sell-border` | `rgba(241,149,149,0.32)` | `rgba(150,47,55,0.32)` | Canonical ask RGB with the existing native 32% decorative-border opacity. |
| `--poc` | `#c9ad4b` | `#8f7419` | Existing native POC identity retained; not an OI alias. |
| `--vwap` | `#b08cd0` | `#7a4aa0` | Existing native VWAP identity retained; not a funding alias. |
| `--shadow` | `rgba(0,0,0,0.4)` | `rgba(0,0,0,0.12)` | Existing native shadow retained. |

The canonical `--strong` values (`#696969` / `#898989`) have no distinct production consumer and are not introduced. Font, radius and density prototype tokens are not imported. Existing indicator color overrides remain authoritative.

## Four retained choices

Midnight (`dark`), Daylight (`light`), Forest (`forest`) and Neon (`futuristic`) retain their existing surfaces, distinctive accents, recognizable buy/sell hue identities, POC/VWAP, grid and shadow. Midnight, Forest and Neon retain their original financial/warning values and translucent fills. Daylight alone receives the evidence-driven financial/warning readability adjustment detailed below. The new plot property equals each choice's existing canvas value. Muted/neutral readability and the newly required semantic roles are added or strengthened:

| Property | Midnight | Daylight | Forest | Neon |
| --- | --- | --- | --- | --- |
| `--bg-chart` | `#08090b` | `#eef1f6` | `#060d09` | `#04040f` |
| `--text-muted`, `--neutral` | `#92959f` | `#5f687a` | `#8bb89b` | `#9a9ae6` |
| `--focus` | `#f2f3f5` | `#141a26` | `#9fe0b6` | `#b4b4ff` |
| `--control-edge` | `#858993` | `#707b8f` | `#799e86` | `#8b8bce` |
| `--critical` | `#ff9696` | `#a0222b` | `#ff9696` | `#ff9696` |
| `--oi` | `#d2c092` | `#6b581f` | `#d2c092` | `#d2c092` |
| `--funding` | `#a2a5ae` | `#4b566b` | `#8bb89b` | `#9a9ae6` |
| Retained `--accent` | `#d7f75b` | `#2f6fec` | `#9be46a` | `#9b6bff` |
| `--buy` | `#2fd897` | `#086648` | `#4cd07d` | `#00e5ff` |
| `--sell` | `#ff5c74` | `#a1262d` | `#ff6b5e` | `#ff3df5` |

These four palettes are native compatibility choices, not claimed B.2 Graphite/Paper variants. OI and critical colors extend the canonical dark/light semantic families; funding follows each existing secondary-text color. No seventh theme is added.

### Daylight readability exception

The original 1920px production capture and ink sampling (full initial diagnostics retained locally) found Daylight bid text at 3.40:1 on a plain surface and 2.44:1 on its depth fill, ask text at 4.50:1 plain and 2.98:1 on its fill, and small warning text at 3.73:1 plain. These failures justify deepening the existing green, red and amber hues rather than replacing Daylight's identity:

| Daylight property | Previous value | Revised value |
| --- | --- | --- |
| `--buy` | `#0c9c72` | `#086648` |
| `--buy-dim` | `#08704f` | `#064f38` |
| `--buy-bg` | `rgba(12,156,114,0.09)` | `rgba(8,102,72,0.09)` |
| `--buy-border` | `rgba(12,156,114,0.35)` | `rgba(8,102,72,0.35)` |
| `--sell` | `#d63a43` | `#a1262d` |
| `--sell-dim` | `#a1262d` | `#781d23` |
| `--sell-bg` | `rgba(214,58,67,0.09)` | `rgba(161,38,45,0.09)` |
| `--sell-border` | `rgba(214,58,67,0.35)` | `rgba(161,38,45,0.35)` |
| `--warn` | `#c76a00` | `#825000` |

Dim marks remain a distinct darker shade. Tinted roles use the revised side RGBs with their existing token opacities; required control boundaries use `--control-edge`. The six [final Daylight samples](evidence/inspection/contrast-final.json) were all at least 5.441:1 against their sampled surfaces. This is not a guarantee for every composition. Depth-fill opacity is a separate presentation setting, not a new theme or financial calculation. Daylight's blue accent, surfaces, POC/VWAP, critical role and ID remain unchanged. Graphite/Paper canonical values and the other three legacy financial identities are untouched.


## Defaults, fallback and persistence

Only the fresh `uiStore` theme default changes, from `dark` to `dark-dull`. Existing manual hydration, storage key `limit-orderbook.orderflow.ui-preferences`, version 3, migration, merge and persisted fields are unchanged. `saved.theme ?? current.theme` still selects a saved value before the fresh default, including all six valid saved IDs. Panels, indicators, heatmap, footprint mode, layout, density and navigation defaults are not changed by this work.

Before theme application, the stylesheet's root defaults are Graphite. After application, the selected theme map is authoritative for CSS and canvas. `useCanvas`, `PriceChart` and `MarketChartsRenderer` read computed CSS properties first. For an absent property, they fall back to the published map selected by `data-theme` (root for `useCanvas`, closest themed ancestor for the other two), using Graphite when the attribute is absent and Midnight for an unsupported explicit ID. `MarketChartsRenderer` also initializes its pre-read palette from Graphite. `applyTheme` retains its existing unsupported-ID fallback to Midnight without rewriting the saved preference. This is defensive rendering behavior, not a new malformed-storage migration.

The obsolete fixed Midnight `theme-color` meta tag is removed rather than advertising the wrong browser tint after selection. Existing `applyTheme` color-scheme handling remains unchanged (Daylight/Paper light, other valid choices dark).

## Extrapolation limits

Production heat scaling remains native and fixed according to the existing implementation. The prototype's five BTC heat bands, band clipping, opacity assumptions and legend values are not transplanted. No heat-band tokens are added. Existing metric calculations, units, numeric strings, missing/gap semantics, paint scheduling, viewport handling, DPR/visibility invalidation and renderer ownership remain native. A token correspondence does not authorize new views, providers, storage fields, prototype layout, or financial reinterpretation.
