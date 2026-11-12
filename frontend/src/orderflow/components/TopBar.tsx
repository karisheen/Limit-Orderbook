// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useMemo } from "react";
import { useSessionStore } from "@/lib/session/SessionContext";
import type { PanelVisibility, Timeframe, ToolProfile, VenueSelection } from "@/lib/types/market";
import { THEME_OPTIONS, type Theme } from "@/lib/theme";
import { displayCohort, displayMarketType, displaySymbol, displayVenue, sameNativeMarket } from "@/lib/config/instruments";
import { useNativeTerminal } from "../../integration/NativeContext";
import type { Instrument } from "../../api";
import { venueLabel } from "../../format";
import { selectableInstrument } from "../../instrument-metadata";
import { INTERVAL_GROUPS } from "../../candle-model";
import { AssetLogo, VenueLogo } from "@/components/AssetLogo";
import { Picker, type PickerOption } from "@/components/ui/Picker";
import { useUIStore } from "@/lib/store/uiStore";
import { Icon } from "@/components/ui/Icons";
import { Menu, MenuCheckbox, MenuSection, Segmented, Select } from "@/components/ui/Primitives";

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: "1m", label: "1m" }, { value: "5m", label: "5m" }, { value: "15m", label: "15m" },
  { value: "30m", label: "30m" }, { value: "1h", label: "1H" }, { value: "4h", label: "4H" },
];
const PROFILES: { value: ToolProfile; label: string; title: string }[] = [
  { value: "order-flow", label: "Order Flow", title: "Footprint, tape, CVD and volume z-score" },
  { value: "liquidity", label: "Liquidity", title: "Order book, depth, imbalance and traded-volume heatmap" },
  { value: "volatility", label: "Volatility", title: "Realized volatility, volume z-score and tape" },
];
const PANEL_ITEMS: { key: keyof PanelVisibility; label: string; hint: string; group: "Chart" | "Studies" | "Sidebar" }[] = [
  { key: "volumeProfile", label: "Session profile", hint: "Volume by price beside the footprint", group: "Chart" },
  { key: "heatmap", label: "Traded-volume heatmap", hint: "Executed volume by price", group: "Chart" },
  { key: "cvd", label: "Cumulative delta", hint: "CVD with price overlay", group: "Studies" },
  { key: "volumeZ", label: "Volume z-score", hint: "Unusual participation", group: "Studies" },
  { key: "volatility", label: "Realized volatility", hint: "RV, ATR, Parkinson", group: "Studies" },
  { key: "depthOfMarket", label: "Market depth", hint: "Cumulative liquidity curve", group: "Studies" },
  { key: "orderBook", label: "Order book", hint: "Price ladder", group: "Sidebar" },
  { key: "obi", label: "Book imbalance", hint: "OBI gauge and history", group: "Sidebar" },
  { key: "timeSales", label: "Time & sales", hint: "Executed prints", group: "Sidebar" },
];

