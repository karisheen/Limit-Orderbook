import type { StoreApi } from "zustand";
import type { Instrument, MarketDataCursor, Mode, NativeMarketBook, NativeMarketData, NativeMarketTrade } from "../../../api";
import type { DisplayStatus, TerminalStore } from "@/lib/store/terminalStore";
import type { FlowBar, NormalizedOrderBook, NormalizedTrade, VenueConnection } from "@/lib/types/market";
import { displayCohort, displayMarketType, displaySymbol, displayVenue } from "@/lib/config/instruments";
import { aggregateBooks, computeBandObi } from "@/lib/aggregation/marketAggregation";
import { computeOBI, computeVolumeProfile } from "@/lib/engine/orderFlowEngine";
import { DisplayStudies } from "@/lib/aggregation/displayStudies";
import { resolveTickSize } from "@/lib/engine/analytics";

const START: MarketDataCursor = { delivery: "0", event: -1 };
const MAX_RETAINED_TRADES = 100_000;
export function compareCursor(a: MarketDataCursor, b: MarketDataCursor): number {
  const deliveryA = BigInt(a.delivery), deliveryB = BigInt(b.delivery);
  return deliveryA < deliveryB ? -1 : deliveryA > deliveryB ? 1 : a.event - b.event;
}
export interface DisplayScope {
  sessionId: string; generation: string; sourceId: string; primary: Instrument; sourceMode?: Mode;
}

/** A cursor consumer, never an exchange transport or execution authority. */
export class NativeMarketBridge {
  cursor: MarketDataCursor = { ...START };
  private scope: DisplayScope | null = null;
  private studies: DisplayStudies;
  private retained: NativeMarketTrade[] = [];
  private instruments = new Map<string, Instrument>();
  private books: NativeMarketBook[] = [];
  private reasons = new Set<string>();
  private gap = false;
  private lastGapCursor: string | null = null;
  private clock: { id: string; mono: bigint; wall: number } | null = null;
  private configuration = "";
  private tickInstrument = "";
  private depthCohort = "";
  private refreshVenues = false;
  private sourceIdentities = new Map<string, NativeMarketTrade["source_identity"]>();

  constructor(private readonly store: StoreApi<TerminalStore>) { this.studies = new DisplayStudies(store); }

  reset(scope: DisplayScope | null): void {
    const previous = this.scope;
    if (previous?.sessionId === scope?.sessionId && previous?.generation === scope?.generation &&
        previous?.sourceId === scope?.sourceId && previous?.primary.instrument_id === scope?.primary.instrument_id &&
        previous?.primary.base.asset_id === scope?.primary.base.asset_id && previous?.primary.market_type === scope?.primary.market_type &&
        previous?.primary.quote.asset_id === scope?.primary.quote.asset_id) {
      this.scope = scope;
      return;
    }
    this.scope = scope;
    this.cursor = { ...START };
    this.retained = [];
    this.instruments.clear();
    this.books = [];
    this.reasons.clear();
    this.gap = false;
    this.lastGapCursor = null;
    this.clock = null;
    this.configuration = "";
    this.depthCohort = "";
    this.tickInstrument = "";
    this.sourceIdentities.clear();
    this.studies = new DisplayStudies(this.store);
    this.store.getState().resetMarketData();
    if (scope) {
      const state = this.store.getState();
      const aggregate = state.venue === "all-perps" || state.venue === "all-spot";
      this.refreshVenues = aggregate;
      this.store.setState({
        symbol: displaySymbol(scope.primary), marketType: displayMarketType(scope.primary),
        venue: aggregate ? (scope.primary.market_type === "spot" ? "all-spot" : "all-perps") : displayVenue(scope.primary),
        selectedVenues: aggregate ? [] : [displayVenue(scope.primary)],
      });
      this.publish([]);
    }
  }

  configure(): void {
    const s = this.store.getState();
    const key = JSON.stringify([s.venue, s.selectedVenues, s.timeframe, s.tickSize, s.imbalanceThreshold, s.valueAreaPct]);
    if (key === this.configuration) return;
    this.configuration = key;
    this.rebuild();
  }

