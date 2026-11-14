import type { CandlestickData, HistogramData, UTCTimestamp, WhitespaceData } from 'lightweight-charts';
import type { PriceCandle } from './candle-model';

interface Decimal { units: bigint; places: number }
function decimal(text: string): Decimal {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) throw new Error('Unsupported precision: invalid decimal');
  const places = (match[3]?.length ?? 0) - Number(match[4] ?? 0);
  if (!Number.isSafeInteger(places) || Math.abs(places) > 1000 || text.length > 2048) throw new Error('Unsupported precision: decimal outside display bounds');
  return { units: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${match[3] ?? ''}`), places };
}
function units(value: Decimal, places: number): bigint { return value.units * 10n ** BigInt(places - value.places); }
function text(value: bigint, places: number): string {
  const sign = value < 0n ? '-' : '';
  const digits = (value < 0n ? -value : value).toString();
  if (places <= 0) return sign + digits + '0'.repeat(-places);
  const padded = digits.padStart(places + 1, '0');
  return `${sign}${padded.slice(0, -places)}.${padded.slice(-places)}`.replace(/\.?0+$/, '');
}

export interface ExactPlotScale {
  key: string; places: number; origin: bigint; exponent: number; logarithmic: boolean;
  coordinate: (value: string) => number; format: (value: number) => string; minMove: number; base: number;
}

/** Only plotting coordinates are floating point. Distinct exact values must remain distinct. */
export function exactPlotScale(values: readonly string[], options: { previous?: ExactPlotScale; zero?: boolean; log?: boolean; tick?: string | null } = {}): ExactPlotScale {
  const decimals = values.map(decimal);
  const tick = options.tick ? decimal(options.tick) : null;
  const places = Math.max(0, tick?.places ?? 0, ...decimals.map(value => value.places));
  const integers = decimals.map(value => units(value, places));
  const previous = options.previous;
  const reuse = previous && previous.places === places && previous.logarithmic === !!options.log;
  const origin = options.zero || options.log ? 0n : reuse ? previous.origin : integers[0] ?? 0n;
  let largest = 0n;
  for (const value of integers) { const distance = value > origin ? value - origin : origin - value; if (distance > largest) largest = distance; }
  const naturalExponent = Math.max(0, largest.toString().length - 6);
  const exponent = reuse && Number(`${largest}e-${previous.exponent}`) <= 1e12 ? previous.exponent : naturalExponent;
  const known = new Map<number, { exact: bigint; text: string }>();
  const coordinate = (value: string): number => {
    const parsed = decimal(value);
    if (parsed.places > places) throw new Error('Unsupported precision: decimal resolution changed');
    const exact = units(parsed, places);
    const result = options.log ? Number(value) : Number(`${exact - origin}e-${exponent}`);
    if (!Number.isFinite(result) || Math.abs(result) > (options.log ? 1e100 : 1e12) || result === 0 && exact !== origin
      || options.log && (exact <= 0n || result < 1e-100)) {
      throw new Error(options.log ? 'Log scale requires positive, representable prices; select Linear' : 'Unsupported precision: plotting range is too wide');
    }
    const existing = known.get(result);
    if (existing && existing.exact !== exact) throw new Error('Unsupported precision: distinct exact values collide on this scale');
    known.set(result, { exact, text: value });
    return result;
  };
  for (const value of values) coordinate(value);
  // The exact decimal quantum scales with the plotting units, not with differences
  // between floating-point coordinates. Native tick precision is included in places.
  const quantumExponent = options.log ? places : exponent;
  const minMove = Number(`1e-${quantumExponent}`);
  // lightweight-charts factors the base into 2s/5s (or accepts decimal powers).
  // Supply it explicitly: reciprocating a tiny minMove can round off that lattice.
  const base = Number(`1e${quantumExponent}`);
  if (!(minMove > 0) || !Number.isFinite(base)) throw new Error('Unsupported precision: display tick is outside chart bounds');
  const format = (value: number): string => {
    const exact = known.get(value);
    if (exact) return exact.text;
    if (!Number.isFinite(value)) return '—';
    if (options.log) return Number(value.toPrecision(8)).toString();
    const parsed = decimal(value.toPrecision(12));
    const power = exponent - parsed.places;
    const offset = power >= 0 ? parsed.units * 10n ** BigInt(power) : parsed.units / 10n ** BigInt(-power);
    return text(origin + offset, places);
  };
  return { key: `${!!options.log}:${places}:${origin}:${exponent}`, places, origin, exponent, logarithmic: !!options.log, coordinate, format, minMove, base };
}

/** Native buckets must be exact seconds, not rounded nanoseconds or relabeled clocks. */
export function chartTime(ns: string): UTCTimestamp {
  if (!/^\d+$/.test(ns)) throw new Error('Unsupported precision: invalid timestamp');
  const value = BigInt(ns);
  if (value % 1_000_000_000n !== 0n) throw new Error('Unsupported precision: subsecond candle boundary');
  const seconds = Number(value / 1_000_000_000n);
  if (!Number.isSafeInteger(seconds) || seconds < 0 || seconds > 253402300799) throw new Error('Unsupported precision: timestamp outside chart bounds');
  return seconds as UTCTimestamp;
}

export type PricePoint = CandlestickData<UTCTimestamp> | WhitespaceData<UTCTimestamp>;
export type VolumePoint = HistogramData<UTCTimestamp> | WhitespaceData<UTCTimestamp>;
export interface PriceSeriesData {
  prices: PricePoint[]; volumes: VolumePoint[]; exact: Map<number, PriceCandle>;
  priceScale: ExactPlotScale; volumeScale: ExactPlotScale; times: number[];
}
export function priceSeriesData(candles: readonly PriceCandle[], options: { log?: boolean; tick?: string | null; previous?: PriceSeriesData; up: string; down: string }): PriceSeriesData {
  const priceScale = exactPlotScale(candles.flatMap(bar => [bar.open, bar.high, bar.low, bar.close]), { log: options.log, tick: options.tick, previous: options.previous?.prices.length ? options.previous.priceScale : undefined });
  const volumeScale = exactPlotScale(candles.map(bar => bar.volume), { zero: true, previous: options.previous?.volumes.length ? options.previous.volumeScale : undefined });
  const prices: PricePoint[] = []; const volumes: VolumePoint[] = []; const exact = new Map<number, PriceCandle>();
  let previous: PriceCandle | undefined;
  for (const bar of candles) {
    const time = chartTime(bar.start_ns);
    if (previous && BigInt(previous.start_ns) >= BigInt(bar.start_ns)) throw new Error('Unsupported display: duplicate or unordered candle buckets');
    if (previous && BigInt(previous.end_ns) < BigInt(bar.start_ns)) {
      const gap = chartTime(previous.end_ns);
      prices.push({ time: gap }); volumes.push({ time: gap });
    }
    const open = priceScale.coordinate(bar.open); const close = priceScale.coordinate(bar.close);
    prices.push({ time, open, high: priceScale.coordinate(bar.high), low: priceScale.coordinate(bar.low), close });
    volumes.push({ time, value: volumeScale.coordinate(bar.volume), color: close >= open ? options.up : options.down });
    exact.set(time, bar); previous = bar;
  }
  return { prices, volumes, exact, priceScale, volumeScale, times: prices.map(point => point.time) };
}

export interface SeriesUpdate<T> { reset: boolean; updates: { point: T; historical: boolean }[] }
/** Corrections use historical update; rollover appends once. Only structural changes replace data. */
export function seriesUpdate<T extends { time: number }>(previous: readonly T[], next: readonly T[]): SeriesUpdate<T> {
  if (!previous.length || next.length < previous.length || previous.some((point, index) => point.time !== next[index]?.time)) return { reset: true, updates: [] };
  const updates: SeriesUpdate<T>['updates'] = [];
  for (let index = 0; index < next.length; index++) {
    const point = next[index]!; const before = previous[index];
    if (!before || Object.keys(point).length !== Object.keys(before).length || Object.keys(point).some(key => point[key as keyof T] !== before[key as keyof T])) {
      updates.push({ point, historical: index < previous.length - 1 });
    }
  }
  return { reset: false, updates };
}

/** Preserve the same timestamp plus fractional bar under the left viewport edge after prepend. */
export function anchorLogicalRange(previous: readonly number[], next: readonly number[], range: { from: number; to: number }): { from: number; to: number } {
  if (!previous.length || !next.length) return range;
  const index = Math.max(0, Math.min(previous.length - 1, Math.floor(range.from)));
  const anchor = previous[index]!;
  const nextIndex = next.indexOf(anchor);
  if (nextIndex < 0) return range;
  const shift = nextIndex - index;
  return { from: range.from + shift, to: range.to + shift };
}

/** LWC ranges include both endpoint bars: keep the left fractional bar and pixel spacing,
 * not the old bar count, when a deferred price-axis layout changes the plot width. */
export function pixelAnchoredRange(from: number, barSpacing: number, width: number): { from: number; to: number } {
  return { from, to: from + Math.max(1, width / barSpacing) - 1 };
}

export function latestLogicalRange(length: number, width: number, rightOffset: number): { from: number; to: number } {
  const count = Math.max(20, Math.min(160, Math.floor(Math.max(1, width) / 8)));
  const to = Math.max(0, length - 1) + rightOffset;
  return { from: to - count, to };
}
