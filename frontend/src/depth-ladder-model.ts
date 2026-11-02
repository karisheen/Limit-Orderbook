import type { GroupedLevel } from './api';

// Bounded decimal projection, like the chart's local-origin projector. Economic
// strings remain native; Number is used only after a bounded pixel ratio exists.
function parts(text: string): { coefficient: bigint; scale: number } {
  const match = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(text);
  if (!match || text.length > 2048) throw new Error('Unsupported depth coordinate.');
  const fraction = match[3] ?? '';
  const scale = fraction.length - Number(match[4] ?? 0);
  if (!Number.isSafeInteger(scale) || Math.abs(scale) > 1000) throw new Error('Depth coordinate precision exceeds the bounded renderer.');
  return { coefficient: BigInt(`${match[1] === '-' ? '-' : ''}${match[2]}${fraction}`), scale };
}

function aligned(left: string, right: string): [bigint, bigint] {
  const a = parts(left), b = parts(right), scale = Math.max(a.scale, b.scale);
  return [a.coefficient * 10n ** BigInt(scale - a.scale), b.coefficient * 10n ** BigInt(scale - b.scale)];
}

export function compareDepth(left: string, right: string): number {
  const [a, b] = aligned(left, right);
  return a === b ? 0 : a > b ? 1 : -1;
}

export function depthFraction(value: string, denominator: string): number | null {
  try {
    const [amount, maximum] = aligned(value, denominator);
    if (maximum <= 0n || amount < 0n) return null;
    if (amount >= maximum) return 1;
    return Number(amount * 1_000_000n / maximum) / 1_000_000;
  } catch { return null; }
}

export function depthMaximum(values: string[]): string | null {
  let maximum: string | null = null;
  for (const value of values) {
    try {
      if (compareDepth(value, '0') >= 0 && (maximum === null || compareDepth(value, maximum) > 0)) maximum = value;
    } catch { /* Unprojectable values retain their native text, without a bar. */ }
  }
  return maximum;
}

export const depthKey = (row: GroupedLevel): string => `${row.side}:${row.price}`;
export const depthQuantity = (row: GroupedLevel, unit: 'base' | 'quote'): string => unit === 'base' ? row.base_quantity : row.quote_notional;
export type DepthChange = 'addition' | 'removal';

/** Only compare observed quantities at the same side + price. A missing bucket
 * may have left the requested scope; absence is never evidence of removal. */
export function depthChanges(previous: GroupedLevel[], current: GroupedLevel[], unit: 'base' | 'quote'): Map<string, DepthChange> {
  const before = new Map(previous.map(row => [depthKey(row), depthQuantity(row, unit)]));
  const changes = new Map<string, DepthChange>();
  for (const row of current) {
    const old = before.get(depthKey(row));
    if (old === undefined) continue;
    try {
      const direction = compareDepth(depthQuantity(row, unit), old);
      if (direction !== 0) changes.set(depthKey(row), direction > 0 ? 'addition' : 'removal');
    } catch { /* No change claim when an exact comparison is unavailable. */ }
  }
  return changes;
}

// Invisible trailing glyphs align decimal points while Value retains the exact
// original string/title. Long or scientific strings are never rounded to fit.
export function decimalPlaces(value: string): number {
  return value.length <= 22 && /^[+-]?\d+(?:\.\d*)?$/.test(value) ? (value.split('.')[1]?.length ?? 0) : 0;
}
export function decimalPadding(value: string, places: number): string {
  if (value.length > 22 || !/^[+-]?\d+(?:\.\d*)?$/.test(value) || places === 0) return '';
  return `${value.includes('.') ? '' : '.'}${'0'.repeat(Math.max(0, places - decimalPlaces(value)))}`;
}
