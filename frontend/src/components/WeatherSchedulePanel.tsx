/**
 * Setup → Weather & FWI: optional weather changes during one run (a manual schedule of step
 * changes). Row 1 is the weather above, from the start; each added row takes over at its
 * clock time. FFMC and ISI are recomputed hourly from the starting codes (API
 * `weather_schedule`; utils/weatherSchedule.ts).
 */
import { useId } from "react";
import type { WeatherParams } from "../types/simulation";
import { newRow, type ScheduleRow } from "../utils/weatherSchedule";
import { formatClock } from "../utils/time";
import InfoTip from "./InfoTip";
import { TIPS } from "../content/explanations";

interface Props {
  rows: ScheduleRow[];
  onChange: (rows: ScheduleRow[]) => void;
  base: WeatherParams;
  startMs: number | null;
  /** Hourly forecast weather is on: the schedule does not apply */
  forecastOn: boolean;
  errors: string[];
}

const NUM_FIELDS: { key: keyof Omit<ScheduleRow, "time">; label: string; min: number; max: number; step: number }[] = [
  { key: "wind_speed", label: "Wind (km/h)", min: 0, max: 100, step: 1 },
  { key: "wind_direction", label: "From (°)", min: 0, max: 359, step: 1 },
  { key: "temperature", label: "Temp (°C)", min: -40, max: 50, step: 1 },
  { key: "relative_humidity", label: "RH (%)", min: 1, max: 100, step: 1 },
  { key: "precipitation_mm_h", label: "Rain (mm/h)", min: 0, max: 50, step: 0.1 },
];

export default function WeatherSchedulePanel({ rows, onChange, base, startMs, forecastOn, errors }: Props) {
  const id = useId();
  const set = (i: number, patch: Partial<ScheduleRow>) =>
    onChange(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="field-group weather-schedule" data-testid="weather-schedule">
      <legend className="with-tip">
        Weather changes during the run
        <InfoTip label="About weather changes during the run" text={TIPS.weatherSchedule} />
      </legend>
      {forecastOn ? (
        <p className="hint-sm">Off while hourly forecast weather is on.</p>
      ) : (
        <>
          {rows.length > 0 && (
            <table className="schedule-table">
              <caption className="visually-hidden">Weather in force from each time</caption>
              <thead>
                <tr>
                  <th scope="col">From</th>
                  {NUM_FIELDS.map((f) => <th key={f.key} scope="col">{f.label}</th>)}
                  <th scope="col"><span className="visually-hidden">Remove</span></th>
                </tr>
              </thead>
              <tbody>
                <tr className="schedule-base">
                  <th scope="row">{startMs !== null ? formatClock(new Date(startMs)) : "Start"}</th>
                  <td>{base.wind_speed}</td>
                  <td>{base.wind_direction}</td>
                  <td>{base.temperature}</td>
                  <td>{base.relative_humidity}</td>
                  <td>0</td>
                  <td />
                </tr>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <input
                        type="time"
                        value={r.time}
                        aria-label={`Change ${i + 1}: from (local time)`}
                        onChange={(e) => set(i, { time: e.target.value })}
                      />
                    </td>
                    {NUM_FIELDS.map((f) => (
                      <td key={f.key}>
                        <input
                          type="number"
                          min={f.min}
                          max={f.max}
                          step={f.step}
                          value={r[f.key]}
                          aria-label={`Change ${i + 1}: ${f.label}`}
                          onChange={(e) => set(i, { [f.key]: e.target.value === "" ? 0 : e.target.valueAsNumber } as Partial<ScheduleRow>)}
                        />
                      </td>
                    ))}
                    <td>
                      <button
                        type="button"
                        className="btn-secondary btn-inline"
                        aria-label={`Remove change ${i + 1}`}
                        onClick={() => onChange(rows.filter((_, k) => k !== i))}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button
            type="button"
            className="btn-secondary btn-inline"
            onClick={() => onChange([...rows, newRow(base, rows, startMs)])}
            aria-describedby={`${id}-sched-hint`}
          >
            Add a weather change
          </button>
          <span className="visually-hidden" id={`${id}-sched-hint`}>{TIPS.weatherSchedule}</span>
          {errors.map((e) => <div key={e} className="input-error" role="alert">{e}</div>)}
        </>
      )}
    </fieldset>
  );
}
