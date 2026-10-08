/**
 * Perimeter Override Panel: restart the run from an observed (RPAS) fire perimeter.
 *
 * Workflow:
 *   1. Load the observed perimeter: an M4TD trace as GeoJSON (drop or browse), or the modelled
 *      perimeter at the timeline's selected time; it is drawn on the map.
 *   2. Mark where the fire is still active: the whole perimeter (default), or only marked
 *      edges, drawn on the map (click along the perimeter, double-click to end) or picked as
 *      whole perimeter sides from a list (keyboard). Edges not marked active are treated as
 *      burned out (POST /simulations/perimeter-override `active_edges`; docs/validation.md).
 *   3. Restart: the request also carries the observation time and Setup's burning period and
 *      FFMC spin-up.
 */

import { memo, useCallback, useId, useRef, useState } from "react";
import type { BurningPeriod, PerimeterOverrideRequest } from "../types/simulation";
import type { Recon } from "../hooks/useRecon";
import {
  SIDES,
  SIDE_NAMES,
  buildOverrideRequest,
  framePerimeterToPolygon,
  perimeterFromGeoJSON,
} from "../utils/activeEdges";
import { formatClock, toEdmontonIso, zoneAbbrev } from "../utils/time";
import { VALIDATION_DOC_URL, formatBurningPeriod } from "../utils/skillOptions";

interface PerimeterOverridePanelProps {
  /** ID of the currently active simulation (source config). Null = disabled. */
  simulationId: string | null;
  /** Called with the override request so the parent hook can start the run. */
  onOverrideStart: (req: PerimeterOverrideRequest) => void;
  /** True while the override simulation is starting/running. */
  isRunning: boolean;
  /** Perimeter, active edges and drawing state (shared with the map) */
  recon: Recon;
  /** Modelled perimeter at the timeline's selected time ([[lat, lng], ...]) */
  modelledPerimeter?: number[][] | null;
  /** Time of the observation: the timeline's selected time (null = now) */
  observedAtMs?: number | null;
  /** Setup's burning period (null = off) */
  burningPeriod?: BurningPeriod | null;
  /** Send the FFMC spin-up (Setup has it on and the source run used it) */
  spinUp?: boolean;
}

