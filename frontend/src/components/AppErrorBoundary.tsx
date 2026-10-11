/**
 * Catches a render error in its subtree and shows a recoverable panel instead of letting React
 * unmount the whole app (a black screen). Used at the top level (main.tsx) and around the EOC
 * console (App.tsx). The error is logged to the console with its component stack.
 *
 * Recovery: "Return to map" calls `onReset` (the EOC boundary switches to the Simulation tab;
 * the top-level boundary reloads the page, which reopens on the map with the incident restored
 * from localStorage). Changing `resetKey` also clears the error, so the boundary is usable again
 * the next time its section is opened.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Short name of the part that failed, shown in the panel title ("EOC console") */
  area?: string;
  /** Called by "Return to map" after the boundary clears its error */
  onReset?: () => void;
  /** Clears the error when it changes (e.g. the active tab) */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
  resetKey: unknown;
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { resetKey: props.resetKey, error: null };
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(`[FireSim] ${this.props.area ?? "app"} crashed:`, error, info.componentStack);
  }

  private handleReset = () => {
    this.setState({ error: null });
    if (this.props.onReset) this.props.onReset();
    else window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div role="alert" className="app-error-fallback">
        <h2 className="app-error-title">
          {this.props.area ? `The ${this.props.area} hit an error` : "FireSim hit an error"}
        </h2>
        <p className="app-error-text">
          Your incident and scenarios are saved in this browser. Return to the map and try again;
          if it happens again, note what you clicked and report it.
        </p>
        <pre className="app-error-detail">{error.message.slice(0, 300)}</pre>
        <button type="button" className="btn-primary app-error-btn" onClick={this.handleReset}>
          Return to map
        </button>
      </div>
    );
  }
}
