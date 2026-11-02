import type { VisualGrid } from './api';

export type ChartMode = 'heatmap' | 'candles' | 'footprint' | 'profile' | 'cvd' | 'ofi';
export type ChartCursor = { time_ns: string; price: string | null };
export type ChartRange = { start_ns: string; end_ns: string };
export type PlotArea = { x: number; y: number; width: number; height: number };
export const CHART_LIMITS = { columns: 600, rows: 256, tiles: 640, trades: 2000, footprints: 12000, dpr: 2, width: 2048, height: 1280 } as const;
export const PLOT_MARGIN = { left: 12, right: 100, top: 14, bottom: 30 } as const;
export const COLORS = { bid: '#36cfc0', ask: '#f4ab58', buy: '#79f2b2', sell: '#ff6c90', unknown: '#c3a5ed', background: '#101721', missing: '#272132', grid: '#263241', text: '#b4c3d4' } as const;

/** Only display measurements use Number. Subtract exact clocks before conversion. */
export function timeFraction(time: string, start: string, end: string): number {
  const span = BigInt(end) - BigInt(start);
  return span > 0n ? Number(BigInt(time) - BigInt(start)) / Number(span) : 0;
}
export function timeAt(fraction: number, start: string, end: string): string {
  const span = BigInt(end) - BigInt(start);
  return (BigInt(start) + span * BigInt(Math.round(fraction * 1_000_000)) / 1_000_000n).toString();
}
export function shiftedRange(range: ChartRange, fraction: number): ChartRange {
  const delta = (BigInt(range.end_ns) - BigInt(range.start_ns)) * BigInt(Math.round(fraction * 1_000_000)) / 1_000_000n;
  const start = BigInt(range.start_ns) + delta;
  const correction = start < 0n ? -start : 0n;
  return { start_ns: (start + correction).toString(), end_ns: (BigInt(range.end_ns) + delta + correction).toString() };
}
export function zoomedRange(range: ChartRange, anchor: number, factor: number, bucket: string): ChartRange {
  const span = BigInt(range.end_ns) - BigInt(range.start_ns);
  const quantum = BigInt(bucket);
  const desired = span * BigInt(Math.round(factor * 1_000_000)) / 1_000_000n;
  const columnLimit = quantum * 600n;
  const maximum = columnLimit < 1_800_000_000_000n ? columnLimit : 1_800_000_000_000n;
  const nextSpan = desired < quantum ? quantum : desired > maximum ? maximum : desired;
  const pivot = BigInt(timeAt(Math.max(0, Math.min(1, anchor)), range.start_ns, range.end_ns));
  const start = pivot - nextSpan * BigInt(Math.round(Math.max(0, Math.min(1, anchor)) * 1_000_000)) / 1_000_000n;
  return { start_ns: (start < 0n ? 0n : start).toString(), end_ns: ((start < 0n ? 0n : start) + nextSpan).toString() };
}
export function plotNumber(value: string | null | undefined): number | null {
  if (value == null) return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function decimalParts(text: string): { coefficient: bigint; scale: number } {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match) throw new Error('Price is not representable by the chart coordinate projector.');
  const fraction = match[3] ?? '';
  const scale = fraction.length - Number(match[4] ?? 0);
  if (!Number.isSafeInteger(scale) || Math.abs(scale) > 1000) throw new Error('Price coordinate precision exceeds the bounded renderer.');
  return { coefficient: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${fraction}`), scale };
}

/** Exact bucket membership for a linked native price, without clamping off-screen prices. */
export function priceCellIndex(view: Pick<VisualGrid, 'price_min' | 'price_step' | 'rows'>, price: string): number {
  const origin = decimalParts(view.price_min), step = decimalParts(view.price_step), value = decimalParts(price);
  const scale = Math.max(origin.scale, step.scale, value.scale);
  const offset = value.coefficient * 10n ** BigInt(scale - value.scale) - origin.coefficient * 10n ** BigInt(scale - origin.scale);
  const unit = step.coefficient * 10n ** BigInt(scale - step.scale);
  if (unit <= 0n || offset < 0n || offset >= unit * BigInt(view.rows)) return -1;
  return Number(offset / unit);
}

/** Local-origin plotting avoids collapsing adjacent prices above Number's integer range.
 * This returns pixel-coordinate input only; it never aggregates or emits economic values.
 */
export function priceRowProjector(minimum: string, quantum: string): (price: string) => number {
  const origin = decimalParts(minimum);
  const step = decimalParts(quantum);
  if (step.coefficient <= 0n) throw new Error('Chart price quantum must be positive.');
  return (price: string) => {
    const value = decimalParts(price);
    const scale = Math.max(value.scale, origin.scale, step.scale);
    const offset = value.coefficient * 10n ** BigInt(scale - value.scale) - origin.coefficient * 10n ** BigInt(scale - origin.scale);
    const unit = step.coefficient * 10n ** BigInt(scale - step.scale);
    // Quotient/remainder stay local before conversion, including very small quantums.
    const ratio = Number(offset / unit) + Number(offset % unit * 1_000_000n / unit) / 1_000_000;
    if (!Number.isFinite(ratio)) throw new Error('Price lies outside the renderer coordinate range.');
    return ratio;
  };
}
export function saturation(contrast: number): number {
  return Number.isFinite(contrast) && contrast > 0 ? Math.min(1e12, Math.max(1e-12, contrast)) : 1;
}
export function intensity(value: string | null, contrast: number): number | null {
  const amount = plotNumber(value);
  return amount == null ? null : Math.max(0, Math.min(1, amount / saturation(contrast)));
}
export function rowAt(fractionFromTop: number, rows: number): number {
  return Math.max(0, Math.min(rows - 1, Math.floor((1 - fractionFromTop) * rows)));
}
export function columnAt(view: VisualGrid, time: string): VisualGrid['columns'][number] | undefined {
  const value = BigInt(time);
  return view.columns.find(column => BigInt(column.time_ns) <= value && value < BigInt(column.end_ns));
}
export function sideColor(side: string): string {
  return side === 'buy' ? COLORS.buy : side === 'sell' ? COLORS.sell : COLORS.unknown;
}
export function boundedSize(width: number, height: number, deviceRatio: number) {
  const w = Math.max(1, Math.floor(width));
  const h = Math.max(1, Math.floor(height));
  const ratio = Math.min(CHART_LIMITS.dpr, Math.max(1, deviceRatio || 1), CHART_LIMITS.width / w, CHART_LIMITS.height / h);
  return { width: w, height: h, ratio, pixelWidth: Math.max(1, Math.floor(w * ratio)), pixelHeight: Math.max(1, Math.floor(h * ratio)) };
}
export function plotArea(width: number, height: number): PlotArea {
  return { x: PLOT_MARGIN.left, y: PLOT_MARGIN.top, width: Math.max(1, width - PLOT_MARGIN.left - PLOT_MARGIN.right), height: Math.max(1, height - PLOT_MARGIN.top - PLOT_MARGIN.bottom) };
}
/** Stable cache identity excludes trades, cursor and viewport time bounds. */
export function liquidityTileKey(view: Pick<VisualGrid, 'generation' | 'grid_id' | 'price_min' | 'price_step' | 'rows'>, column: Pick<VisualGrid['columns'][number], 'segment_id' | 'liquidity_hash' | 'eligible'>, contrast: number): string {
  return JSON.stringify([view.generation, view.grid_id, view.price_min, view.price_step, view.rows, column.segment_id, column.liquidity_hash, column.eligible, saturation(contrast)]);
}
