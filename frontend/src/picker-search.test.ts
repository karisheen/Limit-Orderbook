import { test } from 'node:test';
import assert from 'node:assert/strict';
import { searchPickerGroups } from './picker-search.ts';

const catalog = [
  { value: 'coinbase:BTC-USD', label: 'BTC/USD', sub: 'Bitcoin · Coinbase', keywords: 'BTC-USD coinbase', group: 'USD · Spot' },
  { value: 'kraken:ETH/USD', label: 'ETH/USD', sub: 'Ethereum · Kraken', keywords: 'XETHZUSD kraken', group: 'USD · Spot' },
  { value: 'binance:BTCUSDT', label: 'BTC/USDT', sub: 'Bitcoin · Binance', keywords: 'BTCUSDT binance', group: 'USDT · Spot' },
  { value: 'bybit:ETHUSDT', label: 'ETH/USDT', sub: 'Ethereum · Bybit', keywords: 'ETHUSDT bybit', group: 'USDT · Spot', disabled: true },
];

test('separator and case variants find the same native symbol without rewriting identity', () => {
  for (const query of ['BTCUSDT', 'btc/usdt', 'btc-usdt', 'BTC_USDT', ' btc / usdt ']) {
    const groups = searchPickerGroups(catalog, query);
    assert.equal(groups.length, 1);
    assert.equal(groups[0].label, 'USDT · Spot');
    assert.deepEqual(groups[0].options, [catalog[2]]);
    assert.equal(groups[0].options[0], catalog[2]);
  }
});

test('venue and native symbol metadata combine with symbol terms', () => {
  assert.deepEqual(searchPickerGroups(catalog, 'coinbase btc').flatMap(group => group.options), [catalog[0]]);
  assert.deepEqual(searchPickerGroups(catalog, 'XETHZUSD').flatMap(group => group.options), [catalog[1]]);
  assert.deepEqual(searchPickerGroups(catalog, 'BTC bybit'), []);
  assert.deepEqual(searchPickerGroups(catalog, 'AAPL'), []);
});

test('exact symbol precedes prefix matches while quote groups retain contiguous keyboard indexes', () => {
  const groups = searchPickerGroups([catalog[2], catalog[0], catalog[1]], 'btcusd');
  assert.deepEqual(groups.map(group => [group.label, group.start]), [['USD · Spot', 0], ['USDT · Spot', 1]]);
  assert.deepEqual(groups.flatMap(group => group.options.map(option => option.value)), [catalog[0].value, catalog[2].value]);
});

test('empty search groups the native catalog stably and preserves disabled availability', () => {
  const groups = searchPickerGroups([catalog[0], catalog[2], catalog[1], catalog[3]], '  ');
  assert.deepEqual(groups.map(group => [group.label, group.start]), [['USD · Spot', 0], ['USDT · Spot', 2]]);
  assert.deepEqual(groups.flatMap(group => group.options), catalog);
  assert.equal(groups[1].options[1].disabled, true);
  assert.deepEqual(searchPickerGroups(catalog, 'ethusdt')[0].options, [catalog[3]]);
  assert.deepEqual(searchPickerGroups([], 'btc'), []);
});

test('ungrouped pickers retain their option order and original objects', () => {
  const options = [{ label: 'Indicative aggregate', value: 'all-spot' }, { label: 'Coinbase', value: 'coinbase' }];
  assert.deepEqual(searchPickerGroups(options, ''), [{ label: '', start: 0, options }]);
  assert.equal(searchPickerGroups(options, 'coinbase')[0].options[0], options[1]);
});
