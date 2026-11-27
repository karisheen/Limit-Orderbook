// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5.
// Modified for Limit Orderbook: Vite integration, strict typing and local workspace.
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { applyTheme, type Theme } from "@/lib/theme";
import type { HeatmapSettings, PanelVisibility } from "@/lib/types/market";


export type FootprintMode = "bidask" | "delta" | "volume";

export interface IndicatorSettings {
  vwap: boolean;
  vwapBands: boolean; // ±1σ and ±2σ volume-weighted bands
  vwapColor: string | null; // null = theme colour
  sessionPoc: boolean;
  valueArea: boolean;
  valueAreaPct: number; // 0.6 | 0.7 | 0.8
  sessionHighLow: boolean;
  pocColor: string | null;
  imbalances: boolean;
  imbalanceRatio: number; // 2 | 2.5 | 3 | 4
  stackedImbalances: boolean;
  stackedLevels: number; // 2..5
  candlePoc: boolean;
  ohlcStrip: boolean;
  summaryRows: boolean;
  lastPrice: boolean;
  restingLiquidity: boolean;
  restingOpacity: number;
  restingCeiling: number;
  largePrints: boolean;
  largePrintMinimum: number;
}

export const DEFAULT_INDICATORS: IndicatorSettings = {
  vwap: true,
  vwapBands: false,
  vwapColor: null,
  sessionPoc: true,
  valueArea: true,
  valueAreaPct: 0.7,
  sessionHighLow: false,
  pocColor: null,
  imbalances: true,
  imbalanceRatio: 3,
  stackedImbalances: true,
  stackedLevels: 3,
  candlePoc: true,
  ohlcStrip: true,
  summaryRows: true,
  lastPrice: true,
  restingLiquidity: false,
  restingOpacity: 0.3,
  restingCeiling: 10,
  largePrints: false,
  largePrintMinimum: 1,
};

interface UIStore {
  panels: Pick<PanelVisibility, "volumeProfile" | "heatmap">;
  theme: Theme;
  heatmap: HeatmapSettings;
  footprintMode: FootprintMode;
  indicators: IndicatorSettings;
  setIndicator: <K extends keyof IndicatorSettings>(key: K, value: IndicatorSettings[K]) => void;
  resetIndicators: () => void;
  setPanelVisible: (panel: "volumeProfile" | "heatmap", visible: boolean) => void;
  setTheme: (theme: Theme) => void;
  setHeatmapVisible: (mode: keyof HeatmapSettings, visible: boolean) => void;
  setFootprintMode: (mode: FootprintMode) => void;
}

export const useUIStore = create<UIStore>()(
  persist(
    (set) => ({
      panels: { volumeProfile: true, heatmap: false },
      theme: "dark-dull",
      heatmap: { tradedVolume: false },
      footprintMode: "bidask",
      indicators: DEFAULT_INDICATORS,
      setIndicator: (key, value) => set((state) => ({ indicators: { ...state.indicators, [key]: value } })),
      resetIndicators: () => set({ indicators: DEFAULT_INDICATORS }),
      setPanelVisible: (panel, visible) =>
        set((state) => ({
          panels: { ...state.panels, [panel]: visible },
          heatmap: panel === "heatmap" ? { tradedVolume: visible } : state.heatmap,
        })),
      setTheme: (theme) => {
        applyTheme(theme);
        set({ theme });
      },
      setHeatmapVisible: (mode, visible) =>
        set((state) => ({
          heatmap: { ...state.heatmap, [mode]: visible },
          panels: { ...state.panels, heatmap: visible },
        })),
      setFootprintMode: (footprintMode) => set({ footprintMode }),
    }),
    {
      name: "limit-orderbook.orderflow.ui-preferences",
      version: 3,
      storage: createJSONStorage(() => localStorage),
      // Hydrated manually after mount to keep SSR markup stable.
      skipHydration: true,
      partialize: (state) => ({
        panels: state.panels,
        theme: state.theme,
        heatmap: state.heatmap,
        footprintMode: state.footprintMode,
        indicators: state.indicators,
      }),
      // Additive migration: preserve existing chart/theme/indicator settings.
      // Workspace owns shell layout and reads legacy panel visibility before hydration.
      migrate: (persisted) => (persisted ?? {}) as UIStore,
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<UIStore>;
        return {
          ...current,
          theme: saved.theme ?? current.theme,
          heatmap: { ...current.heatmap, ...(saved.heatmap ?? {}) },
          footprintMode: saved.footprintMode ?? current.footprintMode,
          panels: {
            volumeProfile: saved.panels?.volumeProfile ?? current.panels.volumeProfile,
            heatmap: saved.panels?.heatmap ?? current.panels.heatmap,
          },
          indicators: {
            ...DEFAULT_INDICATORS, ...(saved.indicators ?? {}),
            restingOpacity: Number.isFinite(saved.indicators?.restingOpacity) ? Math.max(0.05, Math.min(1, saved.indicators!.restingOpacity)) : DEFAULT_INDICATORS.restingOpacity,
            restingCeiling: Number.isFinite(saved.indicators?.restingCeiling) && saved.indicators!.restingCeiling > 0 ? saved.indicators!.restingCeiling : DEFAULT_INDICATORS.restingCeiling,
            largePrintMinimum: Number.isFinite(saved.indicators?.largePrintMinimum) && saved.indicators!.largePrintMinimum >= 0 ? saved.indicators!.largePrintMinimum : DEFAULT_INDICATORS.largePrintMinimum,
          },
        };
      },
    }
  )
);
