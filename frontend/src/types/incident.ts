/** Incident session types for multi-day operational period tracking. */

// ── Weather (standalone — no longer depends on simulation types) ──────────────

export interface WeatherParams {
  wind_speed: number;
  wind_direction: number;
  temperature: number;
  relative_humidity: number;
  precipitation: number;
  visibility_km?: number;
}

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
  // Universal drawing tools — available on every layer
  | "generic_point" | "freehand_path" | "text_label"
  // OSM-sourced community assets
  | "pharmacy" | "fire_station" | "assembly_point" | "fuel_station" | "police_station"
  // ── Flood symbols ──
  | "flood_barrier" | "pumping_station" | "high_ground" | "water_level_gauge"
  // ── HAZMAT symbols ──
  | "hot_zone_marker" | "decon_station" | "wind_indicator" | "shelter_in_place"
  // ── Mass Casualty symbols ──
  | "triage_immediate" | "triage_delayed" | "triage_minor" | "triage_expectant"
  | "casualty_collection" | "family_reunification" | "morgue_site"
  // ── Wildfire/Smoke symbols ──
  | "air_quality_monitor" | "smoke_shelter" | "resource_staging" | "observed_perimeter";

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
  { key: "fire_station",        label: "Fire Station",       shortCode: "FS",  color: "#f44336", layer: "ics204", type: "point" },
  // ── ICS-206 ──
  { key: "medical_aid_station", label: "Medical Aid Station",shortCode: "MAS", color: "#f44336", layer: "ics206", type: "point" },
  { key: "hospital",            label: "Hospital",           shortCode: "H",   color: "#f44336", layer: "ics206", type: "point" },
  { key: "medevac_lz",          label: "Medevac LZ",         shortCode: "LZ",  color: "#f44336", layer: "ics206", type: "point" },
  { key: "ambulance_staging",   label: "Ambulance Staging",  shortCode: "AMB", color: "#f44336", layer: "ics206", type: "point" },
  { key: "pharmacy",            label: "Pharmacy",           shortCode: "Rx",  color: "#4caf50", layer: "ics206", type: "point" },
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
  { key: "assembly_point",      label: "Assembly Point",     shortCode: "AP",  color: "#00bcd4", layer: "evac",   type: "point" },
  { key: "fuel_station",        label: "Fuel Station",       shortCode: "GAS", color: "#ff9800", layer: "evac",   type: "point" },
  // ── General ──
  { key: "command_post",        label: "Command Post",       shortCode: "CP★", color: "#4caf50", layer: "situation", type: "point" },
  { key: "icp",                 label: "Incident Cmd Post",  shortCode: "ICP", color: "#4caf50", layer: "situation", type: "point" },
  { key: "police_station",      label: "Police Station",     shortCode: "PS",  color: "#3f51b5", layer: "situation", type: "point" },
  // ── Flood ──
  { key: "flood_barrier",       label: "Flood Barrier",      shortCode: "FB",  color: "#1565c0", layer: "situation", type: "path"  },
  { key: "pumping_station",     label: "Pumping Station",    shortCode: "PMP", color: "#1565c0", layer: "situation", type: "point" },
  { key: "high_ground",         label: "High Ground",        shortCode: "HG",  color: "#2196f3", layer: "situation", type: "point" },
  { key: "water_level_gauge",   label: "Water Level Gauge",  shortCode: "WLG", color: "#1565c0", layer: "situation", type: "point" },
  // ── HAZMAT ──
  { key: "hot_zone_marker",     label: "Hot Zone",           shortCode: "HOT", color: "#f44336", layer: "situation", type: "point" },
  { key: "decon_station",       label: "Decon Station",      shortCode: "DCN", color: "#fdd835", layer: "situation", type: "point" },
  { key: "wind_indicator",      label: "Wind Indicator",     shortCode: "WND", color: "#fdd835", layer: "situation", type: "point" },
  { key: "shelter_in_place",    label: "Shelter-in-Place",   shortCode: "SIP", color: "#ff9800", layer: "situation", type: "point" },
  // ── Mass Casualty ──
  { key: "triage_immediate",    label: "Triage: Immediate",  shortCode: "T-I", color: "#f44336", layer: "ics206",    type: "point" },
  { key: "triage_delayed",      label: "Triage: Delayed",    shortCode: "T-D", color: "#ff9800", layer: "ics206",    type: "point" },
  { key: "triage_minor",        label: "Triage: Minor",      shortCode: "T-M", color: "#4caf50", layer: "ics206",    type: "point" },
  { key: "triage_expectant",    label: "Triage: Expectant",  shortCode: "T-E", color: "#607d8b", layer: "ics206",    type: "point" },
  { key: "casualty_collection", label: "Casualty Collection",shortCode: "CCP", color: "#f44336", layer: "ics206",    type: "point" },
  { key: "family_reunification",label: "Family Reunification",shortCode:"FRC", color: "#00bcd4", layer: "evac",      type: "point" },
  { key: "morgue_site",         label: "Temporary Morgue",   shortCode: "MRG", color: "#607d8b", layer: "ics206",    type: "point" },
  // ── Wildfire / Smoke ──
  { key: "air_quality_monitor", label: "Air Quality Monitor",shortCode: "AQI", color: "#e64a19", layer: "situation", type: "point" },
  { key: "smoke_shelter",       label: "Smoke Shelter",      shortCode: "SSh", color: "#e64a19", layer: "situation", type: "point" },
  { key: "resource_staging",    label: "Resource Staging",   shortCode: "RST", color: "#e64a19", layer: "ics204",    type: "point" },
  { key: "observed_perimeter",  label: "Observed Perimeter", shortCode: "OBP", color: "#e64a19", layer: "situation", type: "path"  },
];

