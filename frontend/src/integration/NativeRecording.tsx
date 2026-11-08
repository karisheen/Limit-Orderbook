import { useEffect, useRef, useState } from 'react';
import { errorMessage, request } from '../api';
import type { ManagedRecording, RecordingMutationResponse, RecordingsResponse } from '../workflow-api';
import { Reasons } from '../WorkflowUI';
import { useNativeTerminal } from './NativeContext';
import { ownedSessionRecording } from './nativeWorkspace';
import { venueLabel } from '../format';

export function NativeRecording() {
  const { terminal, connected, fence, selectSource } = useNativeTerminal();
  const { frame, catalog } = terminal;
  const [recordings, setRecordings] = useState<RecordingsResponse['recordings']>([]);
  const [captureScope, setCaptureScope] = useState<ManagedRecording['scope']>('instrument');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const listing = useRef<AbortController | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let timer: number | undefined;
    const controller = new AbortController();
    listing.current = controller;
    const refresh = async () => {
      try {
        const result = await request<RecordingsResponse>('/recordings', undefined, controller.signal);
        if (!controller.signal.aborted) setRecordings(result.recordings);
      } catch (cause) { if (!controller.signal.aborted) setError(errorMessage(cause)); }
      finally { if (!controller.signal.aborted) timer = window.setTimeout(() => void refresh(), 3000); }
    };
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [fence, revision]);
  useEffect(() => {
    operation.current?.abort();
    setPending(false);
    setError(null);
    setCaptureScope('instrument');
    return () => operation.current?.abort();
  }, [fence]);
  const ownedRecording = ownedSessionRecording(recordings, frame?.session_id);
  const ownedId = ownedRecording?.id;
  const capture = async (action: 'start' | 'stop') => {
    if (!frame || (action === 'stop' ? !ownedRecording : !!ownedRecording)) return;
    operation.current?.abort();
    listing.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    setPending(true);
    setError(null);
    try {
      // Durable reconnect/finalization is not a ten-second analytical query.
      // The owner fence still aborts obsolete requests; the server joins cleanup.
      const result = await request<RecordingMutationResponse>(`/recordings/${action}`, {
        session_id: frame.session_id, generation: frame.generation,
        ...(action === 'stop' ? { id: ownedId } : { scope: captureScope }),
      }, controller.signal);
      if (controller.signal.aborted) return;
      setRecordings(result.recordings);
      setRevision(value => value + 1);
    } catch (cause) {
      if (!controller.signal.aborted) { setError(errorMessage(cause)); setRevision(value => value + 1); }
    } finally { if (!controller.signal.aborted) setPending(false); }
  };
  return <details className="native-recording" aria-busy={pending}><summary>Recordings</summary><div className="native-control-body">
    <p className="caption">Durable native capture. Selected instrument records all its current session channels, including any reference/context streams; Session cohort records all current session channels across instruments and aggregate venues. Unavailable channels can make a capture incomplete. Selection changes do not change captured keys. Closing the session finalizes its capture.</p>
    <label>Capture scope<select aria-label="Capture scope" value={ownedRecording?.scope ?? captureScope} disabled={pending || !!ownedId} onChange={event => setCaptureScope(event.target.value as ManagedRecording['scope'])}><option value="instrument">Selected instrument · all channels</option><option value="cohort">Session cohort</option></select></label>
    <button disabled={!connected || terminal.busy || pending || frame?.source.mode !== 'live' || !!ownedId} onClick={() => void capture('start')}>{captureScope === 'instrument' ? 'Record selected instrument' : 'Record session cohort'}</button>
    <button disabled={!connected || terminal.busy || pending || !ownedId} onClick={() => void capture('stop')}>Stop recording</button>
    <button disabled={pending} onClick={() => setRevision(value => value + 1)}>Refresh recordings</button>
    {frame?.source.mode !== 'live' && <p className="caption">Recording is available for public live data only.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!recordings.length && <p className="caption">No managed recordings.</p>}
    {recordings.map(item => {
      const source = catalog?.sources.find(candidate => candidate.id === item.source_id);
      return <section key={item.id} className="native-recording-row"><strong>{item.label}</strong> <span>{item.status}</span><Reasons reasons={item.reasons} />
        <p className="caption">Scope: {item.scope === 'cohort' ? 'Session cohort' : 'Selected instrument'} · {item.instruments.length} instruments · {item.channels.length} channels</p>
        <details><summary>Captured instruments and channels</summary>
          <p>Instruments: {item.instruments.length ? item.instruments.join(', ') : 'None reported'}</p>
          {item.channels.length ? <ul>{item.channels.map(channel => <li key={JSON.stringify([channel.venue, channel.instrument_id, channel.stream])}>{venueLabel(channel.venue)} · {channel.instrument_id} · {channel.stream}</li>)}</ul> : <p>No channels reported.</p>}
        </details>
        {item.source_id && <button disabled={terminal.busy || source?.status !== 'ready'} onClick={() => { if (source) selectSource(source); }}>Replay recording</button>}
      </section>;
    })}
  </div></details>;
}
