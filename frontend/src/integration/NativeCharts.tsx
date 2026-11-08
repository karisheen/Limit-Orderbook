import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { ChartPane } from '../ChartPane';
import { PriceChart } from '../PriceChart';
import type { ChartCursor } from '../MarketCharts';
import { Freshness } from '../Freshness';
import { DepthLadder } from '../DepthLadder';
import { useMarketView } from '../useMarketView';
import { chartSettings, navigateChartTime, workspaceGeometry } from '../workspace';
import type { ChartTimeIntent, PaneId, WorkspacePreferences } from '../workspace';
import { PaneActions, ResizeHandle, Tape } from './NativeWidgets';
import { useNativeTerminal } from './NativeContext';
import { paneForView, toggleBookView, viewForPane } from './nativeWorkspace';
import type { MarketPane } from './nativeWorkspace';

const marketPanes = [['price', 'Price'], ['heatmap', 'Liquidity'], ['flow', 'Flow'], ['dom', 'Book'], ['tape', 'Tape']] as const;
const chartViews = [['footprint', 'Footprint'], ['price', 'Price action'], ['orderflow', 'Liquidity & flow'], ['book', 'Native book & tape']] as const;

export function NativeChartArea({ footprint }: { footprint: ReactNode }) {
  const { terminal, preferences: p, updatePreferences: update, workspace: { setPreferences }, instrument: selected, connected, usable, fence } = useNativeTerminal();
  const { frame } = terminal;
  const workspaceView = p.nativeView ?? 'footprint';
  const [maximized, setMaximized] = useState<MarketPane | null>(null);
  const nativeView = maximized ? viewForPane(maximized) : workspaceView;
  const [narrowPane, setNarrowPane] = useState<MarketPane>(() => paneForView(nativeView));
  const [interactionPane, setInteractionPane] = useState<MarketPane>(() => paneForView(nativeView));
  const [clockScopeRevision, setClockScopeRevision] = useState(0);
  const shell = useRef<HTMLDivElement | null>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  const maximizeReturn = useRef<HTMLElement | null>(null);
  const pendingFocus = useRef<HTMLElement | null>(null);
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const resized = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', resized);
    return () => window.removeEventListener('resize', resized);
  }, []);
  const geometry = workspaceGeometry(p, viewportWidth);
  const narrow = viewportWidth <= 965;
  const primaryPane = paneForView(nativeView);
  const selectedPane = paneForView(nativeView, maximized ?? narrowPane);
  const bookOpen = nativeView === 'book' || p.rightOpen;
  const paneVisible = useCallback((pane: MarketPane) => {
    if (nativeView === 'footprint') return false;
    if (maximized) return maximized === pane;
    if (narrow) return selectedPane === pane;
    if (nativeView === 'book') return pane === 'dom' || pane === 'tape';
    if (pane === 'dom' || pane === 'tape') return p.rightOpen;
    return nativeView === 'price' ? pane === 'price' : pane !== 'price';
  }, [nativeView, maximized, narrow, selectedPane, p.rightOpen]);
  const [cursor, setCursor] = useState<{ fence: string; value: ChartCursor | null } | null>(null);
  const [domCursor, setDomCursor] = useState<{ fence: string; price: string | null } | null>(null);
  const crosshair = cursor?.fence === fence ? cursor.value : null;
  const setCrosshair = useCallback((value: ChartCursor | null) => setCursor({ fence, value }), [fence]);
  useEffect(() => {
    const clear = () => { setCursor(null); setDomCursor(null); };
    window.addEventListener('terminal-clear-cursor', clear);
    return () => window.removeEventListener('terminal-clear-cursor', clear);
  }, []);
  useEffect(() => {
    setMaximized(null);
    setNarrowPane(previous => paneForView(workspaceView, previous));
    setInteractionPane(previous => paneForView(workspaceView, previous));
  }, [workspaceView, p.preset]);
  const updateSecondary = useCallback((value: Partial<WorkspacePreferences>) => setPreferences(previous => ({
    ...previous, secondary: chartSettings({ ...previous.secondary, ...value }),
  })), [setPreferences]);
  const resetClockScope = useCallback(() => {
    setCursor(null); setDomCursor(null);
    setClockScopeRevision(previous => previous + 1);
    window.dispatchEvent(new Event('terminal-clear-cursor'));
    setPreferences(previous => ({ ...previous, range: null, followSpanNs: null, follow: true,
      secondary: { ...previous.secondary, range: null, followSpanNs: null, follow: true } }));
  }, [setPreferences]);
  const changeChartTime = (pane: 'heatmap' | 'flow', intent: ChartTimeIntent) => {
    setPreferences(previous => navigateChartTime(previous, pane, intent));
  };
  const toggleTimeLink = (pane: 'heatmap' | 'flow', intent: ChartTimeIntent | null) => {
    setPreferences(previous => {
      const next = { ...previous, rangeLinked: !previous.rangeLinked };
      return next.rangeLinked && intent ? navigateChartTime(next, pane, intent) : next;
    });
  };
  const views = useMarketView({ frame, connected: usable, preferences: p, recenter: 0, visualization: terminal.visualization, ladder: terminal.ladder, chartEnabled: false, domEnabled: paneVisible('dom'), clockScopeRevision });
  useLayoutEffect(() => {
    const visible = (element: HTMLElement | null): element is HTMLElement => !!element?.isConnected && element.getClientRects().length > 0;
    const pane = maximized ?? (narrow ? selectedPane : paneVisible(interactionPane) ? interactionPane : primaryPane);
    if (pane !== interactionPane) setInteractionPane(pane);
    const requested = pendingFocus.current;
    pendingFocus.current = null;
    if (visible(requested)) { requested.focus(); return; }
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body && visible(active)) return;
    if (!lastFocus.current && !requested) return;
    const container = shell.current?.querySelector<HTMLElement>(`[data-interaction-pane="${pane}"]`);
    container?.querySelector<HTMLElement>('button, [tabindex="0"], summary')?.focus();
  }, [fence, maximized, p.leftOpen, p.rightOpen, p.bottomOpen, p.preset, p.desktopView, interactionPane, narrow, selectedPane, primaryPane, paneVisible]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || nativeView === 'footprint') return;
      const target = event.target;
      if (event.key === 'Escape') {
        const disclosure = target instanceof Element ? target.closest<HTMLDetailsElement>('details[open]') : null;
        if (disclosure) {
          event.preventDefault();
          event.stopPropagation();
          disclosure.open = false;
          disclosure.querySelector<HTMLElement>(':scope > summary')?.focus();
          return;
        }
        const close = [...(shell.current?.querySelectorAll<HTMLButtonElement>('.chart-pane-settings button, .m4-chart-inspector button, .tape-inspector button') ?? [])]
          .reverse().find(button => button.getClientRects().length > 0 && /^Close /.test(button.getAttribute('aria-label') ?? ''));
        if (close) { event.preventDefault(); close.click(); return; }
        const open = [...(shell.current?.querySelectorAll<HTMLDetailsElement>('.tape-settings[open], .dom-settings[open]') ?? [])].reverse()
          .find(details => details.getClientRects().length > 0);
        if (open) { event.preventDefault(); open.open = false; open.querySelector<HTMLElement>(':scope > summary')?.focus(); return; }
        event.preventDefault();
        if (maximized) { pendingFocus.current = maximizeReturn.current; setMaximized(null); }
        else { setCursor(null); setDomCursor(null); window.dispatchEvent(new Event('terminal-clear-cursor')); }
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || target instanceof Element && target.closest('input,select,textarea,button,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return;
      if (event.key.toLowerCase() === 'd') setPreferences(previous => ({ ...previous, domFrozen: !previous.domFrozen }));
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [setPreferences, maximized, nativeView]);
  const toggleMaximize = (pane: MarketPane) => {
    if (narrow) setNarrowPane(pane);
    if (maximized === pane) pendingFocus.current = maximizeReturn.current;
    else maximizeReturn.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setMaximized(previous => previous === pane ? null : pane);
  };
  const selectPane = (pane: MarketPane) => {
    setNarrowPane(pane);
    setInteractionPane(pane);
    setMaximized(null);
    update({ nativeView: viewForPane(pane), ...(pane === 'dom' || pane === 'tape' ? { rightOpen: true } : { desktopView: pane === 'price' ? 'price' : 'orderflow' }) });
  };
  const selectView = (view: NonNullable<WorkspacePreferences['nativeView']>) => {
    setMaximized(null);
    const pane = paneForView(view);
    setNarrowPane(pane);
    setInteractionPane(pane);
    update({ nativeView: view, ...(view === 'price' || view === 'orderflow' ? { desktopView: view } : {}), ...(view === 'book' ? { rightOpen: true } : {}) });
  };
  const toggleBook = () => {
    const next = toggleBookView(nativeView, bookOpen, narrow, p.desktopView);
    const pane = paneForView(next.nativeView ?? nativeView);
    setMaximized(null);
    setNarrowPane(pane);
    setInteractionPane(pane);
    update(next);
  };
  const toggleLink = (pane: PaneId) => { setCrosshair(null); update({ links: { ...p.links, [pane]: !p.links[pane] } }); };
  const linkPrice = (price: string | null) => {
    if (!p.links.dom) { setDomCursor({ fence, price }); return; }
    const reference = views.dom.data?.asof;
    setCrosshair(price !== null && reference ? { time_ns: reference.receive_mono_ns, price } : null);
  };
  const chartProps = { frame, instrument: selected, connected, visualization: terminal.visualization, ladder: terminal.ladder,
    acknowledgeVisualization: terminal.acknowledgeVisualization, crosshair, onCrosshair: setCrosshair, onClockScopeChange: resetClockScope, clockScopeRevision };
  const style = { '--chart-primary-size': `${p.chartSplit}fr`, '--chart-secondary-size': `${100 - p.chartSplit}fr`, '--tape-split': `${p.tapeSplit}%`, '--right-width': `${geometry.rightWidth}px` } as CSSProperties;
  return <section ref={shell} className="native-chart-area" data-native-view={nativeView} style={style}
    onFocusCapture={event => {
      lastFocus.current = event.target as HTMLElement;
      const pane = (event.target as HTMLElement).closest<HTMLElement>('[data-interaction-pane]')?.dataset.interactionPane;
      if (pane) setInteractionPane(pane as MarketPane);
    }}
    onPointerDownCapture={event => {
      const pane = (event.target as HTMLElement).closest<HTMLElement>('[data-interaction-pane]')?.dataset.interactionPane;
      if (pane) setInteractionPane(pane as MarketPane);
    }}>
    <nav className="native-chart-tabs native-surface" role="tablist" aria-label="Chart workspace">
      {chartViews.map(([id, name], index) => <button key={id} id={`native-view-${id}`} role="tab" tabIndex={nativeView === id ? 0 : -1} aria-selected={nativeView === id} aria-controls={id === 'footprint' ? 'native-footprint' : 'native-chart-content'} onClick={() => selectView(id)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? chartViews.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + chartViews.length) % chartViews.length;
          const view = chartViews[next]![0]; selectView(view); document.getElementById(`native-view-${view}`)?.focus();
        }}>{name}</button>)}
      {nativeView !== 'footprint' && <button aria-pressed={narrow ? nativeView === 'book' : bookOpen} onClick={toggleBook}>DOM / tape</button>}
    </nav>
    <div id="native-footprint" className="native-footprint" role="tabpanel" aria-labelledby="native-view-footprint" hidden={nativeView !== 'footprint'}>{footprint}</div>
    <div id="native-chart-content" className="native-chart-content native-surface" role="tabpanel" aria-labelledby={`native-view-${nativeView}`} hidden={nativeView === 'footprint'}>
    <nav className="market-pane-tabs" role="tablist" aria-label="Market panes" hidden={!narrow}>
      {marketPanes.map(([id, name], index) => <button key={id} id={`market-tab-${id}`} role="tab" tabIndex={selectedPane === id ? 0 : -1} aria-selected={selectedPane === id} aria-controls={`market-panel-${id}`} onClick={() => selectPane(id)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? marketPanes.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + marketPanes.length) % marketPanes.length;
          const pane = marketPanes[next]![0];
          selectPane(pane);
          document.getElementById(`market-tab-${pane}`)?.focus();
        }}>{name}</button>)}
    </nav>
      <div className="native-chart-workspace" data-testid="workspace" data-maximized={maximized ?? ''}>
      <section id="market-panel-price" className="price-workspace" role="tabpanel" aria-labelledby={narrow ? 'market-tab-price' : 'native-view-price'} data-interaction-pane="price" data-interaction-active={interactionPane === 'price'} hidden={!paneVisible('price')}>
        <PriceChart frame={frame} instrument={selected} connected={usable} active={paneVisible('price')} interval={p.candleInterval} onIntervalChange={candleInterval => update({ candleInterval })} maximized={maximized === 'price'} onMaximize={() => toggleMaximize('price')} />
      </section>
      <section id="orderflow-workspace" className="chart-stack native-linked-charts" role={narrow ? undefined : 'tabpanel'} aria-labelledby={narrow ? undefined : 'native-view-orderflow'} aria-label="Market visualizations" hidden={!paneVisible('heatmap') && !paneVisible('flow')}>
        <div id="market-panel-heatmap" role={narrow ? 'tabpanel' : undefined} aria-labelledby={narrow ? 'market-tab-heatmap' : undefined} className="chart-slot primary-chart" data-interaction-pane="heatmap" data-interaction-active={interactionPane === 'heatmap'} hidden={!paneVisible('heatmap')}><ChartPane {...chartProps} key={`heatmap:${fence}`} id="heatmap" title="Liquidity" preferences={p} update={update} linked={p.links.heatmap} onLinked={() => toggleLink('heatmap')} timeLinked={p.rangeLinked} onTimeChange={intent => changeChartTime('heatmap', intent)} onTimeLinked={intent => toggleTimeLink('heatmap', intent)} interactionActive={interactionPane === 'heatmap'} maximized={maximized === 'heatmap'} onMaximize={() => toggleMaximize('heatmap')} active={paneVisible('heatmap')} /></div>
        <div className="chart-splitter"><ResizeHandle axis="vertical" proportional name="Chart split" value={p.chartSplit} min={30} max={70} onChange={chartSplit => update({ chartSplit })} /></div>
        <div id="market-panel-flow" role={narrow ? 'tabpanel' : undefined} aria-labelledby={narrow ? 'market-tab-flow' : undefined} className="chart-slot secondary-chart" data-interaction-pane="flow" data-interaction-active={interactionPane === 'flow'} hidden={!paneVisible('flow')}><ChartPane {...chartProps} key={`flow:${fence}`} id="flow" title="Order flow" preferences={{ ...p, ...p.secondary }} update={updateSecondary} linked={p.links.flow} onLinked={() => toggleLink('flow')} timeLinked={p.rangeLinked} onTimeChange={intent => changeChartTime('flow', intent)} onTimeLinked={intent => toggleTimeLink('flow', intent)} interactionActive={interactionPane === 'flow'} maximized={maximized === 'flow'} onMaximize={() => toggleMaximize('flow')} active={paneVisible('flow')} /></div>
      </section>
      <div className="right-resizer" hidden={!p.rightOpen}><ResizeHandle axis="horizontal" reverse name="DOM width" value={geometry.rightWidth} min={geometry.min} max={geometry.max} onChange={rightWidth => { if (rightWidth !== geometry.rightWidth) update({ rightWidth, rightWidthMode: 'manual' }); }} /></div><aside className="book-tape native-book-tape" hidden={!paneVisible('dom') && !paneVisible('tape')}>
        <section id="market-panel-dom" role={narrow ? 'tabpanel' : undefined} aria-labelledby={narrow ? 'market-tab-dom' : undefined} className="dom-pane" data-pane="dom" data-interaction-pane="dom" data-interaction-active={interactionPane === 'dom'} hidden={!paneVisible('dom')}>
          <DepthLadder key={`${fence}:${clockScopeRevision}`} extra={<PaneActions pane="DOM" linked={p.links.dom} onLinked={() => toggleLink('dom')} maximized={maximized === 'dom'} onMaximize={() => toggleMaximize('dom')} />} data={usable ? views.dom.data : null} loading={views.dom.loading} error={!usable ? 'Stream evidence unavailable' : views.dom.error} frozen={p.domFrozen} viewPaused={false} rows={p.domRows} onRows={domRows => update({ domRows })} onFreeze={() => update({ domFrozen: !p.domFrozen })} crosshair={p.links.dom ? crosshair?.price ?? null : domCursor?.fence === fence ? domCursor.price : null} onPrice={linkPrice} priceStep={p.domPriceStep} onPriceStep={domPriceStep => update({ domPriceStep })} unit={p.domUnit} onUnit={domUnit => update({ domUnit })} cumulative={p.domCumulative} onCumulative={domCumulative => update({ domCumulative })} sourceMode={frame?.source.mode ?? 'demo'} />
          <Freshness frame={frame} reference={usable ? views.dom.data?.asof ?? null : null} connected={connected} label="Book" frozen={p.domFrozen} />
        </section>
        <div className="tape-splitter"><ResizeHandle axis="vertical" proportional name="DOM and tape split" value={p.tapeSplit} min={30} max={75} onChange={tapeSplit => update({ tapeSplit })} /></div>
        <section id="market-panel-tape" role={narrow ? 'tabpanel' : undefined} aria-labelledby={narrow ? 'market-tab-tape' : undefined} className="tape-pane" data-pane="tape" data-interaction-pane="tape" data-interaction-active={interactionPane === 'tape'} hidden={!paneVisible('tape')}><Tape key={`${fence}:${clockScopeRevision}`} frame={frame} connected={connected} instrument={selected} preferences={p} update={update} crosshair={crosshair} onCrosshair={setCrosshair} maximized={maximized === 'tape'} onMaximize={() => toggleMaximize('tape')} /></section>
      </aside>
      </div>
    </div>
  </section>;
}
