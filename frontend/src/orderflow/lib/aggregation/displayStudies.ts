// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: display-only studies over canonical native events.
import type { StoreApi } from "zustand";
import { MAX_FLOW_BARS, type TerminalStore } from "@/lib/store/terminalStore";
import type { FlowBar, FootprintCandle, FootprintLevel, NormalizedTrade, SessionStats } from "@/lib/types/market";
import { applyTradeToCandle, applyZScores, createEmptyCandle, getCandleOpenTime } from "@/lib/engine/orderFlowEngine";
import { applyTradeToSession, createFlowBar, createSessionStats } from "@/lib/engine/analytics";

/** Subtract exact epoch nanoseconds before projecting to display milliseconds. */
export function nativeTradeLagMs(trade: NormalizedTrade): number | null {
  const reference = trade.native?.asof;
  if (!reference || reference.exchange_event_wall_ns === null || trade.native?.trade.is_subscription_history) return null;
  const elapsed = BigInt(reference.receive_wall_ns) - BigInt(reference.exchange_event_wall_ns);
  if (elapsed < 0n) return null;
  const milliseconds = Number(elapsed) / 1_000_000;
  return Number.isFinite(milliseconds) ? milliseconds : null;
}

/** Not financial evidence: all inputs are plotting projections of native trades. */
export class DisplayStudies {
  candle: FootprintCandle | null = null;
  liveBar: FlowBar | null = null;
  session: SessionStats | null = null;
  private levels: FootprintLevel[] = [];
  private finalizedDelta = 0;
  private cvd = 0;
  private frontier: number | null = null;

  constructor(private readonly store: StoreApi<TerminalStore>) {}

  /** Source time closes observed buckets only; idle time never creates candles. */
  advance(frontier: number | null): void {
    this.frontier = frontier;
    if (frontier !== null && this.candle && frontier > this.candle.closeTime) this.finalize();
  }

  private finalize(): void {
    if (!this.candle) return;
    this.finalizedDelta += this.candle.delta;
    this.store.getState().finalizeCandle({ ...this.candle, cumulativeDelta: this.finalizedDelta, complete: true });
    this.levels.push(...this.candle.levels.values());
    if (this.levels.length > 5000) this.levels = this.levels.slice(-3000);
    this.candle = null;
  }

  apply(trade: NormalizedTrade, completedBars: FlowBar[]): void {
    const settings = this.store.getState();
    const openTime = getCandleOpenTime(trade.timestamp, settings.timeframe);
    const late = (this.candle !== null && openTime < this.candle.openTime) ||
      openTime <= (settings.candles.at(-1)?.openTime ?? -Infinity) ||
      (this.frontier !== null && openTime < getCandleOpenTime(this.frontier, settings.timeframe));
    if (!late && (!this.candle || openTime > this.candle.openTime)) {
      this.finalize();
      this.candle = createEmptyCandle(openTime, settings.timeframe);
    }
    // A delayed receive pair belongs to its producer-time bucket, not the most
    // recent candle. OHLC within each bucket still follows native delivery order.
    const candle = late
      ? settings.candles.find(item => item.openTime === openTime) ?? createEmptyCandle(openTime, settings.timeframe)
      : this.candle!;
    const delta = trade.side === "buy" ? trade.quantity : trade.side === "sell" ? -trade.quantity : 0;
    applyTradeToCandle(candle, trade, settings.tickSize, settings.imbalanceThreshold);
    if (this.levels.length > 1) applyZScores(candle, this.levels);
    this.session ??= createSessionStats(trade.timestamp, trade.price);
    applyTradeToSession(this.session, trade.price, trade.quantity, trade.side, nativeTradeLagMs(trade));
    candle.vwap = this.session.vwap;
    candle.vwapStd = this.session.vwapStd;
    if (late) {
      const historical = [...settings.candles];
      const index = historical.findIndex(item => item.openTime === openTime);
      if (index < 0) {
        const next = historical.find(item => item.openTime > openTime);
        candle.cumulativeDelta = (next ? next.cumulativeDelta - next.delta : this.finalizedDelta) + delta;
        historical.push(candle);
      } else {
        candle.cumulativeDelta += delta;
      }
      for (let index = 0; index < historical.length; index++) {
        const item = historical[index]!;
        if (item.openTime > openTime) historical[index] = { ...item, cumulativeDelta: item.cumulativeDelta + delta };
      }
      historical.sort((a, b) => a.openTime - b.openTime);
      candle.complete = true;
      this.finalizedDelta += delta;
      // Use the store's existing bounded finalization path, including when a
      // previously empty older bucket first receives a late print.
      const last = historical.pop()!;
      this.store.setState({ candles: historical });
      this.store.getState().finalizeCandle(last);
    }
    if (this.candle) this.candle.cumulativeDelta = this.finalizedDelta + this.candle.delta;
    const bucket = Math.floor(trade.timestamp / 1000) * 1000;
    if (this.liveBar && bucket < this.liveBar.time) {
      const bars = [...this.store.getState().flowBars, ...completedBars];
      let bar = bars.find(item => item.time === bucket);
      if (!bar) {
        const next = bars.find(item => item.time > bucket) ?? this.liveBar;
        bar = createFlowBar(bucket, trade.price, next.cvd - (next.buyVolume - next.sellVolume));
        bars.push(bar);
      }
      this.updateFlowBar(bar, trade);
      const nextObserved = bars.find(item => item.time > bucket && item.trades > 0)?.time ?? this.liveBar.time;
      for (let index = 0; index < bars.length; index++) {
        const item = bars[index]!;
        if (item.time > bucket) {
          bars[index] = { ...item, cvd: item.cvd + delta };
          if (!item.trades && item.time < nextObserved) {
            bars[index] = { ...bars[index]!, open: trade.price, high: trade.price, low: trade.price, close: trade.price };
          }
        }
      }
      this.cvd += delta;
      this.liveBar.cvd = this.cvd;
      completedBars.length = 0;
      this.store.setState({ flowBars: bars.sort((a, b) => a.time - b.time).slice(-MAX_FLOW_BARS) });
      return;
    }
    if (!this.liveBar || bucket > this.liveBar.time) {
      if (this.liveBar) {
        const previous = this.liveBar;
        completedBars.push(previous);
        // Do not manufacture zero-volume observations across an unobserved gap.
        if (bucket - previous.time <= 120_000) {
          for (let time = previous.time + 1000; time < bucket; time += 1000) {
            completedBars.push(createFlowBar(time, previous.close, previous.cvd));
          }
        }
      }
      this.liveBar = createFlowBar(bucket, trade.price, this.cvd);
    }
    this.cvd += delta;
    this.updateFlowBar(this.liveBar!, trade);
    this.liveBar!.cvd = this.cvd;
  }

  private updateFlowBar(bar: FlowBar, trade: NormalizedTrade): void {
    if (!bar.trades) bar.open = bar.high = bar.low = trade.price;
    bar.high = Math.max(bar.high, trade.price);
    bar.low = Math.min(bar.low, trade.price);
    bar.close = trade.price;
    bar.volume += trade.quantity;
    if (trade.side === "buy") bar.buyVolume += trade.quantity;
    else if (trade.side === "sell") bar.sellVolume += trade.quantity;
    bar.trades += 1;
    bar.cvd += trade.side === "buy" ? trade.quantity : trade.side === "sell" ? -trade.quantity : 0;
  }
}
