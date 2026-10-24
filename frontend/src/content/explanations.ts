/**
 * Explanations, caveats and source notes: one place for the text behind the info tooltips
 * (components/InfoTip.tsx, Badge.tsx), the About & sources tab (components/AboutPanel.tsx) and
 * the e2e tests, so the three never drift apart.
 *
 * Owner direction 2026-10-10: keep the screen to labels, values and short badges; the
 * explanation sits one hover, focus or tap away, and the literature in About & sources.
 * Safety-relevant statuses stay visible as short badges (see BADGES).
 */

import { ARRIVAL_BUFFER_M } from "../utils/evacZones";
import { HFI_CLASS_CAVEAT, HFI_CLASS_SOURCE } from "../utils/fireClasses";

/** GitHub links (the About tab and tips with an evidence link). */
export const REPO_URL = "https://github.com/Tphambolio/wildfire-simulator";
export const DOCS_URL = `${REPO_URL}/blob/master/docs`;
export const doc = (file: string, anchor = "") => `${DOCS_URL}/${file}${anchor ? `#${anchor}` : ""}`;

/** Short visible badge texts (at most four words) for safety- or science-essential statuses. */
export const BADGES = {
  lowSkill: "Low one-day skill",
  notSaved: "Not saved",
  rangeTooNarrow: "Range too narrow",
  modelOutput: "Model output",
  unsourced: "Unsourced",
  illustrative: "Illustrative",
  scenario: "Scenario",
  exposureNotIgnition: "Exposure, not ignition",
  setByPlanning: "Status set by Planning",
  c2Generalisation: "C-2 generalisation",
  singleRun: "Single run",
  d2NoSpread: "D-2 aspen won't burn",
  pastStart: "Past start: hindcast",
  needsGrid: "Needs Edmonton grid",
  needsHourly: "Needs hourly forecast",
  notMultiDay: "Not for multi-day",
} as const;

