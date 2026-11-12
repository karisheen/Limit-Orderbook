import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react';
import type { Frame, Instrument } from './api';
import { useCandleHistory } from './candle-api';
import { candleAtTime, candleDate, candlePriceScale, candleRange, candleTimeTicks, followCandleRange, fractionAt, INTERVAL_GROUPS, panCandles, QUICK_INTERVALS, timeAtFraction, zoomCandles } from './candle-model';
import type { CandleRange, CandleSource, PriceCandle } from './candle-model';
import { venueLabel } from './format';
import { usePositioningHistory } from './positioning-api';
import { annualizedFundingPercent, fundingIntervalLabel } from './positioning-model';
import type { PositioningBar } from './positioning-model';
import './price-chart.css';

export interface PriceChartProps {
  frame: Frame | null; instrument?: Instrument; connected: boolean; active: boolean;
  interval: string; onIntervalChange: (interval: string) => void;
  instrumentIds: readonly string[]; cohortReady: boolean;
  maximized: boolean; onMaximize: () => void;
}
interface Viewport { identity: string; range: CandleRange; follow: boolean; latestEnd: bigint }
interface Cursor { identity: string; time: string; y: number | null }
const EMPTY_CANDLES: PriceCandle[] = [];
const EMPTY_POSITIONING: PositioningBar[] = [];
const UP = '#40d5b1';
const DOWN = '#f37888';
// Positioning uses its own hues: funding sign is who pays, not price direction.
const INTEREST = '#77cce9';
const FUNDING_POSITIVE = '#e0b05c';
const FUNDING_NEGATIVE = '#8f9cf4';
const LEFT = 14;
const RIGHT = 106;
const TOP = 24;
const BOTTOM = 30;

