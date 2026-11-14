import type { VisualGrid, NativeMarketTrade } from './api';
import type { FootprintCandle, NormalizedTrade } from './orderflow/lib/types/market';

export const FOOTPRINT_LAYER_LIMITS = { rangeSeconds: 1800, columns: 600, rows: 128, tiles: 16000, prints: 200, candidates: 2000 } as const;
export interface LiquidityTile { start: number; end: number; price: number; step: number; bid: string | null; ask: string | null }
export interface LiquidityProjection { tiles: LiquidityTile[]; reason: string | null; limited: boolean }
const integer = (value: string) => /^\d+$/.test(value) ? BigInt(value) : null;
const milliseconds = (value: bigint) => {
  const ms = Number(value / 1_000_000n);
  return value >= 0n && Number.isSafeInteger(ms) && ms <= 8_640_000_000_000_000 ? ms : NaN;
};

/** Match the native 30-minute range ceiling independently of the column budget. */
export function footprintLiquidityWindow(candles: readonly Pick<FootprintCandle, 'openTime' | 'closeTime'>[]) {
  const retainedSeconds = Math.ceil(((candles.at(-1)?.closeTime ?? 0) - (candles[0]?.openTime ?? 0) + 1) / 1000);
  const rangeSeconds = Math.max(180, Math.min(FOOTPRINT_LAYER_LIMITS.rangeSeconds, retainedSeconds));
  return { rangeSeconds, bucket: rangeSeconds < FOOTPRINT_LAYER_LIMITS.columns ? '1000000000' : '5000000000',
    limited: retainedSeconds > FOOTPRINT_LAYER_LIMITS.rangeSeconds };
}

/** The footprint uses the producer's FIRST receive-pair anchor, not the visual
 * grid's piecewise wall-clock labels. Recover that exact affine mapping from
 * retained native references; never substitute Date.now or the current book. */
export function projectFootprintLiquidity(grid: VisualGrid | null, trades: readonly NormalizedTrade[], generation: string, instrumentId: string, cohortInstrumentIds: readonly string[] = [instrumentId]): LiquidityProjection {
  const unavailable = (reason: string): LiquidityProjection => ({ tiles: [], reason, limited: false });
  if (!grid) return unavailable('No historical resting-liquidity evidence');
  if (grid.generation !== generation || grid.instrument_id !== instrumentId) return unavailable('Historical liquidity identity mismatch');
  const axis = grid.time_axis;
  if (!axis.scope_id || !axis.source_identity || axis.clock_id !== grid.clock_id || axis.version !== '1' || axis.projection !== 'piecewise_receive_pair_v1' || axis.basis !== 'receive_mono_highwater') return unavailable('Historical liquidity clock unavailable');
  if (!cohortInstrumentIds.includes(instrumentId)) return unavailable('Footprint cohort does not contain selected primary');
  let offset: bigint | null = null;
  let session: string | null = null;
  let primaryAnchor = false;
  for (const trade of trades.slice(0, FOOTPRINT_LAYER_LIMITS.candidates)) {
    const native = trade.native;
    if (!native || !cohortInstrumentIds.includes(native.instrument_id) || native.trade.is_subscription_history) continue;
    if (native.asof.clock_id !== grid.clock_id || native.source_identity.clock_id !== grid.clock_id) return unavailable('Footprint and liquidity clocks differ');
    if (native.source_identity.session_id !== native.asof.session_id) return unavailable('Footprint receive source mismatch');
    if (native.instrument_id === instrumentId) primaryAnchor = true;
    const wall = integer(native.display_time_ns), mono = integer(native.asof.receive_mono_ns);
    if (wall === null || mono === null) return unavailable('Footprint receive projection unavailable');
    const next = wall - mono;
    if (offset !== null && (next !== offset || session !== native.asof.session_id)) return unavailable('Footprint receive projection changed');
    offset = next;
    session = native.asof.session_id;
  }
  if (offset === null || !primaryAnchor) return unavailable('Waiting for a retained primary trade receive-clock anchor');
  const floor = axis.available_start_ns === null ? null : integer(axis.available_start_ns);
  const frontier = axis.available_end_ns === null ? null : integer(axis.available_end_ns);
  const now = integer(grid.now_ns);
  const step = Number(grid.price_step);
  if (floor === null || frontier === null || now === null || !Number.isFinite(step) || step <= 0) return unavailable('Historical liquidity coverage unavailable');
  const endLimit = frontier < now ? frontier : now;
  const tiles: LiquidityTile[] = [];
  let limited = grid.truncated || !grid.complete_window
    || floor > BigInt(grid.start_ns) || endLimit < BigInt(grid.end_ns)
    || grid.columns.length > FOOTPRINT_LAYER_LIMITS.columns || grid.prices.length > FOOTPRINT_LAYER_LIMITS.rows;
  for (const column of grid.columns.slice(-FOOTPRINT_LAYER_LIMITS.columns)) {
    if (!column.eligible || !column.asof || column.asof.clock_id !== grid.clock_id || column.asof.session_id !== session) continue;
    const start = integer(column.time_ns), end = integer(column.end_ns);
    if (start === null || end === null || end <= start) continue;
    const left = start > floor ? start : floor, right = end < endLimit ? end : endLimit;
    if (right <= left) continue;
    const from = milliseconds(left + offset), to = milliseconds(right + offset);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) continue;
    for (let row = 0; row < Math.min(grid.prices.length, column.cells.length, FOOTPRINT_LAYER_LIMITS.rows); row++) {
      const price = Number(grid.prices[row]), [bid, ask] = column.cells[row]!;
      if (!Number.isFinite(price) || price <= 0 || ![bid, ask].some(quantity => quantity !== null && Number.isFinite(Number(quantity)) && Number(quantity) > 0)) continue;
      if (tiles.length === FOOTPRINT_LAYER_LIMITS.tiles) { limited = true; break; }
      tiles.push({ start: from, end: to, price, step, bid, ask });
    }
    if (tiles.length === FOOTPRINT_LAYER_LIMITS.tiles) { limited = true; break; }
  }
  return { tiles, limited, reason: tiles.length ? null : 'No eligible historical depth in the retained window' };
}

