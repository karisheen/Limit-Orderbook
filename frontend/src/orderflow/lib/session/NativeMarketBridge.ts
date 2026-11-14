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

/** Floor while exact, then enforce the JavaScript date and integer ranges. */
function displayMilliseconds(value: string): number {
  const nanoseconds = BigInt(value);
  const timestamp = Number(nanoseconds / 1_000_000n);
  return nanoseconds >= 0n && Number.isSafeInteger(timestamp) && timestamp <= 8_640_000_000_000_000
    ? timestamp : NaN;
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
  private configuration = "";
  private tickInstrument = "";
  private depthCohort = "";
  private refreshVenues = false;
  private sourceIdentities = new Map<string, NativeMarketTrade["source_identity"]>();
  private displayTimeMs: number | null = null;
  private retentionKey = "";
  private retainedDirty = true;
  private retainedInstrumentIds: string[] = [];
  private studyMetadataKey = "";
  private bookKey = "";
  private projectedBooks = new Map<string, NormalizedOrderBook>();
  private aggregate: TerminalStore["aggregatedBook"] = null;
  private band: { bidVolume: number; askVolume: number } | null = null;
  private bookReasons: string[] = [];
  private profileArea = NaN;

  constructor(private readonly store: StoreApi<TerminalStore>) { this.studies = new DisplayStudies(store); }

  reset(scope: DisplayScope | null): void {
    const previous = this.scope;
    // Optional mode omission retains the prior explicit mode; an actual mode
    // transition is a source fence, even if a caller reuses its generation.
    if (scope && scope.sourceMode === undefined && previous) scope = { ...scope, sourceMode: previous.sourceMode };
    if (previous?.sessionId === scope?.sessionId && previous?.generation === scope?.generation &&
        previous?.sourceId === scope?.sourceId && previous?.sourceMode === scope?.sourceMode &&
        previous?.primary.instrument_id === scope?.primary.instrument_id &&
        previous?.primary.base.asset_id === scope?.primary.base.asset_id && previous?.primary.market_type === scope?.primary.market_type &&
        previous?.primary.quote.asset_id === scope?.primary.quote.asset_id) {
      const metadataChanged = JSON.stringify(previous?.primary) !== JSON.stringify(scope?.primary);
      this.scope = scope;
      if (metadataChanged && scope) {
        this.instruments.set(scope.primary.instrument_id, scope.primary);
        this.rebuild();
      }
      return;
    }
    this.scope = scope;
    this.cursor = { ...START };
    this.retained = [];
    this.displayTimeMs = null;
    this.instruments.clear();
    this.books = [];
    this.reasons.clear();
    this.gap = false;
    this.lastGapCursor = null;
    this.configuration = "";
    this.depthCohort = "";
    this.tickInstrument = "";
    this.retentionKey = "";
    this.retainedDirty = true;
    this.retainedInstrumentIds = [];
    this.studyMetadataKey = "";
    this.bookKey = "";
    this.projectedBooks = new Map();
    this.aggregate = null;
    this.band = null;
    this.bookReasons = [];
    this.profileArea = NaN;
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
    const key = JSON.stringify([s.venue, s.selectedVenues, s.timeframe, s.tickSize, s.imbalanceThreshold]);
    if (key !== this.configuration) {
      this.configuration = key;
      this.rebuild();
    } else if (s.valueAreaPct !== this.profileArea) {
      this.publish([]);
    }
  }

  private rebuild(): void {
    this.studies = new DisplayStudies(this.store);
    this.store.getState().resetTradeStudies();
    this.retainedDirty = true;
    this.profileArea = NaN;
    this.publish(this.retained, true);
  }

  accept(page: NativeMarketData): boolean {
    if (!this.scope || page.session_id !== this.scope.sessionId || page.generation !== this.scope.generation) return false;
    if (!page.gap && compareCursor(page.cursor, this.cursor) < 0) return false;
    const frontier = page.display_frontier_ns === null ? NaN : displayMilliseconds(page.display_frontier_ns);
    this.displayTimeMs = Number.isFinite(frontier) ? frontier : null;
    if (page.display_frontier_ns !== null && this.displayTimeMs === null) this.reasons.add("Native source frontier outside plotting range");
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
    const rewind = page.gap && compareCursor(page.cursor, this.cursor) < 0;
    if (page.gap && this.lastGapCursor !== gapCursor) {
      this.lastGapCursor = gapCursor;
      this.gap = true;
      this.reasons.add("Native cursor gap: studies limited to available history");
    }
    if (rewind) {
      this.retained = [];
      this.cursor = { ...START };
      this.sourceIdentities.clear();
      this.retainedDirty = true;
      this.retentionKey = "";
    }
    const cohort = this.cohort();
    const selected = new Set(cohort.map(instrument => instrument.instrument_id));
    const metadataKey = JSON.stringify(cohort);
    let rebuild = rewind || metadataKey !== this.studyMetadataKey;
    // Floors are inclusive retirement cursors, independent of pagination and
    // whether this client has already caught up. Never discard healthy peers.
    const retained = (trade: NativeMarketTrade): boolean => {
      const streamFloor = page.retention.stream_floors[trade.stream_id];
      return admitted.has(trade.instrument_id) &&
        compareCursor(trade.cursor, page.retention.floor) > 0 &&
        (!streamFloor || compareCursor(trade.cursor, streamFloor) > 0);
    };
    const retentionKey = JSON.stringify([[...admitted].sort(), page.retention.floor, page.retention.stream_floors]);
    if (retentionKey !== this.retentionKey) {
      this.retentionKey = retentionKey;
      const previousCount = this.retained.length;
      this.retained = this.retained.filter(retained);
      if (this.retained.length !== previousCount) {
        this.retainedDirty = true;
        rebuild = true;
        this.reasons.add("Native retention floors limit study history");
      }
    }
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
        this.retainedDirty = true;
        this.sourceIdentities.delete(event.stream_id);
        rebuild ||= visible;
      } else {
        if (!retained(event)) continue;
        const previous = this.sourceIdentities.get(event.stream_id);
        const identity = event.source_identity;
        if (previous && (previous.session_id !== identity.session_id || previous.segment_id !== identity.segment_id ||
            previous.clock_id !== identity.clock_id || previous.connection_epoch !== identity.connection_epoch)) {
          if (visible) {
            this.gap = true;
            this.reasons.add(`Native trade source identity changed (${instrument.venue}); affected stream studies restarted`);
          }
          this.retained = this.retained.filter(trade => trade.stream_id !== event.stream_id);
          this.retainedDirty = true;
          rebuild ||= visible;
        }
        this.sourceIdentities.set(event.stream_id, identity);
        this.retained.push(event);
        this.retainedDirty = true;
        fresh.push(event);
      }
    }
    if (compareCursor(page.cursor, this.cursor) > 0) this.cursor = page.cursor;
    if (this.retained.length > MAX_RETAINED_TRADES) {
      this.retained = this.retained.slice(-MAX_RETAINED_TRADES);
      this.retainedDirty = true;
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

  private plot(trade: NativeMarketTrade, instrument: Instrument): NormalizedTrade | null {
    if (trade.base_quantity === null) { this.reasons.add("Native base trade quantity unavailable"); return null; }
    const price = Number(trade.trade.price), quantity = Number(trade.base_quantity), timestamp = displayMilliseconds(trade.display_time_ns);
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

  private publish(trades: NativeMarketTrade[], rebuilding = false): void {
    const primary = this.scope?.primary;
    if (!primary) return;
    const cohort = this.cohort();
    const cohortKey = JSON.stringify(cohort.map(instrument => instrument.instrument_id).sort());
    if (cohortKey !== this.depthCohort) {
      this.depthCohort = cohortKey;
      this.store.getState().resetDepthHistory();
      this.retainedDirty = true;
      this.bookKey = "";
    }
    const selected = new Map(cohort.map(instrument => [instrument.instrument_id, instrument]));
    this.studyMetadataKey = JSON.stringify(cohort);
    const previousCandle = this.studies.candle;
    let studiesChanged = rebuilding;
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
      if (!native.trade.is_subscription_history) {
        this.studies.apply(trade, completedBars);
        studiesChanged = true;
      }
    }
    this.studies.advance(this.displayTimeMs);
    this.studies.flush();
    const candlesChanged = studiesChanged || previousCandle !== this.studies.candle;
    const state = this.store.getState();
    // Include complete native evidence, not only revision: quality, eligibility,
    // exact provenance and metadata can change independently of depth revision.
    const bookKey = JSON.stringify([cohort, this.books]);
    const booksChanged = bookKey !== this.bookKey;
    let connections: VenueConnection[] | undefined;
    if (booksChanged) {
      this.bookKey = bookKey;
      const books = new Map<string, NormalizedOrderBook>();
      const nextConnections: VenueConnection[] = [];
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
        nextConnections.push({ venue: instrument.venue,
          status: native.eligibility?.eligible && book ? "live" : "stale",
          lastHeartbeat: book?.timestamp ?? 0, reconnectAttempts: 0,
          stateFlag: `${q.source_mode}${q.recorded_source_mode ? `/${q.recorded_source_mode}` : ""} · ${q.lifecycle} · ${q.integrity}`,
        });
      }
      if (cohort.some(instrument => instrument.quote.asset_id !== primary.quote.asset_id)) qualityReasons.add("Indicative cross-quote aggregation; native quotes are not converted");
      if (cohort.length > 1) qualityReasons.add("Display cohort only; costs and paper use native primary");
      if (!books.size) qualityReasons.add("No eligible native depth in selected display cohort");
      this.projectedBooks = books;
      this.aggregate = aggregateBooks(books);
      this.band = this.aggregate ? computeBandObi(books, this.aggregate) : null;
      this.bookReasons = [...qualityReasons];
      if (JSON.stringify(Object.fromEntries(nextConnections.map(connection => [connection.venue, connection]))) !==
          JSON.stringify(state.connections)) connections = nextConnections;
    }
    const qualityReasons = [...this.bookReasons];
    if (history) qualityReasons.push("Subscription history is tape-only, excluded from ordered studies");
    if (unknown) qualityReasons.push("Unknown aggressor side contributes volume, not signed delta");
    const { candle, session, liveBar } = this.studies;
    const profileChanged = candlesChanged || state.valueAreaPct !== this.profileArea;
    this.profileArea = state.valueAreaPct;
    const aggregate = this.aggregate, band = this.band;
    if (plotted.length || candlesChanged || profileChanged || booksChanged) {
      state.applyMarketDataBatch({
        trades: plotted, orderBook: this.projectedBooks.get(primary.instrument_id) ?? null, aggregatedBook: aggregate,
        candle: candlesChanged ? (candle ? { ...candle } : null) : state.currentCandle,
        volumeProfile: profileChanged
          ? (state.candles.length || candle
            ? computeVolumeProfile(candle ? [...state.candles, candle] : state.candles, state.valueAreaPct) : null)
          : state.volumeProfile,
        ticker: studiesChanged ? (session ? { venue: primary.venue, symbol: displaySymbol(primary), price: session.last,
          open: session.open, high: session.high, low: session.low, change: session.last - session.open,
          changePct: session.open ? (session.last / session.open - 1) * 100 : 0, volume: session.volume,
          timestamp: liveBar?.time ?? session.startTime } : null) : state.ticker,
        obi: band && aggregate ? { timestamp: aggregate.timestamp, value: computeOBI(band.bidVolume, band.askVolume), ...band } : null,
        flowBars: completedBars,
        liveBar: studiesChanged ? (liveBar ? { ...liveBar } : null) : state.liveBar,
        session: studiesChanged ? (session ? { ...session } : null) : state.session,
        connections,
      });
    }
    if (this.retainedDirty) {
      this.retainedInstrumentIds = [...new Set(this.retained
        .filter(trade => selected.has(trade.instrument_id) && !trade.trade.is_subscription_history)
        .map(trade => trade.instrument_id))];
      this.retainedDirty = false;
    }
    const displayStatus: DisplayStatus = { reasons: [...this.reasons, ...qualityReasons],
      primaryInstrumentId: primary.instrument_id, instrumentIds: cohort.map(instrument => instrument.instrument_id),
      retainedTradeInstrumentIds: this.retainedInstrumentIds,
      displayTimeMs: this.displayTimeMs,
      gap: this.gap, unknownSideTrades: unknown, historyTrades: history,
      lagObservation: this.scope?.sourceMode === "replay" ? "recorded" : this.scope?.sourceMode === "demo" ? "synthetic" :
        this.scope?.sourceMode === "live" ? "live" : "unknown" };
    if (JSON.stringify(displayStatus) !== JSON.stringify(this.store.getState().displayStatus)) {
      this.store.setState({ displayStatus });
    }
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
