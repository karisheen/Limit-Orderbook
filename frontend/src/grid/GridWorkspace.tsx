import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import { useNativeTerminal } from '../integration/NativeContext';
import { MAX_LAYOUT_NAME } from '../workspace';
import { changeGrid, GRID_COLUMNS, GRID_MIN_ROW_HEIGHT, GRID_PRESETS, gridPreset, gridReadingOrder, gridRowHeight, PANEL_IDS, PANEL_LABELS } from './layout';
import type { GridCell, GridOperation, GridPanelId, GridPresetId } from './layout';
import './grid.css';

interface Drag { id: GridPanelId; kind: 'move' | 'resize'; pointerId: number; target: HTMLButtonElement; startX: number; startY: number; dx: number; dy: number; column: number; row: number }
export function GridWorkspace({ renderPanel }: { renderPanel: (id: GridPanelId) => ReactNode }) {
  const { preferences, updatePreferences, workspace } = useNativeTerminal();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [savedName, setSavedName] = useState('');
  const [adding, setAdding] = useState<GridPanelId>('chart');
  const [notice, setNotice] = useState('');
  const [drag, setDrag] = useState<Drag | null>(null);
  const gesture = useRef<Drag | null>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 965px)').matches);
  const [buttonEditor, setButtonEditor] = useState<{ id: GridPanelId; kind: Drag['kind'] } | null>(null);
  const cancelDrag = () => {
    const owner = gesture.current;
    gesture.current = null;
    setDrag(null);
    if (owner?.target.hasPointerCapture(owner.pointerId)) owner.target.releasePointerCapture(owner.pointerId);
  };
  const surface = useRef<HTMLDivElement>(null);
  const savedLayouts = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 965px)');
    const update = () => {
      setNarrow(media.matches);
      const owner = gesture.current;
      gesture.current = null;
      setDrag(null);
      if (owner?.target.hasPointerCapture(owner.pointerId)) owner.target.releasePointerCapture(owner.pointerId);
    };
    media.addEventListener('change', update);
    return () => {
      media.removeEventListener('change', update);
      const owner = gesture.current;
      gesture.current = null;
      if (owner?.target.hasPointerCapture(owner.pointerId)) owner.target.releasePointerCapture(owner.pointerId);
    };
  }, []);
  useLayoutEffect(() => {
    const element = surface.current;
    if (!element) return;
    const styles = getComputedStyle(element);
    const gap = Number.parseFloat(styles.rowGap);
    const sizeRows = (contentHeight: number) => {
      element.style.setProperty('--grid-row-height', `${gridRowHeight(contentHeight, gap)}px`);
    };
    // Observe remaining space, including source-banner and toolbar expansion,
    // without changing persisted cells or refitting each drag to its new extent.
    sizeRows(element.clientHeight - Number.parseFloat(styles.paddingTop) - Number.parseFloat(styles.paddingBottom));
    const observer = new ResizeObserver(entries => {
      const entry = entries[0];
      if (entry) sizeRows(entry.contentRect.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const disclosure = savedLayouts.current;
    if (!disclosure) return;
    const summary = disclosure.querySelector<HTMLElement>(':scope > summary');
    // Dismiss only the disclosure; pending names and selections survive reopening.
    const dismissOutside = (event: Event) => {
      if (!disclosure.open || !(event.target instanceof Node) || disclosure.contains(event.target)) return;
      const restoreFocus = event.type === 'pointerdown' && disclosure.contains(document.activeElement);
      disclosure.open = false;
      if (restoreFocus) summary?.focus({ preventScroll: true });
    };
    const dismissWithEscape = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.key !== 'Escape' || !disclosure.open) return;
      event.preventDefault();
      event.stopPropagation();
      disclosure.open = false;
      summary?.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', dismissOutside, true);
    document.addEventListener('focusin', dismissOutside);
    disclosure.addEventListener('keydown', dismissWithEscape);
    return () => {
      document.removeEventListener('pointerdown', dismissOutside, true);
      document.removeEventListener('focusin', dismissOutside);
      disclosure.removeEventListener('keydown', dismissWithEscape);
    };
  }, []);
  const layout = preferences.grid;
  const chartPresent = layout.cells.some(item => item.id === 'chart');
  const nativeInspection = chartPresent && preferences.nativeView === 'book';
  const hiddenByInspection = (id: GridPanelId) => nativeInspection && (id === 'orderBook' || id === 'timeSales');
  const available = PANEL_IDS.filter(id => !layout.cells.some(item => item.id === id));
  const addId = available.includes(adding) ? adding : available[0];
  const selectedName = preferences.layouts.some(item => item.name === savedName) ? savedName : preferences.layouts[0]?.name ?? '';
  const operate = (operation: GridOperation) => {
    const result = changeGrid(layout, operation);
    if (!result.error) updatePreferences({ grid: result.layout });
    setNotice(result.error ?? `${PANEL_LABELS[operation.id]} ${operation.kind === 'remove' ? 'removed' : operation.kind === 'add' ? 'added' : 'updated'}.`);
  };
  const beginDrag = (event: PointerEvent<HTMLButtonElement>, cell: GridCell, kind: Drag['kind']) => {
    if (gesture.current || !event.isPrimary || event.button !== 0 || !surface.current || window.matchMedia('(max-width: 965px)').matches) return;
    event.preventDefault();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    const styles = getComputedStyle(surface.current);
    const gap = Number.parseFloat(styles.columnGap);
    const width = surface.current.clientWidth - Number.parseFloat(styles.paddingLeft) - Number.parseFloat(styles.paddingRight);
    const next = { id: cell.id, kind, pointerId: event.pointerId, target: event.currentTarget,
      startX: event.clientX, startY: event.clientY, dx: 0, dy: 0,
      column: (width + gap) / GRID_COLUMNS, row: (Number.parseFloat(styles.gridAutoRows) || GRID_MIN_ROW_HEIGHT) + Number.parseFloat(styles.rowGap) };
    gesture.current = next;
    setDrag(next);
  };
  const dragEvents = (cell: GridCell, kind: Drag['kind']) => ({
    onPointerDown: (event: PointerEvent<HTMLButtonElement>) => beginDrag(event, cell, kind),
    onPointerMove: (event: PointerEvent<HTMLButtonElement>) => {
      const owner = gesture.current;
      if (!owner || owner.id !== cell.id || owner.kind !== kind || owner.pointerId !== event.pointerId) return;
      const dx = Math.round((event.clientX - owner.startX) / owner.column), dy = Math.round((event.clientY - owner.startY) / owner.row);
      if (dx !== owner.dx || dy !== owner.dy) {
        const next = { ...owner, dx, dy };
        gesture.current = next;
        setDrag(next);
      }
    },
    onPointerUp: (event: PointerEvent<HTMLButtonElement>) => {
      const owner = gesture.current;
      if (!owner || owner.id !== cell.id || owner.kind !== kind || owner.pointerId !== event.pointerId) return;
      const dx = Math.round((event.clientX - owner.startX) / owner.column), dy = Math.round((event.clientY - owner.startY) / owner.row);
      cancelDrag();
      operate({ kind, id: cell.id, dx, dy });
    },
    onPointerCancel: (event: PointerEvent<HTMLButtonElement>) => {
      if (gesture.current?.pointerId === event.pointerId) cancelDrag();
    },
    onLostPointerCapture: (event: PointerEvent<HTMLButtonElement>) => {
      if (gesture.current?.pointerId === event.pointerId) cancelDrag();
    },
  });
  return <section className="grid-workspace" aria-label="Market workspace">
    <nav className="grid-toolbar native-surface" aria-label="Workspace layout">
      <button aria-pressed={editing} onClick={() => { setEditing(!editing); cancelDrag(); setNotice(''); }}>{editing ? 'Finish editing' : 'Edit layout'}</button>
      <label>Preset<select aria-label="Layout preset" value="" onChange={event => {
        const id = event.target.value as GridPresetId;
        if (!(id in GRID_PRESETS)) return;
        cancelDrag();
        const nativeView = GRID_PRESETS[id].view;
        updatePreferences({ grid: gridPreset(id), nativeView, ...(nativeView === 'price' || nativeView === 'orderflow' ? { desktopView: nativeView } : {}) });
        setNotice(`${GRID_PRESETS[id].label} layout applied. Market selection is unchanged.`);
      }}><option value="" disabled>Choose preset</option>{Object.entries(GRID_PRESETS).map(([id, item]) => <option value={id} key={id}>{item.label}</option>)}</select></label>
      <details ref={savedLayouts} className="grid-saved"><summary>Saved layouts</summary><div className="grid-saved-controls">
        <label>Saved layout<select aria-label="Saved layout" value={selectedName} onChange={event => { setSavedName(event.target.value); setName(event.target.value); }}><option value="" disabled>Choose layout</option>{preferences.layouts.map(item => <option key={item.name}>{item.name}</option>)}</select></label>
        <button disabled={!selectedName} onClick={() => { const error = workspace.changeLayout({ kind: 'load', name: selectedName }); setNotice(error ?? `${selectedName} loaded. Market selection is unchanged.`); if (!error && savedLayouts.current) { savedLayouts.current.open = false; savedLayouts.current.querySelector('summary')?.focus(); } }}>Load layout</button>
        <label>Name<input aria-label="Layout name" maxLength={MAX_LAYOUT_NAME} value={name} onChange={event => setName(event.target.value)} /></label>
        <button onClick={() => { const error = workspace.changeLayout({ kind: 'save', name }); setNotice(error ?? `${name.trim()} saved.`); if (!error) setSavedName(name.trim()); }}>Save layout</button>
        <button disabled={!selectedName} onClick={() => { const error = workspace.changeLayout({ kind: 'rename', name: selectedName, nextName: name }); setNotice(error ?? `Layout renamed to ${name.trim()}.`); if (!error) setSavedName(name.trim()); }}>Rename layout</button>
        <button disabled={!selectedName} onClick={() => { const error = workspace.changeLayout({ kind: 'delete', name: selectedName }); setNotice(error ?? `${selectedName} deleted.`); }}>Delete layout</button>
        <small>Names are unique; up to 12 layouts. Layouts never change the source, instrument, cohort, or replay cursor.</small>
      </div></details>
      {editing && <><label>Add<select aria-label="Add panel" value={addId ?? ''} disabled={!addId} onChange={event => setAdding(event.target.value as GridPanelId)}>{available.map(id => <option value={id} key={id}>{PANEL_LABELS[id]}</option>)}</select></label><button disabled={!addId} onClick={() => { if (addId) operate({ kind: 'add', id: addId }); }}>Add panel</button><small id="grid-edit-help">{narrow ? 'Narrow screen: panels stack; dragging is disabled. Choose Move / Resize, then use direction buttons or arrow keys to edit the saved desktop grid. Moving changes stack order; sizes apply on wider screens.' : 'Drag handles, or focus Move / Resize and use arrow keys. Escape cancels dragging.'} Panels cannot overlap.</small></>}
      <span className="grid-notice" role="status" aria-live="polite">{notice || workspace.storageError}</span>
    </nav>
    {nativeInspection && <p className="grid-inspection-note">Native inspection owns book and tape; their side panels return on chart views.</p>}
    <div ref={surface} className="grid-surface" data-editing={editing}>
      {gridReadingOrder(layout).map(cell => {
        const suppressed = hiddenByInspection(cell.id);
        if (suppressed && !editing) return null;
        const dragging = drag?.id === cell.id ? drag : null;
        const buttonKind = buttonEditor?.id === cell.id ? buttonEditor.kind : 'move';
        const style = { gridColumn: `${cell.x + 1} / span ${cell.w}`, gridRow: `${cell.y + 1} / span ${cell.h}` } as CSSProperties;
        return <section key={cell.id} className="grid-panel" data-grid-panel={cell.id} aria-label={`${PANEL_LABELS[cell.id]} panel`} style={style}>
          {editing && <div className="grid-panel-tools">
            {(['move', 'resize'] as const).map(kind => <button key={kind} aria-label={`${kind === 'move' ? 'Move' : 'Resize'} ${PANEL_LABELS[cell.id]}`} aria-describedby="grid-edit-help" aria-pressed={narrow ? buttonKind === kind : undefined} title={narrow ? 'Choose an action, then use direction buttons or arrow keys' : 'Arrow keys edit one grid cell; Escape cancels dragging'} {...dragEvents(cell, kind)} onClick={() => { if (narrow) setButtonEditor({ id: cell.id, kind }); }} onKeyDown={event => {
              if (event.key === 'Escape') { event.preventDefault(); cancelDrag(); return; }
              if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
              event.preventDefault();
              cancelDrag();
              operate({ kind, id: cell.id, dx: event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0, dy: event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0 });
            }}>{kind === 'move' ? `Move ${PANEL_LABELS[cell.id]}` : 'Resize'}</button>)}
            {narrow && <div className="grid-direction-controls" role="group" aria-label={`${buttonKind === 'move' ? 'Move' : 'Resize'} ${PANEL_LABELS[cell.id]} directions`}>
              {([{ label: 'left', symbol: '←', dx: -1, dy: 0 }, { label: 'right', symbol: '→', dx: 1, dy: 0 },
                { label: 'up', symbol: '↑', dx: 0, dy: -1 }, { label: 'down', symbol: '↓', dx: 0, dy: 1 }] as const).map(direction =>
                <button key={direction.label} aria-label={`${buttonKind === 'move' ? 'Move' : 'Resize'} ${PANEL_LABELS[cell.id]} ${direction.label}`} onClick={() => operate({ kind: buttonKind, id: cell.id, dx: direction.dx, dy: direction.dy })}>{direction.symbol}</button>)}
            </div>}
            <span className="grid-coordinates">{cell.x + 1},{cell.y + 1} · {cell.w}×{cell.h}</span>
            <button aria-label={`Remove ${PANEL_LABELS[cell.id]}`} onClick={() => operate({ kind: 'remove', id: cell.id })}>Remove</button>
          </div>}
          <div className="grid-panel-content">{suppressed ? <p>Already visible in native inspection. Choose a chart view to restore this panel.</p> : renderPanel(cell.id)}</div>
          {dragging && <div aria-hidden="true" className="grid-drag-preview" style={dragging.kind === 'move' ? { transform: `translate(${dragging.dx * dragging.column}px, ${dragging.dy * dragging.row}px)` } : { width: `calc(100% + ${dragging.dx * dragging.column}px)`, height: `calc(100% + ${dragging.dy * dragging.row}px)` }} />}
        </section>;
      })}
      {layout.cells.length === 0 && <p className="grid-empty">No panels selected. Choose Edit layout to add a panel, or apply a preset.</p>}
    </div>
  </section>;
}
