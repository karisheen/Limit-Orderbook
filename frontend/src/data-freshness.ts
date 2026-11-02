import type { Frame, Reference, VisualGrid } from './api';
import type { ChartMode, ChartRowViewport } from './market-charts-model';
import { CHART_LIMITS, priceCellIndex, priceRowProjector } from './market-charts-model';

export interface SourcePoint { clock_id: string; mono_ns: string }
export interface EvidenceLag {
  ms: number | null;
  reason: string | null;
  clockId: string | null;
  evidenceNs: string | null;
  highwaterNs: string | null;
}

function nativeNs(value: string): bigint | null {
  return /^\d+$/.test(value) ? BigInt(value) : null;
}

function unavailable(reason: string, evidence: SourcePoint | null = null): EvidenceLag {
  return { ms: null, reason, clockId: evidence?.clock_id || null, evidenceNs: evidence?.mono_ns ?? null, highwaterNs: null };
}

/** Only native points carrying an identical, nonempty clock identity may be subtracted. */
export function sourceClockLag(evidence: SourcePoint | null, highwaters: readonly SourcePoint[]): EvidenceLag {
  if (!evidence || !evidence.clock_id) return unavailable('source evidence unavailable', evidence);
  const start = nativeNs(evidence.mono_ns);
  if (start === null) return unavailable('invalid source timestamp', evidence);
  const matching = highwaters.filter(point => point.clock_id === evidence.clock_id);
  if (!matching.length) return unavailable(highwaters.length ? 'source clock mismatch' : 'source highwater unavailable', evidence);
  let end: bigint | null = null;
  for (const point of matching) {
    const value = nativeNs(point.mono_ns);
    if (value === null) return unavailable('invalid source highwater', evidence);
    if (end === null || value > end) end = value;
  }
  if (end === null || end < start) return unavailable('source clock skew / evidence ahead of highwater', evidence);
  const delta = end - start;
  if (delta / 1_000_000n > BigInt(Number.MAX_SAFE_INTEGER)) return unavailable('source lag exceeds numeric range', evidence);
  return { ms: Number(delta / 1_000_000n) + Number(delta % 1_000_000n) / 1_000_000,
    reason: null, clockId: evidence.clock_id, evidenceNs: evidence.mono_ns, highwaterNs: end.toString() };
}

/** Browser monotonic elapsed time only; no backend timestamp enters this calculation. */
export function browserResidencyMs(nowMs: number, receivedAtMs: number | null): number | null {
  if (receivedAtMs === null || !Number.isFinite(nowMs) || !Number.isFinite(receivedAtMs) || receivedAtMs < 0 || nowMs < receivedAtMs) return null;
  return nowMs - receivedAtMs;
}

/** New transport frames or query IDs must not rejuvenate unchanged evidence. */
export function evidenceIdentity(reference: Reference | null): string | null {
  return reference ? JSON.stringify([reference.session_id, reference.clock_id, reference.ingest_ordinal,
    reference.event_index, reference.receive_mono_ns]) : null;
}

function frameHighwaters(frame: Frame, reference?: Reference): SourcePoint[] {
  const result: SourcePoint[] = [];
  const add = (value: Reference | null | undefined) => {
    if (value && reference && value.session_id === reference.session_id) result.push({ clock_id: value.clock_id, mono_ns: value.receive_mono_ns });
  };
  add(frame.book?.asof);
  for (const print of frame.analytics?.tape ?? []) add(print.asof);
  for (const metric of frame.analytics?.metrics ?? []) {
    if (metric.instrument_id !== frame.selection.instrument_id) continue;
    add(metric.asof);
    for (const value of metric.references) add(value);
    const clock = metric.window.clock_id;
    const end = metric.window.end_mono_ns;
    if (typeof clock === 'string' && typeof end === 'string') result.push({ clock_id: clock, mono_ns: end });
  }
  // Processing observations explicitly identify their clock. The frame's publication
  // timestamp does not, and replay recording clocks need not be the server clock.
  if (frame.source.mode === 'live') {
    for (const stream of frame.telemetry.ingestion) {
      if (stream.key.instrument_id !== frame.selection.instrument_id) continue;
      const processing = stream.last_processing;
      if (processing) result.push({ clock_id: processing.clock_id, mono_ns: processing.publish_mono_ns });
    }
  }
  return result;
}

