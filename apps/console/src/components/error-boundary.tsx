import { Component, type ErrorInfo, type ReactNode } from "react";

import { ApiError } from "@/lib/api";

interface Props {
  fallback?: (
    error: Error,
    reset: () => void,
  ) => ReactNode;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/*
 * ErrorBoundary — surfaces typed reason_code-shaped errors
 * (P8: never "something went wrong, see logs"). Every Knowledge
 * UI panel default-export must be wrapped in <ErrorBoundary>.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.error("[BrickVision] ErrorBoundary caught", error, info);
    }
  }

  reset = () => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    const reason =
      error instanceof ApiError && error.reason_code
        ? error.reason_code
        : null;

    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm">
        <div className="font-semibold text-destructive">
          {error.message}
        </div>
        {reason && (
          <div className="mt-2 font-mono text-xs text-muted-foreground">
            {reason}
          </div>
        )}
        <button
          type="button"
          className="mt-3 text-xs underline-offset-4 hover:underline"
          onClick={this.reset}
        >
          Try again
        </button>
      </div>
    );
  }
}