/** Sparse footprint columns are ordinal: never stretch a tile across missing buckets. */
export function footprintTimeSlices(candles: readonly Pick<FootprintCandle, 'openTime' | 'closeTime'>[], start: number, end: number) {
  const result: { index: number; from: number; to: number }[] = [];
  let low = 0, high = candles.length;
  while (low < high) { const middle = (low + high) >>> 1; if (candles[middle]!.closeTime + 1 <= start) low = middle + 1; else high = middle; }
  for (let index = low; index < candles.length; index++) {
    const candle = candles[index]!;
    if (candle.openTime >= end) break;
    const span = candle.closeTime + 1 - candle.openTime;
    if (span <= 0) continue;
    const from = Math.max(start, candle.openTime), to = Math.min(end, candle.closeTime + 1);
    if (to > from) result.push({ index, from: (from - candle.openTime) / span, to: (to - candle.openTime) / span });
  }
  return result;
}

export interface NativePrint { id: string; venue: string; time: number; price: number; quantity: number; side: NormalizedTrade['side']; native: NativeMarketTrade }
export function retainedLargePrints(trades: readonly NormalizedTrade[], instrumentIds: readonly string[], minimum: number): NativePrint[] {
  if (!Number.isFinite(minimum) || minimum < 0) return [];
  const allowed = new Set(instrumentIds), seen = new Set<string>();
  const result: NativePrint[] = [];
  for (const trade of trades.slice(0, FOOTPRINT_LAYER_LIMITS.candidates)) {
    const native = trade.native;
    if (!native || !allowed.has(native.instrument_id) || native.trade.is_subscription_history || native.base_quantity === null) continue;
    const quantity = Number(native.base_quantity), price = Number(native.trade.price), stamp = integer(native.display_time_ns);
    const time = stamp === null ? NaN : milliseconds(stamp);
    if (!Number.isFinite(quantity) || quantity <= 0 || quantity < minimum || !Number.isFinite(price) || price <= 0 || !Number.isFinite(time)) continue;
    const id = `${native.instrument_id}:${native.cursor.delivery}:${native.cursor.event}`;
    if (seen.has(id)) continue;
    seen.add(id);
    result.push({ id, venue: trade.venue, time, price, quantity, side: trade.side, native });
  }
  return result.sort((a, b) => b.quantity - a.quantity || a.id.localeCompare(b.id)).slice(0, FOOTPRINT_LAYER_LIMITS.prints);
}

export interface PrintBadge { print: NativePrint; x: number; y: number; radius: number; width: number }
/** Largest first, fixed bound; overlapping venue labels and bubbles are suppressed together. */
export function suppressPrintCollisions(candidates: readonly PrintBadge[]): PrintBadge[] {
  const visible: PrintBadge[] = [];
  for (const badge of candidates.slice(0, FOOTPRINT_LAYER_LIMITS.prints)) {
    if (visible.some(other => Math.abs(badge.x - other.x) < (badge.width + other.width) / 2 + 4 && Math.abs(badge.y - other.y) < Math.max(6, badge.radius) + Math.max(6, other.radius) + 4)) continue;
    visible.push(badge);
  }
  return visible;
}

export function printInspection(print: NativePrint): string[] {
  const native = print.native;
  return [`Venue: ${print.venue}`, `Instrument: ${native.instrument_id}`, `Price: ${native.trade.price}`, `Base size: ${native.base_quantity}`,
    `Native size: ${native.trade.quantity} ${native.trade.quantity_unit}`, `Projected receive ns: ${native.display_time_ns}`,
    `Original receive wall ns: ${native.asof.receive_wall_ns}`, `Receive monotonic ns: ${native.asof.receive_mono_ns}`,
    `Exchange event ns: ${native.asof.exchange_event_wall_ns ?? 'Unavailable'}`, `Clock: ${native.asof.clock_id}`, `Trade: ${native.trade.trade_id ?? native.trade.dedup_key}`];
}
