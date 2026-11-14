import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { formatCompact } from './orderflow/lib/format.ts';

for (const timezone of ['UTC', 'America/Chicago', 'Asia/Kolkata']) {
  test(`tape and study clocks stay aligned with UTC candles in ${timezone}`, () => {
    const result = spawnSync(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', `
      import { formatClock, formatHM } from './src/orderflow/lib/format.ts';
      const times = ['2026-11-01T06:59:59.999Z', '2026-11-01T07:00:00.001Z', '2026-11-14T08:12:34.701Z'];
      process.stdout.write(JSON.stringify(times.map(value => {
        const stamp = Date.parse(value);
        return [formatClock(stamp), formatClock(stamp, true), formatHM(stamp)];
      })));
    `], { encoding: 'utf8', env: { ...process.env, TZ: timezone } });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), [
      ['06:59:59', '06:59:59.999', '06:59'],
      ['07:00:00', '07:00:00.001', '07:00'],
      ['08:12:34', '08:12:34.701', '08:12'],
    ]);
  });
}

test('small nonzero quantities remain nonzero and signed in compact displays', () => {
  for (const value of [0.00001, -0.00001, 1e-9, -1e-9, 0.00004998]) {
    const displayed = Number(formatCompact(value));
    assert.equal(Math.sign(displayed), Math.sign(value));
    assert.ok(Math.abs((displayed - value) / value) < 0.05);
  }
  assert.equal(Number(formatCompact(0)), 0);
});
