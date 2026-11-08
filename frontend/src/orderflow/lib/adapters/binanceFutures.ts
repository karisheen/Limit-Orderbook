// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
// ============================================================
// BINANCE FUTURES ADAPTER
// Connects to Binance USD-M Futures WebSocket streams
// Provides: normalized trades + order book depth
// ============================================================

import { BaseExchangeAdapter } from "./base";
import type {
  NormalizedOrderBook,
  OrderBookLevel,
  Venue,
  VenueConnection,
} from "@/lib/types/market";

class BinanceFuturesStream extends BaseExchangeAdapter {
  readonly venue: Venue = "binance-futures";
  private bids = new Map<number, number>();
  private asks = new Map<number, number>();

  constructor(symbol: string, private readonly route: "public" | "market", private readonly stream: string) {
    super(symbol.toUpperCase());
  }
  protected buildWsUrl(): string { return `wss://fstream.binance.com/${this.route}/ws`; }

  protected subscribe(): void {
    const s = this.symbol.toLowerCase();
    this.ws?.send(JSON.stringify({
      method: "SUBSCRIBE",
      params: [`${s}@${this.stream}`],
      id: 1,
    }));
  }

  protected handleMessage(raw: unknown): void {
    const msg = raw as
      | { e: "aggTrade"; p: string; q: string; a: number; s: string; T: number; m: boolean }
      | { e: "depthUpdate"; b: [string, string][]; a: [string, string][] };
    if (msg.e === "aggTrade") {
      const price = parseFloat(msg.p), qty = parseFloat(msg.q);
      this.emitTrade({ id: `bf-${msg.a}`, venue: this.venue, symbol: msg.s, timestamp: msg.T, localTimestamp: Date.now(), price, quantity: qty, quoteVolume: price * qty, side: msg.m ? "sell" : "buy", marketType: "perpetual", sequence: msg.a });
    } else if (msg.e === "depthUpdate") {
      this.bids.clear(); this.asks.clear();
      for (const [p, q] of msg.b) { const price = parseFloat(p), qty = parseFloat(q); if (qty > 0) this.bids.set(price, qty); }
      for (const [p, q] of msg.a) { const price = parseFloat(p), qty = parseFloat(q); if (qty > 0) this.asks.set(price, qty); }
      this.emitBook();
    }
  }

  private emitBook(): void {
    const bids: OrderBookLevel[] = [...this.bids.entries()]
      .sort((a, b) => b[0] - a[0])
      .slice(0, 50)
      .map(([price, size]) => ({ price, size, venue: this.venue }));
    const asks: OrderBookLevel[] = [...this.asks.entries()]
      .sort((a, b) => a[0] - b[0])
      .slice(0, 50)
      .map(([price, size]) => ({ price, size, venue: this.venue }));
    const book: NormalizedOrderBook = { venue: this.venue, symbol: this.symbol, timestamp: Date.now(), localTimestamp: Date.now(), bids, asks };
    this.emitOrderBook(book);
  }

  protected sendPing(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ method: "LIST_SUBSCRIPTIONS", id: 2 }));
    }
  }
}

// Binance split depth and aggregate trades into separate routed endpoints.
// An unrouted /ws connection now receives depth but silently receives no trades.
export class BinanceFuturesAdapter extends BinanceFuturesStream {
  private readonly trades: BinanceFuturesStream;

  constructor(symbol: string) {
    super(symbol, "public", "depth20@100ms");
    this.trades = new BinanceFuturesStream(symbol, "market", "aggTrade");
    this.trades.on("trade", trade => this.emitTrade(trade));
    this.trades.on("status", () => this.emit("status", this.getStatus()));
  }

  connect(): void {
    super.connect();
    this.trades.connect();
  }

  disconnect(): void {
    this.trades.disconnect();
    super.disconnect();
  }

  getStatus(): VenueConnection {
    const book = super.getStatus();
    const trades = this.trades.getStatus();
    return {
      ...book,
      status: book.status === "live" ? trades.status : book.status,
      lastHeartbeat: Math.min(book.lastHeartbeat, trades.lastHeartbeat),
      reconnectAttempts: Math.max(book.reconnectAttempts, trades.reconnectAttempts),
      error: book.error ?? trades.error,
    };
  }
}
