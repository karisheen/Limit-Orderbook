import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { VisualGrid } from './api';
import { CHART_LIMITS, columnAt, plotArea, rowAt, saturation, shiftedRange, timeAt, zoomedRange } from './market-charts-model';
import type { ChartCursor, ChartMode, ChartRange } from './market-charts-model';
import { MarketRenderer } from './market-charts-renderer';
import type { RenderReport } from './market-charts-renderer';
import './market-charts.css';

export interface MarketChartsProps {
  view: VisualGrid | null;
  loading: boolean;
  error: string | null;
  mode: ChartMode;
  contrast: number;
  showTrades: boolean;
  showQuotes: boolean;
  crosshair: ChartCursor | null;
  onCrosshair: (value: ChartCursor | null) => void;
  onRangeChange: (range: ChartRange) => void;
  onPricePan: (rows: number) => void;
  receivedAtMs: number | null;
  onPaint?: (value: { view_id: string; receive_to_paint_ms: number; draw_ms: number }) => void;
}

const MODE_LABEL: Record<ChartMode, string> = {
  heatmap: 'Resting liquidity', candles: 'Executed-trade candles', footprint: 'Executed-volume footprint',
  profile: 'Visible-window executed profile', cvd: 'Cumulative volume delta', ofi: 'Order-flow imbalance',
};

