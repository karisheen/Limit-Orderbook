// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useEffect, useLayoutEffect } from "react";
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
import { GridWorkspace } from "../../grid/GridWorkspace";
import { NativeControls, NativeChartArea, NativeTools } from "../../integration/NativePanels";

export default function Terminal() {
  return (
    <MarketSessionProvider>
      <SessionTerminal />
    </MarketSessionProvider>
  );
}

function SessionTerminal() {
  useMarketData();

  const theme = useUIStore((s) => s.theme);

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

  return (
    <div className="terminal">
      <GlobalTooltip />
      <TopBar />
      <NativeControls />
      <MarketStatsBar />

      <GridWorkspace renderPanel={id => {
        switch (id) {
          case "chart": return <NativeChartArea footprint={<FootprintChart />} />;
          case "orderBook": return <OrderBookPanel />;
          case "obi": return <OBIPanel />;
          case "timeSales": return <TimeAndSales />;
          case "cvd": return <CVDPanel />;
          case "volumeZ": return <VolumeZScorePanel />;
          case "volatility": return <VolatilityPanel />;
          case "depthOfMarket": return <DepthOfMarket />;
        }
      }} />

      <NativeTools />
      <StatusBar />
    </div>
  );
}
