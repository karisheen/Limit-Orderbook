// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useEffect, useState } from "react";
import { useSessionStore } from "@/lib/session/SessionContext";
import { formatDuration } from "@/lib/format";
import { venueLabel } from "../../format";

// ─── Status bar ──────────────────────────────────────────────────
// Feed health per venue, data lag, session age and a UTC clock.

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export default function StatusBar() {
  const connections = useSessionStore((s) => s.connections);
  const selectedVenues = useSessionStore((s) => s.selectedVenues);
  const displayStatus = useSessionStore((s) => s.displayStatus);
  const session = useSessionStore((s) => s.session);
  const candles = useSessionStore((s) => s.candles.length);
  const now = useNow();

  const venues = selectedVenues.length ? selectedVenues : Object.keys(connections);
  const live = venues.filter((v) => connections[v]?.status === "live").length;
  const lag = session?.lastTradeLagMs ?? null;
  const lagTone = lag === null ? "" : lag < 250 ? "tone-buy" : lag < 1000 ? "tone-warn" : "tone-sell";
  const lagLabel = displayStatus.lagObservation === "recorded" ? "Recorded lag" :
    displayStatus.lagObservation === "synthetic" ? "Synthetic lag" : "Feed lag";

  return (
    <footer className="statusbar">
      <div className="statusbar-feeds">
        {venues.map((venue) => {
          const conn = connections[venue];
          const status = conn?.status ?? "connecting";
          return (
            <span
              key={venue}
              className="feed"
              title={`${venueLabel(venue)}: ${status}${conn?.error ? ` — ${conn.error}` : ""}${conn?.reconnectAttempts ? ` · ${conn.reconnectAttempts} reconnects` : ""}`}
            >
              <span className={`status-dot ${status}`} />
              {venueLabel(venue)}
              <em>{conn?.stateFlag ?? status}</em>
              {conn?.error && <span className="feed-error" role="status">{conn.error}</span>}
            </span>
          );
        })}
        {venues.length > 1 && <>
          <strong>{live}/{venues.length} live</strong>
          <span title="Prices in different quote assets are combined without conversion. Use Native book & tape for exact primary-instrument inspection.">Indicative aggregate · quotes retained</span>
        </>}
        <details className="display-status">
          <summary className={displayStatus.gap || displayStatus.reasons.length ? "tone-warn" : ""}>Coverage{displayStatus.gap ? " · gap" : ""}</summary>
          <div className="display-status-content">
            <strong>Primary: {displayStatus.primaryInstrumentId ?? "not selected"}</strong>
            <span>{displayStatus.instrumentIds.length} instruments · {displayStatus.unknownSideTrades} unknown-side prints · {displayStatus.historyTrades} history prints</span>
            {displayStatus.reasons.map((reason) => <span key={reason}>{reason}</span>)}
          </div>
        </details>
      </div>
      <div className="statusbar-meta">
        <span title="Native exchange-to-receive wall-clock difference for the latest ordered print; includes clock skew. Missing or negative differences are unavailable. Recorded lag describes capture-time receipt, not replay delivery.">
          {lagLabel} <b className={lagTone}>{lag === null ? "—" : `${lag.toFixed(0)} ms`}</b>
        </span>
        <span>Session <b>{session && now ? formatDuration(now - session.startTime) : "—"}</b></span>
        <span>Candles <b>{candles + (session ? 1 : 0)}</b></span>
        <span className="statusbar-clock">
          {now ? new Date(now).toISOString().slice(11, 19) : "--:--:--"} <small>UTC</small>
        </span>
      </div>
    </footer>
  );
}
