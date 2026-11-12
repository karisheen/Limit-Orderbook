// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useEffect, useState } from "react";
import { useSessionStore } from "@/lib/session/SessionContext";
import { formatDuration } from "@/lib/format";
import { venueLabel } from "../../format";
import { useNativeTerminal } from "../../integration/NativeContext";

// ─── Status bar ──────────────────────────────────────────────────
// Native book observations and independent retained prints, lag and UTC clock.

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
  const { terminal, usable, instruments } = useNativeTerminal();
  const session = useSessionStore((s) => s.session);
  const candles = useSessionStore((s) => s.candles.length);
  const hasCurrentCandle = useSessionStore((s) => s.currentCandle !== null);
  const now = useNow();

  const venues = selectedVenues.length ? selectedVenues : Object.keys(connections);
  const cohortCurrent = !!terminal.frame?.selection.instrument_id &&
    displayStatus.primaryInstrumentId === terminal.frame.selection.instrument_id;
  const observedBooks = cohortCurrent ? venues.filter((v) => connections[v]?.status === "live").length : 0;
  const lag = session?.lastTradeLagMs ?? null;
  const lagTone = lag === null ? "" : lag < 250 ? "tone-buy" : lag < 1000 ? "tone-warn" : "tone-sell";
  const lagLabel = displayStatus.lagObservation === "recorded" ? "Recorded lag" :
    displayStatus.lagObservation === "synthetic" ? "Synthetic lag" : "Feed lag";

  return (
    <footer className="statusbar">
      <div className="statusbar-feeds">
        {venues.map((venue) => {
          const conn = connections[venue];
          const status = !cohortCurrent ? "connecting" : !usable && conn?.status === "live" ? "stale" : conn?.status ?? "connecting";
          const bookLabel = status === "live" ? "book observed" : status === "stale" ? "book unavailable / degraded" : "book unobserved";
          const tradesObserved = cohortCurrent && instruments.some(instrument => instrument.venue === venue &&
            displayStatus.retainedTradeInstrumentIds.includes(instrument.instrument_id));
          return (
            <span
              key={venue}
              className="feed"
              title={`${venueLabel(venue)}: ${bookLabel} · ${tradesObserved ? "retained non-history native prints observed" : "no retained non-history native prints"} · ${conn?.stateFlag ?? status}${conn?.error ? ` — ${conn.error}` : ""}. Book status does not establish trade-stream freshness or financial support.`}
            >
              <span className={`status-dot ${status}`} />
              {venueLabel(venue)}
              <em>{bookLabel} · {tradesObserved ? "prints observed" : "prints unobserved"}</em>
              {conn?.error && <span className="feed-error" role="status">{conn.error}</span>}
            </span>
          );
        })}
        {venues.length > 1 && <>
          <strong>{observedBooks}/{venues.length} books observed</strong>
        </>}
        {displayStatus.instrumentIds.length > 1 && <>
          <span title="Display studies only, never financial execution inputs. Prices in different quote assets are combined without conversion. Costs and paper target the exact native primary.">Indicative aggregate · not execution input</span>
        </>}
        <details className="display-status">
          <summary className={displayStatus.gap || displayStatus.reasons.length ? "tone-warn" : ""}>Coverage{displayStatus.gap ? " · gap" : ""}</summary>
          <div className="display-status-content">
            <strong>Primary: {displayStatus.primaryInstrumentId ?? "not selected"}</strong>
            <span>{displayStatus.instrumentIds.length} instruments · {displayStatus.unknownSideTrades} unknown-side prints · {displayStatus.historyTrades} history prints</span>
            <span>Book and retained print observations are independent; neither establishes freshness, continuous coverage or financial support. Subscription history is not ordered trade-stream evidence.</span>
            {displayStatus.reasons.map((reason) => <span key={reason}>{reason}</span>)}
          </div>
        </details>
      </div>
      <div className="statusbar-meta">
        <span title="Native exchange-to-receive wall-clock difference for the latest ordered print; includes clock skew. Missing or negative differences are unavailable. Recorded lag describes capture-time receipt, not replay delivery.">
          {lagLabel} <b className={lagTone}>{lag === null ? "—" : `${lag.toFixed(0)} ms`}</b>
        </span>
        <span title="Elapsed projected source time; paused playback does not advance the session.">Session <b>{session && displayStatus.displayTimeMs !== null
          ? formatDuration(Math.max(0, displayStatus.displayTimeMs - session.startTime)) : "—"}</b></span>
        <span>Candles <b>{candles + (hasCurrentCandle ? 1 : 0)}</b></span>
        <span className="statusbar-clock">
          {now ? new Date(now).toISOString().slice(11, 19) : "--:--:--"} <small>UTC</small>
        </span>
      </div>
    </footer>
  );
}
