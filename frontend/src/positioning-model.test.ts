import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annualizedFundingPercent, fundingIntervalLabel, validatePositioning } from './positioning-model.ts';
import type { PositioningBar, PositioningHistory } from './positioning-model.ts';
import { candleAtTime, candlePriceScale } from './candle-model.ts';

const minute = 60_000_000_000n;
const origin = 1_704_067_200_000_000_000n;
const expected = { session_id: 'session', generation: '4', source_identity: 'live:clock-session', instrument_id: 'venue:perpetual:BTC', interval: '1m' };
function bar(offset: bigint, overrides: Partial<PositioningBar> = {}): PositioningBar {
  const start = origin + offset * minute;
  return { start_ns: start.toString(), end_ns: (start + minute).toString(), first_ns: start.toString(), last_ns: (start + minute - 1n).toString(),
    samples: 3, closed: true, open_interest: { open: '100', high: '120', low: '90', close: '110', change: '10' },
    funding_rate: '-0.0000125', funding_interval_ns: '3600000000000', mark: '60000', oracle: '60001', ...overrides };
}
function history(bars: PositioningBar[] = [bar(0n)], overrides: Partial<PositioningHistory> = {}): PositioningHistory {
  return { version: '1', ...expected, source_identity: 'live:clock-session', delivery_index: '10', status: bars.length ? 'ready' : 'empty',
    source: 'session_context', time_basis: 'receive_utc', fetched_at_ns: (origin + minute).toString(), supported_intervals: ['1m'],
    bucket_alignment: 'utc_epoch_floor', reasons: [], partial: true, highwater: '10', label: 'venue_reported_positioning_not_executable',
    series: [{ stream_id: 'context', instrument_id: expected.instrument_id, venue: 'venue', api_family: 'family', native_symbol: 'BTC',
      base_asset_id: 'BTC', quote_asset_id: 'USD', relation: 'selected', open_interest_unit: 'base',
      open_interest_unit_provenance: 'adapter_declared_not_independently_verified', funding_rate_unit: 'fraction_per_funding_interval',
      bars, coverage_start_ns: bars[0]?.first_ns ?? null, coverage_end_ns: bars.at(-1)?.last_ns ?? null,
      retention: { base_interval: '1m', max_minutes: 10080, retained_minutes: bars.length } }],
    ...overrides };
}

test('observed gaps, funding-only buckets and an empty history are accepted as delivered', () => {
  validatePositioning(history([bar(0n), bar(1n, { open_interest: null }), bar(5n, { funding_rate: null, funding_interval_ns: null })]), expected);
  validatePositioning(history([]), expected);
});

test('a response for another source, generation, selection or interval is never plotted beside current candles', () => {
  for (const foreign of [{ source_identity: 'live:other-clock' }, { generation: '5' }, { instrument_id: 'venue:perpetual:ETH' }, { interval: '5m' }, { session_id: 'other' }]) {
    assert.throws(() => validatePositioning(history([bar(0n)], foreign), expected), /different source, session, generation, selection or interval/);
  }
});

test('a related perpetual cannot be presented as the selected instrument', () => {
  const related = history();
  related.series[0]!.instrument_id = 'other-venue:perpetual:BTC';
  assert.throws(() => validatePositioning(related, expected), /series identity/);
  related.series[0]!.relation = 'related_underlying';
  validatePositioning(related, expected);
});

test('overlapping buckets, unitless open interest and inconsistent status are rejected', () => {
  assert.throws(() => validatePositioning(history([bar(1n), bar(0n)]), expected), /Unordered or overlapping/);
  const unitless = history();
  unitless.series[0]!.open_interest_unit = null;
  assert.throws(() => validatePositioning(unitless, expected), /open interest/);
  assert.throws(() => validatePositioning(history([bar(0n, { funding_rate: null })]), expected), /funding/);
  assert.throws(() => validatePositioning(history([bar(0n)], { status: 'empty' }), expected), /status/);
});

test('hourly funding annualizes by simple scaling and keeps its sign', () => {
  assert.ok(Math.abs(annualizedFundingPercent('0.0000125', '3600000000000') - 10.95) < 1e-9);
  assert.ok(Math.abs(annualizedFundingPercent('-0.0001', '28800000000000') + 10.95) < 1e-9);
});

test('funding pane labels do not assign the latest interval to mixed or unknown observations', () => {
  assert.equal(fundingIntervalLabel([]), 'not observed');
  assert.equal(fundingIntervalLabel([bar(0n), bar(1n)]), 'fraction per 1h');
  assert.equal(fundingIntervalLabel([bar(0n), bar(1n, { funding_rate: null, funding_interval_ns: null })]), 'fraction per 1h');
  assert.equal(fundingIntervalLabel([bar(0n, { funding_interval_ns: null })]), 'fraction · interval unknown');
  assert.equal(fundingIntervalLabel([bar(0n), bar(1n, { funding_interval_ns: null })]), 'fraction · mixed or unknown intervals');
  assert.equal(fundingIntervalLabel([bar(0n), bar(1n, { funding_interval_ns: '28800000000000' })]), 'fraction · mixed or unknown intervals');
});

test('positioning lookup preserves missing buckets on the candle inspection axis', () => {
  const bars = [bar(0n), bar(2n)];
  assert.equal(candleAtTime(bars, origin + minute / 2n), 0);
  assert.equal(candleAtTime(bars, origin + minute), -1);
  assert.equal(candleAtTime(bars, origin + 2n * minute), 1);
  assert.equal(candleAtTime(bars, origin + 3n * minute), -1);
});

test('signed funding fractions share an explicit zero baseline without annualization', () => {
  for (const rates of [['0.0000125'], ['-0.0000125'], ['-0.0000125', '0.000025']]) {
    const scale = candlePriceScale(rates.map(rate => ({ open: '0', high: rate, low: rate, close: rate })));
    const zero = scale.fraction('0');
    assert.ok(zero > 0 && zero < 1);
    for (const rate of rates) {
      const fraction = scale.fraction(rate);
      assert.ok(fraction > 0 && fraction < 1);
      assert.equal(fraction > zero, !rate.startsWith('-'));
    }
  }
});
