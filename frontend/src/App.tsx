import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { Analytics, CostInput, Costs, Frame, GroupedLadder, Instrument, Metric, Pair, ReplayAction, Source } from './api';
import { errorMessage } from './api';
import { exact, label, milliseconds, timestamp } from './format';
import { useTerminal } from './useTerminal';
import { MarketCharts } from './MarketCharts';
import { ProposalRiskPanel } from './ProposalRiskPanel';
import { useMarketView } from './useMarketView';
import { useChartTelemetry } from './useChartTelemetry';
import { presetLayout, useWorkspacePreferences } from './workspace';
import type { ChartMode, WorkspacePreferences } from './workspace';
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

function Ladder({ data, loading, error, frozen, viewPaused, rows, onRows, onFreeze, crosshair, onPrice }: {
  data: GroupedLadder | null; loading: boolean; error: string | null; frozen: boolean; viewPaused: boolean; rows: number;
  onRows: (rows: number) => void; onFreeze: () => void; crosshair: string | null; onPrice: (price: string) => void;
}) {
  const middle = useRef<HTMLTableRowElement | null>(null);
  const scroll = useRef<HTMLDivElement | null>(null);
  const centerDom = useCallback(() => {
    const container = scroll.current, boundary = middle.current;
    if (!container || !boundary) return;
    const row = boundary.getBoundingClientRect();
    container.scrollTop += row.top - container.getBoundingClientRect().top - container.clientHeight / 2 + row.height / 2;
  }, []);
  const centering = useRef<{ rows: number; instrument: string | undefined; data: GroupedLadder | null; pending: boolean }>({ rows, instrument: undefined, data: null, pending: true });
  const asks = data?.rows.filter(row => row.side === 'ask').reverse() ?? [];
  const bids = data?.rows.filter(row => row.side === 'bid') ?? [];
  const hasBothSides = asks.length > 0 && bids.length > 0;
  useEffect(() => {
    const prior = centering.current;
    const fresh = data !== prior.data;
    const scrollable = !!scroll.current && scroll.current.scrollHeight > scroll.current.clientHeight;
    const pending = prior.pending || prior.rows !== rows || prior.instrument !== data?.instrument_id || !hasBothSides || !scrollable;
    centering.current = { rows, instrument: data?.instrument_id, data, pending };
    if (!pending || !fresh || !hasBothSides || !scroll.current || !middle.current) return;
    centerDom();
    centering.current.pending = !scrollable;
  }, [data, rows, hasBothSides, centerDom]);
  useEffect(() => {
    const container = scroll.current;
    if (!container) return;
    let width = container.clientWidth, height = container.clientHeight;
    const observer = new ResizeObserver(() => {
      if (container.clientWidth === width && container.clientHeight === height) return;
      width = container.clientWidth; height = container.clientHeight;
      centerDom();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [centerDom]);
  return <Panel title="Order book" className="ladder-panel" extra={<div className="tabs"><button aria-pressed={frozen} onClick={onFreeze} data-testid="dom-freeze">{frozen ? 'Unfreeze DOM' : 'Freeze DOM'}</button><button onClick={centerDom}>Recenter DOM</button></div>}>
    <div className="panel-toolbar"><label>Rows <select aria-label="Ladder rows" value={rows} onChange={event => onRows(Number(event.target.value))}>{![10, 15, 25, 50, 100].includes(rows) && <option>{rows}</option>}{[10, 15, 25, 50, 100].map(value => <option key={value}>{value}</option>)}</select></label><span>{data?.unit ?? 'Unit unavailable'} · step {data?.step ?? '—'}{loading ? ' · updating' : ''}</span></div>
    <p className="caption">Aggregate L2 resting bid / ask quantities, not individual orders or executed trades. Showing {bids.length} bid / {asks.length} ask price buckets; row limits affect presentation only.</p>
    {(frozen || viewPaused) && <p className="caption warning">Frozen DOM snapshot{viewPaused ? ' · global view paused' : ''} · tape and collection continue</p>}
    {error && <p className="error" role="alert">DOM unavailable / last snapshot stale: {error}</p>}
    <Reasons reasons={data?.reasons ?? []} />
    <div ref={scroll} className="scroll ladder-scroll">{!data?.rows.length ? <Empty>No eligible grouped depth. Unknown coverage is not zero.</Empty> : <table className="ladder" data-testid="grouped-ladder"><thead><tr><th>Side</th><th>Price</th><th>Base size</th><th>Cumulative base</th></tr></thead><tbody>{[...asks, ...bids].map((row, index) => <tr key={`${row.side}:${row.price}`} ref={index === asks.length ? middle : undefined} className={`${row.side} ${crosshair === row.price ? 'crosshair-row' : ''}`} onPointerEnter={() => onPrice(row.price)}><td>{row.side}</td><td><Value value={row.price} /></td><td><Value value={row.base_quantity} /></td><td><Value value={row.cumulative_base} /></td></tr>)}</tbody></table>}</div>
    <Details title={`${data?.eligible ? 'Eligible grouped evidence' : 'Unavailable / diagnostic'} · as of ${timestamp(data?.asof?.receive_wall_ns)}`} value={data} />
  </Panel>;
}

function ResizeHandle({ axis, value, min, max, reverse = false, onChange, name }: {
  axis: 'horizontal' | 'vertical'; value: number; min: number; max: number; reverse?: boolean; onChange: (value: number) => void; name: string;
}) {
  const drag = useRef<{ position: number; value: number } | null>(null);
  return <div className={`resize-handle resize-${axis}`} role="separator" aria-label={name} aria-orientation={axis === 'horizontal' ? 'vertical' : 'horizontal'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0}
    onPointerDown={event => { event.preventDefault(); event.currentTarget.focus(); drag.current = { position: axis === 'horizontal' ? event.clientX : event.clientY, value }; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => {
      if (!drag.current) return;
      const distance = (axis === 'horizontal' ? event.clientX : event.clientY) - drag.current.position;
      const size = drag.current.value + distance * (reverse ? -1 : 1);
      onChange(Math.round(Math.max(min, Math.min(max, size))));
    }}
    onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); onChange(Math.max(min, Math.min(max, value + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 10 : -10) * (reverse ? -1 : 1)))); } }} />;
}

