import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { errorMessage } from './api';
import type { AlertEvidenceResponse, AlertEvaluation, AlertKind, AlertRuleInput, AlertsResponse, ContextResponse, ResearchReport, ResearchResponse, WorkflowPanelProps } from './workflow-api';
import { Details, Empty, Panel, Reasons, Value } from './WorkflowUI';
import { venueLabel } from './format';
import { metadataVerified } from './instrument-metadata';
import './signal-panels.css';

function panelScope({ frame }: WorkflowPanelProps) {
  return JSON.stringify([frame?.session_id, frame?.generation, frame?.source.id, frame?.selection.instrument_id, frame?.selection.metadata_status, frame?.selection.metadata_version]);
}

// One completion-paced read or explicit action at a time. No mutation is retried.
function useNativePanel<T extends object>(props: WorkflowPanelProps, path: string, input: object, cadence = 2000) {
  const { frame, active, connected, busy, request } = props;
  const scope = panelScope(props);
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');
  const enabled = active && visible && connected && !busy && !!frame &&
    !['loading', 'seeking', 'unavailable', 'error'].includes(frame.status);
  const [result, setResult] = useState<{ scope: string; value: T } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [revision, setRevision] = useState(0);
  const flight = useRef<{ controller: AbortController; mutation: boolean } | null>(null);
  const epoch = useRef(0);
  const latest = useRef({ enabled, scope });
  latest.current = { enabled, scope };

  useEffect(() => {
    const update = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    const version = ++epoch.current;
    setResult(null);
    if (!enabled) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      if (stopped) return;
      if (!flight.current) {
        const job = { controller: new AbortController(), mutation: false };
        flight.current = job;
        setPending(true);
        try {
          const value = await request<T>(path, input, job.controller.signal);
          if (!stopped && version === epoch.current && latest.current.scope === scope && latest.current.enabled) {
            setResult({ scope, value });
          }
        } catch (reason) {
          if (!stopped && !job.controller.signal.aborted) {
            setResult(null);
            setError(errorMessage(reason));
          }
        } finally {
          if (flight.current === job) { flight.current = null; setPending(false); }
        }
      }
      if (!stopped) timer = window.setTimeout(() => { void poll(); }, cadence);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      const job = flight.current;
      if (job?.mutation) setError('Action interrupted: it may already have committed. Refresh before retrying.');
      job?.controller.abort();
    };
  }, [enabled, scope, request, path, input, cadence, revision]);

  const action = useCallback(async <R extends object,>(actionPath: string, body: object, mutation = false): Promise<R | null> => {
    if (!latest.current.enabled || latest.current.scope !== scope || flight.current) return null;
    const version = epoch.current;
    const job = { controller: new AbortController(), mutation };
    flight.current = job;
    setPending(true);
    setError(null);
    try {
      const value = await request<R>(actionPath, body, job.controller.signal);
      if (job.controller.signal.aborted || version !== epoch.current || latest.current.scope !== scope || !latest.current.enabled) return null;
      return value;
    } catch (reason) {
      if (version === epoch.current) setError(`${errorMessage(reason)}${mutation ? ' The action may already have committed; refresh before retrying. No automatic retry was made.' : ''}`);
      return null;
    } finally {
      if (flight.current === job) { flight.current = null; setPending(false); }
    }
  }, [request, scope]);

  const accept = (value: T) => setResult({ scope, value });
  const refresh = () => { if (!flight.current) { setError(null); setRevision(value => value + 1); } };
  return { data: enabled && result?.scope === scope ? result.value : null, enabled, pending, error, scope, action, accept, refresh };
}

