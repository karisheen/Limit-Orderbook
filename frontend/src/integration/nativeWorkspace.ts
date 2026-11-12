import type { Frame, Instrument } from '../api';
import type { ManagedRecording } from '../workflow-api';
import type { PaneId, WorkspacePreferences } from '../workspace';

export interface ConfirmedCatalog {
  scope: string;
  instruments: Instrument[];
  selected: Instrument | undefined;
}

// Failed refreshes may retain evidence only within the exact confirmed scope.
// A successful empty catalog is authoritative, not a reason to use old metadata.
export function retainConfirmedCatalog(previous: ConfirmedCatalog | null, frame: Pick<Frame, 'session_id' | 'generation' | 'source' | 'selection'> | null, instruments: Instrument[], error: string | null): ConfirmedCatalog | null {
  if (!frame) return null;
  const scope = JSON.stringify([frame.session_id, frame.generation, frame.source.mode, frame.source.id, frame.selection.instrument_id, frame.selection.metadata_status, frame.selection.metadata_version]);
  if (error) return previous?.scope === scope ? previous : null;
  const candidate = instruments.find(instrument => instrument.instrument_id === frame.selection.instrument_id
    && instrument.metadata_version === frame.selection.metadata_version && instrument.metadata_status === frame.selection.metadata_status);
  // Discovery may enrich independently; never apply its new precision to old evidence.
  const selected = candidate ?? (previous?.scope === scope && instruments.some(instrument => instrument.instrument_id === frame.selection.instrument_id) ? previous.selected : undefined);
  return previous?.scope === scope && previous.instruments === instruments && previous.selected === selected
    ? previous : { scope, instruments, selected };
}

// Discovery stays current, but financial labels must match the selected stream.
// Missing pinned metadata removes that choice rather than borrowing newer precision.
export function financialInstrumentsForSelection(instruments: Instrument[], selectedId: string | null | undefined, selected: Instrument | undefined): Instrument[] {
  if (!selectedId) return instruments;
  if (!selected || selected.instrument_id !== selectedId) return instruments.filter(instrument => instrument.instrument_id !== selectedId);
  if (!instruments.some(instrument => instrument.instrument_id === selectedId)) return [...instruments, selected];
  return instruments.map(instrument => instrument.instrument_id === selectedId ? selected : instrument);
}

// An owned capture remains active until Stop releases its session, even on error.
export function ownedSessionRecording(recordings: readonly ManagedRecording[], sessionId: string | null | undefined): ManagedRecording | undefined {
  if (sessionId == null) return undefined;
  return recordings.find(recording => recording.session_id === sessionId);
}

export type NativeView = NonNullable<WorkspacePreferences['nativeView']>;
export type MarketPane = PaneId | 'price';

export function viewForPane(pane: MarketPane): NativeView {
  return pane === 'price' ? 'price' : pane === 'dom' || pane === 'tape' ? 'book' : 'orderflow';
}

export function paneForView(view: NativeView, preferred?: MarketPane): MarketPane {
  if (preferred && viewForPane(preferred) === view) return preferred;
  return view === 'price' ? 'price' : view === 'book' ? 'dom' : 'heatmap';
}

