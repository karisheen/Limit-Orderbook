import type { ReactNode } from 'react';
import { compact, exact, label } from './format';

export function Value({ value, className = '' }: { value: string | null | undefined; className?: string }) {
  return <span className={`number ${className}`} title={exact(value)}>{compact(value)}</span>;
}
export function Reasons({ reasons }: { reasons: string[] }) {
  return reasons.length > 0 ? <ul className="reasons">{[...new Set(reasons)].map(reason => <li key={reason}>{label(reason)}</li>)}</ul> : null;
}
export function Details({ title = 'Evidence', value }: { title?: string; value: unknown }) {
  return <details className="evidence"><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
export function Panel({ title, extra, children, className = '' }: { title: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><header className="panel-heading"><h2>{title}</h2>{extra}</header>{children}</section>;
}
export function Empty({ children }: { children: ReactNode }) { return <p className="empty">{children}</p>; }
