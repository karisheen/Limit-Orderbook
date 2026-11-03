import { useCallback, useEffect, useRef, useState } from 'react';
import type { Frame, GroupedLadder, Instrument, LadderInput, VisualGrid, VisualizationAck, VisualizationInput } from './api';
import type { ChartCursor, ChartMode, ChartRange, ChartRowViewport } from './market-charts-model';
import { chartTimeZoneLabel } from './chart-time';
import type { ChartTimeIntent, WorkspacePreferences } from './workspace';
import { retainedChartRange } from './workspace';
import { MarketCharts } from './MarketCharts';
import { ChartFreshness } from './Freshness';
import { useMarketView } from './useMarketView';
import { useChartTelemetry } from './useChartTelemetry';
import { milliseconds } from './format';

export interface ChartPaneProps {
  id: string;
  title: string;
  frame: Frame | null;
  instrument?: Instrument;
  connected: boolean;
  preferences: WorkspacePreferences;
  update: (value: Partial<WorkspacePreferences>) => void;
  visualization: (input: VisualizationInput, signal: AbortSignal, onReceive?: (receivedAtMs: number) => void) => Promise<VisualGrid>;
  ladder: (input: LadderInput, signal: AbortSignal) => Promise<GroupedLadder>;
  acknowledgeVisualization: (viewId: string, duration: number, signal: AbortSignal) => Promise<VisualizationAck>;
  crosshair: ChartCursor | null;
  onCrosshair: (value: ChartCursor | null) => void;
  linked: boolean;
  onLinked: () => void;
  timeLinked: boolean;
  onTimeChange: (intent: ChartTimeIntent) => void;
  onTimeLinked: (intent: ChartTimeIntent | null) => void;
  interactionActive: boolean;
  onClockScopeChange: () => void;
  clockScopeRevision: number;
  maximized: boolean;
  onMaximize: () => void;
  active: boolean;
}

const modes: ChartMode[] = ['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'];
const modeNames: Record<ChartMode, string> = { heatmap: 'Heatmap', candles: 'Candles', footprint: 'Footprint', profile: 'Profile', cvd: 'CVD', ofi: 'OFI' };

