export const GRID_COLUMNS = 12;
export const GRID_ROWS = 48;
export const GRID_BASE_ROWS = 12;
export const GRID_MIN_ROW_HEIGHT = 24;
/** Size the desktop's fixed logical page, not its occupied extent: custom layouts may scroll. */
export function gridRowHeight(contentHeight: number, gap: number): number {
  return Math.max(GRID_MIN_ROW_HEIGHT, (contentHeight - gap * (GRID_BASE_ROWS - 1)) / GRID_BASE_ROWS);
}
export const PANEL_IDS = ['chart', 'orderBook', 'obi', 'timeSales', 'cvd', 'volumeZ', 'volatility', 'depthOfMarket'] as const;
export type GridPanelId = typeof PANEL_IDS[number];
export interface GridCell { id: GridPanelId; x: number; y: number; w: number; h: number }
export interface GridLayout { cells: GridCell[] }
export const PANEL_LABELS: Record<GridPanelId, string> = {
  chart: 'Chart', orderBook: 'Order book', obi: 'Book imbalance', timeSales: 'Time & sales',
  cvd: 'Cumulative delta', volumeZ: 'Volume Z-score', volatility: 'Realized volatility', depthOfMarket: 'Depth of market',
};
export const PANEL_MINIMUM: Record<GridPanelId, { w: number; h: number }> = {
  chart: { w: 6, h: 8 }, orderBook: { w: 3, h: 4 }, obi: { w: 3, h: 2 }, timeSales: { w: 3, h: 4 },
  cvd: { w: 3, h: 3 }, volumeZ: { w: 3, h: 3 }, volatility: { w: 3, h: 3 }, depthOfMarket: { w: 3, h: 3 },
};
const cell = (id: GridPanelId, x: number, y: number, w: number, h: number): GridCell => ({ id, x, y, w, h });
export const GRID_PRESETS = {
  balanced: { label: 'Order flow', view: 'footprint', cells: [cell('chart', 0, 0, 9, 8), cell('orderBook', 9, 0, 3, 4), cell('obi', 9, 4, 3, 2), cell('timeSales', 9, 6, 3, 5), cell('cvd', 0, 8, 4, 3), cell('volumeZ', 4, 8, 5, 3)] },
  price: { label: 'Price focus', view: 'price', cells: [cell('chart', 0, 0, 12, GRID_BASE_ROWS)] },
  liquidity: { label: 'Liquidity', view: 'orderflow', cells: [cell('chart', 0, 0, 9, 12), cell('orderBook', 9, 0, 3, 5), cell('obi', 9, 5, 3, 3), cell('depthOfMarket', 9, 8, 3, 4)] },
  volatility: { label: 'Volatility', view: 'price', cells: [cell('chart', 0, 0, 9, 8), cell('timeSales', 9, 0, 3, 11), cell('volumeZ', 0, 8, 4, 3), cell('volatility', 4, 8, 5, 3)] },
  execution: { label: 'Execution', view: 'book', cells: [cell('chart', 0, 0, 9, 10), cell('obi', 9, 0, 3, 4), cell('cvd', 9, 4, 3, 6)] },
} as const;
export type GridPresetId = keyof typeof GRID_PRESETS;
export function gridPreset(id: GridPresetId): GridLayout {
  return { cells: GRID_PRESETS[id].cells.map(item => ({ ...item })) };
}
export function overlaps(a: GridCell, b: GridCell): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}
export function gridReadingOrder(layout: GridLayout): GridCell[] {
  return [...layout.cells].sort((a, b) => a.y - b.y || a.x - b.x || PANEL_IDS.indexOf(a.id) - PANEL_IDS.indexOf(b.id));
}
function integer(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, Math.round(value))) : fallback;
}
function vacant(cells: GridCell[], candidate: GridCell): boolean {
  return !cells.some(other => other.id !== candidate.id && overlaps(candidate, other));
}
function firstVacancy(cells: GridCell[], candidate: GridCell): GridCell | null {
  for (let y = 0; y <= GRID_ROWS - candidate.h; y++) {
    for (let x = 0; x <= GRID_COLUMNS - candidate.w; x++) {
      const next = { ...candidate, x, y };
      if (vacant(cells, next)) return next;
    }
  }
  return null;
}
export function validateGrid(value: unknown): GridLayout {
  if (!value || typeof value !== 'object' || !('cells' in value) || !Array.isArray(value.cells)) return gridPreset('balanced');
  const cells: GridCell[] = [];
  // At most one instance of every known panel; malformed or foreign panels never mount.
  for (const item of value.cells.slice(0, 64)) {
    if (!item || typeof item !== 'object' || !PANEL_IDS.includes(item.id) || cells.some(other => other.id === item.id)) continue;
    const id = item.id as GridPanelId;
    const minimum = PANEL_MINIMUM[id];
    const w = integer(item.w, minimum.w, GRID_COLUMNS, minimum.w);
    const h = integer(item.h, minimum.h, GRID_ROWS, minimum.h);
    let candidate: GridCell | null = { id, w, h, x: integer(item.x, 0, GRID_COLUMNS - w, 0), y: integer(item.y, 0, GRID_ROWS - h, 0) };
    if (!vacant(cells, candidate)) candidate = firstVacancy(cells, candidate);
    if (candidate) cells.push(candidate);
  }
  return { cells: gridReadingOrder({ cells }) };
}
export type GridOperation =
  | { kind: 'move'; id: GridPanelId; dx: number; dy: number }
  | { kind: 'resize'; id: GridPanelId; dx: number; dy: number }
  | { kind: 'add'; id: GridPanelId }
  | { kind: 'remove'; id: GridPanelId };
