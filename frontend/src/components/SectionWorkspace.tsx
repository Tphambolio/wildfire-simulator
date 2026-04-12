/**
 * SectionWorkspace — per-ICS-section roster, responsibilities, and form launcher.
 * Rendered inside EOCConsole when the user selects Command/Operations/Planning/Logistics/Finance tab.
 */

import { useState, useCallback } from "react";
import type {
  ICSSection,
  IncidentResource,
  IncidentAgency,
} from "../types/incident";
import {
  ICS_SECTION_META,
  ICS_POSITIONS_BY_SECTION,
} from "../types/incident";
import type { ICSFormId } from "./EOCConsole";

// ── Section responsibilities + form assignments ───────────────────────────────

interface SectionDef {
  responsibilities: string[];
  forms: ICSFormId[];
  formLabels: Partial<Record<ICSFormId, string>>;
}

const SECTION_DEFS: Record<Exclude<ICSSection, "other">, SectionDef> = {
  command: {
    responsibilities: [
      "Establish incident objectives and priorities",
      "Approve the Incident Action Plan (IAP)",
      "Establish Unified Command if multi-agency",
      "Authorize public information releases",
      "Ensure safety standards are met (Safety Officer)",
      "Maintain agency liaison and coordination",
    ],
    forms: ["ics201", "ics202", "ics208"],
    formLabels: {
      ics201: "ICS-201 Incident Briefing",
      ics202: "ICS-202 Incident Objectives",
      ics208: "ICS-208 Safety Message",
    },
  },
  operations: {
    responsibilities: [
      "Direct all tactical operations",
      "Implement the approved IAP",
      "Manage division/branch assignments",
      "Request resources through Planning Section",
      "Report situation status to Planning Section",
      "Coordinate air operations if applicable",
    ],
    forms: ["ics204"],
    formLabels: {
      ics204: "ICS-204 Assignment List",
    },
  },
  planning: {
    responsibilities: [
      "Draft incident objectives (ICS-202) for IC approval",
      "Prepare Organization Assignment List (ICS-203)",
      "Compile Org Chart from all sections (ICS-207)",
      "Track and manage resource status (ICS-215)",
      "Maintain situation unit and map products",
      "Develop demobilization plan",
    ],
    forms: ["ics202", "ics203", "ics207", "ics215", "ics215a"],
    formLabels: {
      ics202: "ICS-202 Objectives",
      ics203: "ICS-203 Organization List",
      ics207: "ICS-207 Org Chart",
      ics215: "ICS-215 Resource Needs",
      ics215a: "ICS-215A Safety Analysis",
    },
  },
  logistics: {
    responsibilities: [
      "Establish communications plan (ICS-205)",
      "Coordinate medical support for responders (ICS-206)",
      "Manage supply, facilities, and ground support",
      "Coordinate food and service branch",
      "Track incoming and outgoing resources",
    ],
    forms: ["ics205", "ics206"],
    formLabels: {
      ics205: "ICS-205 Comms Plan",
      ics206: "ICS-206 Medical Plan",
    },
  },
  finance: {
    responsibilities: [
      "Track all incident costs and expenditures",
      "Maintain personnel time records (ICS-214)",
      "Process procurement and contractor invoices",
      "Handle compensation and claims",
      "Prepare cost summaries for IC",
    ],
    forms: ["ics213", "ics214"],
    formLabels: {
      ics213: "ICS-213 General Message",
      ics214: "ICS-214 Activity Log",
    },
  },
};

// ── Additional role options per section ───────────────────────────────────────

const ADDITIONAL_ROLES_BY_SECTION: Record<ICSSection, string[]> = {
  command: [
    "Acting Incident Commander",
    "IC Trainee",
    "Senior Advisor",
    "Agency Representative",
    "Intelligence / Investigations Officer",
    "Recovery Coordinator",
  ],
  operations: [
    "Deputy Operations Section Chief",
    "Strike Team Leader",
    "Task Force Leader",
    "Single Resource Boss",
    "Entry Team Leader",
    "Rescue Group Supervisor",
    "Evacuation Group Supervisor",
    "Air Tactical Group Supervisor",
    "Helicopter Coordinator",
    "Dozer Group Supervisor",
  ],
  planning: [
    "Deputy Planning Section Chief",
    "GIS / Mapping Specialist",
    "Intelligence Officer",
    "Fire Behaviour Analyst",
    "Environmental Unit Leader",
    "Technical Specialist – Hazmat",
    "Technical Specialist – Structure",
    "Technical Specialist – Medical",
    "Technical Specialist – Water",
  ],
  logistics: [
    "Deputy Logistics Section Chief",
    "Radio Operator / COML",
    "Information Technology Specialist",
    "Incident Dispatcher",
    "Temporary Flight Restriction Coordinator",
    "Fuel Unit Leader",
  ],
  finance: [
    "Deputy Finance/Admin Section Chief",
    "Claims Specialist",
    "Property Management Officer",
  ],
  other: [],
};

