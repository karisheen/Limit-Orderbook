import { Component, lazy, Suspense } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { createRoot } from 'react-dom/client';

// A document owns one engine and stylesheet. Navigation between workspaces
// reloads the document so feeds and global theme tokens cannot leak across it.
const params = new URLSearchParams(window.location.search);
const native = params.get('workspace') === 'native' || params.has('source');
const Workspace = native ? lazy(() => import('./App')) : lazy(() => import('./OrderFlowApp'));

class TerminalBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Terminal rendering failure', error, info.componentStack); }
  render() {
    if (this.state.error) return <main className="error-banner" role="alert"><h1>Terminal unavailable</h1><p>No replacement or fixture data has been displayed.</p><pre>{this.state.error}</pre><button onClick={() => window.location.reload()}>Reload independent session</button></main>;
    return this.props.children;
  }
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing terminal root');
createRoot(root).render(<TerminalBoundary><Suspense fallback={<p role="status">Loading terminal…</p>}><Workspace /></Suspense></TerminalBoundary>);
