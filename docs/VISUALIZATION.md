# Visualization evidence and proposal risk

The workstation exposes `POST /api/v1/visualization`, `/api/v1/ladder`, and
`/api/v1/proposal-risk`. Each request carries the existing independent
`session_id` and exact string `generation`. The normal local-origin, host,
request-size, action, worker ownership, cancellation, and generation fences
apply. These are analytical queries, not exchange order APIs.

## Historical liquidity

`VisualHistory` is the replay observer and the live completed-message observer.
It owns the ordering around the existing `AnalyticsEngine`: close sampling
boundaries using the **previous completed delivery**, apply the new completed
message to analytics, then retain that message's admitted public prints. A
boundary closes only when the source receive-monotonic highwater moves strictly
past it. Thus all completed observations at the same boundary are included and
no later message can enter an earlier liquidity sample or metric.

Coordinates use source receive-monotonic highwater, not exchange time, browser
receipt, replay pacing, or wall-clock alignment. Native references still retain
original receive and exchange timestamps. Late cross-stream trades are placed
at their actual admission highwater; their original references remain visible.
This prevents later deliveries from changing closed candles by backdating them.
Clock changes discard the incompatible time-axis cache with explicit retention
reasons rather than inventing cross-clock alignment. Source/gap transitions have
separate segment identities; multi-segment candle/footprint buckets are omitted
and report `interval_segment_discontinuity`.

### Receive-clock display projection

Every visual response carries a complete `time_axis`, including no-book and
unchanged-column incremental responses. It is a native piecewise projection of
the existing receive-monotonic admission coordinates, not exchange time and not
a browser-estimated offset. Within an admitted segment:

```text
projected_wall_ns(t) = reference.receive_wall_ns
                      + (t - reference.receive_mono_ns)
```

The original paired receive reference is retained exactly. `segment.start_ns`
is its inclusive admission boundary, **not** the receive-mono anchor used in the
equation. An initially late pair can therefore establish a projection at a later
admission highwater without backfilling earlier coordinates. Clock reads at the
receive boundary are sequential observations, not a claim of atomic measurement
or synchronized exchange time.

The first completed delivery establishes the initial segment even without a
validated book. A strictly advancing delivery whose original receive mono equals
the admission frontier establishes a new segment when its wall-minus-mono offset
differs from the active offset by at least **1,000,000 ns**. Smaller changes leave
the anchor unchanged; this threshold is an explicit display policy, not financial
tolerance or an accuracy guarantee. Positive and negative steps affect only
labels from the new boundary onward. Late and equal-highwater conflicting pairs
never relabel existing coordinates; the latest retained conflict is exposed in
`last_unapplied_pair` with `nonadvancing_receive_pair`.

Projection coverage has inclusive endpoints, unlike the half-open financial
window. There are no projected labels before the first admitted pair or beyond
the captured frontier. A historical query returns the original predecessor
segment at its coverage start and transitions through its end; future references
and unapplied-pair diagnostics are excluded. Missing projection reports
`receive_clock_unavailable`, never a fabricated date. Clock reasons are separate
from financial eligibility and do not alter quantities, bucket ownership,
sampling, candles, footprints or `complete_window`.

The observer retains at most 1,801 immutable segments and one unapplied pair,
covering at most the latest 1,800 seconds independently of book availability.
Time eviction preserves the original predecessor needed at the retention floor.
Segment-count and shared 32 MiB byte-budget eviction retire old coverage rather
than merging unequal offsets or extrapolating a future anchor backwards.
`receive_clock_history_truncated` and
`requested_receive_clock_history_unavailable` disclose lost/requested coverage.

Runtime supplies its established recording/demo/live `source_identity`.
Standalone observers derive identity from the raw evidence session, leaving it
empty before evidence when no identity is configured. `scope_id` identifies a
contiguous source-session/clock epoch, with deterministic epoch numbering so an
A → B → A transition cannot reuse the first A mapping. Source-clock cutovers
discard the prior projection and report `source_clock_changed`. The query
snapshot owns a copied bounded segment container and immutable records; accepting
a query commits presentation state only and rejects reset, source or epoch
cutovers, never replacing owner clock evidence.

