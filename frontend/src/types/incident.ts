/** Incident session types for multi-day operational period tracking. */

import type { WeatherParams, FWIOverrides } from "./simulation";

// ── Annotation layer identifiers (distinct from ICS viewer form IDs) ──────────

/** Which map layer an annotation belongs to — one per ICS form that has a map. */
export type AnnotationLayer = "situation" | "ics204" | "ics205" | "ics206" | "evac";

export const ANNOTATION_LAYER_LABELS: Record<AnnotationLayer, string> = {
  situation: "Situation",
  ics204: "204 Assignments",
  ics205: "205 Comms",
  ics206: "206 Medical",
  evac: "Evacuation",
};

// ── Symbol library ────────────────────────────────────────────────────────────

export type ICSSymbolKey =
  // ICS-204 Assignments
  | "division_supervisor" | "staging_area" | "drop_point" | "camp" | "water_fill"
  | "hand_line" | "dozer_line"
  // ICS-206 Medical
  | "medical_aid_station" | "hospital" | "medevac_lz" | "ambulance_staging"
  // ICS-205 Comms
  | "radio_repeater" | "net_control"
  // Evac / General
  | "checkpoint" | "reception_centre" | "shelter" | "vulnerable_pop"
  | "evac_route" | "do_not_enter" | "command_post" | "icp"
  // Drawing modes (no icon — use existing SVG tools)
  | "freehand_path" | "text_label";

export interface SymbolDef {
  key: ICSSymbolKey;
  label: string;
  shortCode: string;    // displayed when icon not yet fully designed
  color: string;        // stroke / fill color
  layer: AnnotationLayer;
  type: "point" | "path"; // point = single click, path = drag
}

export const SYMBOL_DEFS: SymbolDef[] = [
  // ── ICS-204 ──
  { key: "division_supervisor", label: "Division Supervisor", shortCode: "SUP", color: "#ff9800", layer: "ics204", type: "point" },
  { key: "staging_area",        label: "Staging Area",       shortCode: "STG", color: "#ff9800", layer: "ics204", type: "point" },
  { key: "drop_point",          label: "Drop Point",         shortCode: "DP",  color: "#ff9800", layer: "ics204", type: "point" },
  { key: "camp",                label: "Camp / Base",        shortCode: "BASE",color: "#ff9800", layer: "ics204", type: "point" },
  { key: "water_fill",          label: "Water Fill Site",    shortCode: "W",   color: "#2196f3", layer: "ics204", type: "point" },
  { key: "hand_line",           label: "Hand Line",          shortCode: "HL",  color: "#ff9800", layer: "ics204", type: "path"  },
  { key: "dozer_line",          label: "Dozer Line",         shortCode: "DZL", color: "#ff9800", layer: "ics204", type: "path"  },
  // ── ICS-206 ──
  { key: "medical_aid_station", label: "Medical Aid Station",shortCode: "MAS", color: "#f44336", layer: "ics206", type: "point" },
  { key: "hospital",            label: "Hospital",           shortCode: "H",   color: "#f44336", layer: "ics206", type: "point" },
  { key: "medevac_lz",          label: "Medevac LZ",         shortCode: "LZ",  color: "#f44336", layer: "ics206", type: "point" },
  { key: "ambulance_staging",   label: "Ambulance Staging",  shortCode: "AMB", color: "#f44336", layer: "ics206", type: "point" },
  // ── ICS-205 ──
  { key: "radio_repeater",      label: "Radio Repeater",     shortCode: "REP", color: "#9c27b0", layer: "ics205", type: "point" },
  { key: "net_control",         label: "Net Control Station",shortCode: "NCS", color: "#9c27b0", layer: "ics205", type: "point" },
  // ── Evac ──
  { key: "checkpoint",          label: "Checkpoint / TCP",   shortCode: "CP",  color: "#00bcd4", layer: "evac",   type: "point" },
  { key: "reception_centre",    label: "Reception Centre",   shortCode: "RC",  color: "#00bcd4", layer: "evac",   type: "point" },
  { key: "shelter",             label: "Shelter",            shortCode: "SH",  color: "#00bcd4", layer: "evac",   type: "point" },
  { key: "vulnerable_pop",      label: "Vulnerable Pop.",    shortCode: "VP",  color: "#ff5722", layer: "evac",   type: "point" },
  { key: "evac_route",          label: "Evacuation Route",   shortCode: "→",   color: "#00bcd4", layer: "evac",   type: "path"  },
  { key: "do_not_enter",        label: "Do Not Enter",       shortCode: "DNE", color: "#f44336", layer: "evac",   type: "path"  },
  // ── General ──
  { key: "command_post",        label: "Command Post",       shortCode: "CP★", color: "#4caf50", layer: "situation", type: "point" },
  { key: "icp",                 label: "Incident Cmd Post",  shortCode: "ICP", color: "#4caf50", layer: "situation", type: "point" },
  // ── Drawing modes ──
  { key: "freehand_path",       label: "Freehand Draw",      shortCode: "✏",   color: "#ff6400", layer: "situation", type: "path"  },
  { key: "text_label",          label: "Text Label",         shortCode: "T",   color: "#ffffff", layer: "situation", type: "point" },
];