function Field({ title, children }: { title: string; children: ReactNode }) {
  return <label className="signal-field"><span>{title}</span>{children}</label>;
}
function TextField({ title, value, change, required = true, integer = false, type = 'text' }: {
  title: string; value: string; change: (value: string) => void; required?: boolean; integer?: boolean; type?: string;
}) {
  return <Field title={title}><input type={type} value={value} onChange={event => change(event.target.value)} required={required} pattern={integer ? '[0-9]+' : undefined} /></Field>;
}
function TableScroll({ title, children }: { title: string; children: ReactNode }) {
  return <div className="signal-table-scroll" role="region" aria-label={title} tabIndex={0}>{children}</div>;
}
function QueryStatus({ enabled, pending, error }: { enabled: boolean; pending: boolean; error: string | null }) {
  return <>{error && <p role="alert" className="error">{error}</p>}{!enabled && <Empty>Unavailable while this panel is hidden, disconnected, or the native lifecycle is transitioning.</Empty>}{enabled && pending && <p className="caption" role="status">Requesting native data…</p>}</>;
}
const alertKinds: AlertKind[] = ['spread_shock', 'depth_shock', 'large_executed_print', 'event_ofi', 'best_quote_imbalance', 'persistent_displayed_liquidity', 'net_cost'];
const kindName = (kind: string) => kind.replaceAll('_', ' ');

function Evaluation({ value }: { value: AlertEvaluation }) {
  return <><Value value={value.value} /> <span>{value.rule.unit}</span>
    <p>{value.eligibility.eligible ? 'Eligible' : 'Unavailable'} · {value.armed ? 'Armed' : 'Latched'} · condition {value.condition_met ? 'met' : 'not met'}</p>
    <p>Delivery {value.delivery_index} · cooldown remaining {value.cooldown_remaining_ns} ns · persistence {value.persistence_ns} ns</p>
    <Reasons reasons={[...value.eligibility.reasons, ...value.reasons]} />
    <Details title="Evaluation clock and window" value={{ now: value.now, window: value.window }} />
  </>;
}