Observer checkpoints are version **3** and include projection policy, exact
anchors, epoch, coverage, truncation and the unapplied pair. Restore rejects
incompatible policy/source/version; reset clears evidence but preserves configured
source identity. Replay continues to reset/recompute the actual prefix on seek,
so later wall steps cannot leak into a rewind and pacing cannot select anchors.


The economic price grid remains zero-origin. Exact Decimal display steps use
source-compatible quanta; known fixed ticks supply multiples, while
variable/unknown ticks report `display_quantum_not_native_tick`. A display
quantum is not an executable price rule. Without an explicit step, the first
eligible two-sided book fits nearby
native depth (up to one quarter of the requested rows per side) and admitted
in-window trade prices. The fit uses exact 1/5-decade tick multiples and leaves
edge room; coarse fitted steps remain selectable in the chart. The DOM selects
its compatible native/display quantum independently.
Unavailable bootstrap does not freeze a fallback step or zero price anchor.
Each pane retains its resolved native lower bound and quantum until its
source/session/generation/instrument identity, requested aggregation, row count,
or explicit recenter changes. Switching between 32-row footprint/profile and
128-row other modes requests new centered geometry with the pane's current
price offset, including for a panned view. An explicitly selected step remains
authoritative; it prevents automatic quantum selection, not the origin reset.
Switching modes within the same row-count group does not itself reset the origin.
Replay rewind/reset clears the evidence-derived anchor and reused step,
reconstructing from the available prefix; future scale cannot survive at an
earlier cursor. Clients retain explicit aggregation by sending it on each query.
Panning changes the viewport, not economic bins.

An optional `VisualizationRequest.price_min` fixes a caller's viewport origin
without changing that economic grid. It is an exact finite nonnegative decimal
string of 1–128 characters, subject to the shared native decimal bounds (at most
38 coefficient digits, absolute stored exponent at most 38, and magnitude at
most `1e30`). It requires an explicit supported `price_step` and must be aligned
to that step. Numeric JSON values, misaligned origins and the combination with
`recenter: true` are rejected. Native Decimal arithmetic computes
the visible lower bound as
`max(0, price_min + price_offset_rows * price_step)`. A derived lower bound
outside the same input domain is rejected before publishing geometry, so every
returned origin can be submitted again exactly. Omitting `price_min` preserves
the existing fitted-anchor/recenter behavior.

Each frontend chart retains and resends its own explicit origin and step after
fitting, so a sibling's recenter cannot move an already established independent
viewport. This retention is in-memory, not a promise of origin persistence after
browser reload. Subsequent offsets are relative to the offset captured with the
saved origin, so the initial pan is not added repeatedly. An explicit-origin
query does not mutate the shared legacy anchor or step. This is query geometry,
not a backend pane registry or an alteration
of native history. All panes in the session still share one selected instrument,
source and generation, not separate feeds. Selection, replay reset/rewind and
source-clock fences remain authoritative; an old viewport cannot carry future
evidence across them.

The base sampling quantum is one second; explicit five- and ten-second requests
select retained exact boundary evidence. No incoming range or percentile
changes a historical price grid or liquidity hash. Color transfer belongs to
explicit frontend presentation controls, not server normalization.

The **Fixed contrast** slider selects a full-intensity base quantity on a
logarithmic control spanning `1e-12` through `1e12`. Color intensity itself stays
linear from zero to that fixed quantity; incoming liquidity never rescales old
cells. This makes both small BTC quantities and large SOL quantities visible
without clipping everything at the former 10-unit control ceiling. For example,
an explicitly selected 1,000-SOL scale reveals differences among 100–1,000-SOL
levels. The legend and accessible slider value state the actual quantity/unit.
The selected quantity survives preference reload; adjust it when changing base
assets. This is presentation only, not a change to native prices or quantities.

The immutable native `Book` at a sample is shared, not copied for each delivery.
Explicit price reprojection reads those retained exact native levels. It never
reconstructs finer history from aggregated pixels. Coverage is per side and
per entire cell: observed absence in covered depth is exact `"0"`; uncovered,
ineligible, unknown-valuation, or unavailable evidence is `null`. Partial depth
can coexist with eligible covered cells and reports its limitation. Best quotes
remain exact native prices. CVD and event OFI retain the shared analytics metric
provenance, formula, window, and eligibility; snapshot sources are not relabeled
as event OFI.

