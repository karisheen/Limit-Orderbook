import type { VisualGrid } from './api';
import { chartTimeTicks } from './chart-time';
import type { ChartTimeZone } from './chart-time';
import { CHART_LIMITS, COLORS, boundedSize, intensity, liquidityTileKey, plotArea, plotNumber, priceCellIndex, priceRowProjector, saturation, sideColor, timeAt, timeFraction } from './market-charts-model';
import type { ChartCursor, ChartMode, ChartRowViewport, PlotArea } from './market-charts-model';

type Column = VisualGrid['columns'][number];
type Surface = { canvas: HTMLCanvasElement; context: CanvasRenderingContext2D };
export type RenderReport = { duration: number; tiles: number; allocated: number; dirty: number; trades: number; omittedTrades: number; omittedCells: number; pixelWidth: number; pixelHeight: number; compactFootprint: boolean };
export type RenderOptions = { mode: ChartMode; contrast: number; showTrades: boolean; showQuotes: boolean; timeZone: ChartTimeZone; rowViewport: ChartRowViewport; quoteUnit?: string };

const FOOTPRINT_LANES = [
  [0, 'sell_base', COLORS.sell, 'SELL'],
  [1, 'buy_base', COLORS.buy, 'BUY'],
  [2, 'unknown_base', COLORS.unknown, '?'],
] as const;

/** One immutable two-side column tile. At most 640 × 2 × 256 RGBA pixels. */
export class MarketRenderer {
  private readonly tiles = new Map<string, HTMLCanvasElement>();
  private readonly base: Surface;
  private readonly overlay: Surface;
  private readonly cursor: Surface;
  private width = 1;
  private height = 1;
  private geometry = '';
  private drawn = new Map<string, string>();
  private identity = '';
  private size = boundedSize(1, 1, 1);
  private allocated = 0;