export const TIPS = {
  outbuildings:
    "Footprints under 20 m² (sheds, small garages) are outbuildings. They still burn and pass " +
    "fire between buildings, but are not counted as buildings above, so towns whose maps " +
    "include sheds compare with towns whose maps do not. DINS also reports minor structures " +
    "separately.",
  // ── Top bar ──
  lowSkill:
    "Training and planning tool, not an operational forecast. The FBP equations match the cffdrs " +
    "reference and spread was compared with WISE, but on 143 held-out Alberta fire-days one-day " +
    "skill was low (F1 about 0.15–0.24) and growth was over-predicted on most days. Select for " +
    "limits and validation in About & sources.",
  notSaved:
    "This scenario is not saved. Open or create an incident in the EOC Console, or save the " +
    "scenario under Incidents & saved scenarios in Setup. Incidents and the Setup panel are " +
    "stored in this browser only; they survive reloads, including hard reloads, and are cleared " +
    "with \"Reset this browser's FireSim data\" in About & sources.",
  incidentStorage:
    "Incidents and the Setup panel are stored in this browser only, not on the server, and " +
    "survive reloads, including hard reloads. Export an incident as JSON to keep or move it; " +
    "\"Reset this browser's FireSim data\" (About & sources) clears them.",
  fwiClass:
    "CWFIS national FWI map class (Low 0–5, Moderate 6–15, High 16–22, Very High 23–29, " +
    "Extreme 30+). Not an official fire danger rating.",

  // ── Setup: 1 Ignition & time ──
  preset: "Example starting points for exercises, not climatology. Check them against today's weather.",
  coords:
    "Decimal (53.4606, -113.6597) or degrees-minutes-seconds (53°27'38\"N 113°39'35\"W). Paste " +
    "a pair into either field.",
  ignitionHowTo:
    "Click the map, or Tab to the map, move the crosshair with the arrow keys (Shift: larger " +
    "steps) and press Enter. Ctrl+Enter runs the simulation from anywhere.",
  startNow:
    "Start at the current time. Follows the clock until you set a date or time. The timeline and " +
    "Situation times count from the start (America/Edmonton).",
  hindcast:
    "The start is in the past, so the hourly forecast is used as a hindcast (Open-Meteo keeps one " +
    "past day).",

  // ── Setup: 2 Weather & FWI ──
  hourlyForecast:
    "Wind, temperature, RH and rain change hour by hour (Open-Meteo forecast for the ignition " +
    "point); FFMC follows the hourly FFMC model from the FFMC above.",
  weatherSchedule:
    "Optional step changes during this run, e.g. 47 km/h from the start, then 20 km/h from " +
    "20:00. Each row applies from its local time until the next; the weather above applies " +
    "from the start. FFMC and ISI are recomputed hourly from the FFMC above (hourly FFMC " +
    "model); DMC and DC stay fixed. The range of outcomes perturbs every step. A restart from " +
    "an observed perimeter uses the step in force at its time and the later ones.",
  multiDayWeather: "In multi-day mode the weather is entered per day under 4 Run options.",
  // The station fill's wind and RH read as the run's weather in multi-day mode (72 h rerun, 2026-10-17)
  multiDayReplaced:
    "A multi-day run uses only these FWI start codes; each day's wind, RH and temperature are set under 4 Run options.",
  d2Bui: (bui: number) =>
    `BUI ${bui.toFixed(0)} is below 80: green aspen (D-2) does not carry fire in the FBP System ` +
    "(Alexander 2010). Raise DMC/DC for a drier scenario, or use D-1 (leafless) in spring.",
  updateCodes: "Update FFMC, DMC and DC from the weather inputs above.",
  loadWeather:
    "Load the current weather and FWI codes from the nearest CWFIS station for the ignition " +
    "point. This replaces the values above.",
  weatherManual:
    "The weather and FWI values were set by you, a preset or a loaded scenario. Moving the " +
    "ignition keeps them; use Update weather for this location to load the nearest station's values.",
  weatherFar: (km: number) =>
    `The ignition is now about ${Math.round(km)} km from where the weather was set. The values ` +
    "are kept; use Update weather for this location to load the nearest station's values.",

  // ── Setup: 3 Fuel & landscape ──
  edmontonGrid: (fallbackFuel: string) =>
    "City of Edmonton canopy-LiDAR FBP fuel product: 20 m cells (EPSG:3776; the file name says " +
    "10 m), fuel types C-2, D-2, M-2, O-1a and O-1b. The run uses 50 m cells (the main fuel " +
    "type in each); when buildings are near the fire it is repeated on the native 20 m cells " +
    "in a window around the fire, if the fire is small enough. " +
    `Cells without fuel data use ${fallbackFuel}. ` +
    "Outside the City grid's mapped area the Alberta Wildfire provincial grid is used " +
    "(see the Alberta grid tip).",
  albertaGrid:
    "Alberta Wildfire FBP fuel types 2025, 100 m (Government of Alberta). Used where the City " +
    "grid has no data and for ignitions outside it (a 40 km window around the ignition, 50 m " +
    "cells). D-1/D-2 aspen and M-1/M-2 mixedwood (percent conifer from the map) are leaf-on " +
    "30 May-14 Sept; O-1 grass runs as standing grass (O-1b); vegetated non-fuel is non-fuel. " +
    "Partly built from the Alberta Vegetation Inventory; it maps little vegetation inside " +
    "Edmonton. Outside Alberta the fuel type chosen in Setup is used. Contains information " +
    "licensed under the Open Government Licence – Alberta.",
  syntheticCA: "Generates a 5 km mixed-fuel demo grid around the ignition point and runs the grid model on it.",
  spotting:
    "Crown fires loft embers downwind and seed new ignitions. Maximum distance from Albini (1979); " +
    "emission, probability and landing are heuristic.",
  waterMask:
    "OpenStreetMap water polygons as extra non-fuel. Off by default: it covers about 3,700 ha the " +
    "LiDAR grid maps as vegetation, and the grid already maps water as non-fuel.",
  buildings:
    "Microsoft Canadian Building Footprints (2019; © Microsoft, Open Database License ODbL) " +
    "across Alberta, including Edmonton (346,238 in the Edmonton area); in Calgary, " +
    "Lethbridge, Grande Prairie, Airdrie, Okotoks, Cochrane, Canmore, Banff and Parkland " +
    "County the municipality's own footprints " +
    "(Statistics Canada Open Database of Buildings), which include sheds and garages, so counts " +
    "there are higher. Used for non-fuel cells, exposure counts and house-to-house spread. " +
    "Coverage varies; some towns are incomplete.",
  structureOption:
    "Fire passing from building to building (Hamada model), from the buildings the modelled front " +
    "reaches; a building stops passing fire when its 66 min design fire ends (burnt out). " +
    "Illustrative, not validated in Canada. Needs the Edmonton grid and buildings.",
  structureEmbers:
    "Buildings also ignited by short-range embers from burning buildings and the front (published " +
    "Californian firebrand model, 150 kW/m² design fire). Illustrative. Needs house-to-house spread.",
  structureVegBridge:
    "Buildings up to 20 m apart pass fire as usual; 20–45 m apart only where trees cover at least " +
    "20 % of the gap (open 1 m canopy map). FireSim rule from the FPInnovations Jasper threshold; " +
    "not validated.",
  structureRoofs:
    "A scenario, not observed roofs: no open data says which roofs are wood shake. This share of " +
    "buildings, picked at random with the run seed, ignite from embers at 0.375 of the threshold " +
    "(lab firebrand tests, cedar vs treated pine). Needs ember ignition.",
  wui:
    "425 park-buffer zones with spread ×0.7, intensity ×1.2 and embers ×3.0. These values have no " +
    "documented source; leave off unless testing.",
  dem: "Slope from the Edmonton 30 m DEM, and outside Edmonton from NRCan MRDEM-30 (~100 m), applied through the FBP net effective wind speed (ST-X-3).",

  // ── Setup: 4 Run options ──
  ensemble: (n: number) =>
    `Re-runs the fire ${n} times with varied wind, moisture and spread rate after the single run: ` +
    "about 1 s per member on the server, longer for large fires. The range is narrower than the " +
    "real uncertainty (about half of observed days fall inside the 10–90 % range).",
  ensembleNeedsGrid: "The range of outcomes needs the Edmonton fuel grid (grid runs only).",
  burningPeriod:
    "No spread outside these local hours. The 10:00–20:00 default was chosen on held-out Alberta " +
    "fires: with the spin-up and RPAS active edges it raised one-day skill (F1 0.12 to 0.21).",
  spinUp:
    "Starts the hourly FFMC at 17:00 the evening before from the FFMC in 2, so morning spread is " +
    "not overstated (Lawson et al. 1996). Needs hourly forecast weather; not for multi-day runs.",
  multiDay:
    "FWI codes (FFMC, DMC, DC) carry forward each day with the CFFDRS equations; the fire front " +
    "continues from the previous day's perimeter. Each day runs 24 h from the start time; with a " +
    "burning period, no spread is modelled outside it. Each day takes longer to compute than the " +
    "one before, as the fire grows. On the shared server a 3-day run with the burning period " +
    "takes a few minutes when the server has been idle and up to about 45 minutes after other " +
    "heavy runs (its CPU is throttled once a burst allowance is used up); without the burning " +
    "period a large fire can take several hours. The time left shown is for the day being " +
    "computed. No ember spotting, buildings or ensemble.",
  burnProbability:
    "Monte Carlo burn probability: repeated runs with jittered ignition, wind speed and RH. More " +
    "iterations are slower and smoother.",
  runShortcut: "Ctrl+Enter runs from anywhere.",

  // ── Setup: More ──
  ownLayer:
    "GeoJSON in WGS84. Points and polygons are treated as assets (a category property is used " +
    "when it matches a FireSim category), lines as roads (named by name or ref). The layer stays " +
    "in this browser tab.",
  isochrones:
    "Modelled time for the fire front to reach each place: rings at each interval after ignition, " +
    "red sooner, green later.",
  reconRestart: (at: string) =>
    `Restarts the run at ${at} (the timeline's selected time) from an observed fire perimeter, ` +
    "with only its active edges spreading. It uses Setup's current weather, FWI codes and " +
    "weather changes (not the earlier run's), runs the full Predict time, and keeps the " +
    "earlier run's arrival times near communities and assets.",
  reconWeather:
    "The restart uses these Setup values (Weather & FWI), with any weather changes after the " +
    "restart time. Change them in Setup before restarting.",
  runChips:
    "Weather the shown run used (with weather changes: the values in force at the timeline's " +
    "time; multi-day runs: the shown day's weather and FWI). Setup edits apply from the next run or restart.",
  runShort: (shownTo: string, hours: number) =>
    `The run was asked for ${hours} h but its frames end at ${shownTo}. The model always runs to the end, ` +
    "so frames were lost on the way (e.g. a dropped connection). Reload run reads it again from the server.",
  runReattached:
    "The page was reloaded while this tab had a run on screen. The run is read again from the " +
    "server by its ID (no passphrase needed for this tab's own run); a run still computing is " +
    "followed to the end. Clear results removes it.",
  activeEdges:
    "Edges not marked active are treated as burned out and do not spread. This raised one-day " +
    "skill on held-out Alberta fires.",
  bufferBlank: "Blank: one fuel-grid cell (the validated default).",
  observationTime:
    "When the perimeter was observed (T). The forecast starts at T, and hotspots or VIIRS " +
    "detections count only in the 12 h up to T.",
  segmentClassification:
    "Each 250 m piece of the perimeter is active if a hotspot or a VIIRS 375 m detection " +
    "(nominal or high confidence) lies within 1 km of it in the 12 h before T. Active pieces " +
    "become the active edges; the rest is treated as burned out, as with edges you mark. " +
    "Hotspots take priority over VIIRS. Not validated: the 1 km / 12 h / 250 m rule has not " +
    "been checked against real RPAS thermal flights.",
  staticShare:
    "Modelled share of the start perimeter that advanced 1 km or less (500 m samples). The " +
    "observed share is not known during an incident; compare it after the fact.",

  // ── Situation panel ──
  outsideBurning: (period: string, from: string) =>
    `Outside the burning period (${period}): no spread is modelled until ${from}.`,
  singleRun: "The deterministic run with the inputs as set. It is not the ensemble median.",
  p10:
    "P10 arrival: at least one member in ten brings the fire to a place this early. It is the " +
    "early end of the modelled range, not a worst case: on held-out Alberta fires part of the " +
    "observed growth fell outside the P10 footprint on most days. Map lines are P10 arrival " +
    "times in clock time.",
  p50: "Median (P50): half of the members reach this extent by the selected time.",
  p90: "P90 footprint: the area at least 9 in 10 members reach by the end of the run.",
  endRange: "Burned area at the end of the run across all members: smallest, median and largest.",
  probability: "Burn probability: the share of members that burn each cell. The single run is hidden while it is shown.",
  rangeTooNarrow:
    "Members vary wind, fuel moisture, curing and spread rate by amounts set from Alberta " +
    "forecast errors and observed fires, but on held-out fires the observed one-day area fell " +
    "inside the members' 10–90 % range on only about half the days, and FireSim usually " +
    "over-predicts one-day growth. Use the P10 line as a planning margin, not a forecast or a " +
    "worst case.",
  gridModel: "Level-set spread on the fuel grid (one FBP ellipse per cell).",
  huygensModel: "Huygens wavelet perimeter spread on uniform fuel.",
  spotDistance: "Albini maximum spotting distance from the head (surface or torching-tree model).",
  exposure:
    "Exposure, not ignition probability. Radiant heat uses Cohen's (2004) worst-case flame model, " +
    "which overestimates measured flux; embers are not modelled; building-to-building fire only " +
    "with House-to-house spread.",
  neighbourhoods: (ensemble: boolean) =>
    `Modelled fire within ${ARRIVAL_BUFFER_M} m of each neighbourhood for this run` +
    (ensemble ? ": the ensemble's P10 (early end of the range, 1 in 10 members) first, the single run beside it." : ".") +
    " Times on a later day than the start read \"D2 16:25\" (D2 = the day after the start date)." +
    " Evacuation status is set by Planning: FireSim does not recommend evacuation tiers.",
  evacSaved: (incident: string | null) =>
    incident ? `Saved with the incident "${incident}".` : "Saved in this browser (no incident open).",
  criticalAssets: (ensemble: boolean) =>
    `When the modelled fire comes within ${ARRIVAL_BUFFER_M} m of each asset, and inside it, for this run` +
    (ensemble ? ": the ensemble's P10 (early end of the range, 1 in 10 members) first, the single run beside it" : "") +
    ". Times on a later day than the start read \"D2 16:25\" (D2 = the day after the start date)." +
    " Model output, not an instruction.",
  albertaAssets:
    "Outside Edmonton the assets and major roads come from Statistics Canada's healthcare facilities " +
    "(ODHF, 2020) and OpenStreetMap, for about 28 km around the ignition. Coverage depends on what " +
    "is mapped there; each asset is tagged with its source.",
  assetsReachedEoc: (ensemble: boolean) =>
    `Model output for this run (${ensemble ? "ensemble P10 / single run" : "single run"}), not an instruction.`,
  burnProbEoc: (iterations: number | string, cellM: number) =>
    `Monte Carlo burn probability from ${iterations} iterations on ${cellM.toFixed(0)} m cells. Model output, not an instruction.`,
  hfiClassCaveat: HFI_CLASS_CAVEAT,
  hfiClassSource: HFI_CLASS_SOURCE,

  newIgnition:
    "Click the map to place a new ignition (or drag the ignition marker, or type coordinates in " +
    "Setup). The other inputs stay as set; press Run to re-run there. The map view is kept.",
  clearResults: "Remove this run's fire, ensemble and timeline from the map and panels. Inputs stay as set.",

  // ── Map ──
  fireLegend: "Burned cells weighted by head fire intensity. Zoom in (level 14 and closer) for the crown fire state of each cell.",

  // ── EOC console ──
  eocResume: "Or resume an existing incident under Incidents & saved scenarios in the Setup column. You can rename the incident at any time from the period strip.",
  eocSnapshot: "The current map state is captured as a snapshot for the embedded maps.",
} as const;
