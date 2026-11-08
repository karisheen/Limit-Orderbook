import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { Frame } from './api';
import type {
  BalanceInput, ConsumerEnvelope, JournalEntry, JournalInput, JournalPutResponse, JournalResponse,
  PaperArchiveResponse, PaperConfigurationInput, PaperOrderInput, PaperResponse, PaperRunsResponse,
  PaperSnapshot, WorkflowPanelProps, WorkflowQuery,
} from './workflow-api';
import { Details, Empty, Panel, Reasons, Value } from './WorkflowUI';
import { venueLabel } from './format';
import { metadataVerified } from './instrument-metadata';
import './paper-panels.css';

const detailLimits = { orders_limit: 100, fills_limit: 100 };
const scopeOf = (props: WorkflowPanelProps) => [props.frame?.session_id, props.frame?.generation, props.frame?.source.id, props.frame?.selection.instrument_id].join('|');

// One completion-paced read chain per visible panel. Mutations take exclusive ownership,
// cancel outstanding reads, and are never retried, including on lifecycle interruption.
function useWorkflowData<T>(props: WorkflowPanelProps, load: (query: WorkflowQuery, signal: AbortSignal) => Promise<T>) {
  const scope = scopeOf(props);
  const [visible, setVisible] = useState(() => !document.hidden);
  const [stored, setStored] = useState<{ scope: string; loader: typeof load; value: T } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(false);
  const [revision, setRevision] = useState(0);
  const lock = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const runningRead = useRef<Promise<void> | null>(null);
  const owned = props.active && visible && props.connected && !!props.frame &&
    ['paused', 'playing', 'ended', 'live'].includes(props.frame.status) && !!props.frame.selection.instrument_id;
  const enabled = owned && !props.busy;
  const latest = useRef({ enabled, scope });
  latest.current = { enabled, scope };
  useEffect(() => {
    const change = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', change);
    return () => document.removeEventListener('visibilitychange', change);
  }, []);
  useEffect(() => {
    setStored(null);
    setError('');
    setNotice('');
  }, [scope]);
  useEffect(() => {
    if (!enabled || pending) return;
    let stopped = false;
    let timer: number | undefined;
    let readAbort: AbortController | null = null;
    const read = async () => {
      if (stopped || lock.current) return;
      const abort = new AbortController();
      readAbort = abort;
      controller.current = abort;
      const work = (async () => {
        try {
          const value = await load(props.request, abort.signal);
          if (!stopped && !abort.signal.aborted && latest.current.scope === scope) {
            setStored({ scope, loader: load, value });
            setError('');
          }
        } catch (failure) {
          if (!stopped && !abort.signal.aborted) {
            setStored(null);
            setError(failure instanceof Error ? failure.message : String(failure));
          }
        }
      })();
      runningRead.current = work;
      await work;
      if (runningRead.current === work) runningRead.current = null;
      if (!stopped && !lock.current) timer = window.setTimeout(() => { void read(); }, 2000);
    };
    void read();
    return () => { stopped = true; clearTimeout(timer); readAbort?.abort(); };
  }, [enabled, pending, scope, revision, load, props.request]);
  const act = async (operation: (signal: AbortSignal) => Promise<unknown>, mutation = true) => {
    if (lock.current || !latest.current.enabled) return false;
    lock.current = true;
    setPending(true);
    controller.current?.abort();
    await runningRead.current;
    const abort = new AbortController();
    controller.current = abort;
    try {
      if (!latest.current.enabled || latest.current.scope !== scope) throw new Error('Source or lifecycle changed before request.');
      await operation(abort.signal);
      if (abort.signal.aborted || latest.current.scope !== scope) throw new Error('Request interrupted by source or lifecycle change.');
      setNotice(mutation ? 'Request completed. Refreshing native persisted state.' : 'Read completed.');
      setStored(null);
      return true;
    } catch (failure) {
      setNotice(`${failure instanceof Error ? failure.message : String(failure)}${mutation ? ' The mutation may already have committed. Refresh and inspect native state before retrying; no automatic retry was made.' : ''}`);
      return false;
    } finally {
      lock.current = false;
      setPending(false);
      setRevision(value => value + 1);
    }
  };
  // Also cancel mutations when the panel loses ownership, not just polling reads.
  useEffect(() => {
    if (!owned) {
      controller.current?.abort();
      setStored(null);
    }
    return () => { controller.current?.abort(); };
  }, [owned, scope]);
  return {
    data: enabled && stored?.scope === scope && stored.loader === load ? stored.value : null,
    enabled, pending, error, notice, act,
    refresh: () => { if (!lock.current) setRevision(value => value + 1); },
  };
}

