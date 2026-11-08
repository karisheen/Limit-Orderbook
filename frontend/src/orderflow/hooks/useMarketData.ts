// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: native cursor display bridge; no exchange transport.
import { useLayoutEffect } from "react";
import { useNativeTerminal } from "../../integration/NativeContext";
import type { NativeMarketData } from "../../api";
import { useMarketSession, useSessionStore } from "@/lib/session/SessionContext";
import { drainNativeMarketData } from "@/lib/session/NativeMarketBridge";

export function useMarketData() {
  const { terminal, instrument, usable } = useNativeTerminal();
  const session = useMarketSession();
  const { frame, workflow, busy } = terminal;
  const sessionId = frame?.session_id;
  const generation = frame?.generation;
  const sourceId = frame?.source.id;
  const sourceMode = frame?.source.mode;
  const venue = useSessionStore(s => s.venue);
  const selectedVenues = useSessionStore(s => s.selectedVenues);
  const timeframe = useSessionStore(s => s.timeframe);
  const tickSize = useSessionStore(s => s.tickSize);
  const imbalanceThreshold = useSessionStore(s => s.imbalanceThreshold);
  const valueAreaPct = useSessionStore(s => s.valueAreaPct);

  // Busy/unusable pauses polling, not evidence. Only the native scope fences caches.
  useLayoutEffect(() => {
    session.bridge.reset(instrument && sessionId && generation && sourceId
      ? { sessionId, generation, sourceId, primary: instrument, sourceMode } : null);
  }, [session, sessionId, generation, sourceId, sourceMode, instrument]);

  useLayoutEffect(() => () => session.bridge.reset(null), [session]);

  useLayoutEffect(() => {
    session.bridge.configure();
  }, [session, venue, selectedVenues, timeframe, tickSize, imbalanceThreshold, valueAreaPct,
    sessionId, generation, sourceId, instrument]);

  useLayoutEffect(() => {
    if (!usable || busy || !instrument || !sessionId || !generation || !sourceId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        await drainNativeMarketData(session.bridge, cursor => workflow<NativeMarketData>("/market-data", {
          after_delivery: cursor.delivery, after_event: cursor.event, limit: 2000,
        }, controller.signal, undefined, controller.signal), controller.signal);
      } catch (error) {
        if (!controller.signal.aborted) session.bridge.fail(error instanceof Error ? error.message : String(error));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 150);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [session, workflow, sessionId, generation, sourceId, instrument, usable, busy]);
}
