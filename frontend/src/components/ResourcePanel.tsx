/** NIMS resource tracking — personnel + equipment roster. Feeds ICS-203/204. */

import { useState, useCallback } from "react";

export interface Resource {
  id: string;
  kind: "person" | "equipment" | "vehicle";
  name: string;
  role?: string;
  agency: string;
  typeRating?: "T1" | "T2" | "T3" | "T4" | "T5";
  status: "available" | "assigned" | "released" | "oos";
  assignedDivision?: string;
  notes?: string;
}

const ICS_POSITIONS = [
  "Incident Commander",
  "Deputy IC",
  "Safety Officer",
  "Public Information Officer",
  "Liaison Officer",
  "Operations Section Chief",
  "Planning Section Chief",
  "Logistics Section Chief",
  "Finance/Admin Section Chief",
  "Division Supervisor",
  "Group Supervisor",
  "Branch Director",
  "Medical Unit Leader",
  "Communications Unit Leader",
  "Resources Unit Leader",
  "Documentation Unit Leader",
  "Demobilization Unit Leader",
  "Situation Unit Leader",
];

const STATUS_LABELS: Record<Resource["status"], string> = {
  available: "Available",
  assigned: "Assigned",
  released: "Released",
  oos: "Out of Service",
};

const STATUS_COLORS: Record<Resource["status"], string> = {
  available: "#4caf50",
  assigned: "#2196f3",
  released: "#9e9e9e",
  oos: "#f44336",
};

function makeResource(kind: Resource["kind"]): Resource {
  return {
    id: crypto.randomUUID(),
    kind,
    name: "",
    role: "",
    agency: "",
    status: "available",
  };
}

interface ResourceFormProps {
  resource: Resource;
  onChange: (r: Resource) => void;
  onSave: () => void;
  onCancel: () => void;
}

