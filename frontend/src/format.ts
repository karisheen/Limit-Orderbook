// Presentation only. Never feed a formatted/truncated value back into an order input.
export function exact(value: string | null | undefined): string {
  return value == null ? '—' : value;
}

export function compact(value: string | null | undefined, maxLength = 22): string {
  if (value == null) return '—';
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 1)}…`;
}

export function label(value: string): string { return value.replaceAll('_', ' '); }

/** Display-only venue names; keep native IDs in selections, keys and diagnostics. */
export function venueLabel(venue: string): string {
  switch (venue) {
    case 'binance':
    case 'binance-global': return 'Binance';
    case 'binance-us': return 'Binance.US';
    case 'bybit': return 'Bybit';
    case 'okx': return 'OKX';
    case 'bitget': return 'Bitget';
    case 'hyperliquid': return 'Hyperliquid';
    case 'deribit': return 'Deribit';
    case 'coinbase': return 'Coinbase';
    case 'kraken': return 'Kraken';
    case 'all-perps': return 'All perps';
    case 'all-spot': return 'All spot';
    default: return venue;
  }
}

export function timestamp(ns: string | null | undefined): string {
  if (!ns || !/^\d+$/.test(ns)) return '—';
  // BigInt division precedes Number conversion; only the bounded Date display is numeric.
  const millis = BigInt(ns) / 1_000_000n;
  if (millis > 8_640_000_000_000_000n) return ns;
  return new Date(Number(millis)).toISOString().slice(11, 23);
}

export function milliseconds(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)} ms`;
}
