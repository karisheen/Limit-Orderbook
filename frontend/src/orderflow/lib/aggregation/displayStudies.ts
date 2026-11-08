// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: display-only studies over canonical native events.
import type { StoreApi } from "zustand";
import type { TerminalStore } from "@/lib/store/terminalStore";
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

  constructor(private readonly store: StoreApi<TerminalStore>) {}

  apply(trade: NormalizedTrade, completedBars: FlowBar[]): void {
    const settings = this.store.getState();
    const openTime = Math.max(getCandleOpenTime(trade.timestamp, settings.timeframe), this.candle?.openTime ?? -Infinity);
    if (!this.candle || this.candle.openTime !== openTime) {
      if (this.candle) {
        this.finalizedDelta += this.candle.delta;
        settings.finalizeCandle({ ...this.candle, cumulativeDelta: this.finalizedDelta, complete: true });
        this.levels.push(...this.candle.levels.values());
        if (this.levels.length > 5000) this.levels = this.levels.slice(-3000);
      }
      this.candle = createEmptyCandle(openTime, settings.timeframe);
    }
    applyTradeToCandle(this.candle, trade, settings.tickSize, settings.imbalanceThreshold);
    this.candle.cumulativeDelta = this.finalizedDelta + this.candle.delta;
    if (this.levels.length > 1) applyZScores(this.candle, this.levels);
    this.session ??= createSessionStats(trade.timestamp, trade.price);
    applyTradeToSession(this.session, trade.price, trade.quantity, trade.side, nativeTradeLagMs(trade));
    this.candle.vwap = this.session.vwap;
    this.candle.vwapStd = this.session.vwapStd;
    const bucket = Math.floor(trade.timestamp / 1000) * 1000;
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
    const bar = this.liveBar;
    this.cvd += trade.side === "buy" ? trade.quantity : trade.side === "sell" ? -trade.quantity : 0;
    bar.high = Math.max(bar.high, trade.price);
    bar.low = Math.min(bar.low, trade.price);
    bar.close = trade.price;
    bar.volume += trade.quantity;
    if (trade.side === "buy") bar.buyVolume += trade.quantity;
    else if (trade.side === "sell") bar.sellVolume += trade.quantity;
    bar.trades += 1;
    bar.cvd = this.cvd;
  }
}
