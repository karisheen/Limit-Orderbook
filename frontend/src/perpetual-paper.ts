import type { Frame, Instrument } from './api';
import type { PaperCapability, PaperFundingModel, PaperPerpetualPosition, PaperRiskModel } from './workflow-api';

export interface PerpetualDraft {
  instrument_id: string; leverage: string; maintenance_margin_rate: string; liquidation_fee_rate: string;
  risk_provenance: string; risk_asof: string; funding_mode: '' | PaperFundingModel['mode'];
  funding_provenance: string; funding_asof: string; rate: string; interval_ns: string; first_wall_ns: string;
  events: { wall_ns: string; rate: string }[];
}
export const emptyPerpetualDraft = (): PerpetualDraft => ({
  instrument_id: '', leverage: '', maintenance_margin_rate: '', liquidation_fee_rate: '',
  risk_provenance: '', risk_asof: '', funding_mode: '', funding_provenance: '', funding_asof: '',
  rate: '', interval_ns: '', first_wall_ns: '', events: [],
});

// Capability is native policy, never a browser reconstruction of contract economics.
export function matchingPaperCapability(frame: Pick<Frame, 'selection'> | null, instrument: Instrument | undefined, capability: PaperCapability | undefined): PaperCapability | null {
  const selected = frame?.selection;
  return selected?.metadata_status === 'verified' && instrument?.metadata_status === 'verified'
    && selected.instrument_id === instrument.instrument_id && selected.metadata_version === instrument.metadata_version
    && capability?.instrument_id === selected.instrument_id && capability.metadata_version === selected.metadata_version
    ? capability : null;
}

export function perpetualModels(rows: readonly PerpetualDraft[]): { risk_models: PaperRiskModel[]; funding_models: PaperFundingModel[] } {
  if (rows.length > 64) throw new Error('At most 64 instrument models are allowed.');
  const ids = new Set<string>();
  const risk_models: PaperRiskModel[] = [];
  const funding_models: PaperFundingModel[] = [];
  for (const row of rows) {
    if (!row.instrument_id || ids.has(row.instrument_id)) throw new Error('Choose one unique exact instrument per model.');
    ids.add(row.instrument_id);
    if (!row.funding_mode) throw new Error('Explicitly select no funding, periodic funding, or dated events.');
    if (row.events.length > 10000) throw new Error('At most 10000 funding events are allowed per model.');
    risk_models.push({ instrument_id: row.instrument_id, leverage: row.leverage,
      maintenance_margin_rate: row.maintenance_margin_rate, liquidation_fee_rate: row.liquidation_fee_rate,
      provenance: row.risk_provenance, asof_wall_ns: row.risk_asof });
    const funding: PaperFundingModel = { instrument_id: row.instrument_id, mode: row.funding_mode,
      provenance: row.funding_provenance, asof_wall_ns: row.funding_asof };
    if (row.funding_mode === 'periodic') Object.assign(funding, { rate: row.rate, interval_ns: row.interval_ns, first_wall_ns: row.first_wall_ns });
    if (row.funding_mode === 'events') funding.events = row.events.map(event => ({ ...event }));
    funding_models.push(funding);
  }
  return { risk_models, funding_models };
}

// Only the native signed quantity's sign is interpreted. No financial arithmetic.
export function closeIntent(position: Pick<PaperPerpetualPosition, 'quantity'>): { side: 'buy' | 'sell'; quantity: string; reduce_only: true } | null {
  if (!/^-?\d+(?:\.\d+)?$/.test(position.quantity) || !/[1-9]/.test(position.quantity)) return null;
  return { side: position.quantity.startsWith('-') ? 'buy' : 'sell', quantity: position.quantity.replace(/^-/, ''), reduce_only: true };
}
