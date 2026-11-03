import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { Analytics, CostInput, Costs, Frame, Instrument, Metric, Pair, ReplayAction, Source } from './api';
import { errorMessage } from './api';
import { exact, label, milliseconds, timestamp } from './format';
import { useTerminal } from './useTerminal';
import { ChartPane } from './ChartPane';
import type { ChartCursor } from './MarketCharts';
import { Freshness } from './Freshness';
import { DepthLadder } from './DepthLadder';
import { ProposalRiskPanel } from './ProposalRiskPanel';
import { useMarketView } from './useMarketView';
import { chartSettings, presetLayout, useWorkspacePreferences, workspaceGeometry } from './workspace';
import type { PaneId, WorkspacePreferences } from './workspace';
import './workstation.css';
import { Details, Empty, Panel, Reasons, Value } from './WorkflowUI';
import { AlertsPanel, ContextPanel, ResearchPanel } from './SignalPanels';
import { JournalPanel, PaperPanel } from './PaperPanels';

const workflowTabs = [
  ['costs', 'Costs & proposed risk'], ['analytics', 'Core analytics'],
  ['alerts', 'Alerts'], ['context', 'Context'], ['paper', 'Paper'],
  ['journal', 'Journal'], ['research', 'Research'],
] as const;
type WorkflowTab = typeof workflowTabs[number][0];
const emptyInstruments: Instrument[] = [];
const instrumentPageSize = 100;
const abbreviateCaptureLabel = (value: string) => value.replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi, id => `${id.slice(0, 8)}…`);


function MetricRow({ metric }: { metric: Metric }) {
  return <tr><th scope="row">{label(metric.name)}</th><td><Value value={metric.value} /></td><td className="muted">{metric.unit}</td><td>
    <details><summary className={metric.eligibility.eligible ? 'positive' : 'warning'}>{metric.eligibility.eligible ? 'Available' : 'Unavailable'}</summary>
      <Reasons reasons={metric.eligibility.reasons} />
      <dl className="metadata"><dt>As of (receive UTC)</dt><dd>{timestamp(metric.asof?.receive_wall_ns)}</dd><dt>Formula</dt><dd>{metric.formula_version}</dd><dt>Exact instrument</dt><dd>{metric.instrument_id}</dd></dl>
      <Details title="Window, quality and reproducible references" value={metric} />
    </details>
  </td></tr>;
}

function AnalyticsPanel({ analytics }: { analytics: Analytics | null }) {
  const [band, setBand] = useState('10');
  const [tab, setTab] = useState('metrics');
  const metrics = analytics?.metrics.filter(metric => !metric.name.startsWith('depth_') || metric.name.includes(`_${band}bps_`)) ?? [];
  return <Panel title="Core analytics" className="analytics-panel" extra={<div className="tabs" role="tablist" aria-label="Analytics view">{['metrics', 'profile', 'evidence'].map(name => <button key={name} role="tab" aria-selected={tab === name} onClick={() => setTab(name)}>{label(name)}</button>)}</div>}>
    <div className="panel-toolbar"><span>Exact shared-core values · no browser financial engine</span><label>Depth band <select aria-label="Depth band" value={band} onChange={event => setBand(event.target.value)}>{['5', '10', '25', '50'].map(value => <option key={value} value={value}>{value} bps</option>)}</select></label></div>
    <Reasons reasons={analytics?.reasons ?? []} />
    {analytics && (analytics.presentation.profile_omitted > 0 || analytics.presentation.evidence_omitted > 0) && <p className="caption warning">Bounded presentation: {analytics.presentation.profile_omitted} profile rows and {analytics.presentation.evidence_omitted} evidence records omitted. Metrics still use the full eligible native window.</p>}
    <div className="scroll analytics-scroll" role="tabpanel">
      {!analytics ? <Empty>Waiting for analytics evidence. Missing observations are not zero.</Empty> : tab === 'metrics' ? <table className="metrics-table"><thead><tr><th>Metric</th><th>Value</th><th>Unit</th><th>Quality / provenance</th></tr></thead><tbody>{metrics.map(metric => <MetricRow key={metric.name} metric={metric} />)}</tbody></table> : tab === 'profile' ? <>
        <p className="caption">Executed volume, not resting liquidity. Unknown aggressors remain separate. Availability and window evidence are in the profile metric.</p>
        {analytics.profile.length ? <table><thead><tr><th>Price</th><th>Buy base</th><th>Sell base</th><th>Unknown base</th><th>Quote notional</th></tr></thead><tbody>{analytics.profile.slice(0, 200).map(level => <tr key={level.price}><td><Value value={level.price} /></td><td><Value value={level.buy_base} /></td><td><Value value={level.sell_base} /></td><td><Value value={level.unknown_base} /></td><td><Value value={level.quote_notional} /></td></tr>)}</tbody></table> : <Empty>No eligible executed-volume profile. Inspect window/quality reasons in metrics.</Empty>}
      </> : <><p className="caption">Observed persistence/depletion/replenishment. Absorption and pulling are heuristics, not identity, intent, queue or hidden-size claims.</p>{analytics.display_evidence.length ? analytics.display_evidence.slice(0, 100).map((item, index) => <Details key={`${item.kind}:${item.price}:${item.end_mono_ns}:${index}`} title={`${label(item.kind)} · ${item.side} · ${item.price} × ${item.quantity}`} value={item} />) : <Empty>No eligible display evidence in this window.</Empty>}</>}
    </div>
  </Panel>;
}


