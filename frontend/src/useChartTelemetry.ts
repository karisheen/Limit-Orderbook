import { useCallback, useEffect, useRef, useState } from 'react';
import type { Frame, VisualGrid, VisualizationAck } from './api';
import { errorMessage } from './api';

export function useChartTelemetry(frame: Frame | null, view: VisualGrid | null, acknowledge: (viewId: string, duration: number, signal: AbortSignal) => Promise<VisualizationAck>) {
  const identity = frame ? `${frame.session_id}:${frame.generation}` : '';
  const current = useRef({ identity, view });
  current.current = { identity, view };
  const sent = useRef(new Set<string>());
  const pending = useRef<AbortController | null>(null);
  const [measurement, setMeasurement] = useState<{ view_id: string; draw_ms: number; receive_ms: number; upper_bound_ms: number | null; error: string | null } | null>(null);
  useEffect(() => {
    sent.current.clear(); pending.current?.abort(); setMeasurement(null);
    return () => pending.current?.abort();
  }, [identity]);
  const onPaint = useCallback((paint: { view_id: string; receive_to_paint_ms: number; draw_ms: number }) => {
    const scope = current.current;
    if (!scope.view || scope.view.view_id !== paint.view_id || document.visibilityState !== 'visible' || sent.current.has(paint.view_id)) return;
    if (!Number.isFinite(paint.receive_to_paint_ms) || paint.receive_to_paint_ms < 0 || paint.receive_to_paint_ms > 60000) return;
    sent.current.add(paint.view_id);
    if (sent.current.size > 64) sent.current.delete(sent.current.values().next().value!);
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const value = { view_id: paint.view_id, draw_ms: paint.draw_ms, receive_ms: paint.receive_to_paint_ms, upper_bound_ms: null, error: null };
    setMeasurement(value);
    void acknowledge(paint.view_id, paint.receive_to_paint_ms, controller.signal).then(result => {
      if (!controller.signal.aborted && current.current.identity === scope.identity) setMeasurement({ ...value, upper_bound_ms: result.publish_to_ack_upper_bound_ms });
    }).catch(cause => {
      if (!controller.signal.aborted && current.current.identity === scope.identity) setMeasurement({ ...value, error: errorMessage(cause) });
    });
  }, [acknowledge]);
  return { measurement, onPaint };
}
