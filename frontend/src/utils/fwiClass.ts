/**
 * FWI display classes: the CWFIS national Fire Weather Index map intervals
 * (0-5 Low, 6-15 Moderate, 16-22 High, 23-29 Very High, 30+ Extreme), the same table
 * as the engine (firesim/fwi/classes.py) and the API. An FWI map classification, not an
 * official fire danger rating, which provincial agencies issue from their own criteria.
 */
export type FwiClass = "Low" | "Moderate" | "High" | "Very High" | "Extreme";

const LIMITS: Array<[number, FwiClass]> = [
  [5.5, "Low"],
  [15.5, "Moderate"],
  [22.5, "High"],
  [29.5, "Very High"],
];

export function fwiClass(fwi: number): FwiClass {
  for (const [upper, label] of LIMITS) if (fwi < upper) return label;
  return "Extreme";
}

const COLORS: Record<FwiClass, string> = {
  Low: "#2e7d32",
  Moderate: "#558b2f",
  High: "#f57f17",
  "Very High": "#e65100",
  Extreme: "#b71c1c",
};

/** Badge colour for an FWI value or a class label. */
export function fwiClassColor(value: number | string): string {
  if (typeof value === "number") return COLORS[fwiClass(value)];
  return COLORS[value as FwiClass] ?? "#1a237e";
}
