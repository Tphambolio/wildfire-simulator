/**
 * IAP Forms Dashboard — per-period form status cards with approve/reject workflow.
 *
 * Replaces the flat button list in the IAP tab with section-grouped status cards.
 * Each card shows form status (empty → draft → complete → approved), auto-populated
 * preparer name, and approve/reject actions. The iframe viewer is untouched — cards
 * just call onSelectForm() which triggers the existing iframe render.
 */

import { useState } from "react";
import type {
  IncidentSession,
  OperationalPeriod,
  ICSFormId,
  FormRecord,
  FormStatus,
  ResourceRequest,
  ICSSection,
} from "../types/incident";
import { HAZARD_DEFS } from "../types/incident";

interface IAPDashboardProps {
  incident: IncidentSession;
  activePeriod: OperationalPeriod;
  activePeriodIndex: number;
  selectedForm: ICSFormId | "";
  onSelectForm: (formId: ICSFormId) => void;
  onSelectRequest: (requestId: string) => void;
  onUpdateFormRecord: (periodIndex: number, formId: ICSFormId, patch: Partial<FormRecord>) => void;
  onAddResourceRequest: (req: Omit<ResourceRequest, "id" | "requestNumber" | "createdAt">) => void;
  onUpdateResourceRequest: (id: string, patch: Partial<ResourceRequest>) => void;
  onRemoveResourceRequest: (id: string) => void;
  onApproveIAP: (periodIndex: number, approverName: string, approverPosition: string) => void;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const ALWAYS_REQUIRED: ICSFormId[] = ["ics201", "ics202", "ics203"];

const PREPARER_MAP: Partial<Record<ICSFormId, { section: ICSSection; position: string } | null>> = {
  ics201: null,  // IC — handled specially
  ics202: null,
  ics203: { section: "planning",   position: "Planning Section Chief" },
  ics204: { section: "operations", position: "Operations Section Chief" },
  ics205: { section: "logistics",  position: "Logistics Section Chief" },
  ics205a:{ section: "logistics",  position: "Logistics Section Chief" },
  ics206: { section: "logistics",  position: "Logistics Section Chief" },
  ics207: { section: "planning",   position: "Planning Section Chief" },
  ics208: { section: "command",    position: "Safety Officer" },
  ics209: { section: "planning",   position: "Planning Section Chief" },
  ics211: { section: "logistics",  position: "Logistics Section Chief" },
  ics213: null,  // individual
  ics213rr: null,
  ics214: null,
  ics215: { section: "planning",   position: "Planning Section Chief" },
  ics215a:{ section: "planning",   position: "Planning Section Chief" },
};

const STATUS_LABEL: Record<FormStatus, string> = {
  empty:    "Not started",
  draft:    "In progress",
  complete: "Ready for approval",
  approved: "Approved",
  rejected: "Rejected",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function getPreparerDisplay(formId: ICSFormId, incident: IncidentSession): string {
  if (formId === "ics201" || formId === "ics202") {
    return incident.incidentCommanderName || "(unassigned)";
  }
  if (formId === "ics213" || formId === "ics213rr" || formId === "ics214") {
    return "(individual)";
  }
  const mapping = PREPARER_MAP[formId];
  if (!mapping) return "(unassigned)";
  const r = incident.resources.find(
    (res) => res.icsSection === mapping.section && res.icsPosition === mapping.position
  );
  return r?.name ?? "(unassigned)";
}

// ── FormCard ──────────────────────────────────────────────────────────────────

interface FormCardProps {
  formId: ICSFormId;
  label: string;
  record: FormRecord;
  isSelected: boolean;
  isRequired: boolean;
  isAlwaysApproved?: boolean;
  carryFromPeriod?: number | null;
  incident: IncidentSession;
  activePeriodIndex: number;
  onOpen: () => void;
  onUpdateRecord: (patch: Partial<FormRecord>) => void;
  onCarryForward: () => void;
}

function FormCard({
  formId, label, record, isSelected, isRequired, isAlwaysApproved,
  carryFromPeriod, incident, onOpen, onUpdateRecord, onCarryForward,
}: FormCardProps) {
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const [rejectNote, setRejectNote] = useState("");

  const preparerDisplay = getPreparerDisplay(formId, incident);
  const status = isAlwaysApproved ? "approved" : record.status;

  function handleApproveConfirm() {
    onUpdateRecord({
      status: "approved",
      approvedBy: incident.incidentCommanderName,
      approvedByPosition: "Incident Commander",
      approvedAt: new Date().toISOString(),
    });
    setConfirming(null);
  }

  function handleRejectConfirm() {
    onUpdateRecord({ status: "rejected", rejectionNote: rejectNote || "Requires revision" });
    setConfirming(null);
    setRejectNote("");
  }

  const approvedDate = record.approvedAt
    ? new Date(record.approvedAt).toLocaleDateString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div className={`iap-form-card iap-form-card--${status}${isSelected ? " iap-form-card--selected" : ""}`}>
      <div className="iap-card-title">
        <span>{label}</span>
        {isRequired && <span className="iap-required-badge">* req</span>}
      </div>

      {status !== "empty" && (
        <div className="iap-card-preparer">
          Preparer: {preparerDisplay}
          {status === "approved" && record.approvedByPosition && ` · Approved by ${record.approvedBy} (${record.approvedByPosition})${approvedDate ? ` · ${approvedDate}` : ""}`}
        </div>
      )}

      <div className={`iap-card-status iap-card-status--${status}`}>
        {status === "approved" ? `✓ ${STATUS_LABEL[status]}` :
         status === "rejected" ? `✕ ${STATUS_LABEL[status]}${record.rejectionNote ? ` — ${record.rejectionNote}` : ""}` :
         STATUS_LABEL[status]}
      </div>

      {status === "empty" && carryFromPeriod != null && (
        <span className="iap-carry-fwd" onClick={onCarryForward}>
          ↩ Carry from Period {carryFromPeriod + 1}
        </span>
      )}

      {confirming === "approve" && (
        <div className="iap-approve-confirm">
          <span>Sign off as {incident.incidentCommanderName || "IC"}?</span>
          <button className="iap-card-btn iap-card-btn--approve" onClick={handleApproveConfirm}>✓ Yes</button>
          <button className="iap-card-btn" onClick={() => setConfirming(null)}>✕ Cancel</button>
        </div>
      )}

      {confirming === "reject" && (
        <div className="iap-approve-confirm" style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
          <input
            className="iap-rr-input"
            placeholder="Rejection note (optional)"
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleRejectConfirm(); if (e.key === "Escape") setConfirming(null); }}
            autoFocus
          />
          <div style={{ display: "flex", gap: 4 }}>
            <button className="iap-card-btn iap-card-btn--reject" onClick={handleRejectConfirm}>✕ Reject</button>
            <button className="iap-card-btn" onClick={() => setConfirming(null)}>Cancel</button>
          </div>
        </div>
      )}

