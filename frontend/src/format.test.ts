import { test } from 'node:test';
import assert from 'node:assert/strict';
import { exact, compact, timestamp } from './format.ts';

test('economic values never pass through binary floating point', () => {
  const price = '9007199254740993.123456789012345678901234567890';
  assert.equal(exact(price), price);
  assert.equal(compact(price, 22), '9007199254740993.1234…');
  assert.equal(exact('0.00000000000000000000000000000000000001'), '0.00000000000000000000000000000000000001');
});

test('missing observations are not fabricated zero values', () => {
  assert.equal(exact(null), '—');
  assert.equal(exact(undefined), '—');
  assert.equal(exact('0'), '0');
  assert.equal(compact(null), '—');
});

test('nanosecond identities remain exact until bounded display-only date conversion', () => {
  assert.equal(timestamp('1735689600123456789'), '00:00:00.123');
  assert.equal(timestamp(null), '—');
  assert.equal(timestamp('9000000000000000000000000'), '9000000000000000000000000');
});
