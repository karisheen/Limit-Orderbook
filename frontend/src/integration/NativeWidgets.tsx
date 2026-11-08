import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Analytics, CostInput, Costs, Frame, Instrument, Metric, Pair, ReplayAction } from '../api';
import { errorMessage } from '../api';
import { exact, label, timestamp } from '../format';
import type { ChartCursor } from '../MarketCharts';
import { Freshness } from '../Freshness';
import type { WorkspacePreferences } from '../workspace';
import { Details, Empty, Panel, Reasons, Value } from '../WorkflowUI';

function MetricRow({ metric }: { metric: Metric }) {
  return <tr><th scope="row">{label(metric.name)}</th><td><Value value={metric.value} /></td><td className="muted">{metric.unit}</td><td>
    <details><summary className={metric.eligibility.eligible ? 'positive' : 'warning'}>{metric.eligibility.eligible ? 'Available' : 'Unavailable'}</summary>
      <Reasons reasons={metric.eligibility.reasons} />
      <dl className="metadata"><dt>As of (receive UTC)</dt><dd>{timestamp(metric.asof?.receive_wall_ns)}</dd><dt>Formula</dt><dd>{metric.formula_version}</dd><dt>Exact instrument</dt><dd>{metric.instrument_id}</dd></dl>
      <Details title="Window, quality and reproducible references" value={metric} />
    </details>
  </td></tr>;
}

export function AnalyticsPanel({ analytics }: { analytics: Analytics | null }) {
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


export function ResizeHandle({ axis, value, min, max, reverse = false, proportional = false, onChange, name }: {
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

export function PaneActions({ pane, linked, onLinked, maximized, onMaximize }: { pane: string; linked: boolean; onLinked: () => void; maximized: boolean; onMaximize: () => void }) {
  return <span className="pane-actions"><button aria-label={`Link ${pane} cursor`} aria-pressed={linked} onClick={onLinked}>Cursor</button><button aria-label={`${maximized ? 'Restore' : 'Maximize'} ${pane}`} onClick={onMaximize}>{maximized ? 'Restore' : 'Max'}</button></span>;
}

export function Tape({ frame, connected, instrument, preferences: p, update, crosshair, onCrosshair, maximized, onMaximize }: {
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
    const dismissOutside = (event: PointerEvent | FocusEvent) => {
      if (!disclosure.open || !(event.target instanceof Node) || disclosure.contains(event.target)) return;
      const restoreFocus = event.type === 'pointerdown' && disclosure.contains(document.activeElement);
      disclosure.open = false;
      positionSettings();
      if (restoreFocus) summary.focus({ preventScroll: true });
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.key !== 'Escape' || !disclosure.open) return;
      event.preventDefault();
      event.stopPropagation();
      disclosure.open = false;
      positionSettings();
      summary.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', dismissOutside, true);
    document.addEventListener('focusin', dismissOutside);
    disclosure.addEventListener('keydown', dismissWithEscape);
    disclosure.addEventListener('toggle', positionSettings);
    window.addEventListener('resize', positionSettings);
    window.addEventListener('scroll', positionSettings, true);
    const viewport = window.visualViewport;
    viewport?.addEventListener('resize', positionSettings);
    viewport?.addEventListener('scroll', positionSettings);
    return () => {
      observer.disconnect();
      document.removeEventListener('pointerdown', dismissOutside, true);
      document.removeEventListener('focusin', dismissOutside);
      disclosure.removeEventListener('keydown', dismissWithEscape);
      disclosure.removeEventListener('toggle', positionSettings);
      window.removeEventListener('resize', positionSettings);
      window.removeEventListener('scroll', positionSettings, true);
      viewport?.removeEventListener('resize', positionSettings);
      viewport?.removeEventListener('scroll', positionSettings);
    };
  }, [positionSettings]);
  const tapeCursor = p.links.tape ? crosshair : localCursor;
  const setTapeCursor = p.links.tape ? onCrosshair : setLocalCursor;
  useEffect(() => {
    const clear = () => setLocalCursor(null);
    window.addEventListener('terminal-clear-cursor', clear);
    return () => window.removeEventListener('terminal-clear-cursor', clear);
  }, []);
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
      onKeyDown={event => {
        if (event.key !== 'Escape') return;
        event.preventDefault(); event.stopPropagation();
        const disclosure = (event.target as HTMLElement).closest<HTMLDetailsElement>('details[open]');
        if (disclosure) { disclosure.open = false; disclosure.querySelector<HTMLElement>(':scope > summary')?.focus(); }
        else closeInspector();
      }}>
      <div className="tape-inspector-heading"><strong>Native print · pinned snapshot</strong><button onClick={closeInspector} aria-label="Close trade inspector">Close</button></div>
      <dl className="metadata"><dt>Receive UTC</dt><dd>{timestamp(inspected.print.asof.receive_wall_ns)}</dd><dt>Source</dt><dd>{inspected.source.label} · {inspected.source.synthetic ? 'Synthetic' : inspected.source.recording ? 'Recorded' : inspected.source.mode}</dd><dt>Origin</dt><dd>{inspected.print.trade.is_subscription_history ? 'Subscription history' : 'Observed print'}</dd><dt>Side provenance</dt><dd>{inspected.print.trade.side_provenance}</dd></dl>
      <Details title="Exact native print, timestamps, units and source" value={{ print: inspected.print, source: inspected.source }} />
    </div>}
  </Panel>;
}

export function ReplayControls({ frame, busy, replay }: { frame: Frame | null; busy: boolean; replay: (action: ReplayAction, payload?: Record<string, unknown>) => Promise<void> }) {
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
  return <table className="pair-table"><thead><tr><th>Buy → sell</th><th>Feasible base</th><th>Gross ref.</th><th>Depth cost</th><th>Buy fee</th><th>Sell fee</th><th>Rebalance</th><th>Latency</th><th>Net</th><th>Net bps</th><th>Eligibility / assumptions</th></tr></thead><tbody>{pairs.map(pair => <tr key={`${pair.buy_instrument_id}:${pair.sell_instrument_id}`} className={pair.eligible ? '' : 'excluded'}><th scope="row" title={`${pair.buy_instrument_id} → ${pair.sell_instrument_id}`}>{venue(pair.buy_instrument_id)} → {venue(pair.sell_instrument_id)}<small>{pair.numeraire}</small></th><td><Value value={pair.feasible_base_quantity} /></td><td><Value value={pair.gross_reference} /></td><td><Value value={pair.depth_slippage} /></td><td><Value value={pair.buy_fee} /></td><td><Value value={pair.sell_fee} /></td><td><Value value={pair.rebalance_cost} /></td><td><Value value={pair.latency_stress} /></td><td><Value value={pair.net} /></td><td><Value value={pair.net_bps} /></td><td><details><summary className={pair.eligible ? 'positive' : 'warning'}>{pair.eligible ? 'Conditional' : 'Excluded'}</summary><Reasons reasons={pair.reasons} /><Reasons reasons={pair.assumptions} /><Details title="Exact pair inputs and decomposition" value={pair} /></details></td></tr>)}</tbody></table>;
}

export function CostsPanel({ frame, instruments, connected, calculate }: { frame: Frame | null; instruments: Instrument[]; connected: boolean; calculate: (input: CostInput, signal: AbortSignal) => Promise<Costs> }) {
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