function Exact({ value }: { value: string | null | undefined }) {
  return <span className="paper-exact">{value == null ? <Value value={value} /> : value}</span>;
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="paper-field"><span>{label}</span>{children}</label>;
}
function Provenance({ response }: { response: ConsumerEnvelope }) {
  return <p className="paper-provenance">Source <Exact value={response.source_identity} /> · session <Exact value={response.session_id} /> · generation <Exact value={response.generation} /> · delivery <Exact value={response.delivery_index} /></p>;
}
function Availability({ enabled, pending, error, notice }: { enabled: boolean; pending: boolean; error: string; notice: string }) {
  return <>
    {!enabled && <Empty>Unavailable while inactive, hidden, disconnected, loading, seeking or changing source. Previous results are not current state.</Empty>}
    {pending && <p className="caption" role="status">Request in progress; actions serialized.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="paper-notice" role="status">{notice}</p>}
  </>;
}
function Accounting({ snapshot, cancel, disabled = false }: { snapshot: PaperSnapshot; cancel?: (id: string) => void; disabled?: boolean }) {
  return <div className="paper-accounting">
    <p className="paper-provenance">Run <Exact value={snapshot.run?.run_id} /> · source <Exact value={snapshot.run?.source_id} /> · accounting delivery <Exact value={snapshot.delivery_index} /> · clock <Exact value={snapshot.at?.clock_id} /> · monotonic ns <Exact value={snapshot.at?.mono_ns} /> · wall ns <Exact value={snapshot.at?.wall_ns} /></p>
    <Reasons reasons={snapshot.reasons} />
    {!snapshot.run && <Empty>No configured paper run. Set explicit starting balances and sourced fees below.</Empty>}
    {snapshot.run && <Details title="Run configuration intent, starting balances, fee schedules, latency and origin (not current funds)" value={snapshot.run} />}
    <h3>Balances at the stated accounting prefix</h3>
    {snapshot.balances.length === 0 ? <Empty>No balances visible at this accounting prefix. Configuration intent is not available funds.</Empty> : <div className="paper-table-scroll"><table><thead><tr><th>Venue</th><th>Asset ID</th><th>Available — exact</th></tr></thead><tbody>{snapshot.balances.map(balance => <tr key={`${balance.venue}:${balance.asset_id}`}><td>{venueLabel(balance.venue)}</td><td>{balance.asset_id}</td><td><Exact value={balance.available} /></td></tr>)}</tbody></table></div>}
    <h3>Positions and native P&amp;L</h3>
    <p className="caption">Unrealized P&amp;L uses gross best-bid marks, not net liquidation proceeds. Initial inventory cost basis can be unknown. No browser valuation or cross-asset totals.</p>
    {snapshot.positions.length === 0 ? <Empty>No positions at this prefix.</Empty> : <div className="paper-table-scroll"><table><thead><tr><th>Venue / instrument / assets</th><th>Quantity</th><th>Cost basis (quote)</th><th>Realized P&amp;L (quote)</th><th>Gross bid-mark unrealized (quote)</th><th>Bid mark / reasons</th></tr></thead><tbody>{snapshot.positions.map(position => <tr key={`${position.venue}:${position.instrument_id}`}><td>{venueLabel(position.venue)}<br />{position.instrument_id}<br />{position.asset_id} / {position.quote_asset_id}</td><td><Exact value={position.quantity} /></td><td><Exact value={position.cost_basis_quote} /></td><td><Exact value={position.realized_pnl_quote} /></td><td><Exact value={position.unrealized_pnl_quote} /></td><td><Exact value={position.mark_price} /><Reasons reasons={position.reasons} /></td></tr>)}</tbody></table></div>}
    <h3>Orders — market / marketable-limit IOC</h3>
    {snapshot.orders.length === 0 ? <Empty>No orders visible at this prefix.</Empty> : <div className="paper-table-scroll"><table><thead><tr><th>Order / instrument</th><th>Request</th><th>Status / exact execution</th><th>Latency clocks / reasons / action</th></tr></thead><tbody>{snapshot.orders.map(order => <tr key={order.order_id}><td>{order.order_id}<br />{order.request.instrument_id}</td><td>{order.request.side} {order.request.order_type}<br />Quantity <Exact value={order.request.quantity} /><br />Limit <Exact value={order.request.limit_price} /></td><td>{order.status}<br />Filled <Exact value={order.filled_quantity} /><br />Remaining <Exact value={order.remaining_quantity} /></td><td>Submit ns <Exact value={order.submitted_at.mono_ns} /><br />Arrival ns <Exact value={order.arrival_at.mono_ns} /><Reasons reasons={order.reasons} />{cancel && order.status === 'pending' && <button type="button" disabled={disabled} onClick={() => cancel(order.order_id)}>Cancel pending order</button>}<Details title="Exact order provenance" value={order} /></td></tr>)}</tbody></table></div>}
    <p className="caption">Showing up to {snapshot.presentation.orders_limit} orders; {snapshot.presentation.orders_omitted} omitted.</p>
    <h3>Fills, exact fees and consumed native book levels</h3>
    {snapshot.fills.length === 0 ? <Empty>No fills visible at this prefix. No passive touch-fill inference.</Empty> : <div className="paper-table-scroll"><table><thead><tr><th>Fill / order / instrument</th><th>Quantity / notional</th><th>Fee / schedule</th><th>Time / native evidence</th></tr></thead><tbody>{snapshot.fills.map(fill => <tr key={fill.fill_id}><td>{fill.fill_id}<br />Order {fill.order_id}<br />{venueLabel(fill.venue)} · {fill.instrument_id} · {fill.side}</td><td><Exact value={fill.quantity} /><br /><Exact value={fill.notional_quote} /> {fill.quote_asset_id}</td><td><Exact value={fill.fee_amount} /> {fill.fee_asset_id}<br />Rate <Exact value={fill.fees.rate} /><br />{fill.fees.provenance}<br />As-of wall ns <Exact value={fill.fees.asof_wall_ns} /> · {fill.fees.assumed ? 'assumed' : 'not assumed'}</td><td>Delivery <Exact value={fill.delivery_index} /><br />Clock {fill.at.clock_id}<br />ns <Exact value={fill.at.mono_ns} /><Details title="Fill, fees, book reference and consumed levels" value={fill} /><span>{fill.consumed_levels_omitted} consumed levels omitted (limit {snapshot.presentation.fill_levels_limit}).</span></td></tr>)}</tbody></table></div>}
    <p className="caption">Showing up to {snapshot.presentation.fills_limit} fills; {snapshot.presentation.fills_omitted} omitted.</p>
    <h3>Native assumptions and limitations</h3><Reasons reasons={snapshot.assumptions} />
  </div>;
}

