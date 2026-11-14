import type { Frame } from './api';

/** Live source reasons describe all-provider discovery, not the selected book. */
export function terminalDiagnosticReasons(frame: Pick<Frame, 'health' | 'source' | 'book'> | null) {
  return {
    selectedReasons: [...new Set([
      ...(frame?.health.reasons ?? []),
      ...(frame?.book?.quality.reason_codes ?? []),
      ...(frame?.source.mode !== 'live' ? frame?.source.reasons ?? [] : []),
    ])],
    providerReasons: frame?.source.mode === 'live' ? frame.source.reasons : [],
  };
}

export interface TerminalIncidentInput {
  terminalError: string | null;
  frameError: string | null;
  catalogError: string | null;
  storageError: string | null;
  connection: string;
  status: string | null;
  ready: boolean;
  /** Selected session/book evidence only; never all-provider discovery reasons. */
  qualityReasons: readonly string[];
  synthetic: boolean;
}

export interface TerminalIncident {
  id: 'overflow' | 'terminal' | 'frame' | 'transport' | 'seeking' | 'readiness' | 'catalog' | 'storage' | 'synthetic';
  severity: 'error' | 'warning' | 'info';
  message: string;
  recovery: 'diagnostics' | 'reconnect' | 'wait' | 'source' | 'storage' | 'synthetic';
}

// Ordered by evidence failure, transport/reconstruction, then ancillary notices.
// Transport and rendering performance never establish market-data readiness.
export function terminalIncidents(input: TerminalIncidentInput): TerminalIncident[] {
  const incidents: TerminalIncident[] = [];
  if (input.qualityReasons.some(reason => reason.includes('analytical_queue_overflow')) || input.terminalError?.includes('analytical_queue_overflow') || input.frameError?.includes('analytical_queue_overflow')) incidents.push({
    id: 'overflow', severity: 'error', message: 'analytical_queue_overflow · analytical history lost; evidence unavailable. Operator must inspect collector capacity. Browser reconnect cannot recover missing observations or repair capacity.', recovery: 'diagnostics',
  });
  if (input.terminalError) incidents.push({ id: 'terminal', severity: 'error', message: input.terminalError, recovery: input.terminalError.includes('analytical_queue_overflow') ? 'diagnostics' : 'reconnect' });
  if (input.frameError) incidents.push({ id: 'frame', severity: 'error', message: input.frameError, recovery: 'diagnostics' });
  if (input.connection !== 'Connected') incidents.push({ id: 'transport', severity: 'warning', message: `Transport ${input.connection.toLowerCase()} · market evidence unavailable.`, recovery: 'reconnect' });
  if (input.status === 'seeking') incidents.push({ id: 'seeking', severity: 'info', message: 'Seeking · rebuilding analytics; previous evidence is unavailable until reconstruction completes.', recovery: 'wait' });
  else if (input.status && (!input.ready || ['unavailable', 'error', 'loading'].includes(input.status))) incidents.push({
    id: 'readiness', severity: 'warning', message: `Selected-market evidence ${input.status === 'loading' ? 'loading' : 'not ready'}${input.qualityReasons.length ? ` · ${input.qualityReasons.join('; ')}` : ''}.`, recovery: 'source',
  });
  if (input.catalogError) incidents.push({ id: 'catalog', severity: 'warning', message: input.catalogError, recovery: 'wait' });
  if (input.storageError) incidents.push({ id: 'storage', severity: 'warning', message: input.storageError, recovery: 'storage' });
  if (input.synthetic) incidents.push({ id: 'synthetic', severity: 'info', message: 'Synthetic demo · not live market data. Only delivered fixture observations are shown.', recovery: 'synthetic' });
  return incidents;
}
