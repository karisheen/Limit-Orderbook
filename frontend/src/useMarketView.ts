import { useEffect, useRef, useState } from 'react';
import type { Frame, GroupedLadder, LadderInput, VisualGrid, VisualizationInput } from './api';
import { errorMessage } from './api';
import type { WorkspacePreferences } from './workspace';
import { mergeVisualGrid } from './workspace';
import { sourceFrontier } from './data-freshness';

interface QueryState<T> { data: T | null; error: string | null; loading: boolean; identity: string; receivedAtMs?: number }
export function useMarketView({ frame, connected, preferences: p, recenter, visualization, ladder, chartEnabled = true, domEnabled = true }: {
  frame: Frame | null; connected: boolean; preferences: WorkspacePreferences; recenter: number;
  chartEnabled?: boolean; domEnabled?: boolean;
  visualization: (input: VisualizationInput, signal: AbortSignal, onReceive?: (receivedAtMs: number) => void) => Promise<VisualGrid>;
  ladder: (input: LadderInput, signal: AbortSignal) => Promise<GroupedLadder>;
}) {
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState === 'visible');
  const identity = frame ? `${frame.session_id}:${frame.generation}:${frame.source.mode}:${frame.source.id}:${frame.selection.instrument_id}` : '';
  const available = connected && !!frame && !['seeking', 'unavailable', 'error', 'loading'].includes(frame.status) && documentVisible;
  const [chart, setChart] = useState<QueryState<VisualGrid>>({ data: null, error: null, loading: false, identity: '' });
  const [dom, setDom] = useState<QueryState<GroupedLadder>>({ data: null, error: null, loading: false, identity: '' });
  const chartRef = useRef<VisualGrid | null>(null);
  const domRef = useRef<GroupedLadder | null>(null);
  const lastChartConfig = useRef('');
  const lastDomConfig = useRef('');
  const centered = useRef(0);
  // Native origin and quantum belong to this pane, never to the session's last query.
  const origin = useRef<{ priceMin: string; step: string; offset: number; requestedStep: string; rows: number } | null>(null);
  const latest = useRef(frame);
  latest.current = frame;
  const rangeStart = p.range?.start_ns, rangeEnd = p.range?.end_ns;
  // Detailed footprint crops only the painted rows; native requests retain their 32-row floor.
  const rows = p.mode === 'footprint' || p.mode === 'profile' ? 32 : 128;
  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', updateVisibility);
    return () => document.removeEventListener('visibilitychange', updateVisibility);
  }, []);
  useEffect(() => {
    chartRef.current = null;
    lastChartConfig.current = '';
    centered.current = 0;
    origin.current = null;
    setChart({ data: null, error: null, loading: false, identity });
  }, [identity]);
  useEffect(() => {
    if (!available || !chartEnabled) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const config = JSON.stringify([identity, p.follow, p.priceStep, p.bucket, p.priceOffset, p.rangeSeconds, rangeStart, rangeEnd, recenter, rows]);
    let after: string | undefined;
    const refresh = async () => {
      if (controller.signal.aborted) return;
      // Freeze holds the already rendered evidence, not the replay/recording clock.
      if (p.paused && chartRef.current && lastChartConfig.current === config) {
        setChart(previous => ({ ...previous, loading: false }));
        return;
      }
      setChart(previous => ({ ...previous, loading: true, identity }));
      const evidence = latest.current;
      const end = sourceFrontier(evidence, chartRef.current?.clock_id);
      const width = BigInt(p.rangeSeconds) * 1_000_000_000n;
      const range = !p.follow && rangeStart && rangeEnd ? { start_ns: rangeStart, end_ns: rangeEnd }
        : p.paused && chartRef.current ? { start_ns: chartRef.current.start_ns, end_ns: chartRef.current.end_ns }
          : p.rangeSeconds !== 180 && end !== null && end > 0n ? { start_ns: (end > width ? end - width : 0n).toString(), end_ns: end.toString() } : {};
      try {
        let receivedAtMs = performance.now();
        const visibleWidth = range.start_ns && range.end_ns ? BigInt(range.end_ns) - BigInt(range.start_ns) : width;
        const bucket = BigInt(p.bucket);
        const maxColumns = Math.max(1, Math.min(600, Number((visibleWidth + bucket - 1n) / bucket) + 1));
        const saved = origin.current;
        const resetOrigin = !saved || recenter !== centered.current || saved.requestedStep !== p.priceStep || saved.rows !== rows;
        const price = resetOrigin
          ? { ...(p.priceStep ? { price_step: p.priceStep } : {}), price_offset_rows: p.priceOffset, recenter: true }
          : { price_min: saved.priceMin, price_step: saved.step, price_offset_rows: p.priceOffset - saved.offset };
        const next = await visualization({ ...range, ...price, bucket_ns: p.bucket, rows, max_columns: maxColumns,
          ...(!resetOrigin && after ? { after_column: after } : {}) }, controller.signal, time => { receivedAtMs = time; });
        if (controller.signal.aborted) return;
        if (resetOrigin) {
          // Keep the returned lower bound as the base; subtract the initial UI offset
          // on later requests instead of adding it again to every returned snapshot.
          // Depth sampling can omit every column while native trade flow is
          // displayable. Native fits against a display-ready two-sided book,
          // never trades alone; use its frame health gate, not bootstrap fallback.
          const book = evidence?.book;
          const observedBook = evidence?.health.ready === true && book?.instrument_id === next.instrument_id
            && book.asof.clock_id === next.clock_id;
          const displayableFlow = next.footprints.length > 0 || next.profile.length > 0
            || next.columns.some(column => column.candle?.eligible);
          origin.current = next.columns.some(column => column.eligible) || (observedBook && displayableFlow)
            ? { priceMin: next.price_min, step: next.price_step, offset: p.priceOffset, requestedStep: p.priceStep, rows }
            : null;
        }
        centered.current = recenter;
        lastChartConfig.current = config;
        after = next.next_after ?? undefined;
        const data = mergeVisualGrid(chartRef.current, next);
        chartRef.current = data;
        setChart({ data, loading: false, error: null, identity, receivedAtMs });
      } catch (cause) {
        if (!controller.signal.aborted) setChart(previous => ({ ...previous, loading: false, error: errorMessage(cause), identity }));
      } finally {
        if (!controller.signal.aborted && !p.paused) timer = window.setTimeout(() => { void refresh(); }, 1000);
      }
    };
    timer = window.setTimeout(() => { void refresh(); }, 120);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [identity, available, chartEnabled, p.paused, p.follow, p.priceStep, p.bucket, p.priceOffset, p.rangeSeconds, rangeStart, rangeEnd, recenter, rows, visualization]);
  useEffect(() => {
    domRef.current = null;
    lastDomConfig.current = '';
    setDom({ data: null, error: null, loading: false, identity });
  }, [identity]);
  useEffect(() => {
    if (!available || !domEnabled || !p.rightOpen) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const config = JSON.stringify([identity, p.domRows, p.domPriceStep]);
    const refresh = async () => {
      if (controller.signal.aborted) return;
      if (p.domFrozen && domRef.current && lastDomConfig.current === config) {
        setDom(previous => ({ ...previous, loading: false }));
        return;
      }
      setDom(previous => ({ ...previous, loading: true, identity }));
      try {
        const data = await ladder({ rows: p.domRows, ...(p.domPriceStep ? { price_step: p.domPriceStep } : {}) }, controller.signal);
        if (!controller.signal.aborted) {
          domRef.current = data;
          lastDomConfig.current = config;
          setDom({ data, loading: false, error: null, identity });
        }
      } catch (cause) {
        if (!controller.signal.aborted) setDom(previous => ({ ...previous, loading: false, error: errorMessage(cause), identity }));
      } finally {
        if (!controller.signal.aborted && !p.domFrozen) timer = window.setTimeout(() => { void refresh(); }, 750);
      }
    };
    // Keep an existing DOM snapshot on freeze; settings changes deliberately request a new grouped view.
    timer = window.setTimeout(() => { void refresh(); }, 120);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [identity, available, domEnabled, p.rightOpen, p.domFrozen, p.domRows, p.domPriceStep, ladder]);
  return {
    chart: { ...chart, loading: chart.loading && available && chartEnabled, data: chart.identity === identity ? chart.data : null },
    dom: { ...dom, loading: dom.loading && available && domEnabled && p.rightOpen, data: dom.identity === identity ? dom.data : null },
  };
}