interface BalanceDraft { venue: string; asset_id: string; available: string; provenance: string }
interface FeeDraft { instrument_id: string; fee_bps: string; fee_provenance: string; fee_date: string; fee_asset_id: string }
const EMPTY_BALANCE: BalanceDraft = { venue: '', asset_id: '', available: '', provenance: '' };
const EMPTY_FEE: FeeDraft = { instrument_id: '', fee_bps: '', fee_provenance: '', fee_date: '', fee_asset_id: '' };

export function PaperPanel(props: WorkflowPanelProps & { resumeRun: (id: string, signal: AbortSignal) => Promise<Frame> }) {
  const load = useCallback(async (query: WorkflowQuery, signal: AbortSignal) => {
    const paper = await query<PaperResponse>('/paper/query', detailLimits, signal);
    signal.throwIfAborted();
    const runs = await query<PaperRunsResponse>('/paper/runs', { limit: 100 }, signal);
    return { paper, runs };
  }, []);
  const io = useWorkflowData(props, load);
  const [balances, setBalances] = useState<BalanceDraft[]>([EMPTY_BALANCE]);
  const [fees, setFees] = useState<FeeDraft[]>([EMPTY_FEE]);
  const [feed, setFeed] = useState('');
  const [decision, setDecision] = useState('');
  const [orderLatency, setOrderLatency] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [confirmedFork, setConfirmedFork] = useState(false);
  const [archive, setArchive] = useState<{ scope: string; response: PaperArchiveResponse } | null>(null);
  const [resumeRequested, setResumeRequested] = useState<{ id: string; generation: string; session: string; acceptedGeneration: string | null } | null>(null);
  const scope = scopeOf(props);
  const paper = io.data?.paper;
  const snapshot = paper?.snapshot;
  const instrument = props.instruments.find(item => item.instrument_id === props.frame?.selection.instrument_id);
  const disabled = !io.enabled || io.pending;
  const verified = metadataVerified(props.frame);
  const configurationDisabled = disabled || !verified;
  const currentRun = snapshot?.run;
  const runActive = !!currentRun && !snapshot?.reasons.some(reason => ['run_not_active_at_cursor', 'future_commands_fork_required', 'stale_run_reload_required', 'run_source_clock_mismatch'].includes(reason));
  const canOrder = !configurationDisabled && runActive && instrument?.market_type === 'spot' && instrument.quantity_unit === 'base';
  useEffect(() => { setArchive(null); setConfirmedFork(false); setQuantity(''); setLimitPrice(''); }, [scope]);
  const configure = (event: FormEvent) => {
    event.preventDefault();
    if (configurationDisabled || !confirmedFork) return;
    const input: PaperConfigurationInput = {
      balances: balances.map((balance): BalanceInput => ({ ...balance })), fees: fees.map(fee => ({ ...fee })),
      feed_latency_ns: feed, decision_latency_ns: decision, order_latency_ns: orderLatency,
    };
    void io.act(signal => props.request<PaperResponse>('/paper/configure', input, signal)).then(ok => { if (ok) setConfirmedFork(false); });
  };
  const submitOrder = (event: FormEvent) => {
    event.preventDefault();
    if (!canOrder || !instrument) return;
    const input: PaperOrderInput = { instrument_id: instrument.instrument_id, side, quantity, order_type: orderType, ...(orderType === 'limit' ? { limit_price: limitPrice } : {}) };
    void io.act(signal => props.request<PaperResponse>('/paper/orders', input, signal));
  };
  const showArchive = (runId: string) => {
    setArchive(null);
    void io.act(async signal => {
      const response = await props.request<PaperArchiveResponse>('/paper/archive', { run_id: runId, ...detailLimits }, signal);
      signal.throwIfAborted();
      setArchive({ scope, response });
    }, false);
  };
  const resume = (id: string) => {
    if (!props.frame || disabled) return;
    const attempt = { id, generation: props.frame.generation, session: props.frame.session_id, acceptedGeneration: null };
    setResumeRequested(attempt);
    // Only this completed response's generation can confirm this attempt.
    // An unrelated seek or retained inactive run is not evidence of resume.
    void io.act(async signal => {
      const accepted = await props.resumeRun(id, signal);
      if (accepted.session_id !== attempt.session || BigInt(accepted.generation) <= BigInt(attempt.generation)) {
        throw new Error('Native resume did not establish a new owned generation.');
      }
      setResumeRequested(previous => previous === attempt ? { ...attempt, acceptedGeneration: accepted.generation } : previous);
    });
  };
  return <Panel title="Paper execution — native spot simulation" className="paper-workflow" extra={<button type="button" disabled={disabled} onClick={io.refresh}>Refresh native state</button>}>
    <p className="caption">Simulation only. No credentials or real orders. Spot market and marketable-limit IOC; no passive fills or derivative economics. Native fees, funds, depth, latency and reservations determine outcomes.</p>
    <Availability {...io} />
    {props.frame?.selection.instrument_id && !verified && <p className="paper-notice" role="status">Streaming-only or unverified session metadata: paper configuration and orders require verified session-pinned instrument metadata. Native accounting, archived audits, market data and analytics remain available.</p>}
    {paper && <Provenance response={paper} />}
    {resumeRequested && resumeRequested.session === props.frame?.session_id && <p className="paper-notice">Resume requested: {resumeRequested.id}. {resumeRequested.acceptedGeneration !== null && paper?.generation === resumeRequested.acceptedGeneration && currentRun?.run_id === resumeRequested.id && !snapshot?.reasons.includes('run_not_active_at_cursor') ? 'Confirmed by a new-generation native run snapshot; accounting is rebuilt only through the current cursor.' : 'Not yet confirmed by a new-generation native run snapshot. Inspect terminal lifecycle/errors and refresh; completion alone is not success.'}</p>}
    <form className="paper-order-form" onSubmit={submitOrder}>
      <h3>Simulated entry — {instrument?.native_symbol ?? 'select an instrument'}</h3>
      <p className="caption">Exact instrument: {instrument?.instrument_id ?? 'unavailable'}. Quantity unit: {instrument?.quantity_unit ?? 'unavailable'}. A pending order requires subsequent native source time to reach arrival.</p>
      <fieldset disabled={!canOrder}><div className="paper-fields">
        <Field label="Side"><select value={side} onChange={event => setSide(event.target.value as 'buy' | 'sell')}><option value="buy">Buy</option><option value="sell">Sell</option></select></Field>
        <Field label="Base quantity — exact"><input required inputMode="decimal" value={quantity} onChange={event => setQuantity(event.target.value)} /></Field>
        <Field label="Order type"><select value={orderType} onChange={event => setOrderType(event.target.value as 'market' | 'limit')}><option value="market">Market IOC</option><option value="limit">Marketable limit IOC</option></select></Field>
        {orderType === 'limit' && <Field label="Limit price — exact"><input required inputMode="decimal" value={limitPrice} onChange={event => setLimitPrice(event.target.value)} /></Field>}
        <button type="submit" className="primary">Submit simulated order</button>
      </div></fieldset>
      {!runActive && <p className="caption">Entry requires an active native run at this cursor. Rewind before creation leaves configuration intent only; future commands require an explicit fork.</p>}
      {instrument && (instrument.market_type !== 'spot' || instrument.quantity_unit !== 'base') && <p className="paper-notice">Unsupported instrument economics: {instrument.market_type} / {instrument.quantity_unit}. No derivative simulation is represented.</p>}
    </form>
    <details className="paper-section" open={!currentRun}>
      <summary>Explicit new run / fork — starting funds, sourced fees and latency</summary>
      <p className="caption">Every configuration creates a new durable run, preserving prior history. With a selected run it records the parent as a fork; starting balances are exactly what you enter, not copied accounting or automatic funds. Fees and latencies are never inferred. Zero must be an explicit entry.</p>
      <form onSubmit={configure}>
        <fieldset disabled={configurationDisabled}>
          <h3>Starting balances by venue and exact asset ID</h3>
          {balances.map((balance, index) => <div className="paper-draft-row" key={index}>
            {(['venue', 'asset_id', 'available', 'provenance'] as const).map(key => <Field key={key} label={{ venue: 'Venue', asset_id: 'Asset ID', available: 'Starting available — exact', provenance: 'Balance provenance' }[key]}><input required value={balance[key]} inputMode={key === 'available' ? 'decimal' : undefined} onChange={event => setBalances(previous => previous.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: event.target.value } : row))} /></Field>)}
            <button type="button" onClick={() => setBalances(previous => previous.filter((_, rowIndex) => rowIndex !== index))}>Remove balance</button>
          </div>)}
          <button type="button" disabled={balances.length >= 64} onClick={() => setBalances(previous => [...previous, EMPTY_BALANCE])}>Add balance</button>
          <p className="caption">Selected metadata: venue {instrument?.venue ?? 'unavailable'}; base {instrument?.base.asset_id ?? 'unavailable'}; quote {instrument?.quote.asset_id ?? 'unavailable'}. These identifiers do not grant funds. Balance as-of is unspecified; provenance is supplied by you.</p>
          <h3>Explicit sourced and dated fee schedules</h3>
          {fees.map((fee, index) => <div className="paper-draft-row" key={index}>
            <Field label="Fee instrument"><select required value={fee.instrument_id} onChange={event => setFees(previous => previous.map((row, rowIndex) => rowIndex === index ? { ...row, instrument_id: event.target.value } : row))}><option value="">Select exact instrument</option>{props.instruments.map(item => <option key={item.instrument_id} value={item.instrument_id}>{venueLabel(item.venue)} · {item.native_symbol} · {item.instrument_id}</option>)}</select></Field>
            {(['fee_bps', 'fee_asset_id', 'fee_provenance', 'fee_date'] as const).map(key => <Field key={key} label={{ fee_bps: 'Fee basis points — exact', fee_asset_id: 'Fee asset ID', fee_provenance: 'Fee source / provenance', fee_date: 'Fee source date' }[key]}><input required type={key === 'fee_date' ? 'date' : 'text'} inputMode={key === 'fee_bps' ? 'decimal' : undefined} value={fee[key]} onChange={event => setFees(previous => previous.map((row, rowIndex) => rowIndex === index ? { ...row, [key]: event.target.value } : row))} /></Field>)}
            <button type="button" onClick={() => setFees(previous => previous.filter((_, rowIndex) => rowIndex !== index))}>Remove fee</button>
          </div>)}
          <button type="button" disabled={fees.length >= 64} onClick={() => setFees(previous => [...previous, EMPTY_FEE])}>Add fee schedule</button>
          <p className="caption">No fee schedule is not a zero fee: native execution reports missing/unsupported fee assumptions. Asset identifiers must match native economics.</p>
          <div className="paper-fields">
            <Field label="Feed latency (ns)"><input required pattern="[0-9]+" inputMode="numeric" value={feed} onChange={event => setFeed(event.target.value)} /></Field>
            <Field label="Decision latency (ns)"><input required pattern="[0-9]+" inputMode="numeric" value={decision} onChange={event => setDecision(event.target.value)} /></Field>
            <Field label="Order latency (ns)"><input required pattern="[0-9]+" inputMode="numeric" value={orderLatency} onChange={event => setOrderLatency(event.target.value)} /></Field>
          </div>
          <label className="paper-check"><input type="checkbox" required checked={confirmedFork} onChange={event => setConfirmedFork(event.target.checked)} />I explicitly intend a new run / fork with these starting balances, fees and latencies; previous history remains preserved.</label>
          <button className="primary" type="submit" disabled={!confirmedFork}>{currentRun ? 'Create explicit fork' : 'Create new paper run'}</button>
        </fieldset>
      </form>
    </details>
    {snapshot ? <Accounting snapshot={snapshot} disabled={disabled || !runActive} cancel={id => { void io.act(signal => props.request<PaperResponse>('/paper/cancel', { order_id: id, ...detailLimits }, signal)); }} /> : io.enabled && !io.error && <Empty>Reading native paper state…</Empty>}
    <section className="paper-section"><h3>Durable runs — explicit resume or separate archive audit</h3>
      <p className="caption">Resume requires the same exact source identity and replay ownership; live histories cannot resume across sessions. Resume reconstructs the selected history at the current cursor, not its final accounting.</p>
      {io.data?.runs.runs.length === 0 && <Empty>No durable runs recorded.</Empty>}
      {io.data && <><div className="paper-run-list">{io.data.runs.runs.map(run => {
        const sameSource = run.source_id === io.data?.runs.source_identity;
        const eligible = sameSource && props.frame?.source.mode !== 'live';
        return <article key={run.run_id}><strong>{run.run_id}{run.run_id === currentRun?.run_id ? ' — selected' : ''}</strong><p>Source {run.source_id}<br />Created delivery {run.delivery_index} · clock {run.created_at.clock_id} · ns {run.created_at.mono_ns}<br />Revision {run.revision} · parent {run.parent_run_id ?? 'none'}</p><div className="paper-actions"><button type="button" disabled={disabled || !eligible} onClick={() => resume(run.run_id)}>Resume this run at cursor</button><button type="button" disabled={disabled} onClick={() => showArchive(run.run_id)}>Open read-only archived audit</button></div>{!eligible && <p className="caption">{!sameSource ? 'Different source identity: resume unavailable.' : 'Live ownership: resume unavailable.'} Archived audit remains separately available.</p>}</article>;
      })}</div><p className="caption">Bounded list: {io.data.runs.runs.length} runs returned, limit 100. The API provides no omitted count or pagination; older runs may not appear at the limit.</p></>}
    </section>
    {io.enabled && archive?.scope === scope && <section className="paper-archive" aria-label="Read-only historical accounting audit"><h3>ARCHIVED AUDIT — not current replay or live state</h3><button type="button" onClick={() => setArchive(null)}>Close archive</button><p className="caption">Explicit opt-in read-only historical accounting. Not merged into current balances, positions or journal link choices. Scope: {archive.response.archive_scope}.</p><Provenance response={archive.response} /><Accounting snapshot={archive.response.snapshot} /></section>}
  </Panel>;
}

