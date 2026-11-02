import { useCallback, useEffect, useRef, useState } from 'react';
import type { Frame, GroupedLadder, LadderInput, VisualGrid, VisualizationAck, VisualizationInput } from './api';
import type { ChartCursor, ChartMode, ChartRange } from './market-charts-model';
import type { WorkspacePreferences } from './workspace';
import { MarketCharts } from './MarketCharts';
import { ChartFreshness } from './Freshness';
import { useMarketView } from './useMarketView';
import { useChartTelemetry } from './useChartTelemetry';
import { milliseconds } from './format';

export interface ChartPaneProps {
  id: string;
  title: string;
  frame: Frame | null;
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
  maximized: boolean;
  onMaximize: () => void;
  active: boolean;
}

const modes: ChartMode[] = ['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'];
const modeNames: Record<ChartMode, string> = { heatmap: 'Heatmap', candles: 'Candles', footprint: 'Footprint', profile: 'Profile', cvd: 'CVD', ofi: 'OFI' };

export function ChartPane({ id, title, frame, connected, preferences: p, update, visualization, ladder, acknowledgeVisualization, crosshair, onCrosshair, linked, onLinked, maximized, onMaximize, active }: ChartPaneProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [recenter, setRecenter] = useState(0);
  const host = useRef<HTMLElement>(null);
  const settingsTrigger = useRef<HTMLButtonElement>(null);
  const inspectorTrigger = useRef<HTMLButtonElement>(null);
  const identity = frame ? `${frame.session_id}:${frame.generation}:${frame.source.mode}:${frame.source.id}:${frame.selection.instrument_id}` : '';
  const [localCursor, setLocalCursor] = useState<{ identity: string; value: ChartCursor | null } | null>(null);
  const paneCursor = linked ? crosshair : localCursor?.identity === identity ? localCursor.value : null;
  const moveCursor = useCallback((value: ChartCursor | null) => {
    if (linked) onCrosshair(value);
    else setLocalCursor({ identity, value });
  }, [identity, linked, onCrosshair]);
  const usable = connected && !!frame && !['unavailable', 'error', 'loading', 'seeking'].includes(frame.status);
  const views = useMarketView({ frame, connected, preferences: p, recenter, visualization, ladder, chartEnabled: active, domEnabled: false });
  const view = views.chart.data;
  const [presented, setPresented] = useState<{ identity: string; view: VisualGrid; receivedAtMs: number | null } | null>(null);
  const telemetry = useChartTelemetry(frame, active && usable ? view : null, acknowledgeVisualization);
  const onPresented = useCallback((painted: VisualGrid | null, receivedAtMs: number | null) => {
    setPresented(previous => painted === null ? null : previous?.view === painted && previous.receivedAtMs === receivedAtMs && previous.identity === identity ? previous : { identity, view: painted, receivedAtMs });
  }, [identity]);
  const displayed = presented?.identity === identity && view ? presented : null;
  const center = useCallback(() => {
    update({ priceOffset: 0, follow: true, range: null, paused: false });
    setRecenter(value => value + 1);
  }, [update]);
  const changeRange = useCallback((range: ChartRange) => update({ range, follow: false }), [update]);
  const panPrice = useCallback((rows: number) => update({ priceOffset: Math.max(-10000, Math.min(10000, p.priceOffset + Math.round(rows))) }), [p.priceOffset, update]);

  useEffect(() => {
    if (!active) return;
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || target instanceof Element && target.closest('input,select,textarea,button,summary,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
      const focusedPane = target instanceof Element ? target.closest('.chart-pane') : null;
      if (focusedPane ? focusedPane !== host.current : id !== 'heatmap') return;
      if (event.code === 'Space') { event.preventDefault(); update({ paused: !p.paused }); }
      else if (event.key.toLowerCase() === 'r') { event.preventDefault(); center(); }
      else if (/^[1-6]$/.test(event.key) && id === 'heatmap') { event.preventDefault(); update({ mode: modes[Number(event.key) - 1] }); }
    };
    window.addEventListener('keydown', keyboard);
    return () => window.removeEventListener('keydown', keyboard);
  }, [active, id, p.paused, center, update]);

  const stepOptions = view?.step_options ?? [];
  const error = !usable ? `No current stream evidence${frame ? ` · ${frame.status}` : ''}.${view ? ' Retained chart is diagnostic only.' : ''}`
    : views.chart.error ? `Chart unavailable / retained snapshot stale: ${views.chart.error}` : null;
  return <section ref={host} className="chart-pane" data-pane={id} data-testid={`chart-pane-${id}`} hidden={!active} aria-label={`${title} chart pane`} onKeyDown={event => {
    if (event.key !== 'Escape' || !settingsOpen && !inspectorOpen) return;
    event.preventDefault();
    event.stopPropagation();
    const trigger = settingsOpen ? settingsTrigger : inspectorTrigger;
    setSettingsOpen(false);
    setInspectorOpen(false);
    trigger.current?.focus();
  }}>
    <div className="chart-pane-toolbar">
    <header className="chart-pane-header">
      <strong>{title}</strong>
      <select aria-label="Chart mode" value={p.mode} onChange={event => update({ mode: event.target.value as ChartMode })}>{(id === 'flow' ? ['footprint', 'candles'] as ChartMode[] : modes).map(mode => <option key={mode} value={mode}>{modeNames[mode]}</option>)}</select>
      <span className="chart-pane-follow" title={p.paused ? 'Only this view is frozen; source capture and replay continue.' : p.follow ? 'Following observed source time' : 'Pinned source-time window'}>{p.paused ? 'Frozen' : p.follow ? 'Follow' : 'Pinned'}</span>
      <div className="chart-pane-actions">
        <button aria-label={`Link ${title} cursor`} aria-pressed={linked} onClick={onLinked} title="Link exact source time and price with other panes">Link</button>
        <button ref={settingsTrigger} aria-label={`${title} settings`} aria-expanded={settingsOpen} aria-controls={`${id}-settings`} onClick={() => { setSettingsOpen(value => !value); setInspectorOpen(false); }}>Settings</button>
        <button ref={inspectorTrigger} aria-label={`${title} Inspector`} aria-expanded={inspectorOpen} aria-controls={`${id}-inspector`} onClick={() => { setInspectorOpen(value => !value); setSettingsOpen(false); }}>Inspector</button>
        <button data-testid="view-freeze" aria-pressed={p.paused} onClick={() => update({ paused: !p.paused })}>{p.paused ? 'Resume' : 'Freeze'}</button>
        <button data-testid="view-recenter" onClick={center}>Recenter</button>
        <button aria-label={`${maximized ? 'Restore' : 'Maximize'} ${title}`} onClick={onMaximize}>{maximized ? 'Restore' : 'Max'}</button>
      </div>
    </header>
    {settingsOpen && <div id={`${id}-settings`} className="chart-pane-settings">
      <label title="Native presentation buckets only. Auto-fit is resolved independently on initial view and Recenter; native depth is unchanged.">Price aggregation<select aria-label="Price aggregation" value={p.priceStep} onChange={event => { const priceStep = event.target.value; update({ priceStep, priceOffset: 0 }); setRecenter(value => value + 1); }}><option value="">Auto-fit depth</option>{p.priceStep && !stepOptions.includes(p.priceStep) && <option value={p.priceStep}>{p.priceStep} · saved / validate</option>}{stepOptions.map(step => <option key={step}>{step}</option>)}</select></label>
      <label>Time bucket<select aria-label="Time bucket" value={p.bucket} onChange={event => update({ bucket: event.target.value })}>{['1000000000', '5000000000', '10000000000'].map((value, index) => <option key={value} value={value}>{[1, 5, 10][index]}s</option>)}</select></label>
      <label>Range<select aria-label="Chart range" value={p.rangeSeconds} onChange={event => update({ rangeSeconds: Number(event.target.value), range: null, follow: true })}>{[30, 60, 180, 300, 600].map(value => <option key={value} value={value}>{value}s</option>)}</select></label>
      <label><input type="checkbox" checked={p.follow} onChange={event => update({ follow: event.target.checked, range: event.target.checked ? null : view ? { start_ns: view.start_ns, end_ns: view.end_ns } : null })} />Follow time</label>
      <label title="Fixed base quantity at full intensity. Incoming data never rescales historical colors.">Fixed contrast<input aria-label="Fixed contrast" aria-valuetext={`Saturates at ${p.contrast} ${view?.quantity_unit ?? 'base units'}`} type="range" min="-12" max="12" step="0.1" value={Math.log10(p.contrast)} onChange={event => update({ contrast: 10 ** Number(event.target.value) })} /></label>
      <label><input type="checkbox" checked={p.showTrades} onChange={event => update({ showTrades: event.target.checked })} />Trades</label>
      <label><input type="checkbox" checked={p.showQuotes} onChange={event => update({ showQuotes: event.target.checked })} />Quotes</label>
    </div>}
    </div>
    <MarketCharts view={active ? view : null} receivedAtMs={views.chart.receivedAtMs ?? null} onPaint={telemetry.onPaint} onPresented={onPresented}
      loading={views.chart.loading} error={error} mode={p.mode} contrast={p.contrast} showTrades={p.showTrades} showQuotes={p.showQuotes}
      crosshair={paneCursor} onCrosshair={moveCursor} onRangeChange={changeRange} onPricePan={panPrice}
      freshness={<ChartFreshness frame={frame} view={displayed?.view ?? null} receivedAtMs={displayed?.receivedAtMs ?? null} connected={connected} frozen={p.paused} mode={p.mode} />}
      inspectorOpen={inspectorOpen} inspectorId={`${id}-inspector`}
      diagnostics={<details className="chart-pane-diagnostics"><summary>Paint & cursor diagnostics</summary>
      <div className="chart-timing" data-testid="chart-telemetry" data-view-id={telemetry.measurement?.view_id}>Chart draw {milliseconds(telemetry.measurement?.draw_ms)} · Chart receive→paint {milliseconds(telemetry.measurement?.receive_ms)} · Chart publish→ACK ≤ {milliseconds(telemetry.measurement?.upper_bound_ms)}</div>
      <p>CPU draw and two visible animation-frame paint opportunities; ACK is a conservative round-trip upper bound, not GPU completion or one-way latency.</p>
      <p>Last measured view: {telemetry.measurement?.view_id ?? 'unavailable'}</p>
      <div className="linked-readout">{linked ? 'Linked' : 'Local'} cursor · source monotonic ns {paneCursor?.time_ns ?? '—'} · exact price {paneCursor?.price ?? '—'}</div>
      <p>{view ? `${view.rows} price rows × ${view.price_step} · ${view.sampling}` : 'No native chart received.'} · Space freeze · R recenter · 1–6 primary mode · focused arrows / ± navigate</p>
      </details>} />
    {telemetry.measurement?.error && <p className="m4-chart-error" role="alert">Chart ACK unavailable: {telemetry.measurement.error}</p>}
  </section>;
}
