import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

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
createRoot(root).render(<TerminalBoundary><App /></TerminalBoundary>);
