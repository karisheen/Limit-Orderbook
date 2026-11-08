// Adapted from zadescoxp/order-flow-screener (Apache-2.0), revision 2b451b5. Modified for Limit Orderbook: Vite integration, strict typing and local workspace.

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon } from "./Icons";

export interface PickerOption<T extends string> {
  value: T;
  label: string;
  sub?: string;
  icon?: ReactNode;
  disabled?: boolean;
  keywords?: string;
}

/** Pill button that opens a searchable list; replaces native selects where options need icons. */
export function Picker<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  searchable = false,
  className = "",
  renderValue,
  width = 280,
}: {
  value: T;
  options: PickerOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  searchable?: boolean;
  className?: string;
  renderValue?: (option: PickerOption<T> | undefined) => ReactNode;
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [offset, setOffset] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const current = options.find((option) => option.value === value);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => `${o.label} ${o.sub ?? ""} ${o.keywords ?? ""}`.toLowerCase().includes(q));
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const reposition = () => {
      const left = root.current?.getBoundingClientRect().left ?? 0;
      setOffset(Math.min(0, window.innerWidth - left - Math.min(width, window.innerWidth - 24) - 12));
    };
    reposition();
    window.addEventListener("pointerdown", close);
    window.addEventListener("resize", reposition);
    const frame = requestAnimationFrame(() => (input.current ?? list.current)?.focus());
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", reposition);
    };
  }, [open, width]);

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const choose = (option: PickerOption<T> | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    trigger.current?.focus();
  };

  const toggle = () => {
    if (!open) {
      setQuery("");
      setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    }
    setOpen((v) => !v);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) return;
    if (event.key === "Escape") {
      setOpen(false);
      trigger.current?.focus();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      const enabled = filtered.map((option, index) => option.disabled ? -1 : index).filter((index) => index >= 0);
      const position = enabled.indexOf(active);
      const next = event.key === "Home" ? enabled[0] : event.key === "End" ? enabled.at(-1)
        : enabled[(position + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length];
      if (next !== undefined) setActive(next);
    } else if (event.key === "Enter") {
      choose(filtered[active]);
    } else return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div className="picker" ref={root} onKeyDown={onKeyDown} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
    }}>
      <button
        type="button"
        ref={trigger}
        className={`ui-button picker-button ${className} ${open ? "is-open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${ariaLabel}: ${current?.label ?? value}`}
        onClick={toggle}
      >
        {renderValue ? renderValue(current) : (
          <>
            {current?.icon}
            <span className="picker-label">{current?.label ?? value}</span>
          </>
        )}
        <span className="picker-chevron"><Icon.Chevron /></span>
      </button>
      {open && (
        <div className="picker-popover" style={{ width, left: offset }} role="dialog" aria-label={ariaLabel}>
          {searchable && (
            <label className="picker-search">
              <Icon.Search />
              <input
                ref={input}
                value={query}
                placeholder="Search"
                onChange={(e) => { setQuery(e.target.value); setActive(0); }}
                aria-label={`Search ${ariaLabel.toLowerCase()}`}
                role="combobox"
                aria-expanded={open}
                aria-controls={listId}
                aria-activedescendant={filtered[active] ? `${listId}-${active}` : undefined}
              />
              <kbd>esc</kbd>
            </label>
          )}
          <div id={listId} className="picker-list" role="listbox" ref={list} tabIndex={searchable ? -1 : 0} aria-label={ariaLabel} aria-activedescendant={filtered[active] ? `${listId}-${active}` : undefined}>
            {filtered.length === 0 && <div className="picker-empty">No matches</div>}
            {filtered.map((option, index) => (
              <button
                type="button"
                key={option.value}
                data-index={index}
                id={`${listId}-${index}`}
                tabIndex={-1}
                role="option"
                aria-selected={option.value === value}
                disabled={option.disabled}
                className={`picker-option ${index === active ? "is-active" : ""} ${option.value === value ? "is-selected" : ""}`}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(option)}
              >
                {option.icon}
                <span className="picker-option-text">
                  <span>{option.label}</span>
                  {option.sub && <small>{option.sub}</small>}
                </span>
                {option.value === value && <span className="picker-check" aria-hidden>●</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
