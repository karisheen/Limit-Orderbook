import { useEffect, useMemo, useRef, useState } from 'react';
import type { Frame, Reference, VisualGrid } from './api';
import { browserResidencyMs, chartEvidence, chartLag, evidenceIdentity, referenceLag } from './data-freshness';
import type { ChartEvidenceLayers, EvidenceLag } from './data-freshness';

function useEvidenceResidence(identity: string | null) {
  const [, tick] = useState(0);
  const now = performance.now();
  const observed = useRef({ identity, at: now });
  if (observed.current.identity !== identity) observed.current = { identity, at: now };
  useEffect(() => {
    if (!identity) return;
    const timer = window.setInterval(() => tick(value => (value + 1) % 3600), 1000);
    return () => window.clearInterval(timer);
  }, [identity]);
  return { now, residentMs: identity ? browserResidencyMs(now, observed.current.at) : null };
}

function duration(ms: number | null): string {
  if (ms === null) return 'unavailable';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

interface BadgeProps {
  frame: Frame | null;
  lag: EvidenceLag;
  residentMs: number | null;
  responseMs?: number | null;
  label: string;
  frozen: boolean;
  testId: string;
  viewId?: string;
}

function FreshnessBadge({ frame, lag, residentMs, responseMs, label, frozen, testId, viewId }: BadgeProps) {
  const mode = frame?.source.mode;
  const identity = mode === 'replay' ? 'Replay' : mode === 'live' ? 'Live' : mode === 'demo' ? 'Demo' : 'No source';
  const explanation = `${label}: ${identity}${frozen ? ', frozen' : ''}. Source-clock lag compares the displayed evidence receive timestamp with the latest observed same-clock source/evaluation highwater. It is not exchange latency, transport latency, or wall-clock age; a silent source can stop its clock frontier. Replay uses recording time, never the recording’s wall date. Resident is browser elapsed time since this evidence was first displayed here; new frames with unchanged evidence do not reset it.${responseMs !== undefined ? ` Response resident: ${duration(responseMs)} since this displayed query response arrived, separate from draw/paint/ACK timing.` : ''}${lag.reason ? ` Source lag unavailable: ${lag.reason}.` : ` Clock: ${lag.clockId}; evidence: ${lag.evidenceNs} ns; highwater: ${lag.highwaterNs} ns.`}`;
  return <span className={`freshness freshness--${lag.ms === null ? 'unavailable' : 'available'}${frozen ? ' freshness--frozen' : ''}`}
    title={explanation} aria-label={`${label}: ${identity}, ${frozen ? 'frozen, ' : ''}source-clock lag ${duration(lag.ms)}${lag.reason ? ` (${lag.reason})` : ''}, evidence resident ${duration(residentMs)}`}
    aria-description={explanation} tabIndex={0}
    data-frame-id={frame?.frame_id} data-generation={frame?.generation}
    data-testid={testId} data-source-mode={mode ?? 'unavailable'} data-source-id={frame?.source.id}
    data-source-lag-ms={lag.ms ?? undefined} data-evidence-resident-ms={residentMs ?? undefined}
    data-response-resident-ms={responseMs ?? undefined} data-clock-id={lag.clockId ?? undefined}
    data-evidence-ns={lag.evidenceNs ?? undefined} data-highwater-ns={lag.highwaterNs ?? undefined}
    data-unavailable-reason={lag.reason ?? undefined} data-view-id={viewId} data-frozen={frozen}>
    <span className="freshness-identity">{identity}{frozen ? ' frozen' : ''}</span>
    <span className="freshness-value"> · source lag {lag.ms === null ? 'unavailable' : duration(lag.ms)}</span>
    <span className="freshness-value"> · resident {residentMs === null ? '—' : duration(residentMs)}</span>
  </span>;
}

export interface FreshnessProps {
  frame: Frame | null;
  reference: Reference | null;
  connected: boolean;
  label?: string;
  frozen?: boolean;
}

export function Freshness({ frame, reference, connected, label = 'Displayed data', frozen = false }: FreshnessProps) {
  const key = evidenceIdentity(reference);
  const identity = key && frame ? `${frame.session_id}:${frame.generation}:${frame.source.id}:${key}` : null;
  const { residentMs } = useEvidenceResidence(identity);
  const lag = useMemo(() => referenceLag(frame, reference, connected), [frame, reference, connected]);
  return <FreshnessBadge frame={frame} lag={lag} residentMs={residentMs} label={label} frozen={frozen} testId="data-freshness" />;
}

export interface ChartFreshnessProps extends ChartEvidenceLayers {
  frame: Frame | null;
  /** The painted view, not an unpainted replacement query result. */
  view: VisualGrid | null;
  receivedAtMs: number | null;
  connected: boolean;
  frozen?: boolean;
}

export function ChartFreshness({ frame, view, receivedAtMs, connected, frozen = false, mode = 'heatmap', rowViewport }: ChartFreshnessProps) {
  const evidence = useMemo(() => chartEvidence(view, { mode, rowViewport }), [view, mode, rowViewport]);
  const key = evidenceIdentity(evidence.reference);
  const identity = key && frame ? `${frame.session_id}:${frame.generation}:${frame.source.id}:${key}` : null;
  const { now, residentMs } = useEvidenceResidence(identity);
  const lag = useMemo(() => chartLag(frame, view, connected, { mode, rowViewport }, evidence), [frame, view, connected, mode, rowViewport, evidence]);
  return <FreshnessBadge frame={frame} lag={lag} residentMs={residentMs} responseMs={browserResidencyMs(now, receivedAtMs)}
    label={`Displayed ${mode} chart (primary layer)`} frozen={frozen} testId="chart-freshness" viewId={view?.view_id} />;
}
