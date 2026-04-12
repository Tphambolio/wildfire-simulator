/** Compact sidebar panel — shows section chiefs + counts, opens Org Board in EOC. */

import { useState } from "react";
import type { IncidentResource, IncidentAgency } from "../types/incident";
import { ICS_SECTION_META, ICS_POSITIONS_BY_SECTION } from "../types/incident";

interface TeamSummaryPanelProps {
  resources: IncidentResource[];
  agencies: IncidentAgency[];
  onOpenSection: (section: "command" | "operations" | "planning" | "logistics" | "finance") => void;
}

const SECTIONS = ["command", "operations", "planning", "logistics", "finance"] as const;
const CHIEF_POSITIONS: Record<typeof SECTIONS[number], string> = {
  command:    "Incident Commander",
  operations: "Operations Section Chief",
  planning:   "Planning Section Chief",
  logistics:  "Logistics Section Chief",
  finance:    "Finance/Admin Section Chief",
};

export default function TeamSummaryPanel({ resources, agencies, onOpenSection }: TeamSummaryPanelProps) {
  const [open, setOpen] = useState(true);

  const totalStaffed = resources.length;
  const unassigned = resources.filter((r) => r.icsSection === "other").length;
  const ucNames = agencies.filter((a) => a.isUnifiedCommand).map((a) => a.name);

  function getChief(section: typeof SECTIONS[number]): IncidentResource | undefined {
    const chiefPos = CHIEF_POSITIONS[section];
    return (
      resources.find((r) => r.icsSection === section && r.icsPosition === chiefPos && r.kind === "person") ??
      resources.find((r) => r.icsSection === section && r.role === chiefPos && r.kind === "person")
    );
  }

  function getSectionCount(section: typeof SECTIONS[number]): number {
    return resources.filter((r) => r.icsSection === section).length;
  }

  return (
    <div className="panel team-summary-panel">
      <button className="panel-collapse-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span>Team</span>
        <span className="collapse-icon">{open ? "▲" : "▼"}</span>
        {totalStaffed > 0 && <span className="scenario-count-badge">{totalStaffed}</span>}
      </button>

      {open && (
        <div className="scenario-body">
          {SECTIONS.map((section) => {
            const meta = ICS_SECTION_META[section];
            const chief = getChief(section);
            const count = getSectionCount(section);
            const positions = ICS_POSITIONS_BY_SECTION[section];
            const filled = positions.filter((pos) =>
              resources.some((r) => r.icsSection === section && r.icsPosition === pos && r.kind === "person")
            ).length;

            return (
              <div key={section} className="ts-section-row" onClick={() => onOpenSection(section)} title={`Open ${meta.label} workspace`}>
                <span className="ts-pill" style={{ background: meta.color, color: meta.color === "#ffd54f" ? "#000" : "#fff" }}>
                  {meta.abbrev}
                </span>
                <div className="ts-chief-col">
                  <span className="ts-chief">
                    {chief ? chief.name : <span style={{ opacity: 0.45 }}>— unassigned</span>}
                  </span>
                  {section === "command" && ucNames.length > 0 && (
                    <span className="ts-uc-hint">★ UC: {ucNames.join(" / ")}</span>
                  )}
                </div>
                <span className="ts-count">
                  {count > 0 ? `${filled}/${positions.length}` : "empty"}
                </span>
              </div>
            );
          })}

          {unassigned > 0 && (
            <div className="ts-pool-row">
              <span className="ts-pill" style={{ background: ICS_SECTION_META.other.color, color: "#fff" }}>—</span>
              <span className="ts-chief" style={{ opacity: 0.55 }}>Unassigned pool</span>
              <span className="ts-count">{unassigned}</span>
            </div>
          )}

          <button className="org-board-btn" onClick={() => onOpenSection("command")}>
            Open Org Board →
          </button>
        </div>
      )}
    </div>
  );
}