  constructor(base: HTMLCanvasElement, overlay: HTMLCanvasElement, cursor: HTMLCanvasElement) {
    const surface = (canvas: HTMLCanvasElement): Surface => {
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) throw new Error('Canvas2D is unavailable in this browser. Exact chart data remains available below.');
      return { canvas, context };
    };
    this.base = surface(base);
    // Transparent overlays must not erase the underlying liquidity layer.
    const transparent = (canvas: HTMLCanvasElement): Surface => {
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas2D overlay is unavailable.');
      return { canvas, context };
    };
    this.overlay = transparent(overlay);
    this.cursor = transparent(cursor);
  }

  resize(width: number, height: number, dpr: number) {
    const next = boundedSize(width, height, dpr);
    if (this.size.width === next.width && this.size.height === next.height && this.size.ratio === next.ratio) return false;
    this.width = next.width;
    this.height = next.height;
    this.size = next;
    for (const { canvas, context } of [this.base, this.overlay, this.cursor]) {
      canvas.width = next.pixelWidth;
      canvas.height = next.pixelHeight;
      context.setTransform(next.ratio, 0, 0, next.ratio, 0, 0);
      context.imageSmoothingEnabled = false;
    }
    this.geometry = '';
    return true;
  }

  private tile(view: VisualGrid, column: Column, contrast: number): HTMLCanvasElement {
    const key = liquidityTileKey(view, column, contrast);
    const cached = this.tiles.get(key);
    if (cached) {
      this.tiles.delete(key);
      this.tiles.set(key, cached);
      return cached;
    }
    const canvas = document.createElement('canvas');
    canvas.width = 2;
    canvas.height = Math.max(1, Math.min(CHART_LIMITS.rows, view.rows));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Unable to allocate liquidity tile.');
    context.fillStyle = COLORS.background;
    context.fillRect(0, 0, 2, canvas.height);
    for (let row = 0; row < canvas.height; row++) {
      const cell = column.cells[row];
      const bid = column.eligible ? intensity(cell?.[0] ?? null, contrast) : null;
      const ask = column.eligible ? intensity(cell?.[1] ?? null, contrast) : null;
      const occupiedSide = bid != null && bid > 0 && ask === 0 ? 0 : ask != null && ask > 0 && bid === 0 ? 1 : null;
      for (let side = 0; side < 2; side++) {
        const sourceSide = occupiedSide ?? side;
        const value = sourceSide === 0 ? bid : ask;
        context.globalAlpha = value == null ? 1 : value * value;
        context.fillStyle = value == null ? COLORS.missing : sourceSide === 0 ? COLORS.bid : COLORS.ask;
        context.fillRect(side, canvas.height - row - 1, 1, 1);
      }
    }
    context.globalAlpha = 1;
    this.tiles.set(key, canvas);
    this.allocated++;
    while (this.tiles.size > CHART_LIMITS.tiles) {
      const oldest = this.tiles.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      const evicted = this.tiles.get(oldest);
      if (evicted) { evicted.width = 0; evicted.height = 0; }
      this.tiles.delete(oldest);
    }
    return canvas;
  }

  render(view: VisualGrid, options: RenderOptions): RenderReport {
    const started = performance.now();
    const identity = `${view.generation}:${view.grid_id}`;
    if (identity !== this.identity) {
      this.clearTiles();
      this.identity = identity;
      this.geometry = '';
    }
    const area = plotArea(this.width, this.height);
    const x = (time: string) => area.x + timeFraction(time, view.start_ns, view.end_ns) * area.width;
    const priceRow = priceRowProjector(view.price_min, view.price_step);
    const { first, count } = options.rowViewport;
    const y = (price: string) => area.y + area.height - (priceRow(price) - first) / count * area.height;
    const columns = view.columns.slice(-CHART_LIMITS.columns);
    const ctx = this.base.context;
    const geometry = JSON.stringify([this.width, this.height, this.size.ratio, view.grid_id, view.start_ns, view.end_ns, view.price_min, view.rows, first, count, options.mode, saturation(options.contrast), options.timeZone, options.quoteUnit]);
    let dirty = 0;
    let compactFootprint = false;
    if (options.mode === 'heatmap') {
      const current = new Map(columns.map(column => [column.id, liquidityTileKey(view, column, options.contrast)]));
      const reset = geometry !== this.geometry || [...this.drawn.keys()].some(id => !current.has(id));
      if (reset) {
        ctx.fillStyle = COLORS.background;
        ctx.fillRect(0, 0, this.width, this.height);
        this.missing(ctx, area.x, area.y, area.width, area.height);
      }
      ctx.save();
      ctx.beginPath(); ctx.rect(area.x, area.y, area.width, area.height); ctx.clip();
      for (const column of columns) {
        if (!reset && this.drawn.get(column.id) === current.get(column.id)) continue;
        const left = x(column.time_ns);
        const width = x(column.end_ns) - left;
        if (width <= 0 || left >= area.x + area.width || left + width <= area.x) continue;
        ctx.clearRect(left, area.y, width, area.height);
        const tile = this.tile(view, column, options.contrast);
        ctx.drawImage(tile, 0, view.rows - first - count, 2, count, left, area.y, width, area.height);
        if (!column.eligible) this.missing(ctx, left, area.y, width, area.height);
        else {
          // Unknown side depth is hatched; observed zero keeps the unmarked canvas.
          // Coalesce adjacent unknown rows so bounded tiles do not create a path per cell.
          const height = area.height / count;
          for (let side = 0; side < 2; side++) {
            let start = -1;
            for (let row = first; row <= first + count; row++) {
              const unknown = row < first + count && column.cells[row]?.[side] == null;
              if (unknown && start < 0) start = row;
              if (!unknown && start >= 0) {
                this.missing(ctx, left + side * width / 2, area.y + area.height - (row - first) * height, width / 2, (row - start) * height);
                start = -1;
              }
            }
          }
        }
        dirty++;
      }
      ctx.restore();
      this.drawn = current;
    } else {
      ctx.fillStyle = COLORS.background;
      ctx.fillRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.beginPath(); ctx.rect(area.x, area.y, area.width, area.height); ctx.clip();
      if (options.mode !== 'profile' && options.mode !== 'footprint') {
        this.missing(ctx, area.x, area.y, area.width, area.height);
        for (const column of columns) {
          const available = options.mode === 'candles' ? column.candle?.eligible : options.mode === 'cvd' || options.mode === 'ofi' ? column[options.mode]?.eligibility.eligible : column.eligible;
          if (available) { ctx.fillStyle = COLORS.background; ctx.fillRect(x(column.time_ns), area.y, x(column.end_ns) - x(column.time_ns), area.height); }
        }
      }
      if (options.mode === 'candles') {
        for (const column of columns) {
          const candle = column.candle;
          if (!candle?.eligible) continue;
          const middle = (x(column.time_ns) + x(column.end_ns)) / 2;
          const width = Math.max(1, Math.min(18, (x(column.end_ns) - x(column.time_ns)) * 0.65));
          ctx.strokeStyle = ctx.fillStyle = priceRow(candle.close) >= priceRow(candle.open) ? COLORS.buy : COLORS.sell;
          ctx.beginPath(); ctx.moveTo(middle, y(candle.high)); ctx.lineTo(middle, y(candle.low)); ctx.stroke();
          ctx.fillRect(middle - width / 2, Math.min(y(candle.open), y(candle.close)), width, Math.max(1, Math.abs(y(candle.close) - y(candle.open))));
        }
      } else if (options.mode === 'footprint') {
        const rowHeight = area.height / count;
        const bucketWidth = Number(BigInt(view.bucket_ns)) / Number(BigInt(view.end_ns) - BigInt(view.start_ns)) * area.width;
        const cells = view.footprints.slice(-CHART_LIMITS.footprints).filter(cell => {
          const row = priceCellIndex(view, cell.price);
          return row >= first && row < first + count && x(cell.time_ns) < area.x + area.width && x(cell.time_ns) + bucketWidth > area.x;
        });
        const unknown = cells.some(cell => plotNumber(cell.unknown_base) !== 0);
        const lanes = unknown ? 3 : 2;
        const candleSpace = 12;
        ctx.font = '11px ui-monospace, monospace';
        // Edge buckets keep their native time bounds, but only their visible intersection can hold text.
        compactFootprint = rowHeight < 18 || cells.some(cell => {
          const bucketLeft = x(cell.time_ns);
          const visibleWidth = Math.min(area.x + area.width, bucketLeft + bucketWidth) - Math.max(area.x, bucketLeft);
          const laneWidth = (visibleWidth - candleSpace - 8) / lanes;
          return laneWidth < 34 || FOOTPRINT_LANES.some(([side, field, , label]) =>
            side < lanes && (ctx.measureText(cell[field]).width > laneWidth - 6 || ctx.measureText(label).width > laneWidth - 6));
        });
        ctx.strokeStyle = '#242a2f'; ctx.lineWidth = 1;
        ctx.beginPath();
        for (let row = 0; row <= count; row++) {
          const py = area.y + row * rowHeight;
          ctx.moveTo(area.x, py); ctx.lineTo(area.x + area.width, py);
        }
        ctx.stroke();
        for (const column of columns) {
          const left = x(column.time_ns);
          const candle = column.candle;
          if (candle?.eligible) {
            const middle = left + 5;
            ctx.strokeStyle = ctx.fillStyle = priceRow(candle.close) >= priceRow(candle.open) ? COLORS.buy : COLORS.sell;
            ctx.beginPath(); ctx.moveTo(middle, y(candle.high)); ctx.lineTo(middle, y(candle.low)); ctx.stroke();
            ctx.fillRect(middle - 2, Math.min(y(candle.open), y(candle.close)), 4, Math.max(1, Math.abs(y(candle.close) - y(candle.open))));
          }
        }
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        for (const cell of cells) {
          const bucketLeft = x(cell.time_ns);
          const visibleLeft = Math.max(area.x, bucketLeft);
          const visibleRight = Math.min(area.x + area.width, bucketLeft + bucketWidth);
          const left = visibleLeft + (compactFootprint ? 0 : candleSpace);
          const laneWidth = (visibleRight - left - (compactFootprint ? 0 : 8)) / lanes;
          const top = area.y + area.height - (priceCellIndex(view, cell.price) - first + 1) * rowHeight;
          for (const [side, field, color] of FOOTPRINT_LANES) {
            if (side >= lanes) break;
            const value = cell[field];
            const strength = intensity(value, options.contrast) ?? 0;
            ctx.fillStyle = color;
            ctx.globalAlpha = plotNumber(value) === 0 ? 0 : 0.08 + strength * 0.3;
            ctx.fillRect(left + side * laneWidth, top + 1, Math.max(0, laneWidth - Math.min(1, laneWidth / 2)), Math.max(1, rowHeight - 2));
            ctx.globalAlpha = 1;
            if (!compactFootprint) {
              ctx.fillStyle = plotNumber(value) === 0 ? '#68747d' : color;
              ctx.fillText(value, left + (side + 1) * laneWidth - 4, top + rowHeight / 2);
            }
          }
        }
        ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
      } else if (options.mode === 'profile') {
        const rowHeight = area.height / count;
        for (const cell of view.profile.slice(0, CHART_LIMITS.rows)) {
          [cell.buy_base, cell.sell_base, cell.unknown_base].forEach((value, side) => {
            ctx.fillStyle = [COLORS.buy, COLORS.sell, COLORS.unknown][side] ?? COLORS.unknown;
            ctx.fillRect(area.x, y(cell.price) - rowHeight + side * rowHeight / 3, (intensity(value, options.contrast) ?? 0) * area.width, Math.max(1, rowHeight / 3 - 0.5));
          });
        }
      } else {
        this.metric(ctx, columns, options.mode, x, area);
      }
      ctx.restore();
      dirty = columns.length;
      this.drawn.clear();
    }
    this.geometry = geometry;
    this.axes(view, options, x, area);
    const trades = this.overlays(view, options, columns, x, y, area);
    if (options.mode === 'footprint' && !compactFootprint) {
      const width = Number(BigInt(view.bucket_ns)) / Number(BigInt(view.end_ns) - BigInt(view.start_ns)) * area.width;
      const unknown = view.footprints.slice(-CHART_LIMITS.footprints).some(cell => {
        const row = priceCellIndex(view, cell.price);
        return row >= first && row < first + count && x(cell.time_ns) < area.x + area.width && x(cell.time_ns) + width > area.x && plotNumber(cell.unknown_base) !== 0;
      });
      const lanes = unknown ? 3 : 2;
      const plotRight = area.x + area.width;
      ctx.font = '11px ui-monospace, monospace'; ctx.textAlign = 'center';
      for (const column of columns) {
        const bucketLeft = x(column.time_ns);
        const left = Math.max(area.x, bucketLeft);
        const right = Math.min(plotRight, bucketLeft + width);
        const laneWidth = (right - left - 20) / lanes;
        if (laneWidth < 34) continue;
        for (const [side, , color, label] of FOOTPRINT_LANES) {
          if (side >= lanes) break;
          const px = left + 12 + (side + 0.5) * laneWidth;
          const labelWidth = ctx.measureText(label).width;
          if (labelWidth > laneWidth - 6 || px - labelWidth / 2 < left || px + labelWidth / 2 > right) continue;
          ctx.fillStyle = color;
          ctx.fillText(label, px, 11);
        }
      }
      ctx.textAlign = 'left';
    }
    return { duration: performance.now() - started, tiles: this.tiles.size, allocated: this.allocated, dirty, trades, omittedTrades: Math.max(0, view.trades.length - CHART_LIMITS.trades), omittedCells: options.mode === 'footprint' ? Math.max(0, view.footprints.length - CHART_LIMITS.footprints) : 0, pixelWidth: this.size.pixelWidth, pixelHeight: this.size.pixelHeight, compactFootprint };
  }

  private missing(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number) {
    ctx.save();
    ctx.beginPath(); ctx.rect(x, y, width, height); ctx.clip();
    ctx.fillStyle = COLORS.missing; ctx.fillRect(x, y, width, height);
    ctx.strokeStyle = '#30363c'; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let offset = -height; offset < width; offset += 18) { ctx.moveTo(x + offset, y + height); ctx.lineTo(x + offset + height, y); }
    ctx.stroke(); ctx.restore();
  }

  private metric(ctx: CanvasRenderingContext2D, columns: Column[], mode: 'cvd' | 'ofi', x: (time: string) => number, area: PlotArea) {
    let bound = 0;
    for (const column of columns) { const metric = column[mode]; if (metric?.eligibility.eligible) bound = Math.max(bound, Math.abs(plotNumber(metric.value) ?? 0)); }
    const extent = bound || 1;
    const y = (value: number) => area.y + area.height / 2 - value / extent * (area.height / 2 - 14);
    ctx.strokeStyle = COLORS.grid; ctx.beginPath(); ctx.moveTo(area.x, y(0)); ctx.lineTo(area.x + area.width, y(0)); ctx.stroke();
    ctx.fillStyle = COLORS.text; ctx.font = '10px ui-monospace, monospace'; ctx.fillText(`+${extent.toPrecision(4)}`, area.x + 3, area.y + 12); ctx.fillText(`−${extent.toPrecision(4)}`, area.x + 3, area.y + area.height - 3);
    ctx.strokeStyle = mode === 'cvd' ? COLORS.buy : '#a6b7c4'; ctx.lineWidth = 1.5;
    let previous: Column | undefined;
    ctx.beginPath();
    for (const column of columns) {
      const metric = column[mode];
      const value = metric?.eligibility.eligible ? plotNumber(metric.value) : null;
      if (value == null) { previous = undefined; continue; }
      const px = (x(column.time_ns) + x(column.end_ns)) / 2;
      if (!previous || previous.segment_id !== column.segment_id || previous.end_ns !== column.time_ns) ctx.moveTo(px, y(value));
      else ctx.lineTo(px, y(value));
      ctx.fillStyle = ctx.strokeStyle; ctx.fillRect(px - 1, y(value) - 1, 2, 2);
      previous = column;
    }
    ctx.stroke();
  }

  private axes(view: VisualGrid, options: RenderOptions, x: (time: string) => number, area: PlotArea) {
    const { mode, contrast, timeZone, rowViewport: { first, count }, quoteUnit } = options;
    const ctx = this.base.context;
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(area.x + area.width, 0, this.width - area.x - area.width, this.height);
    ctx.fillRect(0, area.y + area.height, this.width, this.height - area.y - area.height);
    ctx.font = '11px ui-monospace, monospace'; ctx.fillStyle = COLORS.text;
    if (mode !== 'cvd' && mode !== 'ofi') {
      if (quoteUnit) ctx.fillText(quoteUnit, area.x + area.width + 7, 11);
      const stride = Math.max(1, Math.ceil(count / Math.max(2, Math.floor(area.height / (mode === 'footprint' ? 20 : 42)))));
      for (let index = first; index < Math.min(first + count, CHART_LIMITS.rows); index += stride) {
        const price = view.prices[index];
        if (price == null) continue;
        const py = area.y + area.height - (index - first + 0.5) / count * area.height;
        ctx.fillText(price, area.x + area.width + 7, py + 3);
      }
    }
    if (mode === 'profile') {
      for (let index = 0; index <= 4; index++) {
        const time = timeAt(index / 4, view.start_ns, view.end_ns);
        ctx.textAlign = index === 4 ? 'right' : 'left';
        ctx.fillText(`${(saturation(contrast) * index / 4).toPrecision(3)} ${view.quantity_unit}`, x(time), area.y + area.height + 17);
      }
    } else {
      ctx.textAlign = 'center';
      for (const tick of chartTimeTicks(view, area.width, timeZone)) {
        const px = x(tick.time_ns);
        ctx.font = `${tick.kind === 'time' ? '' : 'bold '}11px ui-monospace, monospace`;
        ctx.fillStyle = tick.kind === 'discontinuity' ? '#efbe72' : COLORS.text;
        const halfLabel = tick.label.length * 7 / 2;
        const labelX = Math.max(area.x + halfLabel, Math.min(area.x + area.width - halfLabel, px));
        ctx.fillText(tick.label, labelX, area.y + area.height + 17);
        ctx.fillRect(px, area.y + area.height, 1, tick.kind === 'time' ? 3 : 6);
      }
    }
    ctx.textAlign = 'left';
  }

  private overlays(view: VisualGrid, options: RenderOptions, columns: Column[], x: (time: string) => number, y: (price: string) => number, area: PlotArea): number {
    const ctx = this.overlay.context;
    ctx.clearRect(0, 0, this.width, this.height);
    if (options.mode === 'cvd' || options.mode === 'ofi' || options.mode === 'profile') return 0;
    ctx.save(); ctx.beginPath(); ctx.rect(area.x, area.y, area.width, area.height); ctx.clip();
    if (options.showQuotes) {
      for (const side of ['best_bid', 'best_ask'] as const) {
        ctx.strokeStyle = side === 'best_bid' ? COLORS.bid : COLORS.ask; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
        let previous: Column | undefined;
        ctx.beginPath();
        for (const column of columns) {
          const price = column[side];
          if (!column.eligible || price == null) { previous = undefined; continue; }
          if (!previous || previous.segment_id !== column.segment_id || previous.end_ns !== column.time_ns) ctx.moveTo(x(column.time_ns), y(price));
          else ctx.lineTo(x(column.time_ns), y(price));
          ctx.lineTo(x(column.end_ns), y(price));
          previous = column;
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);
    }
    let count = 0;
    if (options.showTrades) {
      for (const trade of view.trades.slice(-CHART_LIMITS.trades)) {
        const px = x(trade.time_ns); const py = y(trade.price);
        if (px < area.x || px > area.x + area.width || py < area.y || py > area.y + area.height) continue;
        const radius = 2 + 7 * Math.sqrt(intensity(trade.base_quantity, options.contrast) ?? 0);
        ctx.strokeStyle = sideColor(trade.taker_side); ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = 1;
        ctx.setLineDash(trade.is_subscription_history ? [2, 2] : []);
        ctx.beginPath();
        if (trade.taker_side !== 'buy' && trade.taker_side !== 'sell') {
          ctx.moveTo(px, py - radius); ctx.lineTo(px + radius, py); ctx.lineTo(px, py + radius); ctx.lineTo(px - radius, py); ctx.closePath();
        } else ctx.arc(px, py, radius, 0, Math.PI * 2);
        if (!trade.is_subscription_history && trade.base_quantity != null) { ctx.globalAlpha = 0.5; ctx.fill(); ctx.globalAlpha = 1; }
        ctx.stroke(); count++;
      }
    }
    ctx.restore();
    if (options.mode === 'heatmap') {
      const markers: { price: string; label: string; color: string; execution: boolean }[] = [];
      const newestColumn = columns.at(-1);
      const now = BigInt(view.now_ns), start = BigInt(view.start_ns), end = BigInt(view.end_ns);
      if (options.showQuotes && view.clock_id && newestColumn?.eligible && newestColumn.asof?.clock_id === view.clock_id &&
        BigInt(newestColumn.time_ns) < end && BigInt(newestColumn.end_ns) > start &&
        BigInt(newestColumn.asof.receive_mono_ns) <= now && BigInt(newestColumn.asof.receive_mono_ns) <= end) {
        if (newestColumn.best_bid !== null) markers.push({ price: newestColumn.best_bid, label: 'B', color: COLORS.bid, execution: false });
        if (newestColumn.best_ask !== null) markers.push({ price: newestColumn.best_ask, label: 'A', color: COLORS.ask, execution: false });
      }
      let latest: VisualGrid['trades'][number] | undefined;
      if (options.showTrades) {
        for (const trade of view.trades.slice(-CHART_LIMITS.trades)) {
          const time = BigInt(trade.time_ns), received = BigInt(trade.asof.receive_mono_ns);
          if (trade.is_subscription_history || !view.clock_id || trade.asof.clock_id !== view.clock_id || time < start || time >= end || time > now || received > now || received > end || received < start) continue;
          if (!latest || time > BigInt(latest.time_ns)) latest = trade;
        }
      }
      if (latest) markers.push({ price: latest.price, label: 'T', color: sideColor(latest.taker_side), execution: true });
      const visible = markers.filter(marker => y(marker.price) >= area.y && y(marker.price) <= area.y + area.height).sort((a, b) => y(a.price) - y(b.price));
      ctx.font = '11px ui-monospace, monospace';
      let previousY = area.y - 16;
      for (const [index, marker] of visible.entries()) {
        const py = y(marker.price);
        const labelY = Math.max(previousY + 16, Math.min(py, area.y + area.height - (visible.length - index - 1) * 16));
        previousY = labelY;
        ctx.fillStyle = ctx.strokeStyle = marker.color;
        ctx.setLineDash(marker.execution ? [] : [2, 2]);
        ctx.beginPath(); ctx.moveTo(area.x + area.width - 16, py); ctx.lineTo(area.x + area.width, py); ctx.lineTo(area.x + area.width + 4, labelY); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = COLORS.background; ctx.fillRect(area.x + area.width + 5, labelY - 8, 94, 15);
        ctx.fillStyle = marker.color; ctx.fillText(`${marker.label} ${marker.price}`, area.x + area.width + 7, labelY + 4);
      }
    }
    return count;
  }

  drawCursor(view: VisualGrid | null, cursor: ChartCursor | null, mode: ChartMode, rowViewport: ChartRowViewport) {
    const ctx = this.cursor.context;
    ctx.clearRect(0, 0, this.width, this.height);
    if (!view || !cursor) return;
    const area = plotArea(this.width, this.height);
    const px = area.x + timeFraction(cursor.time_ns, view.start_ns, view.end_ns) * area.width;
    if (mode !== 'profile' && (px < area.x || px > area.x + area.width)) return;
    ctx.save(); ctx.beginPath(); ctx.rect(area.x, area.y, area.width, area.height); ctx.clip();
    ctx.strokeStyle = '#e2e9f3'; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
    ctx.beginPath();
    if (mode !== 'profile') { ctx.moveTo(px, area.y); ctx.lineTo(px, area.y + area.height); }
    const row = cursor.price == null ? -1 : priceCellIndex(view, cursor.price);
    if (row >= rowViewport.first && row < rowViewport.first + rowViewport.count && cursor.price != null && mode !== 'cvd' && mode !== 'ofi') {
      const coordinate = mode === 'footprint' ? row + 0.5 : priceRowProjector(view.price_min, view.price_step)(cursor.price);
      const py = area.y + area.height - (coordinate - rowViewport.first) / rowViewport.count * area.height;
      ctx.moveTo(area.x, py); ctx.lineTo(area.x + area.width, py);
    }
    ctx.stroke(); ctx.restore();
  }

  clear() {
    for (const { canvas, context } of [this.base, this.overlay, this.cursor]) {
      // Reset the context stack as well as pixels after an interrupted draw.
      canvas.width = this.size.pixelWidth;
      context.setTransform(this.size.ratio, 0, 0, this.size.ratio, 0, 0);
      context.imageSmoothingEnabled = false;
    }
    this.geometry = ''; this.drawn.clear(); this.clearTiles();
  }
  private clearTiles() {
    for (const tile of this.tiles.values()) { tile.width = 0; tile.height = 0; }
    this.tiles.clear();
  }
  dispose() {
    this.clearTiles(); this.drawn.clear();
    for (const { canvas } of [this.base, this.overlay, this.cursor]) { canvas.width = 0; canvas.height = 0; }
  }
}