function ResourceForm({ resource, onChange, onSave, onCancel }: ResourceFormProps) {
  const set = (partial: Partial<Resource>) => onChange({ ...resource, ...partial });

  return (
    <div className="resource-form">
      <div className="resource-form-row">
        <label>
          Name
          <input
            type="text"
            value={resource.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder={resource.kind === "person" ? "Last, First" : "Unit designation"}
            maxLength={60}
          />
        </label>
        <label>
          Agency
          <input
            type="text"
            value={resource.agency}
            onChange={(e) => set({ agency: e.target.value })}
            placeholder="EPS, AHS, EFD…"
            maxLength={40}
          />
        </label>
      </div>
      {resource.kind === "person" && (
        <label>
          ICS Role
          <select value={resource.role ?? ""} onChange={(e) => set({ role: e.target.value })}>
            <option value="">— Select position —</option>
            {ICS_POSITIONS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
            <option value="Other">Other</option>
          </select>
        </label>
      )}
      <div className="resource-form-row">
        <label>
          NIMS Type
          <select value={resource.typeRating ?? ""} onChange={(e) => set({ typeRating: e.target.value as Resource["typeRating"] || undefined })}>
            <option value="">—</option>
            {(["T1", "T2", "T3", "T4", "T5"] as const).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label>
          Division
          <input
            type="text"
            value={resource.assignedDivision ?? ""}
            onChange={(e) => set({ assignedDivision: e.target.value || undefined })}
            placeholder="A, B, Alpha…"
            maxLength={20}
          />
        </label>
        <label>
          Status
          <select value={resource.status} onChange={(e) => set({ status: e.target.value as Resource["status"] })}>
            {Object.entries(STATUS_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Notes
        <input
          type="text"
          value={resource.notes ?? ""}
          onChange={(e) => set({ notes: e.target.value || undefined })}
          placeholder="Special skills, equipment specs…"
          maxLength={120}
        />
      </label>
      <div className="resource-form-actions">
        <button className="btn-primary" style={{ padding: "5px 14px", fontSize: "0.85em" }} onClick={onSave}>
          Save
        </button>
        <button className="btn-secondary" style={{ padding: "5px 10px", fontSize: "0.85em" }} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

interface ResourcePanelProps {
  resources: Resource[];
  onChange: (resources: Resource[]) => void;
}

export default function ResourcePanel({ resources, onChange }: ResourcePanelProps) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState<Resource | null>(null);
  const [filterStatus, setFilterStatus] = useState<Resource["status"] | "all">("all");

  const addNew = (kind: Resource["kind"]) => {
    setEditing(makeResource(kind));
  };

  const saveEditing = useCallback(() => {
    if (!editing || !editing.name.trim()) return;
    const exists = resources.find((r) => r.id === editing.id);
    if (exists) {
      onChange(resources.map((r) => (r.id === editing.id ? editing : r)));
    } else {
      onChange([...resources, editing]);
    }
    setEditing(null);
  }, [editing, resources, onChange]);

  const remove = (id: string) => onChange(resources.filter((r) => r.id !== id));

  const filtered = filterStatus === "all" ? resources : resources.filter((r) => r.status === filterStatus);

  const kindIcon = (k: Resource["kind"]) => k === "person" ? "👤" : k === "vehicle" ? "🚗" : "🔧";

  return (
    <div className="panel resource-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Resources</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {resources.length > 0 && (
          <span className="scenario-count-badge">{resources.length}</span>
        )}
      </button>

      {open && (
        <div className="scenario-body">
          {/* Add buttons */}
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <button className="btn-secondary" style={{ flex: 1, fontSize: "0.82em", padding: "5px 0" }} onClick={() => addNew("person")}>
              + Person
            </button>
            <button className="btn-secondary" style={{ flex: 1, fontSize: "0.82em", padding: "5px 0" }} onClick={() => addNew("equipment")}>
              + Equipment
            </button>
            <button className="btn-secondary" style={{ flex: 1, fontSize: "0.82em", padding: "5px 0" }} onClick={() => addNew("vehicle")}>
              + Vehicle
            </button>
          </div>

          {/* Edit form */}
          {editing && (
            <ResourceForm
              resource={editing}
              onChange={setEditing}
              onSave={saveEditing}
              onCancel={() => setEditing(null)}
            />
          )}

          {/* Filter */}
          {resources.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value as typeof filterStatus)}
                style={{ fontSize: "0.82em", padding: "3px 6px", background: "#1a2540", color: "#ccc", border: "1px solid #445", borderRadius: 4 }}
              >
                <option value="all">All ({resources.length})</option>
                {Object.entries(STATUS_LABELS).map(([k, v]) => {
                  const count = resources.filter((r) => r.status === k).length;
                  return count > 0 ? <option key={k} value={k}>{v} ({count})</option> : null;
                })}
              </select>
            </div>
          )}

          {/* Resource list */}
          {filtered.length === 0 ? (
            <div className="hint">No resources yet.</div>
          ) : (
            <div className="resource-list">
              {filtered.map((r) => (
                <div key={r.id} className="resource-item">
                  <div className="resource-item-header">
                    <span className="resource-kind-icon">{kindIcon(r.kind)}</span>
                    <span className="resource-name">{r.name}</span>
                    <span
                      className="resource-status-badge"
                      style={{ background: STATUS_COLORS[r.status] + "33", color: STATUS_COLORS[r.status], border: `1px solid ${STATUS_COLORS[r.status]}55` }}
                    >
                      {STATUS_LABELS[r.status]}
                    </span>
                  </div>
                  <div className="resource-item-meta">
                    {[r.role, r.agency, r.typeRating, r.assignedDivision && `Div. ${r.assignedDivision}`]
                      .filter(Boolean).join(" · ")}
                  </div>
                  <div className="resource-item-actions">
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.78em", padding: "2px 7px" }}
                      onClick={() => setEditing({ ...r })}
                    >
                      Edit
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.78em", padding: "2px 7px", borderColor: "#8b2020", color: "#e57373" }}
                      onClick={() => remove(r.id)}
                    >
                      Remove
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