  private rebuild(): void {
    // Staggered stream transitions can leave only trades from a new clock.
    // Re-anchor only after all evidence in the previous monotonic domain is gone.
    const selected = new Set(this.cohort().map(instrument => instrument.instrument_id));
    if (this.clock && !this.retained.some(trade => selected.has(trade.instrument_id) && trade.asof.clock_id === this.clock!.id)) this.clock = null;
    this.studies = new DisplayStudies(this.store);
    this.store.getState().resetTradeStudies();
    this.publish(this.retained);
  }

  accept(page: NativeMarketData): boolean {
    if (!this.scope || page.session_id !== this.scope.sessionId || page.generation !== this.scope.generation) return false;
    if (!page.gap && compareCursor(page.cursor, this.cursor) < 0) return false;
    // A response is a complete current book cohort, not a patch: remove vanished books.
    this.books = page.books;
    this.instruments = new Map(page.instruments.map(instrument => [instrument.instrument_id, instrument]));
    this.instruments.set(this.scope.primary.instrument_id, this.scope.primary);
    const available = displayCohort(this.instruments.values(), this.scope.primary);
    const admitted = new Set(available.map(instrument => instrument.instrument_id));
    if (this.refreshVenues) {
      this.refreshVenues = false;
      this.store.getState().setSelectedVenues([...new Set(available.map(displayVenue))]);
    }
    const gapCursor = `${page.cursor.delivery}:${page.cursor.event}`;
    if (page.gap && this.lastGapCursor !== gapCursor) {
      this.lastGapCursor = gapCursor;
      this.gap = true;
      this.reasons.add("Native cursor gap: studies restart at available history");
      this.retained = [];
      this.cursor = { ...START };
      this.clock = null;
      this.sourceIdentities.clear();
      this.studies = new DisplayStudies(this.store);
      this.store.getState().resetTradeStudies();
    }
    const selected = new Set(this.cohort().map(instrument => instrument.instrument_id));
    let rebuild = JSON.stringify([...selected].sort()) !== this.depthCohort;
    if (rebuild) this.clock = null;
    this.retained = this.retained.filter(trade => {
      if (admitted.has(trade.instrument_id)) return true;
      this.sourceIdentities.delete(trade.stream_id);
      return false;
    });
    const fresh: NativeMarketTrade[] = [];
    // Native boundaries and trades share one cursor order; an epoch boundary can
    // precede a trade at the same cursor, so compare both with the page-start cursor.
    const events = [...page.boundaries, ...page.trades].sort((a, b) =>
      compareCursor(a.cursor, b.cursor) || (("kind" in a ? 0 : 1) - ("kind" in b ? 0 : 1)));
    for (const event of events) {
      if (compareCursor(event.cursor, this.cursor) <= 0) continue;
      const instrument = this.instruments.get(event.instrument_id);
      if (!instrument || !admitted.has(event.instrument_id)) continue;
      const visible = selected.has(event.instrument_id);
      if ("kind" in event) {
        if (visible) {
          this.gap = true;
          this.reasons.add(`Native trade ${event.kind} (${instrument.venue}): ${event.reason}; affected stream studies restarted`);
        }
        this.retained = this.retained.filter(trade => trade.stream_id !== event.stream_id);
        this.sourceIdentities.delete(event.stream_id);
        rebuild ||= visible;
      } else {
        const previous = this.sourceIdentities.get(event.stream_id);
        const identity = event.source_identity;
        if (previous && (previous.session_id !== identity.session_id || previous.segment_id !== identity.segment_id ||
            previous.clock_id !== identity.clock_id || previous.connection_epoch !== identity.connection_epoch)) {
          if (visible) {
            this.gap = true;
            this.reasons.add(`Native trade source identity changed (${instrument.venue}); affected stream studies restarted`);
          }
          this.retained = this.retained.filter(trade => trade.stream_id !== event.stream_id);
          rebuild ||= visible;
        }
        this.sourceIdentities.set(event.stream_id, identity);
        this.retained.push(event);
        fresh.push(event);
      }
    }
    if (compareCursor(page.cursor, this.cursor) > 0) this.cursor = page.cursor;
    if (this.retained.length > MAX_RETAINED_TRADES) {
      this.retained = this.retained.slice(-MAX_RETAINED_TRADES);
      this.reasons.add("Display study window limited to latest 100,000 native trades");
      rebuild = true;
    }
    if (rebuild) this.rebuild();
    else this.publish(fresh);
    return true;
  }

  fail(message: string): void {
    const status = this.store.getState().displayStatus;
    this.store.setState({ displayStatus: { ...status, reasons: [...new Set([...status.reasons, message])] } });
  }