The heatmap fills the complete time bucket when only one side has positive
quantity and the other is known zero. Splitting every bucket into bid/ask halves
would falsely suggest a temporal gap. Mixed-side bins remain split; unknown
side coverage remains visibly unknown rather than being filled from its neighbor.

Executed-trade overlays, candles, profiles, and footprints consume the existing
analytics admission/deduplication/valuation path. Admission alone is not proof
of usable flow: aggregation also requires the shared selected-instrument
trade-channel eligibility gate. Multiple matching trade streams are ambiguous,
not additive; their prints cannot enter visual totals or overlays.
Unknown aggressor side remains separate. Subscription history from a unique
source can appear as a labeled overlay even before live flow is established,
but never enters observed-flow candle/profile/footprint totals.

Elapsed half-open flow intervals retain the shared quality reasons before
analytics advances to the next source highwater. Equal-highwater deliveries
settle together; freshness is checked through the elapsed interval, including
periods with no trade message. A configured channel or transport heartbeat
does not establish validated trade evidence. Missing, stale, unhealthy, reset,
or ambiguous evidence makes the affected window incomplete and any observed
candle in that bucket ineligible. Flow reasons are independent of resting-book
eligibility: valid native liquidity remains visible. Later recovery cannot
erase historical flow failures or alter a closed candle.
A fresh but silent channel alone does not prove an empty window. Empty-flow
completeness additionally requires retained validation at its final included
nanosecond (`end_ns - 1`), the half-open counterpart of analytics' endpoint
validation rule; otherwise it reports `quiet_trade_interval_unproven`.

Missing prints are not synthesized or forward-filled. A candle is an observed
public-trade OHLC/volume bucket, not a promise of complete venue execution
history. Profiles and footprints may contain the admitted eligible portion of
an incomplete window; consumers must retain the response's reasons and
`complete_window` disclosure. Warming, unavailable history, truncation, gaps,
and partial trailing windows remain explicit.

## Ownership, bounds, and incremental delivery

Each selected-instrument history has a 32 MiB accounted budget, at most 1,800
base samples, 1,800 flow-quality intervals, and 4,096 trades. Accounting charges shared native books once
(`32,768 + 512 * native levels`), sample/metric evidence conservatively from its
encoded size, cached exact metric JSON bytes plus per-entry overhead, trade and
flow-quality evidence conservatively from encoded size, and a 256 KiB reserve
for bounded query identity and discontinuity state. Oldest evidence is evicted;
retained start and truncation are exposed. A native book too large for a sampled
history budget produces an unavailable sample, never a clipped native book.
These are maximum capacities, not a promised retained duration: deep native books
can exhaust the conservative byte budget well before the requested time range.
The real Binance benchmark visibly shows that truncation rather than filling
evicted history with zero or current depth.
Omitted start bounds use the observed/retained history floor, aligned to the
requested bucket and capped at a 180-second lookback. The default chart therefore
expands as observations arrive rather than reserving most pixels for uncollected
or evicted history. Actual returned bounds, initial partial buckets and retention
truncation remain explicit. User-requested start bounds are not shortened;
unavailable portions stay unknown/hatched.
The native reducer, recorder, raw schemas, and live collection remain unchanged.
The current owner already holds the current full native book; this module does
not create a second current-depth reconstruction.

Queries accept at most 1,800 seconds, 32–256 rows and 1–600 columns. They cap
rendered cells at 65,536 and native visible-level work at 500,000 per request.
The work allowance is divided into fixed per-column quotas from explicit query
controls; incoming columns cannot change an old column's availability. Exact
projection work is shared when multiple samples reference the same native book.
Columns have a 4 MiB exact encoded output budget and trade overlays a 1 MiB
encoded budget; profile rows are bounded by visible price rows and footprints by
retained admitted prints. All limits report explicit reasons. Range/quantum
requests outside supported bounds are rejected, not silently rebinned. Visible
time intervals are half-open `[start_ns, end_ns)`, and end is clamped to the
observed cursor. A print exactly at the frontier remains in the current tape and
enters historical overlays when highwater advances. Initial partial buckets are
explicit; closed aligned historical windows can report complete coverage.
Read queries never advance playback or open
another reader.

The session owner captures one shallow query snapshot of immutable evidence and
bounded history containers. A single runtime-wide visual worker projects that
completed prefix without holding the session lock or occupying the ingestion
worker. It neither opens a reader nor calls the financial engine. Live delivery
may advance or evict history while the snapshot remains valid; responses retain
the captured native clock rather than claiming the latest arrival.

