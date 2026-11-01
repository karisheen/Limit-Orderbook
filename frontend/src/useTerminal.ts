import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage, request, streamUrl } from './api';
import type { Catalog, Costs, CostInput, Frame, Mode, ReplayAction } from './api';

interface ConnectionOptions { mode: Mode; source_id?: string; instrument_id?: string }
interface Presentation { frame: Frame; receivedAt: number; acknowledge: boolean }
interface SessionScope { active: boolean; connected: boolean; sessionId: string | null; sourceId: string | null }
interface Mutation { scope: SessionScope; controller: AbortController }

export function useTerminal() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [presentation, setPresentation] = useState<Presentation | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [connection, setConnection] = useState('Connecting');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const options = useRef<ConnectionOptions>({ mode: 'demo' });
  const current = useRef<Frame | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const lifecycle = useRef<SessionScope | null>(null);
  const actionPending = useRef<Mutation | null>(null);
  const retries = useRef(0);
  const reconnect = useCallback(() => { retries.current = 0; setRevision(value => value + 1); }, []);

  useEffect(() => {
    const scope: SessionScope = { active: true, connected: false, sessionId: null, sourceId: null };
    lifecycle.current = scope;
    let ws: WebSocket | null = null;
    let retryTimer: number | undefined;
    let watchdogTimer: number | undefined;
    let catalogTimer: number | undefined;
    let reconnecting = false;
    let lastReceivedAt = 0;
    let renderRequest = 0;
    let pending: Presentation | null = null;
    const abort = new AbortController();
    current.current = null;
    setPresentation(null);
    setBusy(false);
    setConnection('Connecting');
    setError(null);
    setCatalogError(null);

    const closeSession = (id: string) => {
      void fetch(`/api/v1/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', keepalive: true }).catch(() => undefined);
    };
    const invalidate = () => {
      scope.connected = false;
      current.current = null;
      pending = null;
      cancelAnimationFrame(renderRequest);
      renderRequest = 0;
      const mutation = actionPending.current;
      if (mutation?.scope === scope) {
        actionPending.current = null;
        mutation.controller.abort();
        setBusy(false);
      }
    };
    const retry = (reason: string) => {
      if (!scope.active || reconnecting) return;
      reconnecting = true;
      invalidate();
      clearTimeout(watchdogTimer);
      clearTimeout(catalogTimer);
      setConnection('Disconnected · reconnecting');
      setError(`${reason} Evidence is unavailable. Reconnecting creates a new independent session and resets replay; selection is retained.`);
      ws?.close(4000, 'Session recovery');
      if (scope.sessionId) closeSession(scope.sessionId);
      retryTimer = window.setTimeout(() => setRevision(value => value + 1), Math.min(1000 * 2 ** Math.min(retries.current++, 4), 10_000));
    };
    const armWatchdog = () => {
      clearTimeout(watchdogTimer);
      watchdogTimer = window.setTimeout(() => retry('No stream frame received for 5 seconds.'), 5000);
    };
    const refreshCatalog = async () => {
      try {
        const next = await request<Catalog>(`/instruments?source_id=${encodeURIComponent(scope.sourceId ?? '')}`, undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]));
        if (!scope.active || reconnecting) return;
        setCatalog(next);
        setCatalogError(null);
      } catch (cause) {
        if (scope.active && !reconnecting) {
          setCatalog(previous => previous ? { ...previous, instruments: [] } : previous);
          setCatalogError(`Source catalog refresh failed: ${errorMessage(cause)}`);
        }
      } finally {
        if (scope.active && !reconnecting) catalogTimer = window.setTimeout(() => { void refreshCatalog(); }, 3000);
      }
    };
    const accept = (next: Presentation) => {
      if (!scope.active || reconnecting || next.frame.session_id !== scope.sessionId) return;
      const prior = current.current;
      if (prior && (BigInt(next.frame.generation) < BigInt(prior.generation) ||
        (next.frame.generation === prior.generation && BigInt(next.frame.frame_id) < BigInt(prior.frame_id)))) return;
      current.current = next.frame;
      options.current.instrument_id = next.frame.selection.instrument_id ?? undefined;
      setPresentation(next);
    };
    const connect = async () => {
      try {
        const nextCatalog = await request<Catalog>('/instruments', undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]));
        if (!scope.active) return;
        setCatalog(nextCatalog);
        // Keep creation observable: a late response still needs its server session released.
        const first = await request<Frame>('/sessions', options.current);
        scope.sessionId = first.session_id;
        if (!scope.active || reconnecting) { closeSession(first.session_id); return; }
        scope.sourceId = first.source.id;
        options.current.source_id = first.source.id;
        options.current.mode = first.source.mode;
        await refreshCatalog();
        if (!scope.active || reconnecting) { closeSession(first.session_id); return; }
        accept({ frame: first, receivedAt: performance.now(), acknowledge: false });
        ws = new WebSocket(streamUrl(first.session_id));
        socket.current = ws;
        armWatchdog();
        ws.onopen = () => {
          if (!scope.active || reconnecting) return;
          setConnection('Awaiting stream evidence');
          armWatchdog();
        };
        ws.onmessage = event => {
          const receivedAt = performance.now();
          if (!scope.active || reconnecting) return;
          try {
            if (typeof event.data !== 'string' || event.data.length > 2_000_000) throw new Error('Invalid or oversized presentation frame');
            const next = JSON.parse(event.data) as Frame;
            if (next.version !== '1' || next.type !== 'frame' || next.session_id !== scope.sessionId ||
              !/^\d+$/.test(next.generation) || !/^\d+$/.test(next.frame_id)) throw new Error('Unsupported presentation frame');
            lastReceivedAt = receivedAt;
            armWatchdog();
            if (!scope.connected) {
              scope.connected = true;
              retries.current = 0;
              setConnection('Connected');
              setError(null);
            }
            pending = { frame: next, receivedAt, acknowledge: true };
            if (!renderRequest) renderRequest = requestAnimationFrame(() => {
              renderRequest = 0;
              if (pending) accept(pending);
              pending = null;
            });
          } catch (cause) { retry(errorMessage(cause)); }
        };
        ws.onerror = () => retry('Stream transport failed.');
        ws.onclose = () => retry('Stream connection lost.');
      } catch (cause) {
        if (scope.active) retry(errorMessage(cause));
      }
    };
    void connect();
    const pagehide = () => {
      scope.active = false;
      invalidate();
      clearTimeout(retryTimer);
      clearTimeout(watchdogTimer);
      clearTimeout(catalogTimer);
      ws?.close(1000, 'Document hidden');
      if (scope.sessionId) closeSession(scope.sessionId);
    };
    const pageshow = (event: PageTransitionEvent) => { if (event.persisted) setRevision(value => value + 1); };
    const visibility = () => {
      if (document.visibilityState === 'visible' && scope.connected && performance.now() - lastReceivedAt >= 5000) retry('Stream evidence expired while this document was hidden.');
    };
    window.addEventListener('pagehide', pagehide);
    window.addEventListener('pageshow', pageshow);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      scope.active = false;
      invalidate();
      abort.abort();
      clearTimeout(retryTimer);
      clearTimeout(watchdogTimer);
      clearTimeout(catalogTimer);
      window.removeEventListener('pagehide', pagehide);
      window.removeEventListener('pageshow', pageshow);
      document.removeEventListener('visibilitychange', visibility);
      ws?.close(1000, 'Session replaced');
      if (socket.current === ws) socket.current = null;
      if (lifecycle.current === scope) lifecycle.current = null;
      if (scope.sessionId) closeSession(scope.sessionId);
    };
  }, [revision]);

  useEffect(() => {
    if (!presentation?.acknowledge || document.visibilityState !== 'visible') return;
    let second = 0;
    // Two frame callbacks give the committed DOM a real paint opportunity. This is not GPU completion.
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        const ws = socket.current;
        if (ws?.readyState === WebSocket.OPEN && current.current?.session_id === presentation.frame.session_id) {
          ws.send(JSON.stringify({ type: 'ack', frame_id: presentation.frame.frame_id,
            browser_receive_to_paint_ms: performance.now() - presentation.receivedAt }));
        }
      });
    });
    return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); };
  }, [presentation]);

  const mutate = useCallback(async (path: string, payload: Record<string, unknown>) => {
    const active = current.current;
    const scope = lifecycle.current;
    if (!active || !scope?.active || !scope.connected || actionPending.current) return;
    const operation: Mutation = { scope, controller: new AbortController() };
    actionPending.current = operation;
    setBusy(true);
    setError(null);
    try {
      const next = await request<Frame>(path, { session_id: active.session_id, generation: active.generation, ...payload }, operation.controller.signal);
      if (actionPending.current !== operation || !scope.active || current.current?.session_id !== active.session_id) return;
      const prior = current.current;
      if (BigInt(next.generation) < BigInt(prior.generation) ||
        (next.generation === prior.generation && BigInt(next.frame_id) < BigInt(prior.frame_id))) return;
      current.current = next;
      options.current.instrument_id = next.selection.instrument_id ?? undefined;
      setPresentation({ frame: next, receivedAt: performance.now(), acknowledge: false });
    } catch (cause) {
      if (actionPending.current === operation && scope.active && !operation.controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (actionPending.current === operation) {
        actionPending.current = null;
        setBusy(false);
      }
    }
  }, []);

  const select = useCallback((instrumentId: string) => mutate('/subscriptions', { instrument_id: instrumentId }), [mutate]);
  const replay = useCallback((action: ReplayAction, payload: Record<string, unknown> = {}) => mutate('/replay', { action, ...payload }), [mutate]);
  const source = useCallback((mode: Mode, sourceId: string) => {
    // Recorded catalogs differ. The backend reflects a new source's exact default;
    // live selection is explicit and never silently mapped across native identities.
    options.current = { mode, source_id: sourceId, ...(mode === 'live' ? { instrument_id: options.current.instrument_id } : {}) };
    retries.current = 0;
    setRevision(value => value + 1);
  }, []);
  const costs = useCallback(async (input: CostInput, signal: AbortSignal): Promise<Costs> => {
    const active = current.current;
    if (!active || !lifecycle.current?.connected || active.status === 'seeking') throw new ApiError('No current presentation is available for costs.', 409);
    const result = await request<Costs>('/costs', { session_id: active.session_id, generation: active.generation, ...input }, signal);
    if (current.current?.session_id !== active.session_id || current.current.generation !== result.generation) {
      throw new ApiError('Cost response belongs to a replaced selection or replay cursor. Recalculate.', 409);
    }
    return result;
  }, []);

  return { catalog, catalogError, frame: presentation?.frame ?? null, connection, error, busy, reconnect, select, replay, source, costs };
}