export function PriceChart({ frame, instrument, connected, active, interval, instrumentIds, cohortReady, onIntervalChange, maximized, onMaximize }: PriceChartProps): ReactNode {
  const [source, setSource] = useState<CandleSource>('session_trades');
  const query = useCandleHistory(frame, interval, active, connected, instrumentIds, cohortReady, source);
  const data = query.data;
  const candles = data?.candles ?? EMPTY_CANDLES;
  // Observed positioning lives on the receive-time axis; it is never drawn against exchange-time venue history.
  const [positioningChoice, setPositioningChoice] = useState<boolean | null>(null);
  const showPositioning = source === 'session_trades' && (positioningChoice ?? instrument?.market_type === 'perpetual');
  const positioning = usePositioningHistory(frame, interval, showPositioning && query.usable && !query.paused,
    data?.source === 'session_trades' ? data.source_identity : null);
  const positioningSeries = positioning.data?.series.find(series => series.relation === 'selected') ?? positioning.data?.series[0];
  const positioningBars = positioningSeries?.bars ?? EMPTY_POSITIONING;
  // A source-level break has no per-bar boundary; do not imply continuity anywhere in that history.
  const connectPositioning = !positioning.data?.reasons.includes('context_discontinuities');
  const canvas = useRef<HTMLCanvasElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const helpId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const [size, setSize] = useState({ width: 800, height: 420 });
  const [viewport, setViewport] = useState<Viewport | null>(null);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const drag = useRef<{ x: number; range: CandleRange } | null>(null);
  const last = candles[candles.length - 1];
  const latestEnd = last ? BigInt(last.end_ns) : 0n;
  const follow = viewport?.identity === query.identity ? viewport.follow : true;
  const range = viewport?.identity === query.identity
    ? follow ? followCandleRange(viewport.range, viewport.latestEnd, candles) : viewport.range
    : candleRange(candles, true);
  const rangeStart = range?.start;
  const rangeEnd = range?.end;
  const visible = useMemo(() => rangeStart === undefined || rangeEnd === undefined ? EMPTY_CANDLES
    : candles.filter(c => BigInt(c.end_ns) > rangeStart && BigInt(c.start_ns) < rangeEnd), [candles, rangeStart, rangeEnd]);
  const priceScale = useMemo(() => candlePriceScale(visible), [visible]);
  const visiblePositioning = useMemo(() => rangeStart === undefined || rangeEnd === undefined ? EMPTY_POSITIONING
    : positioningBars.filter(bar => BigInt(bar.end_ns) > rangeStart && BigInt(bar.start_ns) < rangeEnd), [positioningBars, rangeStart, rangeEnd]);
  const interestScale = useMemo(() => candlePriceScale(visiblePositioning.flatMap(bar => bar.open_interest ? [bar.open_interest] : [])), [visiblePositioning]);
  // Funding is signed: every bar is measured from an explicit zero line.
  const fundingScale = useMemo(() => candlePriceScale(visiblePositioning.flatMap(bar => bar.funding_rate === null ? []
    : [{ open: '0', high: bar.funding_rate, low: bar.funding_rate, close: bar.funding_rate }])), [visiblePositioning]);
  const currentCursor = cursor?.identity === query.identity ? cursor : null;
  const selectedIndex = currentCursor ? candleAtTime(candles, BigInt(currentCursor.time)) : -1;
  const inspected = currentCursor ? candles[selectedIndex] : last;
  const positioningTime = currentCursor ? BigInt(currentCursor.time) : last ? (BigInt(last.start_ns) + BigInt(last.end_ns)) / 2n : null;
  const inspectedPositioning = positioningTime === null ? undefined : positioningBars[candleAtTime(positioningBars, positioningTime)];
  const plotWidth = Math.max(1, size.width - LEFT - RIGHT);
  const timeTicks = useMemo(() => rangeStart === undefined || rangeEnd === undefined ? []
    : candleTimeTicks({ start: rangeStart, end: rangeEnd }, plotWidth), [rangeStart, rangeEnd, plotWidth]);
  const plotBottom = size.height - BOTTOM;
  const priceBottom = Math.max(TOP + 50, plotBottom * (showPositioning ? 0.5 : 0.76));
  const volumeTop = priceBottom + 20;
  const volumeBottom = showPositioning ? Math.max(volumeTop + 16, plotBottom * 0.64) : plotBottom;
  const interestTop = volumeBottom + 20;
  const interestBottom = Math.max(interestTop + 16, plotBottom * 0.83);
  const fundingTop = interestBottom + 20;
  const setRange = (next: CandleRange, following = false) => setViewport({ identity: query.identity, range: next, follow: following, latestEnd });
  const fit = () => { const next = candleRange(candles); if (next) setRange(next); };
  const latest = () => {
    const next = candleRange(candles, true);
    if (next) setRange(next, true);
    setCursor(null);
  };
  const zoom = (factor: number, anchor = 0.5) => { if (range) setRange(zoomCandles(range, factor, anchor, candles)); };

  useEffect(() => {
    const node = plot.current;
    if (!node) return;
    const resize = () => {
      const box = node.getBoundingClientRect();
      if (box.width && box.height) setSize(previous => previous.width === box.width && previous.height === box.height ? previous : { width: box.width, height: box.height });
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    (menu.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]:not(:disabled)') ?? menu.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"]:not(:disabled)'))?.focus();
    const outside = (event: globalThis.PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target) && !menuButton.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [menuOpen]);
  useEffect(() => { setMenuOpen(false); drag.current = null; }, [query.identity, active]);
  useEffect(() => { setViewport(null); setCursor(null); }, [query.identity]);

  useEffect(() => {
    const node = plot.current;
    if (!node || rangeStart === undefined || rangeEnd === undefined || !active) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const anchor = (event.clientX - node.getBoundingClientRect().left - LEFT) / plotWidth;
      setViewport({ identity: query.identity, range: zoomCandles({ start: rangeStart, end: rangeEnd }, event.deltaY > 0 ? 1.12 : 0.89, anchor, candles), follow: false, latestEnd });
    };
    node.addEventListener('wheel', wheel, { passive: false });
    return () => node.removeEventListener('wheel', wheel);
  }, [active, rangeStart, rangeEnd, plotWidth, query.identity, candles, latestEnd]);

  useEffect(() => {
    const node = canvas.current;
    if (!node || !active) return;
    const context = node.getContext('2d');
    if (!context) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2, 4096 / size.width, 4096 / size.height);
    const pixelWidth = Math.max(1, Math.round(size.width * ratio));
    const pixelHeight = Math.max(1, Math.round(size.height * ratio));
    if (node.width !== pixelWidth) node.width = pixelWidth;
    if (node.height !== pixelHeight) node.height = pixelHeight;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.width, size.height);
    context.fillStyle = '#0b111b';
    context.fillRect(0, 0, size.width, size.height);
    context.font = '12px ui-monospace, SFMono-Regular, Menlo, monospace';
    const x = (time: string | bigint) => LEFT + (rangeStart === undefined || rangeEnd === undefined ? 0 : fractionAt(time, { start: rangeStart, end: rangeEnd }) * plotWidth);
    const y = (price: string) => priceBottom - priceScale.fraction(price) * (priceBottom - TOP);
    context.textBaseline = 'middle';
    for (let row = 0; row <= 5; row++) {
      const py = TOP + (priceBottom - TOP) * row / 5;
      context.strokeStyle = '#1b2533'; context.lineWidth = 1;
      context.beginPath(); context.moveTo(LEFT, py + 0.5); context.lineTo(LEFT + plotWidth, py + 0.5); context.stroke();
      if (visible.length) {
        context.fillStyle = '#8292a8'; context.textAlign = 'left';
        context.fillText(priceScale.label(1 - row / 5), LEFT + plotWidth + 10, py, RIGHT - 16);
      }
    }
    let labelEnd = -Infinity;
    for (const tick of timeTicks) {
      const px = x(tick.time);
      context.strokeStyle = '#17212e'; context.beginPath(); context.moveTo(px, TOP); context.lineTo(px, plotBottom); context.stroke();
      context.fillStyle = '#9aabc0'; context.textAlign = 'left';
      const labelWidth = context.measureText(tick.label).width;
      const labelX = Math.min(px + 3, LEFT + plotWidth - labelWidth);
      if (labelX >= labelEnd + 12) {
        context.fillText(tick.label, labelX, size.height - 12);
        labelEnd = labelX + labelWidth;
      }
    }
    context.strokeStyle = '#263143'; context.beginPath(); context.moveTo(LEFT, volumeTop - 8); context.lineTo(LEFT + plotWidth, volumeTop - 8); context.stroke();
    context.fillStyle = '#8191a7'; context.textAlign = 'left'; context.fillText('VOLUME', LEFT + 4, volumeTop + 4);
    const maxVolume = Math.max(0, ...visible.map(c => Number(c.volume)));
    const level = (scale: { fraction: (text: string) => number }, top: number, bottom: number, value: string) => bottom - scale.fraction(value) * (bottom - top);
    const hasInterest = visiblePositioning.some(bar => bar.open_interest);
    const hasFunding = visiblePositioning.some(bar => bar.funding_rate !== null);
    if (showPositioning) {
      for (const [top, bottom, scale, observed, label] of [
        [interestTop, interestBottom, interestScale, hasInterest, `OPEN INTEREST · ${hasInterest ? positioningSeries?.open_interest_unit ?? 'unit unknown' : 'not observed'}`],
        [fundingTop, plotBottom, fundingScale, hasFunding, `FUNDING · ${fundingIntervalLabel(visiblePositioning)}`],
      ] as const) {
        context.strokeStyle = '#263143'; context.beginPath(); context.moveTo(LEFT, top - 8); context.lineTo(LEFT + plotWidth, top - 8); context.stroke();
        context.fillStyle = '#8191a7'; context.textAlign = 'left'; context.fillText(label, LEFT + 4, top + 4);
        if (!observed) continue;
        context.fillText(scale.label(1), LEFT + plotWidth + 10, top + 14, RIGHT - 16);
        context.fillText(scale.label(0), LEFT + plotWidth + 10, bottom, RIGHT - 16);
      }
      if (hasFunding) {
        const zero = level(fundingScale, fundingTop + 14, plotBottom, '0');
        context.strokeStyle = '#34435a'; context.beginPath(); context.moveTo(LEFT, zero + 0.5); context.lineTo(LEFT + plotWidth, zero + 0.5); context.stroke();
      }
    }
    context.save(); context.beginPath(); context.rect(LEFT, TOP - 4, plotWidth, plotBottom - TOP + 4); context.clip();
    for (const candle of visible) {
      const center = x((BigInt(candle.start_ns) + BigInt(candle.end_ns)) / 2n);
      const width = Math.max(1, Math.min(36, (x(candle.end_ns) - x(candle.start_ns)) * 0.68));
      const open = y(candle.open); const close = y(candle.close);
      const color = priceScale.fraction(candle.close) >= priceScale.fraction(candle.open) ? UP : DOWN;
      context.strokeStyle = color; context.fillStyle = color; context.lineWidth = 1;
      context.globalAlpha = candle.closed ? 1 : 0.85;
      context.beginPath(); context.moveTo(center, y(candle.high)); context.lineTo(center, y(candle.low)); context.stroke();
      const bodyTop = Math.min(open, close); const bodyHeight = Math.max(1.5, Math.abs(close - open));
      if (candle.closed) context.fillRect(center - width / 2, bodyTop, width, bodyHeight);
      else { context.fillStyle = '#0b111b'; context.fillRect(center - width / 2, bodyTop, width, bodyHeight); context.strokeRect(center - width / 2, bodyTop, width, bodyHeight); }
      const volumeHeight = maxVolume > 0 ? Number(candle.volume) / maxVolume * Math.max(1, volumeBottom - volumeTop - 16) : 0;
      context.fillStyle = color; context.globalAlpha = candle.closed ? 0.4 : 0.2;
      context.fillRect(center - width / 2, volumeBottom - volumeHeight, width, volumeHeight);
    }
    context.globalAlpha = 1;
    if (showPositioning) {
      // Join touching buckets only when the source reports no unlocated continuity breaks.
      context.strokeStyle = INTEREST; context.lineWidth = 1.5; context.beginPath();
      let joinedEnd: string | null = null;
      for (const bar of visiblePositioning) {
        if (!bar.open_interest) continue;
        const px = x((BigInt(bar.start_ns) + BigInt(bar.end_ns)) / 2n);
        const py = level(interestScale, interestTop + 14, interestBottom, bar.open_interest.close);
        if (connectPositioning && bar.start_ns === joinedEnd) context.lineTo(px, py); else context.moveTo(px, py);
        joinedEnd = bar.end_ns;
      }
      context.stroke();
      context.lineWidth = 1;
      const zero = level(fundingScale, fundingTop + 14, plotBottom, '0');
      for (const bar of visiblePositioning) {
        const px = x((BigInt(bar.start_ns) + BigInt(bar.end_ns)) / 2n);
        if (bar.open_interest) {
          context.fillStyle = INTEREST; context.globalAlpha = 0.45;
          context.beginPath(); context.moveTo(px, level(interestScale, interestTop + 14, interestBottom, bar.open_interest.high));
          context.lineTo(px, level(interestScale, interestTop + 14, interestBottom, bar.open_interest.low)); context.stroke();
          context.globalAlpha = bar.closed ? 1 : 0.6;
          context.fillRect(px - 1.5, level(interestScale, interestTop + 14, interestBottom, bar.open_interest.close) - 1.5, 3, 3);
        }
        if (bar.funding_rate !== null) {
          const py = level(fundingScale, fundingTop + 14, plotBottom, bar.funding_rate);
          const width = Math.max(1, Math.min(36, (x(bar.end_ns) - x(bar.start_ns)) * 0.68));
          context.fillStyle = bar.funding_rate.startsWith('-') ? FUNDING_NEGATIVE : FUNDING_POSITIVE;
          context.globalAlpha = bar.closed ? 0.8 : 0.45;
          context.fillRect(px - width / 2, Math.min(zero, py), width, Math.max(1, Math.abs(py - zero)));
        }
      }
      context.globalAlpha = 1;
    }
    context.restore();
    if (last && visible.length) {
      const py = y(last.close);
      if (py >= TOP && py <= priceBottom) {
        context.strokeStyle = UP; context.setLineDash([4, 4]); context.beginPath(); context.moveTo(LEFT, py); context.lineTo(LEFT + plotWidth, py); context.stroke(); context.setLineDash([]);
        context.fillStyle = '#173a35'; context.fillRect(LEFT + plotWidth + 1, py - 11, RIGHT - 5, 22);
        context.fillStyle = '#89efd2'; context.textAlign = 'left'; context.fillText(last.close, LEFT + plotWidth + 7, py, RIGHT - 16);
      }
    }
    if (currentCursor && rangeStart !== undefined && rangeEnd !== undefined) {
      const px = x(currentCursor.time);
      if (px >= LEFT && px <= LEFT + plotWidth) {
        const py = currentCursor.y === null ? inspected ? y(inspected.close) : priceBottom : Math.max(TOP, Math.min(priceBottom, currentCursor.y));
        context.strokeStyle = '#a5b8d1'; context.lineWidth = 1; context.setLineDash([3, 4]);
        context.beginPath(); context.moveTo(px, TOP); context.lineTo(px, plotBottom); context.moveTo(LEFT, py); context.lineTo(LEFT + plotWidth, py); context.stroke(); context.setLineDash([]);
        const label = candleDate(currentCursor.time);
        const labelWidth = context.measureText(label).width + 16;
        const labelX = Math.max(LEFT, Math.min(size.width - labelWidth, px - labelWidth / 2));
        context.fillStyle = '#2b3c53'; context.fillRect(labelX, size.height - BOTTOM + 2, labelWidth, BOTTOM - 3);
        context.fillStyle = '#eef5ff'; context.fillText(label, labelX + 8, size.height - 12);
        context.fillStyle = '#2b3c53'; context.fillRect(LEFT + plotWidth + 1, py - 11, RIGHT - 5, 22);
        context.fillStyle = '#eef5ff'; context.fillText(priceScale.label((priceBottom - py) / (priceBottom - TOP)), LEFT + plotWidth + 7, py, RIGHT - 16);
      }
    }
  }, [active, size, candles, visible, priceScale, timeTicks, rangeStart, rangeEnd, plotWidth, plotBottom, priceBottom, volumeTop, volumeBottom, last, currentCursor, inspected,
    showPositioning, visiblePositioning, positioningSeries, connectPositioning, interestScale, fundingScale, interestTop, interestBottom, fundingTop]);

  const inspectPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (!range) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    if (drag.current) {
      setRange(panCandles(drag.current.range, (drag.current.x - x) / plotWidth));
      return;
    }
    if (x < LEFT || x > LEFT + plotWidth) { setCursor(null); return; }
    setCursor({ identity: query.identity, time: timeAtFraction((x - LEFT) / plotWidth, range).toString(), y: event.clientY - bounds.top });
  };
  const keyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey || !range || !candles.length) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const direction = event.key === 'ArrowLeft' ? -1 : 1;
      if (event.shiftKey) { setRange(panCandles(range, direction * 0.15)); return; }
      const index = Math.max(0, Math.min(candles.length - 1, (selectedIndex < 0 ? candles.length - 1 : selectedIndex) + direction));
      const candle = candles[index]!;
      const time = (BigInt(candle.start_ns) + BigInt(candle.end_ns)) / 2n;
      setCursor({ identity: query.identity, time: time.toString(), y: null });
      const fraction = fractionAt(time, range);
      if (fraction < 0.05 || fraction > 0.95) setRange(panCandles(range, fraction - 0.5));
    } else if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(0.8); }
    else if (event.key === '-') { event.preventDefault(); zoom(1.25); }
    else if (event.key === 'Home' || event.key.toLowerCase() === 'f') { event.preventDefault(); fit(); }
    else if (event.key === 'End' || event.key.toLowerCase() === 'l') { event.preventDefault(); latest(); }
    else if (event.key === 'Escape') { event.preventDefault(); setCursor(null); }
  };
  const stateTitle = query.blockedReason ? 'Candle source not available for this selection'
    : query.loading && !data ? 'Loading candle history' : query.error && !data ? 'Candle request failed'
      : !query.usable && !data ? 'Waiting for the selected native trade cohort'
        : data?.status === 'unavailable' ? 'Candle history unavailable' : 'No candles in available history';
  const status = query.blockedReason ? 'Selection unavailable' : query.stale ? 'Stale snapshot' : query.paused ? 'Updates paused' : query.loading ? 'Refreshing' : data?.status === 'ready' ? '~5s refresh' : data?.status === 'unavailable' ? 'Unavailable' : data?.status === 'empty' ? 'Empty history' : 'Waiting';
  const sourceLabel = source === 'session_trades' ? 'Observed session trades · partial history' : 'Venue historical OHLCV · separate series';
  const displayVenues = data ? data.venue_label.split(' + ').map(venueLabel).join(' + ') : '';
  const reasons = data?.reasons.map(reason => reason.replace(/_/g, ' ')).join(' · ');
  const volumeUnit = data?.volume_unit === 'base' ? instrument?.base.ticker ?? 'base' : data?.volume_unit ?? '';
  const chooseInterval = (value: string) => { onIntervalChange(value); setMenuOpen(false); menuButton.current?.focus(); };

  const quickInterval = QUICK_INTERVALS.some(value => value === interval);
  return <section className={`price-chart${maximized ? ' price-chart--maximized' : ''}`} data-testid="price-chart" data-pane="price" data-interval={interval} data-source={source} data-presented-source={data?.source ?? ''} data-presented-scope={data?.scope ?? ''} data-presented-interval={data?.interval ?? ''} data-presented-generation={data?.generation ?? ''} data-positioning={!showPositioning ? 'off' : positioning.data?.status ?? (positioning.error ? 'error' : 'loading')} data-positioning-instrument={positioningSeries?.instrument_id ?? ''} hidden={!active} aria-label="Price action">
    {maximized && <header className="price-chart__header"><div className="price-chart__heading"><h2>{data?.scope === 'aggregate' ? `${instrument?.base.ticker ?? 'Selected'} aggregate · ${displayVenues}` : instrument?.native_symbol ?? 'Market'} <span>{interval}</span></h2></div></header>}
    <div className="price-chart__toolbar">
      <div className="price-chart__intervals" role="group" aria-label="Quick candle timeframes">{QUICK_INTERVALS.map(value => <button type="button" key={value} aria-label={`Candle interval ${value}`} aria-pressed={interval === value} className={interval === value ? 'is-selected' : ''} onClick={() => onIntervalChange(value)}>{value}</button>)}
        <div className="price-chart__menu-anchor"><button type="button" ref={menuButton} aria-label="Candle timeframe" aria-haspopup="menu" aria-expanded={menuOpen} className={quickInterval ? undefined : 'is-selected'} onClick={() => setMenuOpen(value => !value)} onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setMenuOpen(true); } }}>{quickInterval ? 'More' : interval} <span aria-hidden="true">⌄</span></button>
          {menuOpen && <div ref={menu} className="price-chart__menu" role="menu" aria-label="Candle timeframe" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false); }} onKeyDown={event => {
            const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]:not(:disabled)'));
            const index = items.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === 'Escape') { event.preventDefault(); setMenuOpen(false); menuButton.current?.focus(); }
            else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
              event.preventDefault();
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
              items[next]?.focus();
            }
          }}>{INTERVAL_GROUPS.map(group => <div key={group.label} role="group" aria-label={group.label}><span className="price-chart__menu-heading">{group.label}</span><div>{group.values.map(value => <button key={value} type="button" role="menuitemradio" aria-checked={interval === value} tabIndex={interval === value ? 0 : -1} title={value === '1M' ? 'Calendar month' : value} onClick={() => chooseInterval(value)}>{value}<span>{interval === value ? '•' : ''}</span></button>)}</div></div>)}</div>}
        </div>
      </div>
      <div className="price-chart__view-actions" role="group" aria-label="Candle source">
        <button type="button" aria-pressed={source === 'session_trades'} onClick={() => setSource('session_trades')}>Session trades</button>
        <button type="button" aria-pressed={source === 'venue_ohlcv'} onClick={() => setSource('venue_ohlcv')}>Venue history</button>
      </div>
      <div className="price-chart__view-actions" role="group" aria-label="Perpetual positioning">
        <button type="button" aria-pressed={showPositioning} disabled={source !== 'session_trades'} title={source === 'session_trades' ? 'Venue-reported open interest and funding observed by this session, on the same time axis as price' : 'Observed positioning uses receive time and is not drawn against exchange-time venue history'} onClick={() => setPositioningChoice(!showPositioning)}>OI &amp; funding</button>
      </div>
      <div className="price-chart__view-actions" role="group" aria-label="Candle viewport"><button type="button" onClick={fit} disabled={!candles.length}>Fit</button><button type="button" className={follow ? 'is-following' : ''} onClick={latest} disabled={!candles.length} aria-pressed={follow}>Latest</button><button type="button" onClick={() => zoom(0.8)} disabled={!candles.length} aria-label="Zoom in price chart">+</button><button type="button" onClick={() => zoom(1.25)} disabled={!candles.length} aria-label="Zoom out price chart">−</button></div>
      <div className="price-chart__header-actions"><span className={`price-chart__status${query.stale ? ' is-stale' : ''}`} role="status">{status}</span><button type="button" className="price-chart__maximize" onClick={onMaximize} aria-label={maximized ? 'Restore price chart' : 'Maximize price chart'}>{maximized ? 'Restore' : 'Expand'}</button></div>
    </div>
    <div className="price-chart__legend" aria-live={currentCursor?.y === null ? 'polite' : 'off'} aria-atomic="true">
      {inspected ? <><span className="price-chart__bar-time">{candleDate(inspected.start_ns, true)}</span><dl>{(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}><dt>{field[0]!.toUpperCase()}</dt><dd title={inspected[field]}>{inspected[field]}</dd></div>)}<div><dt>Vol</dt><dd title={`${inspected.volume} ${volumeUnit}`}>{inspected.volume} <small>{volumeUnit}</small></dd></div></dl><span className={`price-chart__bar-state${inspected.closed ? '' : ' is-active'}`} title="Closed means the time bucket ended, not independently verified complete history">{inspected.closed ? 'Closed bar' : 'Active bar'}</span></> : <span>{currentCursor ? 'No candle at this timestamp · history gaps are preserved' : 'OHLC and volume from the selected source'}</span>}
    </div>
    {showPositioning && <dl className="price-chart__positioning" aria-label="Open interest and funding at the inspected time">
      <div><dt>OI</dt><dd>{inspectedPositioning?.open_interest ? <>{inspectedPositioning.open_interest.close} <small>{positioningSeries?.open_interest_unit}</small> <small title="Change inside this bucket: last observation minus first">Δ {inspectedPositioning.open_interest.change}</small></> : '—'}</dd></div>
      <div><dt>Funding</dt><dd>{inspectedPositioning?.funding_rate != null ? <>{inspectedPositioning.funding_rate}{inspectedPositioning.funding_interval_ns && <small title="Simple annualization of a variable venue-reported rate; not a forecast"> / {+(Number(inspectedPositioning.funding_interval_ns) / 3.6e12).toFixed(2)}h · ≈{annualizedFundingPercent(inspectedPositioning.funding_rate, inspectedPositioning.funding_interval_ns).toFixed(2)}% APR</small>}</> : '—'}</dd></div>
      <div><dt>Source</dt><dd>{positioningSeries ? `${venueLabel(positioningSeries.venue)} ${positioningSeries.native_symbol} · ${positioningSeries.relation === 'selected' ? 'selected perpetual' : 'related perpetual, not the selected instrument'}` : positioning.error ?? (positioning.data ? 'No perpetual context stream for this selection' : 'Waiting for observed context')}</dd></div>
    </dl>}
    <div className={`price-chart__plot${showPositioning ? ' price-chart__plot--positioning' : ''}`} ref={plot} tabIndex={0} role="group" aria-label="Price action chart" aria-describedby={helpId} onKeyDown={keyboard} onPointerMove={inspectPointer} onPointerLeave={() => { if (!drag.current) setCursor(null); }} onPointerDown={event => {
      if (event.button !== 0 || !range || event.target instanceof Element && event.target.closest('button')) return;
      event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { x: event.clientX - event.currentTarget.getBoundingClientRect().left, range };
    }} onPointerUp={event => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }} onDoubleClick={latest}>
      <canvas ref={canvas} aria-hidden="true" />
      {!candles.length && <div className="price-chart__empty" role="status"><span className="price-chart__empty-mark" aria-hidden="true">{query.loading ? '···' : '—'}</span><h3>{stateTitle}</h3><p>{query.blockedReason ?? query.error ?? (reasons || 'Candles use admitted trades from the selected native cohort; no venue backfill is required.')}</p>{!query.loading && query.usable && <button type="button" onClick={query.retry}>Retry candles</button>}</div>}
      {!!candles.length && !visible.length && <div className="price-chart__empty"><h3>Outside available history</h3><p>No synthetic candles are inserted into gaps.</p><button type="button" onClick={fit}>Fit history</button></div>}
    </div>
    {query.stale && <div className="price-chart__warning" role="status"><span>{query.error ? `Refresh failed · retained candles are stale. ${query.error}` : !query.usable ? 'Market connection interrupted · retained candles are stale.' : `No successful refresh for ${query.ageSeconds}s · retained candles are stale.`}</span><button type="button" onClick={query.retry}>Retry candles</button></div>}
    <footer className="price-chart__footer">
      <div><span className="price-chart__provenance">{sourceLabel}</span><span>{data ? `${data.scope === 'aggregate' ? 'Aggregate' : 'Primary'} · ${displayVenues} · ${data.instrument_ids.length} instrument${data.instrument_ids.length === 1 ? '' : 's'}` : 'Awaiting source coverage'}</span><span>{source === 'session_trades' ? 'Projected receive time · UTC' : 'Exchange time · UTC'}</span>{data?.coverage_start_ns && data.coverage_end_ns && <span title={`${candleDate(data.coverage_start_ns, true)} — ${candleDate(data.coverage_end_ns, true)}`}>Coverage {candleDate(data.coverage_start_ns)} — {candleDate(data.coverage_end_ns)}</span>}</div>
      {data && <div className="price-chart__receipt"><span title={`Exact timestamp: ${data.fetched_at_ns} ns`}>{data.source === 'session_trades' ? 'Source clock' : 'Fetched'} {candleDate(data.fetched_at_ns, true)}</span>{query.receivedAt !== null && <span>Checked {new Date(query.receivedAt).toISOString().slice(11, 19)} UTC · {query.ageSeconds}s ago</span>}</div>}
      <details className="price-chart__details"><summary>Chart help &amp; provenance</summary>
        {reasons && <p className="price-chart__coverage" title={data?.reasons.join(' · ')}>{reasons}</p>}
        {source === 'venue_ohlcv' && <p>Explicit venue history only: never merged with session trades or substituted for an aggregate. Exchange timestamps differ from projected receive timestamps.</p>}
        {data && <p>Source {data.source_identity} · {frame?.source.mode} · session {data.session_id} · generation {data.generation}. {data.partial ? 'Partial observed coverage; closed bars are not a completeness guarantee.' : 'Coverage is limited to the returned history window.'}</p>}
        {data && <p>Selected instruments: {data.instrument_ids.join(', ')}. Quote assets: {data.quote_asset_ids.join(', ')}.{data.quote_asset_ids.length > 1 && ' Cross-quote aggregate is indicative only; no currency conversion and no financial use.'}</p>}
        {!!data?.missing_instrument_ids?.length && <p>Missing observed trade history: {data.missing_instrument_ids.join(', ')}.</p>}
        {data?.bucket_alignment && <p>Bucket alignment: {data.bucket_alignment.replace(/_/g, ' ')}.</p>}
        {data?.retention && <p>Session retention: {data.retention.retained_trades} / {data.retention.max_trades} trades; {data.retention.retained_bytes} / {data.retention.max_bytes} bytes. {data.retention.truncated ? 'Older history has been retired; bars may contain only retained trades.' : 'Bounded observed history, not complete venue history.'}</p>}
        {data?.highwater !== undefined && <p>Source delivery highwater: {data.highwater}.</p>}
        {showPositioning && positioning.data && <p>Open interest and funding: venue-reported context observed by this session, bucketed on the candle receive-time axis; not executable prices, exchange guarantees or paper funding inputs. Unobserved intervals stay empty.{positioningSeries && ` Retained ${positioningSeries.retention.retained_minutes} / ${positioningSeries.retention.max_minutes} one-minute observations; open-interest unit is ${positioningSeries.open_interest_unit_provenance.replace(/_/g, ' ')}.`} {positioning.data.reasons.map(reason => reason.replace(/_/g, ' ')).join(' · ')}.</p>}
        <p id={helpId} className="price-chart__help">Drag to pan · scroll to zoom · ← → inspect · Shift + ← → pan · + / − zoom · F fit · L latest · Esc clear</p>
      </details>
    </footer>
  </section>;
}