Lifecycle actions admit at most one snapshot per session. Each pins one bounded
history prefix and its selected current book until completion, in addition to
the advancing owner's history and bounded response work; this is not a whole-
process RSS guarantee. Cancellation joins the visual job before releasing the
lifecycle action. Publication commits only presentation preferences and the
incremental fingerprint, and rejects source-clock changes; it cannot overwrite
new evidence or survive selection/removal. Session admission bounds outstanding
snapshots, and runtime close joins and shuts down the visual executor.

Frontend visualization query and chart ACK cancellation are scoped to the caller
rather than the endpoint.
Two charts may query `/visualization` with distinct geometry without replacing
or aborting one another's request. Each caller cancels its own obsolete work
and fences responses by session, generation and selection; the native ownership
and snapshot bounds above still apply. Hidden panes suspend their queries,
including siblings hidden by a maximized pane, and hidden browser documents
suspend polling. Restoring a pane resumes its own query lifecycle. Neither
layout visibility nor presentation freezing stops shared source collection.

One bounded prior-query fingerprint map supports opaque `after_column` tokens.
Matching geometry, generation and retained continuity can return changed/new
columns with `replace=false`; incompatible caches, evictions, source/quantum
changes or obsolete tokens return a replacement. Trade/profile/footprint
responses describe the requested visible window, with explicit incompleteness
when history or output limits prevent that claim.

Exact HTTP column scalars are canonically encoded once for both their byte-budget
charge and incremental hash. The owner does not recursively copy and serialize
the same provenance tree a second time. Hash identity and output bounds remain
unchanged; this is not a larger queue, shallower native book or omitted evidence.
Closed metric provenance is encoded once per retained sample, within that same
32 MiB budget. Queries decode fresh containers, so consumer mutation cannot
alter native history or later responses. Eviction/reset removes the derived
bytes; checkpoint restoration rebuilds them without changing native metric
values. The receive-clock addition uses observer checkpoint version 3.

Replay seek/reset uses the existing replay reset/recompute path. Selection
rebuilds actual observations through the same owned reader to the existing
cursor; it does not fabricate history from the selected current book. Live
selection starts at its subscription boundary and cannot claim pre-subscription
history. All analytical state, admission caches, dedup state, samples, trades,
elapsed flow-quality intervals, and receive-clock projection are checkpointed or
reset/reconstructed; presentation quantum/recenter preferences do not insert
future market evidence. Playback speed changes pacing only.

## Ladder and proposal risk

The grouped ladder reads the current native book independently of frozen browser
presentation. Requests retain `price_step` and `rows` (1–100 per side). Bids
group downward and asks upward, so grouping cannot fabricate a crossed spread.
Each row returns `side`, `price`, `base_quantity`, `cumulative_base`,
`quote_notional` and `cumulative_quote`. Both valuations use each **original
native level price** through the shared contract valuation before aggregation;
bucket price multiplied by grouped base is not the notional.

Response `unit` and `quote_unit` identify the native assets. `tick_size` remains
null when unknown; `step_options` supplies compatible display choices.
`coverage` carries subscribed depth, retained bid/ask levels and `full_depth`;
`source_mode`, `asof`, eligibility and reasons preserve scope/provenance.
The nullable `summary` contains ungrouped `best_bid`, `best_ask`, `midpoint`,
`spread`, `spread_ticks` and `spread_bps`. Midpoint is `(ask + bid) / 2`;
bps is `spread / midpoint * 10000`, using the native Decimal context, not browser
arithmetic. Tick count is null when the native tick is unknown. Ineligible or
missing two-sided evidence has no summary. Grouping cannot widen this native
spread or turn unknown values into zero.

The browser independently selects DOM grouping and base/quote presentation.
Both sides share a held, explicitly rescalable bar cap; bar coordinates alone
are converted to display numbers. Quantity changes compare the same observed
side/price only, with no inference from newly visible or missing rows.
Hover/focus links exact DOM prices to the containing chart bucket using bounded
integer decimal comparisons; prices outside the viewport remain outside it.
Unknown contract units, stale books, unsupported quanta, native work limits and
partial depth remain explicit.

