// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
// ============================================================
// ZUSTAND STORE — Per-session market settings + market data
// UI preferences (theme, panels, profiles) live in uiStore.
// ============================================================

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type {
  Venue,
  VenueSelection,
  MarketType,
  Timeframe,
  NormalizedTrade,
  NormalizedOrderBook,
  AggregatedOrderBook,
  FootprintCandle,
  VolumeProfile,
  OBISnapshot,
  FlowBar,
  SessionStats,
  VenueConnection,
  MarketTicker,
} from "@/lib/types/market";
import { RingBuffer } from "@/lib/session/RingBuffer";

export type OrderBookMode = "single" | "aggregated" | "compact";

// ----------------------------------------------------------
// Terminal Settings
// ----------------------------------------------------------
export interface TerminalSettings {
  symbol: string;
  venue: VenueSelection;
  selectedVenues: Venue[];
  marketType: MarketType;
  timeframe: Timeframe;
  tickSize: number;
  imbalanceThreshold: number;
  stackedImbalanceLevels: number;
  orderBookMode: OrderBookMode;
  valueAreaPct: number;
}

// ----------------------------------------------------------
// Market Data State
// ----------------------------------------------------------
export interface DisplayStatus {
  reasons: string[];
  primaryInstrumentId: string | null;
  instrumentIds: string[];
  gap: boolean;
  unknownSideTrades: number;
  historyTrades: number;
  lagObservation: "live" | "recorded" | "synthetic" | "unknown";
}
export interface MarketDataState {
  displayStatus: DisplayStatus;
  candles: FootprintCandle[];
  currentCandle: FootprintCandle | null;
  volumeProfile: VolumeProfile | null;
  recentTrades: NormalizedTrade[];
  orderBook: NormalizedOrderBook | null;
  aggregatedBook: AggregatedOrderBook | null;
  obiHistory: OBISnapshot[];
  currentOBI: number;
  flowBars: FlowBar[]; // completed 1-second bars
  liveBar: FlowBar | null; // bar still being built
  session: SessionStats | null;
  connections: Record<string, VenueConnection>;
  ticker: MarketTicker | null;
  crosshairTime: number | null;
  crosshairPrice: number | null;
}

// ----------------------------------------------------------
// Actions
// ----------------------------------------------------------
export interface TerminalActions {
  setSymbol: (s: string) => void;
  setVenue: (v: VenueSelection) => void;
  setSelectedVenues: (venues: Venue[]) => void;
  setMarketType: (m: MarketType) => void;
  setTimeframe: (t: Timeframe) => void;
  setTickSize: (ts: number) => void;
  setImbalanceThreshold: (t: number) => void;
  setOrderBookMode: (m: OrderBookMode) => void;
  setValueAreaPct: (pct: number) => void;

  finalizeCandle: (candle: FootprintCandle) => void;
  updateConnection: (conn: VenueConnection) => void;
  applyMarketDataBatch: (batch: MarketDataBatch) => void;
  setCrosshair: (time: number | null, price: number | null) => void;
  resetMarketData: () => void;
  resetTradeStudies: () => void;
  resetDepthHistory: () => void;
}

export type TerminalStore = TerminalSettings & MarketDataState & TerminalActions;

export interface MarketDataBatch {
  trades: NormalizedTrade[];
  orderBook: NormalizedOrderBook | null;
  aggregatedBook: AggregatedOrderBook | null;
  candle: FootprintCandle | null;
  volumeProfile: VolumeProfile | null;
  ticker: MarketTicker | null;
  obi: OBISnapshot | null;
  flowBars: FlowBar[];
  liveBar: FlowBar | null;
  session: SessionStats | null;
  connections: VenueConnection[];
}

export const MAX_FLOW_BARS = 7200; // two hours of 1-second bars
const MAX_CANDLES = 500;
const MAX_OBI_POINTS = 600;

const INITIAL_SETTINGS: TerminalSettings = {
  symbol: "",
  venue: "",
  selectedVenues: [],
  marketType: "spot",
  timeframe: "5m",
  tickSize: 10,
  imbalanceThreshold: 3,
  stackedImbalanceLevels: 3,
  orderBookMode: "single",
  valueAreaPct: 0.7,
};