export function AlertsPanel(props: WorkflowPanelProps) {
  const input = useMemo(() => ({ event_limit: 100 }), []);
  const query = useNativePanel<AlertsResponse>(props, '/alerts/query', input);
  const [kind, setKind] = useState<AlertKind>('spread_shock');
  const [direction, setDirection] = useState<'above' | 'below'>('above');
  const [fields, setFields] = useState({ threshold: '', rearm: '', window: '0', cooldown: '0', depth: '10', quantity: '', buyFee: '', buySource: '', buyDate: '', sellFee: '', sellSource: '', sellDate: '', latency: '', rebalance: '' });
  const [depthSide, setDepthSide] = useState<'bid' | 'ask'>('bid');
  const [sell, setSell] = useState('');
  const [evidence, setEvidence] = useState<{ scope: string; title: string; value: AlertEvidenceResponse } | null>(null);
  const edit = (key: keyof typeof fields) => (value: string) => setFields(current => ({ ...current, [key]: value }));
  const disabled = !query.enabled || query.pending;
  const selected = props.instruments.find(item => item.instrument_id === props.frame?.selection.instrument_id
    && item.metadata_version === props.frame.selection.metadata_version && item.metadata_status === props.frame.selection.metadata_status);
  const netCostAvailable = metadataVerified(props.frame) && !!selected;
  const unit = query.data?.kinds.find(item => item.kind === kind)?.unit;
  useEffect(() => { setEvidence(null); setSell(''); }, [query.scope]);
  useEffect(() => { if (!query.enabled) setEvidence(null); }, [query.enabled]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (disabled || !unit || kind === 'net_cost' && !netCostAvailable) return;
    const body: AlertRuleInput = {
      instrument_id: props.frame?.selection.instrument_id ?? undefined, kind, direction,
      threshold: fields.threshold, rearm_threshold: fields.rearm, window_ns: fields.window, cooldown_ns: fields.cooldown,
      ...(kind === 'depth_shock' ? { depth_side: depthSide, depth_bps: fields.depth } : {}),
      ...(kind === 'net_cost' ? {
        sell_instrument_id: sell, base_quantity: fields.quantity,
        buy_fees: { fee_bps: fields.buyFee, fee_provenance: fields.buySource, fee_date: fields.buyDate },
        sell_fees: { fee_bps: fields.sellFee, fee_provenance: fields.sellSource, fee_date: fields.sellDate },
        latency_bps: fields.latency, rebalance_quote: fields.rebalance,
      } : {}),
    };
    const response = await query.action<AlertsResponse>('/alerts/rules/put', { ...body, event_limit: 100 }, true);
    if (response) { query.accept(response); setEvidence(null); }
  };
  const remove = async (rule_id: string) => {
    const response = await query.action<AlertsResponse>('/alerts/rules/remove', { rule_id, event_limit: 100 }, true);
    if (response) { query.accept(response); setEvidence(null); }
  };
  const inspect = async (target: { rule_id: string } | { event_id: string }) => {
    setEvidence(null);
    const response = await query.action<AlertEvidenceResponse>('/alerts/evidence', target);
    if (response) setEvidence({ scope: query.scope, title: 'rule_id' in target ? `Rule ${target.rule_id}` : `Fire ${target.event_id}`, value: response });
  };
  const snapshot = query.data?.snapshot;
  return <Panel title="Local alerts" className="signal-panel" extra={<button disabled={disabled} onClick={query.refresh}>Refresh alerts</button>}>
    <p className="caption">Local only; no outbound delivery. Evaluations describe the last delivered native batch, not continuous live freshness. Rewind never adds future fires.</p>
    <QueryStatus {...query} />
    <details className="signal-disclosure"><summary>Create alert rule — all seven native kinds</summary>
      <form onSubmit={event => { void create(event); }}>
        <fieldset disabled={disabled || !unit} className="signal-form"><legend>Rule for {selected?.native_symbol ?? 'selected instrument'}</legend>
          <p className="signal-full caption">Native identity: {props.frame?.selection.instrument_id ?? 'unavailable'} · threshold/rearm unit: {unit ?? 'unavailable until native kinds are loaded'}. All decimal and nanosecond values are sent as exact text.</p>
          <p className="signal-full caption">Event OFI and persistent displayed liquidity require a positive window equal to the native analytics window; all other kinds require 0. Rearm must be below the trigger for “above”, or above it for “below”. Unsupported windows and depth bands remain explicitly unavailable.</p>
          <div className="signal-full"><Details title="Current native rolling windows and persistence provenance" value={props.frame?.analytics?.metrics.filter(metric => metric.name === 'event_ofi' || metric.name === 'displayed_persistence').map(metric => ({ name: metric.name, window: metric.window, eligibility: metric.eligibility, formula_version: metric.formula_version, config_hash: metric.config_hash })) ?? null} /></div>
          <Field title="Alert kind"><select value={kind} onChange={event => setKind(event.target.value as AlertKind)}>{alertKinds.map(item => <option key={item} value={item}>{kindName(item)}</option>)}</select></Field>
          <Field title="Trigger direction"><select value={direction} onChange={event => setDirection(event.target.value as 'above' | 'below')}><option value="above">Above</option><option value="below">Below</option></select></Field>
          <TextField title={`Threshold (${unit ?? 'native unit'})`} value={fields.threshold} change={edit('threshold')} />
          <TextField title={`Rearm threshold (${unit ?? 'native unit'})`} value={fields.rearm} change={edit('rearm')} />
          <TextField title="Window (ns; 0 for instantaneous kinds)" value={fields.window} change={edit('window')} integer />
          <TextField title="Cooldown (ns; 0 = none)" value={fields.cooldown} change={edit('cooldown')} integer />
          {kind === 'depth_shock' && <>
            <Field title="Depth side"><select value={depthSide} onChange={event => setDepthSide(event.target.value as 'bid' | 'ask')}><option value="bid">Bid</option><option value="ask">Ask</option></select></Field>
            <TextField title="Depth band (bps)" value={fields.depth} change={edit('depth')} />
          </>}
          {kind === 'net_cost' && <>
            <p className="signal-full caption">Selected instrument is the buy leg. Supply both dated fee assumptions, size, latency stress and rebalance quote cost explicitly; zero is an explicit assumption, not a default.</p>
            {!netCostAvailable && <p className="signal-full caption warning" role="status">Net-cost rules require verified session-pinned instrument metadata. Other alert kinds and retained alert evidence remain available.</p>}
            <Field title="Sell native instrument"><select required value={sell} onChange={event => setSell(event.target.value)}><option value="">Choose sell leg</option>{props.instruments.map(item => <option key={item.instrument_id} value={item.instrument_id}>{item.instrument_id}</option>)}</select></Field>
            <TextField title="Base quantity" value={fields.quantity} change={edit('quantity')} />
            <TextField title="Buy fee (bps)" value={fields.buyFee} change={edit('buyFee')} />
            <TextField title="Buy fee provenance" value={fields.buySource} change={edit('buySource')} />
            <TextField title="Buy fee date" type="date" value={fields.buyDate} change={edit('buyDate')} />
            <TextField title="Sell fee (bps)" value={fields.sellFee} change={edit('sellFee')} />
            <TextField title="Sell fee provenance" value={fields.sellSource} change={edit('sellSource')} />
            <TextField title="Sell fee date" type="date" value={fields.sellDate} change={edit('sellDate')} />
            <TextField title="Latency stress (bps)" value={fields.latency} change={edit('latency')} />
            <TextField title="Rebalance cost (buy quote units)" value={fields.rebalance} change={edit('rebalance')} />
          </>}
          <button type="submit" className="primary" disabled={kind === 'net_cost' && !netCostAvailable}>Create local rule</button>
        </fieldset>
      </form>
    </details>
    {snapshot && <>
      <p className="caption">Last delivered evaluation batch: {snapshot.delivery_index} · source clock {snapshot.now?.clock_id ?? 'unavailable'} / {snapshot.now?.mono_ns ?? 'unavailable'} ns. Retained fires omitted: {query.data?.presentation.events_omitted}; dropped fires: {snapshot.dropped_events}.</p>
      <Reasons reasons={snapshot.reasons} />
      {snapshot.rules.length === 0 ? <Empty>No native alert rules configured.</Empty> : <TableScroll title="Rules and last-delivered evaluations"><table><caption>Rules and last-delivered evaluations</caption><thead><tr><th scope="col">Rule / native identity</th><th scope="col">Exact configuration</th><th scope="col">Last evaluation</th><th scope="col">Actions</th></tr></thead><tbody>{snapshot.rules.map(rule => {
        const evaluation = snapshot.evaluations.find(item => item.rule.rule_id === rule.rule_id);
        return <tr key={rule.rule_id}><th scope="row">{kindName(rule.kind)}<small>{rule.rule_id}</small><small>{rule.key.instrument_id}</small></th><td>{rule.direction} {rule.threshold} {rule.unit}<p>Rearm {rule.rearm_threshold}; window {rule.window_ns} ns; cooldown {rule.cooldown_ns} ns</p><Details title="Exact rule, cost assumptions and native keys" value={rule} /></td><td>{evaluation ? <Evaluation value={evaluation} /> : 'No delivered evaluation yet'}</td><td><div className="signal-actions"><button disabled={disabled || !evaluation?.evidence_available} onClick={() => { void inspect({ rule_id: rule.rule_id }); }}>Load rule evidence</button><button disabled={disabled} onClick={() => { void remove(rule.rule_id); }}>Remove rule</button></div></td></tr>;
      })}</tbody></table></TableScroll>}
      <h3>Fired timeline — retained prefix, oldest to newest</h3>
      {snapshot.events.length === 0 ? <Empty>No retained alert fires at this cursor.</Empty> : <TableScroll title="Fired alert timeline"><table><thead><tr><th scope="col">Fire / rule</th><th scope="col">Delivered evaluation</th><th scope="col">Supporting inputs</th></tr></thead><tbody>{snapshot.events.map(event => <tr key={event.event_id}><th scope="row">{event.event_id}<small>{event.evaluation.rule.rule_id}</small><small>{kindName(event.evaluation.rule.kind)}</small></th><td><Evaluation value={event.evaluation} /></td><td><button disabled={disabled} onClick={() => { void inspect({ event_id: event.event_id }); }}>Load fire {event.event_id} evidence</button></td></tr>)}</tbody></table></TableScroll>}
    </>}
    {query.enabled && evidence?.scope === query.scope && <section className="signal-evidence" aria-label="Loaded alert evidence"><h3>{evidence.title} — requested native evidence</h3><p className="caption">Snapshot captured on explicit request; not automatically refreshed. Includes exact references, book/trade inputs, prints, displayed liquidity and pair cost inputs where available.</p><Details title="Full bounded native evidence" value={evidence.value} /></section>}
  </Panel>;
}

