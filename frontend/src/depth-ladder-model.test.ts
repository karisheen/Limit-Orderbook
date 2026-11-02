import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { GroupedLevel } from './api';
import { compareDepth, decimalPadding, depthChanges, depthFraction, depthMaximum } from './depth-ladder-model.ts';

const row = (side: 'bid' | 'ask', price: string, quantity: string, quote = quantity): GroupedLevel => ({ side, price, base_quantity: quantity, cumulative_base: quantity, quote_notional: quote, cumulative_quote: quote });

test('depth coordinates retain exact tiny decimals and large integer distinctions', () => {
  assert.equal(compareDepth('9007199254740993.00000002', '9007199254740993.00000001'), 1);
  assert.equal(compareDepth('1E-30', '0.000000000000000000000000000001'), 0);
  assert.equal(depthMaximum(['9007199254740993.00000001', '9007199254740993.00000002']), '9007199254740993.00000002');
  assert.equal(depthFraction('1E-300', '4E-300'), 0.25);
  assert.equal(depthFraction('900719925474099300000000000', '1801439850948198600000000000'), 0.5);
  assert.equal(depthFraction('1E999', '1E-999'), 1);
  assert.equal(depthFraction('0', '4'), 0);
  assert.equal(depthFraction('4', '0'), null);
  assert.equal(depthFraction('1E1001', '4'), null);
});

test('shared cap uses both sides without changing or rounding the native denominator', () => {
  const cap = depthMaximum(['1.00000000000000000001', '4.00000000000000000002']);
  assert.equal(cap, '4.00000000000000000002');
  assert.equal(depthFraction('8', cap!), 1);
  assert.equal(cap, '4.00000000000000000002');
  assert.equal(depthMaximum([]), null);
});

test('size changes compare only the same side and exact price, never scope entry or exit', () => {
  const previous = [row('bid', '100', '9007199254740993.00000001'), row('bid', '99', '8'), row('ask', '101', '6')];
  const next = [row('bid', '100', '9007199254740993.00000002'), row('ask', '101', '5'), row('ask', '99', '9'), row('bid', '98', '20')];
  assert.deepEqual([...depthChanges(previous, next, 'base')], [['bid:100', 'addition'], ['ask:101', 'removal']]);
  assert.equal(depthChanges([], next, 'base').size, 0);
  assert.equal(depthChanges(previous, [], 'base').size, 0);
  assert.equal(depthChanges([row('bid', '100', '1.0')], [row('bid', '100', '1.00')], 'base').size, 0);
});

test('quote comparison uses native notional rather than browser price multiplication', () => {
  assert.deepEqual([...depthChanges([row('bid', '100', '2', '201')], [row('bid', '100', '2', '202')], 'quote')], [['bid:100', 'addition']]);
  assert.equal(depthChanges([row('bid', '100', '2', '201')], [row('bid', '100', '2', '202')], 'base').size, 0);
});

test('decimal alignment pads glyphs without changing or rounding native text', () => {
  assert.equal(decimalPadding('9007199254740993.01', 4), '00');
  assert.equal(decimalPadding('12', 3), '.000');
  assert.equal(decimalPadding('12.123', 3), '');
  assert.equal(decimalPadding('1E-30', 3), '');
});
