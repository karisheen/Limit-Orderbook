import { useEffect, useMemo, useState } from 'react';
import type { RefObject } from 'react';
import { useNativeTerminal } from './integration/NativeContext';
import { useSessionStore } from './orderflow/lib/session/SessionContext';
import { useMarketView } from './useMarketView';
import { sourceFrontier } from './data-freshness';
import { footprintLiquidityWindow, projectFootprintLiquidity } from './footprint-layers';
import type { FootprintCandle, NormalizedTrade } from './orderflow/lib/types/market';
import type { LiquidityProjection } from './footprint-layers';

export interface FootprintLiquidityState extends LiquidityProjection {
  observeHost: (host: HTMLElement | null) => void;
  visible: boolean;
  active: boolean;
  error: string | null;
  loading: boolean;
  unit: string;
  venue: string;
  primaryOnly: boolean;
  sampling: string;
  trades: readonly NormalizedTrade[];
  instrumentIds: readonly string[];
}

/** Native requests are owned by the mounted, visible footprint, not its toolbar toggle alone. */
export function useFootprintLiquidity(enabled: boolean, candles: readonly FootprintCandle[]): FootprintLiquidityState {
  const { terminal, preferences, instrument, usable } = useNativeTerminal();
  const trades = useSessionStore(state => state.recentTrades);
  const status = useSessionStore(state => state.displayStatus);
  const [visible, setVisible] = useState(false);
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!host) return;
    let intersects = false;
    const update = () => setVisible(intersects && document.visibilityState === 'visible' && host.getClientRects().length > 0);
    const observer = new IntersectionObserver(entries => { intersects = entries.some(entry => entry.isIntersecting); update(); });
    observer.observe(host);
    document.addEventListener('visibilitychange', update);
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', update); };
  }, [host]);
  const primary = instrument?.instrument_id ?? '';
  const cohortMatches = status.primaryInstrumentId === primary && status.instrumentIds.includes(primary);
  const active = enabled && visible && usable && cohortMatches;
  // The native range ceiling is 30 minutes even with coarser sampling.
  // Older or unobserved spans stay empty, never stretched backward.
  const { rangeSeconds, bucket, limited } = footprintLiquidityWindow(candles);
  const queryPreferences = useMemo(() => ({ ...preferences, mode: 'heatmap' as const, paused: false, follow: true,
    range: null, followSpanNs: null, rangeSeconds, bucket,
    priceStep: '', priceOffset: 0 }), [preferences, rangeSeconds, bucket]);
  const { chart } = useMarketView({ frame: terminal.frame, connected: active, preferences: queryPreferences,
    recenter: 0, visualization: terminal.visualization, ladder: terminal.ladder, chartEnabled: active, domEnabled: false });
  const grid = active && chart.data && sourceFrontier(terminal.frame, chart.data.clock_id) !== null ? chart.data : null;
  const projection = useMemo(() => projectFootprintLiquidity(grid, trades, terminal.frame?.generation ?? '', primary, status.instrumentIds), [grid, trades, terminal.frame?.generation, primary, status.instrumentIds]);
  return { ...projection, limited: projection.limited || limited, observeHost: setHost, visible, active, error: chart.error, loading: chart.loading,
    unit: grid?.quantity_unit ?? 'base', venue: instrument?.venue ?? 'primary', primaryOnly: status.instrumentIds.length > 1,
    sampling: grid?.sampling ?? 'sampled', trades, instrumentIds: visible && usable && cohortMatches ? status.instrumentIds : [] };
}
/** Keep the existing canvas ref and redraw scheduler authoritative. */
export function useFootprintLayerHost(ref: RefObject<HTMLDivElement | null>, observe: (host: HTMLElement | null) => void) {
  useEffect(() => { observe(ref.current); return () => observe(null); }, [ref, observe]);
}

export function FootprintLiquidity({ state, ceiling, opacity }: {
  state: FootprintLiquidityState; ceiling: number; opacity: number;
}) {
  return <span data-testid="footprint-liquidity-status" title="Historical native sampled resting depth, projected using the footprint's native receive-clock anchor; not executed volume or today's book.">
    <i className="lg" style={{ background: 'var(--buy)' }} /><i className="lg" style={{ background: 'var(--sell)' }} />
    Resting bids / asks · {state.venue}{state.primaryOnly ? ' primary-only (not cohort aggregate)' : ' primary'} · 0–{ceiling} {state.unit} · {Math.round(opacity * 100)}% opacity · {state.sampling} · Unknown spans blank
    {state.limited && ' · bounded/limited history'}
    {state.error ? ` · ${state.error}` : state.reason ? ` · ${state.loading ? 'Loading · ' : ''}${state.reason}` : ` · ${state.tiles.length} historical tiles`}
  </span>;
}