export const SYMBOLS_BY_LAYER: Record<AnnotationLayer, SymbolDef[]> = {
  situation: SYMBOL_DEFS.filter(s => s.layer === "situation"),
  ics204:    SYMBOL_DEFS.filter(s => s.layer === "ics204"),
  ics205:    SYMBOL_DEFS.filter(s => s.layer === "ics205"),
  ics206:    SYMBOL_DEFS.filter(s => s.layer === "ics206"),
  evac:      SYMBOL_DEFS.filter(s => s.layer === "evac"),
};

// ── Annotations ───────────────────────────────────────────────────────────────

export interface IncidentAnnotation {
  id: string;                           // crypto.randomUUID()
  layer: AnnotationLayer;
  type: "symbol" | "path" | "text";
  symbolKey: ICSSymbolKey;
  /** [[lng, lat], ...] — GeoJSON coordinate order. Single-element for point symbols. */
  coordinates: [number, number][];
  label: string;
  properties: Record<string, string>;   // form-specific: contact, capacity, frequency
  operationalDay: number;               // 1-based
  createdAt: string;
}

// ── Operational period ────────────────────────────────────────────────────────

export interface EvacDecisionRecord {
  id: string;
  timestamp: string;
  frameIndex: number;
  timeHours: number;
  zones: { tier: "Order" | "Alert" | "Watch"; communities: string[]; areaHa: number }[];
}

export interface FrameSummary {
  timeHours: number;
  areaHa: number;
  headRosMMin: number;
  maxHfiKwM: number;
  fireType: string;
  flameLengthM: number;
  day?: number;
}

export interface OperationalPeriod {
  day: number;                          // 1, 2, 3...
  date: string;                         // "2026-04-05"
  opPeriodStart: string;                // "08:00"
  opPeriodEnd: string;                  // "20:00"
  weather: WeatherParams;
  fwi: FWIOverrides;
  ignitionPoint: { lat: number; lng: number } | null;
  simulationId: string | null;
  simulationStatus: string | null;
  durationHours: number;
  frameSummaries: FrameSummary[];       // metrics only, no burned_cells
  finalPerimeter: [number, number][] | null;  // last frame perimeter — seeds Day N+1
  annotations: IncidentAnnotation[];
  evacuationDecisions: EvacDecisionRecord[];
  objectives: string[];                 // ICS-202 objectives for this period
  situationNarrative: string;
}

// ── Incident session ──────────────────────────────────────────────────────────

export interface IncidentSession {
  id: string;
  name: string;
  incidentNumber: string;
  incidentCommanderName: string;
  status: "active" | "closed";
  createdAt: string;
  updatedAt: string;
  activePeriodIndex: number;            // 0-based index into operationalPeriods
  operationalPeriods: OperationalPeriod[];
  // Config shared across all operational periods
  useEdmontonGrid: boolean;
  fuelType: string;
  enableSpotting: boolean;
  spottingIntensity: number;
  includeWater: boolean;
  includeBuildings: boolean;
  includeWUI: boolean;
  includeDEM: boolean;
  snapshotMinutes: number;
}

// ── Factory helpers ───────────────────────────────────────────────────────────

export function makeOperationalPeriod(day: number, date: string): OperationalPeriod {
  return {
    day,
    date,
    opPeriodStart: "08:00",
    opPeriodEnd: "20:00",
    weather: { wind_speed: 20, wind_direction: 180, temperature: 25, relative_humidity: 30, precipitation_24h: 0 },
    fwi: { ffmc: null, dmc: null, dc: null },
    ignitionPoint: null,
    simulationId: null,
    simulationStatus: null,
    durationHours: 12,
    frameSummaries: [],
    finalPerimeter: null,
    annotations: [],
    evacuationDecisions: [],
    objectives: [],
    situationNarrative: "",
  };
}

export function makeIncident(name: string): IncidentSession {
  const today = new Date().toISOString().slice(0, 10);
  return {
    id: crypto.randomUUID(),
    name,
    incidentNumber: "",
    incidentCommanderName: "",
    status: "active",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    activePeriodIndex: 0,
    operationalPeriods: [makeOperationalPeriod(1, today)],
    useEdmontonGrid: true,
    fuelType: "C2",
    enableSpotting: false,
    spottingIntensity: 1.0,
    includeWater: true,
    includeBuildings: true,
    includeWUI: true,
    includeDEM: true,
    snapshotMinutes: 30,
  };
}