function PerimeterOverridePanel({
  simulationId,
  onOverrideStart,
  isRunning,
  recon,
  modelledPerimeter = null,
  observedAtMs = null,
  burningPeriod = null,
  spinUp = false,
}: PerimeterOverridePanelProps) {
  const id = useId();
  const [parseError, setParseError] = useState<string | null>(null);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const { state } = recon;

  const processFile = useCallback(
    (file: File) => {
      setParseError(null);
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const gj = JSON.parse(reader.result as string) as Record<string, unknown>;
          recon.setPerimeter(perimeterFromGeoJSON(gj), file.name);
        } catch (err) {
          setParseError(err instanceof SyntaxError
            ? "Failed to parse GeoJSON. Ensure the file is valid JSON."
            : (err as Error).message);
        } finally {
          if (fileRef.current) fileRef.current.value = "";
        }
      };
      reader.readAsText(file);
    },
    [recon],
  );

  const disabled = !simulationId || isRunning;
  const at = observedAtMs !== null ? new Date(observedAtMs) : new Date();
  const atLabel = `${formatClock(at)} ${zoneAbbrev(at)}`;
  const marked = state.mode === "marked";

  const restart = () => {
    if (!simulationId || !state.perimeter) return;
    setParseError(null);
    try {
      onOverrideStart(buildOverrideRequest({
        simulationId,
        perimeter: state.perimeter,
        durationHours,
        snapshotMinutes,
        mode: state.mode,
        lines: recon.lines,
        bufferM: state.bufferM,
        startTime: toEdmontonIso(at.getTime()),
        burningPeriod,
        ffmcSpinUp: spinUp,
      }));
    } catch (err) {
      setParseError((err as Error).message);
    }
  };

  const sideAvailable = (s: (typeof SIDES)[number]) => (recon.sideLines?.[s].length ?? 0) > 0;

  return (
    <div className="panel recon-panel">
      <h3 className="recon-title">RPAS Recon Override</h3>

      {!simulationId ? (
        <p className="recon-hint">Run a simulation first to enable perimeter correction.</p>
      ) : (
        <>
          <p className="recon-hint">
            Restart the run at {atLabel} (the timeline&apos;s selected time) from an observed
            fire perimeter, with only its active edges spreading.
          </p>

          <fieldset className="field-group">
            <legend>1 · Observed perimeter</legend>
            <div
              className={`recon-drop${dragOver ? " drag-over" : ""}${disabled ? " recon-drop-disabled" : ""}`}
              onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={disabled ? undefined : (e) => {
                e.preventDefault();
                setDragOver(false);
                const file = e.dataTransfer.files[0];
                if (file) processFile(file);
              }}
              onClick={() => !disabled && fileRef.current?.click()}
              role="button"
              tabIndex={disabled ? -1 : 0}
              aria-disabled={disabled}
              onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !disabled) { e.preventDefault(); fileRef.current?.click(); } }}
              aria-label="Load an observed perimeter: drop a GeoJSON file or press to browse"
            >
              {isRunning ? (
                <span className="recon-drop-text">Override running…</span>
              ) : (
                <>
                  <span className="recon-drop-icon" aria-hidden="true">📡</span>
                  <span className="recon-drop-text">Drop M4TD perimeter (.geojson) or click to browse</span>
                </>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".geojson,.json"
              style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) processFile(f); }}
              disabled={disabled}
              data-testid="recon-file"
            />
            <button
              type="button"
              className="btn-secondary recon-btn"
              disabled={disabled || !modelledPerimeter || modelledPerimeter.length < 3}
              onClick={() => {
                const poly = modelledPerimeter ? framePerimeterToPolygon(modelledPerimeter) : null;
                if (poly) recon.setPerimeter(poly, `modelled perimeter at ${atLabel}`);
              }}
            >
              Use the modelled perimeter at {atLabel}
            </button>
            {state.perimeter && (
              <div className="recon-loaded" role="status">
                <span>Loaded: {state.source ?? "perimeter"}</span>
                <button type="button" className="btn-secondary btn-inline" onClick={recon.clear}>
                  Clear
                </button>
              </div>
            )}
          </fieldset>

          {state.perimeter && (
            <fieldset className="field-group recon-edges">
              <legend>2 · Active edges</legend>
              <div className="recon-mode" role="radiogroup" aria-label="Which edges are active">
                <label>
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    checked={!marked}
                    onChange={() => recon.setMode("whole")}
                  />
                  Whole perimeter active
                </label>
                <label>
                  <input
                    type="radio"
                    name={`${id}-mode`}
                    checked={marked}
                    onChange={() => recon.setMode("marked")}
                  />
                  Only marked edges active
                </label>
              </div>
              <p className="hint-sm">
                Edges not marked active are treated as burned out and do not spread
                (raised skill on held-out Alberta fires;{" "}
                <a href={VALIDATION_DOC_URL} target="_blank" rel="noopener noreferrer" className="hint-link">
                  evidence
                </a>).
              </p>

              {marked && (
                <>
                  <div className="recon-draw-row">
                    {!state.drawing ? (
                      <button type="button" className="btn-secondary btn-inline" onClick={recon.startDrawing} disabled={disabled}>
                        Draw active edge on map
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="btn-secondary btn-inline"
                          onClick={recon.finishDrawing}
                          disabled={state.draft.length < 2}
                        >
                          Finish line ({state.draft.length} points)
                        </button>
                        <button type="button" className="btn-secondary btn-inline" onClick={recon.cancelDrawing}>
                          Cancel
                        </button>
                      </>
                    )}
                  </div>
                  {state.drawing && (
                    <p className="hint-sm" role="status">
                      Click along the perimeter on the map; double-click or Finish line to end.
                    </p>
                  )}
                  {state.drawn.length > 0 && (
                    <ul className="recon-lines" aria-label="Drawn active edges">
                      {state.drawn.map((line, i) => (
                        <li key={i}>
                          <span className="recon-swatch" aria-hidden="true" />
                          Drawn edge {i + 1} ({line.length} points)
                          <button
                            type="button"
                            className="btn-secondary btn-inline"
                            onClick={() => recon.removeLine(i)}
                            aria-label={`Remove drawn edge ${i + 1}`}
                          >
                            Remove
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <fieldset className="field-group recon-sides">
                    <legend>Or mark whole sides of the perimeter</legend>
                    <div className="recon-side-grid">
                      {SIDES.map((s) => (
                        <label key={s} title={SIDE_NAMES[s]}>
                          <input
                            type="checkbox"
                            checked={state.sides.includes(s)}
                            disabled={!sideAvailable(s)}
                            onChange={() => recon.toggleSide(s)}
                            aria-label={`${SIDE_NAMES[s]} side active`}
                          />
                          {s}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <label className="field recon-buffer">
                    Buffer around active edges (m)
                    <input
                      type="number"
                      min={0}
                      max={5000}
                      step={10}
                      placeholder="1 grid cell"
                      value={state.bufferM ?? ""}
                      onChange={(e) => recon.setBuffer(e.target.value === "" ? null : Math.max(0, Math.min(5000, e.target.valueAsNumber)))}
                      aria-describedby={`${id}-buffer-hint`}
                    />
                  </label>
                  <p className="hint-sm" id={`${id}-buffer-hint`}>
                    Blank: one fuel-grid cell (the validated default).{" "}
                    {recon.lines.length
                      ? `${recon.lines.length} active line${recon.lines.length === 1 ? "" : "s"} on the map (orange-red dashes, “active (RPAS)”).`
                      : "No active edge marked yet."}
                  </p>
                </>
              )}
            </fieldset>
          )}

          <div className="recon-controls">
            <label className="recon-label">
              Predict
              <span className="recon-unit">(h)</span>
              <input
                type="number"
                className="recon-input"
                min={0.5}
                max={24}
                step={0.5}
                value={durationHours}
                disabled={disabled}
                onChange={(e) => setDurationHours(Number(e.target.value))}
              />
            </label>
            <label className="recon-label">
              Snapshot
              <span className="recon-unit">(min)</span>
              <input
                type="number"
                className="recon-input"
                min={5}
                max={120}
                step={5}
                value={snapshotMinutes}
                disabled={disabled}
                onChange={(e) => setSnapshotMinutes(Number(e.target.value))}
              />
            </label>
          </div>
          <p className="hint-sm">
            Run options from Setup: {burningPeriod ? `burning period ${formatBurningPeriod(burningPeriod)}` : "burns all day"}
            {spinUp ? " · evening FFMC spin-up" : ""}.
          </p>
          <button
            type="button"
            className="btn-primary recon-restart"
            disabled={disabled || !state.perimeter || state.drawing || (marked && recon.lines.length === 0)}
            onClick={restart}
          >
            Restart from observed perimeter
          </button>

          {parseError && <div className="recon-error" role="alert">{parseError}</div>}
        </>
      )}
    </div>
  );
}

// Re-render only when props change, not on every streamed frame
export default memo(PerimeterOverridePanel);