export default function TopBar() {
  const { terminal, connected, instruments, instrument, selectInstrument, preferences, updatePreferences } = useNativeTerminal();
  const symbol = useSessionStore((s) => s.symbol);
  const venue = useSessionStore((s) => s.venue);
  const marketType = useSessionStore((s) => s.marketType);
  const timeframe = preferences.candleInterval;
  const setSymbol = useSessionStore((s) => s.setSymbol);
  const setVenue = useSessionStore((s) => s.setVenue);
  const setSelectedVenues = useSessionStore((s) => s.setSelectedVenues);
  const setMarketType = useSessionStore((s) => s.setMarketType);
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const panels = useUIStore((s) => s.panels);
  const toolProfile = useUIStore((s) => s.toolProfile);
  const setToolProfile = useUIStore((s) => s.setToolProfile);
  const setPanelVisible = useUIStore((s) => s.setPanelVisible);

  // A candidate can be subscribed before it has any ready market evidence.
  const canSelect = connected && !terminal.busy && terminal.frame?.status !== "seeking";
  const live = terminal.frame?.source.mode === "live";
  const candidates = useMemo(
    () => instruments.filter((item) => !live || selectableInstrument(item)),
    [instruments, live],
  );
  const available = useMemo(
    () => instruments.filter((item) => displayMarketType(item) === marketType),
    [instruments, marketType],
  );
  const selectable = useMemo(
    () => candidates.filter((item) => displayMarketType(item) === marketType),
    [candidates, marketType],
  );
  const instrumentOptions = useMemo<PickerOption<string>[]>(() => {
    // Native discovery is much larger than a curated watchlist. One symbol index
    // avoids rescanning the entire catalogue for every option on each frame.
    const enabledSymbols = new Set(selectable.map(displaySymbol));
    const symbols = new Map(available.map((item) => [displaySymbol(item), item]));
    return [...symbols].map(([value, item]) => ({
      value,
      label: value,
      sub: item.base.ticker,
      keywords: `${item.base.ticker} ${item.quote.ticker}`,
      icon: <AssetLogo symbol={value} size={22} />,
      disabled: !canSelect || !enabledSymbols.has(value),
    }));
  }, [available, selectable, canSelect]);
  const aggregateValue: VenueSelection = marketType === "perpetual" ? "all-perps" : "all-spot";
  const primary = instrument && displayMarketType(instrument) === marketType
    ? instrument : available.find((item) => displaySymbol(item) === symbol);
  const cohort = primary ? available.filter((item) => sameNativeMarket(item, primary)) : [];
  const selectableCohort = primary ? selectable.filter((item) => sameNativeMarket(item, primary)) : [];
  const aggregateCohort = primary ? displayCohort(selectable, primary) : [];
  const venues = [...new Set(cohort.map(displayVenue))];
  const aggregate = venue === "all-perps" || venue === "all-spot";

  const activate = (next: Instrument, nextVenue: VenueSelection) => {
    if (!canSelect || !candidates.some((item) => item.instrument_id === next.instrument_id)) return;
    setMarketType(displayMarketType(next));
    setSymbol(displaySymbol(next));
    setVenue(nextVenue);
    if (nextVenue === "all-perps" || nextVenue === "all-spot") {
      setSelectedVenues([...new Set(displayCohort(candidates, next).map(displayVenue))]);
    }
    selectInstrument(next.instrument_id);
  };

  const chooseSymbol = (next: string) => {
    const matches = selectable.filter((item) => displaySymbol(item) === next);
    const selected = matches.find((item) => displayVenue(item) === (instrument ? displayVenue(instrument) : venue)) ?? matches[0];
    if (selected) activate(selected, aggregate ? aggregateValue : displayVenue(selected));
  };

  const venuePickerOptions: PickerOption<VenueSelection>[] = [
    {
      value: aggregateValue,
      label: venueLabel(aggregateValue),
      sub: `Indicative aggregate · ${aggregateCohort.length} venues · eligible quotes only`,
      icon: <VenueLogo venue={aggregateValue} size={20} />,
      disabled: !canSelect || !primary || !candidates.some((item) => item.instrument_id === primary.instrument_id),
    },
    ...venues.map((value) => ({
      value,
      label: venueLabel(value),
      sub: marketType === "perpetual" ? "Perpetual" : "Spot",
      icon: <VenueLogo venue={value} size={20} />,
      disabled: !canSelect || !selectableCohort.some((item) => displayVenue(item) === value),
    })),
  ];

  const chooseVenue = (next: VenueSelection) => {
    if (next === aggregateValue) {
      if (primary) activate(primary, next);
      return;
    }
    const matches = selectableCohort.filter((item) => displayVenue(item) === next);
    const selected = matches.find((item) => item.instrument_id === primary?.instrument_id)
      ?? matches.find((item) => item.quote.asset_id === primary?.quote.asset_id)
      ?? matches.find((item) => displaySymbol(item) === symbol) ?? matches[0];
    if (selected) activate(selected, next);
  };

  const chooseMarket = (next: "spot" | "perpetual") => {
    if (next === marketType) return;
    const matches = candidates.filter((item) => displayMarketType(item) === next);
    const sameBase = matches.filter((item) => item.base.asset_id === instrument?.base.asset_id);
    const selected = sameBase.find((item) => displayVenue(item) === instrument?.venue && item.quote.asset_id === instrument?.quote.asset_id)
      ?? sameBase.find((item) => displayVenue(item) === instrument?.venue) ?? sameBase[0] ?? matches[0];
    if (selected) activate(selected, aggregate ? (next === "spot" ? "all-spot" : "all-perps") : displayVenue(selected));
  };

  return (
    <header className="topbar">
      <span className="brand" aria-label="Limit Orderbook">Limit Orderbook</span>

      <div className="topbar-group">
        <Segmented
          value={marketType}
          onChange={chooseMarket}
          options={[
            { value: "perpetual", label: "Perp", title: "Crypto perpetual futures", disabled: !canSelect || !candidates.some((item) => displayMarketType(item) === "perpetual") },
            { value: "spot", label: "Spot", title: "Crypto spot", disabled: !canSelect || !candidates.some((item) => displayMarketType(item) === "spot") },
          ]}
          ariaLabel="Market"
        />
      </div>

      <div className="topbar-group">
        <Picker
          searchable
          className="instrument-picker"
          value={symbol}
          options={instrumentOptions}
          onChange={chooseSymbol}
          ariaLabel="Instrument"
          width={300}
        />
          <Picker
            value={venue}
            fallbackLabel={venueLabel(venue)}
            options={venuePickerOptions}
            onChange={chooseVenue}
            ariaLabel="Venue"
            width={260}
          />
      </div>

      <div className="topbar-group">
        <Segmented accent value={timeframe} onChange={(candleInterval) => updatePreferences({ candleInterval })} options={TIMEFRAMES} ariaLabel="Candle interval" />
        <Select className="interval-select" value={timeframe} onChange={(event) => updatePreferences({ candleInterval: event.target.value })} aria-label="All candle intervals">
          {INTERVAL_GROUPS.map((group) => <optgroup key={group.label} label={group.label}>
            {group.values.map((interval) => <option key={interval} value={interval}>{interval}</option>)}
          </optgroup>)}
        </Select>
      </div>

      <div className="topbar-spacer" />

      <div className="topbar-group">
        <Segmented value={toolProfile} onChange={setToolProfile} options={PROFILES} ariaLabel="Workspace" />
        <Menu ariaLabel="Panels" label={<><Icon.Layout /><span className="hide-compact">Panels</span></>}>
          {(["Chart", "Studies", "Sidebar"] as const).map((group) => (
            <MenuSection key={group} title={group}>
              {PANEL_ITEMS.filter((item) => item.group === group).map((item) => (
                <MenuCheckbox
                  key={item.key}
                  checked={panels[item.key]}
                  onChange={(checked) => setPanelVisible(item.key, checked)}
                  label={item.label}
                  hint={preferences.nativeView === "book" && (item.key === "orderBook" || item.key === "timeSales")
                    ? `${item.hint} · restored on chart views; native inspection is active`
                    : item.hint}
                />
              ))}
            </MenuSection>
          ))}
        </Menu>
        <Select className="theme-select" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label="Theme">
          {THEME_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </Select>
      </div>

    </header>
  );
}
