/** Scenario save/load panel — persist named simulation configs for pre-incident planning. */

import { memo, useRef, useState } from "react";
import type { ScenarioConfig } from "../types/simulation";

interface ScenarioPanelProps {
  scenarios: ScenarioConfig[];
  currentConfig: Omit<ScenarioConfig, "id" | "createdAt" | "name" | "description">;
  onSave: (config: Omit<ScenarioConfig, "id" | "createdAt">) => void;
  onLoad: (scenario: ScenarioConfig) => void;
  onDelete: (id: string) => void;
  onExport: (scenario: ScenarioConfig) => void;
  onImport: (file: File) => Promise<ScenarioConfig>;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ScenarioPanel({
  scenarios,
  currentConfig,
  onSave,
  onLoad,
  onDelete,
  onExport,
  onImport,
}: ScenarioPanelProps) {
  const [open, setOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveDesc, setSaveDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSave = () => {
    const name = saveName.trim();
    if (!name) return;
    setSaving(true);
    onSave({ ...currentConfig, name, description: saveDesc.trim() || undefined });
    setSaveName("");
    setSaveDesc("");
    setSaving(false);
  };

  const handleImportClick = () => {
    setImportError(null);
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await onImport(file);
      setImportError(null);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Import failed");
    }
    e.target.value = "";
  };

  const hasIgnition = !!currentConfig.ignitionPoint;

  return (
    <div className="panel scenario-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Scenarios</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {scenarios.length > 0 && (
          <span className="scenario-count-badge">{scenarios.length}</span>
        )}
      </button>

      {open && (
        <div className="scenario-body">
          {/* Save current config */}
          <div className="section" style={{ paddingTop: 0 }}>
            <h4>Save Current Config</h4>
            {!hasIgnition && (
              <div className="hint" style={{ color: "#e57373" }}>
                Set an ignition point before saving.
              </div>
            )}
            <input
              className="scenario-name-input"
              type="text"
              placeholder="Scenario name (e.g. Terwillegar Aug extreme)"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              maxLength={60}
              disabled={!hasIgnition}
            />
            <input
              className="scenario-name-input"
              type="text"
              placeholder="Location description (optional)"
              value={saveDesc}
              onChange={(e) => setSaveDesc(e.target.value)}
              maxLength={120}
              style={{ marginTop: 4 }}
              disabled={!hasIgnition}
            />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button
                className="btn-primary"
                style={{ flex: 1, padding: "6px 0", fontSize: "0.85em" }}
                onClick={handleSave}
                disabled={!hasIgnition || !saveName.trim() || saving || scenarios.length >= 10}
                title={scenarios.length >= 10 ? "Maximum 10 scenarios — delete one first" : "Save current simulation config"}
              >
                Save Scenario
              </button>
              <button
                className="btn-secondary"
                style={{ padding: "6px 10px", fontSize: "0.85em" }}
                onClick={handleImportClick}
                title="Import scenario from JSON file"
              >
                Import
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                style={{ display: "none" }}
                onChange={handleFileChange}
              />
            </div>
            {importError && (
              <div className="hint" style={{ color: "#e57373", marginTop: 4 }}>
                {importError}
              </div>
            )}
            {scenarios.length >= 10 && (
              <div className="hint" style={{ color: "#ffb74d", marginTop: 4 }}>
                Limit reached (10). Delete a scenario to save new ones.
              </div>
            )}
          </div>

          {/* Saved scenarios list */}
          {scenarios.length === 0 ? (
            <div className="hint" style={{ marginTop: 4 }}>
              No saved scenarios yet.
            </div>
          ) : (
            <div className="scenario-list">
              {scenarios.map((s) => (
                <div key={s.id} className="scenario-item">
                  <div className="scenario-item-header">
                    <span className="scenario-item-name">{s.name}</span>
                    <span className="scenario-item-date">{formatDate(s.createdAt)}</span>
                  </div>
                  {s.description && (
                    <div className="scenario-item-desc">{s.description}</div>
                  )}
                  <div className="scenario-item-meta">
                    {s.ignitionPoint
                      ? `${s.ignitionPoint.lat.toFixed(3)}, ${s.ignitionPoint.lng.toFixed(3)}`
                      : "No ignition"}
                    {" · "}
                    {s.useEdmontonGrid ? "Edm grid" : s.useSyntheticCA ? "CA" : s.fuelType}
                    {" · "}
                    {s.simMode === "multiday"
                      ? `${s.multiDayDays.length}d multi`
                      : `${s.durationHours}h`}
                    {s.lastRunStats && (
                      <> · {s.lastRunStats.areaHa.toFixed(1)} ha</>
                    )}
                  </div>
                  <div className="scenario-item-actions">
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.8em", padding: "3px 8px" }}
                      onClick={() => onLoad(s)}
                      title="Restore this scenario config"
                    >
                      Load
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.8em", padding: "3px 8px" }}
                      onClick={() => onExport(s)}
                      title="Export scenario as JSON for sharing"
                    >
                      Export
                    </button>
                    <button
                      className="btn-secondary"
                      style={{
                        fontSize: "0.8em",
                        padding: "3px 8px",
                        borderColor: "#8b2020",
                        color: "#e57373",
                      }}
                      onClick={() => {
                        if (confirm(`Delete scenario "${s.name}"?`)) onDelete(s.id);
                      }}
                      title="Delete this scenario"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Re-render only when props change, not on every streamed frame
export default memo(ScenarioPanel);