/** Follow the selected source's evaluation frontier, including quiet intervals. */
export function sourceFrontier(frame: Frame | null, clockId?: string): bigint | null {
  if (!frame) return null;
  const references = [
    ...(frame.book?.instrument_id === frame.selection.instrument_id ? [frame.book.asof] : []),
    ...(frame.analytics?.instrument_id === frame.selection.instrument_id ? [
      ...frame.analytics.tape.map(print => print.asof),
      ...frame.analytics.metrics.filter(metric => metric.instrument_id === frame.selection.instrument_id).flatMap(metric => metric.asof ? [metric.asof] : []),
    ] : []),
  ];
  const reference = references.find(value => value.clock_id && (!clockId || value.clock_id === clockId));
  const clock = clockId || reference?.clock_id;
  if (!clock) return null;
  let frontier: bigint | null = null;
  for (const point of frameHighwaters(frame, reference)) {
    if (point.clock_id !== clock) continue;
    const time = nativeNs(point.mono_ns);
    if (time !== null && (frontier === null || time > frontier)) frontier = time;
  }
  return frontier;
}

export function referenceLag(frame: Frame | null, reference: Reference | null, connected: boolean): EvidenceLag {
  if (!connected) return unavailable('disconnected');
  if (!frame || frame.status === 'unavailable' || frame.status === 'error') return unavailable('source unavailable');
  if (!reference) return unavailable('displayed evidence unavailable');
  return sourceClockLag({ clock_id: reference.clock_id, mono_ns: reference.receive_mono_ns }, frameHighwaters(frame, reference));
}

export interface ChartEvidence { reference: Reference | null; reason: string | null }
export interface ChartEvidenceLayers { mode?: ChartMode; rowViewport?: ChartRowViewport }

