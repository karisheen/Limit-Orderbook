import { useLayoutEffect, useRef, useState } from 'react';
import type { Frame, ProposalRisk, ProposalRiskInput } from './api';
import { errorMessage } from './api';

interface RiskSnapshot {
  data: ProposalRisk;
  input: ProposalRiskInput;
  inputKey: string;
  identity: string;
  session: string;
  source: string;
  cursor: string;
}

export function ProposalRiskPanel({ frame, connected, calculate }: {
  frame: Frame | null;
  connected: boolean;
  calculate: (input: ProposalRiskInput, signal: AbortSignal) => Promise<ProposalRisk>;
}) {
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [entry, setEntry] = useState('');
  const [stop, setStop] = useState('');
  const [sizeMode, setSizeMode] = useState<'base' | 'risk'>('base');
  const [size, setSize] = useState('');
  const [feeMode, setFeeMode] = useState<'unknown' | 'assumed'>('unknown');
  const [fee, setFee] = useState('');
  const [feeDate, setFeeDate] = useState('');
  const [provenance, setProvenance] = useState('');
  const [result, setResult] = useState<RiskSnapshot | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const input: ProposalRiskInput = {
    side, entry_price: entry, stop_price: stop,
    ...(sizeMode === 'base' ? { base_quantity: size } : { risk_budget: size }),
    ...(feeMode === 'assumed' ? { fee_bps: fee, fee_provenance: provenance, fee_date: feeDate } : {}),
  };
  const inputKey = JSON.stringify(input);
  const identity = JSON.stringify([frame?.session_id, frame?.generation, frame?.source.id, frame?.source.mode, frame?.selection.instrument_id]);
  const unavailable = !connected ? 'Disconnected · reconnect before calculating.'
    : !frame ? 'Waiting for a session frame.'
    : !frame.selection.instrument_id ? 'Select an exact instrument before calculating.'
    : frame.status === 'loading' || frame.status === 'seeking' ? 'Source is loading or seeking · wait before calculating.'
    : frame.status === 'unavailable' || frame.status === 'error' ? frame.error ?? 'Source unavailable · inspect the source health reasons.'
    : null;

  // Fence before async responses can publish after a committed input/identity change.
  // Keep same-identity results as explicitly stale evidence, never as a new calculation.
  useLayoutEffect(() => {
    abort.current?.abort();
    abort.current = null;
    setPending(false);
    setError(null);
    setResult(previous => previous?.identity === identity ? previous : null);
    return () => { abort.current?.abort(); };
  }, [identity, inputKey, unavailable]);

  const submit = async () => {
    if (!frame || unavailable || pending) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPending(true);
    setError(null);
    try {
      const data = await calculate(input, controller.signal);
      if (controller.signal.aborted || abort.current !== controller) return;
      if (data.generation !== frame.generation || data.instrument_id !== frame.selection.instrument_id) {
        setError('Calculation returned an obsolete generation or instrument. Calculate again for the current selection.');
        return;
      }
      setResult({
        data, input, inputKey, identity, session: frame.session_id, source: frame.source.id,
        cursor: frame.replay?.cursor.delivery_index ?? 'live observation',
      });
    } catch (cause) {
      if (!controller.signal.aborted && abort.current === controller) setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted && abort.current === controller) {
        abort.current = null;
        setPending(false);
      }
    }
  };

  const snapshot = result?.identity === identity ? result : null;
  const data = snapshot?.data;
  const values = data ? [
    ['Scenario base quantity', data.base_quantity],
    ['Suggested base quantity', data.suggested_base_quantity],
    [`Gross stop loss (${data.quote_asset_id})`, data.gross_loss],
    [`Assumed fee cost (${data.quote_asset_id})`, data.fee_cost],
    [`Estimated loss including fees (${data.quote_asset_id})`, data.estimated_loss],
  ] : [];

  return <section className="panel costs-panel" aria-label="Proposed trade risk">
    <header className="panel-heading"><h2>Proposed trade risk</h2><span className="muted">Hypothetical · not an order ticket</span></header>
    <p className="caption">Manual entry/stop scenario, not a fill promise or guaranteed maximum loss. Gaps, slippage and unknown stop liquidity can produce larger losses. No order is submitted.</p>
    <details className="evidence" open>
      <summary>Scenario inputs · exact decimal text</summary>
      <form className="cost-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label>Side<select aria-label="Proposal side" value={side} onChange={event => setSide(event.target.value as 'buy' | 'sell')}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
        <label>Entry price<input aria-label="Proposal entry price" type="text" inputMode="decimal" value={entry} onChange={event => setEntry(event.target.value)} maxLength={128} required /></label>
        <label>Stop price<input aria-label="Proposal stop price" type="text" inputMode="decimal" value={stop} onChange={event => setStop(event.target.value)} maxLength={128} required /></label>
        <label>Size convention<select aria-label="Proposal size convention" value={sizeMode} onChange={event => setSizeMode(event.target.value as 'base' | 'risk')}><option value="base">Base quantity</option><option value="risk">Risk budget (native quote)</option></select></label>
        <label>{sizeMode === 'base' ? 'Base quantity' : 'Risk budget (native quote)'}<input aria-label={sizeMode === 'base' ? 'Proposal base quantity' : 'Proposal risk budget'} type="text" inputMode="decimal" value={size} onChange={event => setSize(event.target.value)} maxLength={128} required /></label>
        <label>Fees<select aria-label="Proposal fee assumption" value={feeMode} onChange={event => setFeeMode(event.target.value as 'unknown' | 'assumed')}><option value="unknown">Unknown fees</option><option value="assumed">Explicit user assumption</option></select></label>
        {feeMode === 'assumed' && <>
          <label>Fee bps / leg<input aria-label="Proposal fee basis points" type="text" inputMode="decimal" value={fee} onChange={event => setFee(event.target.value)} maxLength={128} required /></label>
          <label>Assumption date<input aria-label="Proposal fee date" type="date" value={feeDate} onChange={event => setFeeDate(event.target.value)} required /></label>
          <label className="provenance-input">Provenance<input aria-label="Proposal fee provenance" type="text" value={provenance} onChange={event => setProvenance(event.target.value)} maxLength={200} required /></label>
        </>}
        <button className="primary" data-testid="proposal-risk-calculate" disabled={Boolean(unavailable) || pending}>{pending ? 'Calculating…' : 'Calculate hypothetical risk'}</button>
      </form>
      <p className="caption">Prices and risk budget use the selected instrument’s native quote, without FX conversion. All arithmetic, stop direction, lot/minimum checks and conservative size rounding are server-side. Unknown fees never mean zero; they can make net loss and risk-budget sizing unavailable.</p>
    </details>
    {unavailable && <div role="status"><p className="empty">{unavailable}</p>{frame?.health.reasons.length ? <ul className="reasons">{frame.health.reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul> : null}</div>}
    {error && <p role="alert" className="error">{error}</p>}
    <div aria-live="polite" aria-busy={pending}>
      {!data || !snapshot ? <p className="empty">Enter an entry, stop and quantity or risk budget, then calculate. No loss or size is inferred in the browser.</p> : <>
        <div className="result-heading"><span>Hypothetical snapshot · requested at delivery {snapshot.cursor} · calculate again to refresh</span><strong className={data.eligible ? 'positive' : 'warning'}>{data.eligible ? 'Scenario available · non-guaranteed' : 'Scenario unavailable'}</strong></div>
        {snapshot.inputKey !== inputKey && <p className="caption warning">Inputs changed · stale result shown for the previous inputs. Recalculate before using these values.</p>}
        {unavailable && <p className="caption warning">Previous snapshot only · current source unavailable.</p>}
        {pending && <p className="caption">Calculating · previous snapshot remains visible until the response arrives.</p>}
        {error && <p className="caption warning">Latest calculation failed · any values below are from the previous successful response.</p>}
        <p className="caption">Exact instrument: {data.instrument_id}. Unavailable fields are not zero; suggested size is not an executable order or a guaranteed loss bound.</p>
        {data.reasons.length > 0 && <ul className="reasons" aria-label="Proposal risk reasons">{data.reasons.map((reason, index) => <li key={`${index}:${reason}`}>{reason}</li>)}</ul>}
        <div className="estimate-grid">{values.map(([name, value]) => <div key={name} className="scroll"><span>{name}</span><strong className="number">{value ?? 'Unavailable'}</strong></div>)}</div>
        <details className="evidence" open><summary>Scenario assumptions and limitations</summary>{data.assumptions.length ? <ul className="reasons">{data.assumptions.map((assumption, index) => <li key={`${index}:${assumption}`}>{assumption}</li>)}</ul> : <p className="caption">No additional assumptions supplied by the server. The hypothetical, non-guaranteed limitations above still apply.</p>}</details>
        <details className="evidence"><summary>Exact inputs, source identity and server evidence</summary><pre>{JSON.stringify({ input: snapshot.input, session_id: snapshot.session, source_id: snapshot.source, requested_at_delivery: snapshot.cursor, response: data }, null, 2)}</pre></details>
      </>}
    </div>
  </section>;
}