/** Universal drawing tools appended to every layer's palette. */
export const DRAWING_TOOLS: SymbolDef[] = [
  { key: "generic_point", label: "Labeled Marker", shortCode: "●", color: "#9e9e9e", layer: "situation", type: "point" },
  { key: "freehand_path", label: "Freehand Draw",  shortCode: "✏", color: "#888888", layer: "situation", type: "path"  },
  { key: "text_label",    label: "Text Label",      shortCode: "T", color: "#ffffff", layer: "situation", type: "point" },
];

export const SYMBOLS_BY_LAYER: Record<AnnotationLayer, SymbolDef[]> = {
  situation: [...SYMBOL_DEFS.filter(s => s.layer === "situation"), ...DRAWING_TOOLS],
  ics204:    [...SYMBOL_DEFS.filter(s => s.layer === "ics204"),    ...DRAWING_TOOLS],
  ics205:    [...SYMBOL_DEFS.filter(s => s.layer === "ics205"),    ...DRAWING_TOOLS],
  ics206:    [...SYMBOL_DEFS.filter(s => s.layer === "ics206"),    ...DRAWING_TOOLS],
  evac:      [...SYMBOL_DEFS.filter(s => s.layer === "evac"),      ...DRAWING_TOOLS],
};

// ── Hazard types ──────────────────────────────────────────────────────────────

export type HazardType =
  | "flood"
  | "hazmat"
  | "mass_casualty"
  | "wildfire_smoke"
  | "severe_weather"
  | "infrastructure"
  | "evacuation"
  | "other";

export type ICSFormId =
  | "ics201" | "ics202" | "ics203" | "ics204" | "ics205" | "ics206"
  | "ics207" | "ics208" | "ics213" | "ics215" | "ics215a" | "ics214"
  | "full-iap";

export interface HazardDef {
  key: HazardType;
  label: string;
  icon: string;
  color: string;
  zoneNames: string[];
  zoneColors: string[];
  relevantForms: ICSFormId[];
  defaultRadius: number; // km — for OSM fetch
}

