// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useEffect, useState } from "react";
import { useSessionStore } from "@/lib/session/SessionContext";
import { formatDuration, VENUE_LABEL } from "@/lib/format";

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
  const marketType = useSessionStore((s) => s.marketType);
  const session = useSessionStore((s) => s.session);
  const candles = useSessionStore((s) => s.candles.length);
  const now = useNow();

  const venues = marketType === "forex" ? (["finnhub-forex"] as const) : selectedVenues;
  const live = venues.filter((v) => connections[v]?.status === "live").length;
  const lag = session?.lastTradeLagMs ?? null;
  const lagTone = lag === null ? "" : lag < 250 ? "tone-buy" : lag < 1000 ? "tone-warn" : "tone-sell";

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
              title={`${VENUE_LABEL[venue] ?? venue}: ${status}${conn?.error ? ` — ${conn.error}` : ""}${conn?.reconnectAttempts ? ` · ${conn.reconnectAttempts} reconnects` : ""}`}
            >
              <span className={`status-dot ${status}`} />
              {VENUE_LABEL[venue] ?? venue}
              <em>{conn?.stateFlag ?? status}</em>
              {conn?.error && <span className="feed-error" role="status">{conn.error}</span>}
            </span>
          );
        })}
        {venues.length > 1 && <>
          <strong>{live}/{venues.length} live</strong>
          <span title="Venue USD, USDT and USDC prices are combined without FX conversion; use Workbench for exact venue/quote analysis.">Indicative aggregate · no FX conversion</span>
        </>}
        {marketType === "forex" && (
          <span className="tone-warn" title="Finnhub price ticks: tick-rule direction, provider volume or unit-tick fallback; no centralized executions, bid/ask quotes or L2 depth">
            FX quote proxy · tick volume · no L2
          </span>
        )}
      </div>
      <div className="statusbar-meta">
        <span title="Exchange timestamp to local receive time for the latest print (includes clock skew)">
          Feed lag <b className={lagTone}>{lag === null ? "—" : `${lag.toFixed(0)} ms`}</b>
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