function Tape({ analytics, instrument, synthetic }: { analytics: Analytics | null; instrument?: Instrument; synthetic: boolean }) {
  const prints = analytics?.tape.slice(-100).reverse() ?? [];
  return <Panel title="Time & sales" className="tape-panel" extra={<span className="muted">Last {prints.length} · receive UTC</span>}>
    <p className="caption">{synthetic ? 'Synthetic fixture prints, not live executions' : 'Public executed trade prints, not resting L2 orders'} · subscription history labeled, excluded from default live flow.</p>
    <div className="scroll tape-scroll">{!prints.length ? <Empty>No public trades observed at this cursor. No past prints are invented.</Empty> : <table><thead><tr><th>Time</th><th>Side</th><th>Price {instrument?.quote.ticker}</th><th>Base size</th><th>Origin</th></tr></thead><tbody>{prints.map(item => <tr key={`${item.stream_id}:${item.trade.dedup_key}`}><td title={`Receive ${item.asof.receive_wall_ns}; exchange ${exact(item.asof.exchange_event_wall_ns)}`}>{timestamp(item.asof.receive_wall_ns)}</td><td className={item.trade.taker_side === 'buy' ? 'bid' : item.trade.taker_side === 'sell' ? 'ask' : 'muted'} title={item.trade.side_provenance}>{item.trade.taker_side}</td><td><Value value={item.trade.price} /></td><td><Value value={item.base_quantity} /></td><td>{item.trade.is_subscription_history ? <span className="warning">History</span> : 'Observed'}</td></tr>)}</tbody></table>}</div>
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
  const { preferences: p, setPreferences, storageError } = useWorkspacePreferences();
  const terminal = useTerminal(p);
  const { catalog, frame, select } = terminal;
  const [query, setQuery] = useState('');
  const [venue, setVenue] = useState('');
  const [quote, setQuote] = useState('');
  const [market, setMarket] = useState('');
  const [instrumentPage, setInstrumentPage] = useState(0);
  const [bottomTab, setBottomTab] = useState<WorkflowTab>('costs');
  const [crosshair, setCrosshair] = useState<{ time_ns: string; price: string | null } | null>(null);
  const [recenter, setRecenter] = useState(0);
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
  const usable = connected && frame?.status !== 'seeking';
  const update = useCallback((value: Partial<WorkspacePreferences>) => setPreferences(previous => ({ ...previous, ...value })), [setPreferences]);
  const changeSource = (source: Source) => {
    update({ source: { mode: source.mode, id: source.id }, sourceExplicit: true, priceStep: '', range: null, follow: true, priceOffset: 0, paused: false, domFrozen: false });
    const url = new URL(window.location.href);
    if (url.searchParams.has('source')) {
      url.searchParams.set('source', source.id);
      window.history.replaceState(null, '', url);
    }
    terminal.source(source.mode, source.id);
  };
  const liveSource = catalog?.sources.find(source => source.mode === 'live' && source.status === 'ready');
  const views = useMarketView({ frame, connected, preferences: p, recenter, visualization: terminal.visualization, ladder: terminal.ladder });
  const centerView = useCallback(() => { update({ priceOffset: 0, follow: true, range: null, paused: false }); setRecenter(value => value + 1); }, [update]);
  const selectContextInstrument = useCallback(async (id: string) => {
    update({ priceStep: '', range: null, follow: true, priceOffset: 0 });
    await select(id);
  }, [select, update]);
  const workflow = { frame, instruments, connected, busy: terminal.busy, request: terminal.workflow };
  const sourceId = frame?.source.id, sourceMode = frame?.source.mode, instrumentId = frame?.selection.instrument_id;
  useEffect(() => { setInstrumentPage(0); }, [query, venue, quote, market, sourceId]);
  useEffect(() => {
    if (sourceId && sourceMode) setPreferences(previous => previous.source?.id === sourceId && previous.instrument === instrumentId ? previous
      : { ...previous, ...(previous.source && previous.source.id !== sourceId ? { priceStep: '', range: null, follow: true, priceOffset: 0, paused: false, domFrozen: false } : {}), source: { id: sourceId, mode: sourceMode }, instrument: instrumentId ?? '' });
  }, [sourceId, sourceMode, instrumentId, setPreferences]);
  useEffect(() => { setCrosshair(null); }, [frame?.session_id, frame?.generation]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || target instanceof Element && target.closest('input,select,textarea,button,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
      if (event.key === '/') { event.preventDefault(); search.current?.focus(); }
      else if (event.code === 'Space') { event.preventDefault(); setPreferences(previous => ({ ...previous, paused: !previous.paused })); }
      else if (event.key.toLowerCase() === 'r') centerView();
      else if (event.key.toLowerCase() === 'd') setPreferences(previous => ({ ...previous, domFrozen: !previous.domFrozen }));
      else if (/^[1-6]$/.test(event.key)) update({ mode: (['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'] as ChartMode[])[Number(event.key) - 1] });
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [centerView, setPreferences, update]);
  const changeRange = useCallback((range: { start_ns: string; end_ns: string }) => update({ range, follow: false }), [update]);
  const panPrice = useCallback((rows: number) => setPreferences(previous => ({ ...previous, priceOffset: Math.max(-10000, Math.min(10000, previous.priceOffset + Math.round(rows))) })), [setPreferences]);
  const chartView = usable ? views.chart.data : null;
  const chartTelemetry = useChartTelemetry(frame, chartView, terminal.acknowledgeVisualization);
  const linkPrice = (price: string) => {
    if (!chartView) return;
    const current = crosshair?.time_ns;
    const time = current && BigInt(current) >= BigInt(chartView.start_ns) && BigInt(current) < BigInt(chartView.end_ns)
      ? current : chartView.columns.at(-1)?.time_ns;
    if (time) setCrosshair({ time_ns: time, price });
  };
  const stepOptions = chartView?.step_options ?? [];
  const style = { '--left-width': `${p.leftOpen ? p.leftWidth : 0}px`, '--right-width': `${p.rightOpen ? p.rightWidth : 0}px`, '--bottom-height': `${p.bottomOpen ? p.bottomHeight : 0}px` } as CSSProperties;
  return <div className="terminal" style={style}>
    <header className="topbar"><div className="brand">LIMIT<span>ORDERBOOK</span><small>RESEARCH WORKSPACE</small></div>
      <div className="selected-identity"><strong>{selected ? `${selected.base.ticker} / ${selected.quote.ticker}` : 'Select instrument'}</strong><span>{selected ? `${selected.venue} · ${selected.market_type} · ${selected.native_symbol}` : 'Exact public-market identities'}</span></div>
      <input ref={search} className="top-search" aria-label="Search instruments" placeholder="Search instruments /" type="search" value={query} onChange={event => { setQuery(event.target.value); update({ leftOpen: true }); }} />
      <label className="cohort-control">Cohort<select aria-label="Native cohort" value={quote && market ? JSON.stringify([quote, market]) : quote || market ? 'custom' : ''} onChange={event => { const [nextQuote, nextMarket] = event.target.value ? JSON.parse(event.target.value) as [string, string] : ['', '']; setQuote(nextQuote); setMarket(nextMarket); update({ leftOpen: true }); }}><option value="">All native cohorts</option>{(quote || market) && !(quote && market) && <option value="custom" disabled>Custom filters</option>}{choices.cohorts.map(([value, item]) => <option key={value} value={value}>{item.quote.ticker} · {item.market_type} · {item.quote.asset_id}</option>)}</select></label>
      <label className="source-control">Source<select aria-label="Data source" value={frame?.source.id ?? p.source?.id ?? ''} disabled={!catalog} onChange={event => { const source = catalog?.sources.find(item => item.id === event.target.value); if (source?.status === 'ready') changeSource(source); }}>{!frame && <option value="">Choose source…</option>}{catalog?.sources.map(source => <option key={source.id} value={source.id} disabled={source.status !== 'ready'} title={source.reasons.join('; ')}>{source.label} · {source.mode} · {source.status}{source.synthetic ? ' · SYNTHETIC' : ''}{source.reasons.length ? ` · ${source.reasons.join('; ')}` : ''}</option>)}</select></label>
      <label>Preset<select aria-label="Workspace preset" value={p.preset} onChange={event => update(presetLayout(event.target.value as WorkspacePreferences['preset']))}><option value="balanced">Balanced</option><option value="analysis">Analysis</option><option value="ladder">DOM focus</option></select></label>
      <span className={`status ${connected ? 'positive' : 'warning'}`}>{terminal.connection}</span><button onClick={terminal.reconnect} aria-label="Reconnect terminal">Reconnect</button>
    </header>
    <div className="source-banner"><strong>{frame?.source.synthetic ? 'SYNTHETIC DEMO' : frame?.source.mode.toUpperCase() ?? 'CONNECTING'}</strong><span>{frame?.source.label ?? 'Opening an independent session for this document'}</span><span>{frame?.source.recording ? 'Recorded capture · playback' : 'Not recording'}</span><span className={frame?.health.ready && usable ? 'positive' : 'warning'}>{frame?.health.ready && usable ? 'Required data ready' : 'Not ready / diagnostic'}</span><span>{label(frame?.status ?? 'loading')}</span>
      <div className="pane-toggles">{(['leftOpen', 'rightOpen', 'bottomOpen'] as const).map((key, index) => <button key={key} aria-pressed={p[key]} onClick={() => update({ [key]: !p[key] })}>{['Watchlist', 'DOM / tape', 'Costs / analytics'][index]}</button>)}</div>
    </div>
    {frame?.source.synthetic && <div className="warning-banner" role="status" data-testid="synthetic-guidance">
      <strong>Synthetic demo <span className="synthetic-state">{label(frame.status)}</span> — not live market data.</strong>{' '}
      Sparse aggregate L2 levels are intentional fixture data, not a full exchange order book. Charts only show observations delivered at this replay cursor.{' '}
      <button disabled={terminal.busy || !connected || frame.status !== 'paused'} onClick={() => void terminal.replay('play')}>Play synthetic demo</button>{' '}
      {liveSource ? <button disabled={terminal.busy} onClick={() => changeSource(liveSource)}>Switch to public live data</button>
        : <span>Public live data is unavailable{catalog?.sources.filter(source => source.mode === 'live').map(source => ` · ${source.status}${source.reasons.length ? `: ${source.reasons.join('; ')}` : ''}`).join('') || ' · operator must launch serve --network'}. No live data is substituted.</span>}
    </div>}
    {(terminal.error || frame?.error) && <div className="error-banner" role="alert">{terminal.error ?? frame?.error}</div>}
    {(terminal.preferenceNotice || storageError) && <div className="warning-banner" role="status">{terminal.preferenceNotice ?? storageError}</div>}
    {terminal.catalogError && <div className="warning-banner" role="status">{terminal.catalogError} · retrying automatically without replacing the current session.</div>}
    {frame?.status === 'seeking' && <div className="warning-banner" role="status">Seeking and rebuilding analytics. Previous evidence is unavailable until reconstruction completes.</div>}
    <ReplayControls frame={frame} busy={terminal.busy || !connected} replay={terminal.replay} />
    <main className={`workspace ${p.leftOpen ? '' : 'left-collapsed'} ${p.rightOpen ? '' : 'right-collapsed'} ${p.bottomOpen ? '' : 'bottom-collapsed'}`} data-testid="workspace">
      {p.leftOpen && <aside className="sidebar"><Panel title="Instruments" extra={<span className="muted">{filtered.length}</span>}>
        <div className="instrument-filters">
          <select aria-label="Filter venue" value={venue} onChange={event => setVenue(event.target.value)}><option value="">All venues</option>{choices.venues.map(value => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter quote" value={quote} onChange={event => setQuote(event.target.value)}><option value="">All exact quote assets</option>{choices.quotes.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.ticker} · {asset.asset_id}</option>)}</select>
          <select aria-label="Filter market" value={market} onChange={event => setMarket(event.target.value)}><option value="">All markets</option>{choices.markets.map(value => <option key={value}>{value}</option>)}</select>
        </div>
        <div className="instrument-list scroll" key={JSON.stringify([sourceId, query, venue, quote, market, page])}>{!filtered.length ? <Empty>{catalog ? 'No exact instruments match these filters.' : 'Loading instrument catalog…'}</Empty> : pageInstruments.map(item => <button className={`instrument ${item.instrument_id === selected?.instrument_id ? 'selected' : ''}`} key={item.instrument_id} title={item.instrument_id} data-instrument-id={item.instrument_id} aria-pressed={item.instrument_id === selected?.instrument_id} disabled={terminal.busy || !usable} onClick={() => { update({ priceStep: '', priceOffset: 0, range: null, follow: true }); void terminal.select(item.instrument_id); }}><span><strong>{item.native_symbol}</strong><small>{item.venue}</small></span><span><small>{item.market_type} · {item.quote.ticker}</small><small>{item.status} / {item.data_access}</small></span></button>)}</div>
        {pageCount > 1 && <nav className="panel-toolbar" aria-label="Instrument pages"><button aria-label="Previous instrument page" disabled={page === 0} onClick={() => setInstrumentPage(page - 1)}>Prev</button><span>{page + 1} / {pageCount} · {instrumentPageSize}/page</span><button aria-label="Next instrument page" disabled={page + 1 >= pageCount} onClick={() => setInstrumentPage(page + 1)}>Next</button></nav>}
      </Panel>
      <Panel title="Source & health" className="health-panel"><div className="health-body"><Reasons reasons={[...(frame?.health.reasons ?? []), ...(frame?.source.reasons ?? []), ...(frame?.book?.quality.reason_codes ?? [])]} />
        {frame?.book && <p className="caption" data-testid="native-depth">Native retained depth: {frame.book.quality.coverage.retained_bid_levels} bid / {frame.book.quality.coverage.retained_ask_levels} ask levels · frame shows {frame.book.bids.length} / {frame.book.asks.length}. Grouped DOM and chart rows are presentation buckets, not a count of individual orders.</p>}
        <dl className="metadata"><dt>Native feed mode</dt><dd>{frame?.book?.quality.source_mode ?? 'Awaiting evidence'}</dd><dt>Recorded source mode</dt><dd>{frame?.book?.quality.recorded_source_mode ?? '—'}</dd><dt>Capture complete</dt><dd>{frame?.book ? String(frame.book.quality.capture_complete) : 'Unknown'}</dd><dt>Coverage</dt><dd>{frame?.book?.quality.coverage.full_depth ? 'Full depth' : 'Bounded / unknown outside scope'}</dd><dt>Source state</dt><dd>{frame?.source.status ?? 'Loading'}</dd><dt>Settlement</dt><dd>{selected?.settlement?.ticker ?? 'None declared'}</dd><dt>Tick / lot</dt><dd><Value value={selected?.tick_size} /> / <Value value={selected?.lot_size} /></dd><dt>Execution access</dt><dd>{selected?.execution_access ?? 'Unknown'}</dd></dl>
        {frame?.book && (frame.book.presentation.bids_omitted > 0 || frame.book.presentation.asks_omitted > 0) && <p className="caption">Frame carries the first {frame.book.presentation.depth_limit} levels per side. Native reconstruction, grouped ladder and cost walks retain full source depth.</p>}
        <Details title="Exact instrument metadata" value={selected ?? null} /><Details title="Source & capture descriptor" value={frame?.source ?? null} />
        <Details title="Available source readiness & reasons" value={catalog?.sources ?? []} /><Details title={`Live ingestion evidence · ${frame?.telemetry.ingestion.length ?? 0} streams`} value={frame?.telemetry.ingestion ?? []} />
      </div></Panel></aside>}
      {p.leftOpen && <div className="left-resizer"><ResizeHandle axis="horizontal" name="Watchlist width" value={p.leftWidth} min={180} max={360} onChange={leftWidth => update({ leftWidth })} /></div>}
      <section className="panel chart-panel" aria-label="Market visualization">
        <header className="panel-heading"><div className="tabs chart-tabs" role="tablist" aria-label="Chart view">{(['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'] as ChartMode[]).map(mode => <button key={mode} role="tab" aria-selected={p.mode === mode} onClick={() => update({ mode })}>{label(mode)}</button>)}</div><button data-testid="view-freeze" aria-pressed={p.paused} onClick={() => update({ paused: !p.paused })}>{p.paused ? 'Resume view' : 'Pause view'}</button><button data-testid="view-recenter" onClick={centerView}>Recenter</button></header>
        <div className="chart-controls">
          <label title="Auto-fit observed depth when selected, on initial view and Recenter; presentation buckets only, native depth unchanged.">Price aggregation<select aria-label="Price aggregation" value={p.priceStep} onChange={event => { const priceStep = event.target.value; update({ priceStep, ...(priceStep ? {} : { priceOffset: 0 }) }); if (!priceStep) setRecenter(value => value + 1); }}><option value="">Auto-fit depth</option>{p.priceStep && !stepOptions.includes(p.priceStep) && <option value={p.priceStep}>{p.priceStep} · saved / validate</option>}{stepOptions.map(step => <option key={step}>{step}</option>)}</select></label>
          <label>Time bucket<select aria-label="Time bucket" value={p.bucket} onChange={event => update({ bucket: event.target.value })}>{['1000000000', '5000000000', '10000000000'].map((value, index) => <option key={value} value={value}>{[1, 5, 10][index]}s</option>)}</select></label>
          <label>Range<select aria-label="Chart range" value={p.rangeSeconds} onChange={event => update({ rangeSeconds: Number(event.target.value), range: null, follow: true })}>{[30, 60, 180, 300, 600].map(value => <option key={value} value={value}>{value}s</option>)}</select></label>
          <label><input type="checkbox" checked={p.follow} onChange={event => update({ follow: event.target.checked, range: event.target.checked ? null : chartView ? { start_ns: chartView.start_ns, end_ns: chartView.end_ns } : null })} />Follow time</label>
          <label>Fixed contrast<input aria-label="Fixed contrast" type="range" min="0.1" max="10" step="0.1" value={p.contrast} onChange={event => update({ contrast: Number(event.target.value) })} /></label>
          <label><input type="checkbox" checked={p.showTrades} onChange={event => update({ showTrades: event.target.checked })} />Trades</label><label><input type="checkbox" checked={p.showQuotes} onChange={event => update({ showQuotes: event.target.checked })} />Quotes</label>
        </div>
        <div className="chart-status" role="status">{p.paused ? 'VIEW FROZEN · replay / recording unaffected' : p.follow ? 'Following observed time' : 'Pinned time window'} · {chartView?.complete_window ? 'Complete retained window' : 'Partial / unavailable history'}{chartView?.truncated ? ' · retained history truncated' : ''}{!usable ? ' · connection evidence unavailable' : ''}{chartView ? ` · ${chartView.rows} price rows × ${chartView.price_step}${p.priceStep ? '' : ' · auto-fit on Recenter'}` : ''}</div>
        <MarketCharts view={chartView} receivedAtMs={views.chart.receivedAtMs ?? null} onPaint={chartTelemetry.onPaint} loading={views.chart.loading} error={!usable ? 'Current stream evidence is unavailable.' : views.chart.error ? `Chart unavailable / retained snapshot stale: ${views.chart.error}` : null} mode={p.mode} contrast={p.contrast} showTrades={p.showTrades} showQuotes={p.showQuotes} crosshair={crosshair} onCrosshair={setCrosshair} onRangeChange={changeRange} onPricePan={panPrice} />
        <div className="linked-readout">Linked cursor · source monotonic ns {crosshair?.time_ns ?? '—'} · exact price {crosshair?.price ?? '—'} <span title="/ search; Space pause view; R recenter; D freeze DOM; 1–6 chart modes. Editable fields ignore shortcuts.">Keyboard shortcuts: / Space R D 1–6</span></div>
        <div className="chart-timing" data-testid="chart-telemetry" title="Chart-specific CPU draw and two animation-frame paint opportunities; publication→ACK is a conservative round-trip upper bound, not GPU completion or one-way latency.">Chart draw {milliseconds(chartTelemetry.measurement?.draw_ms)} · Chart receive→paint {milliseconds(chartTelemetry.measurement?.receive_ms)} · Chart publish→ACK ≤ {milliseconds(chartTelemetry.measurement?.upper_bound_ms)}{chartTelemetry.measurement?.error ? ` · ACK unavailable: ${chartTelemetry.measurement.error}` : ''}</div>
      </section>
      {p.rightOpen && <><div className="right-resizer"><ResizeHandle axis="horizontal" reverse name="DOM width" value={p.rightWidth} min={250} max={500} onChange={rightWidth => update({ rightWidth })} /></div><aside className="book-tape"><Ladder data={usable ? views.dom.data : null} loading={views.dom.loading} error={!usable ? 'Stream evidence unavailable' : views.dom.error} frozen={p.domFrozen} viewPaused={p.paused} rows={p.domRows} onRows={domRows => update({ domRows })} onFreeze={() => update({ domFrozen: !p.domFrozen })} crosshair={crosshair?.price ?? null} onPrice={linkPrice} /><Tape analytics={usable ? frame?.analytics ?? null : null} instrument={selected} synthetic={frame?.source.synthetic ?? false} /></aside></>}
      {p.bottomOpen && <><div className="bottom-resizer"><ResizeHandle axis="vertical" reverse name="Research pane height" value={p.bottomHeight} min={170} max={460} onChange={bottomHeight => update({ bottomHeight })} /></div><section className="research-pane">
        <div className="tabs research-tabs" role="tablist" aria-label="Research pane">{workflowTabs.map(([id, name]) => <button key={id} id={`workflow-tab-${id}`} role="tab" aria-selected={bottomTab === id} aria-controls={`workflow-panel-${id}`} onClick={() => setBottomTab(id)}>{name}</button>)}<span className="muted">Shared-core evidence · no execution</span></div>
        <div className="research-scroll">
          <div id="workflow-panel-costs" role="tabpanel" aria-labelledby="workflow-tab-costs" hidden={bottomTab !== 'costs'}><CostsPanel frame={frame} instruments={instruments} connected={connected} calculate={terminal.costs} /><ProposalRiskPanel frame={frame} connected={connected} calculate={terminal.proposalRisk} /></div>
          <div id="workflow-panel-analytics" role="tabpanel" aria-labelledby="workflow-tab-analytics" hidden={bottomTab !== 'analytics'}><AnalyticsPanel analytics={usable ? frame?.analytics ?? null : null} /></div>
          <div id="workflow-panel-alerts" role="tabpanel" aria-labelledby="workflow-tab-alerts" hidden={bottomTab !== 'alerts'}><AlertsPanel {...workflow} active={bottomTab === 'alerts'} /></div>
          <div id="workflow-panel-context" role="tabpanel" aria-labelledby="workflow-tab-context" hidden={bottomTab !== 'context'}><ContextPanel {...workflow} active={bottomTab === 'context'} selectInstrument={selectContextInstrument} /></div>
          <div id="workflow-panel-paper" role="tabpanel" aria-labelledby="workflow-tab-paper" hidden={bottomTab !== 'paper'}><PaperPanel {...workflow} active={bottomTab === 'paper'} resumeRun={terminal.resumePaper} /></div>
          <div id="workflow-panel-journal" role="tabpanel" aria-labelledby="workflow-tab-journal" hidden={bottomTab !== 'journal'}><JournalPanel {...workflow} active={bottomTab === 'journal'} /></div>
          <div id="workflow-panel-research" role="tabpanel" aria-labelledby="workflow-tab-research" hidden={bottomTab !== 'research'}><ResearchPanel {...workflow} active={bottomTab === 'research'} /></div>
        </div>
      </section></>}
    </main>
    <footer className="telemetry"><span>Session {frame?.session_id.slice(0, 8) ?? '—'} · generation {frame?.generation ?? '—'}</span><span>Queue {frame?.telemetry.queue_depth ?? '—'} / high {frame?.telemetry.queue_highwater ?? '—'}</span><span>Coalesced {frame?.telemetry.coalesced ?? '—'} · overflow {frame?.telemetry.overflows ?? '—'}</span><span title="ACKs evicted from the bounded server buffer">ACK expired {frame?.telemetry.ack_expired ?? '—'}</span><span>Observer {milliseconds(frame?.telemetry.observer_duration_ms)}</span><span title="Two paint opportunities; not GPU completion">Receive→paint {milliseconds(frame?.telemetry.browser_receive_to_paint_ms)}</span><span title="Conservative round-trip upper bound">Publish→ACK ≤ {milliseconds(frame?.telemetry.publish_to_ack_upper_bound_ms)}</span></footer>
  </div>;
}
