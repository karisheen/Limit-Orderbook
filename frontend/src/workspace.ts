import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { Catalog, Mode, VisualGrid } from './api';
import type { ChartTimeZone } from './chart-time';

export type ChartMode = 'heatmap' | 'candles' | 'footprint' | 'profile' | 'cvd' | 'ofi';
export type ChartSettings = Pick<WorkspacePreferences, 'mode' | 'contrast' | 'showTrades' | 'showQuotes' | 'footprintOverview' | 'timeZone' | 'priceStep' | 'bucket' | 'rangeSeconds' | 'range' | 'follow' | 'followSpanNs' | 'paused' | 'priceOffset'>;
export type ChartTimeIntent = { kind: 'pin'; range: { start_ns: string; end_ns: string } } | { kind: 'follow'; span_ns: string | null; rangeSeconds?: number };
export type PaneId = 'heatmap' | 'flow' | 'dom' | 'tape';
export interface WorkspacePreferences {
  version: 1; source: { mode: Mode; id: string } | null; sourceExplicit: boolean; instrument: string;
  desktopView: 'price' | 'orderflow'; candleInterval: string;
  nativeView?: 'footprint' | 'price' | 'orderflow' | 'book';
  preset: 'balanced' | 'analysis' | 'ladder'; leftWidth: number; rightWidth: number; bottomHeight: number;
  rightWidthMode?: 'auto' | 'manual';
  leftOpen: boolean; rightOpen: boolean; bottomOpen: boolean;
  mode: ChartMode; contrast: number; showTrades: boolean; showQuotes: boolean; footprintOverview: boolean; timeZone: ChartTimeZone;
  priceStep: string; bucket: string; rangeSeconds: number; range: { start_ns: string; end_ns: string } | null;
  follow: boolean; followSpanNs: string | null; paused: boolean; domFrozen: boolean; domRows: number; priceOffset: number;
  domPriceStep: string; domUnit: 'base' | 'quote'; domCumulative: boolean;
  secondary: ChartSettings;
  chartSplit: number; tapeSplit: number;
  links: Record<PaneId, boolean>;
  rangeLinked: boolean;
  tapeRows: number; tapeSide: 'all' | 'buy' | 'sell' | 'unknown'; tapeFrozen: boolean;
}
export const preferenceKey = 'limit-orderbook.workspace.v1';
const candleIntervals = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'];
const secondaryDefaults: ChartSettings = {
  mode: 'footprint', contrast: 1, showTrades: false, showQuotes: false, footprintOverview: false, timeZone: 'UTC',
  priceStep: '', bucket: '1000000000', rangeSeconds: 6, range: null,
  follow: true, followSpanNs: null, paused: false, priceOffset: 0,
};
const defaults: WorkspacePreferences = {
  version: 1, source: null, sourceExplicit: false, instrument: '', preset: 'balanced', leftWidth: 222, rightWidth: 360, rightWidthMode: 'auto', bottomHeight: 248,
  desktopView: 'price', candleInterval: '1h', nativeView: 'footprint',
  leftOpen: false, rightOpen: true, bottomOpen: false, mode: 'heatmap', contrast: 1,
  showTrades: true, showQuotes: true, footprintOverview: false, timeZone: 'UTC', priceStep: '', bucket: '1000000000', rangeSeconds: 180,
  range: null, follow: true, followSpanNs: null, paused: false, domFrozen: false, domRows: 15, priceOffset: 0,
  domPriceStep: '', domUnit: 'base', domCumulative: false,
  secondary: secondaryDefaults, chartSplit: 52, tapeSplit: 60,
  links: { heatmap: true, flow: true, dom: true, tape: true },
  rangeLinked: false,
  tapeRows: 50, tapeSide: 'all', tapeFrozen: false,
};
const bounded = (value: unknown, min: number, max: number, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
const text = (value: unknown, max: number) => typeof value === 'string' && value.length <= max ? value : '';
export function chartSettings(p: ChartSettings): ChartSettings {
  return { mode: p.mode, contrast: p.contrast, showTrades: p.showTrades, showQuotes: p.showQuotes, footprintOverview: p.footprintOverview, timeZone: p.timeZone,
    priceStep: p.priceStep, bucket: p.bucket, rangeSeconds: p.rangeSeconds, range: p.range,
    follow: p.follow, followSpanNs: p.followSpanNs, paused: p.paused, priceOffset: p.priceOffset };
}

/** A duration, never an absolute source clock coordinate; retain exact nanoseconds. */
export function chartFollowSpan(value: unknown): string | null {
  if (typeof value !== 'string' || !/^\d{1,24}$/.test(value)) return null;
  const span = BigInt(value);
  return span > 0n && span <= 1_800_000_000_000n ? span.toString() : null;
}

export function navigateChartTime(preferences: WorkspacePreferences, pane: 'heatmap' | 'flow', intent: ChartTimeIntent): WorkspacePreferences {
  let time: Pick<ChartSettings, 'follow' | 'range' | 'followSpanNs'> & Partial<Pick<ChartSettings, 'rangeSeconds'>>;
  if (intent.kind === 'pin') {
    const { start_ns, end_ns } = intent.range;
    if (!/^\d{1,24}$/.test(start_ns) || !/^\d{1,24}$/.test(end_ns)) return preferences;
    const start = BigInt(start_ns), end = BigInt(end_ns);
    if (end <= start || !chartFollowSpan((end - start).toString())) return preferences;
    time = { follow: false, range: { start_ns: start.toString(), end_ns: end.toString() }, followSpanNs: null };
  } else {
    const span = chartFollowSpan(intent.span_ns);
    if (intent.span_ns !== null && span === null) return preferences;
    if (intent.rangeSeconds !== undefined && ![6, 30, 60, 180, 300, 600].includes(intent.rangeSeconds)) return preferences;
    time = { follow: true, range: null, followSpanNs: span,
      ...(intent.rangeSeconds === undefined ? {} : { rangeSeconds: intent.rangeSeconds }) };
  }
  return { ...preferences, ...(pane === 'heatmap' || preferences.rangeLinked ? time : {}),
    secondary: pane === 'flow' || preferences.rangeLinked ? { ...preferences.secondary, ...time } : preferences.secondary };
}

/** Depth has a retained sample bound; flow can only fit its returned, paintable aggregates. */
export function retainedChartRange(view: VisualGrid, mode: ChartMode): { start_ns: string; end_ns: string } | null {
  let start: bigint | null = null;
  let end: bigint | null = null;
  const now = BigInt(view.now_ns);
  if (mode === 'heatmap' || mode === 'ofi') {
    if (view.retained_start_ns === null) return null;
    start = BigInt(view.retained_start_ns);
    end = now;
  } else {
    if (mode === 'footprint' || mode === 'profile') {
      const bucket = BigInt(view.bucket_ns);
      for (const cell of view.footprints) {
        const first = BigInt(cell.time_ns), last = first + bucket;
        if (start === null || first < start) start = first;
        if (end === null || last > end) end = last;
      }
    } else {
      for (const column of view.columns) {
        if (!(mode === 'candles' ? column.candle?.eligible : column.cvd?.eligibility.eligible && column.cvd.value !== null)) continue;
        const first = BigInt(column.time_ns), last = BigInt(column.end_ns);
        if (start === null || first < start) start = first;
        if (end === null || last > end) end = last;
      }
    }
    if (start === null || end === null) return null;
    const queryStart = BigInt(view.start_ns), queryEnd = BigInt(view.end_ns);
    if (start < queryStart) start = queryStart;
    if (end > queryEnd) end = queryEnd;
    if (end > now) end = now;
  }
  if (end <= start) return null;
  // Native requests reject durations over 30 minutes. Never invent older history.
  if (end - start > 1_800_000_000_000n) start = end - 1_800_000_000_000n;
  return { start_ns: start.toString(), end_ns: end.toString() };
}
export function validatePreferences(value: unknown, nested = false): WorkspacePreferences {
  if (!value || typeof value !== 'object' || !('version' in value) || value.version !== 1) return { ...defaults };
  const p = value as Record<string, unknown>;
  const next = { ...defaults };
  if (p.desktopView === 'price' || p.desktopView === 'orderflow') next.desktopView = p.desktopView;
  if (['footprint', 'price', 'orderflow', 'book'].includes(p.nativeView as string)) next.nativeView = p.nativeView as WorkspacePreferences['nativeView'];
  // Legacy Price action was the default, not evidence of an intentional choice.
  else if (p.desktopView === 'orderflow') next.nativeView = 'orderflow';
  if (typeof p.candleInterval === 'string' && candleIntervals.includes(p.candleInterval)) next.candleInterval = p.candleInterval;
  for (const key of ['leftOpen', 'rightOpen', 'bottomOpen', 'showTrades', 'showQuotes', 'footprintOverview', 'follow', 'paused', 'domFrozen', 'domCumulative', 'tapeFrozen', 'rangeLinked'] as const) if (typeof p[key] === 'boolean') next[key] = p[key];
  next.leftWidth = Math.round(bounded(p.leftWidth, 180, 360, defaults.leftWidth));
  next.rightWidth = Math.round(bounded(p.rightWidth, 250, 500, defaults.rightWidth));
  // A numeric legacy width may be deliberate, even when it matches an old default.
  next.rightWidthMode = p.rightWidthMode === 'auto' ? 'auto'
    : p.rightWidthMode === 'manual' || typeof p.rightWidth === 'number' && Number.isFinite(p.rightWidth) ? 'manual' : 'auto';
  next.bottomHeight = Math.round(bounded(p.bottomHeight, 170, 460, defaults.bottomHeight));
  next.contrast = bounded(p.contrast, 1e-12, 1e12, 1);
  next.domRows = Math.round(bounded(p.domRows, 1, 100, 15));
  next.priceOffset = Math.round(bounded(p.priceOffset, -10000, 10000, 0));
  next.rangeSeconds = [6, 30, 60, 180, 300, 600].includes(p.rangeSeconds as number) ? p.rangeSeconds as number : (nested ? secondaryDefaults.rangeSeconds : defaults.rangeSeconds);
  next.bucket = ['1000000000', '5000000000', '10000000000'].includes(p.bucket as string) ? p.bucket as string : defaults.bucket;
  if (['balanced', 'analysis', 'ladder'].includes(p.preset as string)) next.preset = p.preset as WorkspacePreferences['preset'];
  if (['heatmap', 'candles', 'footprint', 'profile', 'cvd', 'ofi'].includes(p.mode as string)) next.mode = p.mode as ChartMode;
  if (p.timeZone === 'UTC' || p.timeZone === 'local') next.timeZone = p.timeZone;
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
  next.followSpanNs = next.follow ? chartFollowSpan(p.followSpanNs) : null;
  next.chartSplit = Math.round(bounded(p.chartSplit, 30, 70, defaults.chartSplit));
  next.tapeSplit = Math.round(bounded(p.tapeSplit, 30, 75, defaults.tapeSplit));
  next.tapeRows = [25, 50, 100].includes(p.tapeRows as number) ? p.tapeRows as number : defaults.tapeRows;
  if (['all', 'buy', 'sell', 'unknown'].includes(p.tapeSide as string)) next.tapeSide = p.tapeSide as WorkspacePreferences['tapeSide'];
  next.links = { ...defaults.links };
  if (p.links && typeof p.links === 'object') {
    const links = p.links as Record<string, unknown>;
    for (const pane of ['heatmap', 'flow', 'dom', 'tape'] as const) if (typeof links[pane] === 'boolean') next.links[pane] = links[pane];
  }
  next.secondary = !nested && p.secondary && typeof p.secondary === 'object'
    ? chartSettings(validatePreferences({ ...secondaryDefaults, ...p.secondary, version: 1 }, true))
    : { ...secondaryDefaults };
  if (!['footprint', 'candles'].includes(next.secondary.mode)) next.secondary.mode = 'footprint';
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
export const workspaceSlotsKey = 'limit-orderbook.workspace.slots.v1';
export function validateWorkspaceSlots(value: unknown): (WorkspacePreferences | null)[] {
  const slots = Array.isArray(value) ? value : [];
  return Array.from({ length: 4 }, (_, index) => {
    const slot: unknown = slots[index];
    return slot && typeof slot === 'object' && 'version' in slot && slot.version === 1
      ? { ...validatePreferences(slot), source: null, sourceExplicit: false, instrument: '' } : null;
  });
}
export function applyWorkspaceSlot(current: WorkspacePreferences, saved: WorkspacePreferences): WorkspacePreferences {
  const validated = validatePreferences(saved);
  return { ...validated, source: current.source, sourceExplicit: current.sourceExplicit, instrument: current.instrument,
    range: null, follow: true, paused: false, domFrozen: false, tapeFrozen: false,
    secondary: { ...validated.secondary, range: null, follow: true, paused: false } };
}
function loadWorkspaceSlots() {
  try {
    const raw = localStorage.getItem(workspaceSlotsKey);
    return validateWorkspaceSlots(raw && raw.length <= 32768 ? JSON.parse(raw) : null);
  } catch { return validateWorkspaceSlots(null); }
}
export interface WorkspaceState {
  preferences: WorkspacePreferences;
  setPreferences: Dispatch<SetStateAction<WorkspacePreferences>>;
  storageError: string | null;
  slots: (WorkspacePreferences | null)[];
  saveSlot: (index: number) => void;
  loadSlot: (index: number) => void;
}

export function useWorkspacePreferences(): WorkspaceState {
  const [preferences, setPreferences] = useState(loadPreferences);
  const latestPreferences = useRef(preferences);
  useLayoutEffect(() => { latestPreferences.current = preferences; }, [preferences]);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [slots, setSlots] = useState(loadWorkspaceSlots);
  useEffect(() => {
    const save = () => {
      try { localStorage.setItem(preferenceKey, JSON.stringify(validatePreferences(latestPreferences.current))); setStorageError(null); }
      catch { setStorageError('Workspace preferences cannot be saved in this browser. This session remains usable.'); }
    };
    const timer = window.setTimeout(save, 200);
    // Keep drag writes debounced, but never lose the last selection on reload.
    window.addEventListener('pagehide', save);
    return () => { clearTimeout(timer); window.removeEventListener('pagehide', save); };
  }, [preferences]);
  const saveSlot = (index: number) => {
    if (!Number.isInteger(index) || index < 0 || index >= 4) return;
    const next = validateWorkspaceSlots(slots.map((slot, i) => i === index ? preferences : slot));
    setSlots(next);
    try { localStorage.setItem(workspaceSlotsKey, JSON.stringify(next)); setStorageError(null); }
    catch { setStorageError('Workspace slots cannot be saved in this browser. This session remains usable.'); }
  };
  const loadSlot = (index: number) => {
    const saved = slots[index];
    if (saved) setPreferences(current => applyWorkspaceSlot(current, saved));
  };
  return { preferences, setPreferences, storageError, slots, saveSlot, loadSlot };
}
export function presetLayout(preset: WorkspacePreferences['preset']): Partial<WorkspacePreferences> {
  return preset === 'analysis' ? { preset, leftWidth: 200, rightWidth: 300, rightWidthMode: 'manual', bottomHeight: 260, chartSplit: 50, tapeSplit: 55, leftOpen: false, rightOpen: true, bottomOpen: true }
    : preset === 'ladder' ? { preset, leftWidth: 200, rightWidth: 420, rightWidthMode: 'manual', bottomHeight: 200, chartSplit: 55, tapeSplit: 72, leftOpen: false, rightOpen: true, bottomOpen: false }
      : { preset, leftWidth: 222, rightWidth: 360, rightWidthMode: 'auto', bottomHeight: 248, chartSplit: 52, tapeSplit: 60, leftOpen: false, rightOpen: true, bottomOpen: false };
}

export function workspaceGeometry(preferences: Pick<WorkspacePreferences, 'rightWidth' | 'rightWidthMode' | 'leftOpen' | 'leftWidth'>, viewportWidth: number) {
  const stacked = viewportWidth < 966;
  const watchlistWidth = preferences.leftOpen && viewportWidth > 1100 ? preferences.leftWidth + 6 : 0;
  // Manual layouts retain the persisted 250–500 range; only auto sizing needs 320.
  const min = preferences.rightWidthMode === 'auto' ? 320 : 250;
  const max = Math.max(min, Math.min(500, viewportWidth - watchlistWidth - 6 - 640));
  const automatic = viewportWidth <= 1440
    ? 336 + (Math.max(1280, viewportWidth) - 1280) * 24 / 160
    : 360 + (Math.min(1920, viewportWidth) - 1440) * 60 / 480;
  const requested = preferences.rightWidthMode === 'auto' ? automatic : preferences.rightWidth;
  return { rightWidth: Math.round(Math.min(max, Math.max(min, requested))), min, max, stacked };
}
export function mergeVisualGrid(previous: VisualGrid | null, next: VisualGrid): VisualGrid {
  if (next.replace || !previous || previous.grid_id !== next.grid_id || previous.generation !== next.generation) return next;
  const columns = new Map(previous.columns.map(column => [column.id, column]));
  for (const column of next.columns) columns.set(column.id, column);
  const start = BigInt(next.start_ns), end = BigInt(next.end_ns);
  return { ...next, columns: [...columns.values()].filter(column => BigInt(column.end_ns) > start && BigInt(column.time_ns) < end)
    .sort((a, b) => BigInt(a.time_ns) < BigInt(b.time_ns) ? -1 : BigInt(a.time_ns) > BigInt(b.time_ns) ? 1 : 0).slice(-600) };
}
