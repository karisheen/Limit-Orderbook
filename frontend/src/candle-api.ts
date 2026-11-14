import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { errorMessage, request } from './api';
import type { Frame } from './api';
import { candleIdentity, candleInstrumentIds, candleSourceBlock, matchesCandleHistory,
  MAX_RETAINED_CANDLES, mergeCandleHistory, normalizeCandlePage } from './candle-model';
import type { CandleHistory, CandleRequestIdentity, CandleSource } from './candle-model';

export interface CandleQueryState {
  data: CandleHistory | null; loading: boolean; error: string | null; receivedAt: number | null;
  olderLoading: boolean; olderError: string | null; hasMore: boolean; historyLimitReached: boolean;
}
export interface CandlePageRequest extends Omit<CandleRequestIdentity, 'instrument_id'> { limit: number; before_ns?: string }
export type CandlePageTransport = (body: CandlePageRequest, signal: AbortSignal) => Promise<CandleHistory>;
const initialState: CandleQueryState = { data: null, loading: false, error: null, receivedAt: null,
  olderLoading: false, olderError: null, hasMore: false, historyLimitReached: false };
const MAX_RECOVERY_PAGES = 32;
class CandleIdentityError extends Error {}

/** One mounted, exact query. The hook and deterministic consumers share this lifecycle. */
export class CandleHistoryQuery {
  private state: CandleQueryState = initialState;
  private listeners = new Set<() => void>();
  private controllers = new Set<AbortController>();
  private epoch = 0;
  private newest: Promise<void> | null = null;
  private oldest: Promise<void> | null = null;
  private sourceIdentity: string | null = null;
  private seriesKey: string | null = null;
  private gapTarget: bigint | null = null;

  constructor(private expected: CandleRequestIdentity, private transport: CandlePageTransport,
    private clock: () => number = Date.now) {}

  getSnapshot = (): CandleQueryState => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private publish(change: Partial<CandleQueryState>) {
    this.state = { ...this.state, ...change };
    const metadata = this.state.data?.history;
    this.state.hasMore = this.expected.source === 'venue_ohlcv' && !this.state.historyLimitReached
      && metadata?.state === 'more' && metadata.pagination === 'time' && metadata.next_before_ns !== null;
    for (const listener of this.listeners) listener();
  }
  cancel = () => {
    this.epoch++;
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
    this.newest = null;
    this.oldest = null;
    this.publish({ loading: false, olderLoading: false });
  };
  reset = () => {
    this.cancel();
    this.sourceIdentity = null;
    this.seriesKey = null;
    this.gapTarget = null;
    this.publish(initialState);
  };
  private discard(error: Error) {
    this.reset();
    this.publish({ error: error.message });
  }
  private async page(controller: AbortController, epoch: number, before?: string, limit = 300) {
    const { session_id, generation, interval, source, instrument_ids } = this.expected;
    const response = await this.transport({ session_id, generation, interval, source, instrument_ids,
      limit, ...(before ? { before_ns: before } : {}) }, controller.signal);
    if (epoch !== this.epoch || controller.signal.aborted) return null;
    if (!matchesCandleHistory(response, this.expected, this.sourceIdentity)
      || this.expected.source === 'venue_ohlcv' && this.seriesKey !== null && response.series_key !== this.seriesKey
      || this.state.data && (response.time_basis !== this.state.data.time_basis || response.volume_unit !== this.state.data.volume_unit)) {
      throw new CandleIdentityError('Candle response belongs to a different source, series, cohort, market or generation');
    }
    const data = normalizeCandlePage(response);
    if (data.candles.length > limit) throw new Error('Candle page exceeds requested bar limit');
    if (data.source === 'venue_ohlcv' && data.history?.requested_before_ns !== (before ?? null)) {
      throw new Error('Candle page belongs to a different history boundary');
    }
    this.sourceIdentity = data.source_identity;
    this.seriesKey = data.series_key ?? null;
    // Initial observed snapshots expose source identity even without receive-time evidence after a seek.
    // Once this exact owner has ready data, temporary unavailability must leave it visibly stale.
    if (data.status === 'unavailable' && data.supported_intervals.includes(this.expected.interval)
      && (data.source === 'venue_ohlcv' || this.state.data?.status === 'ready')) {
      throw new Error(data.reasons.join(' · ') || 'Source candle history temporarily unavailable');
    }
    return data;
  }
  refresh = (): Promise<void> => {
    if (this.newest) return this.newest;
    const epoch = this.epoch;
    const controller = new AbortController();
    this.controllers.add(controller);
    this.publish({ loading: true });
    this.newest = this.refreshPages(controller, epoch).finally(() => {
      this.controllers.delete(controller);
      if (epoch === this.epoch) {
        this.newest = null;
        this.publish({ loading: false });
      }
    });
    return this.newest;
  };
  private async refreshPages(controller: AbortController, epoch: number) {
    try {
      let page = await this.page(controller, epoch);
      if (!page) return;
      if (page.status === 'unavailable') {
        this.gapTarget = null;
        this.publish({ data: page, error: null, receivedAt: this.clock(), historyLimitReached: false });
        return;
      }
      const held = this.state.data;
      const target = this.gapTarget ?? (held?.candles.length ? BigInt(held.candles[held.candles.length - 1]!.start_ns) : null);
      if (page.source === 'venue_ohlcv' && target !== null && page.candles.length
        && BigInt(page.candles[0]!.start_ns) > target && !this.state.historyLimitReached) this.gapTarget = target;
      const merged = mergeCandleHistory(held, page, false, this.state.historyLimitReached);
      this.publish({ ...merged, error: this.gapTarget === null ? null : 'Incomplete history: recovering missed polling overlap',
        receivedAt: this.clock() });
      if (page.source !== 'venue_ohlcv' || this.state.historyLimitReached) return;
      let pages = 0;
      while (this.gapTarget !== null) {
        if (page.candles.length && BigInt(page.candles[0]!.start_ns) <= this.gapTarget) {
          this.gapTarget = null;
          this.publish({ error: null });
          break;
        }
        const metadata = page.history;
        if (++pages > MAX_RECOVERY_PAGES || metadata?.state !== 'more' || metadata.pagination !== 'time'
          || metadata.next_before_ns === null) throw new Error('Provider cannot recover the missing polling overlap within the bounded history window');
        page = await this.page(controller, epoch, metadata.next_before_ns);
        if (!page) return;
        const current = this.state.data;
        const recovered = mergeCandleHistory(current, page, true, this.state.historyLimitReached);
        // Recovery fills an internal gap; only leftward user loading advances the left cursor.
        recovered.data = { ...recovered.data, history: current?.history };
        this.publish(recovered);
        if (recovered.historyLimitReached) throw new Error('Retained history limit reached before missing polling overlap was recovered');
      }
    } catch (error) {
      if (epoch !== this.epoch || controller.signal.aborted) return;
      if (error instanceof CandleIdentityError) this.discard(error);
      else this.publish({ error: `${this.gapTarget === null ? '' : 'Incomplete history: '}${errorMessage(error)}` });
    }
  }
  loadOlder = (): Promise<void> => {
    if (this.oldest) return this.oldest;
    const before = this.state.data?.history?.next_before_ns;
    if (!this.state.hasMore || !before) return Promise.resolve();
    const epoch = this.epoch;
    const controller = new AbortController();
    this.controllers.add(controller);
    this.publish({ olderLoading: true, olderError: null });
    this.oldest = this.olderPage(controller, epoch, before).finally(() => {
      this.controllers.delete(controller);
      if (epoch === this.epoch) {
        this.oldest = null;
        this.publish({ olderLoading: false });
      }
    });
    return this.oldest;
  };
  private async olderPage(controller: AbortController, epoch: number, before: string) {
    try {
      const limit = Math.min(300, MAX_RETAINED_CANDLES - (this.state.data?.candles.length ?? 0));
      const page = await this.page(controller, epoch, before, limit);
      if (!page) return;
      if (page.history?.state === 'unavailable') throw new Error(page.reasons.join(' · ') || 'Older candle history temporarily unavailable');
      this.publish({ ...mergeCandleHistory(this.state.data, page, true, this.state.historyLimitReached), olderError: null });
    } catch (error) {
      if (epoch !== this.epoch || controller.signal.aborted) return;
      if (error instanceof CandleIdentityError) this.discard(error);
      else this.publish({ olderError: errorMessage(error) });
    }
  }
}

