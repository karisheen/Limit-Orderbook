import type { Frame } from './api';

export interface PriceCandle {
  start_ns: string; end_ns: string;
  open: string; high: string; low: string; close: string; volume: string;
  closed: boolean;
}
export type CandleSource = 'session_trades' | 'venue_ohlcv';
export interface CandleRetention {
  max_trades: number; max_bytes: number; retained_trades: number; retained_bytes: number; truncated: boolean;
}
export interface CandleHistoryMetadata {
  requested_before_ns: string | null;
  next_before_ns: string | null;
  state: 'more' | 'exhausted' | 'limited' | 'unavailable' | 'unsupported';
  pagination: 'time' | 'recent_only';
  max_bars: number;
  update_mode: 'poll';
  poll_interval_ms: number;
}
export interface CandleHistory {
  version: '1'; session_id: string; generation: string; source_identity: string; instrument_id: string;
  instrument_ids: string[]; scope: 'primary' | 'aggregate'; venue_label: string; quote_asset_ids: string[];
  interval: string; status: 'ready' | 'empty' | 'unavailable';
  source: CandleSource; venue: string; partial: boolean;
  time_basis: 'exchange_utc' | 'receive_utc'; fetched_at_ns: string;
  supported_intervals: string[]; candles: PriceCandle[]; reasons: string[];
  volume_unit: string; coverage_start_ns: string | null; coverage_end_ns: string | null;
  // Required on venue responses; observed history remains causally session-fenced.
  series_key?: string;
  history?: CandleHistoryMetadata;
  // These fields are supplied only by the admitted session-trade producer.
  missing_instrument_ids?: string[]; bucket_alignment?: 'utc_calendar_month' | 'utc_epoch_floor';
  highwater?: string; retention?: CandleRetention;
}
export const QUICK_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const;
export const INTERVAL_GROUPS = [
  { label: 'Minutes', values: ['1m', '3m', '5m', '15m', '30m'] },
  { label: 'Hours', values: ['1h', '2h', '4h', '6h', '8h', '12h'] },
  { label: 'Days & longer', values: ['1d', '3d', '1w', '1M'] },
] as const;
export function candleInstrumentIds(primary: string, instrumentIds: readonly string[]): string[] {
  return [...instrumentIds].sort((a, b) => a === b ? 0 : a === primary ? -1 : b === primary ? 1 : a < b ? -1 : 1);
}
export function candleIdentity(frame: Frame | null, interval: string, instrumentIds: readonly string[], source: CandleSource, cohortReady: boolean): string {
  return frame ? JSON.stringify([frame.session_id, frame.generation, frame.source.id, frame.source.mode,
    frame.selection.instrument_id, interval, source,
    source === 'venue_ohlcv' ? [frame.selection.instrument_id] : candleInstrumentIds(frame.selection.instrument_id ?? '', instrumentIds),
    source === 'venue_ohlcv' ? true : cohortReady]) : '';
}
export function candleSourceBlock(source: CandleSource, primary: string, instrumentIds: readonly string[], cohortReady: boolean): string | null {
  if (source === 'venue_ohlcv') return primary ? null : 'Select an exact native primary instrument.';
  if (!cohortReady) return 'Waiting for the selected native trade cohort.';
  if (!primary || !instrumentIds.includes(primary) || instrumentIds.length > 16
    || new Set(instrumentIds).size !== instrumentIds.length) return 'The selected native cohort does not match the active primary.';
  return null;
}
export type CandleRequestIdentity = Pick<CandleHistory, 'session_id' | 'generation' | 'instrument_id' | 'interval' | 'source' | 'instrument_ids'>;
export function matchesCandleHistory(data: CandleHistory, expected: CandleRequestIdentity, sourceIdentity: string | null = null): boolean {
  return data.version === '1' && data.session_id === expected.session_id && data.generation === expected.generation
    && data.instrument_id === expected.instrument_id && data.interval === expected.interval && data.source === expected.source
    && typeof data.source_identity === 'string' && data.source_identity.length > 0
    && (sourceIdentity === null || data.source_identity === sourceIdentity)
    && Array.isArray(data.instrument_ids) && data.instrument_ids.length === expected.instrument_ids.length
    && data.instrument_ids.every((id, index) => id === expected.instrument_ids[index])
    && data.scope === (expected.instrument_ids.length > 1 ? 'aggregate' : 'primary')
    && (data.source !== 'venue_ohlcv' || expected.instrument_ids.length === 1);
}
// A gap is deliberately not mapped to its nearest bar: absent history stays absent.
export function candleAtTime(candles: readonly Pick<PriceCandle, 'start_ns' | 'end_ns'>[], time: bigint): number {
  let lo = 0; let hi = candles.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1; const candle = candles[mid]!;
    if (time < BigInt(candle.start_ns)) hi = mid - 1;
    else if (time >= BigInt(candle.end_ns)) lo = mid + 1;
    else return mid;
  }
  return -1;
}

