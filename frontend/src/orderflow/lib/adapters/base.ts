// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
// ============================================================
// BASE EXCHANGE ADAPTER
// All exchange adapters extend this abstract class
// ============================================================

import type { NormalizedTrade, NormalizedOrderBook, VenueConnection, Venue } from "@/lib/types/market";

interface AdapterPayloads {
  trade: NormalizedTrade;
  orderbook: NormalizedOrderBook;
  status: VenueConnection;
}
export type AdapterEvent = keyof AdapterPayloads;

export abstract class BaseExchangeAdapter {
  private readonly listeners: { [K in AdapterEvent]: Set<(payload: AdapterPayloads[K]) => void> } = {
    trade: new Set(),
    orderbook: new Set(),
    status: new Set(),
  };

  on<K extends AdapterEvent>(event: K, listener: (payload: AdapterPayloads[K]) => void): this {
    this.listeners[event].add(listener);
    return this;
  }

  removeListener<K extends AdapterEvent>(event: K, listener: (payload: AdapterPayloads[K]) => void): this {
    this.listeners[event].delete(listener);
    return this;
  }

  protected emit<K extends AdapterEvent>(event: K, payload: AdapterPayloads[K]): void {
    for (const listener of this.listeners[event]) listener(payload);
  }
  abstract readonly venue: Venue;
  protected ws: WebSocket | null = null;
  protected reconnectAttempts = 0;
  protected maxReconnectAttempts = 20;
  protected reconnectDelay = 1000;
  protected pingInterval: ReturnType<typeof setInterval> | null = null;
  protected reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  protected lastHeartbeat = 0;
  protected staleThresholdMs = 30_000;
  public symbol: string;
  protected status: VenueConnection["status"] = "connecting";
  protected errorMessage: string | undefined;
  protected stateFlag: string | undefined;
  private intentionallyOffline = false;

  constructor(symbol: string) {
    this.symbol = symbol;
  }

  connect(): void {
    const previous = this.ws;
    this.ws = null;
    previous?.close();
    this.intentionallyOffline = false;
    this.clearReconnectTimer();
    this.stopPingInterval();
    this.setStatus("connecting");

    const ws = new WebSocket(this.buildWsUrl()) as unknown as WebSocket;
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.reconnectAttempts = 0;
      this.lastHeartbeat = Date.now();
      this.errorMessage = undefined;
      this.stateFlag = undefined;
      this.setStatus("live");
      this.startPingInterval();
      this.subscribe();
    };
    ws.onmessage = (event: MessageEvent) => {
      if (this.ws !== ws) return;
      this.lastHeartbeat = Date.now();
      if (this.status !== "live") this.setStatus("live");
      try {
        this.handleMessage(JSON.parse(event.data as string));
      } catch (error) {
        this.reportError(
          `Unable to parse ${this.venue} message: ${
            error instanceof Error ? error.message : String(error)
          }`
        );
      }
    };
    ws.onerror = () => {
      if (this.ws === ws) this.reportError(`${this.venue} WebSocket error`);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.stopPingInterval();
      this.ws = null;
      if (!this.intentionallyOffline) {
        this.setStatus("connecting");
        this.reconnect();
      }
    };
  }

  disconnect(): void {
    this.intentionallyOffline = true;
    this.clearReconnectTimer();
    this.stopPingInterval();
    this.setStatus("offline");
    this.ws?.close();
    this.ws = null;
  }

  protected abstract buildWsUrl(): string;
  protected subscribe(): void {
    throw new Error(`${this.venue} adapter does not implement subscribe()`);
  }
  protected abstract handleMessage(raw: unknown): void;
  protected abstract sendPing(): void;

  protected reconnect(): void {
    if (this.intentionallyOffline || this.reconnectTimer) return;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.setStatus("offline");
      return;
    }
    const delay = Math.min(
      this.reconnectDelay * 2 ** this.reconnectAttempts,
      30_000
    );
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  protected setStatus(s: VenueConnection["status"]): void {
    this.status = s;
    if (s === "offline") {
      this.intentionallyOffline = true;
      this.clearReconnectTimer();
    } else if (s === "connecting") {
      this.intentionallyOffline = false;
    } else if (s === "live") {
      this.errorMessage = undefined;
      this.stateFlag = undefined;
    }
    this.emit("status", this.getStatus());
  }

  protected reportError(message: string, stateFlag?: string): void {
    this.errorMessage = message;
    this.stateFlag = stateFlag;
    this.setStatus("error");
  }

  protected requestResubscribe(): void {
    this.setStatus("stale");
    this.ws?.close();
  }

  protected startPingInterval(intervalMs = 10_000): void {
    this.stopPingInterval();
    this.pingInterval = setInterval(() => {
      if (Date.now() - this.lastHeartbeat > this.staleThresholdMs) {
        this.setStatus("stale");
        this.ws?.close();
        return;
      }
      this.sendPing();
    }, intervalMs);
  }

  protected stopPingInterval(): void {
    if (this.pingInterval) clearInterval(this.pingInterval);
    this.pingInterval = null;
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  protected emitTrade(trade: NormalizedTrade): void {
    if (
      !Number.isFinite(trade.timestamp) ||
      !Number.isFinite(trade.price) ||
      trade.price <= 0 ||
      !Number.isFinite(trade.quantity) ||
      trade.quantity <= 0
    ) {
      this.reportError(`Invalid ${this.venue} trade payload`);
      return;
    }
    this.emit("trade", trade);
  }

  protected emitOrderBook(book: NormalizedOrderBook): void {
    const isValidLevel = (level: NormalizedOrderBook["bids"][number]) =>
      Number.isFinite(level.price) &&
      level.price > 0 &&
      Number.isFinite(level.size) &&
      level.size > 0;
    const bids = book.bids.filter(isValidLevel);
    const asks = book.asks.filter(isValidLevel);
    // A momentarily one-sided book (e.g. while a delta stream is warming up)
    // is not a feed failure; skip it instead of flipping the venue to error.
    if (bids.length === 0 && asks.length === 0) {
      this.reportError(`Invalid ${this.venue} order book payload`);
      return;
    }
    if (bids.length === 0 || asks.length === 0) return;
    this.emit("orderbook", { ...book, bids, asks });
  }

  getStatus(): VenueConnection {
    return {
      venue: this.venue,
      status: this.status,
      lastHeartbeat: this.lastHeartbeat,
      reconnectAttempts: this.reconnectAttempts,
      error: this.errorMessage,
      stateFlag: this.stateFlag,
    };
  }
}