Proposal risk is a manual hypothetical fixed-position entry/stop calculation.
Buy stops must be below entry; sell stops above. Shared price validators,
contract valuation, native lot rules, size/notional minima and maxima, and
assumed fee conventions remain authoritative. Risk-budget sizing rounds down
to the supported native lot and respects both legs' constraints. Unknown fees
cannot produce a trustworthy net estimate or suggested risk-budget size.
Inverse contract loss is valued in quote at the hypothetical stop, not reported
as an unrelated base or settlement cash amount. Unsupported valuation and
precision rules are unavailable, never guessed.

The result explicitly warns that entry/stop are hypothetical, stop liquidity is
unknown, and gaps/slippage can exceed the estimate. Fees are user assumptions
with provenance/date and quote-denomination on both legs. This is neither an
execution engine nor a guaranteed maximum-loss bound.

## Chart-specific paint acknowledgement

Visualization responses include opaque `view_id` and server monotonic
`published_mono_ns`. At most 64 pending publications are retained per session,
expire after 60 seconds, and are cleared on seek, replacement/selection and
close. `POST /api/v1/visualization/ack` accepts the exact session/generation/view
and a finite 0–60,000 ms browser receive-to-paint duration. Unknown, repeated,
expired, cross-session and obsolete IDs cannot invent timing.

The response reports server publication-to-ACK elapsed milliseconds as a
conservative round-trip upper bound alongside the browser measurement. It does
not claim synchronized one-way latency, actual GPU completion, or that a normal
WebSocket frame ACK proves a separately fetched chart painted.

Displayed-chart freshness follows the canvas presentation, not merely the latest
query response. Clearing a missing view, recovering from a rendering exception,
resizing the backing store, or disposing the renderer invalidates its displayed
identity and evidence ages. Cancelling scheduled work without clearing retained
pixels does not invalidate the retained presentation. Only a successful draw
followed by two visible animation-frame opportunities can restore the displayed
identity and emit a paint acknowledgement; failed draws never acknowledge a view.

`ChartFreshness` takes that identity-fenced **presented** view and keeps three
measurements distinct:

- **Source lag** compares the primary rendered mode's latest eligible native
  `asof.receive_mono_ns` with the greatest observed same-clock highwater,
  including the displayed view's `now_ns` and current source/evaluation
  observations. A matching current clock observation is required; the view alone
  is insufficient. Heatmap uses eligible overlapping depth columns; footprint,
  profile and candles use non-history, known-size trades contributing to the
  displayed cells, rows or eligible candles. CVD/OFI use eligible displayed
  metric references. Native time/price bounds, clock identity and renderer caps
  qualify every candidate. Fresh overlays never substitute for missing or stale
  primary-mode evidence.
- **Evidence resident** is browser-monotonic elapsed time since that native
  evidence identity was first continuously displayed in the component. A fresh
  response or Frame carrying unchanged evidence does not reset it.
- **Response resident** is browser-monotonic elapsed time since the actually
  displayed query response body completed transfer, before JSON parsing.
  It is neither evidence age nor receive-to-paint timing. Receiving a newer
  unpainted response does not replace the displayed identity or timestamp.

Source lag can increase while a chart is frozen when current observations
advance the same source clock. If replay is paused or a silent source's observed
frontier does not advance, source lag may stay unchanged while browser residency
grows. Freeze stops repeat polling of retained evidence, not collection or
replay; changing chart settings or explicitly panning/zooming can deliberately
request a replacement while frozen. A cleared canvas has no presented evidence
identity or residency. Following explicit time ranges uses the qualified current
source-clock frontier, including evaluation-window endpoints, rather than
stopping at the last selected-instrument trade or book update.

Missing/mismatched clocks, invalid or future evidence timestamps, unavailable
displayed evidence, disconnection or source unavailability produce unavailable
source lag with a reason, never a fabricated zero. Valid retained evidence can
still accumulate browser residency while disconnected. Invalid browser receipt
timestamps independently make response residency unavailable. Replay lag is
measured within the recording's clock: no recording wall date is subtracted from
today's wall clock, and no browser/server/source clock domains are mixed.
Neither source lag nor residency claims exchange latency, network latency,
one-way delivery time, GPU completion or market-data completeness. The separate
chart ACK remains only the conservative publication-to-ACK round trip and
browser receive-to-paint measurement described above.
