import { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Catches render-time errors so a single bad component doesn't blank the
 * whole app. We deliberately keep the fallback dead-simple — no retries,
 * no telemetry — so the boundary itself can't fail.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    // eslint-disable-next-line no-console
    console.error('UI error boundary caught', error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-6 rounded-xl border border-red-500/40 bg-red-500/10 p-6 text-sm text-red-200">
        <div className="mb-2 font-semibold">Something went wrong rendering this view.</div>
        <pre className="mb-3 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-red-200/80">
          {this.state.error.message}
        </pre>
        <button
          onClick={this.reset}
          className="rounded-md border border-red-400/40 px-3 py-1.5 text-xs font-medium text-red-100 hover:bg-red-500/20"
        >
          Try again
        </button>
      </div>
    );
  }
}