/** Only the primary painted layer may establish chart freshness; overlays cannot rejuvenate it. */
export function chartEvidence(view: VisualGrid | null, { mode = 'heatmap', rowViewport }: ChartEvidenceLayers = {}): ChartEvidence {
  if (!view) return { reference: null, reason: 'displayed chart unavailable' };
  const start = nativeNs(view.start_ns), end = nativeNs(view.end_ns), now = nativeNs(view.now_ns);
  if (start === null || end === null || now === null || end <= start) return { reference: null, reason: 'invalid chart source bounds' };
  const firstRow = rowViewport?.first ?? 0;
  const rowCount = rowViewport?.count ?? view.rows;
  if (mode === 'footprint' && (!Number.isSafeInteger(view.rows) || view.rows <= 0 ||
    !Number.isSafeInteger(firstRow) || !Number.isSafeInteger(rowCount) ||
    firstRow < 0 || rowCount <= 0 || firstRow > view.rows - rowCount)) {
    return { reference: null, reason: 'invalid displayed footprint row viewport' };
  }
  const rowEnd = firstRow + rowCount;
  let latest: Reference | null = null;
  let latestTime = -1n;
  let reason = 'eligible displayed evidence unavailable';
  const consider = (reference: Reference | null, upper: bigint, inWindow = false) => {
    if (!reference) return;
    if (!view.clock_id || reference.clock_id !== view.clock_id) { reason = 'chart evidence clock mismatch'; return; }
    const stamp = nativeNs(reference.receive_mono_ns);
    if (stamp === null || stamp > now || stamp > upper || stamp > end || (inWindow && stamp < start)) {
      reason = 'chart evidence outside source bounds / skew';
      return;
    }
    if (stamp >= latestTime) { latest = reference; latestTime = stamp; }
  };
  const priceRow = priceRowProjector(view.price_min, view.price_step);
  const candles = new Map<string, { start: bigint; end: bigint }>();
  for (const column of view.columns.slice(-CHART_LIMITS.columns)) {
    const time = nativeNs(column.time_ns), columnEnd = nativeNs(column.end_ns);
    if (time === null || columnEnd === null || columnEnd <= time) return { reference: null, reason: 'invalid column source bounds' };
    if (time >= end || columnEnd <= start || time > now) continue;
    if (column.eligible && mode === 'heatmap') consider(column.asof, columnEnd);
    if (mode === 'cvd' || mode === 'ofi') {
      const metric = column[mode];
      if (metric?.eligibility.eligible && metric.value !== null) {
        consider(metric.asof, columnEnd);
        for (const reference of metric.references) consider(reference, columnEnd);
      }
    }
    if (mode === 'candles' && column.candle?.eligible && priceRow(column.candle.high) >= 0 && priceRow(column.candle.low) <= view.rows) {
      candles.set(`${column.time_ns}:${column.segment_id}`, { start: time, end: columnEnd });
    }
  }
  if (mode !== 'candles' && mode !== 'footprint' && mode !== 'profile') {
    return latest ? { reference: latest, reason: null } : { reference: null, reason };
  }
  const footprintCells = new Set<string>();
  if (mode === 'footprint') {
    for (const cell of view.footprints.slice(-CHART_LIMITS.footprints)) {
      const row = priceCellIndex(view, cell.price);
      if (row >= firstRow && row < rowEnd) footprintCells.add(`${cell.time_ns}:${row}`);
    }
  }
  const profileRows = new Set(mode === 'profile' ? view.profile.slice(0, CHART_LIMITS.rows).map(cell => priceCellIndex(view, cell.price)) : []);
  const bucket = nativeNs(view.bucket_ns);
  for (const trade of view.trades) {
    const time = nativeNs(trade.time_ns);
    if (time === null || time < start || time >= end || time > now) continue;
    const row = priceCellIndex(view, trade.price);
    const visiblePrice = row >= 0;
    let aggregate = false;
    if (!trade.is_subscription_history && trade.base_quantity !== null && bucket !== null && bucket > 0n) {
      const stamp = (time / bucket * bucket).toString();
      const candle = candles.get(`${stamp}:${trade.segment_id}`);
      aggregate = mode === 'profile' ? visiblePrice && profileRows.has(row)
        : mode === 'footprint' ? visiblePrice && row >= firstRow && row < rowEnd && footprintCells.has(`${stamp}:${row}`)
          : mode === 'candles' && !!candle && time >= candle.start && time < candle.end;
    }
    if (aggregate) consider(trade.asof, end, true);
  }
  return latest ? { reference: latest, reason: null } : { reference: null, reason };
}

export function chartLag(frame: Frame | null, view: VisualGrid | null, connected: boolean, layers: ChartEvidenceLayers = {}, displayedEvidence?: ChartEvidence): EvidenceLag {
  if (!connected) return unavailable('disconnected');
  if (!frame || frame.status === 'unavailable' || frame.status === 'error') return unavailable('source unavailable');
  if (!view || view.generation !== frame.generation || view.instrument_id !== frame.selection.instrument_id) return unavailable('chart identity mismatch / unavailable');
  // Frame highwaters can advance without repainting; reuse the painted-layer
  // selection instead of rebuilding bounded aggregate indexes on every frame.
  const evidence = displayedEvidence ?? chartEvidence(view, layers);
  if (!evidence.reference) return unavailable(evidence.reason!);
  const current = frameHighwaters(frame, evidence.reference);
  if (!current.some(point => point.clock_id === view.clock_id)) return unavailable(current.length ? 'current source clock mismatch' : 'current source highwater unavailable');
  return sourceClockLag({ clock_id: evidence.reference.clock_id, mono_ns: evidence.reference.receive_mono_ns }, [
    { clock_id: view.clock_id, mono_ns: view.now_ns }, ...current,
  ]);
}