      {confirming === null && (
        <div className="iap-card-actions">
          <button className="iap-card-btn" onClick={onOpen}>Open</button>
          {!isAlwaysApproved && status === "draft" && (
            <button className="iap-card-btn" onClick={() => onUpdateRecord({ status: "complete" })}>Mark Complete</button>
          )}
          {!isAlwaysApproved && status === "complete" && (
            <>
              <button className="iap-card-btn iap-card-btn--approve" onClick={() => setConfirming("approve")}>Approve</button>
              <button className="iap-card-btn iap-card-btn--reject" onClick={() => setConfirming("reject")}>Reject</button>
            </>
          )}
          {!isAlwaysApproved && status === "approved" && (
            <button className="iap-card-btn" onClick={() => onUpdateRecord({ status: "draft", approvedBy: undefined, approvedAt: undefined })}>Revoke</button>
          )}
          {!isAlwaysApproved && status === "rejected" && (
            <button className="iap-card-btn" onClick={() => onUpdateRecord({ status: "complete", rejectionNote: undefined })}>Mark Complete</button>
          )}
        </div>
      )}
    </div>
  );
}

// ── ResourceRequestRow ────────────────────────────────────────────────────────

const PRIORITY_COLOR: Record<string, string> = {
  immediate: "var(--status-err)",
  urgent: "var(--status-warn)",
  routine: "var(--text-secondary)",
};

const RR_STATUS_LABEL: Record<string, string> = {
  pending: "Pending", ordered: "Ordered", filled: "Filled", cancelled: "Cancelled",
};

interface RRRowProps {
  req: ResourceRequest;
  onOpen: () => void;
  onUpdate: (patch: Partial<ResourceRequest>) => void;
  onRemove: () => void;
}