export const HAZARD_DEFS: HazardDef[] = [
  { key: "flood",          label: "Flood / Water",       icon: "🌊", color: "#1565c0", zoneNames: ["Evacuation Zone","Advisory Zone","Shelter Zone"],  zoneColors: ["#f44336","#ff9800","#2196f3"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","ics208","full-iap"],        defaultRadius: 30 },
  { key: "hazmat",         label: "HAZMAT",              icon: "☣️", color: "#fdd835", zoneNames: ["Hot Zone","Warm Zone","Cold Zone"],                zoneColors: ["#f44336","#ff9800","#4caf50"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","ics208","ics215a","full-iap"], defaultRadius: 15 },
  { key: "mass_casualty",  label: "Mass Casualty (MCI)", icon: "🚑", color: "#c62828", zoneNames: ["Scene Perimeter","Staging","Rehab Area"],          zoneColors: ["#f44336","#ff9800","#4caf50"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","full-iap"],                defaultRadius: 10 },
  { key: "wildfire_smoke", label: "Wildfire / Smoke",    icon: "🔥", color: "#e64a19", zoneNames: ["Evacuation Order","Evacuation Alert","Advisory"],  zoneColors: ["#f44336","#ff9800","#ffeb3b"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","ics208","full-iap"],        defaultRadius: 50 },
  { key: "severe_weather", label: "Severe Weather",      icon: "🌪️", color: "#6a1b9a", zoneNames: ["Impact Zone","Warning Area","Watch Area"],         zoneColors: ["#f44336","#ff9800","#9c27b0"], relevantForms: ["ics201","ics202","ics205","ics206","full-iap"],                         defaultRadius: 40 },
  { key: "infrastructure", label: "Infrastructure",      icon: "⚡", color: "#37474f", zoneNames: ["Outage Zone","Affected Area","Restoration Zone"],   zoneColors: ["#607d8b","#90a4ae","#4caf50"], relevantForms: ["ics201","ics202","ics204","ics205","full-iap"],                         defaultRadius: 20 },
  { key: "evacuation",     label: "Mass Evacuation",     icon: "🚶", color: "#00838f", zoneNames: ["Mandatory Evac","Voluntary Evac","Shelter Area"],   zoneColors: ["#f44336","#ff9800","#00bcd4"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","full-iap"],                defaultRadius: 30 },
  { key: "other",          label: "Other / General",     icon: "📋", color: "#546e7a", zoneNames: ["Zone A","Zone B","Zone C"],                        zoneColors: ["#f44336","#ff9800","#4caf50"], relevantForms: ["ics201","ics202","ics204","ics205","ics206","full-iap"],                defaultRadius: 20 },
];

// ── Hazard zones (manually drawn on map) ─────────────────────────────────────

export interface HazardZone {
  id: string;
  name: string;          // "Hot Zone", "Evacuation Order", etc.
  color: string;         // from HAZARD_DEFS zoneColors
  polygon: [number, number][]; // GeoJSON ring [lng, lat]
  createdAt: string;
}

// ── Annotations ───────────────────────────────────────────────────────────────

export interface IncidentAnnotation {
  id: string;                           // crypto.randomUUID()
  layer: AnnotationLayer;
  type: "symbol" | "path" | "text";
  symbolKey: ICSSymbolKey;
  /** [[lng, lat], ...] — GeoJSON coordinate order. Single-element for point symbols. */
  coordinates: [number, number][];
  label: string;
  color?: string;                       // overrides SymbolDef default when set
  properties: Record<string, string>;   // form-specific: contact, capacity, frequency
  operationalDay: number;               // 1-based
  createdAt: string;
}

// ── Operational period ────────────────────────────────────────────────────────

export interface EvacDecisionRecord {
  id: string;
  timestamp: string;
  zones: { tier: "Order" | "Alert" | "Watch"; communities: string[]; areaHa: number }[];
}

export interface OperationalPeriod {
  day: number;                          // 1, 2, 3...
  date: string;                         // "2026-04-05"
  opPeriodStart: string;                // "08:00"
  opPeriodEnd: string;                  // "20:00"
  weather: WeatherParams;
  ignitionPoint: { lat: number; lng: number } | null;
  hazardZones: HazardZone[];
  annotations: IncidentAnnotation[];
  evacuationDecisions: EvacDecisionRecord[];
  objectives: string[];                 // ICS-202 objectives for this period
  situationNarrative: string;
}

// ── ICS Section model ─────────────────────────────────────────────────────────

export type ICSSection =
  | "command"     // IC + Command Staff (Safety, PIO, Liaison)
  | "operations"  // Operations Section
  | "planning"    // Planning Section
  | "logistics"   // Logistics Section
  | "finance"     // Finance/Admin Section
  | "other";      // Unassigned pool

export const ICS_SECTION_META: Record<ICSSection, { label: string; abbrev: string; color: string }> = {
  command:    { label: "Command Staff",         abbrev: "CMD",  color: "#ffd54f" },
  operations: { label: "Operations Section",    abbrev: "OPS",  color: "#ef5350" },
  planning:   { label: "Planning Section",      abbrev: "PLAN", color: "#42a5f5" },
  logistics:  { label: "Logistics Section",     abbrev: "LOG",  color: "#66bb6a" },
  finance:    { label: "Finance/Admin Section", abbrev: "FIN",  color: "#ab47bc" },
  other:      { label: "Unassigned",            abbrev: "—",    color: "#607d8b" },
};

export const ICS_POSITIONS_BY_SECTION: Record<ICSSection, string[]> = {
  command: [
    "Incident Commander",
    "Deputy Incident Commander",
    "Safety Officer",
    "Public Information Officer",
    "Liaison Officer",
  ],
  operations: [
    "Operations Section Chief",
    "Deputy Operations Section Chief",
    "Branch Director",
    "Division Supervisor",
    "Group Supervisor",
    "Air Operations Branch Director",
    "Air Tactical Group Supervisor",
    "Staging Area Manager",
  ],
  planning: [
    "Planning Section Chief",
    "Resources Unit Leader",
    "Situation Unit Leader",
    "Documentation Unit Leader",
    "Demobilization Unit Leader",
    "Technical Specialist",
  ],
  logistics: [
    "Logistics Section Chief",
    "Service Branch Director",
    "Communications Unit Leader",
    "Medical Unit Leader",
    "Food Unit Leader",
    "Support Branch Director",
    "Facilities Unit Leader",
    "Ground Support Unit Leader",
    "Supply Unit Leader",
  ],
  finance: [
    "Finance/Admin Section Chief",
    "Time Unit Leader",
    "Procurement Unit Leader",
    "Compensation/Claims Unit Leader",
    "Cost Unit Leader",
  ],
  other: [],
};

// ── Incident session ──────────────────────────────────────────────────────────

// Inline types for Resource and Agency (avoids circular imports from components)
export interface IncidentResource {
  id: string;
  kind: "person" | "equipment" | "vehicle";
  name: string;
  icsSection: ICSSection;      // explicit section assignment
  icsPosition?: string;        // canonical position from ICS_POSITIONS_BY_SECTION
  role?: string;               // freeform supplemental title
  agency: string;
  typeRating?: "T1" | "T2" | "T3" | "T4" | "T5";
  status: "available" | "assigned" | "released" | "oos";
  assignedDivision?: string;
  notes?: string;
}

export interface IncidentAgency {
  id: string;
  name: string;
  role: string;
  liaison: string;
  phone: string;
  isUnifiedCommand: boolean;
}

export interface IncidentSession {
  id: string;
  name: string;
  incidentNumber: string;
  incidentCommanderName: string;
  hazardType: HazardType;
  incidentComplexity: 1 | 2 | 3 | 4 | 5;
  status: "active" | "closed";
  createdAt: string;
  updatedAt: string;
  activePeriodIndex: number;            // 0-based index into operationalPeriods
  operationalPeriods: OperationalPeriod[];
  resources: IncidentResource[];
  agencies: IncidentAgency[];
  shareCode?: string;   // Set when incident is uploaded to cloud sync
  syncedAt?: string;    // ISO timestamp of last successful cloud sync
}

// ── Factory helpers ───────────────────────────────────────────────────────────

export function makeOperationalPeriod(day: number, date: string): OperationalPeriod {
  return {
    day,
    date,
    opPeriodStart: "08:00",
    opPeriodEnd: "20:00",
    weather: { wind_speed: 20, wind_direction: 180, temperature: 15, relative_humidity: 50, precipitation: 0 },
    ignitionPoint: null,
    hazardZones: [],
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
    hazardType: "other",
    incidentComplexity: 3,
    status: "active",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    activePeriodIndex: 0,
    operationalPeriods: [makeOperationalPeriod(1, today)],
    resources: [],
    agencies: [],
  };
}
