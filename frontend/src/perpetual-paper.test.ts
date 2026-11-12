import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Frame, Instrument } from './api.ts';
import type { PaperCapability } from './workflow-api.ts';
import { closeIntent, emptyPerpetualDraft, matchingPaperCapability, perpetualModels } from './perpetual-paper.ts';

const instrument = { instrument_id: 'native:perpetual:BTC', metadata_version: 'pinned-1', metadata_status: 'verified' } as Instrument;
const frame: Pick<Frame, 'selection'> = { selection: { instrument_id: instrument.instrument_id, metadata_version: 'pinned-1', metadata_status: 'verified' } };
const capability: PaperCapability = { instrument_id: instrument.instrument_id, metadata_version: 'pinned-1', market_type: 'perpetual', quantity_unit: 'contracts', settlement_asset_id: 'BTC', supported: true, reasons: [], native_order_constraints: null };

test('paper capability requires exact selected identity and pinned verified metadata; native support remains authoritative', () => {
  assert.equal(matchingPaperCapability(frame, instrument, capability), capability);
  for (const changed of [{ ...capability, metadata_version: 'latest-2' }, { ...capability, instrument_id: 'other' }, undefined]) {
    assert.equal(matchingPaperCapability(frame, instrument, changed), null);
  }
  assert.equal(matchingPaperCapability(frame, { ...instrument, metadata_version: 'latest-2' }, capability), null);
  assert.equal(matchingPaperCapability({ selection: { ...frame.selection, metadata_status: 'streaming_only' } }, instrument, capability), null);
  assert.equal(matchingPaperCapability(null, instrument, capability), null);
  const unsupported = { ...capability, supported: false, reasons: ['unsupported_perpetual_or_contract_economics'] };
  assert.equal(matchingPaperCapability(frame, instrument, unsupported), unsupported);
  const spot = { ...instrument, market_type: 'spot' };
  const spotCapability = { ...capability, market_type: 'spot', quantity_unit: 'base' };
  assert.equal(matchingPaperCapability(frame, spot, spotCapability), spotCapability);
});

test('risk and funding drafts have no financial defaults and preserve decimal and epoch strings exactly', () => {
  const empty = emptyPerpetualDraft();
  assert.equal(empty.leverage, '');
  assert.equal(empty.funding_mode, '');
  const draft = { ...empty, instrument_id: instrument.instrument_id, leverage: '2.000000000000000001', maintenance_margin_rate: '0.01', liquidation_fee_rate: '0', risk_provenance: 'explicit model', risk_asof: '1700000000000000001', funding_provenance: 'explicit schedule', funding_asof: '1700000000000000001' };
  assert.throws(() => perpetualModels([draft]), /Explicitly select/);
  const periodic = perpetualModels([{ ...draft, funding_mode: 'periodic', rate: '-0.000000000000000001', interval_ns: '3600000000000', first_wall_ns: '1700003600000000001', events: [{ wall_ns: '1', rate: '0.1' }] }]);
  assert.equal(periodic.risk_models[0].leverage, draft.leverage);
  assert.deepEqual(periodic.funding_models[0], { instrument_id: instrument.instrument_id, mode: 'periodic', provenance: draft.funding_provenance, asof_wall_ns: draft.funding_asof, rate: '-0.000000000000000001', interval_ns: '3600000000000', first_wall_ns: '1700003600000000001' });
  const none = perpetualModels([{ ...draft, funding_mode: 'none', rate: '0.3', interval_ns: '123', first_wall_ns: '456' }]);
  assert.deepEqual(none.funding_models[0], { instrument_id: instrument.instrument_id, mode: 'none', provenance: draft.funding_provenance, asof_wall_ns: draft.funding_asof });
  const events = [{ wall_ns: '1700003600000000001', rate: '-0.0001' }];
  const dated = perpetualModels([{ ...draft, funding_mode: 'events', events, rate: '0.3' }]);
  assert.deepEqual(dated.funding_models[0].events, events);
  assert.notEqual(dated.funding_models[0].events, events);
  assert.equal(dated.funding_models[0].rate, undefined);
  assert.deepEqual(perpetualModels([]), { risk_models: [], funding_models: [] });
  assert.throws(() => perpetualModels(Array.from({ length: 65 }, () => draft)), /64/);
  assert.throws(() => perpetualModels([{ ...draft, funding_mode: 'none' }, { ...draft, funding_mode: 'none' }]), /unique/);
  assert.throws(() => perpetualModels([{ ...draft, funding_mode: 'events', events: Array.from({ length: 10001 }, () => events[0]) }]), /10000/);
});

test('close preparation preserves exact native magnitude and always reduces rather than flipping', () => {
  assert.deepEqual(closeIntent({ quantity: '12345678901234567890.000000000000000001' }), { side: 'sell', quantity: '12345678901234567890.000000000000000001', reduce_only: true });
  assert.deepEqual(closeIntent({ quantity: '-0.000000000000000001' }), { side: 'buy', quantity: '0.000000000000000001', reduce_only: true });
  for (const quantity of ['0', '-0.000', '', 'NaN', 'Infinity']) assert.equal(closeIntent({ quantity }), null);
});
