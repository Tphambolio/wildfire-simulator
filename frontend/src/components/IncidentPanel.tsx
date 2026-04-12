/** Incident save/load panel — manage multi-day operational incidents in the sidebar. */

import { useRef, useState } from "react";
import type { IncidentSession } from "../types/incident";

interface IncidentPanelProps {
  incidents: IncidentSession[];
  activeIncidentId: string | null;
  onCreate: (name: string) => void;
  onLoad: (id: string) => void;
  onClose: () => void;
  onDelete: (id: string) => void;
  onExport: (incident: IncidentSession) => void;
  onImport: (file: File) => Promise<IncidentSession>;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function IncidentPanel({
  incidents,
  activeIncidentId,
  onCreate,
  onLoad,
  onClose,
  onDelete,
  onExport,
  onImport,
}: IncidentPanelProps) {
  const [open, setOpen] = useState(true);
  const [newName, setNewName] = useState("");
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    onCreate(name);
    setNewName("");
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

  return (
    <div className="panel incident-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Incidents</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {incidents.length > 0 && (
          <span className="scenario-count-badge">{incidents.length}</span>
        )}
      </button>

      {open && (
        <div className="scenario-body">
          {/* New incident form */}
          <div className="section" style={{ paddingTop: 0 }}>
            <h4>New Incident</h4>
            <input
              className="scenario-name-input"
              type="text"
              placeholder="Incident name (e.g. River Valley Fire)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
              maxLength={60}
            />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button
                className="btn-primary"
                style={{ flex: 1, padding: "6px 0", fontSize: "0.85em" }}
                onClick={handleCreate}
                disabled={!newName.trim()}
              >
                Start Incident
              </button>
              <button
                className="btn-secondary"
                style={{ padding: "6px 10px", fontSize: "0.85em" }}
                onClick={handleImportClick}
                title="Import incident from JSON file"
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
          </div>

          {/* Saved incidents list */}
          {incidents.length === 0 ? (
            <div className="hint" style={{ marginTop: 4 }}>
              No incidents yet.
            </div>
          ) : (
            <div className="scenario-list">
              {incidents.map((inc) => {
                const isActive = inc.id === activeIncidentId;
                const dayCount = inc.operationalPeriods.length;
                return (
                  <div key={inc.id} className={`scenario-item${isActive ? " incident-item--active" : ""}`}>
                    <div className="scenario-item-header">
                      <span className="scenario-item-name">{inc.name}</span>
                      <span className={`incident-status-dot${inc.status === "active" ? " active" : ""}`}>
                        {inc.status === "active" ? "● ACTIVE" : "○ closed"}
                      </span>
                    </div>
                    <div className="scenario-item-meta">
                      {dayCount} day{dayCount !== 1 ? "s" : ""}
                      {" · "}{formatDate(inc.updatedAt)}
                    </div>
                    <div className="scenario-item-actions">
                      {isActive ? (
                        <button
                          className="btn-secondary"
                          style={{ fontSize: "0.8em", padding: "3px 8px", borderColor: "#8b2020", color: "#e57373" }}
                          onClick={onClose}
                          title="Close this incident (archive)"
                        >
                          Close
                        </button>
                      ) : (
                        <button
                          className="btn-secondary"
                          style={{ fontSize: "0.8em", padding: "3px 8px" }}
                          onClick={() => onLoad(inc.id)}
                          title="Resume this incident"
                        >
                          Resume
                        </button>
                      )}
                      <button
                        className="btn-secondary"
                        style={{ fontSize: "0.8em", padding: "3px 8px" }}
                        onClick={() => onExport(inc)}
                        title="Export incident as JSON"
                      >
                        Export
                      </button>
                      <button
                        className="btn-secondary"
                        style={{ fontSize: "0.8em", padding: "3px 8px", borderColor: "#8b2020", color: "#e57373" }}
                        onClick={() => {
                          if (confirm(`Delete incident "${inc.name}"? This cannot be undone.`)) onDelete(inc.id);
                        }}
                        title="Permanently delete this incident"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
