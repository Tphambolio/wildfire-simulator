/**
 * Perimeter Override Panel — import drone reconnaissance to correct simulation.
 *
 * Accepts a GeoJSON file (M4TD orthomosaic trace or manually drawn polygon)
 * representing the actual observed fire perimeter, and triggers a fresh
 * Huygens spread run seeded from that corrected front.
 *
 * Workflow:
 *   1. Operator completes a drone recon flight with M4TD fleet
 *   2. M4TD orthomosaic is traced to a GeoJSON polygon and exported
 *   3. Operator drops the .geojson file onto this panel (or clicks to browse)
 *   4. Panel extracts the geometry and calls onOverrideStart with the request
 *   5. useSimulation hook POSTs to /perimeter-override and opens WebSocket
 */

import { useCallback, useRef, useState } from "react";
import type { PerimeterOverrideRequest } from "../types/simulation";

interface PerimeterOverridePanelProps {
  /** ID of the currently active simulation (source config). Null = disabled. */
  simulationId: string | null;
  /** Called with the override request so the parent hook can start the run. */
  onOverrideStart: (req: PerimeterOverrideRequest) => void;
  /** True while the override simulation is starting/running. */
  isRunning: boolean;
}

export default function PerimeterOverridePanel({
  simulationId,
  onOverrideStart,
  isRunning,
}: PerimeterOverridePanelProps) {
  const [parseError, setParseError] = useState<string | null>(null);
  const [durationHours, setDurationHours] = useState(4);
  const [snapshotMinutes, setSnapshotMinutes] = useState(30);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const processFile = useCallback(
    (file: File) => {
      if (!simulationId) return;
      setParseError(null);

      const reader = new FileReader();
      reader.onload = () => {
        try {
          const geojson = JSON.parse(reader.result as string) as Record<string, unknown>;

          // Accept Feature, bare geometry, or FeatureCollection
          let geometry: Record<string, unknown>;
          if (geojson.type === "Feature") {
            geometry = geojson.geometry as Record<string, unknown>;
          } else if (
            geojson.type === "Polygon" ||
            geojson.type === "MultiPolygon"
          ) {
            geometry = geojson;
          } else if (geojson.type === "FeatureCollection") {
            const features = geojson.features as Array<Record<string, unknown>>;
            const polyFeat = features.find((f) => {
              const g = f.geometry as Record<string, unknown> | null;
              return g && (g.type === "Polygon" || g.type === "MultiPolygon");
            });
            if (!polyFeat) {
              setParseError("No Polygon or MultiPolygon found in FeatureCollection.");
              return;
            }
            geometry = polyFeat.geometry as Record<string, unknown>;
          } else {
            setParseError(
              `Unsupported GeoJSON type '${geojson.type}'. Expected Polygon, MultiPolygon, Feature, or FeatureCollection.`
            );
            return;
          }

          if (geometry.type !== "Polygon" && geometry.type !== "MultiPolygon") {
            setParseError(
              `Geometry type '${geometry.type}' is not supported. Expected Polygon or MultiPolygon.`
            );
            return;
          }

          onOverrideStart({
            simulation_id: simulationId,
            perimeter_geojson: geometry as GeoJSON.Geometry,
            duration_hours: durationHours,
            snapshot_interval_minutes: snapshotMinutes,
          });
        } catch {
          setParseError("Failed to parse GeoJSON. Ensure the file is valid JSON.");
        } finally {
          if (fileRef.current) fileRef.current.value = "";
        }
      };
      reader.readAsText(file);
    },
    [simulationId, durationHours, snapshotMinutes, onOverrideStart]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) processFile(file);
    },
    [processFile]
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) processFile(file);
    },
    [processFile]
  );

  const disabled = !simulationId || isRunning;

  return (
    <div className="panel recon-panel">
      <h3 className="recon-title">RPAS Recon Override</h3>

      {!simulationId ? (
        <p className="recon-hint">Run a simulation first to enable perimeter correction.</p>
      ) : (
        <>
          <p className="recon-hint">
            Import drone-observed fire perimeter (M4TD GeoJSON) to correct the
            simulated front and re-run spread from ground truth.
          </p>

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

          <div
            className={`recon-drop${dragOver ? " drag-over" : ""}${disabled ? " recon-drop-disabled" : ""}`}
            onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={disabled ? undefined : handleDrop}
            onClick={() => !disabled && fileRef.current?.click()}
            role="button"
            tabIndex={disabled ? -1 : 0}
            onKeyDown={(e) => { if (e.key === "Enter" && !disabled) fileRef.current?.click(); }}
            aria-label="Drop GeoJSON perimeter file or click to browse"
          >
            {isRunning ? (
              <span className="recon-drop-text">Override running…</span>
            ) : (
              <>
                <span className="recon-drop-icon">📡</span>
                <span className="recon-drop-text">
                  Drop M4TD perimeter (.geojson) or click to browse
                </span>
              </>
            )}
          </div>

          <input
            ref={fileRef}
            type="file"
            accept=".geojson,.json"
            style={{ display: "none" }}
            onChange={handleFileChange}
            disabled={disabled}
          />

          {parseError && <div className="recon-error">{parseError}</div>}
        </>
      )}
    </div>
  );
}