  private cohort(): Instrument[] {
    if (!this.scope) return [];
    const state = this.store.getState();
    if (state.venue !== "all-perps" && state.venue !== "all-spot") return [this.scope.primary];
    return displayCohort(this.instruments.values(), this.scope.primary).filter(instrument =>
      !state.selectedVenues.length || state.selectedVenues.includes(displayVenue(instrument)));
  }

  private time(trade: NativeMarketTrade): number {
    const reference = trade.asof;
    const mono = BigInt(reference.receive_mono_ns);
    if (!this.clock) this.clock = { id: reference.clock_id, mono, wall: Number(BigInt(reference.receive_wall_ns) / 1_000_000n) };
    if (reference.clock_id !== this.clock.id) {
      this.reasons.add("Multiple native receive clocks; cross-clock studies unavailable");
      return NaN;
    }
    return this.clock.wall + Number(mono - this.clock.mono) / 1_000_000;
  }

  private plot(trade: NativeMarketTrade, instrument: Instrument): NormalizedTrade | null {
    if (trade.base_quantity === null) { this.reasons.add("Native base trade quantity unavailable"); return null; }
    const price = Number(trade.trade.price), quantity = Number(trade.base_quantity), timestamp = this.time(trade);
    if (!Number.isFinite(price) || !Number.isFinite(quantity) || !Number.isFinite(timestamp) || price <= 0 || quantity < 0) {
      this.reasons.add("Native trade outside plotting range"); return null;
    }
    return {
      id: `${trade.instrument_id}:${trade.cursor.delivery}:${trade.cursor.event}`,
      venue: displayVenue(instrument), symbol: displaySymbol(instrument), timestamp, localTimestamp: timestamp,
      price, quantity, quoteVolume: trade.quote_notional === null ? null : Number(trade.quote_notional),
      side: trade.trade.taker_side === "buy" ? "buy" : trade.trade.taker_side === "sell" ? "sell" : "unknown",
      marketType: displayMarketType(instrument), native: trade,
    };
  }

  private plotBook(book: NativeMarketBook, instrument: Instrument): NormalizedOrderBook | null {
    const levels = [...book.bids, ...book.asks];
    if (levels.some(level => level.base_quantity === null || !Number.isFinite(Number(level.base_quantity)) || !Number.isFinite(Number(level.price)))) {
      this.reasons.add("Native base depth quantity unavailable"); return null;
    }
    if (!book.eligibility?.eligible) return null;
    const timestamp = Number(BigInt(book.asof.receive_wall_ns) / 1_000_000n);
    const project = (rows: NativeMarketBook["bids"]) => rows.map(level => ({ price: Number(level.price), size: Number(level.base_quantity), venue: displayVenue(instrument) }));
    return { venue: displayVenue(instrument), symbol: displaySymbol(instrument), timestamp, localTimestamp: timestamp,
      bids: project(book.bids), asks: project(book.asks), native: book };
  }

