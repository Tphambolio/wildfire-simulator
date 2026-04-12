/** Guided Step Panel — shows the highest-priority incomplete ICS step. */

import type { IncidentSession } from "../types/incident";
import type { ConsoleTab } from "./EOCConsole";

interface NextStepCardProps {
  incident: IncidentSession;
  onNavigate: (eocTab: ConsoleTab) => void;
  currentTab?: ConsoleTab;
}

interface ICSStep {
  id: string;
  label: string;
  hint: string;
  phase: 0 | 1 | 2 | 3;
  phaseName: string;
  isComplete: (incident: IncidentSession) => boolean;
  eocTab: ConsoleTab;
}

const PHASE_LABELS: Record<0 | 1 | 2 | 3, string> = {
  0: "Initial Briefing",
  1: "Initial Response",
  2: "Unified Command",
  3: "IAP Ready",
};

const STEPS: ICSStep[] = [
  {
    id: "initial-briefing",
    label: "Complete Initial Briefing",
    hint: "Set the IC, situation summary, and initial objectives. This generates your ICS-201.",
    phase: 0,
    phaseName: PHASE_LABELS[0],
    isComplete: (i) => !!i.ics201CompletedAt,
    eocTab: "briefing",
  },
  {
    id: "assign-ic",
    label: "Assign Incident Commander",
    hint: "Designate the IC to establish unified command.",
    phase: 1,
    phaseName: PHASE_LABELS[1],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "command" && r.icsPosition === "Incident Commander"
      ),
    eocTab: "command",
  },
  {
    id: "assign-safety",
    label: "Assign Safety Officer",
    hint: "Safety Officer ensures personnel safety and hazard assessment.",
    phase: 1,
    phaseName: PHASE_LABELS[1],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "command" && r.icsPosition === "Safety Officer"
      ),
    eocTab: "command",
  },
  {
    id: "assign-ops",
    label: "Assign Operations Section Chief",
    hint: "Ops Chief directs all tactical field operations.",
    phase: 2,
    phaseName: PHASE_LABELS[2],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "operations" && r.icsPosition === "Operations Section Chief"
      ),
    eocTab: "operations",
  },
  {
    id: "assign-planning",
    label: "Assign Planning Section Chief",
    hint: "Planning Chief manages situation status and IAP preparation.",
    phase: 2,
    phaseName: PHASE_LABELS[2],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "planning" && r.icsPosition === "Planning Section Chief"
      ),
    eocTab: "planning",
  },
  {
    id: "assign-logistics",
    label: "Assign Logistics Section Chief",
    hint: "Logistics Chief coordinates resources, facilities and services.",
    phase: 2,
    phaseName: PHASE_LABELS[2],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "logistics" && r.icsPosition === "Logistics Section Chief"
      ),
    eocTab: "logistics",
  },
  {
    id: "assign-finance",
    label: "Assign Finance/Admin Section Chief",
    hint: "Finance Chief handles cost tracking and procurement.",
    phase: 2,
    phaseName: PHASE_LABELS[2],
    isComplete: (i) =>
      i.resources.some(
        (r) => r.icsSection === "finance" && r.icsPosition === "Finance/Admin Section Chief"
      ),
    eocTab: "finance",
  },
  {
    id: "generate-iap",
    label: "Generate Full IAP",
    hint: "Compile the Incident Action Plan with all completed ICS forms.",
    phase: 3,
    phaseName: PHASE_LABELS[3],
    isComplete: () => false,
    eocTab: "iap",
  },
];

export default function NextStepCard({ incident, onNavigate, currentTab }: NextStepCardProps) {
  const firstIncompleteIdx = STEPS.findIndex((s) => !s.isComplete(incident));
  const allSectionsStaffed = firstIncompleteIdx === STEPS.length - 1;
  const allComplete = firstIncompleteIdx === -1;

  const currentStep = firstIncompleteIdx >= 0 ? STEPS[firstIncompleteIdx] : null;
  const upcomingSteps = currentStep
    ? STEPS.slice(firstIncompleteIdx + 1, firstIncompleteIdx + 3)
    : [];

  const phase = currentStep?.phase ?? 3;
  const phaseName = currentStep?.phaseName ?? PHASE_LABELS[3];
  const phaseClass = phase === 0 ? "ns-phase-0" : phase === 1 ? "ns-phase-1" : phase === 2 ? "ns-phase-2" : "ns-phase-3";

  const alreadyHere = currentStep && currentTab === currentStep.eocTab;

  return (
    <div className="next-step-panel">
      <div className={`ns-phase-pill ${phaseClass}`}>
        Phase {phase} — {phaseName}
      </div>

      {allComplete || allSectionsStaffed ? (
        <div className="ns-complete">
          <div className="ns-complete-check">✓ All sections staffed · Ready for IAP</div>
          <button className="ns-step-btn" onClick={() => onNavigate("iap")}>
            → Open IAP
          </button>
        </div>
      ) : currentStep ? (
        <div className="ns-current-step">
          <div className="ns-step-label">{currentStep.label}</div>
          <div className="ns-step-hint">{currentStep.hint}</div>
          {alreadyHere ? (
            <div className="ns-here-nudge">↑ Complete the form above</div>
          ) : (
            <button className="ns-step-btn" onClick={() => onNavigate(currentStep.eocTab)}>
              → Go to {currentStep.eocTab.charAt(0).toUpperCase() + currentStep.eocTab.slice(1)}
            </button>
          )}
          {upcomingSteps.length > 0 && (
            <div className="ns-upcoming">
              <div className="ns-upcoming-label">Coming up:</div>
              {upcomingSteps.map((s) => (
                <div key={s.id} className="ns-upcoming-item">{s.label}</div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