function ResizeHandle({ axis, value, min, max, reverse = false, proportional = false, onChange, name }: {
  axis: 'horizontal' | 'vertical'; value: number; min: number; max: number; reverse?: boolean; proportional?: boolean; onChange: (value: number) => void; name: string;
}) {
  const drag = useRef<{ position: number; value: number; extent: number } | null>(null);
  return <div className={`resize-handle resize-${axis}`} role="separator" aria-label={name} aria-orientation={axis === 'horizontal' ? 'vertical' : 'horizontal'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0}
    onPointerDown={event => { event.preventDefault(); event.currentTarget.focus(); drag.current = { position: axis === 'horizontal' ? event.clientX : event.clientY, value, extent: event.currentTarget.parentElement?.parentElement?.clientHeight ?? 1 }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => {
      if (!drag.current) return;
      const distance = (axis === 'horizontal' ? event.clientX : event.clientY) - drag.current.position;
      const size = drag.current.value + distance * (reverse ? -1 : 1) * (proportional ? 100 / Math.max(1, drag.current.extent) : 1);
      onChange(Math.round(Math.max(min, Math.min(max, size))));
    }}
    onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={event => {
      const decrement = axis === 'horizontal' ? 'ArrowLeft' : 'ArrowUp';
      const increment = axis === 'horizontal' ? 'ArrowRight' : 'ArrowDown';
      if ([decrement, increment, 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        onChange(event.key === 'Home' ? min : event.key === 'End' ? max : Math.max(min, Math.min(max, value + (event.key === increment ? 1 : -1) * (proportional ? 2 : 10) * (reverse ? -1 : 1))));
      }
    }} />;
}

function PaneActions({ pane, linked, onLinked, maximized, onMaximize }: { pane: string; linked: boolean; onLinked: () => void; maximized: boolean; onMaximize: () => void }) {
  return <span className="pane-actions"><button aria-label={`Link ${pane} cursor`} aria-pressed={linked} onClick={onLinked}>Cursor</button><button aria-label={`${maximized ? 'Restore' : 'Maximize'} ${pane}`} onClick={onMaximize}>{maximized ? 'Restore' : 'Max'}</button></span>;
}

function Tape({ frame, connected, instrument, preferences: p, update, crosshair, onCrosshair, maximized, onMaximize }: {
  frame: Frame | null; connected: boolean; instrument?: Instrument; preferences: WorkspacePreferences;
  update: (value: Partial<WorkspacePreferences>) => void; crosshair: ChartCursor | null;
  onCrosshair: (value: ChartCursor | null) => void; maximized: boolean; onMaximize: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Analytics['tape'] | null>(null);
  const [localCursor, setLocalCursor] = useState<ChartCursor | null>(null);
  const [inspected, setInspected] = useState<{ print: Analytics['tape'][number]; source: Frame['source']; trigger: HTMLButtonElement } | null>(null);
  const headerAction = useRef<HTMLButtonElement | null>(null);
  const inspector = useRef<HTMLDivElement | null>(null);
  const focusedPrint = useRef<HTMLElement | null>(null);
  const settings = useRef<HTMLDetailsElement | null>(null);
  const settingsSummary = useRef<HTMLElement | null>(null);
  const settingsToolbar = useRef<HTMLDivElement | null>(null);
  const positionSettings = useCallback(() => {
    const menu = settingsToolbar.current;
    const summary = settingsSummary.current;
    if (!menu || !summary) return;
    if (!settings.current?.open) {
      menu.style.visibility = 'hidden';
      return;
    }
    // Fixed positioning escapes the clipped tape body; clamp to the visible
    // viewport rather than assuming there is room above or below a short pane.
    const viewport = window.visualViewport;
    const left = (viewport?.offsetLeft ?? 0) + 8;
    const top = (viewport?.offsetTop ?? 0) + 8;
    const width = Math.max(0, (viewport?.width ?? document.documentElement.clientWidth) - 16);
    const height = Math.max(0, (viewport?.height ?? document.documentElement.clientHeight) - 16);
    menu.style.width = `${Math.min(225, width)}px`;
    menu.style.maxHeight = `${height}px`;
    const anchor = summary.getBoundingClientRect();
    const bounds = menu.getBoundingClientRect();
    const below = anchor.bottom + 4;
    const above = anchor.top - bounds.height - 4;
    const preferredTop = below + bounds.height <= top + height ? below : above >= top ? above : below;
    menu.style.left = `${Math.max(left, Math.min(anchor.right - bounds.width, left + width - bounds.width))}px`;
    menu.style.top = `${Math.max(top, Math.min(preferredTop, top + height - bounds.height))}px`;
    menu.style.visibility = 'visible';
  }, []);
  useLayoutEffect(positionSettings, [positionSettings, p, maximized]);
  useLayoutEffect(() => {
    const disclosure = settings.current;
    const summary = settingsSummary.current;
    const menu = settingsToolbar.current;
    if (!disclosure || !summary || !menu) return;
    const observer = new ResizeObserver(positionSettings);
    observer.observe(summary);
    observer.observe(menu);
    const panel = disclosure.closest('.tape-panel');
    if (panel) observer.observe(panel);
    const workspace = disclosure.closest('.workstation-workspace');
    if (workspace) observer.observe(workspace);
    disclosure.addEventListener('toggle', positionSettings);
    window.addEventListener('resize', positionSettings);
    window.addEventListener('scroll', positionSettings, true);
    const viewport = window.visualViewport;
    viewport?.addEventListener('resize', positionSettings);
    viewport?.addEventListener('scroll', positionSettings);
    return () => {
      observer.disconnect();
      disclosure.removeEventListener('toggle', positionSettings);
      window.removeEventListener('resize', positionSettings);
      window.removeEventListener('scroll', positionSettings, true);
      viewport?.removeEventListener('resize', positionSettings);
      viewport?.removeEventListener('scroll', positionSettings);
    };
  }, [positionSettings]);
  const tapeCursor = p.links.tape ? crosshair : localCursor;
  const setTapeCursor = p.links.tape ? onCrosshair : setLocalCursor;
  const available = connected && !!frame && !['seeking', 'unavailable', 'error', 'loading'].includes(frame.status);
  useEffect(() => {
    if (!p.tapeFrozen) setSnapshot(null);
    else if (available && frame?.analytics && snapshot === null) setSnapshot(frame.analytics.tape);
  }, [p.tapeFrozen, available, frame?.analytics, snapshot]);
  const tape = available ? (p.tapeFrozen ? snapshot : frame?.analytics?.tape) ?? [] : [];
  const prints = tape.filter(item => p.tapeSide === 'all' || (p.tapeSide === 'unknown' ? !['buy', 'sell'].includes(item.trade.taker_side) : item.trade.taker_side === p.tapeSide)).slice(-p.tapeRows).reverse();
  useLayoutEffect(() => {
    if (inspected) inspector.current?.focus();
  }, [inspected]);
  useLayoutEffect(() => {
    if (!inspected && focusedPrint.current && !focusedPrint.current.isConnected) {
      focusedPrint.current = null;
      if (document.activeElement === document.body) headerAction.current?.focus();
    }
  });
  const closeInspector = () => {
    if (!inspected) return;
    const target = inspected.trigger.isConnected ? inspected.trigger : headerAction.current;
    setInspected(null);
    target?.focus();
  };
  return <Panel title="Time & sales" className="tape-panel" extra={<>
    <button ref={headerAction} aria-label={p.tapeFrozen ? 'Resume tape' : 'Freeze tape'} aria-pressed={p.tapeFrozen} onClick={() => { setSnapshot(p.tapeFrozen || !available ? null : frame?.analytics?.tape ?? null); update({ tapeFrozen: !p.tapeFrozen }); }}>{p.tapeFrozen ? 'Resume' : 'Freeze'}</button>
    <PaneActions pane="tape" linked={p.links.tape} onLinked={() => { onCrosshair(null); setLocalCursor(null); update({ links: { ...p.links, tape: !p.links.tape } }); }} maximized={maximized} onMaximize={onMaximize} />
    <details ref={settings} className="tape-settings"><summary ref={settingsSummary} aria-label="Tape settings">More</summary><div ref={settingsToolbar} className="tape-toolbar">
      <label>Rows<select aria-label="Tape rows" value={p.tapeRows} onChange={event => update({ tapeRows: Number(event.target.value) })}>{[25, 50, 100].map(rows => <option key={rows}>{rows}</option>)}</select></label>
      <label>Side<select aria-label="Tape side" value={p.tapeSide} onChange={event => update({ tapeSide: event.target.value as WorkspacePreferences['tapeSide'] })}>{['all', 'buy', 'sell', 'unknown'].map(side => <option key={side}>{side}</option>)}</select></label>
      <span>Receive time UTC · native prints. Select a time for exact provenance.</span>
    </div></details>
  </>}>
    <Freshness frame={frame} reference={prints[0]?.asof ?? null} connected={connected} label="Trades" frozen={p.tapeFrozen} />
    <div className="scroll tape-scroll">{!prints.length ? <Empty>{available ? 'No public trades observed at this cursor. No past prints are invented.' : 'Stream evidence unavailable.'}</Empty> : <table>
      <thead><tr><th scope="col">TIME <span className="muted">UTC</span></th><th scope="col">PRICE <span className="muted">{instrument?.quote.ticker}</span></th><th scope="col">SIZE <span className="muted">{instrument?.base.ticker ?? 'base'}</span></th><th scope="col">SIDE</th></tr></thead>
      <tbody>{prints.map(item => <tr key={JSON.stringify([item.stream_id, item.trade.dedup_key])} className={tapeCursor?.price === item.trade.price ? 'cursor-linked' : ''}
        onFocusCapture={event => { focusedPrint.current = event.target as HTMLElement; }}
        onBlurCapture={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node)) focusedPrint.current = null; }}
        onPointerEnter={() => setTapeCursor({ time_ns: item.asof.receive_mono_ns, price: item.trade.price })} onPointerLeave={() => setTapeCursor(null)}>
        <td><button className="tape-time" aria-label={`Inspect trade ${item.trade.trade_id ?? item.trade.dedup_key}`}
          title={`Receive UTC ${timestamp(item.asof.receive_wall_ns)}; receive ns ${item.asof.receive_wall_ns}; exchange ns ${exact(item.asof.exchange_event_wall_ns)}`}
          onClick={event => { if (frame) setInspected({ print: item, source: frame.source, trigger: event.currentTarget }); }}>{timestamp(item.asof.receive_wall_ns).replace(/\.\d{3}$/, '')}</button></td>
        <td className="numeric"><button className="tape-price" aria-label={`Link trade price ${item.trade.price}`}
          onFocus={() => setTapeCursor({ time_ns: item.asof.receive_mono_ns, price: item.trade.price })} onBlur={() => setTapeCursor(null)}>{item.trade.price}</button></td>
        <td className="numeric">{exact(item.base_quantity)}</td>
        <td className={item.trade.taker_side === 'buy' ? 'bid' : item.trade.taker_side === 'sell' ? 'ask' : 'muted'} title={item.trade.side_provenance}>
          {item.trade.taker_side}
          {item.trade.is_subscription_history && <span className="tape-origin warning">History</span>}
          {frame?.source.synthetic && <span className="tape-origin warning">Synthetic</span>}
          {item.trade.side_provenance === 'unknown' && ['buy', 'sell'].includes(item.trade.taker_side) && <span className="tape-origin warning">Unknown provenance</span>}
        </td>
      </tr>)}</tbody></table>}</div>
    {inspected && <div ref={inspector} className="tape-inspector" role="dialog" aria-label="Trade inspector" tabIndex={-1}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeInspector(); } }}>
      <div className="tape-inspector-heading"><strong>Native print · pinned snapshot</strong><button onClick={closeInspector} aria-label="Close trade inspector">Close</button></div>
      <dl className="metadata"><dt>Receive UTC</dt><dd>{timestamp(inspected.print.asof.receive_wall_ns)}</dd><dt>Source</dt><dd>{inspected.source.label} · {inspected.source.synthetic ? 'Synthetic' : inspected.source.recording ? 'Recorded' : inspected.source.mode}</dd><dt>Origin</dt><dd>{inspected.print.trade.is_subscription_history ? 'Subscription history' : 'Observed print'}</dd><dt>Side provenance</dt><dd>{inspected.print.trade.side_provenance}</dd></dl>
      <Details title="Exact native print, timestamps, units and source" value={{ print: inspected.print, source: inspected.source }} />
    </div>}
  </Panel>;
}