function futureNote(entry: JournalEntry, currentSource: string, cursor: string | undefined) {
  return entry.source_id === currentSource && cursor !== undefined && /^\d+$/.test(cursor) && /^\d+$/.test(entry.delivery_index) && BigInt(entry.delivery_index) > BigInt(cursor);
}

export function JournalPanel(props: WorkflowPanelProps) {
  const [allSources, setAllSources] = useState(false);
  const [runFilter, setRunFilter] = useState('');
  const [filterDraft, setFilterDraft] = useState('');
  const load = useCallback(async (query: WorkflowQuery, signal: AbortSignal) => {
    const journal = await query<JournalResponse>('/journal/query', { limit: 100, all_sources: allSources, ...(runFilter ? { run_id: runFilter } : {}) }, signal);
    signal.throwIfAborted();
    const paper = await query<PaperResponse>('/paper/query', detailLimits, signal);
    return { journal, paper };
  }, [allSources, runFilter]);
  const io = useWorkflowData(props, load);
  const [rationale, setRationale] = useState('');
  const [entryPrice, setEntryPrice] = useState('');
  const [stop, setStop] = useState('');
  const [context, setContext] = useState('');
  const [editing, setEditing] = useState<JournalEntry | null>(null);
  const [linkedRunId, setLinkedRunId] = useState('');
  const [orderId, setOrderId] = useState('');
  const scope = scopeOf(props);
  const snapshot = io.data?.paper.snapshot;
  const selectedInstrument = props.frame?.selection.instrument_id;
  const activeRun = snapshot?.run && !snapshot.reasons.includes('run_not_active_at_cursor') ? snapshot.run : null;
  const orders = snapshot?.orders.filter(order => order.request.instrument_id === selectedInstrument) ?? [];
  const validOrder = !orderId || orders.some(order => order.order_id === orderId);
  const validLink = !!editing || !linkedRunId || (activeRun?.run_id === linkedRunId && validOrder);
  const disabled = !io.enabled || io.pending;
  useEffect(() => {
    setLinkedRunId(''); setOrderId(''); setEditing(null); setRunFilter(''); setFilterDraft('');
    setRationale(''); setEntryPrice(''); setStop(''); setContext('');
  }, [scope]);
  const beginEdit = (entry: JournalEntry) => {
    setEditing(entry); setRationale(entry.rationale); setEntryPrice(entry.entry ?? ''); setStop(entry.stop ?? ''); setContext(entry.context);
    setLinkedRunId(''); setOrderId('');
  };
  const clearDraft = () => { setEditing(null); setRationale(''); setEntryPrice(''); setStop(''); setContext(''); setLinkedRunId(''); setOrderId(''); };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (disabled || !validLink) return;
    const input: JournalInput = {
      rationale, ...(entryPrice ? { entry: entryPrice } : {}), ...(stop ? { stop } : {}), context,
      ...(editing ? { entry_id: editing.entry_id, ...(editing.run_id ? { run_id: editing.run_id } : {}), ...(editing.order_id ? { order_id: editing.order_id } : {}) }
        : linkedRunId && activeRun ? { run_id: linkedRunId, ...(orderId ? { order_id: orderId } : {}) } : {}),
    };
    void io.act(signal => props.request<JournalPutResponse>('/journal/put', input, signal)).then(ok => { if (ok) clearDraft(); });
  };
  return <Panel title="Journal — durable manually authored notes" className="paper-workflow" extra={<button type="button" disabled={disabled} onClick={io.refresh}>Refresh persisted notes</button>}>
    <p className="caption">Manual rationale, intended entry, stop and context — never causal market features, signals or feed analytics. Notes can survive replay rewind and source reopen. A later note is not evidence available at an earlier market cursor.</p>
    <Availability {...io} />
    <form className="paper-journal-form" onSubmit={submit}>
      <h3>{editing ? `Edit ${editing.entry_id} — immutable origin retained` : 'Author a new note at the current source / instrument / cursor'}</h3>
      {editing ? <><p className="paper-provenance">Original source {editing.source_id} · session {editing.session_id} · delivery {editing.delivery_index} · instrument {editing.instrument_id} · run {editing.run_id ?? 'unlinked'} · order {editing.order_id ?? 'unlinked'}. Only rationale, entry, stop and context change.</p><Details title="Immutable authored origin" value={{ entry_id: editing.entry_id, source_id: editing.source_id, session_id: editing.session_id, delivery_index: editing.delivery_index, at: editing.at, instrument_id: editing.instrument_id, venue: editing.venue, run_id: editing.run_id, order_id: editing.order_id }} /></> : <p className="paper-provenance">Selected instrument {selectedInstrument ?? 'unavailable'} · source identity {io.data?.journal.source_identity ?? 'awaiting native response'} · current cursor {props.frame?.replay?.cursor.delivery_index ?? 'live source clock; captured natively on save'}.</p>}
      <fieldset disabled={disabled}><div className="paper-fields">
        <Field label="Rationale (required)"><textarea required maxLength={8192} value={rationale} onChange={event => setRationale(event.target.value)} /></Field>
        <Field label="Intended entry price — exact, optional"><input inputMode="decimal" maxLength={128} value={entryPrice} onChange={event => setEntryPrice(event.target.value)} /></Field>
        <Field label="Intended stop price — exact, optional"><input inputMode="decimal" maxLength={128} value={stop} onChange={event => setStop(event.target.value)} /></Field>
        <Field label="Authored context"><textarea maxLength={8192} value={context} onChange={event => setContext(event.target.value)} /></Field>
      </div>
      {!editing && <><label className="paper-check"><input type="checkbox" checked={!!linkedRunId} disabled={!activeRun && !linkedRunId} onChange={event => { setLinkedRunId(event.target.checked ? activeRun?.run_id ?? '' : ''); setOrderId(''); }} />Optionally link visible active run {linkedRunId || activeRun?.run_id || '(none at cursor)'}</label>
        {!!linkedRunId && <Field label="Optional visible order for selected instrument"><select value={orderId} onChange={event => setOrderId(event.target.value)}><option value="">Run only — no order link</option>{orders.map(order => <option key={order.order_id} value={order.order_id}>{order.order_id} · {order.request.side} · {order.status}</option>)}</select></Field>}
        {!!linkedRunId && <p className="caption">Only orders returned from the current cursor are offered. {snapshot?.presentation.orders_omitted ?? 0} orders omitted by native bounded presentation. Archive records are never link choices.</p>}
      </>}
      {!validLink && <p className="paper-notice">Selected linkage is no longer visible at this cursor. Refresh, deselect it, or choose a current order before saving.</p>}
      <div className="paper-actions"><button type="submit" className="primary" disabled={!validLink || !io.data}>{editing ? 'Persist edited text (preserve origin)' : 'Persist new journal note'}</button><button type="button" onClick={clearDraft}>{editing ? 'Cancel edit and clear draft' : 'Clear draft'}</button></div>
      </fieldset>
    </form>
    <section className="paper-section"><h3>Persisted journal</h3>
      <form className="paper-fields" onSubmit={event => { event.preventDefault(); setRunFilter(filterDraft.trim()); }}><label className="paper-check"><input type="checkbox" disabled={disabled} checked={allSources} onChange={event => setAllSources(event.target.checked)} />Explicitly include notes from all sources</label><Field label="Optional exact run filter"><input value={filterDraft} onChange={event => setFilterDraft(event.target.value)} /></Field><button type="submit" disabled={disabled}>Apply run filter</button><button type="button" disabled={disabled} onClick={() => { setFilterDraft(''); setRunFilter(''); }}>Clear run filter</button></form>
      {io.data && <><Provenance response={io.data.journal} /><p className="caption">Scope: {io.data.journal.scope}; run filter: {runFilter || 'none'}. Showing {io.data.journal.entries.length} notes, limit {io.data.journal.limit}. The API provides no omitted count or pagination; older notes may be outside this bounded list.</p></>}
      {io.data?.journal.entries.length === 0 && <Empty>No persisted notes match this explicit source/run scope.</Empty>}
      {!io.data && io.enabled && !io.error && <Empty>Reading persisted notes and eligible current paper links…</Empty>}
      <div className="paper-journal-list">{io.data?.journal.entries.map(entry => <article key={entry.entry_id}>
        <header><strong>{entry.entry_id}</strong><button type="button" disabled={disabled} onClick={() => beginEdit(entry)}>Edit text, preserve origin</button></header>
        <p className="paper-notice">{futureNote(entry, io.data!.journal.source_identity, props.frame?.replay?.cursor.delivery_index) ? 'FUTURE-TO-CURSOR MANUAL NOTE — authored at a later replay delivery. Not a causal feed feature.' : entry.source_id !== io.data!.journal.source_identity ? 'OTHER-SOURCE MANUAL NOTE — no current market-clock alignment.' : 'MANUALLY AUTHORED NOTE — not a causal feed feature.'}</p>
        <p className="paper-provenance">Source {entry.source_id} · session {entry.session_id} · delivery {entry.delivery_index}<br />{entry.venue} · {entry.instrument_id}<br />Clock {entry.at.clock_id} · monotonic ns {entry.at.mono_ns} · wall ns {entry.at.wall_ns ?? 'unavailable'}<br />Run {entry.run_id ?? 'unlinked'} · order {entry.order_id ?? 'unlinked'}</p>
        <dl><dt>Rationale</dt><dd>{entry.rationale}</dd><dt>Intended entry</dt><dd><Exact value={entry.entry} /></dd><dt>Intended stop</dt><dd><Exact value={entry.stop} /></dd><dt>Authored context</dt><dd>{entry.context || 'Not supplied'}</dd></dl>
        <Details title="Exact persisted note and immutable origin" value={entry} />
      </article>)}</div>
    </section>
  </Panel>;
}
