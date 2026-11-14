import type { Frame, Instrument } from '../api';
import type { ManagedRecording } from '../workflow-api';
import type { PaneId, WorkspacePreferences } from '../workspace';

export type NativeMetadataFrame = Pick<Frame, 'session_id' | 'generation' | 'selection'> & {
  source: Pick<Frame['source'], 'id' | 'mode'>;
};

export function nativeMetadataScope(frame: NativeMetadataFrame | null): string | null {
  return frame ? JSON.stringify([frame.session_id, frame.generation, frame.source.mode, frame.source.id,
    frame.selection.instrument_id, frame.selection.metadata_status, frame.selection.metadata_version]) : null;
}

// Canonical field order keeps equivalent decoded catalog objects in one drain
// lifetime. Include precision/access/status too: a version alone is not evidence.
export function nativeInstrumentEvidence(instrument: Instrument | undefined): string | null {
  if (!instrument) return null;
  const asset = (value: Instrument['settlement']) => value && [value.asset_id, value.ticker, value.native_token_id];
  return JSON.stringify([
    instrument.instrument_id, instrument.venue, instrument.api_family, instrument.native_symbol,
    asset(instrument.base), asset(instrument.quote), asset(instrument.settlement), instrument.market_type,
    instrument.tick_size, instrument.lot_size, instrument.quantity_unit, instrument.status,
    instrument.data_access, instrument.execution_access, instrument.metadata_version, instrument.metadata_status,
    instrument.contract_multiplier, instrument.contract_value_currency, instrument.inverse,
  ]);
}

export interface ConfirmedCatalog {
  scope: string;
  instruments: Instrument[];
  selected: Instrument | undefined;
}

// Failed refreshes may retain evidence only within the exact confirmed scope.
// A successful empty catalog is authoritative, not a reason to use old metadata.
export function retainConfirmedCatalog(previous: ConfirmedCatalog | null, frame: NativeMetadataFrame | null, instruments: Instrument[], error: string | null): ConfirmedCatalog | null {
  const scope = nativeMetadataScope(frame);
  if (!frame || scope === null) return null;
  if (error) return previous?.scope === scope ? previous : null;
  // Telemetry/status-only frames never scan an unchanged catalog.
  if (previous?.scope === scope && previous.instruments === instruments) return previous;
  const candidate = instruments.find(instrument => instrument.instrument_id === frame.selection.instrument_id
    && instrument.metadata_version === frame.selection.metadata_version && instrument.metadata_status === frame.selection.metadata_status);
  // Discovery may enrich independently; never apply its new precision to old evidence.
  const selected = candidate ?? (previous?.scope === scope && instruments.some(instrument => instrument.instrument_id === frame.selection.instrument_id) ? previous.selected : undefined);
  return { scope, instruments, selected };
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

export function settledRecordingStop(recordings: readonly ManagedRecording[], id: string): ManagedRecording | undefined {
  return recordings.find(recording => recording.id === id && recording.session_id === null && recording.status !== 'recording');
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