function ReplayControls({ frame, busy, replay }: { frame: Frame | null; busy: boolean; replay: (action: ReplayAction, payload?: Record<string, unknown>) => Promise<void> }) {
  const [seek, setSeek] = useState('0');
  const disabled = !frame?.replay || busy || frame.status === 'seeking';
  return <div className="replay-controls" aria-label="Replay controls"><span className="section-label">REPLAY</span>
    <button data-testid="replay-play" disabled={disabled || frame?.status === 'playing'} onClick={() => void replay('play')}>Play</button>
    <button data-testid="replay-pause" disabled={disabled || frame?.status !== 'playing'} onClick={() => void replay('pause')}>Pause</button>
    <button data-testid="replay-step" disabled={disabled} onClick={() => void replay('step', { count: 1 })}>Step</button>
    <button data-testid="replay-reset" disabled={disabled} onClick={() => void replay('reset')}>Reset</button>
    <label>Speed <select aria-label="Replay speed" disabled={disabled} value={frame?.replay?.speed ?? '1'} onChange={event => { void replay('speed', { speed: event.target.value }); }}>{['0.25', '0.5', '1', '2', '4', '8'].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
    <form onSubmit={event => { event.preventDefault(); void replay('seek', { delivery_index: seek }); }}><label>Delivery <input aria-label="Seek delivery" value={seek} onChange={event => setSeek(event.target.value)} inputMode="numeric" pattern="[0-9]+" maxLength={20} required disabled={disabled} /></label><button disabled={disabled}>Seek</button></form>
    <span className="cursor number">{frame?.replay ? `${frame.replay.cursor.delivery_index} / ${frame.replay.total_deliveries} · ${frame.replay.speed}×` : frame?.source.mode === 'live' ? 'Live mode · replay unavailable' : 'Replay state unavailable'}</span>
  </div>;
}

function PairTable({ pairs, instruments }: { pairs: Pair[]; instruments: Instrument[] }) {
  const venue = (id: string) => instruments.find(item => item.instrument_id === id)?.venue ?? id;
  return <table className="pair-table"><thead><tr><th>Buy → sell</th><th>Feasible base</th><th>Gross ref.</th><th>Depth cost</th><th>Buy fee</th><th>Sell fee</th><th>FX</th><th>Rebalance</th><th>Latency</th><th>Net</th><th>Net bps</th><th>Eligibility / assumptions</th></tr></thead><tbody>{pairs.map(pair => <tr key={`${pair.buy_instrument_id}:${pair.sell_instrument_id}`} className={pair.eligible ? '' : 'excluded'}><th scope="row" title={`${pair.buy_instrument_id} → ${pair.sell_instrument_id}`}>{venue(pair.buy_instrument_id)} → {venue(pair.sell_instrument_id)}<small>{pair.numeraire}</small></th><td><Value value={pair.feasible_base_quantity} /></td><td><Value value={pair.gross_reference} /></td><td><Value value={pair.depth_slippage} /></td><td><Value value={pair.buy_fee} /></td><td><Value value={pair.sell_fee} /></td><td><Value value={pair.fx_cost} /></td><td><Value value={pair.rebalance_cost} /></td><td><Value value={pair.latency_stress} /></td><td><Value value={pair.net} /></td><td><Value value={pair.net_bps} /></td><td><details><summary className={pair.eligible ? 'positive' : 'warning'}>{pair.eligible ? 'Conditional' : 'Excluded'}</summary><Reasons reasons={pair.reasons} /><Reasons reasons={pair.assumptions} /><Details title="Exact pair inputs and decomposition" value={pair} /></details></td></tr>)}</tbody></table>;
}

function CostsPanel({ frame, instruments, connected, calculate }: { frame: Frame | null; instruments: Instrument[]; connected: boolean; calculate: (input: CostInput, signal: AbortSignal) => Promise<Costs> }) {
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [sizeMode, setSizeMode] = useState('base');
  const [size, setSize] = useState('0.01');
  const [feeMode, setFeeMode] = useState('unknown');
  const [fee, setFee] = useState('10');
  const [feeDate, setFeeDate] = useState(new Date().toISOString().slice(0, 10));
  const [provenance, setProvenance] = useState('User-entered informational assumption');
  const [latency, setLatency] = useState('0');
  const [rebalance, setRebalance] = useState('0');
  const [result, setResult] = useState<{ data: Costs; input: string; cursor: string; session: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const selected = instruments.find(item => item.instrument_id === frame?.selection.instrument_id);
  const input: CostInput = { side, ...(sizeMode === 'base' ? { base_quantity: size } : { quote_budget: size }), ...(feeMode === 'assumed' ? { fee_bps: fee, fee_provenance: provenance, fee_date: feeDate } : {}), latency_bps: latency, rebalance_quote: rebalance };
  const inputKey = JSON.stringify(input);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { abort.current?.abort(); setPending(false); setResult(null); setError(null); }, [frame?.session_id, frame?.generation]);
  const submit = async () => {
    if (!frame) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPending(true); setError(null);
    try {
      const data = await calculate(input, controller.signal);
      if (!controller.signal.aborted) setResult({ data, input: inputKey, cursor: frame.replay?.cursor.delivery_index ?? 'live observation', session: frame.session_id });
    } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)); }
    finally { if (!controller.signal.aborted) setPending(false); }
  };
  const data = result?.data;
  const estimate = data?.estimate;
  const valid = result && result.session === frame?.session_id && data?.generation === frame?.generation;
  return <Panel title="Proposed order & comparable venue costs" className="costs-panel" extra={<span className="muted">Informational · not an order ticket</span>}>
    <form className="cost-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label>Side<select aria-label="Order side" value={side} onChange={event => setSide(event.target.value as 'buy' | 'sell')}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      <label>Size convention<select aria-label="Size convention" value={sizeMode} onChange={event => setSizeMode(event.target.value)}><option value="base">Base quantity ({selected?.base.ticker ?? 'base'})</option><option value="budget">Native quote budget ({selected?.quote.ticker ?? 'quote'})</option></select></label>
      <label>{sizeMode === 'base' ? 'Base quantity' : 'Quote budget'}<input aria-label="Order size" value={size} onChange={event => setSize(event.target.value)} inputMode="decimal" maxLength={128} required /></label>
      <label>Fees<select aria-label="Fee assumption" value={feeMode} onChange={event => setFeeMode(event.target.value)}><option value="unknown">Unknown fees</option><option value="assumed">Explicit user assumption</option></select></label>
      {feeMode === 'assumed' && <><label>Fee bps / leg<input aria-label="Fee basis points" value={fee} onChange={event => setFee(event.target.value)} inputMode="decimal" maxLength={128} required /></label><label>Assumption date<input aria-label="Fee date" type="date" value={feeDate} onChange={event => setFeeDate(event.target.value)} required /></label><label className="provenance-input">Provenance<input aria-label="Fee provenance" value={provenance} onChange={event => setProvenance(event.target.value)} maxLength={200} required /></label></>}
      <label>Latency stress bps<input aria-label="Latency stress basis points" value={latency} onChange={event => setLatency(event.target.value)} inputMode="decimal" maxLength={128} /></label>
      <label>Rebalance ({selected?.quote.ticker ?? 'quote'})<input aria-label="Rebalance cost" value={rebalance} onChange={event => setRebalance(event.target.value)} inputMode="decimal" maxLength={128} /></label>
      <button className="primary" data-testid="estimate" disabled={!frame || !connected || frame.status === 'seeking' || pending}>{pending ? 'Calculating…' : 'Estimate & compare'}</button>
    </form>
    <p className="caption">Same native quote / market cohorts only. USD ≠ USDT ≠ USDC. Same feasible base on both legs; prefunded inventory unknown unless supplied by the engine. No atomic execution or market-impact claim.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {!valid ? <Empty>Choose an exact instrument and request an estimate. Unknown fees never become zero.</Empty> : <>
      <div className="result-heading"><span>Snapshot estimate · requested at delivery {result.cursor} · use Estimate again to refresh</span>{result.input !== inputKey && <strong className="warning">Inputs changed · recalculate</strong>}</div>
      <Reasons reasons={data?.reasons ?? []} />
      {!estimate ? <Empty>Execution estimate unavailable. See the exclusion reasons.</Empty> : <><div className="estimate-grid">
        <div><span>Filled base</span><Value value={estimate.filled_base_quantity} /></div><div><span>Unfilled base</span><Value value={estimate.unfilled_base_quantity} /></div><div><span>VWAP ({selected?.quote.ticker})</span><Value value={estimate.vwap} /></div><div><span>Cash notional ({selected?.quote.ticker})</span><Value value={estimate.notional_quote} /></div><div><span>Slippage bps</span><Value value={estimate.slippage_bps} /></div><div><span>Fee ({estimate.fee_asset_id ?? 'unknown'})</span><Value value={estimate.fee_amount} /></div><div><span>Effective price</span><Value value={estimate.effective_price} /></div><div><span>Coverage</span><strong className={estimate.partial ? 'warning' : ''}>{estimate.partial ? 'Partial known depth' : estimate.eligibility.eligible ? 'Eligible' : 'Unavailable'}</strong></div>
      </div><Reasons reasons={estimate.reasons} /><Details title="Exact execution ledger, fee assumptions, consumed levels & provenance" value={estimate} /></>}
      <div className="scroll matrix-scroll">{data?.matrix.length ? <PairTable pairs={data.matrix} instruments={instruments} /> : <Empty>No pair estimates for this size/cohort. No cross-quote conversion is assumed.</Empty>}</div>
    </>}
  </Panel>;
}