  private publish(trades: NativeMarketTrade[]): void {
    const primary = this.scope?.primary;
    if (!primary) return;
    const cohort = this.cohort();
    const cohortKey = JSON.stringify(cohort.map(instrument => instrument.instrument_id).sort());
    if (cohortKey !== this.depthCohort) {
      this.depthCohort = cohortKey;
      this.store.getState().resetDepthHistory();
    }
    const selected = new Map(cohort.map(instrument => [instrument.instrument_id, instrument]));
    const completedBars: FlowBar[] = [], plotted: NormalizedTrade[] = [];
    let unknown = this.store.getState().displayStatus.unknownSideTrades;
    let history = this.store.getState().displayStatus.historyTrades;
    for (const native of trades) {
      const instrument = selected.get(native.instrument_id);
      if (!instrument) continue;
      if (native.trade.is_subscription_history) history++;
      if (native.trade.taker_side !== "buy" && native.trade.taker_side !== "sell") unknown++;
      const trade = this.plot(native, instrument);
      if (!trade) continue;
      if (this.tickInstrument !== primary.instrument_id) {
        this.tickInstrument = primary.instrument_id;
        const minimum = Number(primary.tick_size);
        const automatic = resolveTickSize(trade.price);
        this.store.getState().setTickSize(minimum > 0 ? Math.ceil(automatic / minimum) * minimum : automatic);
      }
      plotted.push(trade);
      // History has no trustworthy live ordering. Show it on tape, never invent historical CVD.
      if (!native.trade.is_subscription_history) this.studies.apply(trade, completedBars);
    }
    const books = new Map<string, NormalizedOrderBook>();
    const connections: VenueConnection[] = [];
    const qualityReasons = new Set<string>();
    for (const native of this.books) {
      const instrument = selected.get(native.instrument_id);
      if (!instrument) continue;
      const q = native.quality;
      for (const reason of [...q.reason_codes, ...(native.eligibility?.reasons ?? [])]) qualityReasons.add(reason);
      if (!q.coverage.full_depth) qualityReasons.add("Partial native depth coverage");
      if (native.presentation.bids_omitted || native.presentation.asks_omitted) qualityReasons.add("Native depth presentation truncated");
      if (!q.capture_complete) qualityReasons.add("Native capture incomplete");
      const book = this.plotBook(native, instrument);
      if (book) books.set(instrument.instrument_id, book);
      connections.push({ venue: instrument.venue,
        status: native.eligibility?.eligible && book ? "live" : "stale",
        lastHeartbeat: book?.timestamp ?? 0, reconnectAttempts: 0,
        stateFlag: `${q.source_mode}${q.recorded_source_mode ? `/${q.recorded_source_mode}` : ""} · ${q.lifecycle} · ${q.integrity}`,
      });
    }
    if (cohort.some(instrument => instrument.quote.asset_id !== primary.quote.asset_id)) qualityReasons.add("Indicative cross-quote aggregation; native quotes are not converted");
    if (cohort.length > 1) qualityReasons.add("Display cohort only; costs and paper use native primary");
    if (!books.size) qualityReasons.add("No eligible native depth in selected display cohort");
    if (history) qualityReasons.add("Subscription history is tape-only, excluded from ordered studies");
    if (unknown) qualityReasons.add("Unknown aggressor side contributes volume, not signed delta");
    const aggregate = aggregateBooks(books);
    const band = aggregate ? computeBandObi(books, aggregate) : null;
    const { candle, session, liveBar } = this.studies;
    const state = this.store.getState();
    state.applyMarketDataBatch({
      trades: plotted, orderBook: books.get(primary.instrument_id) ?? null, aggregatedBook: aggregate,
      candle: candle ? { ...candle } : null,
      volumeProfile: candle ? computeVolumeProfile([...state.candles, candle], state.valueAreaPct) : null,
      ticker: session ? { venue: primary.venue, symbol: displaySymbol(primary), price: session.last,
        open: session.open, high: session.high, low: session.low, change: session.last - session.open,
        changePct: session.open ? (session.last / session.open - 1) * 100 : 0, volume: session.volume,
        timestamp: liveBar?.time ?? session.startTime } : null,
      obi: band && aggregate ? { timestamp: aggregate.timestamp, value: computeOBI(band.bidVolume, band.askVolume), ...band } : null,
      flowBars: completedBars, liveBar: liveBar ? { ...liveBar } : null, session: session ? { ...session } : null, connections,
    });
    const displayStatus: DisplayStatus = { reasons: [...this.reasons, ...qualityReasons],
      primaryInstrumentId: primary.instrument_id, instrumentIds: cohort.map(instrument => instrument.instrument_id),
      gap: this.gap, unknownSideTrades: unknown, historyTrades: history,
      lagObservation: this.scope?.sourceMode === "replay" ? "recorded" : this.scope?.sourceMode === "demo" ? "synthetic" :
        this.scope?.sourceMode === "live" ? "live" : "unknown" };
    this.store.setState({ displayStatus, connections: Object.fromEntries(connections.map(connection => [connection.venue, connection])) });
  }
}

/** Exclusive cursor drain: serialize every page, including pages containing only nontrade events. */
export async function drainNativeMarketData(
  bridge: NativeMarketBridge,
  request: (cursor: MarketDataCursor) => Promise<NativeMarketData>,
  signal: AbortSignal,
): Promise<void> {
  while (!signal.aborted) {
    const before = bridge.cursor;
    const page = await request(before);
    if (signal.aborted || !bridge.accept(page)) return;
    if (!page.has_more) return;
    const advancement = compareCursor(bridge.cursor, before);
    if (advancement === 0 || (advancement < 0 && !page.gap)) throw new Error("Native market cursor did not advance");
  }
}