export interface GridResult { layout: GridLayout; error: string | null }
export function changeGrid(layout: GridLayout, operation: GridOperation): GridResult {
  const existing = layout.cells.find(item => item.id === operation.id);
  if (operation.kind === 'remove') return { layout: { cells: layout.cells.filter(item => item.id !== operation.id) }, error: null };
  if (operation.kind === 'add') {
    if (existing) return { layout, error: 'This panel is already present.' };
    const next = firstVacancy(layout.cells, { id: operation.id, x: 0, y: 0, ...PANEL_MINIMUM[operation.id] });
    return next ? { layout: { cells: gridReadingOrder({ cells: [...layout.cells, next] }) }, error: null } : { layout, error: 'No room remains. Remove or shrink a panel first.' };
  }
  if (!existing || !Number.isInteger(operation.dx) || !Number.isInteger(operation.dy)) return { layout, error: 'Invalid panel operation.' };
  const minimum = PANEL_MINIMUM[existing.id];
  const next = operation.kind === 'move' ? { ...existing, x: existing.x + operation.dx, y: existing.y + operation.dy }
    : { ...existing, w: existing.w + operation.dx, h: existing.h + operation.dy };
  if (next.w < minimum.w || next.h < minimum.h || next.x < 0 || next.y < 0 || next.x + next.w > GRID_COLUMNS || next.y + next.h > GRID_ROWS) return { layout, error: 'Panel minimum size or workspace boundary reached.' };
  if (!vacant(layout.cells, next)) return { layout, error: 'Panels cannot overlap. Move into empty space first.' };
  return { layout: { cells: gridReadingOrder({ cells: layout.cells.map(item => item.id === next.id ? next : item) }) }, error: null };
}
/** Read legacy shell visibility once, before the old UI store drops shell preferences. */
export function migrateShellGrid(panels: unknown, rightWidth = 320, bottomHeight = 236): GridLayout {
  const base = gridPreset('balanced');
  if (panels && typeof panels === 'object') {
    const values = panels as Record<string, unknown>;
    base.cells = base.cells.filter(item => item.id === 'chart' || values[item.id] !== false);
    for (const id of PANEL_IDS) {
      if (id !== 'chart' && values[id] === true && !base.cells.some(item => item.id === id)) base.cells = changeGrid(base, { kind: 'add', id }).layout.cells;
    }
  }
  // Existing anonymous workspace widths/heights remain useful starting proportions.
  const sidebar = rightWidth >= 420 ? 4 : 3;
  const chartWidth = GRID_COLUMNS - sidebar;
  const dockRows = Math.max(3, Math.min(7, Math.round(bottomHeight / 64)));
  base.cells = base.cells.map(item => item.id === 'chart' ? { ...item, w: chartWidth }
    : ['orderBook', 'obi', 'timeSales'].includes(item.id) ? { ...item, x: chartWidth, w: sidebar }
      : { ...item, h: dockRows, w: Math.min(item.w, chartWidth - item.x) });
  return validateGrid(base);
}
