import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Workbench error boundary caught:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen flex-col items-center justify-center p-6 text-center">
          <h1 className="mb-2 text-lg font-semibold">Something went wrong</h1>
          <p className="mb-4 max-w-md text-xs text-muted-foreground">{this.state.error.message}</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="border px-3 py-1 text-xs hover:bg-accent"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
