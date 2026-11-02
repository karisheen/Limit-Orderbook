import { useEffect, useState } from 'react';
import type { Catalog, Mode, VisualGrid } from './api';

export type ChartMode = 'heatmap' | 'candles' | 'footprint' | 'profile' | 'cvd' | 'ofi';
export interface WorkspacePreferences {
  version: 1; source: { mode: Mode; id: string } | null; sourceExplicit: boolean; instrument: string;
  preset: 'balanced' | 'analysis' | 'ladder'; leftWidth: number; rightWidth: number; bottomHeight: number;
  leftOpen: boolean; rightOpen: boolean; bottomOpen: boolean;
  mode: ChartMode; contrast: number; showTrades: boolean; showQuotes: boolean;
  priceStep: string; bucket: string; rangeSeconds: number; range: { start_ns: string; end_ns: string } | null;
  follow: boolean; paused: boolean; domFrozen: boolean; domRows: number; priceOffset: number;
  domPriceStep: string; domUnit: 'base' | 'quote'; domCumulative: boolean;
}
export const preferenceKey = 'limit-orderbook.workspace.v1';
const defaults: WorkspacePreferences = {
  version: 1, source: null, sourceExplicit: false, instrument: '', preset: 'balanced', leftWidth: 222, rightWidth: 314, bottomHeight: 248,
  leftOpen: true, rightOpen: true, bottomOpen: true, mode: 'heatmap', contrast: 1,
  showTrades: true, showQuotes: true, priceStep: '', bucket: '1000000000', rangeSeconds: 180,
  range: null, follow: true, paused: false, domFrozen: false, domRows: 15, priceOffset: 0,
  domPriceStep: '', domUnit: 'base', domCumulative: false,
};
const bounded = (value: unknown, min: number, max: number, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
const text = (value: unknown, max: number) => typeof value === 'string' && value.length <= max ? value : '';
export function validatePreferences(value: unknown): WorkspacePreferences {
  if (!value || typeof value !== 'object' || !('version' in value) || value.version !== 1) return { ...defaults };
  const p = value as Record<string, unknown>;
  const next = { ...defaults };
  for (const key of ['leftOpen', 'rightOpen', 'bottomOpen', 'showTrades', 'showQuotes', 'follow', 'paused', 'domFrozen', 'domCumulative'] as const) if (typeof p[key] === 'boolean') next[key] = p[key];
  next.leftWidth = Math.round(bounded(p.leftWidth, 180, 360, defaults.leftWidth));
  next.rightWidth = Math.round(bounded(p.rightWidth, 250, 500, defaults.rightWidth));
  next.bottomHeight = Math.round(bounded(p.bottomHeight, 170, 460, defaults.bottomHeight));
  next.contrast = bounded(p.contrast, 1e-12, 1e12, 1);
  next.domRows = Math.round(bounded(p.domRows, 1, 100, 15));
  next.priceOffset = Math.round(bounded(p.priceOffset, -10000, 10000, 0));
  next.rangeSeconds = [30, 60, 180, 300, 600].includes(p.rangeSeconds as number) ? p.rangeSeconds as number : 180;
  next.bucket = ['1000000000', '5000000000', '10000000000'].includes(p.bucket as string) ? p.bucket as string : defaults.bucket;
  if (['balanced', 'analysis', 'ladder'].includes(p.preset as string)) next.preset = p.preset as WorkspacePreferences['preset'];
  if (['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'].includes(p.mode as string)) next.mode = p.mode as ChartMode;
  for (const key of ['priceStep', 'domPriceStep'] as const) {
    const step = text(p[key], 128);
    next[key] = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d{1,4})?$/.test(step) && /[1-9]/.test(step.split(/[eE]/)[0] ?? '') ? step : '';
  }
  if (p.domUnit === 'base' || p.domUnit === 'quote') next.domUnit = p.domUnit;
  next.instrument = text(p.instrument, 512);
  const source = p.source as Record<string, unknown> | null;
  if (source && typeof source === 'object' && ['demo', 'replay', 'live'].includes(source.mode as string) && text(source.id, 512)) next.source = { mode: source.mode as Mode, id: source.id as string };
  next.sourceExplicit = !!next.source && (p.sourceExplicit === true || (p.sourceExplicit === undefined && next.source.mode !== 'demo'));
  const range = p.range as Record<string, unknown> | null;
  if (range && typeof range === 'object' && /^\d{1,24}$/.test(text(range.start_ns, 24)) && /^\d{1,24}$/.test(text(range.end_ns, 24))) {
    const start = BigInt(range.start_ns as string), end = BigInt(range.end_ns as string);
    if (end > start && end - start <= 1_800_000_000_000n) next.range = { start_ns: start.toString(), end_ns: end.toString() };
  }
  if (!next.range) next.follow = true;
  return next;
}
export function loadPreferences(): WorkspacePreferences {
  try { const raw = localStorage.getItem(preferenceKey); return raw && raw.length <= 8192 ? validatePreferences(JSON.parse(raw)) : { ...defaults }; }
  catch { return { ...defaults }; }
}
export function startupSource(catalog: Catalog, saved: Pick<WorkspacePreferences, 'source' | 'sourceExplicit' | 'instrument'>, search: string) {
  const override = new URLSearchParams(search).get('source');
  const id = override !== null ? override : saved.sourceExplicit && saved.source ? saved.source.id
    : catalog.preferred_source_id ?? saved.source?.id ?? catalog.sources.find(source => source.mode === 'demo')?.id;
  const source = catalog.sources.find(source => source.id === id);
  if (!source) throw new Error(`Requested source ${id || '(empty)'} is unavailable. Live data requires the operator to enable --network. Select another source explicitly; no demo substitution was made.`);
  const sameSource = source.id === saved.source?.id;
  return {
    options: { mode: source.mode, source_id: source.id, ...(sameSource && saved.instrument ? { instrument_id: saved.instrument } : {}) },
    notice: override !== null ? `URL selected ${source.label}; saved source preferences did not override this launch.`
      : saved.source && !sameSource ? `Using server-preferred ${source.label}, not the previously automatic ${saved.source.mode} selection. Choose a source explicitly to remember your choice.` : null,
  };
}
export function useWorkspacePreferences() {
  const [preferences, setPreferences] = useState(loadPreferences);
  const [storageError, setStorageError] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try { localStorage.setItem(preferenceKey, JSON.stringify(validatePreferences(preferences))); setStorageError(null); }
      catch { setStorageError('Workspace preferences cannot be saved in this browser. This session remains usable.'); }
    }, 200);
    return () => clearTimeout(timer);
  }, [preferences]);
  return { preferences, setPreferences, storageError };
}
export function presetLayout(preset: WorkspacePreferences['preset']) {
  return preset === 'analysis' ? { preset, leftWidth: 200, rightWidth: 280, bottomHeight: 320, leftOpen: false, rightOpen: true, bottomOpen: true }
    : preset === 'ladder' ? { preset, leftWidth: 200, rightWidth: 420, bottomHeight: 200, leftOpen: true, rightOpen: true, bottomOpen: false }
      : { preset, leftWidth: 222, rightWidth: 314, bottomHeight: 248, leftOpen: true, rightOpen: true, bottomOpen: true };
}
export function mergeVisualGrid(previous: VisualGrid | null, next: VisualGrid): VisualGrid {
  if (next.replace || !previous || previous.grid_id !== next.grid_id || previous.generation !== next.generation) return next;
  const columns = new Map(previous.columns.map(column => [column.id, column]));
  for (const column of next.columns) columns.set(column.id, column);
  const start = BigInt(next.start_ns), end = BigInt(next.end_ns);
  return { ...next, columns: [...columns.values()].filter(column => BigInt(column.end_ns) > start && BigInt(column.time_ns) < end)
    .sort((a, b) => BigInt(a.time_ns) < BigInt(b.time_ns) ? -1 : BigInt(a.time_ns) > BigInt(b.time_ns) ? 1 : 0).slice(-600) };
}
