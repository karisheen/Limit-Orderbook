import { useEffect, useState } from 'react';
import { errorMessage, request } from './api';
import type { Frame } from './api';
import { validatePositioning } from './positioning-model';
import type { PositioningHistory } from './positioning-model';

interface PositioningState { identity: string; data: PositioningHistory | null; error: string | null }

/**
 * Polls observed open interest and funding on the candle cadence. `enabled` carries the price
 * chart's own visibility, connection and cohort gates, so a hidden or paused chart never polls.
 */
export function usePositioningHistory(frame: Frame | null, interval: string, enabled: boolean, sourceIdentity: string | null) {
  const session = frame?.session_id ?? '';
  const generation = frame?.generation ?? '';
  const instrument = frame?.selection.instrument_id ?? '';
  const identity = enabled && session && instrument && sourceIdentity ? JSON.stringify([session, generation, sourceIdentity, instrument, interval]) : '';
  const [state, setState] = useState<PositioningState>({ identity: '', data: null, error: null });
  useEffect(() => {
    if (!identity || !sourceIdentity) return;
    let timer: number | undefined;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const data = await request<PositioningHistory>('/positioning', { session_id: session, generation, interval, limit: 300 }, controller.signal);
        if (controller.signal.aborted) return;
        validatePositioning(data, { session_id: session, generation, source_identity: sourceIdentity, instrument_id: instrument, interval });
        setState({ identity, data, error: null });
      } catch (error) {
        if (controller.signal.aborted) return;
        // A failed or foreign response must not leave an unverifiable series beside current candles.
        setState({ identity, data: null, error: errorMessage(error) });
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(() => { void refresh(); }, 5000);
      }
    };
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [identity, session, generation, sourceIdentity, instrument, interval]);
  return state.identity === identity ? state : { identity, data: null, error: null };
}