function ResourceRequestRow({ req, onOpen, onUpdate, onRemove }: RRRowProps) {
  return (
    <div className="iap-rr-row">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontWeight: 700, fontSize: 11 }}>{req.requestNumber} · {req.quantity}× {req.resourceKind}</span>
        <span style={{ fontSize: 10, color: PRIORITY_COLOR[req.priority] ?? "inherit" }}>{req.priority.toUpperCase()}</span>
      </div>
      <div className="iap-rr-row-meta">
        {req.resourceType && <span>{req.resourceType} · </span>}
        {req.deliveryLocation && <span>→ {req.deliveryLocation} · </span>}
        <span>{RR_STATUS_LABEL[req.status]}</span>
      </div>
      <div className="iap-rr-row-btns">
        <button className="iap-card-btn" onClick={onOpen}>Open Form</button>
        {req.status === "pending" && (
          <button className="iap-card-btn" onClick={() => onUpdate({ status: "ordered" })}>Mark Ordered</button>
        )}
        {req.status === "ordered" && (
          <button className="iap-card-btn iap-card-btn--approve" onClick={() => onUpdate({ status: "filled" })}>Mark Filled</button>
        )}
        {req.status !== "cancelled" && req.status !== "filled" && (
          <button className="iap-card-btn iap-card-btn--reject" onClick={() => onUpdate({ status: "cancelled" })}>Cancel</button>
        )}
        <button className="iap-card-btn" style={{ marginLeft: "auto" }} onClick={onRemove} title="Delete request">✕</button>
      </div>
    </div>
  );
}

// ── New Resource Request Form ─────────────────────────────────────────────────

interface NewRRFormProps {
  periodDay: number;
  onSubmit: (req: Omit<ResourceRequest, "id" | "requestNumber" | "createdAt">) => void;
  onCancel: () => void;
}

