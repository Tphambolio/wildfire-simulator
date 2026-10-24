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
 *   3. Restart: the request also carries the observation time, Setup's current weather, FWI
 *      codes and weather changes (the step in force then, and the later ones), burning period
 *      and FFMC spin-up. Predict N h runs N h.
 *
 * Active-segment restart (backlog A1, docs/active-segments.md): the observation time T is an
 * editable field; active edges can also be classified from RPAS thermal hotspots (GeoJSON
 * points with a time) or a NASA FIRMS VIIRS CSV (POST /simulations/classify-perimeter), shown
 * on the map as active (orange-red) / held (blue) segments; held segments hold for H hours
 * (default 24). Without a run, the same panel starts a standalone forecast from the observed
 * perimeter at T (POST /simulations/observed-perimeter) with Setup's weather and fuel.
 */

import { memo, useCallback, useId, useRef, useState } from "react";
import type { ActiveSegmentsInfo, BurningPeriod, PerimeterOverrideRequest } from "../types/simulation";
import type { Recon } from "../hooks/useRecon";
import {
  MAX_HOLD_HOURS,
  SIDES,
  SIDE_NAMES,
  buildOverrideRequest,
  framePerimeterToPolygon,
  perimeterFromGeoJSON,
  segmentReadout,
  type OverrideInputs,
} from "../utils/activeEdges";
import { classifyPerimeter } from "../services/api";
import { formatClock, formatDate, toDateTimeInputs, toEdmontonIso, zonedWallTimeToMs, zoneAbbrev } from "../utils/time";
import { VALIDATION_DOC_URL, formatBurningPeriod } from "../utils/skillOptions";
import { scheduleForRestart } from "../utils/weatherSchedule";
import type { SetupWeather } from "./WeatherPanel";
import InfoTip from "./InfoTip";
import Badge from "./Badge";
import { TIPS } from "../content/explanations";

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
  /** Setup's current weather, FWI codes and weather changes (the restart uses them) */
  setupWeather?: SetupWeather | null;
  /** Standalone forecast from the observed perimeter (no source run; A1, R13/R14). Absent =
   * not offered. */
  onObservedStart?: (inputs: Omit<OverrideInputs, "simulationId">, observationIso: string) => void;
  /** The shown run's active-segment readout (API `active_segments`), if it was a restart */
  runActiveSegments?: ActiveSegmentsInfo | null;
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
  setupWeather = null,
  onObservedStart,
  runActiveSegments = null,
}: PerimeterOverridePanelProps) {
  const id = useId();
  const [parseError, setParseError] = useState<string | null>(null);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const hotspotRef = useRef<HTMLInputElement>(null);
  const firmsRef = useRef<HTMLInputElement>(null);
  const [classifying, setClassifying] = useState(false);
  const { state } = recon;

  const processFile = useCallback(
    (file: File) => {
      setParseError(null);
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const gj = JSON.parse(reader.result as string) as Record<string, unknown>;
          recon.setPerimeter(perimeterFromGeoJSON(gj), simulationId ? `${file.name} (loaded on run ${simulationId})` : file.name);
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
    [recon, simulationId],
  );

  const standalone = !simulationId && !!onObservedStart;
  const disabled = (!simulationId && !standalone) || isRunning;
  // Observation time T: the field when set, else the timeline's selected time (or now)
  const at = new Date(state.observedMs ?? observedAtMs ?? Date.now());
  const atLabel = `${formatClock(at)} ${zoneAbbrev(at)}`;
  const atInputs = toDateTimeInputs(at.getTime());
  const marked = state.mode === "marked";
  const classified = state.mode === "classified";

  const classifyFile = (file: File, kind: "hotspots" | "firms") => {
    if (!state.perimeter) return;
    setParseError(null);
    setClassifying(true);
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const text = reader.result as string;
        const body = kind === "hotspots"
          ? { hotspots_geojson: JSON.parse(text) as GeoJSON.FeatureCollection }
          : { firms_csv: text };
        const res = await classifyPerimeter({
          perimeter_geojson: state.perimeter!, observation_time: toEdmontonIso(at.getTime()), ...body,
        });
        recon.setClassified({
          source: res.summary.source, segments: res.segments, activeEdges: res.active_edges, summary: res.summary,
        });
      } catch (err) {
        setParseError(err instanceof SyntaxError ? "Failed to parse the hotspot GeoJSON." : (err as Error).message);
      } finally {
        setClassifying(false);
        for (const r of [hotspotRef, firmsRef]) if (r.current) r.current.value = "";
      }
    };
    reader.readAsText(file);
  };

  // Weather for a restart at the selected time: Setup's, with the weather change in force
  // then (and the later ones) when Setup has a schedule
  const restartWeather = setupWeather
    ? scheduleForRestart(setupWeather.weather, setupWeather.forecastOn ? [] : setupWeather.steps,
      at.getTime(), durationHours)
    : null;
  const nLater = restartWeather?.schedule?.length ?? 0;

  const restart = () => {
    if (!state.perimeter || (!simulationId && !standalone)) return;
    setParseError(null);
    const inputs: Omit<OverrideInputs, "simulationId"> = {
      perimeter: state.perimeter,
      durationHours,
      snapshotMinutes,
      mode: state.mode,
      lines: recon.lines,
      classifiedEdges: state.classified?.activeEdges ?? null,
      holdHours: state.mode === "whole" ? null : state.holdHours,
      bufferM: state.bufferM,
      startTime: toEdmontonIso(at.getTime()),
      burningPeriod,
      ffmcSpinUp: spinUp,
      weather: restartWeather?.weather ?? null,
      fwi: setupWeather?.fwi ?? null,
      schedule: restartWeather?.schedule ?? null,
    };
    try {
      if (simulationId) onOverrideStart(buildOverrideRequest({ ...inputs, simulationId }));
      else {
        buildOverrideRequest({ ...inputs, simulationId: "" }); // same checks (e.g. nothing marked)
        onObservedStart!(inputs, toEdmontonIso(at.getTime()));
      }
    } catch (err) {
      setParseError((err as Error).message);
    }
  };
  const segReadout = classified ? segmentReadout(state.classified?.summary) : null;
  const runReadout = runActiveSegments && runActiveSegments.static_share_model !== undefined
    ? [
      runActiveSegments.static_share_model !== null && runActiveSegments.static_share_model !== undefined
        ? `modelled static share ${Math.round(runActiveSegments.static_share_model * 100)} %` : null,
      segmentReadout(runActiveSegments),
      runActiveSegments.hold_hours !== null && runActiveSegments.hold_hours !== undefined ? `hold ${runActiveSegments.hold_hours} h` : null,
    ].filter(Boolean).join(" · ")
    : null;

  const sideAvailable = (s: (typeof SIDES)[number]) => (recon.sideLines?.[s].length ?? 0) > 0;

  return (
    <div className="panel recon-panel">
      <h3 className="recon-title">RPAS Recon Override</h3>

      {/* Before a run the section summary says "Available after a run" (or offers the
          standalone forecast from an observed perimeter when Setup can build a request) */}
      {!simulationId && !standalone ? null : (
        <>

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
              disabled={disabled || !simulationId || !modelledPerimeter || modelledPerimeter.length < 3}
              onClick={() => {
                const poly = modelledPerimeter ? framePerimeterToPolygon(modelledPerimeter) : null;
                if (poly) recon.setPerimeter(poly, `modelled perimeter at ${atLabel} of run ${simulationId}`);
              }}
            >
              Use the modelled perimeter at {atLabel}
            </button>
            <div className="with-tip recon-observed">
              <label className="field">
                Observed at (T, {zoneAbbrev(at)})
                <input
                  type="datetime-local"
                  value={`${atInputs.date}T${atInputs.time}`}
                  disabled={disabled}
                  onChange={(e) => {
                    const [d, t] = e.target.value.split("T");
                    const ms = d && t ? zonedWallTimeToMs(d, t.slice(0, 5)) : null;
                    recon.setObserved(ms);
                  }}
                  aria-describedby={`${id}-t-hint`}
                />
              </label>
              <InfoTip label="About the observation time" text={TIPS.observationTime} />
            </div>
            <p className="status-line" id={`${id}-t-hint`}>
              {state.observedMs === null ? "Timeline's selected time" : `${formatDate(at)} ${atLabel}`}
              {state.observedMs !== null && (
                <button type="button" className="btn-secondary btn-inline" onClick={() => recon.setObserved(null)}>
                  Use timeline time
                </button>
              )}
            </p>
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
                <div className="with-tip">
                  <label>
                    <input
                      type="radio"
                      name={`${id}-mode`}
                      checked={marked}
                      onChange={() => recon.setMode("marked")}
                    />
                    Only marked edges active
                  </label>
                  <InfoTip label="About active edges" interactive>
                    {TIPS.activeEdges}{" "}
                    <a href={VALIDATION_DOC_URL} target="_blank" rel="noopener noreferrer">
                      Evidence: held-out validation
                    </a>
                  </InfoTip>
                </div>
                <div className="with-tip">
                  <label>
                    <input
                      type="radio"
                      name={`${id}-mode`}
                      checked={classified}
                      disabled={!state.classified}
                      onChange={() => recon.setMode("classified")}
                    />
                    Classified from detections
                  </label>
                  <InfoTip label="About the classification" text={TIPS.segmentClassification} />
                </div>
              </div>
                <div className="recon-draw-row">
                  <button
                    type="button"
                    className="btn-secondary btn-inline"
                    disabled={disabled || classifying}
                    onClick={() => hotspotRef.current?.click()}
                  >
                    Classify from RPAS hotspots
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-inline"
                    disabled={disabled || classifying}
                    onClick={() => firmsRef.current?.click()}
                  >
                    Classify from VIIRS file
                  </button>
                  <input ref={hotspotRef} type="file" accept=".geojson,.json" style={{ display: "none" }}
                    data-testid="recon-hotspots-file"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) classifyFile(f, "hotspots"); }} />
                  <input ref={firmsRef} type="file" accept=".csv,.txt" style={{ display: "none" }}
                    data-testid="recon-firms-file"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) classifyFile(f, "firms"); }} />
                </div>
                {classifying && <p className="hint-sm" role="status">Classifying…</p>}
                {classified && segReadout && (
                  <p className="status-line recon-segments" role="status" data-testid="recon-segments">
                    <span className="recon-swatch" aria-hidden="true" /> Active edge
                    {" · "}<span className="recon-swatch recon-swatch-held" aria-hidden="true" /> {`Held edge (no spread for ${state.holdHours} h)`}
                    {" · "}{segReadout}
                    {state.classified?.summary.n_detections_used !== undefined
                      ? ` · ${state.classified.summary.n_detections_used} detection${state.classified.summary.n_detections_used === 1 ? "" : "s"} used`
                      : ""}
                  </p>
                )}

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
                  <div className="with-tip">
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
                  <InfoTip label="About the buffer" text={TIPS.bufferBlank} />
                  </div>
                  <p className="status-line" id={`${id}-buffer-hint`}>
                    {recon.lines.length
                      ? `${recon.lines.length} active edge${recon.lines.length === 1 ? "" : "s"} on the map`
                      : "No active edge marked yet"}
                  </p>
                </>
              )}
            </fieldset>
          )}

          {state.perimeter && state.mode !== "whole" && (
            <div className="with-tip">
              <label className="field recon-buffer">
                Hold held segments (h)
                <input
                  type="number"
                  min={0}
                  max={MAX_HOLD_HOURS}
                  step={1}
                  value={state.holdHours}
                  disabled={disabled}
                  onChange={(e) => recon.setHold(e.target.valueAsNumber)}
                />
              </label>
              <InfoTip label="About the hold" text={TIPS.holdHours} />
            </div>
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
          <div className="status-line">
            <Badge tone="neutral" testId="recon-run-options">
              {burningPeriod ? `Burns ${formatBurningPeriod(burningPeriod)}` : "Burns all day"}
              {spinUp ? " · spin-up" : ""}
            </Badge>
          </div>
          {restartWeather && setupWeather && (
            <div className="status-line with-tip">
              <Badge tone="neutral" testId="recon-weather">
                {`Setup weather: ${restartWeather.weather.wind_speed} km/h from ${restartWeather.weather.wind_direction}° · RH ${restartWeather.weather.relative_humidity}% · FFMC ${setupWeather.fwi.ffmc ?? "—"}`}
                {nLater ? ` · ${nLater} later change${nLater === 1 ? "" : "s"}` : ""}
                {setupWeather.forecastOn ? " · hourly forecast" : ""}
              </Badge>
              <InfoTip label="Weather used by the restart" text={TIPS.reconWeather} />
            </div>
          )}
          <div className="with-tip">
            <button
              type="button"
              className="btn-primary recon-restart"
              disabled={disabled || !state.perimeter || state.drawing || (marked && recon.lines.length === 0)}
              onClick={restart}
            >
              {simulationId ? `Restart at ${atLabel} from observed` : `Forecast from observed at ${atLabel}`}
            </button>
            <InfoTip label="About the restart" text={`${TIPS.reconRestart(atLabel)} Run options from Setup apply.`} />
          </div>

          {runReadout && (
            <div className="status-line with-tip">
              <Badge tone="neutral" testId="recon-run-readout">{`This run: ${runReadout}`}</Badge>
              <InfoTip label="About the static share" text={TIPS.staticShare} />
            </div>
          )}
          {parseError && <div className="recon-error" role="alert">{parseError}</div>}
        </>
      )}
    </div>
  );
}

// Re-render only when props change, not on every streamed frame
export default memo(PerimeterOverridePanel);
