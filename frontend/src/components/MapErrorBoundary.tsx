/** Keeps a map failure (e.g. WebGL unavailable) from unmounting the whole app. */

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: string | null;
}

export default class MapErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    const raw = error instanceof Error ? error.message : JSON.stringify(error);
    const webgl = /webgl/i.test(raw);
    return {
      error: webgl
        ? "The map could not start because WebGL is unavailable in this browser. " +
          "Check chrome://gpu, or relaunch the browser with software rendering " +
          "(--use-angle=swiftshader --enable-unsafe-swiftshader)."
        : `The map failed to load: ${raw.slice(0, 300)}`,
    };
  }

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="map-error"
        >
          {this.state.error}
        </div>
      );
    }
    return this.props.children;
  }
}
