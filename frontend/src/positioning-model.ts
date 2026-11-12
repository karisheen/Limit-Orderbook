// Venue-reported open interest and funding observed by the native session.
// Buckets share the session-trade candle axis; gaps stay gaps and nothing is executable.

export interface PositioningBar {
  start_ns: string; end_ns: string; first_ns: string; last_ns: string; samples: number; closed: boolean;
  open_interest: { open: string; high: string; low: string; close: string; change: string } | null;
  funding_rate: string | null; funding_interval_ns: string | null; mark: string | null; oracle: string | null;
}
export interface PositioningSeries {
  stream_id: string; instrument_id: string; venue: string; api_family: string; native_symbol: string;
  base_asset_id: string; quote_asset_id: string; relation: 'selected' | 'related_underlying';
  open_interest_unit: string | null; open_interest_unit_provenance: string; funding_rate_unit: string;
  bars: PositioningBar[]; coverage_start_ns: string | null; coverage_end_ns: string | null;
  retention: { base_interval: string; max_minutes: number; retained_minutes: number };
}
export interface PositioningHistory {
  version: '1'; session_id: string; generation: string; source_identity: string; delivery_index: string;
  instrument_id: string; interval: string; status: 'ready' | 'empty' | 'unavailable';
  source: 'session_context'; time_basis: 'receive_utc'; fetched_at_ns: string; supported_intervals: string[];
  bucket_alignment: 'utc_calendar_month' | 'utc_epoch_floor'; series: PositioningSeries[]; reasons: string[];
  partial: boolean; highwater: string; label: string;
}
export type PositioningIdentity = Pick<PositioningHistory, 'session_id' | 'generation' | 'source_identity' | 'instrument_id' | 'interval'>;

const TIME = /^\d+$/;
const EXACT = /^-?\d+(\.\d+)?$/;
const YEAR_NS = 365 * 86_400 * 1e9;

/** Reject a response for another source, session, generation, selection or interval, or one that cannot be plotted honestly. */
export function validatePositioning(data: PositioningHistory, expected: PositioningIdentity): void {
  if (data.version !== '1' || data.session_id !== expected.session_id || data.generation !== expected.generation
    || data.source_identity !== expected.source_identity || data.instrument_id !== expected.instrument_id || data.interval !== expected.interval) {
    throw new Error('Positioning response belongs to a different source, session, generation, selection or interval');
  }
  if (data.source !== 'session_context' || data.time_basis !== 'receive_utc' || !['ready', 'empty', 'unavailable'].includes(data.status)
    || typeof data.source_identity !== 'string' || !data.source_identity || !TIME.test(data.fetched_at_ns)
    || !Array.isArray(data.series) || data.series.length > 16 || !Array.isArray(data.reasons)
    || !data.reasons.every(reason => typeof reason === 'string')) throw new Error('Invalid positioning response');
  let observed = false;
  for (const series of data.series) {
    if (typeof series.instrument_id !== 'string' || !series.instrument_id || !['selected', 'related_underlying'].includes(series.relation)
      || (series.relation === 'selected') !== (series.instrument_id === data.instrument_id) || !Array.isArray(series.bars)) {
      throw new Error('Invalid positioning series identity');
    }
    let previousEnd: bigint | null = null;
    for (const bar of series.bars) {
      if (![bar.start_ns, bar.end_ns, bar.first_ns, bar.last_ns].every(value => TIME.test(value))) throw new Error('Invalid positioning timestamp');
      const start = BigInt(bar.start_ns); const end = BigInt(bar.end_ns);
      if (end <= start || BigInt(bar.first_ns) < start || BigInt(bar.last_ns) >= end || BigInt(bar.last_ns) < BigInt(bar.first_ns)
        || previousEnd !== null && start < previousEnd) throw new Error('Unordered or overlapping positioning history');
      previousEnd = end;
      const interest = bar.open_interest;
      if (interest !== null && (series.open_interest_unit === null
        || ![interest.open, interest.high, interest.low, interest.close, interest.change].every(value => EXACT.test(value))
        || interest.low.startsWith('-'))) throw new Error('Invalid open interest observation');
      if (bar.funding_rate !== null && !EXACT.test(bar.funding_rate)
        || bar.funding_interval_ns !== null && (bar.funding_rate === null || !/^[1-9]\d*$/.test(bar.funding_interval_ns))
        || typeof bar.closed !== 'boolean' || !Number.isSafeInteger(bar.samples) || bar.samples < 1) throw new Error('Invalid funding observation');
      observed = true;
    }
  }
  if ((data.status === 'ready') !== observed) throw new Error('Inconsistent positioning status');
}

/** A pane must not attribute every observed rate to the latest bucket's funding interval. */
export function fundingIntervalLabel(bars: readonly PositioningBar[]): string {
  const intervals = new Set(bars.filter(bar => bar.funding_rate !== null).map(bar => bar.funding_interval_ns));
  if (intervals.size === 0) return 'not observed';
  if (intervals.size > 1) return 'fraction · mixed or unknown intervals';
  const interval = intervals.values().next().value;
  return interval ? `fraction per ${+(Number(interval) / 3.6e12).toFixed(2)}h` : 'fraction · interval unknown';
}

/**
 * Approximate simple annualization of a per-interval funding fraction, in percent.
 * Display only: funding is venue-reported and variable, so this is not a forecast or a P&L input.
 */
export function annualizedFundingPercent(rate: string, intervalNs: string): number {
  return Number(rate) * YEAR_NS / Number(intervalNs) * 100;
}