export function ChartPane({ id, title, frame, instrument, connected, preferences: p, update, visualization, ladder, acknowledgeVisualization, crosshair, onCrosshair, linked, onLinked, timeLinked, onTimeChange, onTimeLinked, interactionActive, onClockScopeChange, clockScopeRevision, maximized, onMaximize, active }: ChartPaneProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [recenter, setRecenter] = useState(0);
  const [cropReset, setCropReset] = useState(0);
  const pendingPriceReset = useRef<VisualGrid | null | undefined>(undefined);
  const host = useRef<HTMLElement>(null);
  const settingsTrigger = useRef<HTMLButtonElement>(null);
  const identity = frame ? `${frame.session_id}:${frame.generation}:${frame.source.mode}:${frame.source.id}:${frame.selection.instrument_id}:${clockScopeRevision}` : '';
  const [localCursor, setLocalCursor] = useState<{ identity: string; value: ChartCursor | null } | null>(null);
  const paneCursor = linked ? crosshair : localCursor?.identity === identity ? localCursor.value : null;
  const moveCursor = useCallback((value: ChartCursor | null) => {
    if (linked) onCrosshair(value);
    else setLocalCursor({ identity, value });
  }, [identity, linked, onCrosshair]);
  const usable = connected && !!frame && !['unavailable', 'error', 'loading', 'seeking'].includes(frame.status);
  const clockScopeChanged = useCallback(() => {
    setLocalCursor(null);
    setPresented(null);
    pendingPriceReset.current = undefined;
    onClockScopeChange();
  }, [onClockScopeChange]);
  const views = useMarketView({ frame, connected, preferences: p, recenter, visualization, ladder, chartEnabled: active, domEnabled: false, onClockScopeChange: clockScopeChanged, clockScopeRevision });
  const view = views.chart.data;
  useEffect(() => {
    if (pendingPriceReset.current !== undefined && view && view !== pendingPriceReset.current && !views.chart.loading) {
      pendingPriceReset.current = undefined;
      setCropReset(value => value + 1);
    }
  }, [view, views.chart.loading]);
  const [presented, setPresented] = useState<{ identity: string; view: VisualGrid; receivedAtMs: number | null; rowViewport: ChartRowViewport; mode: ChartMode } | null>(null);
  const telemetry = useChartTelemetry(frame, active && usable ? view : null, acknowledgeVisualization);
  const onPresented = useCallback((painted: VisualGrid | null, receivedAtMs: number | null, rowViewport: ChartRowViewport | null, mode: ChartMode | null) => {
    setPresented(previous => painted === null || rowViewport === null || mode === null ? null : previous?.view === painted && previous.receivedAtMs === receivedAtMs && previous.identity === identity && previous.mode === mode && previous.rowViewport.first === rowViewport.first && previous.rowViewport.count === rowViewport.count ? previous : { identity, view: painted, receivedAtMs, rowViewport, mode });
  }, [identity]);
  const displayed = presented?.identity === identity && view ? presented : null;
  const centerPrice = useCallback(() => {
    pendingPriceReset.current = view;
    update({ priceOffset: 0 });
    setRecenter(value => value + 1);
  }, [update, view]);
  const currentTimeIntent = useCallback((): ChartTimeIntent | null => {
    const painted = displayed?.view;
    if (!painted) return null;
    return p.follow ? { kind: 'follow', span_ns: (BigInt(painted.end_ns) - BigInt(painted.start_ns)).toString() }
      : { kind: 'pin', range: { start_ns: painted.start_ns, end_ns: painted.end_ns } };
  }, [displayed, p.follow]);
  const followLatest = useCallback(() => {
    const painted = displayed?.view;
    if (painted) onTimeChange({ kind: 'follow', span_ns: (BigInt(painted.end_ns) - BigInt(painted.start_ns)).toString() });
  }, [displayed, onTimeChange]);
  const fitRange = displayed ? retainedChartRange(displayed.view, p.mode) : null;
  const fitTime = useCallback(() => { if (fitRange) onTimeChange({ kind: 'pin', range: fitRange }); }, [fitRange, onTimeChange]);
  const changeRange = useCallback((range: ChartRange) => onTimeChange({ kind: 'pin', range }), [onTimeChange]);
  const panPrice = useCallback((rows: number) => update({ priceOffset: Math.max(-10000, Math.min(10000, p.priceOffset + Math.round(rows))) }), [p.priceOffset, update]);
  const closeContext = useCallback(() => {
    setSettingsOpen(false);
    setInspectorOpen(false);
    settingsTrigger.current?.focus();
  }, []);

  useEffect(() => {
    const clearCursor = () => setLocalCursor(null);
    window.addEventListener('terminal-clear-cursor', clearCursor);
    return () => window.removeEventListener('terminal-clear-cursor', clearCursor);
  }, []);

  useEffect(() => {
    if (!active || !interactionActive) return;
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.repeat || target instanceof Element && target.closest('form,input,select,textarea,button,summary,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"],[role="listbox"],[role="menu"],[role="menuitem"],[role="dialog"],[data-pane="dom"],[data-pane="tape"],[data-interaction-pane="dom"],[data-interaction-pane="tape"]')) return;
      const focusedPane = target instanceof Element ? target.closest('.chart-pane') : null;
      if (focusedPane ? focusedPane !== host.current : target !== document.body || !interactionActive) return;
      if (event.code === 'Space') { event.preventDefault(); update({ paused: !p.paused }); }
      else if (event.key.toLowerCase() === 'r') { event.preventDefault(); centerPrice(); }
      else if (event.key.toLowerCase() === 'f') { event.preventDefault(); if (event.shiftKey) fitTime(); else followLatest(); }
      else if (/^[1-6]$/.test(event.key) && id === 'heatmap') { event.preventDefault(); update({ mode: modes[Number(event.key) - 1] }); }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [active, id, interactionActive, p.paused, centerPrice, fitTime, followLatest, update]);

  const stepOptions = view?.step_options ?? [];
  const error = !usable ? `No current stream evidence${frame ? ` · ${frame.status}` : ''}.${view ? ' Retained chart is diagnostic only.' : ''}`
    : views.chart.error ? `Chart unavailable / retained snapshot stale: ${views.chart.error}` : null;
  const explicitSpan = p.followSpanNs;
  const rangeChoice = !p.follow ? 'pinned' : explicitSpan === null ? p.rangeSeconds === 180 ? 'auto' : String(p.rangeSeconds)
    : [6, 30, 60, 180, 300, 600].find(seconds => BigInt(seconds) * 1_000_000_000n === BigInt(explicitSpan))?.toString() ?? 'custom';
  return <section ref={host} className="chart-pane" data-pane={id} data-testid={`chart-pane-${id}`} hidden={!active} aria-label={`${title} chart pane`} data-interaction-active={interactionActive} onKeyDownCapture={event => {
    if (event.key !== 'Escape' || !settingsOpen && !inspectorOpen) return;
    const target = event.target;
    const disclosure = target instanceof Element ? target.closest('details[open]') : null;
    event.preventDefault();
    event.stopPropagation();
    if (disclosure instanceof HTMLDetailsElement) {
      disclosure.open = false;
      disclosure.querySelector<HTMLElement>(':scope > summary')?.focus();
    } else closeContext();
  }}>
    <div className="chart-pane-toolbar">
    <header className="chart-pane-header">
      <select aria-label="Chart mode" value={p.mode} onChange={event => update({ mode: event.target.value as ChartMode })}>{(id === 'flow' ? ['footprint', 'candles'] as ChartMode[] : modes).map(mode => <option key={mode} value={mode}>{modeNames[mode]}</option>)}</select>
      <select aria-label="Time bucket" title="Time bucket" value={p.bucket} onChange={event => update({ bucket: event.target.value })}>{['1000000000', '5000000000', '10000000000'].map((value, index) => <option key={value} value={value}>{[1, 5, 10][index]}s</option>)}</select>
      <select className="chart-pane-price-group" aria-label="Price aggregation" title="Price aggregation: native presentation buckets. Auto-fit resolves on initial view and Recenter price; native depth is unchanged." value={p.priceStep} onChange={event => { pendingPriceReset.current = view; const priceStep = event.target.value; update({ priceStep, priceOffset: 0 }); setRecenter(value => value + 1); }}><option value="">{view ? `Auto ${view.price_step}` : 'Auto group'}</option>{p.priceStep && !stepOptions.includes(p.priceStep) && <option value={p.priceStep}>{p.priceStep} · saved / validate</option>}{stepOptions.map(step => <option key={step}>{step}</option>)}</select>
      <span className="chart-pane-follow" title={`${p.paused ? 'Only this view is frozen; source capture and replay continue.' : p.follow ? 'Following observed source time' : 'Pinned source-time window'}${timeLinked ? ' Chart time ranges linked; price scales and freezes remain independent.' : ''}`}>{p.paused ? 'Frozen' : p.follow ? 'Follow' : 'Pinned'}{timeLinked ? ' · Time linked' : ''}</span>
      <div className="chart-pane-actions">
        <button aria-label={`Link ${title} cursor`} aria-pressed={linked} onClick={onLinked} title="Link exact source time and price with other panes">Cursor</button>
        <button data-testid="view-freeze" aria-pressed={p.paused} onClick={() => update({ paused: !p.paused })}>{p.paused ? 'Resume' : 'Freeze'}</button>
        <button aria-label={`${maximized ? 'Restore' : 'Maximize'} ${title}`} onClick={onMaximize}>{maximized ? 'Restore' : 'Max'}</button>
        <button ref={settingsTrigger} aria-label={`${title} settings`} aria-expanded={settingsOpen || inspectorOpen} aria-controls={inspectorOpen ? `${id}-inspector` : `${id}-settings`} onClick={() => { if (inspectorOpen) closeContext(); else setSettingsOpen(value => !value); }}>More</button>
      </div>
    </header>
    {settingsOpen && <div id={`${id}-settings`} className="chart-pane-settings" role="region" aria-label={`${title} settings`}>
      <div className="chart-pane-context-actions">
        <button aria-label={`${title} Inspector`} aria-controls={`${id}-inspector`} onClick={() => { setInspectorOpen(true); setSettingsOpen(false); }}>Inspector</button>
        <button data-testid="view-recenter" title="R · Price only; time and freeze unchanged" onClick={() => { centerPrice(); closeContext(); }}>Recenter price</button>
        <button title="F · Preserve displayed time width and freeze" disabled={!displayed} onClick={() => { followLatest(); closeContext(); }}>Follow latest</button>
        <button title={`Shift+F · Fit ${p.mode === 'heatmap' || p.mode === 'ofi' ? 'retained native samples' : 'returned native flow aggregates'}, preserving price and freeze`} disabled={!fitRange} onClick={() => { fitTime(); closeContext(); }}>Fit time</button>
        <button aria-label={`Close ${title} settings`} onClick={closeContext}>Close</button>
      </div>
      <label>Time zone<select aria-label="Chart time zone" value={p.timeZone} onChange={event => update({ timeZone: event.target.value === 'local' ? 'local' : 'UTC' })}><option value="UTC">UTC</option><option value="local">{chartTimeZoneLabel('local')}</option></select></label>
      <label>Range<select aria-label="Chart range" value={rangeChoice} onChange={event => {
        if (event.target.value === 'auto') onTimeChange({ kind: 'follow', span_ns: null, rangeSeconds: 180 });
        else { const seconds = Number(event.target.value); onTimeChange({ kind: 'follow', span_ns: (BigInt(seconds) * 1_000_000_000n).toString(), rangeSeconds: seconds }); }
      }}><option value="auto">Auto retained</option>{rangeChoice === 'pinned' && <option value="pinned" disabled>Pinned window</option>}{rangeChoice === 'custom' && <option value="custom" disabled>Custom · {Number(explicitSpan) / 1e9}s</option>}{[6, 30, 60, 180, 300, 600].map(value => <option key={value} value={value}>{value}s</option>)}</select></label>
      <label><input type="checkbox" checked={p.follow} disabled={!displayed} onChange={event => { if (event.target.checked) followLatest(); else if (displayed) changeRange({ start_ns: displayed.view.start_ns, end_ns: displayed.view.end_ns }); }} />Follow time</label>
      <label><input type="checkbox" aria-label="Link chart time ranges" checked={timeLinked} onChange={() => onTimeLinked(timeLinked ? null : currentTimeIntent())} />Link chart time ranges</label>
      <label title="Fixed base quantity at full intensity. Incoming data never rescales historical colors.">Fixed contrast<input aria-label="Fixed contrast" aria-valuetext={`Saturates at ${p.contrast} ${view?.quantity_unit ?? 'base units'}`} type="range" min="-12" max="12" step="0.1" value={Math.log10(p.contrast)} onChange={event => update({ contrast: 10 ** Number(event.target.value) })} /></label>
      <label><input type="checkbox" checked={p.showTrades} onChange={event => update({ showTrades: event.target.checked })} />Trades</label>
      <label><input type="checkbox" checked={p.showQuotes} onChange={event => update({ showQuotes: event.target.checked })} />Quotes</label>
      {p.mode === 'footprint' ? <label title="Show every native price row. Compact representation is explicit when exact numbers do not fit; Inspector retains exact values."><input type="checkbox" checked={p.footprintOverview} onChange={event => update({ footprintOverview: event.target.checked })} />Full native-grid overview</label> : null}
    </div>}
    </div>
    <MarketCharts view={active ? view : null} receivedAtMs={views.chart.receivedAtMs ?? null} onPaint={telemetry.onPaint} onPresented={onPresented}
      interactionActive={interactionActive} interactionScope={identity}
      loading={views.chart.loading} error={error} mode={p.mode} contrast={p.contrast} showTrades={p.showTrades} showQuotes={p.showQuotes} timeZone={p.timeZone}
      footprintOverview={p.footprintOverview} priceResetToken={`${identity}:${cropReset}`} quoteUnit={instrument?.instrument_id === view?.instrument_id ? instrument?.quote.ticker : undefined}
      crosshair={paneCursor} onCrosshair={moveCursor} onRangeChange={changeRange} onPricePan={panPrice}
      freshness={<ChartFreshness frame={frame} view={displayed?.view ?? null} receivedAtMs={displayed?.receivedAtMs ?? null} rowViewport={displayed?.rowViewport} connected={connected} frozen={p.paused} mode={displayed?.mode} />}
      inspectorOpen={inspectorOpen} inspectorId={`${id}-inspector`} onInspectorClose={closeContext}
      diagnostics={<details className="chart-pane-diagnostics"><summary>Paint & cursor diagnostics</summary>
      <div className="chart-timing" data-testid="chart-telemetry" data-view-id={telemetry.measurement?.view_id}>Chart draw {milliseconds(telemetry.measurement?.draw_ms)} · Chart receive→paint {milliseconds(telemetry.measurement?.receive_ms)} · Chart publish→ACK ≤ {milliseconds(telemetry.measurement?.upper_bound_ms)}</div>
      <p>CPU draw and two visible animation-frame paint opportunities; ACK is a conservative round-trip upper bound, not GPU completion or one-way latency.</p>
      <p>Last measured view: {telemetry.measurement?.view_id ?? 'unavailable'}</p>
      <div className="linked-readout">{linked ? 'Linked' : 'Local'} cursor · source monotonic ns {paneCursor?.time_ns ?? '—'} · exact price {paneCursor?.price ?? '—'}</div>
      <p>{view ? `${view.rows} price rows × ${view.price_step} · ${view.sampling}` : 'No native chart received.'} · Focused chart: Space freeze · R recenter price · F follow latest · Shift+F fit time · 1–6 primary mode · arrows / ± navigate. Time linking never changes price scales or freezes; Cursor links independently.</p>
      <p>Fit time uses retained native sample bounds for liquidity / OFI and returned paintable aggregates for flow, capped to the native 30-minute query limit. Receive-clock label coverage is not retained market history. Follow latest preserves the painted window width; explicit range presets replace it.</p>
      </details>} />
    {telemetry.measurement?.error && <p className="m4-chart-error" role="alert">Chart ACK unavailable: {telemetry.measurement.error}</p>}
  </section>;
}