export function MarketCharts(props: MarketChartsProps): ReactNode {
  const { view, loading, error, mode, contrast, showTrades, showQuotes, crosshair, receivedAtMs, onCrosshair, onRangeChange, onPricePan } = props;
  const id = useId();
  const host = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const cursor = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<MarketRenderer | null>(null);
  const latest = useRef(props);
  const visible = useRef(true);
  const acknowledged = useRef<string | null>(null);
  const [size, setSize] = useState({ width: 1, height: 1 });
  const [visibilityRevision, setVisibilityRevision] = useState(0);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [report, setReport] = useState<RenderReport | null>(null);
  const [paintMs, setPaintMs] = useState<number | null>(null);
  const [paintedViewId, setPaintedViewId] = useState<string | null>(null);
  useEffect(() => { latest.current = props; });

  useEffect(() => {
    const element = host.current;
    if (!element || !base.current || !overlay.current || !cursor.current) return;
    try { renderer.current = new MarketRenderer(base.current, overlay.current, cursor.current); }
    catch (cause) { setRenderError(cause instanceof Error ? cause.message : String(cause)); }
    const resize = () => {
      const bounds = element.getBoundingClientRect();
      setSize(previous => previous.width === bounds.width && previous.height === bounds.height ? previous : { width: bounds.width, height: bounds.height });
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    const intersection = new IntersectionObserver(entries => {
      const next = entries[0]?.isIntersecting ?? false;
      if (visible.current !== next) { visible.current = next; setVisibilityRevision(value => value + 1); }
    });
    intersection.observe(element);
    const visibility = () => setVisibilityRevision(value => value + 1);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('resize', resize);
    return () => {
      observer.disconnect(); intersection.disconnect();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('resize', resize);
      renderer.current?.dispose(); renderer.current = null;
      performance.clearMarks(`lob:chart-start:${id}`);
      performance.clearMarks(`lob:chart-end:${id}`);
      performance.clearMeasures('lob:chart-draw');
    };
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    let frame = 0;
    let paintFrame = 0;
    let acknowledgeFrame = 0;
    if (!view) { renderer.current?.clear(); setReport(null); setPaintMs(null); setPaintedViewId(null); return; }
    if (document.visibilityState !== 'visible' || !visible.current || size.width <= 1 || size.height <= 1) return;
    frame = requestAnimationFrame(() => {
      const instance = renderer.current;
      if (!instance || cancelled) return;
      try {
        instance.resize(size.width, size.height, window.devicePixelRatio);
        performance.clearMarks(`lob:chart-start:${id}`);
        performance.clearMarks(`lob:chart-end:${id}`);
        performance.mark(`lob:chart-start:${id}`);
        const drawn = instance.render(view, { mode, contrast, showTrades, showQuotes });
        instance.drawCursor(view, latest.current.crosshair, mode);
        performance.mark(`lob:chart-end:${id}`);
        performance.clearMeasures('lob:chart-draw');
        performance.measure('lob:chart-draw', `lob:chart-start:${id}`, `lob:chart-end:${id}`);
        setReport(drawn); setRenderError(null);
        // Two visible animation-frame opportunities, not a GPU completion claim.
        paintFrame = requestAnimationFrame(() => {
          acknowledgeFrame = requestAnimationFrame(() => {
            if (cancelled || latest.current.view !== view || document.visibilityState !== 'visible' || !visible.current) return;
            setPaintedViewId(view.view_id);
            if (receivedAtMs == null || acknowledged.current === view.view_id) return;
            const elapsed = performance.now() - receivedAtMs;
            if (!Number.isFinite(elapsed) || elapsed < 0 || elapsed > 60_000) return;
            acknowledged.current = view.view_id;
            setPaintMs(elapsed);
            latest.current.onPaint?.({ view_id: view.view_id, receive_to_paint_ms: elapsed, draw_ms: drawn.duration });
          });
        });
      } catch (cause) {
        instance.clear(); setReport(null); setPaintedViewId(null); setPaintMs(null);
        setRenderError(cause instanceof Error ? cause.message : String(cause));
      }
    });
    return () => { cancelled = true; cancelAnimationFrame(frame); cancelAnimationFrame(paintFrame); cancelAnimationFrame(acknowledgeFrame); };
  }, [view, mode, contrast, showTrades, showQuotes, size, visibilityRevision, receivedAtMs, id]);

  useEffect(() => { renderer.current?.drawCursor(view, crosshair, mode); }, [view, crosshair, mode]);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let cursorFrame = 0;
    let wheelFrame = 0;
    let pending: ChartCursor | null = null;
    let wheelDelta = 0;
    let wheelAnchor = 0.5;
    let drag: { x: number; y: number; pointer: number; range: ChartRange; rows: number; width: number; height: number } | null = null;
    const pointerMove = (event: PointerEvent) => {
      const current = latest.current;
      if (!current.view) return;
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      const px = event.clientX - bounds.left - area.x;
      const py = event.clientY - bounds.top - area.y;
      if (px < 0 || px > area.width || py < 0 || py > area.height) pending = null;
      else pending = {
        time_ns: current.mode === 'profile' ? current.crosshair?.time_ns ?? current.view.columns.at(-1)?.time_ns ?? current.view.start_ns : timeAt(Math.min(0.999999, px / area.width), current.view.start_ns, current.view.end_ns),
        price: current.mode === 'cvd' || current.mode === 'ofi' ? null : current.view.prices[rowAt(py / area.height, current.view.rows)] ?? null,
      };
      if (!cursorFrame) cursorFrame = requestAnimationFrame(() => { cursorFrame = 0; latest.current.onCrosshair(pending); });
    };
    const pointerDown = (event: PointerEvent) => {
      const current = latest.current;
      if (!current.view || event.button !== 0) return;
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      drag = { x: event.clientX, y: event.clientY, pointer: event.pointerId, range: { start_ns: current.view.start_ns, end_ns: current.view.end_ns }, rows: current.view.rows, width: area.width, height: area.height };
      element.setPointerCapture(event.pointerId);
      element.dataset.dragging = 'true';
    };
    const pointerUp = (event: PointerEvent) => {
      if (!drag || drag.pointer !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.abs(dx) > 4) latest.current.onRangeChange(shiftedRange(drag.range, -dx / drag.width));
      if (Math.abs(dy) > 4 && latest.current.mode !== 'cvd' && latest.current.mode !== 'ofi') latest.current.onPricePan(Math.round(dy / drag.height * drag.rows));
      drag = null; delete element.dataset.dragging;
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    };
    const pointerCancel = () => { drag = null; delete element.dataset.dragging; };
    const pointerLeave = () => {
      if (!drag) { cancelAnimationFrame(cursorFrame); cursorFrame = 0; latest.current.onCrosshair(null); }
    };
    const wheel = (event: WheelEvent) => {
      const current = latest.current;
      if (!current.view) return;
      event.preventDefault();
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      if (event.shiftKey) { current.onPricePan(event.deltaY > 0 ? -4 : 4); return; }
      wheelAnchor = (event.clientX - bounds.left - area.x) / area.width;
      wheelDelta += event.deltaY;
      if (!wheelFrame) wheelFrame = requestAnimationFrame(() => {
        wheelFrame = 0;
        const now = latest.current;
        if (now.view) now.onRangeChange(zoomedRange(now.view, wheelAnchor, Math.exp(Math.max(-1, Math.min(1, wheelDelta * 0.002))), now.view.bucket_ns));
        wheelDelta = 0;
      });
    };
    const keyboard = (event: KeyboardEvent) => {
      const current = latest.current;
      if (!current.view) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); current.onRangeChange(shiftedRange(current.view, event.key === 'ArrowLeft' ? -0.2 : 0.2));
      } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault(); current.onPricePan(event.key === 'ArrowUp' ? 4 : -4);
      } else if (event.key === '+' || event.key === '=' || event.key === '-') {
        event.preventDefault(); current.onRangeChange(zoomedRange(current.view, 0.5, event.key === '-' ? 1.25 : 0.8, current.view.bucket_ns));
      } else if (event.key === 'Escape') { current.onCrosshair(null); }
    };
    element.addEventListener('pointermove', pointerMove);
    element.addEventListener('pointerdown', pointerDown);
    element.addEventListener('pointerup', pointerUp);
    element.addEventListener('pointercancel', pointerCancel);
    element.addEventListener('lostpointercapture', pointerCancel);
    element.addEventListener('pointerleave', pointerLeave);
    element.addEventListener('wheel', wheel, { passive: false });
    element.addEventListener('keydown', keyboard);
    return () => {
      cancelAnimationFrame(cursorFrame); cancelAnimationFrame(wheelFrame);
      element.removeEventListener('pointermove', pointerMove); element.removeEventListener('pointerdown', pointerDown);
      element.removeEventListener('pointerup', pointerUp); element.removeEventListener('pointercancel', pointerCancel);
      element.removeEventListener('lostpointercapture', pointerCancel); element.removeEventListener('pointerleave', pointerLeave);
      element.removeEventListener('wheel', wheel); element.removeEventListener('keydown', keyboard);
    };
  }, []);

  const column = view ? crosshair ? columnAt(view, crosshair.time_ns) : view.columns.at(-1) : undefined;
  const row = view ? Math.max(0, crosshair?.price ? view.prices.indexOf(crosshair.price) : Math.floor(view.rows / 2)) : 0;
  const price = view?.prices[row];
  const cell = column?.cells[row];
  const metric = mode === 'cvd' || mode === 'ofi' ? column?.[mode] : null;
  const selectedTrades = useMemo(() => view && column ? view.trades.slice(-CHART_LIMITS.trades).filter(trade => BigInt(trade.time_ns) >= BigInt(column.time_ns) && BigInt(trade.time_ns) < BigInt(column.end_ns)) : [], [view, column]);
  const footprintIndex = useMemo(() => new Map(view?.footprints.slice(-CHART_LIMITS.footprints).map(item => [`${item.time_ns}:${item.price}`, item])), [view]);
  const profileIndex = useMemo(() => new Map(view?.profile.slice(0, CHART_LIMITS.rows).map(item => [item.price, item])), [view]);
  const footprint = footprintIndex.get(`${column?.time_ns}:${price}`);
  const profile = price == null ? undefined : profileIndex.get(price);
  const rowStart = Math.max(0, row - 8);
  const reasons = [...new Set([...(view?.reasons ?? []), ...(column?.reasons ?? []), ...(metric?.eligibility.reasons ?? []), ...(mode === 'candles' ? column?.candle?.reasons ?? [] : [])])];
  const available = view && (mode === 'profile' ? view.profile.length > 0 : mode === 'footprint' ? view.footprints.length > 0 : mode === 'candles' ? view.columns.some(item => item.candle?.eligible) : mode === 'cvd' || mode === 'ofi' ? view.columns.some(item => item[mode]?.eligibility.eligible && item[mode]?.value != null) : view.columns.some(item => item.eligible));

  return <section className="m4-charts" data-testid="market-charts" aria-label={MODE_LABEL[mode]} data-mode={mode} data-view-id={view?.view_id ?? ''}
    data-start-ns={view?.start_ns} data-end-ns={view?.end_ns} data-price-min={view?.price_min} data-price-step={view?.price_step}
    data-painted-view-id={paintedViewId ?? ''}
    data-draw-ms={report?.duration.toFixed(3)} data-receive-to-paint-ms={paintMs?.toFixed(3)} data-tile-count={report?.tiles}
    data-tile-allocations={report?.allocated} data-dirty-columns={report?.dirty} data-overlay-points={report?.trades}
    data-pixel-width={report?.pixelWidth} data-pixel-height={report?.pixelHeight}>
    <div className="m4-chart-legend" aria-label="Chart legend">
      <strong>{MODE_LABEL[mode]}</strong>
      {mode === 'heatmap' ? <><span className="m4-bid">Resting bid ▰</span><span className="m4-ask">Resting ask ▰</span><span className="m4-unknown">Plum cell: unknown depth</span></> : null}
      {mode === 'cvd' || mode === 'ofi' ? <span>{metric?.unit ?? 'Unit unavailable'} · signed, viewport-scaled axis</span> : <>
        <span className="m4-buy">Executed buy ●</span><span className="m4-sell">Executed sell ●</span><span className="m4-unknown">Unknown side ◇</span>
      </>}
      <span className="m4-missing-key">Hatch: missing / ineligible</span>
      {mode === 'heatmap' || mode === 'profile' || mode === 'footprint' ? <span title="Fixed linear saturation; incoming data does not rescale old cells">0 → {saturation(contrast)} {view?.quantity_unit ?? 'unit unavailable'} (saturated)</span> : null}
      {showQuotes && mode !== 'profile' && mode !== 'cvd' && mode !== 'ofi' ? <span>Thin teal / amber: best bid / ask</span> : null}
    </div>
    <div className="m4-chart-navigation" aria-label="Chart viewport controls">
      <button disabled={!view} onClick={() => view && onRangeChange(shiftedRange(view, -0.25))} aria-label="Pan chart earlier">Earlier</button>
      <button disabled={!view} onClick={() => view && onRangeChange(shiftedRange(view, 0.25))} aria-label="Pan chart later">Later</button>
      <button disabled={!view} onClick={() => view && onRangeChange(zoomedRange(view, 0.5, 0.8, view.bucket_ns))} aria-label="Zoom chart in">+</button>
      <button disabled={!view} onClick={() => view && onRangeChange(zoomedRange(view, 0.5, 1.25, view.bucket_ns))} aria-label="Zoom chart out">−</button>
      <button disabled={!view || mode === 'cvd' || mode === 'ofi'} onClick={() => onPricePan(8)} aria-label="Pan prices higher">Price ↑</button>
      <button disabled={!view || mode === 'cvd' || mode === 'ofi'} onClick={() => onPricePan(-8)} aria-label="Pan prices lower">Price ↓</button>
      <span id={`${id}-help`}>Drag: pan · wheel: time zoom · Shift-wheel: price · focused arrows / ±</span>
      {loading ? <span role="status">Updating…</span> : null}
    </div>
    {error || renderError ? <p className="m4-chart-error" role="alert">{error ?? renderError}{error && renderError ? ` · ${renderError}` : ''}</p> : null}
    <div className="m4-chart-plot" ref={host} tabIndex={0} role="group" aria-label={`${MODE_LABEL[mode]} interactive plot`} aria-describedby={`${id}-help`}>
      <canvas ref={base} data-testid="liquidity-canvas" aria-hidden="true" className="m4-chart-base" />
      <canvas ref={overlay} aria-hidden="true" className="m4-chart-overlay" />
      <canvas ref={cursor} aria-hidden="true" className="m4-chart-cursor" />
      {!view || !available ? <div className="m4-chart-empty">{!view ? loading ? 'Loading observed history…' : 'No visualization received.' : `No eligible ${MODE_LABEL[mode].toLowerCase()} in this window.`}<small>Missing evidence is not zero. No inferred or fixture history is drawn.</small></div> : null}
      {view && crosshair ? <div className="m4-chart-tooltip" role="tooltip">
        <b>{crosshair.time_ns} ns</b><span>Price cell {crosshair.price ?? 'not applicable'}</span>
        {mode === 'heatmap' ? <span>Resting B {column?.eligible ? cell?.[0] ?? 'unknown' : 'ineligible'} · A {column?.eligible ? cell?.[1] ?? 'unknown' : 'ineligible'} {view.quantity_unit}</span> : null}
        {metric ? <span>{metric.name}: {metric.eligibility.eligible ? metric.value ?? 'unavailable' : 'ineligible'} {metric.unit}</span> : null}
        {mode === 'candles' ? <span>O {column?.candle?.open ?? '—'} H {column?.candle?.high ?? '—'} L {column?.candle?.low ?? '—'} C {column?.candle?.close ?? '—'}</span> : null}
        {mode === 'footprint' || mode === 'profile' ? <span>B {(mode === 'profile' ? profile : footprint)?.buy_base ?? 'unavailable'} · S {(mode === 'profile' ? profile : footprint)?.sell_base ?? 'unavailable'} · ? {(mode === 'profile' ? profile : footprint)?.unknown_base ?? 'unavailable'} {view.quantity_unit}</span> : null}
        {selectedTrades.slice(-3).map(trade => <span key={trade.id}>{trade.taker_side} {trade.base_quantity ?? 'unknown size'} @ {trade.price} · {trade.time_ns} ns{trade.is_subscription_history ? ' · subscription history' : ''}</span>)}
        {!column ? <span>No sampled column at this time</span> : <span>{column.eligible ? 'Book sample eligible' : 'Book sample ineligible'} · {column.segment_id}</span>}
      </div> : null}
    </div>
    <div className="m4-chart-quality" role="status">
      <span>{view ? `${view.complete_window ? 'Complete window' : 'Partial window'}${view.truncated ? ' · retention truncated' : ''} · ${view.quantity_unit} · ${view.sampling}` : 'Awaiting observed source evidence'}</span>
      {reasons.length ? <span className="m4-chart-warning">{reasons.join(' · ')}</span> : null}
      {view && (view.columns.length > CHART_LIMITS.columns || (report?.omittedTrades ?? 0) > 0 || (report?.omittedCells ?? 0) > 0) ? <span className="m4-chart-warning">Display cap: newest {CHART_LIMITS.columns} columns / {CHART_LIMITS.trades} trades / {CHART_LIMITS.footprints} footprint cells. Native collection unchanged.</span> : null}
    </div>
    {view ? <details className="m4-chart-readout"><summary>Exact data / quality · {column?.time_ns ?? 'no sampled time'} ns · price {price ?? 'unavailable'}</summary>
      <dl><dt>Clock / instrument</dt><dd>{view.clock_id} / {view.instrument_id}</dd><dt>Window (source monotonic ns)</dt><dd>{view.start_ns} → {view.end_ns}</dd><dt>Retention starts</dt><dd>{view.retained_start_ns ?? 'Unavailable'}</dd><dt>Grid / price quantum</dt><dd>{view.grid_id} / {view.price_step}</dd><dt>Selected sample</dt><dd>{column ? `${column.time_ns} → ${column.end_ns} · ${column.segment_id}` : 'No sample at cursor'}</dd><dt>Best bid / ask</dt><dd>{column?.best_bid ?? 'Unknown'} / {column?.best_ask ?? 'Unknown'}</dd></dl>
      <label>Exact price row <select value={price ?? ''} onChange={event => onCrosshair({ time_ns: crosshair?.time_ns ?? column?.time_ns ?? view.start_ns, price: event.target.value })}>{view.prices.slice(0, CHART_LIMITS.rows).map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>Exact sampled time <select value={column?.time_ns ?? ''} onChange={event => onCrosshair({ time_ns: event.target.value, price: crosshair?.price ?? price ?? null })}><option value="" disabled>No sampled column</option>{view.columns.slice(-CHART_LIMITS.columns).map(item => <option key={item.id} value={item.time_ns}>{item.time_ns} ns · {item.eligible ? 'eligible' : 'ineligible'}</option>)}</select></label>
      {mode === 'cvd' || mode === 'ofi' ? <><p>{metric ? `${metric.name}: ${metric.value ?? 'Unavailable'} ${metric.unit} · ${metric.eligibility.eligible ? 'Eligible' : 'Ineligible'}` : 'No metric observation at cursor.'}</p>{metric ? <details><summary>Formula, window, quality and exact references</summary><pre>{JSON.stringify(metric, null, 2)}</pre></details> : null}</> : mode === 'candles' ? <p>Open {column?.candle?.open ?? '—'} · high {column?.candle?.high ?? '—'} · low {column?.candle?.low ?? '—'} · close {column?.candle?.close ?? '—'} · executed volume {column?.candle?.volume ?? '—'} {view.quantity_unit} · {column?.candle?.eligible ? 'Eligible' : 'Unavailable / ineligible'}</p> : <table><caption>Exact server aggregates · {view.quantity_unit} · up to 16 rows around selection; unknown ≠ zero</caption><thead><tr><th>Price lower bound</th>{mode === 'heatmap' ? <><th>Resting bid</th><th>Resting ask</th></> : <><th>Executed buy</th><th>Executed sell</th><th>Unknown side</th></>}</tr></thead><tbody>{view.prices.slice(rowStart, rowStart + 16).map((value, index) => {
        const amounts = mode === 'profile' ? profileIndex.get(value) : footprintIndex.get(`${column?.time_ns}:${value}`);
        return <tr key={value}><th scope="row">{value}</th>{mode === 'heatmap' ? <><td>{column?.eligible ? column.cells[rowStart + index]?.[0] ?? 'Unknown' : 'Ineligible'}</td><td>{column?.eligible ? column.cells[rowStart + index]?.[1] ?? 'Unknown' : 'Ineligible'}</td></> : <><td>{amounts?.buy_base ?? 'No observation'}</td><td>{amounts?.sell_base ?? 'No observation'}</td><td>{amounts?.unknown_base ?? 'No observation'}</td></>}</tr>;
      })}</tbody></table>}
      <table><caption>Executed prints at selected time bucket · newest {Math.min(20, selectedTrades.length)} of {selectedTrades.length} displayed candidates · hollow/dashed = subscription history or unknown size</caption><thead><tr><th>Receive ns</th><th>Price</th><th>Base quantity</th><th>Taker side</th><th>Provenance</th></tr></thead><tbody>{selectedTrades.slice(-20).map(trade => <tr key={trade.id}><td>{trade.time_ns}</td><td>{trade.price}</td><td>{trade.base_quantity ?? 'Unknown'}</td><td>{trade.taker_side}</td><td>{trade.segment_id} · {trade.is_subscription_history ? 'Subscription history (excluded from observed flow)' : 'Observed execution'} · ordinal {trade.asof.ingest_ordinal}</td></tr>)}</tbody></table>
      {column?.asof ? <details><summary>Sample reference</summary><pre>{JSON.stringify(column.asof, null, 2)}</pre></details> : null}
    </details> : null}
    <footer className="m4-chart-performance">{report ? `Draw CPU ${report.duration.toFixed(2)} ms · dirty ${report.dirty} · tiles ${report.tiles}/${CHART_LIMITS.tiles} · trades ${report.trades} · receive→paint ${paintMs == null ? 'unmeasured' : `${paintMs.toFixed(1)} ms`}` : 'Render timing unavailable'} · paint opportunities, not GPU completion. Time labels are offsets from window start; no wall-clock inference.</footer>
  </section>;
}
