/** Participating agencies list — feeds ICS-202 command section and ICS-203 org section. */

import { useState, useCallback } from "react";

export interface Agency {
  id: string;
  name: string;
  role: string;
  liaison: string;
  phone: string;
  isUnifiedCommand: boolean;
}

const AGENCY_ROLES = [
  "Fire",
  "Law Enforcement",
  "Emergency Medical Services",
  "Public Works",
  "Emergency Management",
  "Public Health",
  "Utilities",
  "Transportation",
  "Military / National Guard",
  "Federal",
  "Non-Governmental Organization",
  "Other",
];

function makeAgency(): Agency {
  return {
    id: crypto.randomUUID(),
    name: "",
    role: "",
    liaison: "",
    phone: "",
    isUnifiedCommand: false,
  };
}

interface AgencyFormProps {
  agency: Agency;
  onChange: (a: Agency) => void;
  onSave: () => void;
  onCancel: () => void;
}

function AgencyForm({ agency, onChange, onSave, onCancel }: AgencyFormProps) {
  const set = (partial: Partial<Agency>) => onChange({ ...agency, ...partial });

  return (
    <div className="resource-form">
      <div className="resource-form-row">
        <label>
          Agency Name
          <input
            type="text"
            value={agency.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Edmonton Fire Rescue Services"
            maxLength={80}
          />
        </label>
        <label>
          Role
          <select value={agency.role} onChange={(e) => set({ role: e.target.value })}>
            <option value="">— Select role —</option>
            {AGENCY_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>
      <div className="resource-form-row">
        <label>
          Liaison / Contact
          <input
            type="text"
            value={agency.liaison}
            onChange={(e) => set({ liaison: e.target.value })}
            placeholder="Last, First"
            maxLength={60}
          />
        </label>
        <label>
          Phone
          <input
            type="tel"
            value={agency.phone}
            onChange={(e) => set({ phone: e.target.value })}
            placeholder="780-555-0100"
            maxLength={20}
          />
        </label>
      </div>
      <label style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
        <input
          type="checkbox"
          checked={agency.isUnifiedCommand}
          onChange={(e) => set({ isUnifiedCommand: e.target.checked })}
          style={{ width: "auto" }}
        />
        <span>Unified Command member</span>
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

interface AgencyPanelProps {
  agencies: Agency[];
  onChange: (agencies: Agency[]) => void;
}

export default function AgencyPanel({ agencies, onChange }: AgencyPanelProps) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState<Agency | null>(null);

  const saveEditing = useCallback(() => {
    if (!editing || !editing.name.trim()) return;
    const exists = agencies.find((a) => a.id === editing.id);
    if (exists) {
      onChange(agencies.map((a) => (a.id === editing.id ? editing : a)));
    } else {
      onChange([...agencies, editing]);
    }
    setEditing(null);
  }, [editing, agencies, onChange]);

  const remove = (id: string) => onChange(agencies.filter((a) => a.id !== id));
  const ucAgencies = agencies.filter((a) => a.isUnifiedCommand);

  return (
    <div className="panel agency-panel">
      <button
        className="panel-collapse-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>Agencies</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {agencies.length > 0 && (
          <span className="scenario-count-badge">{agencies.length}</span>
        )}
      </button>

      {open && (
        <div className="scenario-body">
          <button
            className="btn-secondary"
            style={{ width: "100%", marginBottom: 8, padding: "5px 0", fontSize: "0.85em" }}
            onClick={() => setEditing(makeAgency())}
          >
            + Add Agency
          </button>

          {editing && (
            <AgencyForm
              agency={editing}
              onChange={setEditing}
              onSave={saveEditing}
              onCancel={() => setEditing(null)}
            />
          )}

          {ucAgencies.length > 0 && (
            <div className="hint" style={{ marginBottom: 4, color: "#81c784" }}>
              Unified Command: {ucAgencies.map((a) => a.name).join(", ")}
            </div>
          )}

          {agencies.length === 0 ? (
            <div className="hint">No agencies added.</div>
          ) : (
            <div className="resource-list">
              {agencies.map((a) => (
                <div key={a.id} className="resource-item">
                  <div className="resource-item-header">
                    <span className="resource-name">
                      {a.isUnifiedCommand && <span style={{ color: "#ffd54f", marginRight: 4 }}>★</span>}
                      {a.name}
                    </span>
                    {a.role && (
                      <span className="resource-status-badge" style={{ background: "#1e3a5533", color: "#90caf9", border: "1px solid #1e3a5555" }}>
                        {a.role}
                      </span>
                    )}
                  </div>
                  {(a.liaison || a.phone) && (
                    <div className="resource-item-meta">
                      {[a.liaison, a.phone].filter(Boolean).join(" · ")}
                    </div>
                  )}
                  <div className="resource-item-actions">
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.78em", padding: "2px 7px" }}
                      onClick={() => setEditing({ ...a })}
                    >
                      Edit
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.78em", padding: "2px 7px", borderColor: "#8b2020", color: "#e57373" }}
                      onClick={() => remove(a.id)}
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
