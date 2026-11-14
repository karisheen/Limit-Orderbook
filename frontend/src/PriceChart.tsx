import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, PriceScaleMode, createChart } from 'lightweight-charts';
import type { IChartApi, ISeriesApi, LineData, HistogramData, LogicalRange, MouseEventParams, Time, UTCTimestamp } from 'lightweight-charts';
import type { Frame, Instrument } from './api';
import { useCandleHistory } from './candle-api';
import { candleAtTime, candleDate } from './candle-model';
import type { CandleSource, PriceCandle } from './candle-model';
import { venueLabel } from './format';
import { usePositioningHistory } from './positioning-api';
import { fundingIntervalLabel } from './positioning-model';
import { anchorLogicalRange, chartTime, exactPlotScale, latestLogicalRange, pixelAnchoredRange, priceSeriesData, seriesUpdate } from './price-series';
import type { ExactPlotScale, PriceSeriesData } from './price-series';
import type { PriceChartPreferences } from './workspace';
import './price-chart.css';

export interface PriceChartProps {
  frame: Frame | null; instrument?: Instrument; connected: boolean; active: boolean;
  interval: string;
  instrumentIds: readonly string[]; cohortReady: boolean;
  maximized: boolean; onMaximize: () => void;
  preferences: PriceChartPreferences; onPreferencesChange: (value: Partial<PriceChartPreferences>) => void;
}
interface Colors { background: string; text: string; grid: string; border: string; up: string; down: string; interest: string; positive: string; negative: string }
interface ContextPlot {
  interest: LineData<UTCTimestamp>[]; funding: HistogramData<UTCTimestamp>[];
  interestScale: ExactPlotScale; fundingScale: ExactPlotScale;
}
interface Surface {
  chart: IChartApi; price: ISeriesApi<'Candlestick'>; volume: ISeriesApi<'Histogram'>;
  interest?: ISeriesApi<'Line'>; funding?: ISeriesApi<'Histogram'>;
  data?: PriceSeriesData; context?: ContextPlot; times: number[]; changing: boolean; navigating: boolean;
  viewport?: { from: number; barSpacing: number };
}
const EMPTY_CANDLES: PriceCandle[] = [];
function themeColors(node: HTMLElement): Colors {
  const style = getComputedStyle(node);
  return {
    background: style.getPropertyValue('--bg-panel').trim(), text: style.getPropertyValue('--text-secondary').trim(),
    grid: style.getPropertyValue('--border-muted').trim(), border: style.getPropertyValue('--border').trim(),
    up: style.getPropertyValue('--buy').trim(), down: style.getPropertyValue('--sell').trim(),
    interest: style.getPropertyValue('--accent').trim(), positive: style.getPropertyValue('--warn').trim(),
    negative: style.getPropertyValue('--vwap').trim(),
  };
}

