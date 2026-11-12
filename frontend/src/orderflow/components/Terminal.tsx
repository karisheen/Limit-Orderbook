// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import React, { useEffect, useLayoutEffect } from "react";
import { useMarketData } from "@/hooks/useMarketData";
import { MarketSessionProvider, useSessionStore } from "@/lib/session/SessionContext";
import { useUIStore } from "@/lib/store/uiStore";
import { applyTheme } from "@/lib/theme";
import TopBar from "@/components/TopBar";
import MarketStatsBar from "@/components/MarketStatsBar";
import FootprintChart from "@/components/FootprintChart";
import OrderBookPanel from "@/components/OrderBook";
import OBIPanel from "@/components/OBIPanel";
import TimeAndSales from "@/components/TimeAndSales";
import DepthOfMarket from "@/components/DepthOfMarket";
import CVDPanel from "@/components/CVDPanel";
import VolumeZScorePanel from "@/components/VolumeZScorePanel";
import VolatilityPanel from "@/components/VolatilityPanel";
import GlobalTooltip from "@/components/GlobalTooltip";
import StatusBar from "@/components/StatusBar";
import type { PanelVisibility } from "@/lib/types/market";
import { NativeControls, NativeChartArea, NativeTools } from "../../integration/NativePanels";
import { useNativeTerminal } from "../../integration/NativeContext";

// ─── Terminal shell ──────────────────────────────────────────────
//  ┌ top bar ─────────────────────────────────────────────┐
//  ├ KPI strip ───────────────────────────────────────────┤
//  │ footprint + session profile        │ order book       │
//  │                                    │ book imbalance   │
//  ├ studies: CVD · Vol Z · RV · Depth ─┤ time & sales     │
//  └ status bar ──────────────────────────────────────────┘

const STUDIES: { key: keyof PanelVisibility; Component: React.ComponentType }[] = [
  { key: "cvd", Component: CVDPanel },
  { key: "volumeZ", Component: VolumeZScorePanel },
  { key: "volatility", Component: VolatilityPanel },
  { key: "depthOfMarket", Component: DepthOfMarket },
];

export default function Terminal() {
  return (
    <MarketSessionProvider>
      <SessionTerminal />
    </MarketSessionProvider>
  );
}

function SessionTerminal() {
  useMarketData();

  const panels = useUIStore((s) => s.panels);
  const { preferences } = useNativeTerminal();
  const theme = useUIStore((s) => s.theme);
  const [sidebarWidth, setSidebarWidth] = React.useState(320);
  const [dockHeight, setDockHeight] = React.useState(236);
  const [obiHeight, setObiHeight] = React.useState(160);
  const [bookShare, setBookShare] = React.useState(0.56);
  const sidebarRef = React.useRef<HTMLElement>(null);

  useEffect(() => {
    void useUIStore.persist.rehydrate();
  }, []);

  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // The value-area % is a UI preference but the profile is computed in the session.
  const valueAreaPct = useUIStore((s) => s.indicators.valueAreaPct);
  const setValueAreaPct = useSessionStore((s) => s.setValueAreaPct);
  useEffect(() => setValueAreaPct(valueAreaPct), [valueAreaPct, setValueAreaPct]);

  const studies = STUDIES.filter((study) => panels[study.key]);
  const nativeInspection = preferences.nativeView === "book";
  const showOrderBook = panels.orderBook && !nativeInspection;
  const showTimeSales = panels.timeSales && !nativeInspection;
  const showSidebar = showOrderBook || panels.obi || showTimeSales;

  return (
    <div className="terminal">
      <GlobalTooltip />
      <TopBar />
      <NativeControls />
      <MarketStatsBar />

      <div className="workspace">
        <div className="workspace-main">
          <div className="chart-area">
            <NativeChartArea footprint={<FootprintChart />} />
          </div>
          {studies.length > 0 && (
            <div className="studies-dock" style={{ height: dockHeight }}>
              <ResizeHandle direction="top" onResize={(d) => setDockHeight((h) => clamp(h - d, 180, 560))} />
              {studies.map(({ key, Component }) => (
                <div key={key} className="study-slot">
                  <Component />
                </div>
              ))}
            </div>
          )}
        </div>

        {showSidebar && (
          <aside className="sidebar" style={{ width: sidebarWidth }} ref={sidebarRef}>
            <ResizeHandle direction="left" onResize={(d) => setSidebarWidth((w) => clamp(w - d, 260, 520))} />
            {showOrderBook && (
              <div className="sidebar-slot" style={{ flex: showTimeSales ? `${bookShare} 1 0` : "1 1 0" }}>
                <OrderBookPanel />
                {showTimeSales && (
                  <ResizeHandle
                    direction="bottom"
                    onResize={(d) => {
                      const total = sidebarRef.current?.clientHeight ?? 800;
                      setBookShare((s) => clamp(s + d / total, 0.25, 0.8));
                    }}
                  />
                )}
              </div>
            )}
            {panels.obi && (
              <div className="sidebar-slot" style={{ flex: `0 0 ${obiHeight}px` }}>
                <OBIPanel />
                <ResizeHandle direction="bottom" onResize={(d) => setObiHeight((h) => clamp(h + d, 140, 340))} />
              </div>
            )}
            {showTimeSales && (
              <div className="sidebar-slot" style={{ flex: showOrderBook ? `${1 - bookShare} 1 0` : "1 1 0" }}>
                <TimeAndSales />
              </div>
            )}
          </aside>
        )}
      </div>

      <NativeTools />
      <StatusBar />
    </div>
  );
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function ResizeHandle({
  direction,
  onResize,
}: {
  direction: "left" | "right" | "top" | "bottom";
  onResize: (delta: number) => void;
}) {
  const start = React.useRef(0);
  const cleanup = React.useRef<(() => void) | null>(null);
  useEffect(() => () => cleanup.current?.(), []);
  const vertical = direction === "top" || direction === "bottom";
  return (
    <span
      className={`resize-handle resize-handle-${direction}`}
      role="separator"
      aria-orientation={vertical ? "horizontal" : "vertical"}
      aria-label={vertical ? "Resize panel height" : "Resize sidebar width"}
      tabIndex={0}
      onKeyDown={(event) => {
        const decrement = vertical ? "ArrowUp" : "ArrowLeft";
        const increment = vertical ? "ArrowDown" : "ArrowRight";
        if (event.key !== decrement && event.key !== increment) return;
        event.preventDefault();
        onResize((event.key === decrement ? -1 : 1) * (event.shiftKey ? 40 : 10));
      }}
      onPointerDown={(event) => {
        event.preventDefault();
        cleanup.current?.();
        start.current = vertical ? event.clientY : event.clientX;
        const move = (moveEvent: PointerEvent) => {
          const current = vertical ? moveEvent.clientY : moveEvent.clientX;
          const delta = current - start.current;
          if (delta !== 0) {
            onResize(delta);
            start.current = current;
          }
        };
        const stop = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", stop);
          window.removeEventListener("pointercancel", stop);
          window.removeEventListener("blur", stop);
          cleanup.current = null;
          document.body.classList.remove("is-resizing", "is-resizing-row");
        };
        document.body.classList.add("is-resizing");
        if (vertical) document.body.classList.add("is-resizing-row");
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", stop, { once: true });
        window.addEventListener("pointercancel", stop, { once: true });
        window.addEventListener("blur", stop, { once: true });
        cleanup.current = stop;
      }}
    />
  );
}
