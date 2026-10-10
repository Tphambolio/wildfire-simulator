# Model card: FireSim

A short, citable statement of what FireSim is for, what evidence supports it, and where it must
not be relied on. Keep it current with every change to the engine or its evidence.

## Summary

FireSim simulates wildfire growth and fire behaviour with the Canadian Forest Fire Behavior
Prediction (FBP) System and reports building exposure, for municipal EOC planning,
preparedness and training. Version: `GET /api/v1/version` returns the deployed git commit,
which identifies the exact model on any output.

## Intended use

- **Now:** preparedness, training, tabletop exercises and what-if planning by EOC Planning
  sections and fire officers: how a fire could grow under given weather, where intensity
  allows which suppression resources, which neighbourhoods and roads could be reached and
  roughly when, and how sensitive this is to the inputs.
- **Later (not yet supported by evidence):** operational fire-growth prediction during
  incidents, once validation against observed fires (below) shows skill comparable to the
  operational Canadian models (Prometheus / WISE).

## Users

EOC Planning section staff (Situation Unit, Plans Chief), emergency management and fire
officers trained in the FBP System. Outputs assume the reader knows what FBP fuel types,
head fire intensity and FWI codes mean.

## Out-of-scope uses

- Deciding evacuation tiers or timing. FireSim reports fire arrival and exposure; the
  decision belongs to the Director of Emergency Management with the Incident Commander.
  FireSim does not suggest tiers.
- Predicting which buildings will ignite or be lost. Exposure is not ignition probability,
  and the opt-in structure-spread counts are illustrative modelled involvement, not losses.
- Firefighter or aircraft safety distances. FireSim prints none (earlier unsourced RPAS and
  personnel stand-off rules were removed).
- Replacing the lead agency's prediction (e.g. Alberta Wildfire, Parks Canada) where that
  agency has jurisdiction; compare with it, label which is authoritative.
- Fires dominated by pyroconvection or plume dynamics; FBP spread does not represent them.

## Model

| Part | Method | Source |
|---|---|---|
| Fire behaviour | FBP System, 18 fuel types | ST-X-3 (1992), GLC-X-10 (2009); `cffdrs` |
| Weather / moisture | FWI System; hourly FFMC | Van Wagner & Pickett (1985); Van Wagner (1977) PS-X-69; `cffdrs` |
| Diurnal burning (opt-in in the API, on by default in the UI) | Hourly FFMC spin-up from 17:00 local before the start; burning period (spread only between set hours, 10-20 h validated; a point ignition outside it waits for it) | Beck et al. (2002); Lawson et al. (1996); Tymstra et al. (2010) |
| Growth (spatial fuel) | Level set advected with the Huygens velocity of each cell's FBP ellipse | Richards (1990); Lautenberger (2013) |
| Growth (uniform fuel) | Huygens wavelets (convex front) | Richards (1990) |
| Crown fire | Van Wagner (1977) initiation; CFB eq 58; C-6 crown rate | ST-X-3 |
| Flame length | Byram (surface), Thomas (CFB >= 0.1; approximate for crown fires) | Byram's form: Alexander & Cruz (2012) Table 1, p.98; Thomas for crown fires suggested by Rothermel (1991), via Alexander & Cruz (2012) p.99 |
| Spotting (opt-in) | Albini/Chase/Morris maximum distance; heuristic emission and landing | USDA FS INT reports 1979-1987 |
| Building exposure | Distance bands; Cohen solid-flame radiant flux; flux-time index | Cohen (2004); NRC (2021) |
| Structure-to-structure spread (opt-in, API only; **illustrative — not validated in Canada**) | Hamada empirical urban-fire spread between building units (one per footprint), started where the FBP front reaches a grid cell next to one the footprint touches (10 m contact, measured from the building's cells; 2026-10-09); 30 m neighbour cutoff | Purnomo et al. (2026) FSJ 104651; Qin (2025); Himoto & Tanaka (2008); `docs/structure-spread-spec.md` |
| Burn probability | Monte Carlo over ignition point, wind speed and RH | (method, not a validated product) |
| Classes | HFI classes 1-6; FWI classes | Cole & Alexander (1995) and CWFIS HFI map; CWFIS FWI map |

Inputs: ignition point or observed perimeter (optionally with its active edges: lines drawn
along the perimeter or whole sides, from an RPAS thermal flight; the rest is treated as burned
out), start time, weather (constant or hourly, optionally from 17:00 the evening before for
the FFMC spin-up), FWI codes, burning period (local hours), FBP
fuel grid (Edmonton: City canopy-LiDAR product), optional DEM, water and building masks,
grass curing, percent conifer / dead fir, foliar moisture or date. Grass curing (decision M1,
2026-10-10): 95 % by default from 1 March to 29 May (day of year 60-149, between snow-melt and
green-up), a required entry outside that window whenever O-1 grass can burn (API 422; the UI
shows the field as required and suggests the last value entered). Before, a fixed 60 % default
ran grass at a fifth of its fully cured spread (curing factor 0.20, GLC-X-10 eq 35b, p.9);
95 % gives 0.90. Why 95 %: GLC-X-26 (p.24) uses 90 % when curing is not observed, and grass
before green-up is last season's fully cured growth (Pickell et al. 2017; Beverly & Schroeder
2025, p.14). The window dates are FireSim's choice. The UI defaults are the
held-out validated set-up: burning period 10-20 h, spin-up when hourly forecast weather is
used, active edges when marked; the API defaults are off. Outputs: perimeters,
burned cells with arrival time, speed and head/flank/back, head summary, arrival-time grid,
intensity and fire type, spot fires, building exposure, burn probability, and the first
time the modelled fire is within 500 m of (and inside) each neighbourhood and critical asset
and on each major road (single run and ensemble P10; clock time, model output
only).