const fetchCandlePage: CandlePageTransport = (body, signal) => request<CandleHistory>('/candles', body, signal);

/** Venue HTTP history does not depend on book reconstruction or analytical health. */
export function candleQueryUsable(frame: Pick<Frame, 'session_id' | 'generation' | 'selection' | 'status'> | null,
  connected: boolean, source: CandleSource): boolean {
  if (!connected || !frame?.session_id || !frame.generation || !frame.selection.instrument_id
    || frame.status === 'loading' || frame.status === 'seeking') return false;
  return source === 'venue_ohlcv' || frame.status !== 'unavailable' && frame.status !== 'error';
}

export function useCandleHistory(frame: Frame | null, interval: string, active: boolean, connected: boolean,
  instrumentIds: readonly string[], cohortReady: boolean, source: CandleSource) {
  const identity = candleIdentity(frame, interval, instrumentIds, source, cohortReady);
  const session = frame?.session_id ?? '';
  const generation = frame?.generation ?? '';
  const instrument = frame?.selection.instrument_id ?? '';
  const cohortKey = JSON.stringify(source === 'venue_ohlcv' ? [instrument] : candleInstrumentIds(instrument, instrumentIds));
  const blockedReason = candleSourceBlock(source, instrument, instrumentIds, cohortReady);
  const usable = !blockedReason && candleQueryUsable(frame, connected, source);
  const query = useMemo(() => {
    // The full identity also fences source mode/readiness changes absent from the request body.
    void identity;
    return new CandleHistoryQuery({ session_id: session, generation, instrument_id: instrument, interval, source,
      instrument_ids: JSON.parse(cohortKey) as string[] }, fetchCandlePage);
  }, [identity, session, generation, instrument, interval, source, cohortKey]);
  const state = useSyncExternalStore(query.subscribe, query.getSnapshot);
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const enabled = active && visible && usable;
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  const resetHistory = useCallback(() => { query.reset(); setAttempt(value => value + 1); }, [query]);
  const loadOlder = useCallback(() => { if (enabled) void query.loadOlder(); }, [query, enabled]);
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  useEffect(() => {
    if (!active || !visible) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, visible]);
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: number | undefined;
    const refresh = async () => {
      await query.refresh();
      if (!disposed) timer = window.setTimeout(() => { void refresh(); },
        Math.max(5000, query.getSnapshot().data?.history?.poll_interval_ms ?? 5000));
    };
    void refresh();
    return () => { disposed = true; query.cancel(); window.clearTimeout(timer); };
  }, [enabled, query, attempt]);
  const ageSeconds = state.receivedAt !== null ? Math.max(0, Math.floor((now - state.receivedAt) / 1000)) : null;
  return { ...state, identity, loading: enabled && state.loading,
    stale: !!state.data && (!usable || !!state.error || state.historyLimitReached
      || state.data.status === 'ready' && ageSeconds !== null && ageSeconds > 15),
    ageSeconds, paused: !active || !visible, usable, blockedReason, retry, loadOlder, resetHistory };
}