export function ContextPanel(props: WorkflowPanelProps & { selectInstrument: (id: string) => Promise<void> }) {
  const scope = panelScope(props);
  const input = useMemo(() => ({}), []);
  const query = useNativePanel<ContextResponse>(props, '/context', input);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const selectingRef = useRef(false);
  const snapshot = query.data?.snapshot;
  const disabled = !query.enabled || query.pending || selecting;
  useEffect(() => { setSelectionError(null); }, [scope]);
  const select = async (id: string) => {
    if (disabled || selectingRef.current) return;
    selectingRef.current = true; setSelecting(true); setSelectionError(null);
    try { await props.selectInstrument(id); }
    catch (reason) { setSelectionError(errorMessage(reason)); }
    finally { selectingRef.current = false; setSelecting(false); }
  };
  return <Panel title="Native reference context" className="signal-panel" extra={<button disabled={disabled} onClick={query.refresh}>Refresh context</button>}>
    <p className="caption">AllMids, marks and oracles are non-executable references, not order prices. No USD/USDT/USDC equivalence is assumed. Reference basis is not an executable spread or profit claim.</p>
    <p className="caption">Native context coverage is Hyperliquid-only: AllMids and asset-context observations must be supplied by this source or capture. Other venues do not gain context from their book or tape; missing observations remain unavailable.</p>
    <QueryStatus {...query} />{selectionError && <p className="error" role="alert">{selectionError}</p>}
    {snapshot && <>
      <p className="caption">Source {query.data?.source_identity} · delivery {query.data?.delivery_index} · now {snapshot.now?.mono_ns ?? 'unavailable'} ns.</p>
      <Reasons reasons={snapshot.reasons} />
      <h3>AllMids watchlist — native identities, including HYPE when supplied</h3>
      {snapshot.watchlist.length === 0 ? <Empty>No native mids available in this delivered prefix.</Empty> : <TableScroll title="Native AllMids watchlist"><table><thead><tr><th scope="col">Instrument</th><th scope="col">Reference</th><th scope="col">Availability / provenance</th><th scope="col">Selection</th></tr></thead><tbody>{snapshot.watchlist.map(item => <tr key={`${item.stream_id}:${item.instrument.instrument_id}`}><th scope="row">{item.instrument.native_symbol}<small>{item.instrument.instrument_id}</small><small>{venueLabel(item.instrument.venue)} · {item.instrument.market_type}</small></th><td><Value value={item.value?.price} /> {item.value?.currency}<small>{item.value?.kind ?? 'unavailable'} · {item.label}</small></td><td>{item.eligibility.eligible ? 'Eligible reference' : 'Unavailable'}<Reasons reasons={item.eligibility.reasons} /><Details title="Native metadata, stream and reference timestamp" value={{ instrument: item.instrument, stream_id: item.stream_id, asof: item.asof, interval_ns: item.value?.interval_ns }} /></td><td><button disabled={disabled || props.frame?.selection.instrument_id === item.instrument.instrument_id} onClick={() => { void select(item.instrument.instrument_id); }}>{props.frame?.selection.instrument_id === item.instrument.instrument_id ? 'Selected' : `Select ${item.instrument.native_symbol}`}</button></td></tr>)}</tbody></table></TableScroll>}
      <h3>Mark, oracle, hourly funding and open interest</h3>
      {snapshot.contexts.length === 0 ? <Empty>No native asset-context observations available.</Empty> : <TableScroll title="Asset context observations"><table><thead><tr><th scope="col">Native instrument</th><th scope="col">Mark / oracle</th><th scope="col">Funding</th><th scope="col">Open interest</th><th scope="col">Provenance / reasons</th></tr></thead><tbody>{snapshot.contexts.map(item => <tr key={`${item.stream_id}:${item.instrument.instrument_id}`}><th scope="row">{item.instrument.native_symbol}<small>{item.instrument.instrument_id}</small><small>{item.label}</small></th><td>Mark <Value value={item.value?.mark} /><p>Oracle <Value value={item.value?.oracle} /></p><small>{item.instrument.quote.asset_id} · non-executable</small></td><td><Value value={item.value?.funding_rate} /><small>{item.funding_rate_unit}</small><p>Interval {item.value?.funding_interval_ns ?? 'unavailable'} ns</p><small>Native hourly interval; no annualization</small></td><td><Value value={item.value?.open_interest} /><small>Unit: {item.value?.open_interest_unit ?? 'unavailable'}</small><small>Unit provenance: {item.open_interest_unit_provenance}</small></td><td>{item.eligibility.eligible ? 'Eligible reference' : 'Unavailable'}<Reasons reasons={item.eligibility.reasons} />{item.field_reasons.map(([field, reasons]) => <div key={field}><strong>{field}</strong><Reasons reasons={reasons} /></div>)}<Details title="Exact native context and observation provenance" value={item} /></td></tr>)}</tbody></table></TableScroll>}
      <h3>Reference-only basis — non-executable</h3>
      {snapshot.basis.length === 0 ? <Empty>No native reference-basis pairs available.</Empty> : <TableScroll title="Non-executable reference basis"><table><thead><tr><th scope="col">Pair / reference</th><th scope="col">Native references</th><th scope="col">Converted proceeds / funding</th><th scope="col">Basis / reasons</th></tr></thead><tbody>{snapshot.basis.map((item, index) => <tr key={`${item.spot_instrument_id}:${item.perpetual_instrument_id}:${item.reference_kind}:${index}`}><th scope="row">{item.spot_instrument_id}<small>{item.perpetual_instrument_id}</small><small>{item.reference_kind} · {item.label}</small></th><td>Spot mid <Value value={item.spot_mid} /> {item.quote_asset_id}<p>Perpetual <Value value={item.perpetual_reference} /> {item.perpetual_quote_asset_id}</p></td><td>Proceeds <Value value={item.proceeds_in_spot_quote} /><p>Funding cost <Value value={item.funding_cost_in_spot_quote} /></p><small>{item.quote_asset_id}</small></td><td>Proceeds <Value value={item.proceeds_basis_bps} /> bps<p>Funding <Value value={item.funding_basis_bps} /> bps</p><small>{item.cost_scope}</small><Reasons reasons={item.reasons} /><Details title="Reference clocks, quote compatibility and cost scope" value={item} /></td></tr>)}</tbody></table></TableScroll>}
    </>}
  </Panel>;
}