interface Decimal { value: bigint; places: number }
function decimal(text: string): Decimal {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) throw new Error('Invalid candle decimal');
  const exponent = Number(match[4] ?? 0);
  const places = (match[3]?.length ?? 0) - exponent;
  if (!Number.isSafeInteger(places) || Math.abs(places) > 1000) throw new Error('Candle decimal outside display range');
  return { value: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${match[3] ?? ''}`), places };
}
function atPlaces(value: Decimal, places: number): bigint { return value.value * 10n ** BigInt(places - value.places); }
function decimalText(value: bigint, places: number): string {
  const sign = value < 0n ? '-' : ''; const digits = (value < 0n ? -value : value).toString();
  if (places <= 0) return sign + digits + '0'.repeat(-places);
  const padded = digits.padStart(places + 1, '0');
  return `${sign}${padded.slice(0, -places)}.${padded.slice(-places)}`.replace(/\.?0+$/, '');
}
// Subtract the exact decimal origin before converting a display distance to number.
// This keeps adjacent prices distinct even above Number.MAX_SAFE_INTEGER.
export function candlePriceScale(candles: readonly Pick<PriceCandle, 'open' | 'high' | 'low' | 'close'>[]) {
  const values = candles.flatMap(c => [decimal(c.low), decimal(c.high), decimal(c.open), decimal(c.close)]);
  const places = Math.max(0, ...values.map(v => v.places));
  const ints = values.map(v => atPlaces(v, places));
  let low = ints[0] ?? 0n; let high = ints[0] ?? 1n;
  for (const value of ints) { if (value < low) low = value; if (value > high) high = value; }
  const difference = high - low;
  const padding = difference > 10n ? difference / 12n : 1n;
  low -= padding; high += padding;
  const span = high - low;
  const axisPlaces = places + (span < 10n ? 1 : 0);
  const axisMultiplier = span < 10n ? 10n : 1n;
  const axisLow = low * axisMultiplier;
  const axisSpan = span * axisMultiplier;
  return {
    fraction: (text: string) => {
      const parsed = decimal(text);
      const extra = Math.max(0, parsed.places - places);
      const multiplier = 10n ** BigInt(extra);
      return Number((atPlaces(parsed, places + extra) - low * multiplier) * 1_000_000_000n / (span * multiplier)) / 1_000_000_000;
    },
    label: (fraction: number) => decimalText(axisLow + axisSpan * BigInt(Math.round(fraction * 1_000_000_000)) / 1_000_000_000n, axisPlaces),
  };
}
export function validateCandles(data: CandleHistory): void {
  if (!['ready', 'empty', 'unavailable'].includes(data.status) || !['venue_ohlcv', 'session_trades'].includes(data.source)
    || !['exchange_utc', 'receive_utc'].includes(data.time_basis) || !Array.isArray(data.candles)
    || !Array.isArray(data.supported_intervals) || !Array.isArray(data.reasons)
    || !data.supported_intervals.every(value => typeof value === 'string') || !data.reasons.every(value => typeof value === 'string')
    || !/^\d+$/.test(data.fetched_at_ns)
    || [data.coverage_start_ns, data.coverage_end_ns].some(value => value !== null && !/^\d+$/.test(value))) throw new Error('Invalid candle history response');
  if (typeof data.source_identity !== 'string' || !data.source_identity
    || !Array.isArray(data.instrument_ids) || !data.instrument_ids.length || data.instrument_ids.length > 16
    || !data.instrument_ids.every(id => typeof id === 'string' && !!id)
    || new Set(data.instrument_ids).size !== data.instrument_ids.length || data.instrument_ids[0] !== data.instrument_id
    || data.scope !== (data.instrument_ids.length > 1 ? 'aggregate' : 'primary')
    || typeof data.venue_label !== 'string' || !data.venue_label
    || !Array.isArray(data.quote_asset_ids) || !data.quote_asset_ids.length || !data.quote_asset_ids.every(id => typeof id === 'string' && !!id)
    || typeof data.partial !== 'boolean') throw new Error('Invalid candle cohort or source identity');
  if (data.time_basis !== (data.source === 'session_trades' ? 'receive_utc' : 'exchange_utc')
    || data.source === 'venue_ohlcv' && data.scope !== 'primary') throw new Error('Invalid candle source boundary');
  if (data.source === 'session_trades') {
    const retention = data.retention;
    if (!data.partial || !Array.isArray(data.missing_instrument_ids)
      || !data.missing_instrument_ids.every(id => data.instrument_ids.includes(id))
      || data.bucket_alignment !== (data.interval === '1M' ? 'utc_calendar_month' : 'utc_epoch_floor')
      || typeof data.highwater !== 'string' || !/^\d+$/.test(data.highwater)
      || !retention || typeof retention.truncated !== 'boolean'
      || ![retention.max_trades, retention.max_bytes, retention.retained_trades, retention.retained_bytes].every(value => Number.isSafeInteger(value) && value >= 0)
      || retention.retained_trades > retention.max_trades || retention.retained_bytes > retention.max_bytes) {
      throw new Error('Invalid native candle coverage or retention');
    }
  }
  if (data.source === 'venue_ohlcv') {
    const history = data.history;
    const timestamp = (value: unknown) => typeof value === 'string' && /^[1-9]\d*$/.test(value)
      && BigInt(value) <= 9_223_372_036_854_775_807n;
    if (typeof data.series_key !== 'string' || !data.series_key || !history
      || !['more', 'exhausted', 'limited', 'unavailable', 'unsupported'].includes(history.state)
      || !['time', 'recent_only'].includes(history.pagination)
      || history.update_mode !== 'poll'
      || !Number.isSafeInteger(history.max_bars) || history.max_bars < 1
      || !Number.isSafeInteger(history.poll_interval_ms) || history.poll_interval_ms < 1
      || history.requested_before_ns !== null && !timestamp(history.requested_before_ns)
      || history.next_before_ns !== null && !timestamp(history.next_before_ns)
      || history.next_before_ns !== null && history.requested_before_ns !== null
        && BigInt(history.next_before_ns) >= BigInt(history.requested_before_ns)
      || history.state === 'more' && (history.next_before_ns === null || history.pagination !== 'time')) {
      throw new Error('Invalid venue candle history metadata or cursor');
    }
  }
  let previousEnd: bigint | null = null;
  for (const candle of data.candles) {
    if (!/^\d+$/.test(candle.start_ns) || !/^\d+$/.test(candle.end_ns)) throw new Error('Invalid candle timestamp');
    const start = BigInt(candle.start_ns); const end = BigInt(candle.end_ns);
    if (end / 1_000_000n > 8_640_000_000_000_000n) throw new Error('Candle timestamp outside safe plotting range');
    if (end <= start || previousEnd !== null && start < previousEnd) throw new Error('Unordered or overlapping candle history');
    previousEnd = end;
    const fields = [candle.open, candle.high, candle.low, candle.close, candle.volume].map(decimal);
    const places = Math.max(...fields.map(v => v.places));
    const [open, high, low, close, volume] = fields.map(v => atPlaces(v, places)) as [bigint, bigint, bigint, bigint, bigint];
    if (low <= 0n || open < low || close < low || high < open || high < close || volume < 0n || typeof candle.closed !== 'boolean') throw new Error('Invalid candle OHLCV envelope');
  }
  if (data.status === 'ready' && !data.candles.length || data.status !== 'ready' && data.candles.length) throw new Error('Inconsistent candle history status');
}

export const MAX_RETAINED_CANDLES = 10_000;

/** Provider pages may overlap or arrive out of order; the last duplicate in a page wins. */
export function normalizeCandlePage(data: CandleHistory): CandleHistory {
  if (data.source === 'session_trades') {
    validateCandles(data);
    return data;
  }
  const buckets = new Map(data.candles.map(candle => [BigInt(candle.start_ns), candle]));
  const candles = [...buckets.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([, candle]) => candle);
  const normalized = { ...data, candles };
  validateCandles(normalized);
  const before = normalized.history?.requested_before_ns;
  if (before != null && candles.some(candle => BigInt(candle.start_ns) >= BigInt(before))) {
    throw new Error('Candle page exceeds its exclusive before boundary');
  }
  return normalized;
}

/** Older pages cannot overwrite a newer poll. At the cap, keep every retained bucket. */
export function mergeCandleHistory(previous: CandleHistory | null, incoming: CandleHistory, older = false, growthPaused = false):
  { data: CandleHistory; historyLimitReached: boolean } {
  if (!previous || incoming.source === 'session_trades') {
    if (incoming.candles.length > MAX_RETAINED_CANDLES) throw new Error('Candle response exceeds retained history limit');
    return { data: incoming, historyLimitReached: incoming.candles.length === MAX_RETAINED_CANDLES };
  }
  if (!matchesCandleHistory(incoming, previous, previous.source_identity)
    || previous.series_key !== incoming.series_key || previous.time_basis !== incoming.time_basis
    || previous.volume_unit !== incoming.volume_unit) throw new Error('Candle series identity changed');
  const buckets = new Map(previous.candles.map(candle => [BigInt(candle.start_ns), candle]));
  const additions = incoming.candles.filter(candle => !buckets.has(BigInt(candle.start_ns)));
  const overflow = growthPaused || buckets.size + additions.length > MAX_RETAINED_CANDLES;
  for (const candle of incoming.candles) {
    const key = BigInt(candle.start_ns);
    if (buckets.has(key) ? !older : !overflow) buckets.set(key, candle);
  }
  const candles = [...buckets.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([, candle]) => candle);
  const data = { ...(older ? previous : incoming), candles,
    status: candles.length ? 'ready' as const : incoming.status,
    history: older ? incoming.history : previous.history,
    coverage_start_ns: candles[0]?.start_ns ?? null,
    coverage_end_ns: candles[candles.length - 1]?.end_ns ?? null };
  // A newest poll must not reset the oldest retained page's cursor.
  if (!previous.candles.length || !older && ['unavailable', 'unsupported'].includes(previous.history?.state ?? '')) data.history = incoming.history;
  validateCandles(data);
  return { data, historyLimitReached: overflow || candles.length === MAX_RETAINED_CANDLES };
}
export function candleDate(time: string | bigint, full = false): string {
  const date = new Date(Number(BigInt(time) / 1_000_000n));
  if (!Number.isFinite(date.getTime())) return String(time);
  return full ? `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC` : date.toISOString().slice(5, 16).replace('T', ' ');
}