Bundled reference layers (Edmonton; loaded with the Edmonton fuel grid): critical assets (624)
from City of Edmonton Open Data, the Government of Alberta continuing care list (June 2026,
geocoded with City address points), Statistics Canada ODHF (2020) and OpenStreetMap (ODbL),
every source recorded per asset; major roads (motorway to secondary with ramps, OSM
2026-10-08); neighbourhoods from City Open Data. No manual points (the EOC is left out: no
public source gives its location). Sources, licences,
dates, match rates and the refresh commands: `docs/data-sources.md`. Asset arrival depends on
the completeness and positions of these layers: group homes and care sites under 10 units are
not included, 2 Alberta sites and 2 ODHF records could not be positioned, and 13 care sites in
no current official list are marked "verify". Burn probability (P ≥ 25/50/75 % areas in the
EOC summary and ICS-209) is labelled as Monte Carlo/ensemble model output.

## Evidence

| Evidence | Result | Reference |
|---|---|---|
| Verification of FBP and FWI equations | Matches CFS `cffdrs` to floating-point precision (12,960 FBP cases; hourly FFMC 5,760 cases) | docs/verification.md §1 |
| Spread models vs the FBP ellipse | Burned area 0.91-1.07 x the FBP ellipse on uniform fuel, for any wind direction relative to the grid (since 2026-10-09; before, grid-model point ignitions with the wind on a grid diagonal were 10-13 % small in C-2/M-1 and in grass as much as 93 % small: 3 cells instead of 43) | docs/verification.md §2 |
| Comparison with WISE (independent Canadian model) | Uniform fuel, flat and sloped: area ratio 0.955-1.09, head distance ratio 0.98-1.05 | docs/verification.md §3 |
| Head / flank / back | Fastest head cell = FBP ROS; rearmost back cell = BROS; head direction within 2° of RAZ incl. slope | engine/tests/spread/test_deployment_data.py |
| Spotting maximum distance | Reproduces published worked examples | engine/tests/spread/test_albini.py |
| Radiant exposure | Reproduces Cohen (2004) worked values within 3.5 % | engine/tests/test_exposure.py |
| Structure spread (Hamada) | Equations and coefficients as published (hand-checked values, zero-wind isotropy, monotone in wind); Qin (2025)'s worked rate of 0.34 m/s is **not** reproduced (equations give 0.197 m/s; unexplained). No validation against observed structure losses anywhere in Canada | engine/tests/structures/; `docs/structure-spread-spec.md` §4.5, §8 |
| **Observed fires (first results)** | 143 fire-days, 32 Alberta fires 2014-2024 (CFSDS), one burn day from the observed perimeter, Bennett et al. (2026) protocol. F1 at the default 06-23 h window: 0.15 started from the whole perimeter (operational), 0.24 with Bennett's ignition (WISE: 0.26); best burn hour 0.22 / 0.38 (WISE 0.50). Growth over-predicted on 73-93 % of days (normalised area difference +0.29 to +0.67); head direction error median about 50°; spread distance within ±35 % on about 20 % of days. Head runs on the largest Horse River days under-predicted. | docs/validation.md |
| **Observed fires, held-out test (second round)** | Fires split in half by fire; settings chosen on 16 calibration fires, scored on 16 unseen fires (79 fire-days). Marking active edges (previous days' growth, as an RPAS thermal flight would), FFMC spin-up from the previous afternoon and a 10-20 h burning period: one-day F1 0.118 -> 0.208 (+0.091, 95 % CI +0.056 to +0.125, bootstrap by fire); area difference +0.72 -> +0.27; spread distance within ±35 % on 24 % of days. Oracle start (Bennett ignition): 0.245. The biggest wind-driven runs are under-predicted further. Deterministic skill; RPAS mid-day restarts not yet measured. | docs/validation.md |
| **Ensemble calibration (third round)** | Perturbation sizes from an Alberta input-error climatology (GEM day-1 and ERA5 vs 14 ECCC stations, 2024-25; curing from Anderson et al. 2011; ROS from Cruz & Alexander 2013) x one inflation factor (1.5) chosen on the calibration fires; 20 members, scored on 65 held-out fire-days (14 largest excluded by run time). Observed one-day area inside the members' P10-P90: 20 % -> 49 % (ideal ~80 %); CRPS of log area vs the single run: +11 % -> +24 % better; Brier skill vs single run +0.18 -> +0.25; spread/skill 4.7 -> 1.6 (still too narrow); cells at ~95 % burn probability burned 33 % of the time (over-confident: shared over-prediction). P50 F1 0.201 vs single run 0.195. **P10 is not a worst case:** its footprint held >= 90 % of observed growth on 29 % of days. | docs/validation.md |

For context, published single-day skill of operational FBP growth models on observed fires
is modest (WISE: F1 about 0.26 with default settings, about 0.54 tuned; Bennett et al. 2026),
and rate-of-spread models commonly err by 35-75 % (Cruz & Alexander 2013).

## Known limitations and biases

- Only a small validation on observed fires (above): one-day overlap is low (F1 about 0.2 at
  best with operational inputs) and growth is usually over-predicted when the whole perimeter
  is treated as active and the fire burns 06-23 h. Marking the active edges (`active_edges`)
  and a burning period reduce this but make the largest wind-driven runs more under-predicted.
  Show it as a range, not a line.
- The burning period is a fixed clock window: overnight runs and days that burn late are not
  represented (no spread outside it), and a run started at night shows no growth until it
  opens. Turn it off in Run options to model an overnight run.
- No suppression is modelled.
- Weather: constant or hourly; DMC and DC fixed within a run; forecast quality is
  Open-Meteo's. Wind direction is the dominant source of error in fire growth models.
- Grass curing is not observed: in spring FireSim assumes 95 % (an early or late green-up moves
  the real window by weeks); outside the window it is whatever the user enters. Grass is about
  3 % of the observed growth in the validation fires, so the CFSDS harness hardly tests it
  (docs/validation.md, "Grass curing default").
- Crown-fire flame length (Thomas 1963, suggested for crown fires by Rothermel 1991) is
  approximate: no flame-length method matched experimental crown fires consistently (Alexander
  & Cruz 2012, p.99).
- Spotting is illustrative; active crown fire spotting is underestimated.
- Burn probability varies only ignition, wind speed and RH (not a Burn-P3-style analysis).
- Huygens perimeters are convex; use the grid model on heterogeneous fuel.
- Edmonton fuel grid: urban trees are non-fuel. Structure-to-structure spread is only an
  opt-in, illustrative Hamada layer (API `structure_spread`): Japanese empirical coefficients,
  California-only published tests (recall 78-97 %, precision 9-77 %), FireSim's own 10 m
  contact and 30 m cutoff choices, no embers, no construction classes, no suppression; not
  validated in Canada. Front contact is measured from the building's own grid cells, so on the
  50 m grid it happens when the front reaches a cell next to the building's cells (typically
  20-40 m, up to ~70 m from the footprint); this removed a grid artefact of the first rule
  (contact 5 / 20 m had changed counts by −71 % to +557 %) and raised involved buildings by
  +38 % to +751 % on the test sites. Sensitivity on Edmonton footprints (2026-10-09,
  `scripts/structure_sensitivity.py`): involved buildings at 6 h change by −58 % to +170 % for a
  20 / 45 m cutoff, 0 % for a 5 / 20 m contact (below the grid's resolution), and −12 % to 0 %
  when footprints under 40 m² are dropped.
- Building exposure uses worst-case radiant assumptions and ignores embers, burning buildings
  and yard fuels (usually the main causes of loss).
- WUI zone modifiers in the repository have no source and are off by default.
- Time: America/Edmonton; Alberta moved to permanent UTC-6 on 2026-06-18 (IANA tzdata 2026c).
  Systems with older time zone data show MST after 2026-11-01.

## Responsible use

- State the issuer, run time, model version and inputs with any output shared beyond the
  Planning section; outputs carry the "not validated against observed fires" label.
- Do not present a single run as the expected fire; show its sensitivity to inputs.
- Re-check this card when the engine changes; keep `docs/verification.md` as the record.
