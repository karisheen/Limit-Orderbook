import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { GroupedLadder, GroupedLevel } from './api';
import { compact, label, timestamp } from './format';
import { Details, Empty, Panel, Reasons, Value } from './WorkflowUI';
import { decimalPadding, decimalPlaces, depthChanges, depthFraction, depthKey, depthMaximum, depthQuantity } from './depth-ladder-model';
import type { DepthChange } from './depth-ladder-model';

export interface DepthLadderProps {
  data: GroupedLadder | null;
  loading: boolean;
  error: string | null;
  frozen: boolean;
  viewPaused: boolean;
  rows: number;
  onRows: (rows: number) => void;
  onFreeze: () => void;
  crosshair: string | null;
  onPrice: (price: string | null) => void;
  priceStep: string;
  onPriceStep: (step: string) => void;
  unit: 'base' | 'quote';
  onUnit: (unit: 'base' | 'quote') => void;
  cumulative: boolean;
  onCumulative: (cumulative: boolean) => void;
  sourceMode: 'demo' | 'replay' | 'live';
  extra?: ReactNode;
}

function AlignedValue({ value, places }: { value: string; places: number }) {
  return <span className="dom-aligned"><Value value={value} /><span className="dom-decimal-padding" aria-hidden="true">{decimalPadding(value, places)}</span></span>;
}

