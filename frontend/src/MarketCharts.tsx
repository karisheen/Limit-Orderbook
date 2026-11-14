import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { VisualGrid } from './api';
import { chartTimeZoneLabel, formatChartDateContext, formatChartTime, projectReceiveTime } from './chart-time';
import type { ChartTimeZone } from './chart-time';
import { timestamp } from './format';
import { CHART_LIMITS, chartRowViewport, columnAt, footprintCenterRow, plotArea, priceCellIndex, priceRowProjector, rowAt, saturation, shiftedRange, timeAt, zoomedRange } from './market-charts-model';
import type { ChartCursor, ChartMode, ChartRange, ChartRowViewport } from './market-charts-model';
export type { ChartCursor } from './market-charts-model';
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
  timeZone: ChartTimeZone;
  footprintOverview?: boolean;
  interactionActive?: boolean;
  interactionScope?: string;
  priceResetToken?: string;
  quoteUnit?: string;
  crosshair: ChartCursor | null;
  onCrosshair: (value: ChartCursor | null) => void;
  onRangeChange: (range: ChartRange) => void;
  onPricePan: (rows: number) => void;
  receivedAtMs: number | null;
  onPaint?: (value: { view_id: string; receive_to_paint_ms: number; draw_ms: number }) => void;
  /** Reports the mode and crop owned by the acknowledged draw; null means a real canvas clear. */
  onPresented?: (view: VisualGrid | null, receivedAtMs: number | null, rowViewport: ChartRowViewport | null, mode: ChartMode | null) => void;
  freshness?: ReactNode;
  inspectorOpen?: boolean;
  inspectorId?: string;
  onInspectorClose?: () => void;
  diagnostics?: ReactNode;
}

const MODE_LABEL: Record<ChartMode, string> = {
  heatmap: 'Resting liquidity', candles: 'Executed-trade candles', footprint: 'Executed-volume footprint',
  profile: 'Visible-window executed profile', cvd: 'Cumulative volume delta', ofi: 'Order-flow imbalance',
};

type DrawnChart = { view: VisualGrid; mode: ChartMode; rowViewport: ChartRowViewport; timeZone: ChartTimeZone; identity: string };

