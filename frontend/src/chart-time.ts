import type { ReceiveClockSegment, VisualGrid } from './api';

export type ChartTimeZone = 'UTC' | 'local';
export interface ChartTimeTick { time_ns: string; label: string; kind: 'time' | 'date' | 'discontinuity' }

const NS_PER_MS = 1_000_000n;
const NS_PER_SECOND = 1_000_000_000n;
const NS_PER_DAY = 86_400n * NS_PER_SECOND;
const DATE_LIMIT_MS = 8_640_000_000_000_000n;
const UNAVAILABLE = 'Clock time unavailable';
const INTERVALS = [1n, 2n, 5n, 10n, 15n, 30n, 60n, 120n, 300n, 600n, 900n, 1800n,
  3600n, 7200n, 10800n, 21600n, 43200n, 86400n, 172800n, 604800n, 2592000n, 7776000n, 31536000n]
  .map(seconds => seconds * NS_PER_SECOND);

type Segment = { start: bigint; offset: bigint; original: ReceiveClockSegment };
type Projection = { start: bigint; end: bigint; segments: Segment[] };

function integer(value: string | null | undefined, signed = false): bigint | null {
  if (typeof value !== 'string' || !(signed ? /^-?\d+$/ : /^\d+$/).test(value)) return null;
  return BigInt(value);
}

function projection(view: VisualGrid): Projection | null {
  const axis = view.time_axis;
  if (!axis || axis.version !== '1' || axis.basis !== 'receive_mono_highwater'
    || axis.projection !== 'piecewise_receive_pair_v1' || !axis.source_identity || !axis.scope_id
    || !axis.clock_id || axis.clock_id !== view.clock_id) return null;
  const start = integer(axis.available_start_ns), capturedEnd = integer(axis.available_end_ns);
  const now = integer(view.now_ns);
  if (start === null || capturedEnd === null || now === null) return null;
  const end = capturedEnd < now ? capturedEnd : now;
  if (end < start) return null;
  const segments: Segment[] = [];
  let previous: bigint | null = null;
  for (const segment of axis.segments) {
    const admission = integer(segment.start_ns);
    if (admission === null || (previous !== null && admission <= previous)) return null;
    previous = admission;
    // A response's captured frontier, never its requested viewport end, is authoritative.
    if (admission > end) break;
    const mono = integer(segment.reference.receive_mono_ns);
    const wall = integer(segment.reference.receive_wall_ns, true);
    if (!segment.id || segment.reference.clock_id !== axis.clock_id || mono === null || wall === null
      || mono > admission) return null;
    segments.push({ start: admission, offset: wall - mono, original: segment });
  }
  if (!segments.length || segments[0]!.start > start) return null;
  return { start, end, segments };
}

function project(mapping: Projection, time: bigint): bigint | null {
  if (time < mapping.start || time > mapping.end) return null;
  let selected: Segment | undefined;
  for (const segment of mapping.segments) {
    if (segment.start > time) break;
    selected = segment;
  }
  return selected ? time + selected.offset : null;
}

/** Admission selects the pair; its original receive monotonic instant remains the anchor. */
export function projectReceiveTime(view: VisualGrid, timeNs: string): string | null {
  const time = integer(timeNs), mapping = projection(view);
  if (time === null || !mapping) return null;
  return project(mapping, time)?.toString() ?? null;
}

function floorDiv(value: bigint, divisor: bigint): bigint {
  const quotient = value / divisor;
  return value < 0n && value % divisor !== 0n ? quotient - 1n : quotient;
}

function dateAt(wall: bigint): Date | null {
  const milliseconds = floorDiv(wall, NS_PER_MS);
  if (milliseconds < -DATE_LIMIT_MS || milliseconds > DATE_LIMIT_MS) return null;
  return new Date(Number(milliseconds));
}

function pad(value: number, length = 2): string { return String(value).padStart(length, '0'); }

// Numeric date accessors avoid locale ambiguity and repeated Intl construction entirely.
// The local offset is evaluated at the displayed instant, not at render time (DST-safe).
function parts(date: Date, zone: ChartTimeZone) {
  return zone === 'UTC'
    ? { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate(),
      hour: date.getUTCHours(), minute: date.getUTCMinutes(), second: date.getUTCSeconds() }
    : { year: date.getFullYear(), month: date.getMonth() + 1, day: date.getDate(),
      hour: date.getHours(), minute: date.getMinutes(), second: date.getSeconds() };
}