export function PriceChart({ frame, instrument, connected, active, interval, instrumentIds, cohortReady, maximized, onMaximize, preferences, onPreferencesChange }: PriceChartProps): ReactNode {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const [intersecting, setIntersecting] = useState(false);
  const [pixelRatio, setPixelRatio] = useState(() => window.devicePixelRatio || 1);
  const presenting = active && visible && intersecting;
  const plotOffscreen = active && visible && !intersecting;
  const source: CandleSource = preferences.source === 'auto' ? frame?.source.mode === 'live' ? 'venue_ohlcv' : 'session_trades' : preferences.source;
  const query = useCandleHistory(frame, interval, presenting, connected, instrumentIds, cohortReady, source);
  const data = query.data;
  const candles = data?.candles ?? EMPTY_CANDLES;
  const showPositioning = source === 'session_trades' && preferences.positioning;
  const positioning = usePositioningHistory(frame, interval, presenting && showPositioning && query.usable && !query.paused,
    data?.source === 'session_trades' ? data.source_identity : null);
  const positioningSeries = positioning.data?.series.find(series => series.relation === 'selected' && series.bars.length)
    ?? positioning.data?.series.find(series => series.bars.length);
  const plot = useRef<HTMLDivElement>(null);
  const surface = useRef<Surface | null>(null);
  const previousData = useRef<{ identity: string; data: PriceSeriesData } | null>(null);
  const helpId = useId();
  const plotId = useId();
  const [colors, setColors] = useState<Colors | null>(null);
  const [follow, setFollow] = useState(true);
  const following = useRef(true);
  const [cursor, setCursor] = useState<{ identity: string; time: number; keyboard: boolean } | null>(null);
  const retainedCursor = useRef(cursor);
  retainedCursor.current = cursor;
  const [tablePage, setTablePage] = useState(0);
  const callbacks = useRef({ loadOlder: query.loadOlder, hasMore: query.hasMore, olderLoading: query.olderLoading, rightOffset: preferences.rightOffset });
  callbacks.current = { loadOlder: query.loadOlder, hasMore: query.hasMore, olderLoading: query.olderLoading, rightOffset: preferences.rightOffset };
  const savedViewport = useRef<{ identity: string; times: number[]; range: { from: number; to: number }; barSpacing: number; follow: boolean;
    priceRange: { from: number; to: number } | null; priceScaleKey?: string; stretches: number[] } | null>(null);

  const prepared = useMemo(() => {
    if (!colors) return { value: null, error: null };
    try {
      return { value: priceSeriesData(candles, { log: preferences.scale === 'log', tick: instrument?.tick_size,
        previous: previousData.current?.identity === query.identity ? previousData.current.data : undefined,
        up: colors.up, down: colors.down }), error: null };
    } catch (error) { return { value: null, error: error instanceof Error ? error.message : 'Unsupported chart precision' }; }
  }, [candles, colors, preferences.scale, instrument?.tick_size, query.identity]);
  const context = useMemo(() => {
    if (!showPositioning || !positioningSeries || !colors || !candles.length || positioning.data?.time_basis !== 'receive_utc'
      || positioning.data.source_identity !== data?.source_identity) return { value: null, error: null };
    try {
      const first = BigInt(candles[0]!.start_ns); const end = BigInt(candles[candles.length - 1]!.end_ns);
      const bars = positioningSeries.bars.filter(bar => BigInt(bar.start_ns) >= first && BigInt(bar.start_ns) < end);
      const interestScale = exactPlotScale(bars.flatMap(bar => bar.open_interest ? [bar.open_interest.close] : []));
      const fundingScale = exactPlotScale(bars.flatMap(bar => bar.funding_rate === null ? [] : [bar.funding_rate]), { zero: true });
      const interest: LineData<UTCTimestamp>[] = []; const funding: HistogramData<UTCTimestamp>[] = [];
      for (const bar of bars) {
        const time = chartTime(bar.start_ns);
        if (bar.open_interest) interest.push({ time, value: interestScale.coordinate(bar.open_interest.close) });
        if (bar.funding_rate !== null) funding.push({ time, value: fundingScale.coordinate(bar.funding_rate),
          color: bar.funding_rate.startsWith('-') ? colors.negative : colors.positive });
      }
      return { value: { interest, funding, interestScale, fundingScale }, error: null };
    } catch (error) { return { value: null, error: error instanceof Error ? error.message : 'Unsupported positioning precision' }; }
  }, [showPositioning, positioningSeries, positioning.data, colors, candles, data?.source_identity]);

  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    const node = plot.current;
    if (!node) return;
    const observer = new IntersectionObserver(entries => {
      const entry = entries[entries.length - 1];
      if (entry) setIntersecting(entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let resolution: MediaQueryList;
    const update = () => {
      setPixelRatio(window.devicePixelRatio || 1);
      resolution?.removeEventListener('change', update);
      resolution = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      resolution.addEventListener('change', update);
    };
    update();
    window.addEventListener('resize', update);
    return () => {
      resolution.removeEventListener('change', update);
      window.removeEventListener('resize', update);
    };
  }, []);

  useEffect(() => {
    const node = plot.current;
    if (!node || !presenting) return;
    const palette = themeColors(node);
    setColors(palette);
    const saved = savedViewport.current?.identity === query.identity ? savedViewport.current : null;
    following.current = saved?.follow ?? true;
    setFollow(following.current);
    const chart = createChart(node, {
      width: Math.max(1, node.clientWidth), height: Math.max(1, node.clientHeight),
      layout: { background: { type: ColorType.Solid, color: palette.background }, textColor: palette.text,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11, attributionLogo: true,
        panes: { enableResize: true, separatorColor: palette.border, separatorHoverColor: palette.text } },
      grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: palette.border, minimumWidth: 80 },
      timeScale: { borderColor: palette.border, timeVisible: true, secondsVisible: false, barSpacing: saved?.barSpacing ?? 8, minBarSpacing: 2,
        maxBarSpacing: 36, rightOffset: callbacks.current.rightOffset, shiftVisibleRangeOnNewBar: false },
      localization: { locale: 'en-GB', timeFormatter: (time: Time) => typeof time === 'number' ? `${new Date(time * 1000).toISOString().replace('T', ' ').slice(0, 19)} UTC` : String(time) },
    });
    const price = chart.addSeries(CandlestickSeries, { upColor: palette.up, downColor: palette.down, wickUpColor: palette.up,
      wickDownColor: palette.down, borderVisible: false });
    const volume = chart.addSeries(HistogramSeries, { title: 'Volume', lastValueVisible: false, priceLineVisible: false }, 1);
    chart.panes()[0]!.setStretchFactor(5);
    chart.panes()[1]!.setStretchFactor(1);
    const current: Surface = { chart, price, volume, times: [], changing: false, navigating: false };
    surface.current = current;
    const rangeChanged = (range: LogicalRange | null) => {
      if (!range || current.changing || !current.times.length) return;
      const latest = current.times.length - 1;
      const nextFollow = range.to >= latest + callbacks.current.rightOffset - 1 && range.to <= latest + callbacks.current.rightOffset + 2;
      if (current.navigating) {
        following.current = nextFollow; setFollow(nextFollow);
        if (range.from < 15 && callbacks.current.hasMore && !callbacks.current.olderLoading) callbacks.current.loadOlder();
      }
    };
    const crosshair = (event: MouseEventParams) => {
      if (current.changing) return;
      const selectedCursor = retainedCursor.current;
      if (!event.sourceEvent && selectedCursor?.keyboard && selectedCursor.identity === query.identity) {
        // LWC reprojects its saved screen point during layout/data changes.
        // That is not pointer input: keep keyboard inspection at its exact bar,
        // and move the painted crosshair with the bar rather than the old pixel.
        const selected = current.data?.exact.get(selectedCursor.time);
        if (selected && current.data) {
          const value = current.data.priceScale.coordinate(selected.close);
          const x = chart.timeScale().timeToCoordinate(selectedCursor.time as UTCTimestamp);
          const y = price.priceToCoordinate(value);
          if (event.time !== selectedCursor.time || event.point?.x !== x || event.point?.y !== y) {
            chart.setCrosshairPosition(value, selectedCursor.time as UTCTimestamp, price);
          }
        }
        return;
      }
      if (!event.point || typeof event.time !== 'number') {
        if (!retainedCursor.current?.keyboard) setCursor(null);
        return;
      }
      setCursor({ identity: query.identity, time: event.time, keyboard: false });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(rangeChanged);
    chart.subscribeCrosshairMove(crosshair);
    let layoutQueued = false;
    const sizeChanged = () => {
      if (layoutQueued) return;
      layoutQueued = true;
      // LWC emits size changes before updating its time-scale model. Reconcile
      // afterward, including any further deferred price/volume-axis layouts.
      queueMicrotask(() => {
        layoutQueued = false;
        if (surface.current !== current || current.navigating || !current.viewport) return;
        const width = chart.timeScale().width();
        if (width > 0) chart.timeScale().setVisibleLogicalRange(pixelAnchoredRange(current.viewport.from, current.viewport.barSpacing, width));
      });
    };
    chart.timeScale().subscribeSizeChange(sizeChanged);
    let resizeFrame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        if (!node.clientWidth || !node.clientHeight) return;
        // Preserve detached candle pixels before LWC's deferred axis layout.
        const range = chart.timeScale().getVisibleLogicalRange();
        if (!following.current && range) {
          if (current.navigating || !current.viewport) current.viewport = { from: range.from, barSpacing: chart.timeScale().options().barSpacing };
          current.navigating = false;
        }
        chart.resize(node.clientWidth, node.clientHeight);
      });
    });
    observer.observe(node);
    const theme = new MutationObserver(() => {
      const next = themeColors(node);
      setColors(previous => previous && (Object.keys(next) as (keyof Colors)[]).every(key => next[key] === previous[key]) ? previous : next);
    });
    for (let ancestor: HTMLElement | null = node; ancestor; ancestor = ancestor.parentElement) theme.observe(ancestor, { attributes: true, attributeFilter: ['style', 'class', 'data-theme'] });
    return () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) savedViewport.current = { identity: query.identity, times: current.times, range, follow: following.current,
        barSpacing: chart.timeScale().options().barSpacing, priceRange: current.price.priceScale().getVisibleRange(),
        priceScaleKey: current.data?.priceScale.key, stretches: chart.panes().map(pane => pane.getStretchFactor()) };
      observer.disconnect(); theme.disconnect(); cancelAnimationFrame(resizeFrame);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(rangeChanged); chart.unsubscribeCrosshairMove(crosshair);
      chart.timeScale().unsubscribeSizeChange(sizeChanged);
      chart.remove(); surface.current = null;
    };
  }, [presenting, query.identity, pixelRatio]);

  useEffect(() => {
    const current = surface.current;
    if (!current || !colors || !presenting) return;
    const { chart, price, volume } = current;
    const saved = savedViewport.current?.identity === query.identity ? savedViewport.current : null;
    const oldSpacing = current.viewport && !current.navigating ? current.viewport.barSpacing
      : current.times.length ? chart.timeScale().options().barSpacing : saved?.barSpacing ?? null;
    const oldRange = current.viewport && !current.navigating
      ? pixelAnchoredRange(current.viewport.from, current.viewport.barSpacing, chart.timeScale().width())
      : chart.timeScale().getVisibleLogicalRange() ?? saved?.range;
    const oldTimes = current.times.length ? current.times : saved?.times ?? [];
    current.navigating = false;
    current.changing = true;
    current.viewport = undefined;
    chart.applyOptions({
      layout: { background: { type: ColorType.Solid, color: colors.background }, textColor: colors.text,
        panes: { separatorColor: colors.border, separatorHoverColor: colors.text } },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.border }, timeScale: { borderColor: colors.border },
    });
    price.applyOptions({ upColor: colors.up, downColor: colors.down, wickUpColor: colors.up, wickDownColor: colors.down });
    const next = prepared.value;
    if (!next) {
      price.setData([]); volume.setData([]);
      if (current.interest) { chart.removeSeries(current.interest); current.interest = undefined; }
      if (current.funding) { chart.removeSeries(current.funding); current.funding = undefined; }
      current.data = undefined; current.context = undefined; current.times = []; current.changing = false;
      plot.current?.setAttribute('data-chart-pane-count', String(chart.panes().length));
      plot.current?.setAttribute('data-positioning-pane-count', String(Math.max(0, chart.panes().length - 2)));
      return;
    }
    price.applyOptions({ priceFormat: { type: 'custom', formatter: next.priceScale.format, minMove: next.priceScale.minMove, base: next.priceScale.base } });
    volume.applyOptions({ priceFormat: { type: 'custom', formatter: next.volumeScale.format, minMove: next.volumeScale.minMove, base: next.volumeScale.base } });
    price.priceScale().applyOptions({ mode: preferences.scale === 'log' ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal, autoScale: preferences.autoScale });
    const priceUpdate = seriesUpdate(current.data?.prices ?? [], next.prices);
    if (priceUpdate.reset || current.data?.priceScale.key !== next.priceScale.key) price.setData(next.prices);
    else for (const update of priceUpdate.updates) price.update(update.point, update.historical);
    if (!preferences.autoScale && (!current.data || current.data.priceScale.key !== next.priceScale.key)) {
      let low = Infinity; let high = -Infinity;
      for (const point of next.prices) {
        if ('low' in point) { low = Math.min(low, point.low); high = Math.max(high, point.high); }
      }
      if (Number.isFinite(low) && Number.isFinite(high)) {
        const padding = Math.max((high - low) / 20, next.priceScale.minMove * 5);
        price.priceScale().setVisibleRange({ from: preferences.scale === 'log' ? Math.max(low / 2, low - padding) : low - padding, to: high + padding });
      }
    }
    if (!preferences.autoScale && !current.data && saved?.priceRange && saved.priceScaleKey === next.priceScale.key) {
      price.priceScale().setVisibleRange(saved.priceRange);
    }
    const volumeUpdate = seriesUpdate(current.data?.volumes ?? [], next.volumes);
    if (volumeUpdate.reset || current.data?.volumeScale.key !== next.volumeScale.key) volume.setData(next.volumes);
    else for (const update of volumeUpdate.updates) volume.update(update.point, update.historical);
    const nextContext = context.value;
    if (!nextContext?.interest.length && current.interest) { chart.removeSeries(current.interest); current.interest = undefined; }
    if (!nextContext?.funding.length && current.funding) { chart.removeSeries(current.funding); current.funding = undefined; }
    if (nextContext?.interest.length) {
      if (!current.interest) {
        current.interest = chart.addSeries(LineSeries, { lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 2, priceLineVisible: false }, chart.panes().length);
        current.interest.getPane().setStretchFactor(1);
      }
      current.interest.applyOptions({ color: colors.interest, title: `OI · ${positioningSeries?.open_interest_unit ?? ''}`,
        priceFormat: { type: 'custom', formatter: nextContext.interestScale.format, minMove: nextContext.interestScale.minMove, base: nextContext.interestScale.base } });
      const update = seriesUpdate(current.context?.interest ?? [], nextContext.interest);
      if (update.reset || current.context?.interestScale.key !== nextContext.interestScale.key) current.interest.setData(nextContext.interest);
      else for (const item of update.updates) current.interest.update(item.point, item.historical);
    }
    if (nextContext?.funding.length) {
      if (!current.funding) {
        current.funding = chart.addSeries(HistogramSeries, { title: 'Funding · fraction', priceLineVisible: false, base: 0 }, chart.panes().length);
        current.funding.getPane().setStretchFactor(1);
      }
      current.funding.applyOptions({ priceFormat: { type: 'custom', formatter: nextContext.fundingScale.format, minMove: nextContext.fundingScale.minMove, base: nextContext.fundingScale.base } });
      const update = seriesUpdate(current.context?.funding ?? [], nextContext.funding);
      if (update.reset || current.context?.fundingScale.key !== nextContext.fundingScale.key) current.funding.setData(nextContext.funding);
      else for (const item of update.updates) current.funding.update(item.point, item.historical);
    }
    current.times = [...new Set([...next.times, ...(nextContext?.interest.map(point => point.time) ?? []), ...(nextContext?.funding.map(point => point.time) ?? [])])].sort((a, b) => a - b);
    if (current.times.length) {
      if (following.current) {
        const width = oldRange ? oldRange.to - oldRange.from : null;
        const range = latestLogicalRange(current.times.length, plot.current?.clientWidth ?? 800, preferences.rightOffset);
        chart.timeScale().setVisibleLogicalRange(width === null ? range : { from: range.to - width, to: range.to });
      } else if (oldRange) {
        const anchored = anchorLogicalRange(oldTimes, current.times, oldRange);
        if (oldSpacing !== null) current.viewport = { from: anchored.from, barSpacing: oldSpacing };
        chart.timeScale().setVisibleLogicalRange(oldSpacing === null ? anchored : pixelAnchoredRange(anchored.from, oldSpacing, chart.timeScale().width()));
      }
    }
    if (!current.data && saved) {
      chart.panes().forEach((pane, index) => { if (saved.stretches[index] !== undefined) pane.setStretchFactor(saved.stretches[index]); });
    }
    current.data = next; current.context = nextContext ?? undefined; previousData.current = { identity: query.identity, data: next };
    plot.current?.setAttribute('data-chart-pane-count', String(chart.panes().length));
    plot.current?.setAttribute('data-positioning-pane-count', String(Math.max(0, chart.panes().length - 2)));
    const selectedCursor = retainedCursor.current;
    const selected = selectedCursor?.identity === query.identity ? next.exact.get(selectedCursor.time) : undefined;
    if (selected && selectedCursor) chart.setCrosshairPosition(next.priceScale.coordinate(selected.close), selectedCursor.time as UTCTimestamp, price);
    current.changing = false;
  }, [prepared, context, colors, preferences.scale, preferences.autoScale, preferences.rightOffset, presenting, query.identity, positioningSeries?.open_interest_unit, pixelRatio]);

  const fit = () => {
    const current = surface.current;
    if (!current?.times.length) return;
    current.viewport = undefined;
    current.changing = true; current.navigating = false; following.current = false; setFollow(false);
    current.chart.timeScale().fitContent(); current.changing = false;
  };
  const latest = () => {
    if (query.historyLimitReached) query.resetHistory();
    const current = surface.current;
    following.current = true; setFollow(true); setCursor(null);
    if (!current?.times.length) return;
    current.viewport = undefined;
    current.changing = true; current.navigating = false;
    current.chart.timeScale().setVisibleLogicalRange(latestLogicalRange(current.times.length, plot.current?.clientWidth ?? 800, preferences.rightOffset));
    current.changing = false;
  };
  const zoom = (factor: number) => {
    const current = surface.current; const range = current?.chart.timeScale().getVisibleLogicalRange();
    if (!current || !range) return;
    const middle = (range.from + range.to) / 2; const half = (range.to - range.from) * factor / 2;
    current.navigating = true;
    following.current = false; setFollow(false);
    current.chart.timeScale().setVisibleLogicalRange({ from: middle - half, to: middle + half });
  };
  const currentCursor = cursor?.identity === query.identity ? cursor : null;
  const inspected = currentCursor ? prepared.value?.exact.get(currentCursor.time) : candles[candles.length - 1];
  const inspectedPositioning = inspected && positioningSeries ? positioningSeries.bars[candleAtTime(positioningSeries.bars, BigInt(inspected.start_ns))] : undefined;
  const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = surface.current;
    if (event.ctrlKey || event.metaKey || event.altKey || !current || !candles.length || !current.data) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const direction = event.key === 'ArrowLeft' ? -1 : 1;
      const range = current.chart.timeScale().getVisibleLogicalRange();
      if (event.shiftKey && range) {
        current.navigating = true;
        const delta = (range.to - range.from) * direction * 0.15;
        following.current = false; setFollow(false);
        current.chart.timeScale().setVisibleLogicalRange({ from: range.from + delta, to: range.to + delta });
        return;
      }
      const selected = inspected ? candles.indexOf(inspected) : candles.length - 1;
      const bar = candles[Math.max(0, Math.min(candles.length - 1, selected + direction))]!;
      const time = chartTime(bar.start_ns);
      current.chart.setCrosshairPosition(current.data.priceScale.coordinate(bar.close), time, current.price);
      setCursor({ identity: query.identity, time, keyboard: true });
      const index = current.times.indexOf(time);
      if (range && (index < range.from || index > range.to)) {
        current.navigating = false;
        current.viewport = undefined;
        const half = (range.to - range.from) / 2;
        following.current = false; setFollow(false);
        current.chart.timeScale().setVisibleLogicalRange({ from: index - half, to: index + half });
      }
    } else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(0.8); }
    else if (event.key === '-') { event.preventDefault(); zoom(1.25); }
    else if (event.key === 'Home' || event.key.toLowerCase() === 'f') { event.preventDefault(); fit(); }
    else if (event.key === 'End' || event.key.toLowerCase() === 'l') { event.preventDefault(); latest(); }
    else if (event.key === 'Escape') { event.preventDefault(); current.chart.clearCrosshairPosition(); setCursor(null); }
  };
  const stateTitle = prepared.error ? 'Unsupported chart precision' : query.blockedReason ? 'Candle source not available for this selection'
    : query.loading && !data ? 'Loading candle history' : query.error && !data ? 'Candle request failed'
      : !query.usable && !data ? source === 'venue_ohlcv' ? 'Waiting for the selected native instrument' : 'Waiting for the selected native trade cohort'
        : data?.status === 'unavailable' ? 'Candle history unavailable' : 'No candles in available history';
  const status = query.blockedReason ? 'Selection unavailable' : query.stale ? 'Stale snapshot' : query.paused ? plotOffscreen ? 'Offscreen · updates paused' : 'Updates paused'
    : query.historyLimitReached ? 'History cap · growth paused' : query.loading ? 'Refreshing'
      : data?.status === 'ready' ? 'Polling · 5s' : data?.status === 'unavailable' ? 'Unavailable' : data?.status === 'empty' ? 'Empty history' : 'Waiting';
  const reasons = data?.reasons.map(reason => reason.replace(/_/g, ' ')).join(' · ');
  const volumeUnit = data?.volume_unit === 'base' ? instrument?.base.ticker ?? 'base' : data?.volume_unit ?? '';
  const pageCount = Math.max(1, Math.ceil(candles.length / 50));
  const currentPage = Math.min(tablePage, pageCount - 1);
  const tableCandles = candles.slice(Math.max(0, candles.length - (currentPage + 1) * 50), candles.length - currentPage * 50);
  const sourceLabel = source === 'session_trades' ? 'Observed flow · partial retained trades' : 'Ticker history · primary instrument';
  return <section className={`price-chart${maximized ? ' price-chart--maximized' : ''}`} data-testid="price-chart" data-pane="price" data-presentation-active={presenting} data-interval={interval} data-source={source} data-presented-source={data?.source ?? ''} data-presented-scope={data?.scope ?? ''} data-presented-interval={data?.interval ?? ''} data-presented-generation={data?.generation ?? ''} data-positioning={!showPositioning ? 'off' : positioning.data?.status ?? (positioning.error ? 'error' : 'loading')} data-positioning-instrument={positioningSeries?.instrument_id ?? ''} hidden={!active} aria-label="Price action">
    <div className="price-chart__toolbar">
      <strong className="price-chart__identity">{instrument?.native_symbol ?? 'Market'} · {interval}</strong>
      {plotOffscreen && <button type="button" className="price-chart__reveal" aria-controls={plotId} title="Scroll to the plot. Updates resume only while the chart is visible." onClick={() => {
        plot.current?.scrollIntoView({ block: 'center', inline: 'nearest' });
        plot.current?.focus({ preventScroll: true });
      }}>View chart</button>}
      <div className="price-chart__view-actions" role="group" aria-label="Candle source">
        <button type="button" aria-pressed={preferences.source === 'auto'} onClick={() => onPreferencesChange({ source: 'auto' })}>Auto source</button>
        <button type="button" aria-pressed={preferences.source === 'session_trades'} onClick={() => onPreferencesChange({ source: 'session_trades' })}>Session trades</button>
        <button type="button" aria-pressed={preferences.source === 'venue_ohlcv'} onClick={() => onPreferencesChange({ source: 'venue_ohlcv' })}>Venue history</button>
      </div>
      <div className="price-chart__view-actions" role="group" aria-label="Candle viewport">
        <button type="button" aria-pressed={preferences.autoScale} onClick={() => onPreferencesChange({ autoScale: !preferences.autoScale })}>Auto scale</button>
        <button type="button" aria-pressed={preferences.scale === 'log'} onClick={() => onPreferencesChange({ scale: preferences.scale === 'log' ? 'linear' : 'log' })}>{preferences.scale === 'log' ? 'Log' : 'Linear'}</button>
        <button type="button" onClick={fit} disabled={!candles.length || !!prepared.error}>Fit</button>
        <button type="button" className={follow ? 'is-following' : ''} onClick={latest} disabled={!candles.length} aria-pressed={follow}>{query.historyLimitReached ? 'Latest / reset' : 'Latest'}</button>
        <label className="price-chart__margin">Margin <input aria-label="Chart right margin in bars" type="number" min={0} max={50} step={1} value={preferences.rightOffset} onChange={event => { const value = event.currentTarget.valueAsNumber; if (Number.isFinite(value)) onPreferencesChange({ rightOffset: Math.max(0, Math.min(50, value)) }); }} /></label>
        <button type="button" aria-pressed={showPositioning} disabled={source !== 'session_trades'} title={source === 'session_trades' ? 'Observed receive-time context; drag separators to resize panes' : 'Observed positioning cannot share the exchange-time axis'} onClick={() => onPreferencesChange({ positioning: !preferences.positioning })}>OI &amp; funding</button>
      </div>
      <span className={`price-chart__status${query.stale ? ' is-stale' : ''}`} role="status">{status}</span>
      <button type="button" onClick={onMaximize} aria-label={maximized ? 'Restore price chart' : 'Maximize price chart'}>{maximized ? 'Restore' : 'Expand'}</button>
    </div>
    {source === 'venue_ohlcv' && frame && frame.source.mode !== 'live' && <div className="price-chart__warning" role="status">Current external venue history is not replay evidence. Exchange-time ticker history is separate from replay receive-time flow.</div>}
    <div className="price-chart__legend" role="group" tabIndex={0} aria-label="Exact candle readout; scroll horizontally for all values" aria-live={currentCursor?.keyboard ? 'polite' : 'off'} aria-atomic="true">
      {inspected ? <><span className="price-chart__bar-time">{candleDate(inspected.start_ns, true)}</span><dl>{(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}><dt>{field[0]!.toUpperCase()}</dt><dd title={inspected[field]}>{inspected[field]}</dd></div>)}<div><dt>Vol</dt><dd title={`${inspected.volume} ${volumeUnit}`}>{inspected.volume} <small>{volumeUnit}</small></dd></div></dl><span className={`price-chart__bar-state${inspected.closed ? '' : ' is-active'}`} title="Closed means the bucket ended, not guaranteed complete coverage">{inspected.closed ? 'Closed bar' : 'Active bar'}</span></> : <span>{currentCursor ? 'No candle at this timestamp · gaps stay empty' : 'Exact OHLC and volume from the selected source'}</span>}
    </div>
    {showPositioning && <dl className="price-chart__positioning" tabIndex={0} aria-label="Open interest and funding at the inspected time">
      <div><dt>OI</dt><dd title={inspectedPositioning?.open_interest ? `O ${inspectedPositioning.open_interest.open} · H ${inspectedPositioning.open_interest.high} · L ${inspectedPositioning.open_interest.low} · C ${inspectedPositioning.open_interest.close}` : undefined}>{inspectedPositioning?.open_interest?.close ?? '—'} <small>{positioningSeries?.open_interest_unit}</small>{inspectedPositioning?.open_interest && <small> Δ {inspectedPositioning.open_interest.change}</small>}</dd></div>
      <div><dt>Funding</dt><dd title={inspectedPositioning?.funding_interval_ns ? `Interval ${inspectedPositioning.funding_interval_ns} ns` : 'Funding interval unknown'}>{inspectedPositioning?.funding_rate ?? '—'} <small>{positioningSeries ? fundingIntervalLabel(positioningSeries.bars) : ''}</small></dd></div>
      <div><dt>Source</dt><dd>{positioningSeries ? `${venueLabel(positioningSeries.venue)} ${positioningSeries.native_symbol} · ${positioningSeries.relation === 'selected' ? 'selected instrument' : 'related perpetual, not the selected instrument'} · projected receive time UTC` : positioning.error ?? (positioning.data ? 'No observed positioning for this selection' : 'Waiting for observed context')}</dd></div>
      {(!context.value?.interest.length || !context.value.funding.length) && <div><dd>{!context.value?.interest.length && 'OI not observed. '}{!context.value?.funding.length && 'Funding not observed. '}No empty panes reserved.</dd></div>}
      {context.error && <div><dd>{context.error}</dd></div>}
    </dl>}
    <div className="price-chart__plot-frame">
      <div className="price-chart__plot" id={plotId} ref={plot} tabIndex={0} role="group" aria-label="Price action chart" aria-describedby={helpId} onKeyDown={keyboard}
        onPointerDownCapture={() => { if (surface.current) surface.current.navigating = true; }}
        onPointerMoveCapture={event => { if (surface.current && event.buttons === 1) surface.current.navigating = true; }}
        onPointerUp={() => { const autoScale = surface.current?.price.priceScale().options().autoScale; if (autoScale !== undefined && autoScale !== preferences.autoScale) onPreferencesChange({ autoScale }); }}
        onWheelCapture={() => { if (surface.current) surface.current.navigating = true; }} />
      {(!candles.length || prepared.error) && <div className="price-chart__empty" role="status"><h3>{stateTitle}</h3><p>{prepared.error ?? query.blockedReason ?? query.error ?? (reasons || 'No synthetic candles or substituted venue history.')}</p>{prepared.error ? <p>Exact values remain available in the table below.{preferences.scale === 'log' && <button type="button" onClick={() => onPreferencesChange({ scale: 'linear' })}>Use Linear</button>}</p> : !query.loading && query.usable && <button type="button" onClick={query.retry}>Retry candles</button>}</div>}
      {query.olderLoading && <span className="price-chart__loading" role="status">Loading older history…</span>}
    </div>
    {query.stale && <div className="price-chart__warning" role="status"><span>{query.error ? `Refresh failed · retained candles are stale. ${query.error}` : !connected ? 'Market connection interrupted · retained candles are stale.' : !query.usable ? 'Session evidence unavailable · retained candles are stale.' : `No successful refresh for ${query.ageSeconds}s · retained candles are stale.`}</span><button type="button" onClick={query.retry}>Retry candles</button></div>}
    {query.olderError && <div className="price-chart__warning" role="status"><span>Older history request failed · {query.olderError}</span><button type="button" onClick={query.loadOlder}>Retry older history</button></div>}
    <footer className="price-chart__footer">
      <div className="price-chart__source-summary">
        <strong>{sourceLabel}</strong>
        <span>{source === 'session_trades' ? 'Projected receive time · UTC' : 'Exchange time · UTC'}</span>
        {!data && <span role="status">Awaiting source coverage</span>}
        {data?.partial && <span className="price-chart__coverage" role="status">Partial coverage</span>}
        {candles.length > 0 && candles.length < 20 && <span className="price-chart__coverage" role="status">Sparse · {candles.length} buckets</span>}
        {data?.history && <span role="status">History: {data.history.state.replace(/_/g, ' ')}{data.history.pagination === 'recent_only' && ' · recent only'}</span>}
        {query.historyLimitReached && <span className="price-chart__coverage" role="status">10,000-bar cap · growth paused</span>}
      </div>
      <span id={helpId}>← → inspect · Shift + ← → pan · + / − zoom · F fit · L latest. Scroll readouts for exact values.</span>
      {query.hasMore && <button type="button" onClick={query.loadOlder} disabled={query.olderLoading}>{query.olderLoading ? 'Loading older history…' : 'Load older history'}</button>}
      {query.historyLimitReached && <button type="button" onClick={latest} title="Discards loaded history and requests a recent page">Latest / reset</button>}
      <details className="price-chart__details"><summary>Exact candle data</summary>
        <div className="price-chart__table-nav"><button type="button" disabled={currentPage === pageCount - 1} onClick={() => setTablePage(currentPage + 1)}>Older rows</button><span>Page {currentPage + 1} / {pageCount} · {candles.length} loaded bars</span><button type="button" disabled={currentPage === 0} onClick={() => setTablePage(currentPage - 1)}>Newer rows</button></div>
        <div className="price-chart__table-scroll"><table><caption>{sourceLabel} · exact original decimal strings and nanosecond boundaries</caption><thead><tr>{['Start ns (UTC)', 'End ns', 'Open', 'High', 'Low', 'Close', `Volume (${volumeUnit})`, 'Bucket'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{tableCandles.map(bar => <tr key={bar.start_ns}><th scope="row" title={candleDate(bar.start_ns, true)}>{bar.start_ns}</th><td>{bar.end_ns}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td><td>{bar.volume}</td><td>{bar.closed ? 'Closed' : 'Forming'}</td></tr>)}</tbody></table></div>
      </details>
      <details className="price-chart__details"><summary>Chart help &amp; provenance</summary>
        {data && <p>{data.scope === 'aggregate' ? 'Aggregate' : 'Primary'} · {data.venue_label.split(' + ').map(venueLabel).join(' + ')} · {data.instrument_ids.length} instrument(s).</p>}
        {candles.length > 0 && candles.length < 20 && <p className="price-chart__coverage">Sparse coverage · {candles.length} observed buckets. Gaps are not filled; bar widths are bounded.</p>}
        {data?.partial && <p className="price-chart__coverage">Partial coverage · closed bars do not guarantee complete history.</p>}
        {query.historyLimitReached && <p className="price-chart__coverage">10,000-bar memory limit reached. Growth is paused; visible history is retained. Latest / reset discards loaded history and requests a recent page.</p>}
        {data?.history && <p>History: {data.history.state.replace(/_/g, ' ')} · {data.history.pagination === 'recent_only' ? 'provider recent window only' : 'time pagination'} · polling every {data.history.poll_interval_ms / 1000}s.</p>}
        {reasons && <p className="price-chart__coverage">{reasons}</p>}
        {data && <><p>Source {data.source_identity} · {frame?.source.mode} · session {data.session_id} · generation {data.generation}. Selected instruments: {data.instrument_ids.join(', ')}. Quote assets: {data.quote_asset_ids.join(', ')}.{data.quote_asset_ids.length > 1 && ' Cross-quote aggregate is indicative only; no currency conversion or financial use.'}</p><p>Supported intervals: {data.supported_intervals.join(', ') || 'none'}. Use the global timeframe control.</p></>}
        {data?.coverage_start_ns && data.coverage_end_ns && <p>Coverage {candleDate(data.coverage_start_ns, true)} — {candleDate(data.coverage_end_ns, true)}.</p>}
        {data && <p title={`Exact timestamp: ${data.fetched_at_ns} ns`}>{data.source === 'session_trades' ? 'Source clock' : 'Fetched'} {candleDate(data.fetched_at_ns, true)}. {query.receivedAt !== null && `Checked ${new Date(query.receivedAt).toISOString()} · ${query.ageSeconds}s ago.`}</p>}
        {!!data?.missing_instrument_ids?.length && <p>Missing observed trade history: {data.missing_instrument_ids.join(', ')}.</p>}
        {data?.bucket_alignment && <p>Bucket alignment: {data.bucket_alignment.replace(/_/g, ' ')}.</p>}
        {data?.retention && <p>Session retention: {data.retention.retained_trades} / {data.retention.max_trades} trades; {data.retention.retained_bytes} / {data.retention.max_bytes} bytes. {data.retention.truncated ? 'Older history retired; bars can contain only retained trades.' : 'Bounded observations, not complete venue history.'}</p>}
        {showPositioning && positioning.data && <p>Positioning is venue-reported context, not executable or paper-funding evidence. Points are discrete observations, not carried forward. Drag pane separators to resize. {positioningSeries && `Retained ${positioningSeries.retention.retained_minutes} / ${positioningSeries.retention.max_minutes} one-minute observations; OI units: ${positioningSeries.open_interest_unit_provenance.replace(/_/g, ' ')}.`} {positioning.data.reasons.join(' · ')}</p>}
        <p>Drag / pinch / wheel to navigate · drag price scale (Auto off) · ← → inspect · Shift + ← → pan · + / − zoom · F fit · L latest · Esc clear. Exchange-time price cursors are separate from observed liquidity cursors.</p>
        <p>Detached history keeps candle pixel spacing when price or volume labels resize an axis; the right edge may show fewer bars. Keyboard inspection stays on its selected timestamp through resize and theme changes; moving the pointer takes over inspection. Exact OHLCV remains unchanged; axis labels are not shortened to stabilize layout.</p>
        <p>Chart drawing and history requests pause when the plot is offscreen or the document is hidden. Use View chart to scroll to the plot and resume visible updates; the selected source, viewport, pane sizes and exact readout are retained.</p>
      </details>
      <a className="price-chart__attribution" href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">Charts by TradingView</a>
    </footer>
  </section>;
}
