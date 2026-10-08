/**
 * State of the RPAS perimeter restart: the observed perimeter, which of its edges are active
 * (drawn lines and/or perimeter sides picked from a list), and the line being drawn on the map.
 * Shared by the Setup panel (PerimeterOverridePanel) and the map (MapView).
 */
import { useCallback, useMemo, useState } from "react";
import type { ReconMapLayers } from "../components/MapView";
import {
  activeLines,
  perimeterRing,
  perimeterSides,
  type Lnglat,
  type Side,
} from "../utils/activeEdges";

export interface ReconState {
  perimeter: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
  /** Where the perimeter came from, e.g. the file name */
  source: string | null;
  mode: "whole" | "marked";
  drawn: Lnglat[][];
  sides: Side[];
  draft: Lnglat[];
  drawing: boolean;
  /** Buffer (m) around active edges; null = the API default (one fuel-grid cell) */
  bufferM: number | null;
}

const EMPTY: ReconState = {
  perimeter: null, source: null, mode: "whole", drawn: [], sides: [], draft: [], drawing: false, bufferM: null,
};

/** Consecutive duplicate points removed (a double-click adds the same point twice). */
function dedupe(line: Lnglat[]): Lnglat[] {
  return line.filter((p, i) => i === 0 || p[0] !== line[i - 1][0] || p[1] !== line[i - 1][1]);
}

export function useRecon() {
  const [state, setState] = useState<ReconState>(EMPTY);

  const sideLines = useMemo(() => {
    const ring = perimeterRing(state.perimeter);
    return ring ? perimeterSides(ring) : null;
  }, [state.perimeter]);

  const lines = useMemo(
    () => (state.mode === "marked" ? activeLines(state.drawn, state.sides, sideLines) : []),
    [state.mode, state.drawn, state.sides, sideLines],
  );

  const setPerimeter = useCallback((perimeter: ReconState["perimeter"], source: string | null) => {
    setState((s) => ({ ...EMPTY, mode: s.mode, bufferM: s.bufferM, perimeter, source }));
  }, []);
  const clear = useCallback(() => setState(EMPTY), []);
  const setMode = useCallback((mode: ReconState["mode"]) => setState((s) => ({ ...s, mode, drawing: false, draft: [] })), []);
  const setBuffer = useCallback((bufferM: number | null) => setState((s) => ({ ...s, bufferM })), []);
  const toggleSide = useCallback(
    (side: Side) => setState((s) => ({
      ...s, sides: s.sides.includes(side) ? s.sides.filter((x) => x !== side) : [...s.sides, side],
    })),
    [],
  );
  const startDrawing = useCallback(() => setState((s) => ({ ...s, mode: "marked", drawing: true, draft: [] })), []);
  const addPoint = useCallback(
    (lng: number, lat: number) => setState((s) => (s.drawing ? { ...s, draft: [...s.draft, [lng, lat]] } : s)),
    [],
  );
  const finishDrawing = useCallback(() => setState((s) => {
    const line = dedupe(s.draft);
    return { ...s, drawing: false, draft: [], drawn: line.length >= 2 ? [...s.drawn, line] : s.drawn };
  }), []);
  const cancelDrawing = useCallback(() => setState((s) => ({ ...s, drawing: false, draft: [] })), []);
  const removeLine = useCallback((i: number) => setState((s) => ({ ...s, drawn: s.drawn.filter((_, k) => k !== i) })), []);

  const mapLayers = useMemo<ReconMapLayers | null>(
    () => (state.perimeter || state.drawing
      ? { perimeter: state.perimeter, lines, draft: state.draft, drawing: state.drawing }
      : null),
    [state.perimeter, state.drawing, state.draft, lines],
  );

  // One object per state change (the panel is memoised; streamed frames do not re-render it)
  return useMemo(() => ({
    state, lines, sideLines, mapLayers,
    setPerimeter, clear, setMode, setBuffer, toggleSide,
    startDrawing, addPoint, finishDrawing, cancelDrawing, removeLine,
  }), [state, lines, sideLines, mapLayers, setPerimeter, clear, setMode, setBuffer, toggleSide,
    startDrawing, addPoint, finishDrawing, cancelDrawing, removeLine]);
}

export type Recon = ReturnType<typeof useRecon>;
