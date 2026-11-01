import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage, request, streamUrl } from './api';
import type { Catalog, Costs, CostInput, Frame, Mode, ReplayAction, VisualGrid, VisualizationInput, VisualizationAck, GroupedLadder, LadderInput, ProposalRisk, ProposalRiskInput } from './api';

export interface ConnectionOptions { mode: Mode; source_id?: string; instrument_id?: string }
interface Presentation { frame: Frame; receivedAt: number; acknowledge: boolean }
interface SessionScope { active: boolean; connected: boolean; sessionId: string | null; sourceId: string | null }
interface Mutation { scope: SessionScope; controller: AbortController; deferred: Presentation | null }

export function useTerminal(initialOptions: ConnectionOptions = { mode: 'demo' }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [presentation, setPresentation] = useState<Presentation | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [connection, setConnection] = useState('Connecting');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const options = useRef<ConnectionOptions>(initialOptions);
  const restoringSelection = useRef(Boolean(initialOptions.instrument_id));
  const [preferenceNotice, setPreferenceNotice] = useState<string | null>(null);
  const queries = useRef(new Map<string, AbortController>());
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
      for (const controller of queries.current.values()) controller.abort();
      queries.current.clear();
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
      const mutation = actionPending.current;
      const prior = mutation?.scope === scope ? mutation.deferred?.frame ?? current.current : current.current;
      if (prior && (BigInt(next.frame.generation) < BigInt(prior.generation) ||
        (next.frame.generation === prior.generation && BigInt(next.frame.frame_id) < BigInt(prior.frame_id)))) return;
      // The stream can publish a committed generation before its HTTP mutation
      // response arrives. Retain one latest frame without invalidating the
      // caller's still-owned request; publish it when that request settles.
      if (mutation?.scope === scope && current.current &&
        BigInt(next.frame.generation) > BigInt(current.current.generation)) {
        mutation.deferred = next;
        return;
      }
      current.current = next.frame;
      options.current.instrument_id = next.frame.selection.instrument_id ?? undefined;
      setPresentation(next);
    };
    const connect = async () => {
      try {
        const nextCatalog = await request<Catalog>('/instruments', undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]));
        if (!scope.active) return;
        setCatalog(nextCatalog);
        const preferred = options.current.source_id;
        if (preferred && !nextCatalog.sources.some(item => item.id === preferred && item.status === 'ready' && item.mode === options.current.mode)) {
          const fallback = nextCatalog.sources.find(item => item.mode === options.current.mode && item.status === 'ready');
          if (!fallback) throw new ApiError(`Saved ${options.current.mode} source is unavailable. Select another source explicitly; no demo substitution was made.`, 409);
          options.current.source_id = fallback.id;
          setPreferenceNotice(`Saved source ${preferred} is unavailable; using ${fallback.label} in the same ${fallback.mode} mode.`);
        }
        if (restoringSelection.current && options.current.instrument_id && options.current.source_id) {
          const sourceCatalog = await request<Catalog>(`/instruments?source_id=${encodeURIComponent(options.current.source_id)}`, undefined, AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]));
          if (!scope.active) return;
          if (!sourceCatalog.instruments.some(item => item.instrument_id === options.current.instrument_id)) {
            setPreferenceNotice(`Saved exact instrument is unavailable in this source; using the source's declared default.`);
            delete options.current.instrument_id;
          }
        }
        restoringSelection.current = false;
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

  const mutate = useCallback(async (path: string, payload: Record<string, unknown>, signal?: AbortSignal): Promise<Frame | null> => {
    const active = current.current;
    const scope = lifecycle.current;
    if (!active || !scope?.active || !scope.connected || actionPending.current) return null;
    const operation: Mutation = { scope, controller: new AbortController(), deferred: null };
    actionPending.current = operation;
    setBusy(true);
    setError(null);
    try {
      const next = await request<Frame>(path, { session_id: active.session_id, generation: active.generation, ...payload }, signal ? AbortSignal.any([signal, operation.controller.signal]) : operation.controller.signal);
      if (actionPending.current !== operation || !scope.active || current.current?.session_id !== active.session_id) return null;
      const prior = current.current;
      if (BigInt(next.generation) < BigInt(prior.generation) ||
        (next.generation === prior.generation && BigInt(next.frame_id) < BigInt(prior.frame_id))) return null;
      current.current = next;
      options.current.instrument_id = next.selection.instrument_id ?? undefined;
      setPresentation({ frame: next, receivedAt: performance.now(), acknowledge: false });
      return next;
    } catch (cause) {
      if (actionPending.current === operation && scope.active && !operation.controller.signal.aborted && !signal?.aborted) setError(errorMessage(cause));
      return null;
    } finally {
      if (actionPending.current === operation) {
        const deferred = operation.deferred;
        const prior = current.current;
        if (deferred && scope.active && scope.connected && prior?.session_id === active.session_id &&
          (BigInt(deferred.frame.generation) > BigInt(prior.generation) ||
            (deferred.frame.generation === prior.generation && BigInt(deferred.frame.frame_id) >= BigInt(prior.frame_id)))) {
          current.current = deferred.frame;
          options.current.instrument_id = deferred.frame.selection.instrument_id ?? undefined;
          setPresentation(deferred);
        }
        actionPending.current = null;
        setBusy(false);
      }
    }
  }, []);

  const select = useCallback(async (instrumentId: string) => { await mutate('/subscriptions', { instrument_id: instrumentId }); }, [mutate]);
  const replay = useCallback(async (action: ReplayAction, payload: Record<string, unknown> = {}) => { await mutate('/replay', { action, ...payload }); }, [mutate]);
  const resumePaper = useCallback(async (runId: string, signal: AbortSignal): Promise<Frame> => {
    const next = await mutate('/paper/resume', { run_id: runId }, signal);
    if (!next) throw new ApiError('Paper resume failed, was interrupted, or was superseded. Inspect terminal errors and native run state before retrying.', 409);
    return next;
  }, [mutate]);
  const source = useCallback((mode: Mode, sourceId: string) => {
    restoringSelection.current = false;
    setPreferenceNotice(null);
    // Recorded catalogs differ. The backend reflects a new source's exact default;
    // live selection is explicit and never silently mapped across native identities.
    options.current = { mode, source_id: sourceId, ...(mode === 'live' ? { instrument_id: options.current.instrument_id } : {}) };
    retries.current = 0;
    setRevision(value => value + 1);
  }, []);
  const query = useCallback(async <T extends object>(path: string, input: object, signal: AbortSignal, onReceive?: (receivedAtMs: number) => void): Promise<T> => {
    const active = current.current;
    if (!active || !lifecycle.current?.connected || active.status === 'seeking') throw new ApiError('No current presentation is available for this query.', 409);
    queries.current.get(path)?.abort();
    const controller = new AbortController();
    queries.current.set(path, controller);
    try {
      const result = await request<T>(path, { session_id: active.session_id, generation: active.generation, ...input }, AbortSignal.any([signal, controller.signal, AbortSignal.timeout(10000)]), undefined, onReceive);
      if (controller.signal.aborted || !lifecycle.current?.connected || current.current?.session_id !== active.session_id || current.current.generation !== active.generation || ('generation' in result && result.generation !== active.generation)) {
        throw new ApiError('Response belongs to obsolete source, selection or replay evidence. Request again.', 409);
      }
      return result;
    } finally { if (queries.current.get(path) === controller) queries.current.delete(path); }
  }, []);
  const costs = useCallback((input: CostInput, signal: AbortSignal) => query<Costs>('/costs', input, signal), [query]);
  const visualization = useCallback((input: VisualizationInput, signal: AbortSignal, onReceive?: (receivedAtMs: number) => void) => query<VisualGrid>('/visualization', input, signal, onReceive), [query]);
  const acknowledgeVisualization = useCallback((view_id: string, browser_receive_to_paint_ms: number, signal: AbortSignal) => query<VisualizationAck>('/visualization/ack', { view_id, browser_receive_to_paint_ms }, signal), [query]);
  const ladder = useCallback((input: LadderInput, signal: AbortSignal) => query<GroupedLadder>('/ladder', input, signal), [query]);
  const proposalRisk = useCallback((input: ProposalRiskInput, signal: AbortSignal) => query<ProposalRisk>('/proposal-risk', input, signal), [query]);

  return { catalog, catalogError, preferenceNotice, frame: presentation?.frame ?? null, connection, error, busy, reconnect, select, replay, resumePaper, workflow: query, source, costs, visualization, acknowledgeVisualization, ladder, proposalRisk };
}