function dateLabel(date: Date, zone: ChartTimeZone): string {
  const { year, month, day } = parts(date, zone);
  const yearText = year < 0 ? `-${pad(-year, 6)}` : year > 9999 ? `+${pad(year, 6)}` : pad(year, 4);
  return `${yearText}-${pad(month)}-${pad(day)}`;
}

function timeLabel(date: Date, zone: ChartTimeZone, seconds: boolean): string {
  const value = parts(date, zone);
  return `${pad(value.hour)}:${pad(value.minute)}${seconds ? `:${pad(value.second)}` : ''}`;
}

function offsetLabel(date: Date, zone: ChartTimeZone): string {
  if (zone === 'UTC') return 'UTC';
  const minutes = -date.getTimezoneOffset();
  return `UTC${minutes < 0 ? '-' : '+'}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
}

export function chartTimeZoneLabel(zone: ChartTimeZone = 'UTC'): string {
  return zone === 'UTC' ? 'UTC' : 'Local time';
}

/** Compact permanent-axis context; precise time stays in the crosshair and Inspector. */
export function formatChartDateContext(view: VisualGrid, timeNs: string, zone: ChartTimeZone = 'UTC'): string {
  const wall = projectReceiveTime(view, timeNs);
  const date = wall === null ? null : dateAt(BigInt(wall));
  return date ? `${dateLabel(date, zone)} ${offsetLabel(date, zone)}`
    : `${UNAVAILABLE} · ${chartTimeZoneLabel(zone)}`;
}

export function formatChartTime(view: VisualGrid, timeNs: string, zone: ChartTimeZone = 'UTC', detail = false): string {
  const wall = projectReceiveTime(view, timeNs);
  if (wall === null) return UNAVAILABLE;
  const wallNs = BigInt(wall), date = dateAt(wallNs);
  if (!date) return UNAVAILABLE;
  if (!detail) {
    const start = integer(view.start_ns), end = integer(view.end_ns);
    return timeLabel(date, zone, start === null || end === null || end - start < 300n * NS_PER_SECOND);
  }
  const fraction = wallNs - floorDiv(wallNs, NS_PER_SECOND) * NS_PER_SECOND;
  return `${dateLabel(date, zone)} ${timeLabel(date, zone, true)}.${fraction.toString().padStart(9, '0')} ${offsetLabel(date, zone)}`;
}

function intervalFor(span: bigint, width: number): bigint {
  const slots = BigInt(Math.max(1, Math.floor(width / 90)));
  const target = (span + slots - 1n) / slots;
  for (const interval of INTERVALS) if (interval >= target) return interval;
  let interval = INTERVALS[INTERVALS.length - 1]!;
  while (interval < target) interval *= 10n;
  return interval;
}

function localOffset(wall: bigint, zone: ChartTimeZone): bigint {
  if (zone === 'UTC') return 0n;
  const date = dateAt(wall);
  return date ? BigInt(-date.getTimezoneOffset()) * 60n * NS_PER_SECOND : 0n;
}

function nextMidnight(wall: bigint, zone: ChartTimeZone): bigint | null {
  const date = dateAt(wall);
  if (!date) return null;
  if (zone === 'UTC') date.setUTCHours(0, 0, 0, 0);
  else date.setHours(0, 0, 0, 0);
  if (!Number.isFinite(date.getTime())) return null;
  if (BigInt(date.getTime()) * NS_PER_MS < wall) {
    if (zone === 'UTC') date.setUTCDate(date.getUTCDate() + 1);
    else date.setDate(date.getDate() + 1);
  }
  return Number.isFinite(date.getTime()) ? BigInt(date.getTime()) * NS_PER_MS : null;
}

/** Wall-aligned labels only; every returned coordinate remains in native monotonic geometry. */
export function chartTimeTicks(view: VisualGrid, width: number, zone: ChartTimeZone = 'UTC'): ChartTimeTick[] {
  const mapping = projection(view), start = integer(view.start_ns), end = integer(view.end_ns);
  if (!mapping || start === null || end === null || end <= start || !Number.isFinite(width) || width <= 0) return [];
  const low = start > mapping.start ? start : mapping.start;
  const high = end < mapping.end ? end : mapping.end;
  if (high < low) return [];
  const span = end - start, selectedInterval = intervalFor(span, width);
  const seconds = span < 300n * NS_PER_SECOND;
  const interval = !seconds && selectedInterval < 60n * NS_PER_SECOND ? 60n * NS_PER_SECOND : selectedInterval;
  const candidates = new Map<string, ChartTimeTick>();
  const priority = { time: 0, date: 1, discontinuity: 2 };
  const add = (time: bigint, kind: ChartTimeTick['kind'], label: string) => {
    if (time < low || time > high) return;
    const key = time.toString(), previous = candidates.get(key);
    if (!previous || priority[kind] > priority[previous.kind]) candidates.set(key, { time_ns: key, kind, label });
  };
  for (let index = 0; index < mapping.segments.length; index++) {
    const segment = mapping.segments[index]!;
    const segmentLow = segment.start > low ? segment.start : low;
    const following = mapping.segments[index + 1];
    const segmentHigh = following && following.start <= high ? following.start - 1n : high;
    if (segmentHigh < segmentLow) continue;
    const wallLow = segmentLow + segment.offset, wallHigh = segmentHigh + segment.offset;
    const firstDate = dateAt(wallLow);
    if (!firstDate || !dateAt(wallHigh)) continue;
    if (segment.original.cause === 'wall_offset_change' && segment.start >= low) {
      add(segment.start, 'discontinuity', `${timeLabel(firstDate, zone, seconds)}*`);
    }
    const offset = localOffset(wallLow, zone);
    let wall = (floorDiv(wallLow + offset, interval) * interval) - offset;
    if (wall < wallLow) wall += interval;
    // Clock-aligned sub-day steps. Daily/coarser ticks use calendar midnights below.
    if (interval < NS_PER_DAY) {
      for (; wall <= wallHigh; wall += interval) {
        const currentOffset = localOffset(wall, zone);
        let aligned = floorDiv(wall + currentOffset, interval) * interval - currentOffset;
        if (aligned < wall) aligned += interval;
        wall = aligned;
        if (wall > wallHigh) break;
        const date = dateAt(wall);
        if (date) add(wall - segment.offset, 'time', timeLabel(date, zone, seconds));
      }
    }
    // Day boundaries take precedence, including 23/25-hour local days. Large spans
    // advance whole calendar days rather than scanning every hidden date.
    const days = interval / NS_PER_DAY;
    const dayStride = days < 1n ? 1 : Number(days > 200_000_000n ? 200_000_000n : days);
    let midnight = nextMidnight(wallLow, zone);
    if (midnight !== null && dayStride > 1) {
      const day = floorDiv(midnight + localOffset(midnight, zone), NS_PER_DAY);
      const remainder = ((day % BigInt(dayStride)) + BigInt(dayStride)) % BigInt(dayStride);
      if (remainder !== 0n) {
        const date = dateAt(midnight)!;
        if (zone === 'UTC') date.setUTCDate(date.getUTCDate() + dayStride - Number(remainder));
        else date.setDate(date.getDate() + dayStride - Number(remainder));
        midnight = Number.isFinite(date.getTime()) ? BigInt(date.getTime()) * NS_PER_MS : null;
      }
    }
    while (midnight !== null && midnight <= wallHigh) {
      const date = dateAt(midnight)!;
      add(midnight - segment.offset, 'date', dateLabel(date, zone));
      if (zone === 'UTC') date.setUTCDate(date.getUTCDate() + dayStride);
      else date.setDate(date.getDate() + dayStride);
      const next = Number.isFinite(date.getTime()) ? BigInt(date.getTime()) * NS_PER_MS : null;
      midnight = next !== null && next > midnight ? next : null;
    }
  }
  // Use the same clamped label centers as the renderer. Exact bigint ratios are
  // reduced before Number conversion; even extremely large monotonic clocks fit.
  const position = (tick: ChartTimeTick) => Number((BigInt(tick.time_ns) - start) * 1_000_000n / span) / 1_000_000 * width;
  const occupied: Array<{ left: number; right: number }> = [];
  const selected: ChartTimeTick[] = [];
  const ordered = [...candidates.values()].sort((a, b) => priority[b.kind] - priority[a.kind] || position(a) - position(b));
  for (const tick of ordered) {
    const half = tick.label.length * 7 / 2;
    const center = Math.max(half, Math.min(width - half, position(tick)));
    const left = center - half, right = center + half;
    if (right > width || occupied.some(other => left < other.right + 12 && right + 12 > other.left)) {
      // Keep the exact step marker even where two labels cannot both fit.
      if (tick.kind === 'discontinuity') selected.push({ ...tick, label: '' });
      continue;
    }
    occupied.push({ left, right });
    selected.push(tick);
  }
  return selected.sort((a, b) => BigInt(a.time_ns) < BigInt(b.time_ns) ? -1 : BigInt(a.time_ns) > BigInt(b.time_ns) ? 1 : 0);
}
