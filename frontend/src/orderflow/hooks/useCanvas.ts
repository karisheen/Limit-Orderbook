// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.


import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { canvasThemeRevision, mountCanvasPresentation, type CanvasPresentation } from "@/lib/session/canvasScheduler";
import { THEMES, type Theme, type ThemeTokens } from "@/lib/theme";

// ─── Palette ─────────────────────────────────────────────────────
export interface Palette {
  bgBase: string;
  bgSurface: string;
  bgPanel: string;
  bgPanelAlt: string;
  bgActive: string;
  border: string;
  borderMuted: string;
  text: string;
  textSecondary: string;
  textMuted: string;
  buy: string;
  sell: string;
  neutral: string;
  accent: string;
  poc: string;
  vwap: string;
  warn: string;
  grid: string;
  onAccent: string;
  light: boolean;
}

let cachedTheme: string | null = null;
let cachedPalette: Palette | null = null;
let cachedRevision = -1;

export function readPalette(): Palette {
  const root = document.documentElement;
  const theme = root.getAttribute("data-theme") ?? "dark-dull";
  const revision = canvasThemeRevision();
  if (cachedPalette && cachedTheme === theme && cachedRevision === revision) return cachedPalette;
  const cs = getComputedStyle(root);
  const fallback = THEMES[theme as Theme] ?? THEMES.dark;
  const v = (name: keyof ThemeTokens) => cs.getPropertyValue(name).trim() || fallback[name];
  cachedPalette = {
    bgBase: v("--bg-base"),
    bgSurface: v("--bg-surface"),
    // Existing canvas consumers use bgPanel for plot fills and axis wipes.
    bgPanel: v("--bg-chart"),
    bgPanelAlt: v("--bg-panel-alt"),
    bgActive: v("--bg-active"),
    border: v("--border"),
    borderMuted: v("--border-muted"),
    text: v("--text-primary"),
    textSecondary: v("--text-secondary"),
    textMuted: v("--text-muted"),
    buy: v("--buy"),
    sell: v("--sell"),
    neutral: v("--neutral"),
    accent: v("--highlight"),
    poc: v("--poc"),
    vwap: v("--vwap"),
    warn: v("--warn"),
    grid: v("--grid"),
    onAccent: v("--on-accent"),
    light: theme === "light" || theme === "light-dull",
  };
  cachedTheme = theme;
  cachedRevision = revision;
  return cachedPalette;
}

/** Apply alpha to a #rrggbb color (other formats pass through unchanged). */
export function withAlpha(color: string, alpha: number): string {
  if (!color.startsWith("#") || color.length < 7) return color;
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

export const MONO_FONT = 'ui-monospace, "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace';
export const UI_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif';

export function monoFont(size: number, weight = 400): string {
  return `${weight} ${size}px ${MONO_FONT}`;
}

// ─── useCanvas ───────────────────────────────────────────────────
// A device-pixel-ratio aware canvas that resizes with its container.
// `draw` receives CSS-pixel dimensions; redraws are coalesced per frame.

export type DrawFn = (ctx: CanvasRenderingContext2D, width: number, height: number, palette: Palette) => void;

export function useCanvas(draw: DrawFn) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const drawRef = useRef(draw);

  useLayoutEffect(() => {
    drawRef.current = draw;
  }, [draw]);

  const presentationRef = useRef<CanvasPresentation | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const presentation = mountCanvasPresentation(container, canvas, (ctx, width, height) => {
      sizeRef.current = { width, height };
      drawRef.current(ctx, width, height, readPalette());
    });
    presentationRef.current = presentation;
    return () => {
      presentationRef.current = null;
      presentation.dispose();
    };
  }, []);

  useEffect(() => {
    presentationRef.current?.invalidate();
  }, [draw]);

  const redraw = useCallback(() => presentationRef.current?.invalidate(), []);

  return { containerRef, canvasRef, sizeRef, redraw };
}