export default function App() {
  const { preferences: p, setPreferences, storageError, slots, saveSlot, loadSlot } = useWorkspacePreferences();
  const terminal = useTerminal(p);
  const { catalog, frame, select } = terminal;
  const [query, setQuery] = useState('');
  const [venue, setVenue] = useState('');
  const [quote, setQuote] = useState('');
  const [market, setMarket] = useState('');
  const [instrumentPage, setInstrumentPage] = useState(0);
  const [bottomTab, setBottomTab] = useState<WorkflowTab>('costs');
  const [maximized, setMaximized] = useState<PaneId | null>(null);
  const [slot, setSlot] = useState(0);
  const [slotNotice, setSlotNotice] = useState('');
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const resized = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', resized);
    return () => window.removeEventListener('resize', resized);
  }, []);
  const geometry = workspaceGeometry(p, viewportWidth);
  const fence = frame ? `${frame.session_id}:${frame.generation}:${frame.source.id}:${frame.selection.instrument_id}` : '';
  const attachedFence = useRef<string | null>(null);
  const initialSource = useRef(p.source);
  const [cursor, setCursor] = useState<{ fence: string; value: ChartCursor | null } | null>(null);
  const [domCursor, setDomCursor] = useState<{ fence: string; price: string | null } | null>(null);
  const crosshair = cursor?.fence === fence ? cursor.value : null;
  const setCrosshair = useCallback((value: ChartCursor | null) => setCursor({ fence, value }), [fence]);
  const search = useRef<HTMLInputElement | null>(null);
  const instruments = catalog?.instruments ?? emptyInstruments;
  const selected = instruments.find(item => item.instrument_id === frame?.selection.instrument_id);
  const choices = useMemo(() => ({
    venues: [...new Set(instruments.map(item => item.venue))].sort(),
    quotes: [...new Map(instruments.map(item => [item.quote.asset_id, item.quote])).values()],
    markets: [...new Set(instruments.map(item => item.market_type))],
    cohorts: [...new Map(instruments.map(item => [JSON.stringify([item.quote.asset_id, item.market_type]), item])).entries()],
  }), [instruments]);
  const filtered = useMemo(() => {
    const term = query.toLowerCase();
    return instruments.filter(item => (!venue || item.venue === venue) && (!quote || item.quote.asset_id === quote) && (!market || item.market_type === market) && [item.instrument_id, item.native_symbol, item.venue, item.base.ticker, item.quote.ticker, item.market_type].join(' ').toLowerCase().includes(term));
  }, [instruments, venue, quote, market, query]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / instrumentPageSize));
  const page = Math.min(instrumentPage, pageCount - 1);
  const pageInstruments = filtered.slice(page * instrumentPageSize, (page + 1) * instrumentPageSize);
  const connected = terminal.connection === 'Connected';
  const usable = connected && !!frame && !['seeking', 'unavailable', 'error', 'loading'].includes(frame.status);
  const update = useCallback((value: Partial<WorkspacePreferences>) => setPreferences(previous => ({ ...previous, ...value })), [setPreferences]);
  const updateSecondary = useCallback((value: Partial<WorkspacePreferences>) => setPreferences(previous => ({
    ...previous, secondary: chartSettings({ ...previous.secondary, ...value }),
  })), [setPreferences]);
  const resetViews = useCallback(() => setPreferences(previous => ({
    ...previous, priceStep: '', domPriceStep: '', range: null, follow: true, priceOffset: 0, paused: false, domFrozen: false, tapeFrozen: false,
    secondary: { ...previous.secondary, priceStep: '', range: null, follow: true, priceOffset: 0, paused: false },
  })), [setPreferences]);
  const changeSource = (source: Source) => {
    resetViews();
    update({ source: { mode: source.mode, id: source.id }, sourceExplicit: true });
    const url = new URL(window.location.href);
    if (url.searchParams.has('source')) {
      url.searchParams.set('source', source.id);
      window.history.replaceState(null, '', url);
    }
    terminal.source(source.mode, source.id);
  };
  const liveSource = catalog?.sources.find(source => source.mode === 'live' && source.status === 'ready');
  const views = useMarketView({ frame, connected: usable, preferences: p, recenter: 0, visualization: terminal.visualization, ladder: terminal.ladder, chartEnabled: false, domEnabled: p.rightOpen && (!maximized || maximized === 'dom') });
  const selectContextInstrument = useCallback(async (id: string) => { resetViews(); await select(id); }, [select, resetViews]);
  const workflow = { frame, instruments, connected: usable, busy: terminal.busy, request: terminal.workflow };
  const sourceId = frame?.source.id, sourceMode = frame?.source.mode, instrumentId = frame?.selection.instrument_id;
  useEffect(() => { setInstrumentPage(0); }, [query, venue, quote, market, sourceId]);
  useEffect(() => {
    if (sourceId && sourceMode) setPreferences(previous => previous.source?.id === sourceId && previous.instrument === instrumentId ? previous
      : { ...previous, source: { id: sourceId, mode: sourceMode }, instrument: instrumentId ?? '' });
  }, [sourceId, sourceMode, instrumentId, setPreferences]);
  useEffect(() => {
    // Saved ranges and freezes belong to their source, even before this document
    // attaches evidence. Same-source reloads retain their saved view preferences.
    if (!fence || attachedFence.current === fence) return;
    const replacingEvidence = attachedFence.current !== null;
    attachedFence.current = fence;
    if (!replacingEvidence) {
      if (initialSource.current && (initialSource.current.id !== sourceId || initialSource.current.mode !== sourceMode)) resetViews();
      return;
    }
    setCursor(null); setDomCursor(null);
    setPreferences(previous => ({ ...previous, paused: false, domFrozen: false, tapeFrozen: false, range: null, follow: true,
      secondary: { ...previous.secondary, paused: false, range: null, follow: true } }));
  }, [fence, sourceId, sourceMode, resetViews, setPreferences]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.key === 'Escape') {
        const disclosure = target instanceof Element ? target.closest<HTMLDetailsElement>('details[open]') : null;
        if (disclosure) {
          event.preventDefault();
          event.stopPropagation();
          disclosure.open = false;
          disclosure.querySelector<HTMLElement>(':scope > summary')?.focus();
          return;
        }
        setMaximized(null);
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || target instanceof Element && target.closest('input,select,textarea,button,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
      if (event.key === '/') { event.preventDefault(); update({ leftOpen: true }); search.current?.focus(); }
      else if (event.key.toLowerCase() === 'd') setPreferences(previous => ({ ...previous, domFrozen: !previous.domFrozen }));
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [setPreferences, update]);
  const toggleMaximize = (pane: PaneId) => setMaximized(previous => previous === pane ? null : pane);
  const toggleLink = (pane: PaneId) => { setCrosshair(null); update({ links: { ...p.links, [pane]: !p.links[pane] } }); };
  const linkPrice = (price: string | null) => {
    if (!p.links.dom) { setDomCursor({ fence, price }); return; }
    const reference = views.dom.data?.asof;
    setCrosshair(price !== null && reference ? { time_ns: reference.receive_mono_ns, price } : null);
  };
  const chartProps = { frame, instrument: selected, connected, visualization: terminal.visualization, ladder: terminal.ladder,
    acknowledgeVisualization: terminal.acknowledgeVisualization, crosshair, onCrosshair: setCrosshair };
  const qualityReasons = [...new Set([...(frame?.health.reasons ?? []), ...(frame?.source.reasons ?? []), ...(frame?.book?.quality.reason_codes ?? [])])];
  const style = { '--left-width': `${p.leftOpen ? p.leftWidth : 0}px`, '--right-width': `${p.rightOpen ? geometry.rightWidth : 0}px`, '--bottom-height': `${p.bottomOpen ? p.bottomHeight : 0}px`, '--chart-split': `${p.chartSplit}%`, '--tape-split': `${p.tapeSplit}%` } as CSSProperties;
  return <div className="terminal workstation" style={style}>
    <header className="topbar">
      <div className="selected-identity"><strong>{selected ? `${selected.base.ticker} / ${selected.quote.ticker}` : 'Select instrument'}</strong><span title={selected ? `${selected.venue} · ${selected.market_type} · ${selected.native_symbol}` : undefined}>{selected ? `${selected.venue} · ${selected.market_type} · ${selected.native_symbol}` : 'Exact public-market identities'}</span></div>
      <input ref={search} className="top-search" aria-label="Search instruments" placeholder="Search instruments /" type="search" value={query} onChange={event => { setQuery(event.target.value); update({ leftOpen: true }); }} />
      <label className="source-control">Source<select aria-label="Data source" title={frame?.source.label} value={frame?.source.id ?? p.source?.id ?? ''} disabled={!catalog} onChange={event => { const source = catalog?.sources.find(item => item.id === event.target.value); if (source?.status === 'ready') changeSource(source); }}>{!frame && <option value="">Choose source…</option>}{catalog?.sources.map(source => <option key={source.id} value={source.id} disabled={source.status !== 'ready'} title={[source.label, ...source.reasons].join('; ')}>{abbreviateCaptureLabel(source.label)} · {source.mode} · {source.status}{source.synthetic ? ' · SYNTHETIC' : ''}{source.reasons.length ? ` · ${source.reasons.join('; ')}` : ''}</option>)}</select></label>
      <label>Preset<select aria-label="Workspace preset" value={p.preset} onChange={event => { setMaximized(null); update(presetLayout(event.target.value as WorkspacePreferences['preset'])); }}><option value="balanced">Balanced</option><option value="analysis">Analysis</option><option value="ladder">DOM focus</option></select></label>
      <details className="workspace-slots"><summary>Workspaces</summary><div className="workspace-slot-controls"><label>Slot<select aria-label="Workspace slot" value={slot} onChange={event => { setSlot(Number(event.target.value)); setSlotNotice(''); }}>{slots.map((saved, index) => <option key={index} value={index}>Slot {index + 1}{saved ? ' · saved' : ' · empty'}</option>)}</select></label><button onClick={() => { saveSlot(slot); setSlotNotice(`Slot ${slot + 1} captured`); }}>Save workspace</button><button disabled={!slots[slot]} onClick={() => { loadSlot(slot); setMaximized(null); setCrosshair(null); setSlotNotice(`Slot ${slot + 1} loaded`); }}>Load workspace</button><span role="status">{slotNotice}</span></div></details>
      <span className={`status ${connected ? 'positive' : 'warning'}`}>{terminal.connection}</span><button onClick={terminal.reconnect} aria-label="Reconnect terminal">Reconnect</button>
    </header>
    <div className="source-banner"><strong>{frame?.source.synthetic ? 'SYNTHETIC DEMO' : frame?.source.recording ? 'RECORDED' : frame?.source.mode.toUpperCase() ?? 'CONNECTING'}</strong><span title={frame?.source.label}>{abbreviateCaptureLabel(frame?.source.label ?? 'Opening independent session')}</span><span className={`source-readiness ${frame?.health.ready && usable ? 'positive' : 'warning'}`} title={qualityReasons.join('; ')}>{frame?.health.ready && usable ? frame.book && !frame.book.quality.coverage.full_depth ? 'Ready · partial depth' : 'Ready' : usable ? 'Partial / not ready' : 'Unavailable'}</span><span className="source-state">{label(frame?.status ?? 'loading')}</span>
      <div className="pane-toggles">{(['leftOpen', 'rightOpen', 'bottomOpen'] as const).map((key, index) => <button key={key} aria-pressed={p[key]} onClick={() => { setMaximized(null); update({ [key]: !p[key] }); }}>{['Watchlist', 'DOM / tape', 'Costs / analytics'][index]}</button>)}</div>
      <details className="source-inspector"><summary>Source &amp; instrument details</summary><div className="source-inspector-body">
        <p className="caption">Limit Orderbook · exact public-market workstation</p>
        {terminal.preferenceNotice && <p role="status" className="caption" data-testid="source-preference-notice">{terminal.preferenceNotice}</p>}
        <p>{frame?.source.recording ? 'Recorded capture · playback' : 'Not recording'}</p>
        <p className="caption">Current source, feed and readiness reasons</p>
        {qualityReasons.length ? <Reasons reasons={qualityReasons} /> : <p className="caption">No source quality reasons reported.</p>}
        {frame?.book && <p className="caption" data-testid="native-depth">Native retained depth: {frame.book.quality.coverage.retained_bid_levels} bid / {frame.book.quality.coverage.retained_ask_levels} ask levels · frame shows {frame.book.bids.length} / {frame.book.asks.length}. Grouped DOM and chart rows are presentation buckets, not a count of individual orders.</p>}
        <dl className="metadata"><dt>Native feed mode</dt><dd>{frame?.book?.quality.source_mode ?? 'Awaiting evidence'}</dd><dt>Recorded source mode</dt><dd>{frame?.book?.quality.recorded_source_mode ?? '—'}</dd><dt>Capture complete</dt><dd>{frame?.book ? String(frame.book.quality.capture_complete) : 'Unknown'}</dd><dt>Coverage</dt><dd>{frame?.book?.quality.coverage.full_depth ? 'Full depth' : 'Bounded / unknown outside scope'}</dd><dt>Source state</dt><dd>{frame?.source.status ?? 'Loading'}</dd><dt>Settlement</dt><dd>{selected?.settlement?.ticker ?? 'None declared'}</dd><dt>Tick / lot</dt><dd><Value value={selected?.tick_size} /> / <Value value={selected?.lot_size} /></dd><dt>Execution access</dt><dd>{selected?.execution_access ?? 'Unknown'}</dd></dl>
        {frame?.book && (frame.book.presentation.bids_omitted > 0 || frame.book.presentation.asks_omitted > 0) && <p className="caption">Frame carries the first {frame.book.presentation.depth_limit} levels per side. Native reconstruction, grouped ladder and cost walks retain full source depth.</p>}
        <Details title="Exact instrument metadata" value={selected ?? null} /><Details title="Source & capture descriptor" value={frame?.source ?? null} />
        <Details title="Available source readiness & reasons" value={catalog?.sources ?? []} /><Details title={`Live ingestion evidence · ${frame?.telemetry.ingestion.length ?? 0} streams`} value={frame?.telemetry.ingestion ?? []} />
      </div></details>
    </div>
    {frame?.source.synthetic && <div className="warning-banner" role="status" data-testid="synthetic-guidance">
      <strong>Synthetic demo — not live market data.</strong>{' '}
      Sparse fixture L2; only observations delivered at the replay cursor.{' '}
      <button disabled={terminal.busy || !connected || frame.status !== 'paused'} onClick={() => void terminal.replay('play')}>Play synthetic demo</button>{' '}
      {liveSource ? <button disabled={terminal.busy} onClick={() => changeSource(liveSource)}>Switch to public live data</button>
        : <span>Public live data is unavailable{catalog?.sources.filter(source => source.mode === 'live').map(source => ` · ${source.status}${source.reasons.length ? `: ${source.reasons.join('; ')}` : ''}`).join('') || ' · operator must launch serve --network'}. No live data is substituted.</span>}
    </div>}
    {(terminal.error || frame?.error) && <div className="error-banner" role="alert">{terminal.error ?? frame?.error}</div>}
    {storageError && <div className="warning-banner" role="status">{storageError}</div>}
    {terminal.catalogError && <div className="warning-banner" role="status">{terminal.catalogError} · retrying automatically without replacing the current session.</div>}
    {frame?.status === 'seeking' && <div className="warning-banner" role="status">Seeking and rebuilding analytics. Previous evidence is unavailable until reconstruction completes.</div>}
    {frame?.replay && <ReplayControls frame={frame} busy={terminal.busy || !connected} replay={terminal.replay} />}
    <main className={`workspace workstation-workspace ${p.leftOpen ? '' : 'left-collapsed'} ${p.rightOpen ? '' : 'right-collapsed'} ${p.bottomOpen ? '' : 'bottom-collapsed'}`} data-testid="workspace" data-maximized={maximized ?? ''}>
      {p.leftOpen && <aside className="sidebar"><Panel title="Instruments" extra={<span className="muted">{filtered.length}</span>}>
        <div className="instrument-filters">
          <select aria-label="Filter venue" value={venue} onChange={event => setVenue(event.target.value)}><option value="">All venues</option>{choices.venues.map(value => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter quote" value={quote} onChange={event => setQuote(event.target.value)}><option value="">All exact quote assets</option>{choices.quotes.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.ticker} · {asset.asset_id}</option>)}</select>
          <select aria-label="Filter market" value={market} onChange={event => setMarket(event.target.value)}><option value="">All markets</option>{choices.markets.map(value => <option key={value}>{value}</option>)}</select>
      <label className="cohort-control">Cohort<select aria-label="Native cohort" value={quote && market ? JSON.stringify([quote, market]) : quote || market ? 'custom' : ''} onChange={event => { const [nextQuote, nextMarket] = event.target.value ? JSON.parse(event.target.value) as [string, string] : ['', '']; setQuote(nextQuote); setMarket(nextMarket); update({ leftOpen: true }); }}><option value="">All native cohorts</option>{(quote || market) && !(quote && market) && <option value="custom" disabled>Custom filters</option>}{choices.cohorts.map(([value, item]) => <option key={value} value={value}>{item.quote.ticker} · {item.market_type} · {item.quote.asset_id}</option>)}</select></label>
        </div>
        <div className="instrument-list scroll" key={JSON.stringify([sourceId, query, venue, quote, market, page])}>{!filtered.length ? <Empty>{catalog ? 'No exact instruments match these filters.' : 'Loading instrument catalog…'}</Empty> : pageInstruments.map(item => <button className={`instrument ${item.instrument_id === selected?.instrument_id ? 'selected' : ''}`} key={item.instrument_id} title={item.instrument_id} data-instrument-id={item.instrument_id} aria-pressed={item.instrument_id === selected?.instrument_id} disabled={terminal.busy || !usable} onClick={() => void selectContextInstrument(item.instrument_id)}><span><strong>{item.native_symbol}</strong><small>{item.venue}</small></span><span><small>{item.market_type} · {item.quote.ticker}</small><small>{item.status} / {item.data_access}</small></span></button>)}</div>
        {pageCount > 1 && <nav className="panel-toolbar" aria-label="Instrument pages"><button aria-label="Previous instrument page" disabled={page === 0} onClick={() => setInstrumentPage(page - 1)}>Prev</button><span>{page + 1} / {pageCount} · {instrumentPageSize}/page</span><button aria-label="Next instrument page" disabled={page + 1 >= pageCount} onClick={() => setInstrumentPage(page + 1)}>Next</button></nav>}
      </Panel></aside>}
      {p.leftOpen && <div className="left-resizer"><ResizeHandle axis="horizontal" name="Watchlist width" value={p.leftWidth} min={180} max={360} onChange={leftWidth => update({ leftWidth })} /></div>}
      <section className="chart-stack" aria-label="Market visualizations">
        <div className="chart-slot primary-chart" hidden={!!maximized && maximized !== 'heatmap'}><ChartPane {...chartProps} key={`heatmap:${fence}`} id="heatmap" title="Liquidity" preferences={p} update={update} linked={p.links.heatmap} onLinked={() => toggleLink('heatmap')} maximized={maximized === 'heatmap'} onMaximize={() => toggleMaximize('heatmap')} active={!maximized || maximized === 'heatmap'} /></div>
        <div className="chart-splitter"><ResizeHandle axis="vertical" proportional name="Chart split" value={p.chartSplit} min={30} max={70} onChange={chartSplit => update({ chartSplit })} /></div>
        <div className="chart-slot secondary-chart" hidden={!!maximized && maximized !== 'flow'}><ChartPane {...chartProps} key={`flow:${fence}`} id="flow" title="Order flow" preferences={{ ...p, ...p.secondary }} update={updateSecondary} linked={p.links.flow} onLinked={() => toggleLink('flow')} maximized={maximized === 'flow'} onMaximize={() => toggleMaximize('flow')} active={!maximized || maximized === 'flow'} /></div>
      </section>
      {p.rightOpen && <><div className="right-resizer"><ResizeHandle axis="horizontal" reverse name="DOM width" value={geometry.rightWidth} min={geometry.min} max={geometry.max} onChange={rightWidth => { if (rightWidth !== geometry.rightWidth) update({ rightWidth, rightWidthMode: 'manual' }); }} /></div><aside className="book-tape">
        <section className="dom-pane" data-pane="dom" hidden={!!maximized && maximized !== 'dom'}>
          <DepthLadder key={fence} extra={<PaneActions pane="DOM" linked={p.links.dom} onLinked={() => toggleLink('dom')} maximized={maximized === 'dom'} onMaximize={() => toggleMaximize('dom')} />} data={usable ? views.dom.data : null} loading={views.dom.loading} error={!usable ? 'Stream evidence unavailable' : views.dom.error} frozen={p.domFrozen} viewPaused={false} rows={p.domRows} onRows={domRows => update({ domRows })} onFreeze={() => update({ domFrozen: !p.domFrozen })} crosshair={p.links.dom ? crosshair?.price ?? null : domCursor?.fence === fence ? domCursor.price : null} onPrice={linkPrice} priceStep={p.domPriceStep} onPriceStep={domPriceStep => update({ domPriceStep })} unit={p.domUnit} onUnit={domUnit => update({ domUnit })} cumulative={p.domCumulative} onCumulative={domCumulative => update({ domCumulative })} sourceMode={frame?.source.mode ?? 'demo'} />
          <Freshness frame={frame} reference={usable ? views.dom.data?.asof ?? null : null} connected={connected} label="Book" frozen={p.domFrozen} />
        </section>
        <div className="tape-splitter"><ResizeHandle axis="vertical" proportional name="DOM and tape split" value={p.tapeSplit} min={30} max={75} onChange={tapeSplit => update({ tapeSplit })} /></div>
        <section className="tape-pane" data-pane="tape" hidden={!!maximized && maximized !== 'tape'}><Tape key={fence} frame={frame} connected={connected} instrument={selected} preferences={p} update={update} crosshair={crosshair} onCrosshair={setCrosshair} maximized={maximized === 'tape'} onMaximize={() => toggleMaximize('tape')} /></section>
      </aside></>}
      {p.bottomOpen && <><div className="bottom-resizer"><ResizeHandle axis="vertical" reverse name="Research pane height" value={p.bottomHeight} min={170} max={460} onChange={bottomHeight => update({ bottomHeight })} /></div><section className="research-pane">
        <div className="tabs research-tabs" role="tablist" aria-label="Research pane">{workflowTabs.map(([id, name]) => <button key={id} id={`workflow-tab-${id}`} role="tab" aria-selected={bottomTab === id} aria-controls={`workflow-panel-${id}`} onClick={() => setBottomTab(id)}>{name}</button>)}<span className="muted">Shared-core evidence · no execution</span></div>
        <div className="research-scroll">
          <div id="workflow-panel-costs" role="tabpanel" aria-labelledby="workflow-tab-costs" hidden={bottomTab !== 'costs'}><CostsPanel frame={frame} instruments={instruments} connected={usable} calculate={terminal.costs} /><ProposalRiskPanel frame={frame} connected={usable} calculate={terminal.proposalRisk} /></div>
          <div id="workflow-panel-analytics" role="tabpanel" aria-labelledby="workflow-tab-analytics" hidden={bottomTab !== 'analytics'}><AnalyticsPanel analytics={usable ? frame?.analytics ?? null : null} /></div>
          <div id="workflow-panel-alerts" role="tabpanel" aria-labelledby="workflow-tab-alerts" hidden={bottomTab !== 'alerts'}><AlertsPanel {...workflow} active={bottomTab === 'alerts'} /></div>
          <div id="workflow-panel-context" role="tabpanel" aria-labelledby="workflow-tab-context" hidden={bottomTab !== 'context'}><ContextPanel {...workflow} active={bottomTab === 'context'} selectInstrument={selectContextInstrument} /></div>
          <div id="workflow-panel-paper" role="tabpanel" aria-labelledby="workflow-tab-paper" hidden={bottomTab !== 'paper'}><PaperPanel {...workflow} active={bottomTab === 'paper'} resumeRun={terminal.resumePaper} /></div>
          <div id="workflow-panel-journal" role="tabpanel" aria-labelledby="workflow-tab-journal" hidden={bottomTab !== 'journal'}><JournalPanel {...workflow} active={bottomTab === 'journal'} /></div>
          <div id="workflow-panel-research" role="tabpanel" aria-labelledby="workflow-tab-research" hidden={bottomTab !== 'research'}><ResearchPanel {...workflow} active={bottomTab === 'research'} /></div>
        </div>
      </section></>}
    </main>
    <details className="diagnostics"><summary>Diagnostics</summary><footer className="telemetry"><span>Session {frame?.session_id.slice(0, 8) ?? '—'} · generation {frame?.generation ?? '—'}</span><span>Queue {frame?.telemetry.queue_depth ?? '—'} / high {frame?.telemetry.queue_highwater ?? '—'}</span><span>Coalesced {frame?.telemetry.coalesced ?? '—'} · overflow {frame?.telemetry.overflows ?? '—'}</span><span title="ACKs evicted from the bounded server buffer">ACK expired {frame?.telemetry.ack_expired ?? '—'}</span><span>Observer {milliseconds(frame?.telemetry.observer_duration_ms)}</span><span title="Two paint opportunities; not GPU completion">Receive→paint {milliseconds(frame?.telemetry.browser_receive_to_paint_ms)}</span><span title="Conservative round-trip upper bound">Publish→ACK ≤ {milliseconds(frame?.telemetry.publish_to_ack_upper_bound_ms)}</span></footer><Details title={`Live ingestion evidence · ${frame?.telemetry.ingestion.length ?? 0} streams`} value={frame?.telemetry.ingestion ?? []} /></details>
  </div>;
}
