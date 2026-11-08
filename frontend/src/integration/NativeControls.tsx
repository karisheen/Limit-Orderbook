import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { label, milliseconds } from '../format';
import { presetLayout } from '../workspace';
import type { WorkspacePreferences } from '../workspace';
import { terminalIncidents } from '../terminal-incident';
import { Details, Empty, Panel, Reasons, Value } from '../WorkflowUI';
import { ReplayControls } from './NativeWidgets';
import { NativeRecording } from './NativeRecording';
import { useNativeTerminal } from './NativeContext';

const instrumentPageSize = 100;
const abbreviateCaptureLabel = (value: string) => value.replace(/\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b/gi, id => `${id.slice(0, 8)}…`);

export function NativeControls() {
  const { terminal, preferences: p, updatePreferences: update, instrument: selected, instruments, connected, usable,
    selectInstrument: selectContextInstrument, selectSource: changeSource, workspace } = useNativeTerminal();
  const { catalog, frame } = terminal;
  const { storageError, slots, saveSlot, loadSlot } = workspace;
  const [query, setQuery] = useState('');
  const [venue, setVenue] = useState('');
  const [quote, setQuote] = useState('');
  const [market, setMarket] = useState('');
  const [instrumentPage, setInstrumentPage] = useState(0);
  const [slot, setSlot] = useState(0);
  const [slotNotice, setSlotNotice] = useState('');
  const controls = useRef<HTMLElement | null>(null);
  const search = useRef<HTMLInputElement | null>(null);
  const watchlist = useRef<HTMLDetailsElement | null>(null);
  const diagnostics = useRef<HTMLDetailsElement | null>(null);
  const sourceInspector = useRef<HTMLDetailsElement | null>(null);
  const sourceId = frame?.source.id;
  const choices = useMemo(() => ({
    venues: [...new Set(instruments.map(item => item.venue))].sort(),
    quotes: [...new Map(instruments.map(item => [item.quote.asset_id, item.quote])).values()],
    markets: [...new Set(instruments.map(item => item.market_type))],
    cohorts: [...new Map(instruments.map(item => [JSON.stringify([item.quote.asset_id, item.market_type]), item])).entries()],
  }), [instruments]);
  const filtered = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return instruments.filter(item => (!venue || item.venue === venue) && (!quote || item.quote.asset_id === quote) && (!market || item.market_type === market) && terms.every(term => [item.instrument_id, item.native_symbol, item.venue, item.base.ticker, item.quote.ticker, item.market_type].join(' ').toLowerCase().includes(term)));
  }, [instruments, venue, quote, market, query]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / instrumentPageSize));
  const page = Math.min(instrumentPage, pageCount - 1);
  const pageInstruments = filtered.slice(page * instrumentPageSize, (page + 1) * instrumentPageSize);
  useEffect(() => { setInstrumentPage(0); }, [query, venue, quote, market, sourceId]);
  const closeDetails = useCallback((details: HTMLDetailsElement, restoreFocus = false) => {
    details.open = false;
    if (details === watchlist.current) update({ leftOpen: false });
    if (restoreFocus) details.querySelector<HTMLElement>(':scope > summary')?.focus({ preventScroll: true });
  }, [update]);
  useEffect(() => {
    const root = controls.current;
    if (!root) return;
    // Only floating disclosures dismiss on departure. Inline diagnostics and
    // nested workspace/metadata disclosures retain their state.
    const popovers = '.native-controls-toolbar > details[open], .source-inspector[open]';
    const dismissOutside = (event: PointerEvent | FocusEvent) => {
      if (!(event.target instanceof Node)) return;
      for (const details of root.querySelectorAll<HTMLDetailsElement>(popovers)) {
        if (!details.contains(event.target)) closeDetails(details, event.type === 'pointerdown' && details.contains(document.activeElement));
      }
    };
    const exclusive = (event: Event) => {
      const opened = event.target;
      if (!(opened instanceof HTMLDetailsElement) || !opened.matches(popovers)) return;
      for (const details of root.querySelectorAll<HTMLDetailsElement>(popovers)) {
        if (details !== opened) closeDetails(details);
      }
    };
    document.addEventListener('pointerdown', dismissOutside, true);
    document.addEventListener('focusin', dismissOutside);
    root.addEventListener('toggle', exclusive, true);
    return () => {
      document.removeEventListener('pointerdown', dismissOutside, true);
      document.removeEventListener('focusin', dismissOutside);
      root.removeEventListener('toggle', exclusive, true);
    };
  }, [closeDetails]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
      const target = event.target;
      if (event.key === 'Escape') {
        const disclosure = target instanceof Element ? target.closest<HTMLDetailsElement>('details[open]') : null;
        if (disclosure) { event.preventDefault(); closeDetails(disclosure, true); }
        return;
      }
      if (target instanceof Element && target.closest('input,select,textarea,button,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
      if (event.key === '/') {
        event.preventDefault(); update({ leftOpen: true });
        if (watchlist.current) watchlist.current.open = true;
        search.current?.focus();
      }
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [update, closeDetails]);
  const liveSource = catalog?.sources.find(source => source.mode === 'live' && source.status === 'ready');
  const qualityReasons = [...new Set([...(frame?.health.reasons ?? []), ...(frame?.source.reasons ?? []), ...(frame?.book?.quality.reason_codes ?? [])])];
  const incidents = terminalIncidents({ terminalError: terminal.error, frameError: frame?.error ?? null,
    catalogError: terminal.catalogError, storageError, connection: terminal.connection, status: frame?.status ?? null,
    ready: frame?.health.ready ?? false, qualityReasons, synthetic: frame?.source.synthetic ?? false });
  const incident = incidents[0];
  const incidentHint = incident?.recovery === 'wait'
    ? incident.id === 'catalog' ? 'Retrying automatically · current session retained' : 'Reconstruction in progress'
    : incident?.recovery === 'storage' ? 'Settings remain in memory; allow browser storage to persist.' : '';
  const openDetails = (details: HTMLDetailsElement | null) => {
    if (!details) return;
    details.open = true;
    details.querySelector<HTMLElement>(':scope > summary')?.focus();
  };
  return <section ref={controls} className="native-controls native-surface" aria-label="Native session controls">
    <div className="native-controls-toolbar">
      <details className="workspace-configuration"><summary>Source / workspace</summary><div className="workspace-configuration-body">
      <label className="source-control">Source<select aria-label="Data source" title={frame?.source.label} value={frame?.source.id ?? p.source?.id ?? ''} disabled={!catalog} onChange={event => { const source = catalog?.sources.find(item => item.id === event.target.value); if (source?.status === 'ready') changeSource(source); }}>{!frame && <option value="">Choose source…</option>}{catalog?.sources.map(source => <option key={source.id} value={source.id} disabled={source.status !== 'ready'} title={[source.label, ...source.reasons].join('; ')}>{abbreviateCaptureLabel(source.label)} · {source.mode} · {source.status}{source.synthetic ? ' · SYNTHETIC' : ''}{source.reasons.length ? ` · ${source.reasons.join('; ')}` : ''}</option>)}</select></label>
      <label>Preset<select aria-label="Workspace preset" value={p.preset} onChange={event => { update(presetLayout(event.target.value as WorkspacePreferences['preset'])); }}><option value="balanced">Balanced</option><option value="analysis">Analysis</option><option value="ladder">DOM focus</option></select></label>
      <details className="workspace-slots"><summary>Workspaces</summary><div className="workspace-slot-controls"><label>Slot<select aria-label="Workspace slot" value={slot} onChange={event => { setSlot(Number(event.target.value)); setSlotNotice(''); }}>{slots.map((saved, index) => <option key={index} value={index}>Slot {index + 1}{saved ? ' · saved' : ' · empty'}</option>)}</select></label><button onClick={() => { saveSlot(slot); setSlotNotice(`Slot ${slot + 1} captured`); }}>Save workspace</button><button disabled={!slots[slot]} onClick={() => { loadSlot(slot); window.dispatchEvent(new Event('terminal-clear-cursor')); setSlotNotice(`Slot ${slot + 1} loaded`); }}>Load workspace</button><span role="status">{slotNotice}</span></div></details>
      </div></details>
      <span className="status muted" title="Transport connection only; source readiness and pane freshness are separate.">Transport: {terminal.connection}</span><button onClick={terminal.reconnect} aria-label="Reconnect terminal">Reconnect</button>
      <NativeRecording />
      <details ref={watchlist} open={p.leftOpen} onToggle={event => { if (event.currentTarget.open !== p.leftOpen) update({ leftOpen: event.currentTarget.open }); }}><summary>Watchlist</summary><div className="native-control-body">
        <label>Search instruments<input ref={search} aria-label="Search instruments" aria-controls="instrument-results" placeholder="Symbol, venue, quote…" type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <div id="instrument-results" className="native-watchlist"><Panel title="Instruments" extra={<span className="muted">{filtered.length}</span>}>
        <div className="instrument-filters">
          <select aria-label="Filter venue" value={venue} onChange={event => setVenue(event.target.value)}><option value="">All venues</option>{choices.venues.map(value => <option key={value}>{value}</option>)}</select>
          <select aria-label="Filter quote" value={quote} onChange={event => setQuote(event.target.value)}><option value="">All exact quote assets</option>{choices.quotes.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.ticker} · {asset.asset_id}</option>)}</select>
          <select aria-label="Filter market" value={market} onChange={event => setMarket(event.target.value)}><option value="">All markets</option>{choices.markets.map(value => <option key={value}>{value}</option>)}</select>
      <label className="cohort-control">Cohort<select aria-label="Native cohort" value={quote && market ? JSON.stringify([quote, market]) : quote || market ? 'custom' : ''} onChange={event => { const [nextQuote, nextMarket] = event.target.value ? JSON.parse(event.target.value) as [string, string] : ['', '']; setQuote(nextQuote); setMarket(nextMarket); update({ leftOpen: true }); }}><option value="">All native cohorts</option>{(quote || market) && !(quote && market) && <option value="custom" disabled>Custom filters</option>}{choices.cohorts.map(([value, item]) => <option key={value} value={value}>{item.quote.ticker} · {item.market_type} · {item.quote.asset_id}</option>)}</select></label>
        </div>
        <div className="instrument-list scroll" key={JSON.stringify([sourceId, query, venue, quote, market, page])}>{!filtered.length ? <Empty>{catalog ? 'No exact instruments match these filters.' : 'Loading instrument catalog…'}</Empty> : pageInstruments.map(item => <button className={`instrument ${item.instrument_id === selected?.instrument_id ? 'selected' : ''}`} key={item.instrument_id} title={item.instrument_id} data-instrument-id={item.instrument_id} aria-pressed={item.instrument_id === selected?.instrument_id} disabled={terminal.busy || !usable} onClick={() => { if (watchlist.current) closeDetails(watchlist.current, true); void selectContextInstrument(item.instrument_id); }}><span><strong>{item.native_symbol}</strong><small>{item.venue}</small></span><span><small>{item.market_type} · {item.quote.ticker}</small><small>{item.status} / {item.data_access}</small></span></button>)}</div>
        {pageCount > 1 && <nav className="panel-toolbar" aria-label="Instrument pages"><button aria-label="Previous instrument page" disabled={page === 0} onClick={() => setInstrumentPage(page - 1)}>Prev</button><span>{page + 1} / {pageCount} · {instrumentPageSize}/page</span><button aria-label="Next instrument page" disabled={page + 1 >= pageCount} onClick={() => setInstrumentPage(page + 1)}>Next</button></nav>}
      </Panel></div>
      </div></details>
    </div>
    <div className="source-banner"><strong>{frame?.source.synthetic ? 'SYNTHETIC DEMO' : frame?.source.recording ? 'RECORDED' : frame?.source.mode.toUpperCase() ?? 'CONNECTING'}</strong><span className={`source-readiness ${frame?.health.ready && usable ? 'positive' : 'warning'}`} title={qualityReasons.join('; ')}>{frame?.health.ready && usable ? frame.book && !frame.book.quality.coverage.full_depth ? 'Ready · partial depth' : 'Ready' : usable ? 'Partial / not ready' : 'Unavailable'}</span><span className="source-state">{label(frame?.status ?? 'loading')}</span>
      {incident && <div className={`terminal-incident ${incident.severity}`} role={incident.severity === 'error' ? 'alert' : 'status'} data-testid="terminal-incident" data-incident={incident.id}>
        <span className="incident-message" title={incidentHint ? `${incident.message} · ${incidentHint}` : incident.message}><span>{incident.message}</span>{incidentHint && <span className="muted"> · {incidentHint}</span>}</span>
        {incident.recovery === 'reconnect' && <button onClick={terminal.reconnect}>Reconnect</button>}
        {incident.recovery === 'source' && <button onClick={() => openDetails(sourceInspector.current)}>Inspect source</button>}
        {incident.recovery === 'synthetic' && <>
          <button aria-label="Play synthetic demo" title="Play synthetic demo" disabled={terminal.busy || !connected || frame?.status !== 'paused'} onClick={() => void terminal.replay('play')}>Play demo</button>
          {liveSource ? <button aria-label="Switch to public live data" title="Switch to public live data" disabled={terminal.busy} onClick={() => changeSource(liveSource)}>Public live data</button>
            : <button aria-label="Inspect live availability" title="Inspect live availability" onClick={() => openDetails(sourceInspector.current)}>Live availability</button>}
        </>}
        <button onClick={() => openDetails(diagnostics.current)}>Diagnostics{incidents.length > 1 ? ` (${incidents.length})` : ''}</button>
      </div>}
      {!incident && <span className="source-label" title={frame?.source.label}>{abbreviateCaptureLabel(frame?.source.label ?? 'Opening independent session')}</span>}
      <details ref={sourceInspector} className="source-inspector"><summary>Source &amp; instrument details</summary><div className="source-inspector-body">
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
        <Details title="All terminal incidents and exact causes" value={{ incidents, qualityReasons }} />
      </div></details>
    </div>
    {frame?.replay && <ReplayControls frame={frame} busy={terminal.busy || !connected} replay={terminal.replay} />}
    <details ref={diagnostics} className="diagnostics"><summary>Diagnostics</summary>
      <Details title="All terminal incidents and exact causes" value={{ incidents, qualityReasons }} />
      <Reasons reasons={qualityReasons} />
      <p className="caption">Transport connection and browser rendering timings do not establish market-data freshness. Analytical queue overflow requires investigation of native ingestion; reconnecting does not fix capacity.</p>
      <footer className="telemetry"><span>Session {frame?.session_id.slice(0, 8) ?? '—'} · generation {frame?.generation ?? '—'}</span><span>Queue {frame?.telemetry.queue_depth ?? '—'} / high {frame?.telemetry.queue_highwater ?? '—'}</span><span>Coalesced {frame?.telemetry.coalesced ?? '—'} · overflow {frame?.telemetry.overflows ?? '—'}</span><span title="ACKs evicted from the bounded server buffer">ACK expired {frame?.telemetry.ack_expired ?? '—'}</span><span>Observer {milliseconds(frame?.telemetry.observer_duration_ms)}</span><span title="Two paint opportunities; not GPU completion">Receive→paint {milliseconds(frame?.telemetry.browser_receive_to_paint_ms)}</span><span title="Conservative round-trip upper bound">Publish→ACK ≤ {milliseconds(frame?.telemetry.publish_to_ack_upper_bound_ms)}</span></footer><Details title={`Live ingestion evidence · ${frame?.telemetry.ingestion.length ?? 0} streams`} value={frame?.telemetry.ingestion ?? []} /></details>
  </section>;
}
