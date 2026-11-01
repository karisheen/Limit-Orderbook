import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Analytics, Book, CostInput, Costs, Frame, Instrument, Metric, Pair, ReplayAction } from './api';
import { errorMessage } from './api';
import { compact, exact, label, milliseconds, timestamp } from './format';
import { useTerminal } from './useTerminal';

function Value({ value, className = '' }: { value: string | null | undefined; className?: string }) {
  return <span className={`number ${className}`} title={exact(value)}>{compact(value)}</span>;
}
function Reasons({ reasons }: { reasons: string[] }) {
  return reasons.length > 0 ? <ul className="reasons">{[...new Set(reasons)].map(reason => <li key={reason}>{label(reason)}</li>)}</ul> : null;
}
function Details({ title = 'Evidence', value }: { title?: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
function Panel({ title, extra, children, className = '' }: { title: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><header className="panel-heading"><h2>{title}</h2>{extra}</header>{children}</section>;
}
function Empty({ children }: { children: ReactNode }) { return <p className="empty">{children}</p>; }

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
    <div className="scroll analytics-scroll" role="tabpanel">
      {!analytics ? <Empty>Waiting for analytics evidence. Missing observations are not zero.</Empty> : tab === 'metrics' ? <table className="metrics-table"><thead><tr><th>Metric</th><th>Value</th><th>Unit</th><th>Quality / provenance</th></tr></thead><tbody>{metrics.map(metric => <MetricRow key={metric.name} metric={metric} />)}</tbody></table> : tab === 'profile' ? <>
        <p className="caption">Executed volume, not resting liquidity. Unknown aggressors remain separate. Availability and window evidence are in the profile metric.</p>
        {analytics.profile.length ? <table><thead><tr><th>Price</th><th>Buy base</th><th>Sell base</th><th>Unknown base</th><th>Quote notional</th></tr></thead><tbody>{analytics.profile.slice(0, 200).map(level => <tr key={level.price}><td><Value value={level.price} /></td><td><Value value={level.buy_base} /></td><td><Value value={level.sell_base} /></td><td><Value value={level.unknown_base} /></td><td><Value value={level.quote_notional} /></td></tr>)}</tbody></table> : <Empty>No eligible executed-volume profile. Inspect window/quality reasons in metrics.</Empty>}
      </> : <><p className="caption">Observed persistence/depletion/replenishment. Absorption and pulling are heuristics, not identity, intent, queue or hidden-size claims.</p>{analytics.display_evidence.length ? analytics.display_evidence.slice(0, 100).map((item, index) => <Details key={`${item.kind}:${item.price}:${item.end_mono_ns}:${index}`} title={`${label(item.kind)} · ${item.side} · ${item.price} × ${item.quantity}`} value={item} />) : <Empty>No eligible display evidence in this window.</Empty>}</>}
    </div>
  </Panel>;
}

function Ladder({ book, instrument, ready }: { book: Book | null; instrument?: Instrument; ready: boolean }) {
  const [depth, setDepth] = useState('15');
  // Display row count is a bounded UI integer, never an economic value.
  const rows = Math.min(Number(depth), Math.max(book?.bids.length ?? 0, book?.asks.length ?? 0));
  return <Panel title="Order book" className="ladder-panel" extra={<label className="inline">Rows <select aria-label="Ladder rows" value={depth} onChange={event => setDepth(event.target.value)}>{['10', '15', '25', '50'].map(value => <option key={value}>{value}</option>)}</select></label>}>
    <div className="panel-toolbar"><span>{instrument?.quote.ticker ?? 'Native quote'} / {instrument?.base.ticker ?? 'base'}</span><span className={ready ? 'positive' : 'warning'}>{ready ? 'Current evidence' : 'Diagnostic / unavailable'}</span></div>
    {!book ? <Empty>No synchronized book yet. Heartbeats do not create a book.</Empty> : <>
      <div className="scroll ladder-scroll"><table className="ladder"><thead><tr><th>Bid size<br /><small>{instrument?.quantity_unit ?? 'native'}</small></th><th>Bid price</th><th>Ask price</th><th>Ask size<br /><small>{instrument?.quantity_unit ?? 'native'}</small></th></tr></thead><tbody>{Array.from({ length: rows }, (_, index) => <tr key={index}><td><Value value={book.bids[index]?.quantity} /></td><td className="bid"><Value value={book.bids[index]?.price} /></td><td className="ask"><Value value={book.asks[index]?.price} /></td><td><Value value={book.asks[index]?.quantity} /></td></tr>)}</tbody></table></div>
      <div className="caption">{book.quality.coverage.full_depth ? 'Full-depth evidence' : 'Bounded depth · outside scope unknown'} · retained {book.quality.coverage.retained_bid_levels} bid / {book.quality.coverage.retained_ask_levels} ask · subscribed {exact(book.quality.coverage.subscribed_depth)}</div>
      <Details title={`Quality: ${label(book.quality.lifecycle)} / ${book.quality.integrity} · as of ${timestamp(book.asof.receive_wall_ns)} UTC`} value={{ quality: book.quality, asof: book.asof, revision: book.revision }} />
    </>}
  </Panel>;
}

function Tape({ analytics, instrument }: { analytics: Analytics | null; instrument?: Instrument }) {
  const prints = analytics?.tape.slice(-100).reverse() ?? [];
  return <Panel title="Time & sales" className="tape-panel" extra={<span className="muted">Last {prints.length} · receive UTC</span>}>
    <p className="caption">Public prints · subscription history labeled, excluded from default live flow.</p>
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
    <span className="cursor number">{frame?.replay ? `${frame.replay.cursor.delivery_index} / ${frame.replay.total_deliveries} · ${frame.replay.speed}×` : 'Live mode · replay unavailable'}</span>
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
  const terminal = useTerminal();
  const { catalog, frame } = terminal;
  const [query, setQuery] = useState('');
  const [venue, setVenue] = useState('');
  const [quote, setQuote] = useState('');
  const [market, setMarket] = useState('');
  const instruments = catalog?.instruments ?? [];
  const selected = instruments.find(item => item.instrument_id === frame?.selection.instrument_id);
  const filtered = instruments.filter(item => (!venue || item.venue === venue) && (!quote || item.quote.asset_id === quote) && (!market || item.market_type === market) && [item.instrument_id, item.native_symbol, item.venue, item.base.ticker, item.quote.ticker, item.market_type].join(' ').toLowerCase().includes(query.toLowerCase()));
  const sourceValue = frame?.source.id ?? '';
  const connected = terminal.connection === 'Connected';
  const usable = connected && frame?.status !== 'seeking';
  return <div className="terminal">
    <header className="topbar"><div className="brand">LIMIT<span>ORDERBOOK</span><small>M3 TERMINAL</small></div>
      <div className="selected-identity"><strong>{selected ? `${selected.base.ticker} / ${selected.quote.ticker}` : 'Select instrument'}</strong><span>{selected ? `${selected.venue} · ${selected.market_type} · ${selected.native_symbol}` : 'Exact public-market identities'}</span></div>
      <label className="source-control">Source<select aria-label="Data source" value={sourceValue} disabled={!catalog} onChange={event => { const source = catalog?.sources.find(item => item.id === event.target.value); if (source?.status === 'ready') terminal.source(source.mode, source.id); }}>{!frame && <option value="">Connecting…</option>}{catalog?.sources.map(source => <option key={source.id} value={source.id} disabled={source.status !== 'ready'} title={source.reasons.join('; ')}>{source.label} · {source.mode} · {source.status}{source.synthetic ? ' · SYNTHETIC' : ''}{source.reasons.length ? ` · ${source.reasons.join('; ')}` : ''}</option>)}</select></label>
      <span className={`status ${connected ? 'positive' : 'warning'}`}>{terminal.connection}</span><button onClick={terminal.reconnect} aria-label="Reconnect terminal">Reconnect</button>
    </header>
    <div className="source-banner"><strong>{frame?.source.synthetic ? 'SYNTHETIC DEMO' : frame?.source.mode.toUpperCase() ?? 'CONNECTING'}</strong><span>{frame?.source.label ?? 'Opening an independent session for this document'}</span><span>{frame?.source.recording ? 'Recorded capture · playback' : 'Not recording'}</span><span className={frame?.health.ready && usable ? 'positive' : 'warning'}>{frame?.health.ready && usable ? 'Required data ready' : 'Not ready / diagnostic'}</span><span>{label(frame?.status ?? 'loading')}</span></div>
    {(terminal.error || frame?.error) && <div className="error-banner" role="alert">{terminal.error ?? frame?.error}</div>}
    {terminal.catalogError && <div className="warning-banner" role="status">{terminal.catalogError} · retrying automatically without replacing the current session.</div>}
    {frame?.status === 'seeking' && <div className="warning-banner" role="status">Seeking and rebuilding analytics. Previous evidence is unavailable until reconstruction completes.</div>}
    <ReplayControls frame={frame} busy={terminal.busy || !connected} replay={terminal.replay} />
    <main className="workspace"><aside className="sidebar">
      <Panel title="Instruments" extra={<span className="muted">{filtered.length}</span>}>
        <div className="instrument-filters"><input aria-label="Search instruments" placeholder="Search exact symbol, asset, venue…" type="search" value={query} onChange={event => setQuery(event.target.value)} />
          <select aria-label="Filter venue" value={venue} onChange={event => setVenue(event.target.value)}><option value="">All venues</option>{[...new Set(instruments.map(item => item.venue))].sort().map(value => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter quote" value={quote} onChange={event => setQuote(event.target.value)}><option value="">All exact quote assets</option>{[...new Map(instruments.map(item => [item.quote.asset_id, item.quote])).values()].map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.ticker} · {asset.asset_id}</option>)}</select>
          <select aria-label="Filter market" value={market} onChange={event => setMarket(event.target.value)}><option value="">All markets</option>{[...new Set(instruments.map(item => item.market_type))].map(value => <option key={value}>{value}</option>)}</select>
        </div>
        <div className="instrument-list scroll">{!filtered.length ? <Empty>{catalog ? 'No exact instruments match these filters.' : 'Loading instrument catalog…'}</Empty> : filtered.map(item => <button className={`instrument ${item.instrument_id === selected?.instrument_id ? 'selected' : ''}`} key={item.instrument_id} title={item.instrument_id} data-instrument-id={item.instrument_id} aria-pressed={item.instrument_id === selected?.instrument_id} disabled={terminal.busy || !connected || frame?.status === 'seeking'} onClick={() => void terminal.select(item.instrument_id)}><span><strong>{item.native_symbol}</strong><small>{item.venue}</small></span><span><small>{item.market_type} · {item.quote.ticker}</small><small>{item.status} / {item.data_access}</small></span></button>)}</div>
      </Panel>
      <Panel title="Source & health" className="health-panel"><div className="health-body"><Reasons reasons={[...(frame?.health.reasons ?? []), ...(frame?.source.reasons ?? []), ...(frame?.book?.quality.reason_codes ?? [])]} />
        <dl className="metadata"><dt>Native feed mode</dt><dd>{frame?.book?.quality.source_mode ?? 'Awaiting evidence'}</dd><dt>Recorded source mode</dt><dd>{frame?.book?.quality.recorded_source_mode ?? '—'}</dd><dt>Capture complete</dt><dd>{frame?.book ? String(frame.book.quality.capture_complete) : 'Unknown'}</dd><dt>Source state</dt><dd>{frame?.source.status ?? 'Loading'}</dd><dt>Settlement</dt><dd>{selected?.settlement?.ticker ?? 'None declared'}</dd><dt>Tick / lot</dt><dd><Value value={selected?.tick_size} /> / <Value value={selected?.lot_size} /></dd><dt>Execution access</dt><dd>{selected?.execution_access ?? 'Unknown'}</dd></dl>
        <Details title="Exact instrument metadata" value={selected ?? null} /><Details title="Source & capture descriptor" value={frame?.source ?? null} />
        <Details title="Available source readiness & reasons" value={catalog?.sources ?? []} />
        <Details title={`Live ingestion evidence · ${frame?.telemetry.ingestion.length ?? 0} streams`} value={frame?.telemetry.ingestion ?? []} />
      </div></Panel>
    </aside><div className="main-column"><div className="market-grid"><AnalyticsPanel analytics={usable ? frame?.analytics ?? null : null} /><div className="book-tape"><Ladder book={usable ? frame?.book ?? null : null} instrument={selected} ready={Boolean(frame?.health.ready && usable)} /><Tape analytics={usable ? frame?.analytics ?? null : null} instrument={selected} /></div></div>
      <CostsPanel frame={frame} instruments={instruments} connected={connected} calculate={terminal.costs} />
    </div></main>
    <footer className="telemetry"><span>Session {frame?.session_id.slice(0, 8) ?? '—'} · generation {frame?.generation ?? '—'}</span><span>Queue {frame?.telemetry.queue_depth ?? '—'} / high {frame?.telemetry.queue_highwater ?? '—'}</span><span>Coalesced {frame?.telemetry.coalesced ?? '—'} · overflow {frame?.telemetry.overflows ?? '—'}</span><span title="ACKs evicted from the bounded server buffer; separate from coalesced presentations">ACK expired {frame?.telemetry.ack_expired ?? '—'}</span><span>Observer {milliseconds(frame?.telemetry.observer_duration_ms)}</span><span title="Browser event receipt, including JSON decoding, to two animation-frame paint opportunities; not GPU completion">Receive→paint {milliseconds(frame?.telemetry.browser_receive_to_paint_ms)}</span><span title="Server publication to render acknowledgement round trip; conservative upper bound, not one-way latency">Publish→ACK ≤ {milliseconds(frame?.telemetry.publish_to_ack_upper_bound_ms)}</span></footer>
  </div>;
}
