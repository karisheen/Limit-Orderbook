import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import type { Instrument, Source } from '../api';
import { useTerminal } from '../useTerminal';
import type { TerminalController } from '../useTerminal';
import { useWorkspacePreferences } from '../workspace';
import type { WorkspacePreferences, WorkspaceState } from '../workspace';
import { retainConfirmedCatalog } from './nativeWorkspace';
import type { ConfirmedCatalog } from './nativeWorkspace';

interface NativeContextValue {
  terminal: TerminalController;
  preferences: WorkspacePreferences;
  updatePreferences: (patch: Partial<WorkspacePreferences>) => void;
  instrument: Instrument | undefined;
  instruments: Instrument[];
  connected: boolean;
  selectInstrument: (id: string) => Promise<void>;
  selectSource: (source: Source) => void;
  workspace: WorkspaceState;
  fence: string;
  usable: boolean;
}

const NativeContext = createContext<NativeContextValue | null>(null);
const emptyInstruments: Instrument[] = [];

export function NativeProvider({ children }: { children: ReactNode }) {
  const workspace = useWorkspacePreferences();
  const { preferences, setPreferences } = workspace;
  const terminal = useTerminal(preferences);
  const { frame, select, source } = terminal;
  const confirmedCatalog = useRef<ConfirmedCatalog | null>(null);
  const metadata = retainConfirmedCatalog(confirmedCatalog.current, frame, terminal.catalog?.instruments ?? emptyInstruments, terminal.catalogError);
  useEffect(() => { confirmedCatalog.current = metadata; }, [metadata]);
  const instruments = metadata?.instruments ?? emptyInstruments;
  const instrument = instruments.find(item => item.instrument_id === frame?.selection.instrument_id);
  const connected = terminal.connection === 'Connected';
  const usable = connected && !!frame && !['seeking', 'unavailable', 'error', 'loading'].includes(frame.status);
  const fence = frame ? `${frame.session_id}:${frame.generation}:${frame.source.id}:${frame.selection.instrument_id}` : '';
  const attachedFence = useRef<string | null>(null);
  const initialSource = useRef(preferences.source);
  const updatePreferences = useCallback((patch: Partial<WorkspacePreferences>) => {
    setPreferences(previous => ({ ...previous, ...patch }));
  }, [setPreferences]);
  const resetViews = useCallback(() => {
    window.dispatchEvent(new Event('terminal-clear-cursor'));
    setPreferences(previous => ({
      ...previous, priceStep: '', domPriceStep: '', range: null, followSpanNs: null,
      follow: true, priceOffset: 0, paused: false, domFrozen: false, tapeFrozen: false,
      secondary: { ...previous.secondary, priceStep: '', range: null, followSpanNs: null, follow: true, priceOffset: 0, paused: false },
    }));
  }, [setPreferences]);
  const selectInstrument = useCallback(async (id: string) => {
    resetViews();
    await select(id);
  }, [resetViews, select]);
  const selectSource = useCallback((next: Source) => {
    resetViews();
    updatePreferences({ source: { mode: next.mode, id: next.id }, sourceExplicit: true });
    const url = new URL(window.location.href);
    if (url.searchParams.has('source')) {
      url.searchParams.set('source', next.id);
      window.history.replaceState(null, '', url);
    }
    source(next.mode, next.id);
  }, [resetViews, updatePreferences, source]);
  const sourceId = frame?.source.id;
  const sourceMode = frame?.source.mode;
  const instrumentId = frame?.selection.instrument_id;
  useEffect(() => {
    if (sourceId && sourceMode) setPreferences(previous =>
      previous.source?.id === sourceId && previous.instrument === instrumentId ? previous
        : { ...previous, source: { id: sourceId, mode: sourceMode }, instrument: instrumentId ?? '' });
  }, [sourceId, sourceMode, instrumentId, setPreferences]);
  useEffect(() => {
    // Saved ranges/freezes only survive a same-source initial attachment. Every
    // replacement session, selection or replay generation fences all consumers.
    if (!fence || attachedFence.current === fence) return;
    const replacingEvidence = attachedFence.current !== null;
    attachedFence.current = fence;
    if (!replacingEvidence) {
      if (initialSource.current && (initialSource.current.id !== sourceId || initialSource.current.mode !== sourceMode)) resetViews();
      return;
    }
    window.dispatchEvent(new Event('terminal-clear-cursor'));
    setPreferences(previous => ({
      ...previous, paused: false, domFrozen: false, tapeFrozen: false, range: null, followSpanNs: null, follow: true,
      secondary: { ...previous.secondary, paused: false, range: null, followSpanNs: null, follow: true },
    }));
  }, [fence, sourceId, sourceMode, resetViews, setPreferences]);
  const value = useMemo(() => ({ terminal, preferences, updatePreferences, instrument, instruments, connected,
    selectInstrument, selectSource, workspace, fence, usable }),
  [terminal, preferences, updatePreferences, instrument, instruments, connected, selectInstrument, selectSource, workspace, fence, usable]);
  return <NativeContext.Provider value={value}>{children}</NativeContext.Provider>;
}

export function useNativeTerminal() {
  const context = useContext(NativeContext);
  if (!context) throw new Error('useNativeTerminal must be used within NativeProvider.');
  return context;
}