const INITIAL_MARKET: MarketDataState = {
  displayStatus: {
    reasons: [], primaryInstrumentId: null, instrumentIds: [],
    gap: false, unknownSideTrades: 0, historyTrades: 0, lagObservation: "unknown",
  },
  candles: [],
  currentCandle: null,
  volumeProfile: null,
  recentTrades: [],
  orderBook: null,
  aggregatedBook: null,
  obiHistory: [],
  currentOBI: 0,
  flowBars: [],
  liveBar: null,
  session: null,
  connections: {},
  ticker: null,
  crosshairTime: null,
  crosshairPrice: null,
};

// ----------------------------------------------------------
// Create the store
// ----------------------------------------------------------
export const createTerminalStore = () => {
  const recentTradeBuffer = new RingBuffer<NormalizedTrade>(500);
  return create<TerminalStore>()(
    subscribeWithSelector((set) => ({
      ...INITIAL_SETTINGS,
      ...INITIAL_MARKET,

      setSymbol: (symbol) => set({ symbol }),

      setVenue: (venue) =>
        set((s) => ({
          venue,
          selectedVenues:
            venue === "all-perps" || venue === "all-spot" ? s.selectedVenues : [venue],
          orderBookMode: venue === "all-perps" || venue === "all-spot" ? "aggregated" : "single",
        })),

      setSelectedVenues: (selectedVenues) => set({ selectedVenues }),

      setMarketType: (marketType) => set({ marketType }),

      setTimeframe: (timeframe) => set({ timeframe }),

      setTickSize: (tickSize) => set({ tickSize }),

      setImbalanceThreshold: (imbalanceThreshold) => set({ imbalanceThreshold }),

      setOrderBookMode: (orderBookMode) => set({ orderBookMode }),

      setValueAreaPct: (valueAreaPct) => set({ valueAreaPct }),

      finalizeCandle: (candle) =>
        set((s) => ({
          candles: [...s.candles, { ...candle, complete: true }].slice(-MAX_CANDLES),
          currentCandle: null,
        })),

      updateConnection: (conn) =>
        set((s) => ({ connections: { ...s.connections, [conn.venue]: conn } })),

      applyMarketDataBatch: (batch) =>
        set((s) => {
          for (const trade of batch.trades) recentTradeBuffer.push(trade);
          return {
            recentTrades: batch.trades.length ? recentTradeBuffer.newestFirst() : s.recentTrades,
            orderBook: batch.orderBook,
            aggregatedBook: batch.aggregatedBook,
            currentCandle: batch.candle,
            volumeProfile: batch.volumeProfile,
            ticker: batch.ticker,
            currentOBI: batch.obi?.value ?? (batch.aggregatedBook ? s.currentOBI : 0),
            obiHistory: batch.obi && (s.obiHistory.at(-1)?.timestamp !== batch.obi.timestamp ||
              s.obiHistory.at(-1)?.bidVolume !== batch.obi.bidVolume ||
              s.obiHistory.at(-1)?.askVolume !== batch.obi.askVolume)
              ? [...s.obiHistory, batch.obi].slice(-MAX_OBI_POINTS) : s.obiHistory,
            flowBars: batch.flowBars.length
              ? [...s.flowBars, ...batch.flowBars].slice(-MAX_FLOW_BARS)
              : s.flowBars,
            liveBar: batch.liveBar,
            session: batch.session,
            connections: Object.fromEntries(batch.connections.map((c) => [c.venue, c])),
          };
        }),

      setCrosshair: (crosshairTime, crosshairPrice) => set({ crosshairTime, crosshairPrice }),

      // Trade discontinuities and display controls do not erase independent depth evidence.
      resetTradeStudies: () => {
        recentTradeBuffer.clear();
        set((s) => ({
          candles: [], currentCandle: null, volumeProfile: null, recentTrades: [],
          flowBars: [], liveBar: null, session: null, ticker: null,
          displayStatus: { ...s.displayStatus, unknownSideTrades: 0, historyTrades: 0 },
        }));
      },

      resetDepthHistory: () => set({ orderBook: null, aggregatedBook: null, obiHistory: [], currentOBI: 0, connections: {} }),

      resetMarketData: () => {
        recentTradeBuffer.clear();
        set(INITIAL_MARKET);
      },
    }))
  );
};
