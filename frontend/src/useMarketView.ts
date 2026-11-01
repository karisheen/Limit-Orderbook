import { useEffect, useRef, useState } from 'react';
import type { Frame, GroupedLadder, LadderInput, VisualGrid, VisualizationInput } from './api';
import { errorMessage } from './api';
import type { WorkspacePreferences } from './workspace';
import { mergeVisualGrid } from './workspace';

interface QueryState<T> { data: T | null; error: string | null; loading: boolean; identity: string; receivedAtMs?: number }
export function useMarketView({ frame, connected, preferences: p, recenter, visualization, ladder }: {
  frame: Frame | null; connected: boolean; preferences: WorkspacePreferences; recenter: number;
  visualization: (input: VisualizationInput, signal: AbortSignal, onReceive?: (receivedAtMs: number) => void) => Promise<VisualGrid>;
  ladder: (input: LadderInput, signal: AbortSignal) => Promise<GroupedLadder>;
}) {
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState === 'visible');
  const identity = frame ? `${frame.session_id}:${frame.generation}:${frame.selection.instrument_id}` : '';
  const available = connected && !!frame && frame.status !== 'seeking' && documentVisible;
  const [chart, setChart] = useState<QueryState<VisualGrid>>({ data: null, error: null, loading: false, identity: '' });
  const [dom, setDom] = useState<QueryState<GroupedLadder>>({ data: null, error: null, loading: false, identity: '' });
  const chartRef = useRef<VisualGrid | null>(null);
  const domRef = useRef<GroupedLadder | null>(null);
  const lastChartConfig = useRef('');
  const lastDomConfig = useRef('');
  const centered = useRef(0);
  const latest = useRef(frame);
  latest.current = frame;
  const rangeStart = p.range?.start_ns, rangeEnd = p.range?.end_ns;
  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', updateVisibility);
    return () => document.removeEventListener('visibilitychange', updateVisibility);
  }, []);
  useEffect(() => {
    chartRef.current = null;
    setChart({ data: null, error: null, loading: false, identity });
  }, [identity]);
  useEffect(() => {
    if (!available) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const config = JSON.stringify([identity, p.follow, p.priceStep, p.bucket, p.priceOffset, p.rangeSeconds, rangeStart, rangeEnd, recenter]);
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
      const times = [evidence?.book?.asof.receive_mono_ns, ...(evidence?.analytics?.metrics.map(metric => metric.asof?.receive_mono_ns) ?? []), ...(evidence?.analytics?.tape.map(print => print.asof.receive_mono_ns) ?? [])];
      const end = times.reduce<bigint>((highwater, time) => time && BigInt(time) > highwater ? BigInt(time) : highwater, 0n);
      const width = BigInt(p.rangeSeconds) * 1_000_000_000n;
      const range = p.paused && chartRef.current ? { start_ns: chartRef.current.start_ns, end_ns: chartRef.current.end_ns }
        : !p.follow && rangeStart && rangeEnd ? { start_ns: rangeStart, end_ns: rangeEnd }
          : p.rangeSeconds !== 180 && end > 0n ? { start_ns: (end > width ? end - width : 0n).toString(), end_ns: end.toString() } : {};
      try {
        let receivedAtMs = performance.now();
        const visibleWidth = range.start_ns && range.end_ns ? BigInt(range.end_ns) - BigInt(range.start_ns) : width;
        const bucket = BigInt(p.bucket);
        const maxColumns = Math.max(1, Math.min(600, Number((visibleWidth + bucket - 1n) / bucket) + 1));
        const next = await visualization({ ...range, ...(p.priceStep ? { price_step: p.priceStep } : {}), bucket_ns: p.bucket,
          price_offset_rows: p.priceOffset, rows: 128, max_columns: maxColumns, recenter: recenter !== centered.current,
          ...(after ? { after_column: after } : {}) }, controller.signal, time => { receivedAtMs = time; });
        if (controller.signal.aborted) return;
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
  }, [identity, available, p.paused, p.follow, p.priceStep, p.bucket, p.priceOffset, p.rangeSeconds, rangeStart, rangeEnd, recenter, visualization]);
  useEffect(() => {
    domRef.current = null;
    setDom({ data: null, error: null, loading: false, identity });
  }, [identity]);
  useEffect(() => {
    if (!available || !p.rightOpen) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const config = JSON.stringify([identity, p.domRows, p.priceStep]);
    const refresh = async () => {
      if (controller.signal.aborted) return;
      if ((p.domFrozen || p.paused) && domRef.current && lastDomConfig.current === config) {
        setDom(previous => ({ ...previous, loading: false }));
        return;
      }
      setDom(previous => ({ ...previous, loading: true, identity }));
      try {
        const data = await ladder({ rows: p.domRows, ...(p.priceStep ? { price_step: p.priceStep } : {}) }, controller.signal);
        if (!controller.signal.aborted) {
          domRef.current = data;
          lastDomConfig.current = config;
          setDom({ data, loading: false, error: null, identity });
        }
      } catch (cause) {
        if (!controller.signal.aborted) setDom(previous => ({ ...previous, loading: false, error: errorMessage(cause), identity }));
      } finally {
        if (!controller.signal.aborted && !p.domFrozen && !p.paused) timer = window.setTimeout(() => { void refresh(); }, 750);
      }
    };
    // Keep an existing DOM snapshot on freeze; settings changes deliberately request a new grouped view.
    timer = window.setTimeout(() => { void refresh(); }, 120);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [identity, available, p.rightOpen, p.domFrozen, p.paused, p.domRows, p.priceStep, ladder]);
  return {
    chart: { ...chart, data: chart.identity === identity ? chart.data : null },
    dom: { ...dom, data: dom.identity === identity ? dom.data : null },
  };
}
