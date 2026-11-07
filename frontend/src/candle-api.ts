import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import type { Frame } from './api';
import { candleIdentity, matchesCandleHistory, validateCandles } from './candle-model';
import type { CandleHistory } from './candle-model';

interface CandleQueryState {
  identity: string; data: CandleHistory | null; loading: boolean;
  error: string | null; receivedAt: number | null;
}
export function useCandleHistory(frame: Frame | null, interval: string, active: boolean, connected: boolean) {
  const identity = candleIdentity(frame, interval);
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const session = frame?.session_id ?? '';
  const generation = frame?.generation ?? '';
  const instrument = frame?.selection.instrument_id ?? '';
  const usable = connected && !!session && !!instrument && !['loading', 'seeking', 'unavailable', 'error'].includes(frame?.status ?? 'loading');
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
        const data = await request<CandleHistory>('/candles', { session_id: session, generation, interval, limit: 300 }, controller.signal);
        if (!current()) return;
        if (!matchesCandleHistory(data, session, generation, instrument, interval)) throw new Error('Candle response belongs to a different market or generation');
        validateCandles(data);
        setState(previous => {
          if (data.status === 'unavailable' && data.supported_intervals.includes(interval)
            && previous.identity === identity && previous.data?.status === 'ready') {
            return { ...previous, loading: false, error: data.reasons.join(' · ') || 'Source candle history temporarily unavailable' };
          }
          return { identity, data, loading: false, error: null, receivedAt: Date.now() };
        });
      } catch (error) {
        if (!current()) return;
        setState(previous => ({ identity, data: previous.identity === identity ? previous.data : null,
          receivedAt: previous.identity === identity ? previous.receivedAt : null, loading: false, error: errorMessage(error) }));
      } finally {
        if (current()) timer = window.setTimeout(() => { void refresh(); }, 5000);
      }
    };
    void refresh();
    return () => { disposed = true; controller.abort(); window.clearTimeout(timer); };
  }, [enabled, identity, session, generation, instrument, interval, attempt]);
  const matched = state.identity === identity;
  const ageSeconds = matched && state.receivedAt !== null ? Math.max(0, Math.floor((now - state.receivedAt) / 1000)) : null;
  return {
    identity, data: matched ? state.data : null,
    loading: enabled && (!matched || state.loading), error: matched ? state.error : null,
    receivedAt: matched ? state.receivedAt : null,
    stale: matched && !!state.data && (!usable || !!state.error || state.data.status === 'ready' && ageSeconds !== null && ageSeconds > 15),
    ageSeconds, paused: !active || !visible, usable, retry,
  };
}
