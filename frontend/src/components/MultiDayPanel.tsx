/**
 * Multi-day fire scenario input panel.
 *
 * Allows entry of weather conditions for up to 7 days. FWI moisture codes
 * (FFMC/DMC/DC) carry forward between days using CFFDRS daily equations
 * on the server. The fire perimeter from each day seeds the next day.
 */

import type { MultiDayWeatherParams } from "../types/simulation";

const DEFAULT_DAY: MultiDayWeatherParams = {
  wind_speed: 20,
  wind_direction: 270,
  temperature: 25,
  relative_humidity: 30,
  precipitation_24h: 0,
};

const WIND_DIRS = [
  { label: "N",   deg: 0   },
  { label: "NE",  deg: 45  },
  { label: "E",   deg: 90  },
  { label: "SE",  deg: 135 },
  { label: "S",   deg: 180 },
  { label: "SW",  deg: 225 },
  { label: "W",   deg: 270 },
  { label: "NW",  deg: 315 },
];

interface MultiDayPanelProps {
  days: MultiDayWeatherParams[];
  onChange: (days: MultiDayWeatherParams[]) => void;
  disabled?: boolean;
}

function DayInput({
  day,
  index,
  onChange,
  onRemove,
  canRemove,
  disabled,
}: {
  day: MultiDayWeatherParams;
  index: number;
  onChange: (d: MultiDayWeatherParams) => void;
  onRemove: () => void;
  canRemove: boolean;
  disabled?: boolean;
}) {
  const set = (field: keyof MultiDayWeatherParams, value: number) =>
    onChange({ ...day, [field]: value });

  return (
    <div className="md-day">
      <div className="md-day-hdr">
        <span className="md-day-label">Day {index + 1}</span>
        {canRemove && !disabled && (
          <button className="md-day-remove" onClick={onRemove} title="Remove day">
            ✕
          </button>
        )}
      </div>

      <div className="md-grid">
        <label className="md-label">Wind</label>
        <div className="md-wind-row">
          <input
            type="number"
            className="md-input"
            min={0} max={100} step={1}
            value={day.wind_speed}
            onChange={(e) => set("wind_speed", +e.target.value)}
            disabled={disabled}
            placeholder="km/h"
            style={{ width: 56 }}
          />
          <select
            className="md-input"
            value={
              WIND_DIRS.reduce((best, d) => {
                const diff = Math.abs(((d.deg - day.wind_direction + 540) % 360) - 180);
                const bestDiff = Math.abs(((best.deg - day.wind_direction + 540) % 360) - 180);
                return diff < bestDiff ? d : best;
              }, WIND_DIRS[0]).label
            }
            onChange={(e) => {
              const d = WIND_DIRS.find((w) => w.label === e.target.value);
              if (d) set("wind_direction", d.deg);
            }}
            disabled={disabled}
          >
            {WIND_DIRS.map((d) => (
              <option key={d.label}>{d.label}</option>
            ))}
          </select>
        </div>

        <label className="md-label">Temp / RH</label>
        <div className="md-wind-row">
          <input
            type="number"
            className="md-input"
            min={-40} max={50} step={1}
            value={day.temperature}
            onChange={(e) => set("temperature", +e.target.value)}
            disabled={disabled}
            placeholder="°C"
            style={{ width: 52 }}
          />
          <input
            type="number"
            className="md-input"
            min={1} max={100} step={1}
            value={day.relative_humidity}
            onChange={(e) => set("relative_humidity", +e.target.value)}
            disabled={disabled}
            placeholder="%"
            style={{ width: 48 }}
          />
        </div>

        <label className="md-label">Precip</label>
        <input
          type="number"
          className="md-input"
          min={0} max={300} step={0.5}
          value={day.precipitation_24h}
          onChange={(e) => set("precipitation_24h", +e.target.value)}
          disabled={disabled}
          placeholder="mm"
          style={{ width: 60 }}
        />
      </div>
    </div>
  );
}

export default function MultiDayPanel({ days, onChange, disabled }: MultiDayPanelProps) {
  const addDay = () => onChange([...days, { ...DEFAULT_DAY }]);
  const removeDay = (i: number) => onChange(days.filter((_, idx) => idx !== i));
  const updateDay = (i: number, d: MultiDayWeatherParams) =>
    onChange(days.map((day, idx) => (idx === i ? d : day)));

  return (
    <div className="md-panel">
      {days.map((d, i) => (
        <DayInput
          key={i}
          day={d}
          index={i}
          onChange={(nd) => updateDay(i, nd)}
          onRemove={() => removeDay(i)}
          canRemove={days.length > 1}
          disabled={disabled}
        />
      ))}
      {days.length < 7 && !disabled && (
        <button className="md-add-btn" onClick={addDay}>
          + Add Day {days.length + 1}
        </button>
      )}
    </div>
  );
}
