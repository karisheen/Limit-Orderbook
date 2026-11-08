import { useState } from 'react';
import type { CSSProperties } from 'react';
import { ProposalRiskPanel } from '../ProposalRiskPanel';
import { AlertsPanel, ContextPanel, ResearchPanel } from '../SignalPanels';
import { JournalPanel, PaperPanel } from '../PaperPanels';
import { AnalyticsPanel, CostsPanel, ResizeHandle } from './NativeWidgets';
import { useNativeTerminal } from './NativeContext';

const workflowTabs = [
  ['costs', 'Costs & proposed risk'], ['analytics', 'Core analytics'],
  ['alerts', 'Alerts'], ['context', 'Context'], ['paper', 'Paper'],
  ['journal', 'Journal'], ['research', 'Research'],
] as const;
type WorkflowTab = typeof workflowTabs[number][0];

export function NativeTools() {
  const { terminal, preferences: p, updatePreferences: update, instruments, usable, selectInstrument: selectContextInstrument } = useNativeTerminal();
  const { frame } = terminal;
  const [bottomTab, setBottomTab] = useState<WorkflowTab>('costs');
  const workflow = { frame, instruments, connected: usable, busy: terminal.busy, request: terminal.workflow };
  const openWorkflow = (tab: WorkflowTab) => { setBottomTab(tab); update({ bottomOpen: true }); };
  return <section className="native-tools native-surface" style={{ '--bottom-height': `${p.bottomHeight}px` } as CSSProperties}>
      <div className="workflow-dock">
        <div className="tabs research-tabs native-tools-tabs" role="tablist" aria-label="Research pane">{workflowTabs.map(([id, name], index) => <button key={id} id={`workflow-tab-${id}`} role="tab" tabIndex={bottomTab === id ? 0 : -1} aria-selected={p.bottomOpen && bottomTab === id} aria-controls={`workflow-panel-${id}`} onClick={() => openWorkflow(id)}
          onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? workflowTabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + workflowTabs.length) % workflowTabs.length;
            const nextTab = workflowTabs[next]!;
            openWorkflow(nextTab[0]);
            document.getElementById(`workflow-tab-${nextTab[0]}`)?.focus();
          }}>{name}</button>)}</div>
        {p.bottomOpen ? <button className="drawer-toggle" aria-label="Collapse workflow drawer" onClick={() => update({ bottomOpen: false })}>Collapse</button> : <span className="workflow-hint">Analysis &amp; paper tools · no execution</span>}
      </div>
      {p.bottomOpen && <><div className="bottom-resizer"><ResizeHandle axis="vertical" reverse name="Research pane height" value={p.bottomHeight} min={170} max={460} onChange={bottomHeight => update({ bottomHeight })} /></div><section className="research-pane native-tools-content">
        <div className="research-scroll">
          <div id="workflow-panel-costs" role="tabpanel" aria-labelledby="workflow-tab-costs" hidden={bottomTab !== 'costs'}><CostsPanel frame={frame} instruments={instruments} connected={usable} calculate={terminal.costs} /><ProposalRiskPanel frame={frame} connected={usable} calculate={terminal.proposalRisk} /></div>
          <div id="workflow-panel-analytics" role="tabpanel" aria-labelledby="workflow-tab-analytics" hidden={bottomTab !== 'analytics'}><AnalyticsPanel analytics={usable ? frame?.analytics ?? null : null} /></div>
          <div id="workflow-panel-alerts" role="tabpanel" aria-labelledby="workflow-tab-alerts" hidden={bottomTab !== 'alerts'}><AlertsPanel {...workflow} active={bottomTab === 'alerts'} /></div>
          <div id="workflow-panel-context" role="tabpanel" aria-labelledby="workflow-tab-context" hidden={bottomTab !== 'context'}><ContextPanel {...workflow} active={bottomTab === 'context'} selectInstrument={selectContextInstrument} /></div>
          <div id="workflow-panel-paper" role="tabpanel" aria-labelledby="workflow-tab-paper" hidden={bottomTab !== 'paper'}><PaperPanel {...workflow} active={bottomTab === 'paper'} resumeRun={terminal.resumePaper} /></div>
          <div id="workflow-panel-journal" role="tabpanel" aria-labelledby="workflow-tab-journal" hidden={bottomTab !== 'journal'}><JournalPanel {...workflow} active={bottomTab === 'journal'} /></div>
          <div id="workflow-panel-research" role="tabpanel" aria-labelledby="workflow-tab-research" hidden={bottomTab !== 'research'}><ResearchPanel {...workflow} active={bottomTab === 'research'} /></div>
        </div>
      </section></>}
  </section>;
}