function CountPairs({ title, values }: { title: string; values: [string, string][] }) {
  return <section><h3>{title}</h3>{values.length === 0 ? <p className="caption">None reported.</p> : <dl className="signal-counts">{values.map(([name, value], index) => <div key={`${name}:${index}`}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>}</section>;
}

function Report({ report, disabled, page }: { report: ResearchReport; disabled: boolean; page: (offset: number) => void }) {
  const { trial_offset: offset, trial_limit: limit, trial_total: total } = report.presentation;
  return <section aria-label="Selected immutable research report">
    <h3>Outcome: {report.outcome}</h3>
    <p className="caption">{report.synthetic ? 'Synthetic source — not evidence of real-market returns.' : 'Recorded source — observational research, not a profitability claim.'} Every candidate, baseline, split and scenario is retained in native page order, including negative and insufficient outcomes. No scores or favorable-only filtering.</p>
    <dl className="signal-counts"><div><dt>Source</dt><dd>{report.source_id}</dd></div><div><dt>Provenance</dt><dd>{report.provenance}</dd></div><div><dt>Duration (ns)</dt><dd>{report.duration_ns}</dd></div><div><dt>Train end (ns)</dt><dd>{report.train_end_ns}</dd></div><div><dt>Validation end (ns)</dt><dd>{report.validation_end_ns}</dd></div><div><dt>Observation count</dt><dd>{report.observation_count}</dd></div><div><dt>Label count</dt><dd>{report.label_count}</dd></div><div><dt>Portfolio turnover</dt><dd>{report.turnover ?? 'Unavailable'}</dd></div><div><dt>Portfolio drawdown</dt><dd>{report.drawdown ?? 'Unavailable'}</dd></div></dl>
    <Reasons reasons={report.portfolio_unavailable_reasons} />
    <Details title="Exact research configuration: hypotheses, costs, latency, horizons, purge, embargo and coverage assumptions" value={report.config} />
    <Details title="Immutable report identity and hashes" value={{ attempt_id: report.attempt_id, version: report.version, report_hash: report.report_hash, source_hash: report.source_hash, config_hash: report.config_hash, observation_hash: report.observation_hash, label_hash: report.label_hash }} />
    <CountPairs title="Split sample counts" values={report.split_counts} />
    <CountPairs title="All report exclusions" values={report.exclusions} />
    <h3>Native candidate selections and insufficient-selection reasons</h3>
    {report.selections.length === 0 ? <Empty>No candidate selections reported.</Empty> : <TableScroll title="Native candidate selections"><table><thead><tr><th scope="col">Hypothesis</th><th scope="col">Horizon (ns)</th><th scope="col">Threshold</th><th scope="col">Reason</th></tr></thead><tbody>{report.selections.map((item, index) => <tr key={index}><th scope="row">{item.hypothesis}</th><td>{item.horizon_ns}</td><td>{item.threshold ?? 'Unavailable / no selection'}</td><td>{item.reason}</td></tr>)}</tbody></table></TableScroll>}
    <div className="signal-pagination"><h3>All candidate / baseline trials</h3><span>Rows {total === 0 ? 0 : offset + 1}–{Math.min(offset + report.trials.length, total)} of {total}</span><button disabled={disabled || offset === 0} onClick={() => page(Math.max(0, offset - limit))}>Previous trial page</button><button disabled={disabled || offset + limit >= total || offset + limit > 50000} onClick={() => page(offset + limit)}>Next trial page</button></div>
    {offset + limit > 50000 && offset + limit < total && <p className="warning caption">HTTP pagination is capped at offset 50000. Remaining trials require the immutable offline report; they have not been filtered out.</p>}
    {report.trials.length === 0 ? <Empty>No trials in this report page.</Empty> : <TableScroll title="All research trials, costs, latency and baselines"><table className="signal-trials"><thead><tr><th scope="col">Hypothesis / threshold</th><th scope="col">Horizon / scenario</th><th scope="col">Split / native venue-regime group</th><th scope="col">Sample counts</th><th scope="col">Gross / net payoff (bps)</th><th scope="col">Hit / win / loss</th><th scope="col">Matched baselines (bps)</th><th scope="col">Exclusions / exact statistics</th></tr></thead><tbody>{report.trials.map((trial, index) => <tr key={offset + index}><th scope="row">{trial.hypothesis}<small>Threshold {trial.threshold}</small></th><td>{trial.horizon_ns} ns<small>{trial.scenario}</small></td><td>{trial.split}<small>{trial.group}</small></td><td>Eligible {trial.statistics.eligible_samples}<small>Selected {trial.statistics.selected_samples}</small><small>Labeled {trial.statistics.labeled_samples}</small><small>Net {trial.statistics.net_samples}</small><small>Non-overlapping {trial.statistics.nonoverlapping_samples}</small></td><td>Mean gross <Value value={trial.statistics.mean_gross_bps} /><p>Expected net <Value value={trial.statistics.expected_net_payoff_bps} /></p></td><td>Hit rate <Value value={trial.statistics.hit_rate} /><p>Average win <Value value={trial.statistics.average_win_bps} /> bps</p><p>Average loss <Value value={trial.statistics.average_loss_bps} /> bps</p></td><td>No trade <Value value={trial.statistics.no_trade_bps} /><p>Matched long <Value value={trial.statistics.matched_long_bps} /></p><p>Matched short <Value value={trial.statistics.matched_short_bps} /></p></td><td>{trial.statistics.exclusions.length === 0 ? 'No exclusions reported' : <dl>{trial.statistics.exclusions.map(([reason, count], i) => <div key={`${reason}:${i}`}><dt>{reason}</dt><dd>{count}</dd></div>)}</dl>}<Details title="Exact trial values and sample counts" value={trial} /></td></tr>)}</tbody></table></TableScroll>}
    <h3>Native report limitations</h3><Reasons reasons={report.limitations} />
  </section>;
}

export function ResearchPanel(props: WorkflowPanelProps) {
  const scope = panelScope(props);
  const [selection, setSelection] = useState<{ scope: string; id: string; offset: number } | null>(null);
  const chosen = selection?.scope === scope ? selection : null;
  const input = useMemo(() => ({ limit: 100, ...(chosen ? { attempt_id: chosen.id, trial_offset: chosen.offset } : {}), trial_limit: 50 }), [chosen]);
  const query = useNativePanel<ResearchResponse>(props, '/research/query', input, 5000);
  const disabled = !query.enabled || query.pending;
  const data = query.data;
  return <Panel title="Offline research history" className="signal-panel" extra={<button disabled={disabled} onClick={query.refresh}>Refresh research</button>}>
    <p className="caption">Browser access is read-only: actual attempt history and immutable reports. Research execution is offline CLI only; this panel starts no jobs, orders or browser-side financial calculations. Latest 100 attempts are requested; the API does not expose an older-attempt page.</p>
    <details className="signal-disclosure"><summary>Offline CLI commands — run outside the browser</summary><p className="caption">Demo uses explicitly synthetic native data:</p><code className="signal-command">python -m src.workstation.cli research run --demo --duration 900 --state-dir data/workstation-state</code><p className="caption">For a real capture, replace <code>--demo --duration 900</code> with <code>--capture PATH</code>. Optional <code>--config PATH</code> supplies explicit assumptions. Refresh this history after running the CLI.</p></details>
    <QueryStatus {...query} />
    {data && <>
      {data.attempts.length === 0 ? <Empty>No persisted research attempts. Failed and running attempts appear here too when present.</Empty> : <TableScroll title="All retained research attempts"><table><thead><tr><th scope="col">Attempt / status</th><th scope="col">Source / timestamps</th><th scope="col">Failure / provenance</th><th scope="col">Report</th></tr></thead><tbody>{data.attempts.map(attempt => <tr key={attempt.attempt_id}><th scope="row">{attempt.attempt_id}<small>{attempt.status}</small></th><td>{attempt.source}<small>Started wall ns: {attempt.started_wall_ns}</small><small>Finished wall ns: {attempt.finished_wall_ns ?? 'not finished'}</small></td><td>{attempt.failure ?? 'No failure recorded'}<Details title="Attempt status, exact hashes and timestamps" value={attempt} /></td><td><button disabled={disabled || chosen?.id === attempt.attempt_id} onClick={() => setSelection({ scope, id: attempt.attempt_id, offset: 0 })}>{chosen?.id === attempt.attempt_id ? 'Selected attempt' : 'Inspect attempt'}</button></td></tr>)}</tbody></table></TableScroll>}
      {!chosen && <Empty>Select any attempt to inspect its configuration, failure or immutable report.</Empty>}
      {data.attempt && <section aria-label="Selected research attempt"><h3>Selected attempt: {data.attempt.attempt_id} · {data.attempt.status}</h3>{data.attempt.failure && <p className="error">{data.attempt.failure}</p>}<Details title="Selected attempt configuration and source provenance" value={{ attempt: data.attempt, config: data.config, source: data.source }} />{!data.report && <Empty>No immutable report is available for this attempt ({data.attempt.status}). This is not a zero-return or successful research outcome.</Empty>}</section>}
      {data.report && <Report report={data.report} disabled={disabled} page={offset => { if (chosen) setSelection({ ...chosen, offset }); }} />}
    </>}
  </Panel>;
}
