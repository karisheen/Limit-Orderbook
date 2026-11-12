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
export interface CandleHistory {
  version: '1'; session_id: string; generation: string; source_identity: string; instrument_id: string;
  instrument_ids: string[]; scope: 'primary' | 'aggregate'; venue_label: string; quote_asset_ids: string[];
  interval: string; status: 'ready' | 'empty' | 'unavailable';
  source: CandleSource; venue: string; partial: boolean;
  time_basis: 'exchange_utc' | 'receive_utc'; fetched_at_ns: string;
  supported_intervals: string[]; candles: PriceCandle[]; reasons: string[];
  volume_unit: string; coverage_start_ns: string | null; coverage_end_ns: string | null;
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
export interface CandleRange { start: bigint; end: bigint }
export function candleInstrumentIds(primary: string, instrumentIds: readonly string[]): string[] {
  return [...instrumentIds].sort((a, b) => a === b ? 0 : a === primary ? -1 : b === primary ? 1 : a < b ? -1 : 1);
}
export function candleIdentity(frame: Frame | null, interval: string, instrumentIds: readonly string[], source: CandleSource, cohortReady: boolean): string {
  return frame ? JSON.stringify([frame.session_id, frame.generation, frame.source.id, frame.source.mode,
    frame.selection.instrument_id, interval, source, candleInstrumentIds(frame.selection.instrument_id ?? '', instrumentIds), cohortReady]) : '';
}
export function candleSourceBlock(source: CandleSource, primary: string, instrumentIds: readonly string[], cohortReady: boolean): string | null {
  if (!cohortReady) return 'Waiting for the selected native trade cohort.';
  if (!primary || !instrumentIds.includes(primary) || instrumentIds.length > 16
    || new Set(instrumentIds).size !== instrumentIds.length) return 'The selected native cohort does not match the active primary.';
  if (source === 'venue_ohlcv' && instrumentIds.length !== 1) {
    return 'Venue history is primary-only. Select a single native instrument to view it; an aggregate is never replaced by one venue.';
  }
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
export function candleRange(candles: readonly PriceCandle[], latest = false): CandleRange | null {
  if (!candles.length) return null;
  const last = candles[candles.length - 1]!;
  const duration = BigInt(last.end_ns) - BigInt(last.start_ns);
  const first = latest ? candles[Math.max(0, candles.length - 90)]! : candles[0]!;
  const padding = duration * 3n;
  return { start: BigInt(first.start_ns) - duration, end: BigInt(last.end_ns) + padding };
}
export function fractionAt(time: string | bigint, range: CandleRange): number {
  return Number(BigInt(time) - range.start) / Number(range.end - range.start);
}
export function timeAtFraction(fraction: number, range: CandleRange): bigint {
  return range.start + BigInt(Math.round(Number(range.end - range.start) * fraction));
}
export function panCandles(range: CandleRange, fraction: number): CandleRange {
  const shift = BigInt(Math.round(Number(range.end - range.start) * fraction));
  return { start: range.start + shift, end: range.end + shift };
}
export function zoomCandles(range: CandleRange, factor: number, anchor: number, candles: readonly PriceCandle[]): CandleRange {
  if (!candles.length) return range;
  const last = candles[candles.length - 1]!;
  const bucket = BigInt(last.end_ns) - BigInt(last.start_ns);
  const available = BigInt(last.end_ns) - BigInt(candles[0]!.start_ns);
  const max = available * 3n > bucket * 12n ? available * 3n : bucket * 12n;
  const proposed = BigInt(Math.round(Number(range.end - range.start) * factor));
  const span = proposed < bucket * 3n ? bucket * 3n : proposed > max ? max : proposed;
  const point = timeAtFraction(Math.max(0, Math.min(1, anchor)), range);
  const start = point - BigInt(Math.round(Number(span) * Math.max(0, Math.min(1, anchor))));
  return { start, end: start + span };
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
export function followCandleRange(range: CandleRange, previousEnd: bigint, candles: readonly PriceCandle[]): CandleRange {
  const end = candles.length ? BigInt(candles[candles.length - 1]!.end_ns) : previousEnd;
  const shift = end > previousEnd ? end - previousEnd : 0n;
  return { start: range.start + shift, end: range.end + shift };
}

/** UTC calendar ticks stay on useful clock boundaries while the viewport moves. */
export function candleTimeTicks(range: CandleRange, width: number): { time: bigint; label: string }[] {
  const day = 86_400_000_000_000n;
  const span = range.end - range.start;
  const count = Math.max(1, Math.min(12, Math.floor(width / 120)));
  const target = span / BigInt(count);
  const times: bigint[] = [];
  if (target >= 28n * day) {
    const first = new Date(Number(range.start / 1_000_000n));
    if (!Number.isFinite(first.getTime())) return [];
    const months = [1, 3, 6, 12, 24, 60, 120, 240, 600].find(value => BigInt(value) * 28n * day >= target) ?? 1200;
    let index = Math.floor((first.getUTCFullYear() * 12 + first.getUTCMonth()) / months) * months;
    for (let attempt = 0; attempt < 16; attempt++, index += months) {
      const date = new Date(0);
      date.setUTCFullYear(Math.floor(index / 12), index % 12, 1);
      if (!Number.isFinite(date.getTime())) break;
      const time = BigInt(date.getTime()) * 1_000_000n;
      if (time >= range.end) break;
      if (time >= range.start) times.push(time);
    }
  } else {
    const step = [60n, 180n, 300n, 900n, 1800n, 3600n, 7200n, 14400n, 21600n, 43200n, 86400n, 172800n, 604800n, 1209600n, 2419200n]
      .map(seconds => seconds * 1_000_000_000n).find(value => value >= target) ?? 28n * day;
    const first = range.start - ((range.start % step) + step) % step;
    for (let time = first; time < range.end && times.length < 16; time += step) if (time >= range.start) times.push(time);
  }
  return times.map(time => {
    const date = new Date(Number(time / 1_000_000n));
    const iso = Number.isFinite(date.getTime()) ? date.toISOString() : null;
    return { time, label: !iso ? String(time) : span < day ? iso.slice(11, 16)
      : target >= day ? iso.slice(0, 10) : iso.slice(5, 16).replace('T', ' ') };
  });
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
export function candleDate(time: string | bigint, full = false): string {
  const date = new Date(Number(BigInt(time) / 1_000_000n));
  if (!Number.isFinite(date.getTime())) return String(time);
  return full ? `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC` : date.toISOString().slice(5, 16).replace('T', ' ');
}
