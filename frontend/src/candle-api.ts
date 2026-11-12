import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import type { Frame } from './api';
import { candleIdentity, candleInstrumentIds, candleSourceBlock, matchesCandleHistory, validateCandles } from './candle-model';
import type { CandleHistory, CandleSource } from './candle-model';

interface CandleQueryState {
  identity: string; data: CandleHistory | null; loading: boolean;
  error: string | null; receivedAt: number | null;
}
export function useCandleHistory(frame: Frame | null, interval: string, active: boolean, connected: boolean,
  instrumentIds: readonly string[], cohortReady: boolean, source: CandleSource) {
  const identity = candleIdentity(frame, interval, instrumentIds, source, cohortReady);
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const session = frame?.session_id ?? '';
  const generation = frame?.generation ?? '';
  const instrument = frame?.selection.instrument_id ?? '';
  const cohortKey = JSON.stringify(candleInstrumentIds(instrument, instrumentIds));
  const blockedReason = candleSourceBlock(source, instrument, instrumentIds, cohortReady);
  const usable = !blockedReason && connected && !!session && !!instrument
    && !['loading', 'seeking', 'unavailable', 'error'].includes(frame?.status ?? 'loading');
  const sourceFence = useRef<{ identity: string; value: string } | null>(null);
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [state, setState] = useState<CandleQueryState>({ identity: '', data: null, loading: false, error: null, receivedAt: null });
  const enabled = active && visible && usable;
  const currentEnabled = useRef(enabled);
  currentEnabled.current = enabled;
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  useEffect(() => {
    setState({ identity, data: null, loading: false, error: null, receivedAt: null });
  }, [identity]);
  useEffect(() => {
    if (!active || !visible) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, visible]);
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: number | undefined;
    const controller = new AbortController();
    const current = () => !disposed && !controller.signal.aborted && currentEnabled.current && currentIdentity.current === identity;
    const refresh = async () => {
      setState(previous => previous.identity === identity
        ? { ...previous, loading: true }
        : { identity, data: null, loading: true, error: null, receivedAt: null });
      try {
        const expected = { session_id: session, generation, instrument_id: instrument, interval, source,
          instrument_ids: JSON.parse(cohortKey) as string[] };
        const data = await request<CandleHistory>('/candles', { session_id: session, generation, interval, source,
          instrument_ids: expected.instrument_ids, limit: 300 }, controller.signal);
        if (!current()) return;
        const pinnedSource = sourceFence.current?.identity === identity ? sourceFence.current.value : null;
        if (!matchesCandleHistory(data, expected, pinnedSource)) {
          // Another source, cohort, market or generation answered: the held series is no longer verifiable.
          setState({ identity, data: null, receivedAt: null, loading: false, error: 'Candle response belongs to a different source, cohort, market or generation' });
          return;
        }
        validateCandles(data);
        sourceFence.current = { identity, value: data.source_identity };
        setState(previous => data.status === 'unavailable' && data.supported_intervals.includes(interval)
          && previous.identity === identity && previous.data?.status === 'ready'
          ? { ...previous, loading: false, error: data.reasons.join(' · ') || 'Source candle history temporarily unavailable' }
          : { identity, data, loading: false, error: null, receivedAt: Date.now() });
      } catch (error) {
        if (!current()) return;
        // A failed refresh keeps same-owner candles; the chart labels them stale until one succeeds.
        setState(previous => ({ identity, data: previous.identity === identity ? previous.data : null,
          receivedAt: previous.identity === identity ? previous.receivedAt : null, loading: false, error: errorMessage(error) }));
      } finally {
        if (current()) timer = window.setTimeout(() => { void refresh(); }, 5000);
      }
    };
    void refresh();
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [enabled, identity, session, generation, instrument, interval, source, cohortKey, attempt]);
  const matched = state.identity === identity;
  const ageSeconds = matched && state.receivedAt !== null ? Math.max(0, Math.floor((now - state.receivedAt) / 1000)) : null;
  return {
    identity, data: matched ? state.data : null,
    loading: enabled && (!matched || state.loading), error: matched ? state.error : null,
    receivedAt: matched ? state.receivedAt : null,
    stale: matched && !!state.data && (!usable || !!state.error || state.data.status === 'ready' && ageSeconds !== null && ageSeconds > 15),
    ageSeconds, paused: !active || !visible, usable, blockedReason, retry,
  };
}