export function DepthLadder({ data, loading, error, frozen, viewPaused, rows, onRows, onFreeze, crosshair, onPrice, priceStep, onPriceStep, unit, onUnit, cumulative, onCumulative, sourceMode, extra }: DepthLadderProps) {
  const scroll = useRef<HTMLDivElement>(null);
  const middle = useRef<HTMLTableRowElement>(null);
  const table = useRef<HTMLTableElement>(null);
  const [following, setFollowing] = useState(true);
  const follow = useRef(true);
  const expectedScroll = useRef<number | null>(null);
  const pendingCenter = useRef(true);
  const [scaleRevision, setScaleRevision] = useState(0);
  const held = frozen || viewPaused;
  const identity = `${data?.generation ?? ''}|${data?.instrument_id ?? ''}|${sourceMode}|${data?.source_mode ?? ''}|${data?.step ?? ''}|${priceStep}|${unit}|${rows}`;
  const ordered = useMemo(() => {
    const bids = data?.rows.filter(row => row.side === 'bid') ?? [];
    const asks = data?.rows.filter(row => row.side === 'ask').reverse() ?? [];
    return { bids, asks, all: [...asks, ...bids] };
  }, [data]);
  const quantityUnit = unit === 'base' ? data?.unit : data?.quote_unit;
  const diagnostic = !!error || !data?.eligible;
  const summary = diagnostic ? null : data?.summary;
  const bothSides = ordered.bids.length > 0 && ordered.asks.length > 0;
  const bestIdentity = summary ? `${summary.best_bid}|${summary.best_ask}` : null;
  const scale = useRef<{ key: string; denominator: string | null }>({ key: '', denominator: null });
  const scaleKey = `${identity}|${scaleRevision}`;
  if (scale.current.key !== scaleKey) scale.current = { key: scaleKey, denominator: null };
  if (scale.current.denominator === null && !diagnostic && ordered.all.length) scale.current.denominator = depthMaximum(ordered.all.map(row => depthQuantity(row, unit)));
  const denominator = scale.current.denominator;
  const precision = useMemo(() => ({
    price: Math.max(0, ...ordered.all.map(row => decimalPlaces(row.price))),
    quantity: Math.max(0, ...ordered.all.map(row => decimalPlaces(depthQuantity(row, unit)))),
    cumulative: Math.max(0, ...ordered.all.map(row => decimalPlaces(unit === 'base' ? row.cumulative_base : row.cumulative_quote))),
  }), [ordered, unit]);

  const suspendFollow = useCallback(() => {
    follow.current = false;
    expectedScroll.current = null;
    setFollowing(false);
  }, []);
  const center = useCallback(() => {
    const container = scroll.current, boundary = middle.current;
    if (!container || !boundary || !follow.current) return;
    const rect = boundary.getBoundingClientRect();
    const target = Math.max(0, Math.min(container.scrollHeight - container.clientHeight,
      container.scrollTop + rect.top - container.getBoundingClientRect().top - container.clientHeight / 2 + rect.height / 2));
    // A layout change can clamp scrollTop before this effect runs. Claim that
    // resulting scroll event too, rather than mistaking it for manual input.
    expectedScroll.current = target;
    if (Math.abs(container.scrollTop - target) > 0.5) container.scrollTop = target;
    expectedScroll.current = container.scrollTop;
    pendingCenter.current = container.scrollHeight <= container.clientHeight;
  }, []);
  const resumeFollow = useCallback(() => {
    follow.current = true;
    setFollowing(true);
    pendingCenter.current = true;
    center();
  }, [center]);
  const centered = useRef<{ identity: string; best: string | null }>({ identity: '', best: null });
  useLayoutEffect(() => {
    const previous = centered.current;
    const changed = previous.identity !== identity || previous.best !== bestIdentity;
    centered.current = { identity, best: bestIdentity };
    if (!bothSides) {
      pendingCenter.current = true;
      if (follow.current) expectedScroll.current = scroll.current?.scrollTop ?? null;
    }
    if (bothSides && follow.current && (changed || pendingCenter.current)) center();
  }, [identity, bestIdentity, bothSides, data, center]);
  useEffect(() => {
    const container = scroll.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      // Layout changes follow the quote boundary only while follow is enabled.
      // Manual positioning survives both pane resizing and new quantities.
      if (follow.current && bothSides) center();
    });
    observer.observe(container);
    if (table.current) observer.observe(table.current);
    return () => observer.disconnect();
  }, [bothSides, center]);

  const previous = useRef<{ identity: string; rows: GroupedLevel[]; active: boolean } | null>(null);
  const flashes = useRef(new Map<string, { change: DepthChange; until: number }>());
  const [highlights, setHighlights] = useState(new Map<string, DepthChange>());
  useEffect(() => {
    const active = !held && !error && !!data?.eligible;
    const prior = previous.current;
    const now = performance.now();
    if (!active || !prior?.active || prior.identity !== identity) flashes.current.clear();
    else {
      const keys = new Set(ordered.all.map(depthKey));
      for (const [key, flash] of flashes.current) if (!keys.has(key) || flash.until <= now) flashes.current.delete(key);
      for (const [key, change] of depthChanges(prior.rows, ordered.all, unit)) flashes.current.set(key, { change, until: now + 650 });
    }
    previous.current = { identity, rows: ordered.all, active };
    let timer: number | undefined;
    const publish = () => {
      const time = performance.now();
      for (const [key, flash] of flashes.current) if (flash.until <= time) flashes.current.delete(key);
      setHighlights(new Map([...flashes.current].map(([key, flash]) => [key, flash.change])));
      if (flashes.current.size) timer = window.setTimeout(publish, Math.max(1, Math.min(...[...flashes.current.values()].map(flash => flash.until)) - time));
    };
    publish();
    return () => { window.clearTimeout(timer); };
  }, [data, ordered, identity, unit, held, error]);

  const renderRow = (row: GroupedLevel) => {
    const key = depthKey(row);
    const quantity = depthQuantity(row, unit);
    const width = diagnostic || denominator === null ? null : depthFraction(quantity, denominator);
    const best = !!summary && (row.side === 'bid' ? row === ordered.bids[0] : row === ordered.asks[ordered.asks.length - 1]);
    const nativeBest = row.side === 'bid' ? summary?.best_bid : summary?.best_ask;
    const change = !held && !diagnostic && previous.current?.identity === identity ? highlights.get(key) : undefined;
    return <tr key={key} data-side={row.side} data-price={row.price} tabIndex={0}
      className={`${row.side}${best ? ' dom-best' : ''}${crosshair === row.price ? ' crosshair-row' : ''}${change ? ` dom-${change}` : ''}`}
      onPointerEnter={() => onPrice(row.price)} onPointerLeave={() => onPrice(null)} onFocus={() => onPrice(row.price)} onBlur={() => onPrice(null)}>
      <td title={best ? `Bucket containing native best ${row.side}: ${nativeBest}` : undefined}>{row.side}{best && <span className="dom-best-marker" aria-label={`native best ${row.side} bucket`}> •</span>}</td>
      <td><AlignedValue value={row.price} places={precision.price} /></td>
      <td className="dom-quantity" style={{ '--depth-width': `${(width ?? 0) * 100}%` } as CSSProperties}>
        {width !== null && <span className="dom-depth-bar" aria-hidden="true" />}
        <AlignedValue value={quantity} places={precision.quantity} />
      </td>
      {cumulative && <td className="dom-cumulative"><AlignedValue value={unit === 'base' ? row.cumulative_base : row.cumulative_quote} places={precision.cumulative} /></td>}
    </tr>;
  };
  const stepOptions = data?.step_options ?? [];
  return <Panel title="Order book" className="ladder-panel depth-ladder" extra={<div className="dom-header-actions"><div className="tabs"><button aria-label={frozen ? 'Unfreeze DOM' : 'Freeze DOM'} aria-pressed={frozen} onClick={onFreeze} data-testid="dom-freeze">{frozen ? 'Unfreeze' : 'Freeze'}</button><button aria-label="Recenter DOM" onClick={resumeFollow}>Recenter</button></div>{extra}</div>}>
    <div className="dom-status" aria-busy={loading}>
      <span className={diagnostic ? 'warning' : undefined}>{sourceMode.toUpperCase()} · {error ? 'Stale snapshot' : diagnostic ? 'Unavailable' : 'Eligible'}{held ? ' · Frozen' : ''}</span>
      <span>{data?.coverage?.full_depth ? 'Full-depth feed' : 'Partial / bounded depth'} · {quantityUnit ?? 'unit unavailable'}</span>
    </div>
    {held && <p className="dom-notice warning">Frozen DOM snapshot{viewPaused ? ' · global view paused' : ''}</p>}
    {error && <p className="dom-notice error" role="alert">DOM update unavailable · displaying retained snapshot: {error}</p>}
    {!!data?.reasons.length && <p className="dom-notice warning">{data.reasons.map(label).join(' · ')}</p>}
    <details className="dom-settings">
      <summary>Settings <span>{following ? 'Following midpoint' : 'Manual position'}</span></summary>
      <div className="dom-controls">
        <label>Group<select aria-label="DOM price grouping" value={priceStep} onChange={event => onPriceStep(event.target.value)}><option value="">{data?.tick_size ? `Native tick · ${data.tick_size}` : 'Source quantum · tick unknown'}</option>{priceStep && !stepOptions.includes(priceStep) && <option value={priceStep}>{priceStep}</option>}{stepOptions.map(step => <option key={step} value={step}>{step}</option>)}</select></label>
        <label>Rows<select aria-label="Ladder rows" value={rows} onChange={event => onRows(Number(event.target.value))}>{![10, 15, 25, 50, 100].includes(rows) && <option>{rows}</option>}{[10, 15, 25, 50, 100].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Size<select aria-label="DOM quantity units" value={unit} onChange={event => onUnit(event.target.value as 'base' | 'quote')}><option value="base">Base</option><option value="quote">Quote</option></select></label>
        <label><input type="checkbox" aria-label="Cumulative depth" checked={cumulative} onChange={event => onCumulative(event.target.checked)} />Cumulative</label>
        <label><input type="checkbox" aria-label="Follow midpoint" checked={following} onChange={event => event.target.checked ? resumeFollow() : suspendFollow()} />Follow midpoint</label>
      </div>
      <div className="dom-scale"><span>{diagnostic ? 'Bar scale unavailable' : <>Bar cap: <Value value={denominator} /> {quantityUnit ?? ''}</>}</span><button disabled={diagnostic || !ordered.all.length} onClick={() => setScaleRevision(value => value + 1)}>Rescale depth</button></div>
    </details>
    <div ref={scroll} className="scroll ladder-scroll" tabIndex={0} role="region" aria-label="Order book depth"
      onWheel={suspendFollow} onTouchStart={suspendFollow}
      onPointerDown={event => { if (event.target === event.currentTarget) suspendFollow(); }}
      onKeyDown={event => { if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) suspendFollow(); }}
      onScroll={event => {
        const target = expectedScroll.current;
        expectedScroll.current = null;
        if (target !== null && Math.abs(event.currentTarget.scrollTop - target) <= 1) return;
        suspendFollow();
      }}>
      {!ordered.all.length ? <Empty>No eligible grouped depth. Unknown coverage is not zero.</Empty> : <table ref={table} className={`ladder${diagnostic ? ' dom-diagnostic' : ''}`} data-testid="grouped-ladder"><thead><tr><th scope="col">Side</th><th scope="col" title={data?.quote_unit}>Price {data?.quote_unit}</th><th scope="col">Quantity</th>{cumulative && <th scope="col">Cumulative</th>}</tr></thead><tbody>
        {ordered.asks.map(renderRow)}
        <tr ref={middle} className="dom-spread"><td colSpan={cumulative ? 4 : 3}>{summary ? <><span>Mid <Value value={summary.midpoint} /></span><span>Spread <Value value={summary.spread} /> {data?.quote_unit} · <Value value={summary.spread_ticks} /> ticks · <span className="number" title={summary.spread_bps}>{compact(summary.spread_bps, 10)}</span> bps</span></> : <span>Mid / spread unavailable · eligible two-sided evidence required</span>}</td></tr>
        {ordered.bids.map(renderRow)}
      </tbody></table>}
    </div>
    <details className="dom-scope"><summary>Scope & provenance</summary>
      <p className="caption">Aggregated L2 resting liquidity, not individual orders or executions. DOM grouping is independent of chart grouping. Displayed row limits do not change native retained depth.</p>
      <p className="caption">{data?.coverage?.full_depth ? 'Full-depth feed' : 'Partial / unknown outside scope'} · displayed {ordered.bids.length} bid / {ordered.asks.length} ask rows. Native mode: {data?.source_mode ?? 'unknown'} · subscribed depth: {data?.coverage?.subscribed_depth ?? 'unknown'} · retained {data?.coverage?.retained_bid_levels ?? '—'} bid / {data?.coverage?.retained_ask_levels ?? '—'} ask levels. Group step <Value value={data?.step} /> · quantity {quantityUnit ?? 'unknown'}.</p>
      <p className="caption">Bars share a held scale; values above the cap saturate. Rescale explicitly to the current displayed maximum. Brief left/right outlines show observed size increases/decreases at the same side and price, not trades or cancellations. Missing or newly visible rows imply no change. Native best buckets are bold; their exact native prices are in evidence.</p>
      <Reasons reasons={data?.reasons ?? []} />
      <Details title={`${diagnostic ? 'Unavailable / diagnostic' : 'Eligible grouped evidence'} · as of ${timestamp(data?.asof?.receive_wall_ns)}`} value={data} />
    </details>
  </Panel>;
}
