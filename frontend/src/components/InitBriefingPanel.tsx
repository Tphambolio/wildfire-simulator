/** Initial Incident Briefing — gates the EOC Console until ICS-201 is completed. */

import { useState } from "react";

export interface BriefingData {
  icName: string;
  narrative: string;
  objectives: string[];
  jurisdiction: string;
}

interface InitBriefingPanelProps {
  incidentName: string;
  onComplete: (data: BriefingData) => void;
}

export default function InitBriefingPanel({ incidentName, onComplete }: InitBriefingPanelProps) {
  const [icName, setIcName] = useState("");
  const [narrative, setNarrative] = useState("");
  const [objectives, setObjectives] = useState<string[]>(["", "", ""]);
  const [jurisdiction, setJurisdiction] = useState("");

  const addObjective = () => setObjectives((prev) => [...prev, ""]);

  const updateObjective = (idx: number, val: string) => {
    setObjectives((prev) => prev.map((o, i) => (i === idx ? val : o)));
  };

  const removeObjective = (idx: number) => {
    setObjectives((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = () => {
    const filledObjectives = objectives.map((o) => o.trim()).filter(Boolean);
    onComplete({
      icName: icName.trim(),
      narrative: narrative.trim(),
      objectives: filledObjectives,
      jurisdiction: jurisdiction.trim(),
    });
  };

  const canSubmit = icName.trim().length > 0 && narrative.trim().length > 0;

  return (
    <div className="init-briefing-panel">
      <div className="init-briefing-header">
        <div className="init-briefing-icon">📋</div>
        <h2 className="init-briefing-title">Initial Incident Briefing</h2>
        <p className="init-briefing-subtitle">
          <strong>{incidentName}</strong><br />
          Complete the initial briefing before opening the EOC Console.<br />
          This generates your ICS-201.
        </p>
      </div>

      <div className="init-briefing-form">
        {/* IC Name */}
        <div className="init-field">
          <label className="init-label">
            Incident Commander <span className="init-required">*</span>
          </label>
          <input
            className="init-input"
            type="text"
            placeholder="Full name"
            value={icName}
            autoFocus
            onChange={(e) => setIcName(e.target.value)}
            maxLength={80}
          />
        </div>

        {/* Situation Narrative */}
        <div className="init-field">
          <label className="init-label">
            Situation Summary <span className="init-required">*</span>
          </label>
          <p className="init-hint">What is happening, where, and at what scale?</p>
          <textarea
            className="init-textarea"
            placeholder="e.g. A grass fire ignited at approximately 1400h near the River Valley trail network. Fire is spreading NE under 35 km/h winds. Approximately 200 ha affected. Evacuation Order in effect for Terwillegar Towne."
            value={narrative}
            onChange={(e) => setNarrative(e.target.value)}
            rows={4}
            maxLength={600}
          />
        </div>

        {/* Initial Objectives */}
        <div className="init-field">
          <label className="init-label">Initial Objectives</label>
          <p className="init-hint">One per line — these populate ICS-201 Section D and ICS-202.</p>
          <div className="init-objectives">
            {objectives.map((obj, idx) => (
              <div key={idx} className="init-objective-row">
                <input
                  className="init-input"
                  type="text"
                  placeholder={`Objective ${idx + 1}`}
                  value={obj}
                  onChange={(e) => updateObjective(idx, e.target.value)}
                  maxLength={200}
                />
                {objectives.length > 1 && (
                  <button
                    className="init-remove-btn"
                    onClick={() => removeObjective(idx)}
                    title="Remove objective"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            <button className="init-add-btn" onClick={addObjective}>
              + Add objective
            </button>
          </div>
        </div>

        {/* Jurisdiction */}
        <div className="init-field">
          <label className="init-label">Jurisdiction / Reporting Authority</label>
          <input
            className="init-input"
            type="text"
            placeholder="e.g. City of Edmonton, Edmonton Fire Rescue Services"
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
            maxLength={120}
          />
        </div>

        <button
          className="init-submit-btn"
          onClick={handleSubmit}
          disabled={!canSubmit}
        >
          Complete Initial Briefing →
        </button>

        <p className="init-footer-hint">
          IC, narrative, and objectives are saved to the incident and pre-filled in all ICS forms.
          You can edit any field directly in the generated form.
        </p>
      </div>
    </div>
  );
}
