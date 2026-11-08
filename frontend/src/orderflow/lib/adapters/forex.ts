// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
import { BaseExchangeAdapter } from "./base";
import { VENUE_CONFIG } from "@/lib/config/venues";
import type {
  NormalizedTrade,
  Venue,
} from "@/lib/types/market";

export function isForexMarketOpen(date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    hour12: false,
  }).formatToParts(date);
  const weekday = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value);
  if (weekday === "Sat") return false;
  if (weekday === "Sun") return hour >= 17;
  if (weekday === "Fri") return hour < 17;
  return true;
}

abstract class ForexAdapter extends BaseExchangeAdapter {
  protected readonly marketType = "forex" as const;
  private previousPrice: number | null = null;
  private previousSide: "buy" | "sell" = "buy";
  private flowBias = 0;

  // Finnhub supplies last-price ticks, not bid/ask quotes or executable depth.
  // Preserve the reference's explicitly marked synthetic quote/flow proxy.
  protected emitPriceTick(
    price: number,
    timestamp = Date.now(),
    size = 1,
    id = `${this.venue}-${timestamp}`,
  ): void {
    if (!Number.isFinite(timestamp) || !Number.isFinite(price) || price <= 0) return;
    if (!isForexMarketOpen(new Date(timestamp))) {
      this.reportError("MARKET CLOSED 🚫", "MARKET CLOSED 🚫");
      return;
    }
    const side = this.previousPrice === null
      ? this.previousSide
      : price > this.previousPrice
        ? "buy"
        : price < this.previousPrice
          ? "sell"
          : this.previousSide;
    this.previousPrice = price;
    this.previousSide = side;
    this.flowBias = Math.max(-0.75, Math.min(0.75, this.flowBias + (side === "buy" ? 0.08 : -0.08)));
    const quantity = Number.isFinite(size) && size > 0 ? size : 1;
    const trade: NormalizedTrade = {
      id,
      venue: this.venue,
      symbol: this.symbol,
      timestamp,
      localTimestamp: Date.now(),
      price,
      quantity,
      quoteVolume: price * quantity,
      side,
      marketType: this.marketType,
      dataBasis: "fx-proxy",
    };
    this.emitTrade(trade);
    const spread = Math.max(price * 0.00005, 0.00001);
    this.emitOrderBook({
      venue: this.venue,
      symbol: this.symbol,
      timestamp,
      localTimestamp: Date.now(),
      bids: [{ price: price - spread, size: quantity * (1 + this.flowBias), venue: this.venue }],
      asks: [{ price: price + spread, size: quantity * (1 - this.flowBias), venue: this.venue }],
      dataBasis: "fx-proxy",
    });
  }

}

export class FinnhubForexAdapter extends ForexAdapter {
  readonly venue: Venue = "finnhub-forex";

  connect(): void {
    if (!import.meta.env?.VITE_FINNHUB_API_KEY) {
      this.reportError("Missing VITE_FINNHUB_API_KEY — configure frontend/.env.local and rebuild");
      return;
    }
    super.connect();
  }
  protected buildWsUrl(): string {
    const token = import.meta.env?.VITE_FINNHUB_API_KEY;
    return `${VENUE_CONFIG[this.venue]!.websocketUrl}?token=${encodeURIComponent(token ?? "")}`;
  }

  protected subscribe(): void {
    if (!import.meta.env?.VITE_FINNHUB_API_KEY) {
      this.reportError("Missing VITE_FINNHUB_API_KEY");
      return;
    }
    this.ws?.send(JSON.stringify({ type: "subscribe", symbol: VENUE_CONFIG[this.venue]!.formatSymbol(this.symbol) }));
  }

  protected handleMessage(raw: unknown): void {
    const message = raw as { type?: string; data?: Array<{ p: number; t: number; v?: number }> };
    if (message.type !== "trade" || !message.data) return;
    for (const tick of message.data) {
      const price = Number(tick.p);
      this.emitPriceTick(price, Number(tick.t), Number(tick.v));
    }
  }

  protected sendPing(): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "ping" }));
  }
}