/** Keep native disclosure state while unmounting its expensive closed body. */
function InspectorDisclosure({ active, className, summary, children }: {
  active: boolean; className?: string; summary: ReactNode; children: () => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return <details className={className} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{summary}</summary>{active && open ? children() : null}
  </details>;
}

function ExactJson({ value }: { value: unknown }) {
  const text = useMemo(() => JSON.stringify(value, null, 2), [value]);
  return <pre>{text}</pre>;
}

export function MarketCharts(props: MarketChartsProps): ReactNode {
  const { view: requestedView, loading, error, mode: requestedMode, contrast, showTrades, showQuotes, timeZone: requestedTimeZone, crosshair, receivedAtMs, onCrosshair, onRangeChange, onPricePan } = props;
  const id = useId();
  const host = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const cursor = useRef<HTMLCanvasElement>(null);
  const inspectorClose = useRef<HTMLButtonElement>(null);
  const renderer = useRef<MarketRenderer | null>(null);
  const latest = useRef(props);
  const visible = useRef(true);
  const acknowledged = useRef<string | null>(null);
  const [size, setSize] = useState({ width: 1, height: 1, dpr: window.devicePixelRatio });
  const [visibilityRevision, setVisibilityRevision] = useState(0);
  const [themeRevision, setThemeRevision] = useState(0);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [report, setReport] = useState<RenderReport | null>(null);
  const [paintMs, setPaintMs] = useState<number | null>(null);
  const [paintedViewId, setPaintedViewId] = useState<string | null>(null);
  const drawnRef = useRef<DrawnChart | null>(null);
  const [drawnChart, setDrawnChart] = useState<DrawnChart | null>(null);
  const [exactOpen, setExactOpen] = useState(false);
  const [metricOpen, setMetricOpen] = useState(false);
  const [sampleOpen, setSampleOpen] = useState(false);
  const identity = requestedView ? JSON.stringify([props.interactionScope, requestedView.instrument_id, requestedView.generation, requestedView.clock_id, requestedView.time_axis.source_identity, requestedView.time_axis.scope_id]) : '';
  const presented = drawnChart?.identity === identity && !error ? drawnChart : null;
  const view = presented?.view ?? null;
  const mode = presented?.mode ?? requestedMode;
  const timeZone = presented?.timeZone ?? requestedTimeZone;
  const [heldCenter, setHeldCenter] = useState<{ identity: string; origin: string; row: number; occupied: boolean } | null>(null);
  const centerIdentity = requestedView ? JSON.stringify([requestedView.instrument_id, requestedView.generation, requestedView.clock_id, requestedView.time_axis.source_identity, requestedView.time_axis.scope_id, requestedView.price_step, props.priceResetToken]) : '';
  const occupiedCenter = useMemo(() => requestedMode === 'footprint' && requestedView ? footprintCenterRow(requestedView) : null, [requestedMode, requestedView]);
  const activeCenter = heldCenter?.identity === centerIdentity ? heldCenter : null;
  useEffect(() => {
    if (!requestedView || requestedMode !== 'footprint' || activeCenter?.occupied) return;
    if (!activeCenter || occupiedCenter !== null) {
      setHeldCenter({ identity: centerIdentity, origin: requestedView.price_min, row: occupiedCenter ?? (requestedView.rows - 1) / 2, occupied: occupiedCenter !== null });
    }
  }, [requestedView, requestedMode, activeCenter, centerIdentity, occupiedCenter]);
  const centerRow = requestedView && activeCenter ? priceRowProjector(requestedView.price_min, requestedView.price_step)(activeCenter.origin) + activeCenter.row : occupiedCenter;
  const requestedRowViewport = useMemo(() => requestedView ? chartRowViewport(requestedView, requestedMode, plotArea(size.width, size.height).height, centerRow, props.footprintOverview ?? false) : { first: 0, count: 1 }, [requestedView, requestedMode, size, centerRow, props.footprintOverview]);
  const rowViewport = presented?.rowViewport ?? { first: 0, count: 1 };
  const panPrice = useCallback((rows: number) => {
    if (requestedMode === 'cvd' || requestedMode === 'ofi') return;
    if (!requestedView || requestedMode !== 'footprint') { onPricePan(rows); return; }
    const delta = Math.round(rows);
    const visibleCenter = requestedRowViewport.first + (requestedRowViewport.count - 1) / 2;
    const target = visibleCenter + delta;
    setHeldCenter({ identity: centerIdentity, origin: requestedView.price_min, row: target, occupied: true });
    const nextFirst = requestedRowViewport.first + delta;
    const overflow = nextFirst < 0 ? nextFirst : Math.max(0, nextFirst + requestedRowViewport.count - requestedView.rows);
    if (overflow !== 0) onPricePan(overflow);
  }, [requestedView, requestedMode, onPricePan, requestedRowViewport, centerIdentity]);
  const pan = useRef(panPrice);
  pan.current = panPrice;
  useLayoutEffect(() => { latest.current = props; });
  useEffect(() => {
    if (props.inspectorOpen) inspectorClose.current?.focus();
  }, [props.inspectorOpen]);
  const clearPresentation = useCallback(() => {
    setReport(null); setPaintMs(null); setPaintedViewId(null);
    drawnRef.current = null; setDrawnChart(null);
    acknowledged.current = null;
    latest.current.onPresented?.(null, null, null, null);
  }, []);
  useLayoutEffect(() => {
    renderer.current?.clear();
    clearPresentation();
  }, [identity, error, clearPresentation]);

  useEffect(() => {
    const element = host.current;
    if (!element || !base.current || !overlay.current || !cursor.current) return;
    try { renderer.current = new MarketRenderer(base.current, overlay.current, cursor.current); }
    catch (cause) { setRenderError(cause instanceof Error ? cause.message : String(cause)); }
    const resize = () => {
      const bounds = element.getBoundingClientRect();
      const dpr = window.devicePixelRatio;
      setSize(previous => previous.width === bounds.width && previous.height === bounds.height && previous.dpr === dpr ? previous : { width: bounds.width, height: bounds.height, dpr });
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    let resolution: MediaQueryList;
    const resolutionChange = () => {
      resolution?.removeEventListener('change', resolutionChange);
      resize();
      resolution = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      resolution.addEventListener('change', resolutionChange);
    };
    resolutionChange();
    const intersection = new IntersectionObserver(entries => {
      const next = entries.at(-1)?.isIntersecting ?? false;
      if (visible.current !== next) { visible.current = next; setVisibilityRevision(value => value + 1); }
    });
    intersection.observe(element);
    const visibility = () => setVisibilityRevision(value => value + 1);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('resize', resize);
    const themeObserver = new MutationObserver(() => {
      if (renderer.current?.updateTheme()) setThemeRevision(value => value + 1);
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'data-theme'] });
    return () => {
      observer.disconnect(); intersection.disconnect();
      themeObserver.disconnect();
      resolution.removeEventListener('change', resolutionChange);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('resize', resize);
      renderer.current?.dispose(); renderer.current = null;
      clearPresentation();
      performance.clearMarks(`lob:chart-start:${id}`);
      performance.clearMarks(`lob:chart-end:${id}`);
      performance.clearMeasures('lob:chart-draw');
    };
  }, [id, clearPresentation]);

  useEffect(() => {
    let cancelled = false;
    let frame = 0;
    let paintFrame = 0;
    let acknowledgeFrame = 0;
    if (!requestedView || error) { renderer.current?.clear(); clearPresentation(); return; }
    if (document.visibilityState !== 'visible' || !visible.current || size.width <= 1 || size.height <= 1) return;
    frame = requestAnimationFrame(() => {
      const instance = renderer.current;
      if (!instance || cancelled || document.visibilityState !== 'visible' || !visible.current) return;
      try {
        if (instance.resize(size.width, size.height, size.dpr)) clearPresentation();
        performance.clearMarks(`lob:chart-start:${id}`);
        performance.clearMarks(`lob:chart-end:${id}`);
        performance.mark(`lob:chart-start:${id}`);
        const drawn = instance.render(requestedView, { mode: requestedMode, contrast, showTrades, showQuotes, timeZone: requestedTimeZone, rowViewport: requestedRowViewport, quoteUnit: props.quoteUnit });
        instance.drawCursor(requestedView, latest.current.crosshair, requestedMode, requestedRowViewport);
        const tuple = { view: requestedView, mode: requestedMode, rowViewport: requestedRowViewport, timeZone: requestedTimeZone, identity };
        drawnRef.current = tuple;
        setDrawnChart(tuple);
        performance.mark(`lob:chart-end:${id}`);
        performance.clearMeasures('lob:chart-draw');
        performance.measure('lob:chart-draw', `lob:chart-start:${id}`, `lob:chart-end:${id}`);
        setReport(drawn); setRenderError(null);
        // Two visible animation-frame opportunities, not a GPU completion claim.
        paintFrame = requestAnimationFrame(() => {
          acknowledgeFrame = requestAnimationFrame(() => {
            if (cancelled || drawnRef.current !== tuple || document.visibilityState !== 'visible' || !visible.current) return;
            setPaintedViewId(requestedView.view_id);
            latest.current.onPresented?.(requestedView, receivedAtMs, requestedRowViewport, requestedMode);
            if (receivedAtMs == null || acknowledged.current === requestedView.view_id) return;
            const elapsed = performance.now() - receivedAtMs;
            if (!Number.isFinite(elapsed) || elapsed < 0 || elapsed > 60_000) return;
            acknowledged.current = requestedView.view_id;
            setPaintMs(elapsed);
            latest.current.onPaint?.({ view_id: requestedView.view_id, receive_to_paint_ms: elapsed, draw_ms: drawn.duration });
          });
        });
      } catch (cause) {
        instance.clear(); clearPresentation();
        setRenderError(cause instanceof Error ? cause.message : String(cause));
      }
    });
    return () => { cancelled = true; cancelAnimationFrame(frame); cancelAnimationFrame(paintFrame); cancelAnimationFrame(acknowledgeFrame); };
  }, [requestedView, requestedMode, contrast, showTrades, showQuotes, requestedTimeZone, requestedRowViewport, props.quoteUnit, size, visibilityRevision, themeRevision, receivedAtMs, id, identity, error, clearPresentation]);

  useEffect(() => {
    if (document.visibilityState !== 'visible' || !visible.current || !presented) return;
    try { renderer.current?.drawCursor(presented.view, crosshair, presented.mode, presented.rowViewport); }
    catch (cause) {
      renderer.current?.clear(); clearPresentation();
      setRenderError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [presented, crosshair, visibilityRevision, clearPresentation]);

  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    let cursorFrame = 0;
    let wheelFrame = 0;
    let pending: ChartCursor | null = null;
    let wheelDelta = 0;
    let wheelAnchor = 0.5;
    let drag: { x: number; y: number; pointer: number; range: ChartRange; rows: number; width: number; height: number } | null = null;
    const pointerMove = (event: PointerEvent) => {
      const current = drawnRef.current;
      if (!current || document.visibilityState !== 'visible' || !visible.current || drag && drag.pointer !== event.pointerId || !event.isPrimary) return;
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      const px = event.clientX - bounds.left - area.x;
      const py = event.clientY - bounds.top - area.y;
      if (px < 0 || px > area.width || py < 0 || py > area.height) pending = null;
      else pending = {
        time_ns: current.mode === 'profile' ? latest.current.crosshair?.time_ns ?? current.view.columns.at(-1)?.time_ns ?? current.view.start_ns : timeAt(Math.min(0.999999, px / area.width), current.view.start_ns, current.view.end_ns),
        price: current.mode === 'cvd' || current.mode === 'ofi' ? null : current.view.prices[current.rowViewport.first + rowAt(py / area.height, current.rowViewport.count)] ?? null,
      };
      if (!cursorFrame) cursorFrame = requestAnimationFrame(() => { cursorFrame = 0; latest.current.onCrosshair(pending); });
    };
    const pointerDown = (event: PointerEvent) => {
      const current = drawnRef.current;
      if (event.button !== 0 || !event.isPrimary || drag || latest.current.interactionActive === false || !visible.current) return;
      element.focus({ preventScroll: true });
      if (!current) return;
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      drag = { x: event.clientX, y: event.clientY, pointer: event.pointerId, range: { start_ns: current.view.start_ns, end_ns: current.view.end_ns }, rows: current.rowViewport.count, width: area.width, height: area.height };
      element.setPointerCapture(event.pointerId);
      element.dataset.dragging = 'true';
    };
    const pointerUp = (event: PointerEvent) => {
      if (!drag || drag.pointer !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.abs(dx) > 4) latest.current.onRangeChange(shiftedRange(drag.range, -dx / drag.width));
      if (Math.abs(dy) > 4 && latest.current.mode !== 'cvd' && latest.current.mode !== 'ofi') pan.current(Math.round(dy / drag.height * drag.rows));
      drag = null; delete element.dataset.dragging;
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    };
    const pointerCancel = (event?: PointerEvent) => {
      if (!drag || event && drag.pointer !== event.pointerId) return;
      const pointer = drag.pointer;
      drag = null; delete element.dataset.dragging;
      if (element.hasPointerCapture(pointer)) element.releasePointerCapture(pointer);
      cancelAnimationFrame(cursorFrame); cursorFrame = 0;
      latest.current.onCrosshair(null);
    };
    const pointerLeave = (event: PointerEvent) => {
      if (event.relatedTarget instanceof Node && element.contains(event.relatedTarget)) return;
      // Clear before React's delegated pointer-enter publishes another pane's
      // cursor. Native pointerleave fires after that publication and erases it.
      if (!drag) { cancelAnimationFrame(cursorFrame); cursorFrame = 0; latest.current.onCrosshair(null); }
    };
    const wheel = (event: WheelEvent) => {
      const current = latest.current;
      if (!current.view || current.interactionActive === false || !visible.current || document.visibilityState !== 'visible' || event.ctrlKey || event.metaKey || event.altKey
        || !Number.isFinite(event.deltaY) || !Number.isFinite(event.deltaX) || event.deltaY === 0 || Math.abs(event.deltaX) > Math.abs(event.deltaY)
        || ![0, 1, 2].includes(event.deltaMode) || event.shiftKey && (current.mode === 'cvd' || current.mode === 'ofi')) return;
      const bounds = element.getBoundingClientRect();
      const area = plotArea(bounds.width, bounds.height);
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? area.height : 1);
      event.preventDefault();
      if (event.shiftKey) { pan.current(delta > 0 ? -4 : 4); return; }
      wheelAnchor = (event.clientX - bounds.left - area.x) / area.width;
      wheelDelta += delta;
      if (!wheelFrame) wheelFrame = requestAnimationFrame(() => {
        wheelFrame = 0;
        const now = latest.current;
        if (now.view && now.interactionActive !== false && visible.current && document.visibilityState === 'visible' && wheelDelta !== 0) now.onRangeChange(zoomedRange(now.view, wheelAnchor, Math.exp(Math.max(-1, Math.min(1, wheelDelta * 0.002))), now.view.bucket_ns));
        wheelDelta = 0;
      });
    };
    const keyboard = (event: KeyboardEvent) => {
      const current = latest.current;
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.target instanceof Element && event.target.closest('form,button,input,select,textarea,summary,[contenteditable]:not([contenteditable="false"]),[role="menu"],[role="menuitem"],[role="textbox"],[role="combobox"],[role="listbox"]')) return;
      if (!current.view || current.interactionActive === false) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); current.onRangeChange(shiftedRange(current.view, event.key === 'ArrowLeft' ? -0.2 : 0.2));
      } else if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && current.mode !== 'cvd' && current.mode !== 'ofi') {
        event.preventDefault(); pan.current(event.key === 'ArrowUp' ? 4 : -4);
      } else if (event.key === '+' || event.key === '=' || event.key === '-') {
        event.preventDefault(); current.onRangeChange(zoomedRange(current.view, 0.5, event.key === '-' ? 1.25 : 0.8, current.view.bucket_ns));
      }
    };
    element.addEventListener('pointermove', pointerMove);
    element.addEventListener('pointerdown', pointerDown);
    element.addEventListener('pointerup', pointerUp);
    element.addEventListener('pointercancel', pointerCancel);
    element.addEventListener('lostpointercapture', pointerCancel);
    element.addEventListener('pointerout', pointerLeave);
    element.addEventListener('wheel', wheel, { passive: false });
    element.addEventListener('keydown', keyboard);
    return () => {
      cancelAnimationFrame(cursorFrame); cancelAnimationFrame(wheelFrame);
      pointerCancel();
      element.removeEventListener('pointermove', pointerMove); element.removeEventListener('pointerdown', pointerDown);
      element.removeEventListener('pointerup', pointerUp); element.removeEventListener('pointercancel', pointerCancel);
      element.removeEventListener('lostpointercapture', pointerCancel); element.removeEventListener('pointerout', pointerLeave);
      element.removeEventListener('wheel', wheel); element.removeEventListener('keydown', keyboard);
    };
  }, [props.interactionScope, identity, requestedMode]);

  const column = view ? crosshair ? columnAt(view, crosshair.time_ns) : view.columns.at(-1) : undefined;
  const clockContextTime = view && view.time_axis.available_start_ns !== null && BigInt(view.time_axis.available_start_ns) > BigInt(view.start_ns)
    ? view.time_axis.available_start_ns : view?.start_ns;
  const clockContext = view && clockContextTime !== undefined && BigInt(clockContextTime) <= BigInt(view.end_ns)
    ? formatChartDateContext(view, clockContextTime, timeZone) : `Clock time unavailable · ${chartTimeZoneLabel(timeZone)}`;
  const nativeRow = view ? crosshair?.price ? priceCellIndex(view, crosshair.price) : rowViewport.first + Math.floor(rowViewport.count / 2) : -1;
  const row = nativeRow >= rowViewport.first && nativeRow < rowViewport.first + rowViewport.count ? nativeRow : -1;
  const price = view?.prices[row];
  const cell = column?.cells[row];
  const metric = mode === 'cvd' || mode === 'ofi' ? column?.[mode] : null;
  const exactActive = !!props.inspectorOpen && exactOpen;
  const needsSelection = exactActive || crosshair !== null;
  const selectedTrades = useMemo(() => needsSelection && view && column ? view.trades.slice(-CHART_LIMITS.trades).filter(trade => BigInt(trade.time_ns) >= BigInt(column.time_ns) && BigInt(trade.time_ns) < BigInt(column.end_ns)) : [], [view, column, needsSelection]);
  const footprintIndex = useMemo(() => new Map(mode === 'footprint' ? view?.footprints.slice(-CHART_LIMITS.footprints).map(item => [`${item.time_ns}:${item.price}`, item]) : []), [view, mode]);
  const profileIndex = useMemo(() => new Map(mode === 'profile' ? view?.profile.slice(0, CHART_LIMITS.rows).map(item => [item.price, item]) : []), [view, mode]);
  const priceOptions = useMemo(() => exactActive ? view?.prices.slice(rowViewport.first, rowViewport.first + rowViewport.count).map(value => <option key={value} value={value}>{value}</option>) : null, [exactActive, view, rowViewport.first, rowViewport.count]);
  const timeOptions = useMemo(() => exactActive ? view?.columns.slice(-CHART_LIMITS.columns).map(item => <option key={item.id} value={item.time_ns}>{item.time_ns} ns · {item.eligible ? 'eligible' : 'ineligible'}</option>) : null, [exactActive, view]);
  const footprint = footprintIndex.get(`${column?.time_ns}:${price}`);
  const profile = price == null ? undefined : profileIndex.get(price);
  const rowStart = Math.max(rowViewport.first, row - 8);
  const reasons = [...new Set([...(view?.reasons ?? []), ...(view?.time_axis.reasons ?? []), ...(column?.reasons ?? []), ...(metric?.eligibility.reasons ?? []), ...(mode === 'candles' ? column?.candle?.reasons ?? [] : [])])];
  const available = view && (mode === 'profile' ? view.profile.length > 0 : mode === 'footprint' ? view.footprints.length > 0 : mode === 'candles' ? view.columns.some(item => item.candle?.eligible) : mode === 'cvd' || mode === 'ofi' ? view.columns.some(item => item[mode]?.eligibility.eligible && item[mode]?.value != null) : view.columns.some(item => item.eligible));
  const denseFootprint = mode === 'footprint' && report?.compactFootprint;
  const displayCapped = !!view && (view.columns.length > CHART_LIMITS.columns || (report?.omittedTrades ?? 0) > 0 || (report?.omittedCells ?? 0) > 0);
  const windowQuality = !view ? 'Unavailable' : !available ? `${view.complete_window ? '' : 'Partial · '}Ineligible` : view.complete_window ? 'Complete' : 'Partial';
  const qualityDetails = `${view ? `${view.complete_window ? 'Complete window' : 'Partial window'}${view.truncated ? ' · truncated' : ''}${!available ? ' · no eligible evidence for this chart mode' : ''}` : 'Awaiting source evidence'}${displayCapped ? ' · display capped; native collection unchanged' : ''}${reasons.length ? ` · ${reasons.length} quality reasons: ${reasons.join('; ')}` : ''}`;

  return <section className="m4-charts" data-testid="market-charts" aria-label={MODE_LABEL[requestedMode]} data-mode={requestedMode} data-view-id={requestedView?.view_id ?? ''}
    data-drawn-view-id={view?.view_id ?? ''} data-drawn-mode={presented?.mode ?? ''} data-drawn-row-first={presented?.rowViewport.first} data-drawn-row-count={presented?.rowViewport.count}
    data-start-ns={view?.start_ns} data-end-ns={view?.end_ns} data-price-min={view?.price_min} data-price-step={view?.price_step}
    data-painted-view-id={paintedViewId ?? ''}
    data-row-first={rowViewport.first} data-row-count={rowViewport.count}
    data-draw-ms={report?.duration.toFixed(3)} data-receive-to-paint-ms={paintMs?.toFixed(3)} data-tile-count={report?.tiles}
    data-tile-allocations={report?.allocated} data-dirty-columns={report?.dirty} data-overlay-points={report?.trades}
    data-pixel-width={report?.pixelWidth} data-pixel-height={report?.pixelHeight}>
    {error || renderError ? <p className="m4-chart-error" role="alert">{error ?? renderError}{error && renderError ? ` · ${renderError}` : ''}</p> : null}
    <div className="m4-chart-body">
    <div className="m4-chart-plot" ref={host} tabIndex={0} role="group" aria-label={`${MODE_LABEL[mode]} interactive plot`} aria-describedby={`${id}-help`}>
      <canvas ref={base} data-testid="liquidity-canvas" aria-hidden="true" className="m4-chart-base" />
      <canvas ref={overlay} aria-hidden="true" className="m4-chart-overlay" />
      <canvas ref={cursor} aria-hidden="true" className="m4-chart-cursor" />
      <span id={`${id}-help`} className="m4-chart-help">Drag: pan · vertical wheel: time zoom · Shift-wheel: price (not CVD/OFI) · focused arrows / ±. Inspector contains navigation buttons. Scroll the page outside the plot.</span>
      {!view || !available ? <div className="m4-chart-empty">{!view ? loading ? 'Loading observed history…' : 'No visualization received.' : `No eligible ${MODE_LABEL[mode].toLowerCase()} in this window.`}<small>Missing evidence is not zero. No inferred or fixture history is drawn.</small></div> : null}
      {view && crosshair && visible.current && document.visibilityState === 'visible' ? <div className="m4-chart-tooltip" role="tooltip" data-view-id={view.view_id} data-mode={mode}>
        <b>{formatChartTime(view, crosshair.time_ns, timeZone, true)}</b><span>Exact price {crosshair.price ?? 'not applicable'}</span>
        {row >= 0 ? <span>Chart bucket {price} · step {view.price_step}</span> : null}
        {row < 0 ? <span>Price outside the current viewport; row evidence unavailable</span> : null}
        {mode === 'heatmap' ? <span>Resting B {column?.eligible ? cell?.[0] ?? 'unknown' : 'ineligible'} · A {column?.eligible ? cell?.[1] ?? 'unknown' : 'ineligible'} {view.quantity_unit}</span> : null}
        {metric ? <span>{metric.name}: {metric.eligibility.eligible ? metric.value ?? 'unavailable' : 'ineligible'} {metric.unit}</span> : null}
        {mode === 'candles' ? <span>O {column?.candle?.open ?? '—'} H {column?.candle?.high ?? '—'} L {column?.candle?.low ?? '—'} C {column?.candle?.close ?? '—'}</span> : null}
        {mode === 'footprint' || mode === 'profile' ? <span>SELL {(mode === 'profile' ? profile : footprint)?.sell_base ?? 'unavailable'} · BUY {(mode === 'profile' ? profile : footprint)?.buy_base ?? 'unavailable'} · ? {(mode === 'profile' ? profile : footprint)?.unknown_base ?? 'unavailable'} {view.quantity_unit}</span> : null}
        {selectedTrades.slice(-3).map(trade => <span key={trade.id}>{trade.taker_side} {trade.base_quantity ?? 'unknown size'} @ {trade.price} · {formatChartTime(view, trade.time_ns, timeZone, true)}{trade.is_subscription_history ? ' · subscription history' : ''}</span>)}
        {!column ? <span>No sampled column at this time</span> : <span>{column.eligible ? 'Book sample eligible' : 'Book sample ineligible'} · {column.segment_id}</span>}
      </div> : null}
    </div>
    <aside id={props.inspectorId} className="m4-chart-inspector" aria-label="Chart Inspector" hidden={!props.inspectorOpen}>
    <div className="m4-chart-inspector-header"><strong>Inspector</strong>{props.onInspectorClose ? <button ref={inspectorClose} aria-label="Close Chart Inspector" onClick={props.onInspectorClose}>Close</button> : null}</div>
    <InspectorDisclosure active={!!props.inspectorOpen} className="m4-chart-guide" summary={<>Legend & navigation{loading ? ' · updating' : ''}</>}>{() => <>
    <div className="m4-chart-legend" aria-label="Chart legend">
      <strong>{MODE_LABEL[mode]}</strong>
      {mode === 'footprint' && view ? <span>SELL left · BUY right · ? unknown · {props.footprintOverview ? 'Full native grid' : `Rows ${rowViewport.first + 1}–${rowViewport.first + rowViewport.count} / ${view.rows}`}{denseFootprint ? ' · Compact cells; exact values below' : ''}</span> : null}
      {mode === 'heatmap' ? <><span className="m4-bid">Resting bid ▰</span><span className="m4-ask">Resting ask ▰</span><span className="m4-unknown">Hatch: unknown depth</span></> : null}
      {mode === 'heatmap' && report?.compactLiquidity ? <span>Compressed price rows: maximum intensity per pixel; inspect exact source cells below. Hatching includes unknown contributors.</span> : null}
      {mode === 'cvd' || mode === 'ofi' ? <span>{metric?.unit ?? 'Unit unavailable'} · signed, viewport-scaled axis</span> : <>
        <span className="m4-buy">Executed buy ●</span><span className="m4-sell">Executed sell ●</span><span className="m4-unknown">Unknown side ◇</span>
      </>}
      <span className="m4-missing-key">Hatch: missing / ineligible</span>
      {mode === 'heatmap' || mode === 'profile' || mode === 'footprint' ? <span title="Fixed saturation cap; weak heatmap liquidity is subdued quadratically. Incoming data never rescales historical colors.">0 → {saturation(contrast)} {view?.quantity_unit ?? 'unit unavailable'} (saturated)</span> : null}
      {showQuotes && mode !== 'profile' && mode !== 'cvd' && mode !== 'ofi' ? <span>Dashed bid / ask colors: sampled best quotes · B / A: latest eligible sample · T: last observed print in window (not a live-price claim)</span> : null}
    </div>
    <div className="m4-chart-navigation" aria-label="Chart viewport controls">
      <button disabled={!view} onClick={() => view && onRangeChange(shiftedRange(view, -0.25))} aria-label="Pan chart earlier">Earlier</button>
      <button disabled={!view} onClick={() => view && onRangeChange(shiftedRange(view, 0.25))} aria-label="Pan chart later">Later</button>
      <button disabled={!view} onClick={() => view && onRangeChange(zoomedRange(view, 0.5, 0.8, view.bucket_ns))} aria-label="Zoom chart in">+</button>
      <button disabled={!view} onClick={() => view && onRangeChange(zoomedRange(view, 0.5, 1.25, view.bucket_ns))} aria-label="Zoom chart out">−</button>
      <button disabled={!view || mode === 'cvd' || mode === 'ofi'} onClick={() => panPrice(8)} aria-label="Pan prices higher">Price ↑</button>
      <button disabled={!view || mode === 'cvd' || mode === 'ofi'} onClick={() => panPrice(-8)} aria-label="Pan prices lower">Price ↓</button>
      <span>Drag: pan · wheel: time zoom · Shift-wheel: price · focused arrows / ±</span>
      {loading ? <span role="status">Updating…</span> : null}
    </div>
    </>}</InspectorDisclosure>
      <InspectorDisclosure active={!!props.inspectorOpen} className="m4-quality-reasons" summary={<span className="m4-chart-warning">{reasons.length} quality {reasons.length === 1 ? 'reason' : 'reasons'}</span>}>{() => <ul>{reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}</InspectorDisclosure>
      {displayCapped ? <InspectorDisclosure active={!!props.inspectorOpen} className="m4-quality-reasons" summary={<span className="m4-chart-warning">Display capped</span>}>{() => <p>Newest {CHART_LIMITS.columns} columns / {CHART_LIMITS.trades} trades / {CHART_LIMITS.footprints} footprint cells. Native collection unchanged.</p>}</InspectorDisclosure> : null}
    <details className="m4-chart-readout" open={exactOpen} onToggle={event => setExactOpen(event.currentTarget.open)}><summary>Exact data / quality · {column?.time_ns ?? 'no sampled time'} ns · price {price ?? 'unavailable'}</summary>
      {view && exactActive ? <>
      <dl><dt>Clock / instrument</dt><dd>{view.clock_id} / {view.instrument_id}</dd><dt>Window (source monotonic ns)</dt><dd>{view.start_ns} → {view.end_ns}</dd><dt>Retention starts</dt><dd>{view.retained_start_ns ?? 'Unavailable'}</dd><dt>Grid / price quantum</dt><dd>{view.grid_id} / {view.price_step}</dd><dt>Selected sample</dt><dd>{column ? `${column.time_ns} → ${column.end_ns} · ${column.segment_id}` : 'No sample at cursor'}</dd><dt>Best bid / ask</dt><dd>{column?.best_bid ?? 'Unknown'} / {column?.best_ask ?? 'Unknown'}</dd></dl>
      <dl>
        <dt>Projected receive clock</dt><dd>{formatChartTime(view, crosshair?.time_ns ?? column?.time_ns ?? view.start_ns, timeZone, true)}</dd>
        <dt>Cursor / sample source monotonic ns</dt><dd>{crosshair?.time_ns ?? column?.time_ns ?? 'Unavailable'}</dd>
        <dt>Projected receive wall ns</dt><dd>{projectReceiveTime(view, crosshair?.time_ns ?? column?.time_ns ?? view.start_ns) ?? 'Unavailable'}</dd>
        <dt>Sample original receive time (UTC)</dt><dd>{timestamp(column?.asof?.receive_wall_ns)} · {column?.asof?.receive_wall_ns ?? 'Unavailable'} ns</dd>
        <dt>Sample original receive monotonic ns</dt><dd>{column?.asof?.receive_mono_ns ?? 'Unavailable'}</dd>
        <dt>Sample original exchange event time (UTC)</dt><dd>{timestamp(column?.asof?.exchange_event_wall_ns)} · {column?.asof?.exchange_event_wall_ns ?? 'Unavailable'} ns</dd>
      </dl>
      <label>Exact visible price row <select value={price ?? ''} onChange={event => onCrosshair({ time_ns: crosshair?.time_ns ?? column?.time_ns ?? view.start_ns, price: event.target.value })}><option value="" disabled>No visible price row at cursor</option>{priceOptions}</select></label>
      <label>Exact sampled time <select value={column?.time_ns ?? ''} onChange={event => onCrosshair({ time_ns: event.target.value, price: crosshair?.price ?? price ?? null })}><option value="" disabled>No sampled column</option>{timeOptions}</select></label>
      {mode === 'cvd' || mode === 'ofi' ? <><p>{metric ? `${metric.name}: ${metric.value ?? 'Unavailable'} ${metric.unit} · ${metric.eligibility.eligible ? 'Eligible' : 'Ineligible'}` : 'No metric observation at cursor.'}</p>{metric ? <details open={metricOpen} onToggle={event => setMetricOpen(event.currentTarget.open)}><summary>Formula, window, quality and exact references</summary>{metricOpen ? <ExactJson value={metric} /> : null}</details> : null}</> : mode === 'candles' ? <p>Open {column?.candle?.open ?? '—'} · high {column?.candle?.high ?? '—'} · low {column?.candle?.low ?? '—'} · close {column?.candle?.close ?? '—'} · executed volume {column?.candle?.volume ?? '—'} {view.quantity_unit} · {column?.candle?.eligible ? 'Eligible' : 'Unavailable / ineligible'}</p> : <table><caption>Exact server aggregates · {view.quantity_unit} · up to 16 visible rows around selection; unknown ≠ zero</caption><thead><tr><th>Price lower bound</th>{mode === 'heatmap' ? <><th>Resting bid</th><th>Resting ask</th></> : <><th>Executed sell</th><th>Executed buy</th><th>Unknown side</th></>}</tr></thead><tbody>{(row < 0 ? [] : view.prices.slice(rowStart, Math.min(rowStart + 16, rowViewport.first + rowViewport.count))).map((value, index) => {
        const amounts = mode === 'profile' ? profileIndex.get(value) : footprintIndex.get(`${column?.time_ns}:${value}`);
        return <tr key={value}><th scope="row">{value}</th>{mode === 'heatmap' ? <><td>{column?.eligible ? column.cells[rowStart + index]?.[0] ?? 'Unknown' : 'Ineligible'}</td><td>{column?.eligible ? column.cells[rowStart + index]?.[1] ?? 'Unknown' : 'Ineligible'}</td></> : <><td>{amounts?.sell_base ?? 'No observation'}</td><td>{amounts?.buy_base ?? 'No observation'}</td><td>{amounts?.unknown_base ?? 'No observation'}</td></>}</tr>;
      })}</tbody></table>}
      <table><caption>Executed prints at selected time bucket · newest {Math.min(20, selectedTrades.length)} of {selectedTrades.length} displayed candidates · hollow/dashed = subscription history or unknown size</caption><thead><tr><th>Projected receive clock</th><th>Source monotonic ns</th><th>Original receive (UTC / ns)</th><th>Original receive monotonic ns</th><th>Original exchange event (UTC / ns)</th><th>Price</th><th>Base quantity</th><th>Taker side</th><th>Provenance</th></tr></thead><tbody>{selectedTrades.slice(-20).map(trade => <tr key={trade.id}><td>{formatChartTime(view, trade.time_ns, timeZone, true)}</td><td>{trade.time_ns}</td><td>{timestamp(trade.asof.receive_wall_ns)} · {trade.asof.receive_wall_ns} ns</td><td>{trade.asof.receive_mono_ns}</td><td>{timestamp(trade.asof.exchange_event_wall_ns)} · {trade.asof.exchange_event_wall_ns ?? 'Unavailable'} ns</td><td>{trade.price}</td><td>{trade.base_quantity ?? 'Unknown'}</td><td>{trade.taker_side}</td><td>{trade.segment_id} · {trade.is_subscription_history ? 'Subscription history (excluded from observed flow)' : 'Observed execution'} · ordinal {trade.asof.ingest_ordinal}</td></tr>)}</tbody></table>
      {column?.asof ? <details open={sampleOpen} onToggle={event => setSampleOpen(event.currentTarget.open)}><summary>Sample reference</summary>{sampleOpen ? <ExactJson value={column.asof} /> : null}</details> : null}
      </> : null}
    </details>
    <InspectorDisclosure active={!!props.inspectorOpen} className="m4-clock-mapping" summary="Receive-clock mapping & steps">{() => view ? <><p>An asterisk on a clock label marks a receive-clock offset change. Exact native pairs and retained coverage follow.</p><ExactJson value={view.time_axis} /></> : null}</InspectorDisclosure>
    <InspectorDisclosure active={!!props.inspectorOpen} className="m4-chart-performance" summary="Renderer diagnostics">{() => <>{report ? `Draw CPU ${report.duration.toFixed(2)} ms · dirty ${report.dirty} · tiles ${report.tiles}/${CHART_LIMITS.tiles} · trades ${report.trades} · receive→paint ${paintMs == null ? 'unmeasured' : `${paintMs.toFixed(1)} ms`}` : 'Render timing unavailable'} · paint opportunities, not GPU completion.</>}</InspectorDisclosure>
      {props.diagnostics}
    </aside>
    </div>
    <div className="m4-chart-quality" role="status">
      <span className="m4-chart-clock-context" aria-label={`Chart receive-clock date and timezone: ${clockContext}. Asterisk marks a clock step.`} title={`Receive clock · ${clockContext}. * marks a clock step; exact mapping is in Inspector.`}>{clockContext}</span>
      <span className="m4-chart-window-quality">
        {mode === 'footprint' && view ? <span className="m4-footprint-density" data-testid="footprint-density" tabIndex={0} aria-label={`${denseFootprint ? 'Compact cells marked with dashes; exact values in Inspector. ' : ''}SELL left, BUY right, question mark unknown. ${props.footprintOverview ? 'Full native grid' : `Visible native rows ${rowViewport.first + 1}–${rowViewport.first + rowViewport.count} of ${view.rows}`}. Quantities in ${view.quantity_unit}.`} title={`${denseFootprint ? 'Compact cells marked with dashes; exact values in Inspector. ' : ''}SELL left · BUY right · ? unknown. ${props.footprintOverview ? 'Full native grid' : `Visible native rows ${rowViewport.first + 1}–${rowViewport.first + rowViewport.count} of ${view.rows}`}. Quantities in ${view.quantity_unit}.`}>
          <span>{denseFootprint ? 'Compact cells · ' : ''}{props.footprintOverview ? 'Full native grid' : `Rows ${rowViewport.first + 1}–${rowViewport.first + rowViewport.count}/${view.rows}`} · {view.quantity_unit}</span>
          {!props.footprintOverview && rowViewport.count < view.rows ? <><button aria-label="Show higher footprint rows" onClick={() => panPrice(4)}>↑</button><button aria-label="Show lower footprint rows" onClick={() => panPrice(-4)}>↓</button></> : null}
        </span> : <span className="m4-chart-units" title={mode === 'heatmap' || mode === 'profile' ? 'Fixed saturation cap. Incoming data never rescales historical colors.' : 'Chart quantity units'}>{mode === 'heatmap' || mode === 'profile' ? `0–${saturation(contrast)} ` : ''}{mode === 'cvd' || mode === 'ofi' ? metric?.unit ?? 'Unit unavailable' : view?.quantity_unit ?? 'Unit unavailable'}</span>}
        {mode === 'heatmap' && report?.compactLiquidity ? <span title="Each display pixel retains the maximum source-cell intensity per side, not summed quantities. Unknown contributors remain hatched. Native price grid and exact Inspector cells are unchanged.">Compressed rows · max intensity</span> : null}
        <span className={`m4-chart-quality-badge${!view || !available || !view.complete_window || reasons.length ? ' m4-chart-warning' : ''}`} tabIndex={0} aria-label={qualityDetails} title={qualityDetails}>{windowQuality}{view?.truncated ? ' · truncated' : ''}{displayCapped ? ' · capped' : ''}</span>
      </span>
      <div className="m4-chart-freshness">{props.freshness}</div>
    </div>
  </section>;
}
