// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
// ============================================================
// SHARED MARKET TYPES — Normalized across all exchanges
// ============================================================

import type { NativeMarketBook, NativeMarketTrade } from "../../../api";

/** Native venue identifiers; never browser-adapter aliases. */
export type Venue = string;
export type VenueSelection = Venue | "all-perps" | "all-spot";

export type MarketType = "spot" | "perpetual";
export type TradeSide = "buy" | "sell" | "unknown";
export type ConnectionStatus = "connecting" | "live" | "offline" | "error" | "stale";

export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "4h";

// ----------------------------------------------------------
// Numeric plotting projection of native evidence
// ----------------------------------------------------------
export interface NormalizedTrade {
  id: string;
  venue: Venue;
  symbol: string;
  timestamp: number; // native receive clock projected to milliseconds
  localTimestamp: number; // local receive timestamp (ms)
  price: number;
  quantity: number; // base asset quantity
  quoteVolume: number | null; // native quote notional, unavailable when unvalued
  side: TradeSide;
  marketType: MarketType;
  sequence?: number;
  native?: NativeMarketTrade;
}

// ----------------------------------------------------------
// Normalized Order Book Entry
// ----------------------------------------------------------
export interface OrderBookLevel {
  price: number;
  size: number;
  venue?: Venue;
}

export interface NormalizedOrderBook {
  venue: Venue;
  symbol: string;
  timestamp: number;
  localTimestamp: number;
  bids: OrderBookLevel[]; // sorted descending
  asks: OrderBookLevel[]; // sorted ascending
  sequence?: number;
  native?: NativeMarketBook;
}

export interface HeatmapSettings {
  tradedVolume: boolean;
}

// ----------------------------------------------------------
// Footprint Price Level
// ----------------------------------------------------------
export interface FootprintLevel {
  price: number;
  bidVolume: number;
  askVolume: number;
  totalVolume: number;
  delta: number;
  tradeCount: number;
  buyTrades: number;
  sellTrades: number;
  maxTradeSize: number;
  imbalance: "ask" | "bid" | "neutral"; // ask imbalance = ask/bid >= 3x
  isHighVolume: boolean;
  zScore: number;
}

// ----------------------------------------------------------
// Footprint Candle (one time bucket)
// ----------------------------------------------------------
export interface FootprintCandle {
  openTime: number;
  closeTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  delta: number;
  cumulativeDelta: number;
  levels: Map<number, FootprintLevel>; // price -> level
  sortedPrices: number[]; // ascending
  tradeCount: number;
  maxVolumeAtLevel: number;
  complete: boolean;
  vwap?: number; // session VWAP at the latest trade in this candle
  vwapStd?: number; // volume-weighted σ of session prices at that point
  venueVolumes: Partial<Record<Venue, number>>;
}

// ----------------------------------------------------------
// Volume Profile
// ----------------------------------------------------------
export interface VolumeProfileLevel {
  price: number;
  totalVolume: number;
  bidVolume: number;
  askVolume: number;
  delta: number;
  tradeCount: number;
  isPOC: boolean;
  isVAH: boolean;
  isVAL: boolean;
}

export interface VolumeProfile {
  levels: VolumeProfileLevel[];
  poc: number; // price of control
  vah: number; // value area high
  val: number; // value area low
  totalVolume: number;
  buyVolume: number;
  sellVolume: number;
  delta: number;
}

// ----------------------------------------------------------
// OBI — Order Book Imbalance
// ----------------------------------------------------------
export interface OBISnapshot {
  timestamp: number;
  value: number; // -1 to +1
  bidVolume: number;
  askVolume: number;
}

// ----------------------------------------------------------
// Stacked Imbalance
// ----------------------------------------------------------
export interface StackedImbalance {
  direction: "buy" | "sell";
  startPrice: number;
  endPrice: number;
  levels: number; // count of consecutive levels
  candle: FootprintCandle;
}

// ----------------------------------------------------------
// Delta Divergence
// ----------------------------------------------------------
export interface DeltaDivergence {
  type: "price-up-delta-down" | "price-down-delta-up";
  candleIndex: number;
  price: number;
  delta: number;
}

// ----------------------------------------------------------
// Potential Absorption
// ----------------------------------------------------------
export interface AbsorptionEvent {
  timestamp: number;
  price: number;
  side: TradeSide;
  aggressiveVolume: number;
  priceMovement: number;
}

// ----------------------------------------------------------
// Flow bar — fixed 1-second time bucket of executed flow.
// Source series for volume z-score, realized volatility and CVD.
// ----------------------------------------------------------
export interface FlowBar {
  time: number; // bucket open (ms)
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  buyVolume: number;
  sellVolume: number;
  trades: number;
  cvd: number; // session cumulative delta at bar close
}

// ----------------------------------------------------------
// Session statistics (since the feed connected)
// ----------------------------------------------------------
export interface SessionStats {
  startTime: number;
  open: number;
  high: number;
  low: number;
  last: number;
  volume: number;
  notional: number;
  buyVolume: number;
  sellVolume: number;
  trades: number;
  maxTrade: number;
  vwap: number;
  vwapStd: number;
  sumPQ: number;
  sumP2Q: number;
  lastTradeLagMs: number | null;
}

// ----------------------------------------------------------
// Connection Status per venue
// ----------------------------------------------------------
export interface VenueConnection {
  venue: Venue;
  status: ConnectionStatus;
  lastHeartbeat: number;
  latencyMs?: number;
  reconnectAttempts: number;
  error?: string;
  stateFlag?: string;
}

// ----------------------------------------------------------
// Aggregated Order Book (multi-venue)
// ----------------------------------------------------------
export interface AggregatedOrderBook {
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  venues: Venue[];
  timestamp: number;
  spread: number;
  spreadPct: number;
  midPrice: number;
}

// ----------------------------------------------------------
// Tool Profile Layout
// ----------------------------------------------------------
export type ToolProfile = "order-flow" | "liquidity" | "volatility";


export interface PanelVisibility {
  footprint: boolean;
  volumeProfile: boolean; // session profile attached to the footprint
  heatmap: boolean; // traded-volume heatmap behind the footprint
  orderBook: boolean;
  obi: boolean;
  timeSales: boolean;
  cvd: boolean;
  volumeZ: boolean;
  volatility: boolean;
  depthOfMarket: boolean;
}


// ----------------------------------------------------------
// Market Ticker
// ----------------------------------------------------------
export interface MarketTicker {
  venue: Venue;
  symbol: string;
  price: number;
  open: number;
  high: number;
  low: number;
  change: number;
  changePct: number;
  volume: number;
  timestamp: number;
}