// ── Inline resource form ──────────────────────────────────────────────────────

const STATUS_LABELS: Record<IncidentResource["status"], string> = {
  available: "Available",
  assigned: "Assigned",
  released: "Released",
  oos: "Out of Service",
};

interface ResourceFormProps {
  draft: Partial<IncidentResource>;
  section: ICSSection;
  onChange: (d: Partial<IncidentResource>) => void;
  onSave: () => void;
  onCancel: () => void;
}

function InlineResourceForm({ draft, section, onChange, onSave, onCancel }: ResourceFormProps) {
  const set = (partial: Partial<IncidentResource>) => onChange({ ...draft, ...partial });
  const positions = ICS_POSITIONS_BY_SECTION[section];
  const additionalRoles = ADDITIONAL_ROLES_BY_SECTION[section];
  const isPerson = (draft.kind ?? "person") === "person";

  // Track whether the role field is in "other" (free text) mode
  const isOtherRole = !!draft.role && !additionalRoles.includes(draft.role);
  const [showRoleInput, setShowRoleInput] = useState(isOtherRole);

  return (
    <div className="sw-inline-form">
      <div className="sw-form-row">
        <label>Kind
          <select value={draft.kind ?? "person"} onChange={(e) => set({ kind: e.target.value as IncidentResource["kind"], icsPosition: undefined })}>
            <option value="person">Person</option>
            <option value="equipment">Equipment</option>
            <option value="vehicle">Vehicle</option>
          </select>
        </label>
        <label>Status
          <select value={draft.status ?? "available"} onChange={(e) => set({ status: e.target.value as IncidentResource["status"] })}>
            {Object.entries(STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <label>Name
        <input type="text" value={draft.name ?? ""} onChange={(e) => set({ name: e.target.value })}
          placeholder={isPerson ? "Last, First" : "Unit / designation"} maxLength={60} />
      </label>
      <label>Agency
        <input type="text" value={draft.agency ?? ""} onChange={(e) => set({ agency: e.target.value })}
          placeholder="EPS, AHS, EFD…" maxLength={40} />
      </label>
      {isPerson && positions.length > 0 && (
        <label>ICS Position
          <select value={draft.icsPosition ?? ""} onChange={(e) => set({ icsPosition: e.target.value || undefined })}>
            <option value="">— Select position —</option>
            {positions.map((p) => <option key={p} value={p}>{p}</option>)}
            <option value="other">Other / Not listed</option>
          </select>
        </label>
      )}
      {isPerson && additionalRoles.length > 0 && (
        <label>Additional Role (optional)
          <select
            value={showRoleInput ? "__other__" : (draft.role ?? "")}
            onChange={(e) => {
              if (e.target.value === "__other__") {
                setShowRoleInput(true);
                set({ role: undefined });
              } else {
                setShowRoleInput(false);
                set({ role: e.target.value || undefined });
              }
            }}
          >
            <option value="">— None —</option>
            {additionalRoles.map((r) => <option key={r} value={r}>{r}</option>)}
            <option value="__other__">Other (type below)…</option>
          </select>
        </label>
      )}
      {isPerson && showRoleInput && (
        <label>Specify Role
          <input type="text" value={draft.role ?? ""} onChange={(e) => set({ role: e.target.value || undefined })}
            placeholder="Free-text title" maxLength={60} autoFocus />
        </label>
      )}
      {!isPerson && (
        <div className="sw-form-row">
          <label>NIMS Type
            <select value={draft.typeRating ?? ""} onChange={(e) => set({ typeRating: e.target.value as IncidentResource["typeRating"] || undefined })}>
              <option value="">—</option>
              {(["T1","T2","T3","T4","T5"] as const).map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>Division
            <input type="text" value={draft.assignedDivision ?? ""} onChange={(e) => set({ assignedDivision: e.target.value || undefined })}
              placeholder="A, B, Alpha…" maxLength={20} />
          </label>
        </div>
      )}
      <div className="sw-form-actions">
        <button className="btn-primary" style={{ padding: "5px 14px", fontSize: "0.85em" }} onClick={onSave}
          disabled={!(draft.name ?? "").trim()}>Save</button>
        <button className="btn-secondary" style={{ padding: "5px 10px", fontSize: "0.85em" }} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

// ── Roster position row ───────────────────────────────────────────────────────

const STATUS_COLORS: Record<IncidentResource["status"], string> = {
  available: "#4caf50",
  assigned: "#2196f3",
  released: "#9e9e9e",
  oos: "#f44336",
};

// ── Main component ────────────────────────────────────────────────────────────

interface SectionWorkspaceProps {
  section: ICSSection;
  resources: IncidentResource[];
  agencies: IncidentAgency[];
  onResourcesChange: (r: IncidentResource[]) => void;
  onAgenciesChange: (a: IncidentAgency[]) => void;
  onGenerateForm: (formId: ICSFormId) => void;
}

export default function SectionWorkspace({
  section,
  resources,
  agencies,
  onResourcesChange,
  onGenerateForm,
}: SectionWorkspaceProps) {
  const meta = ICS_SECTION_META[section];
  const def = section !== "other" ? SECTION_DEFS[section as Exclude<ICSSection, "other">] : null;
  const positions = ICS_POSITIONS_BY_SECTION[section];

  const sectionResources = resources.filter((r) => r.icsSection === section);
  const personnel = sectionResources.filter((r) => r.kind === "person");
  const equipment = sectionResources.filter((r) => r.kind !== "person");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Partial<IncidentResource> | null>(null);
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});

  // UC agencies for command section
  const ucAgencies = agencies.filter((a) => a.isUnifiedCommand);
  const nonUcAgencies = agencies.filter((a) => !a.isUnifiedCommand);

  const startAdd = (prePosition?: string) => {
    setEditingId("__new__");
    setDraft({
      id: crypto.randomUUID(),
      kind: "person",
      icsSection: section,
      icsPosition: prePosition,
      name: "",
      agency: "",
      status: "available",
    });
  };

  const startEdit = (r: IncidentResource) => {
    setEditingId(r.id);
    setDraft({ ...r });
  };

  const saveResource = useCallback(() => {
    if (!draft || !(draft.name ?? "").trim()) return;
    const complete: IncidentResource = {
      id: draft.id ?? crypto.randomUUID(),
      kind: draft.kind ?? "person",
      icsSection: section,
      icsPosition: draft.icsPosition,
      name: draft.name ?? "",
      role: draft.role,
      agency: draft.agency ?? "",
      typeRating: draft.typeRating,
      status: draft.status ?? "available",
      assignedDivision: draft.assignedDivision,
      notes: draft.notes,
    };
    const exists = resources.find((r) => r.id === complete.id);
    if (exists) {
      onResourcesChange(resources.map((r) => (r.id === complete.id ? complete : r)));
    } else {
      onResourcesChange([...resources, complete]);
    }
    setEditingId(null);
    setDraft(null);
  }, [draft, resources, section, onResourcesChange]);

  const removeResource = (id: string) => onResourcesChange(resources.filter((r) => r.id !== id));

  const toggleCheck = (item: string) =>
    setChecklist((prev) => ({ ...prev, [item]: !prev[item] }));

  const kindIcon = (k: IncidentResource["kind"]) =>
    k === "person" ? "👤" : k === "vehicle" ? "🚗" : "🔧";

  return (
    <div className="sw-root">
      {/* ── Section header ───────────────────────────────────── */}
      <div className="sw-header" style={{ borderLeftColor: meta.color }}>
        <span className="sw-section-pill" style={{ background: meta.color }}>
          {meta.abbrev}
        </span>
        <span className="sw-section-title">{meta.label}</span>
        <button className="btn-secondary sw-add-btn" onClick={() => startAdd()}>+ Add Person</button>
        <button className="btn-secondary sw-add-btn" onClick={() => {
          setEditingId("__new__");
          setDraft({ id: crypto.randomUUID(), kind: "equipment", icsSection: section, name: "", agency: "", status: "available" });
        }}>+ Equipment</button>
      </div>

      {/* ── Add/edit form ────────────────────────────────────── */}
      {editingId && draft && (
        <InlineResourceForm
          draft={draft}
          section={section}
          onChange={setDraft}
          onSave={saveResource}
          onCancel={() => { setEditingId(null); setDraft(null); }}
        />
      )}

      <div className="sw-body">
        {/* ── Unified Command block (Command section only) ──── */}
        {section === "command" && (
          <div className="sw-panel">
            <div className="sw-panel-title">Unified Command</div>
            {ucAgencies.length > 0
              ? ucAgencies.map((a) => (
                  <div key={a.id} className="sw-uc-row">
                    <span style={{ color: "#ffd54f" }}>★</span>
                    <span className="sw-uc-name">{a.name}</span>
                    <span className="sw-uc-liaison">{a.liaison}</span>
                  </div>
                ))
              : <p className="hint">No unified command agencies. Add agencies in the Agencies panel.</p>}
            {nonUcAgencies.length > 0 && (
              <div className="sw-uc-supporting">
                <span className="sw-panel-sub">Supporting Agencies: </span>
                {nonUcAgencies.map((a) => a.name).join(" · ")}
              </div>
            )}
          </div>
        )}

        {/* ── Position roster ──────────────────────────────── */}
        {positions.length > 0 && (
          <div className="sw-panel">
            <div className="sw-panel-title">Section Roster — Personnel</div>
            <div className="sw-position-list">
              {positions.map((pos) => {
                const assigned = personnel.find((r) => r.icsPosition === pos);
                return (
                  <div key={pos} className={`sw-position-row${assigned ? "" : " sw-position-row--empty"}`}>
                    <span className="sw-pos-label">{pos}</span>
                    {assigned ? (
                      <>
                        <span className="sw-pos-name">{assigned.name}</span>
                        <span className="sw-pos-agency">{assigned.agency}</span>
                        <span className="sw-status-dot" style={{ background: STATUS_COLORS[assigned.status] }} title={STATUS_LABELS[assigned.status]} />
                        <button className="sw-row-btn" onClick={() => startEdit(assigned)}>Edit</button>
                        <button className="sw-row-btn sw-row-btn--danger" onClick={() => removeResource(assigned.id)}>✕</button>
                      </>
                    ) : (
                      <>
                        <span className="sw-pos-empty">Unassigned</span>
                        <button className="sw-row-btn sw-row-btn--assign" onClick={() => startAdd(pos)}>+ Assign</button>
                      </>
                    )}
                  </div>
                );
              })}
              {/* Personnel not matched to a canonical position */}
              {personnel.filter((r) => !positions.includes(r.icsPosition ?? "")).map((r) => (
                <div key={r.id} className="sw-position-row">
                  <span className="sw-pos-label">{r.icsPosition ?? r.role ?? "—"}</span>
                  <span className="sw-pos-name">{r.name}</span>
                  <span className="sw-pos-agency">{r.agency}</span>
                  <span className="sw-status-dot" style={{ background: STATUS_COLORS[r.status] }} title={STATUS_LABELS[r.status]} />
                  <button className="sw-row-btn" onClick={() => startEdit(r)}>Edit</button>
                  <button className="sw-row-btn sw-row-btn--danger" onClick={() => removeResource(r.id)}>✕</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Equipment / vehicles ─────────────────────────── */}
        {equipment.length > 0 && (
          <div className="sw-panel">
            <div className="sw-panel-title">Equipment &amp; Vehicles</div>
            {equipment.map((r) => (
              <div key={r.id} className="sw-position-row">
                <span style={{ marginRight: 6 }}>{kindIcon(r.kind)}</span>
                <span className="sw-pos-name">{r.name}</span>
                <span className="sw-pos-agency">{r.agency}{r.typeRating ? ` · ${r.typeRating}` : ""}</span>
                <span className="sw-status-dot" style={{ background: STATUS_COLORS[r.status] }} title={STATUS_LABELS[r.status]} />
                <button className="sw-row-btn" onClick={() => startEdit(r)}>Edit</button>
                <button className="sw-row-btn sw-row-btn--danger" onClick={() => removeResource(r.id)}>✕</button>
              </div>
            ))}
          </div>
        )}

        {/* ── Responsibilities + forms ─────────────────────── */}
        {def && (
          <>
            <div className="sw-panel">
              <div className="sw-panel-title">Section IAP Responsibilities</div>
              <ul className="sw-checklist">
                {def.responsibilities.map((item) => (
                  <li
                    key={item}
                    className={`sw-check-item${checklist[item] ? " sw-check-item--done" : ""}`}
                    onClick={() => toggleCheck(item)}
                  >
                    <span className="sw-check-icon">{checklist[item] ? "✓" : "○"}</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            <div className="sw-panel">
              <div className="sw-panel-title">Section Forms</div>
              <div className="sw-form-btns">
                {def.forms.map((formId) => (
                  <button
                    key={formId}
                    className="sw-form-btn"
                    onClick={() => onGenerateForm(formId)}
                  >
                    {def.formLabels[formId] ?? formId.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        {sectionResources.length === 0 && !editingId && (
          <p className="hint" style={{ padding: "0 4px" }}>
            No one assigned to this section yet. Use "+ Add Person" to assign section members.
          </p>
        )}
      </div>
    </div>
  );
}