function NewRRForm({ periodDay, onSubmit, onCancel }: NewRRFormProps) {
  const [resourceKind, setResourceKind] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [priority, setPriority] = useState<"immediate" | "urgent" | "routine">("routine");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [deliveryTime, setDeliveryTime] = useState("");
  const [deliveryLocation, setDeliveryLocation] = useState("");
  const [section, setSection] = useState<ICSSection>("operations");
  const [notes, setNotes] = useState("");

  function handleSubmit() {
    if (!resourceKind.trim()) return;
    onSubmit({
      periodDay,
      requestedBy: "",
      requestedByPosition: "",
      requestedBySection: section,
      quantity: Math.max(1, parseInt(quantity) || 1),
      resourceKind: resourceKind.trim(),
      resourceType: resourceType.trim(),
      deliveryDate,
      deliveryTime,
      deliveryLocation: deliveryLocation.trim(),
      priority,
      status: "pending",
      notes: notes.trim() || undefined,
    });
  }

  return (
    <div className="iap-rr-new-form">
      <div className="iap-rr-new-form-row">
        <label>Resource Kind *
          <input value={resourceKind} onChange={(e) => setResourceKind(e.target.value)} placeholder="e.g. Engine Crew" autoFocus />
        </label>
        <label>Type
          <input value={resourceType} onChange={(e) => setResourceType(e.target.value)} placeholder="T1–T5" />
        </label>
        <label style={{ flex: "0 0 60px" }}>Qty
          <input type="number" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </label>
      </div>
      <div className="iap-rr-new-form-row">
        <label>Priority
          <select value={priority} onChange={(e) => setPriority(e.target.value as typeof priority)}>
            <option value="immediate">Immediate</option>
            <option value="urgent">Urgent</option>
            <option value="routine">Routine</option>
          </select>
        </label>
        <label>Requesting Section
          <select value={section} onChange={(e) => setSection(e.target.value as ICSSection)}>
            <option value="command">Command</option>
            <option value="operations">Operations</option>
            <option value="planning">Planning</option>
            <option value="logistics">Logistics</option>
            <option value="finance">Finance</option>
          </select>
        </label>
      </div>
      <div className="iap-rr-new-form-row">
        <label>Delivery Date
          <input type="date" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} />
        </label>
        <label>Delivery Time
          <input type="time" value={deliveryTime} onChange={(e) => setDeliveryTime(e.target.value)} />
        </label>
      </div>
      <label>Delivery Location
        <input value={deliveryLocation} onChange={(e) => setDeliveryLocation(e.target.value)} placeholder="Staging area, address, GPS" />
      </label>
      <label>Notes
        <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Special requirements, contact info…" />
      </label>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          className="iap-card-btn iap-card-btn--approve"
          onClick={handleSubmit}
          disabled={!resourceKind.trim()}
        >
          Submit Request
        </button>
        <button className="iap-card-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

// ── Section group ─────────────────────────────────────────────────────────────

function SectionGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="iap-section-group">
      <div className="iap-section-label">{label}</div>
      {children}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const FORM_LABELS: Record<ICSFormId, string> = {
  ics201: "ICS-201 · Incident Briefing",
  ics202: "ICS-202 · Objectives",
  ics203: "ICS-203 · Organization",
  ics204: "ICS-204 · Assignment List",
  ics205: "ICS-205 · Comms Plan",
  ics205a:"ICS-205A · Comms List",
  ics206: "ICS-206 · Medical Plan",
  ics207: "ICS-207 · Org Chart",
  ics208: "ICS-208 · Safety Plan",
  ics209: "ICS-209 · Status Summary",
  ics211: "ICS-211 · Check-In List",
  ics213: "ICS-213 · General Message",
  ics213rr:"ICS-213RR · Resource Request",
  ics214: "ICS-214 · Activity Log",
  ics215: "ICS-215 · Resource Needs",
  ics215a:"ICS-215A · Safety Analysis",
  "full-iap": "Full IAP Package",
};

export default function IAPDashboard({
  incident, activePeriod, activePeriodIndex, selectedForm,
  onSelectForm, onSelectRequest,
  onUpdateFormRecord, onAddResourceRequest, onUpdateResourceRequest, onRemoveResourceRequest,
  onApproveIAP,
}: IAPDashboardProps) {
  const [confirmingIAP, setConfirmingIAP] = useState(false);
  const [showRRForm, setShowRRForm] = useState(false);

  const hazardDef = HAZARD_DEFS.find(h => h.key === incident.hazardType);
  const requiredForms = new Set<ICSFormId>([
    ...ALWAYS_REQUIRED,
    ...(hazardDef?.relevantForms ?? []),
  ]);
  requiredForms.delete("full-iap");

  const formRecords = activePeriod.formRecords ?? {};
  const prevPeriod = activePeriodIndex > 0
    ? incident.operationalPeriods[activePeriodIndex - 1]
    : null;

  function getRecord(formId: ICSFormId): FormRecord {
    return formRecords[formId] ?? { status: "empty" };
  }

  function isAlwaysApproved(formId: ICSFormId): boolean {
    return formId === "ics201" && !!incident.ics201CompletedAt;
  }

  function openForm(formId: ICSFormId) {
    const record = formRecords[formId];
    if (!record || record.status === "empty") {
      onUpdateFormRecord(activePeriodIndex, formId, {
        status: "draft",
        preparedAt: new Date().toISOString(),
      });
    }
    onSelectForm(formId);
  }

  function carryFrom(formId: ICSFormId): number | null {
    if (!prevPeriod) return null;
    const prevRecord = prevPeriod.formRecords?.[formId];
    return prevRecord?.status === "approved" ? activePeriodIndex - 1 : null;
  }

  // IAP approval gate: all required forms approved
  const approvedRequired = [...requiredForms].filter((f) => {
    if (isAlwaysApproved(f)) return true;
    return formRecords[f]?.status === "approved";
  });
  const allRequiredApproved = approvedRequired.length === requiredForms.size;
  const isIAPApproved = !!activePeriod.iapApprovedAt;

  function renderCard(formId: ICSFormId) {
    return (
      <FormCard
        key={formId}
        formId={formId}
        label={FORM_LABELS[formId]}
        record={getRecord(formId)}
        isSelected={selectedForm === formId}
        isRequired={requiredForms.has(formId)}
        isAlwaysApproved={isAlwaysApproved(formId)}
        carryFromPeriod={carryFrom(formId)}
        incident={incident}
        activePeriodIndex={activePeriodIndex}
        onOpen={() => openForm(formId)}
        onUpdateRecord={(patch) => onUpdateFormRecord(activePeriodIndex, formId, patch)}
        onCarryForward={() => onUpdateFormRecord(activePeriodIndex, formId, { status: "draft", preparedAt: new Date().toISOString() })}
      />
    );
  }

  const periodRRs = (incident.resourceRequests ?? []).filter(r => r.periodDay === activePeriod.day);

  return (
    <div className="iap-dashboard">
      {/* ── Status Banner ── */}
      <div className={`iap-banner${isIAPApproved ? " iap-banner--approved" : ""}`}>
        <div className="iap-banner-title">
          IAP — Operational Period {activePeriod.day} &nbsp;
          <span style={{ color: isIAPApproved ? "var(--status-ok)" : "var(--status-warn)", fontWeight: 800 }}>
            {isIAPApproved ? "✓ APPROVED" : "● DRAFT"}
          </span>
        </div>
        <div className="iap-banner-count">
          {approvedRequired.length} of {requiredForms.size} required forms approved
          {isIAPApproved && activePeriod.iapApprovedBy && (
            <span> · Signed by {activePeriod.iapApprovedBy} · {new Date(activePeriod.iapApprovedAt!).toLocaleDateString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
          )}
        </div>
        {!isIAPApproved && (
          confirmingIAP ? (
            <div className="iap-approve-confirm" style={{ marginTop: 8 }}>
              <span>Approve IAP for Op Period {activePeriod.day} as IC {incident.incidentCommanderName || "(unnamed)"}?</span>
              <button className="iap-card-btn iap-card-btn--approve" onClick={() => {
                onApproveIAP(activePeriodIndex, incident.incidentCommanderName, "Incident Commander");
                setConfirmingIAP(false);
              }}>Confirm</button>
              <button className="iap-card-btn" onClick={() => setConfirmingIAP(false)}>Cancel</button>
            </div>
          ) : (
            <button
              className="iap-approve-iap-btn"
              disabled={!allRequiredApproved}
              onClick={() => setConfirmingIAP(true)}
              title={!allRequiredApproved ? `Approve all ${requiredForms.size} required forms first` : undefined}
            >
              Approve Full IAP ▸
            </button>
          )
        )}
      </div>

      {/* ── INITIAL BRIEFING ── */}
      <SectionGroup label="Initial Briefing">
        {renderCard("ics201")}
      </SectionGroup>

      {/* ── SITUATION & STATUS ── */}
      <SectionGroup label="Situation & Status">
        {renderCard("ics202")}
        {renderCard("ics209")}
      </SectionGroup>

      {/* ── OPERATIONS ── */}
      <SectionGroup label="Operations Section">
        {renderCard("ics204")}
      </SectionGroup>

      {/* ── PLANNING ── */}
      <SectionGroup label="Planning Section">
        {renderCard("ics203")}
        {renderCard("ics207")}
        {renderCard("ics215")}
        {renderCard("ics215a")}
      </SectionGroup>

      {/* ── LOGISTICS ── */}
      <SectionGroup label="Logistics Section">
        {renderCard("ics205")}
        {renderCard("ics205a")}
        {renderCard("ics206")}
        {renderCard("ics211")}
      </SectionGroup>

      {/* ── SAFETY ── */}
      <SectionGroup label="Safety">
        {renderCard("ics208")}
      </SectionGroup>

      {/* ── RESOURCE REQUESTS ── */}
      <SectionGroup label={`Resource Requests (ICS-213RR)${periodRRs.length > 0 ? ` · ${periodRRs.length}` : ""}`}>
        {periodRRs.map((req) => (
          <ResourceRequestRow
            key={req.id}
            req={req}
            onOpen={() => { onSelectRequest(req.id); onSelectForm("ics213rr"); }}
            onUpdate={(patch) => onUpdateResourceRequest(req.id, patch)}
            onRemove={() => onRemoveResourceRequest(req.id)}
          />
        ))}
        {showRRForm ? (
          <NewRRForm
            periodDay={activePeriod.day}
            onSubmit={(req) => { onAddResourceRequest(req); setShowRRForm(false); }}
            onCancel={() => setShowRRForm(false)}
          />
        ) : (
          <button className="iap-rr-add-btn" onClick={() => setShowRRForm(true)}>
            + New Resource Request
          </button>
        )}
      </SectionGroup>

      {/* ── SUPPORT / TRACKING ── */}
      <SectionGroup label="Support / Tracking">
        {renderCard("ics213")}
        {renderCard("ics214")}
      </SectionGroup>
    </div>
  );
}
