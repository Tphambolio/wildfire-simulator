# FireSim — Project Record

Living record of the work, decisions, references and testing behind **FireSim**, the
Canadian FBP wildfire spread simulator for municipal EOC planning. This file in the repo
(`docs/PROJECT_RECORD.md`) is the source of truth; copies attached elsewhere are snapshots.
Append to it; don't rewrite history. Compiled 2026-10-08 from the repository, its docs, the
git history and the owner's recorded decisions.

- **Live:** https://wildfire-simulator.vercel.app (frontend, Vercel) · API https://firesim-api.fly.dev/api/v1/health (Fly.io `firesim-api`, region yyz)
- **Repo:** https://github.com/Tphambolio/wildfire-simulator (local: `~/dev/wildfire/wildfire-simulator-v3`), branch `master`
- **Owner:** Travis Kennedy, P.Ag (City of Edmonton)
- **Tracked in Claude for Science project** `proj_4c36553a0c0c` (snapshots of this record are attached there; this file stays the source of truth)
- **Status at this revision:** branch `feat/curing-default-and-citations` merged with `master` `cbbffe2` (PRs #42 and #43 merged 2026-10-10). Mechanics decisions M1-M6 recorded (§3, [R12], [R13]). M1 implemented: grass curing defaults to 95 % from day of year 60 to 149 (snow-melt to green-up) and has no default outside it (API 422 when grass can burn; UI field required). Validated on CFSDS: held-out F1 unchanged (0.2126 → 0.2120) [R14]. M3 rewords the area-bias open item. Citation fixes (flame length, FTR-33, Fox-Hughes, Alexander 2010, Tran 1992, Beverly 2010, ICS forms, ellipse). Tests 2026-10-10 (§5). Previous revision: the Jasper 2024 structure check ([R10], §4.4).

---

## 1. Purpose and audience

FireSim simulates wildfire growth and fire behaviour with the Canadian Forest Fire Behavior
Prediction (FBP) System and reports where and when the modelled fire reaches neighbourhoods,
critical assets, roads and buildings.

- **Primary user:** the EOC **Planning section** (Situation Unit, Plans Chief), plus emergency
  management and fire officers trained in the FBP System. The main screen is built around
  **resource deployment**: head / flank / back, head fire intensity class and what it means for
  suppression, clock-time arrivals.
- **Intended use now:** preparedness, training, tabletop exercises and what-if planning.
- **Later goal:** operational fire-growth prediction during incidents with Prometheus/WISE-level
  accuracy in real time, once validation on observed fires supports it. Not there yet (§4.4).
- **What it is not:** an evacuation decision tool (it never suggests Order / Alert / Watch; the
  Planning section sets them), a structure-loss or ignition-probability model, a source of
  firefighter, aircraft or RPAS safety distances, or a replacement for the lead agency's
  prediction.
- **Disclaimers the product must carry:** "planning tool" limits badge; outputs shared beyond
  Planning state issuer, run time, model version (`/api/v1/version` git SHA) and inputs; a single
  run is not the expected fire (`docs/model-card.md`, "Responsible use").

## 2. Architecture

Frontend → HTTP/WebSocket → API → engine; frames are streamed back over WebSocket
(`docs/architecture.md`). The engine is pure Python with no web dependencies.

| File / module | Role |
|---|---|
| `engine/src/firesim/fbp/` (`calculator.py`, `constants.py`, `crown_fire.py`) | FBP System, 18 fuel types (ST-X-3 + GLC-X-10), verified against `cffdrs`; vectorised path `fbp_ellipse_arrays` |
| `engine/src/firesim/fwi/` (`calculator.py`, `classes.py`) | FWI System (Van Wagner & Pickett 1985), hourly FFMC; CWFIS FWI map classes |
| `engine/src/firesim/spread/cellular.py` | Level-set grid model (ELMFIRE-style Huygens velocity from each cell's FBP ellipse); per-cell arrival, speed, head/flank/back; active edges; flame panels |
| `engine/src/firesim/spread/huygens.py`, `ellipse.py`, `slope.py` | Huygens wavelets on uniform fuel; shared FBP layer `fbp_for_conditions`; ellipse geometry; slope factor |
| `engine/src/firesim/spread/diurnal.py` | Opt-in burning period and hourly-FFMC spin-up from 17:00 local |
| `engine/src/firesim/spread/ensemble.py`, `montecarlo.py` | Ensemble P10/P50/P90 arrival + burn probability; simple Monte Carlo burn probability |
| `engine/src/firesim/spread/albini.py`, `spotting.py` | Albini/Chase/Morris maximum spotting distance; heuristic emission/landing |
| `engine/src/firesim/spread/simulator.py` | `Simulator` orchestrator; picks grid model whenever a fuel grid exists |
| `engine/src/firesim/exposure.py` | Building exposure: distance bands, Cohen (2004) radiant flux, flux-time index |
| `engine/src/firesim/structures/` | Structure-to-structure spread (opt-in, illustrative): `units.py` builds one unit per building footprint (centroid, footprint in local metres, area, square-equivalent size, neighbour graph with edge-to-edge separations within a cutoff, default 30 m); `hamada.py` Hamada rates; `spread.py` front contact (from the building's grid cells) + building-to-building Dijkstra (API `structure_spread`, default off), units built only for the area the spread can reach (`structure_spread_for_grid_run`, footprint source = `BuildingIndex` or a list; memory guard 60,000 units), involved-unit detail for the map; specification `docs/structure-spread-spec.md` |
| `engine/src/firesim/data/` | Fuel loader (code schemes incl. Edmonton canopy LiDAR, drone pipeline, CFS national), DEM loader, reprojection (`raster_grid.py`), WUI/water/building masks, synthetic demo grid |
| `engine/src/firesim/validation/` | CFSDS validation harness (`cfsds.py`, `harness.py`, `metrics.py`, `report.py`, `weather.py`) |
| `api/src/firesim_api/` | FastAPI: `routers/` (simulations, fwi, weather, health), `services/runner.py` (background runs, grid cache), `schemas/`, `ws/` |
| `frontend/src/` | React 19 + TypeScript + Vite + MapLibre GL: `MapView`, Setup sections, Situation panel (`FireMetrics`, `EvacStatusPanel`, `CriticalAssetsPanel`, `EOCSummary`), `TimeSlider`, `EOCConsole` (ICS forms, ICS Canada 209-WF); shared tables `utils/fireClasses.ts`, `utils/fwiClass.ts`, `utils/time.ts` |
| `frontend/src/components/StructureSpreadPanel.tsx`, `InfoTip.tsx`, `utils/structureSpread.ts` | House-to-house spread in the app: Situation card (counts, stacked chart with the timeline cursor, map toggle), map layer `struct-units-*` in `MapView.tsx` (involved footprints by mechanism, legend, hover/click popup), caveat in a keyboard-accessible tooltip |
| `scripts/build_edmonton_assets.py` | Builds `frontend/public/edmonton/assets.geojson` (critical assets) from open sources |
| `scripts/build_edmonton_roads.py` | Builds `frontend/public/edmonton/roads.geojson` (motorway to secondary + ramps) from Overpass |
| `scripts/structure_sensitivity.py` | Structure-spread sensitivity (cutoff, contact distance, footprint size, front-contact rule) on Edmonton footprints; aggregate outputs written outside the repo [R7], [R8] |
| `scripts/jasper_structure_validation.py` | Pre-registered end-state check of the Hamada option on the Jasper 2024 townsite losses (structure-only, ember entry imposed). Downloads the Municipality of Jasper damage layer with `OBJECTID` and `Status` only; the cache and outputs live outside the repo (`~/dev/wildfire/validation-data/jasper2024/`) and are never committed [R10] |
| `scripts/validate.py`, `scripts/validation/` | Validation runs (prepare / run / report / compare), CFSDS fetch, optional WISE fire-day runs |

**External data the code calls at run time:** Open-Meteo forecast API (`frontend/src/services/api.ts`,
`api/.../routers/weather.py`); CWFIS GeoServer WFS (`routers/weather.py`, current station
weather/FWI); OSM raster tiles and OpenTopoMap tiles, optional Mapbox satellite (`MapView.tsx`);
Nominatim geocoding and Overpass (`WeatherPanel.tsx`, `services/overpass.ts`). Build/validation
time only: City of Edmonton Open Data (Socrata, incl. Parcel Addresses `ut27-nrpn`), Government
of Alberta continuing care list (open.alberta.ca), StatCan ODHF, Overpass
(`build_edmonton_assets.py`, `build_edmonton_roads.py`); Open-Meteo ERA5 archive (`validation/weather.py`); CFSDS (OSF),
CFS national FBP grids, MRDEM (`scripts/validation/`).

**Build / deploy:** push to `master` → GitHub Actions (`engine-tests (3.11)`, `engine-tests (3.12)`,
`frontend` required; `frontend-e2e` not required) → `deploy` job runs
`flyctl deploy --remote-only --build-arg GIT_SHA=…` (API image bundles `data/` at `/app/data`);
Vercel rebuilds the frontend from `master` (`docs/deployment.md`).

## 3. Decisions log

Owner decisions are dated from the owner's recorded notes and the commits that implemented
them. Future sessions must respect them; ask the owner before reversing any.

| Date | Decision | Why / evidence |
|---|---|---|
| 2026-10-05 | Label FireSim a planning and training tool, not an operational forecast | Spread not then validated against observed fires (`876cf94`, PR #1) |
| 2026-10-05 | FBP layer must match `cffdrs` to float precision; stochastic CA replaced by a deterministic level set | NRES 799 audit found many departures from ST-X-3 (`289a1d0`, `5061025`; `docs/verification.md` §4) |
| 2026-10-06 | WUI zone multipliers off by default unless sourced | 425 park buffers (spread ×0.7, intensity ×1.2, embers ×3.0) have no source or generating script (`aae819b`, PR #7) |
| 2026-10-06 | Head fire intensity classes 1-6 from Cole & Alexander (1995) with CWFIS HFI map limits; one shared table | Replaced three conflicting, partly unsourced schemes incl. an I-V scheme with US resource typing (`10ce32b`, PR #12) |
| 2026-10-06 | FWI classes = CWFIS national FWI map intervals (0-5 / 6-15 / 16-22 / 23-29 / 30+), one table for engine, API and UI; not an official danger rating | `43f0800` (PR #9); `engine/src/firesim/fwi/classes.py` |
| 2026-10-06 | RPAS stand-off distance and 2 km crown-fire personnel rule removed; generic "fire authority / SFOC / CARs" reminder only, no distances | Unsourced, could be read as permission (`5f3be56`, `5771c73`, PR #14; at the owner's direction) |
| 2026-10-06 | Never put SFOC numbers or SFOC condition IDs in the public repo | Owner rule (`CLAUDE.md`, City of Edmonton notes) |
| 2026-10-06 | EOC redesign: primary user is the EOC Planning section; main screen built around resource deployment; desktop/laptop only; dark theme (light only for print/export); EOC console simplified and centred on the ICS Canada 209 | Owner brief (redesign note 2026-10-06); dark tokens `f0a3c28` (PR #15), map-first layout `d8affaa` (PR #16). 209 rebuilt on ICS Canada Form 209-WF `d34b349` (2026-10-08, below) |
| 2026-10-06 | FireSim must never suggest evacuation tiers; Planning sets Order / Alert / Watch, drawn as blue outlines | Owner decision; implemented `c815fcf` (PR #20, merged 2026-10-07) |
| 2026-10-06 | Times in America/Edmonton; Alberta permanent UTC-6 from 2026-06-18 (IANA tzdata 2026c); old tz data shows MST after 2026-11-01 | `89a0a4b`; `docs/model-card.md` |
| 2026-10-07 | Positioning: preparedness/training tool now; Prometheus-level real-time accuracy is the later goal. Build order: validation harness → ensemble-first product → fixed-horizon 209 snapshots → time-available vs time-needed → trigger buffers / ember reach → exercise/replay | Owner, after the internal EOC best-practice review [R1]. Review's "avoid" list stands: no evac tier suggestions, no headline single arrival time, no per-building loss probability, no barrier credit for rivers/roads |
| 2026-10-07 | OSM water mask off by default; rebuild water from authoritative hydrography | OSM layer (2,275 polygons, 429 invalid) masked 17-21 % of C-2/D-2/M-2 cells; LiDAR grid already maps water as non-fuel (`d5f3bc1`, PR #23; internal fuel-grid report [R2]) |
| 2026-10-07 | Measure every prediction improvement on the CFSDS validation harness (Bennett et al. 2026 protocol), held-out fires for settings | `498fec7` (PR #22), `37311ef`/`6934822` (PR #27) |
| 2026-10-08 | UI defaults = held-out validated set-up (burning period 10-20 h, FFMC spin-up with hourly forecast, active edges when marked); API defaults stay off | `c35e308`, `c53fafb` (PR #28); `docs/model-card.md` |
| 2026-10-08 | Critical assets and roads are automatic from open sources (no user JSON); report arrival times (single run + ensemble P10) per asset instead of a 50 %-burn-probability flag | Owner decision 2026-10-08; `0751583` (PR #29) |
| 2026-10-08 | ~~The EOC location may be published (manual point in `assets.geojson`)~~ **Superseded** the same day (below: EOC point left out) | Owner, 2026-10-08 (roadmap note); `docs/data-sources.md` |
| 2026-10-08 | Critical-asset data gaps closed: care facilities from the current Government of Alberta continuing care list (geocoded with City address points) + ODHF + OSM, merged by name within 150 m with every source kept per feature; ODHF-only sites flagged "possibly closed", OSM-only "verify" (11 → 127 sites); EOC kept but marked "Unverified manual point" everywhere (no public source gives its address; superseded by the next row); roads rebuilt from OSM incl. `secondary` and ramps by a kept script; generic same-name de-duplication within 300 m (30 merged); burn-probability area table labelled model output | `2357a02` (PR #31, merged `03997fd`); `docs/data-sources.md` |
| 2026-10-08 | The EOC point is left out of the critical-assets layer: no public source gives the EOC's location and the old manual point was unverifiable (supersedes "The EOC location may be published") | Owner decision 2026-10-08; PR #33 (`8da78f5`, merged `dd523f6`); `assets.geojson` 623 features |
| 2026-10-08 | Situation report = ICS Canada Form 209-WF (May 2021), not NIMS ICS-209. Model-filled blocks tagged MODEL OUTPUT (7, 27, 29, 30B, 36, 38, 42); observed blocks 9 (status) and 28 (observed behaviour) user-entered only; projections at 12/24/48/72 h in clock time from the ensemble (P50, P10) where available; run ID + model version stamped; unsourced HFI→complexity mapping removed; other ICS forms cite their ICS Canada counterparts (layouts are adaptations) | Owner choice (ICS Canada); form checked against icscanada.ca PDF; `d34b349`; `docs/ics-canada-209.md` |
| 2026-10-08 | Engine runs are repeatable: spotting draws from a private `random.Random` seeded by `SimulationConfig.seed` (API `seed`) or a SHA-256 of the other inputs; no global random state | `f681189`; `docs/verification.md` §5 |
| 2026-10-08 | Real-raster integration tests start from known O-1a fuel cells of the reprojected grid and must run, not skip | `af052e4` |
| 2026-10-09 | Grid model: point ignitions must burn the same area whatever the wind's angle to the grid. Fixed numerically, FBP physics unchanged: the starting ellipse is connected through cell corners (not across diagonal non-fuel, not through zero-ROS fuel), the level set uses a rotated upwind stencil (grid axis + grid diagonal bracketing the Huygens velocity) instead of axis-by-axis upwinding, and phi starts as the signed distance to the starting ellipse. Rotational invariance is now a test (O-1a within 12 %, C-2 within 5 % over six wind directions; head within 10° of downwind) | Grass point ignition 43 cells at 180° vs 3 at 225°; ablation in `docs/verification.md` §2. `4bcfb1d` (PR #35). Validation effect: §4.4 |
| 2026-10-09 | Ensemble perturbation sizes = Alberta input-error climatology x one inflation factor (1.5) chosen on the calibration fires (wind dir 24°, ws log 0.405, FFMC 7.2, DMC/DC log 0.6, ROS log 0.825, FMC 15; curing 13.5 not inflated). "Worst-credible" dropped for P10 everywhere (API, docs, UI): P10 = reached by 1 in 10 members, the early end of the modelled range | Held-out ensemble calibration: coverage 20 → 49 %, CRPS skill vs single run +0.11 → +0.24, P10 held ≥ 90 % of observed growth on only 29 % of days (`docs/validation.md` "Ensemble calibration"; PR feat/ensemble-calibration) |
| 2026-10-09 | Rebuild the Berkeley/UMD WUI structure-spread model (Purnomo, Qin, Trouvé, Gollner et al.; the ELMFIRE WUI extensions) **from the published literature only**, as an opt-in, illustrative layer: specification first, then a per-building unit layer, then the Hamada option; WU-E, embers and validation later | Owner decision 2026-10-09 after the structure-ignition paper check [R4]. Licence: ELMFIRE is AGPL v3 + Commons Clause, so its source is never opened, read, quoted or translated; every equation and constant is cited to a paper, section, equation and page (`docs/structure-spread-spec.md`) |
| 2026-10-09 | Buildings are single units (graph nodes from the Microsoft footprints) coupled to the FBP grid front, not cells of the fuel grid | Qin et al. (2026) FSJ 104686 pp.7-9 [37]: structure spread converges only with cells ≤ half the structure size and separation; Edmonton side yards would need 1-3 m cells (`docs/structure-spread-spec.md` §2) |
| 2026-10-09 | Structure-spread defaults where the papers conflict (spec §7): design fire 150 kW/m² (PROCI) with 400 kW/m² as a scenario (~~owner to confirm~~ confirmed, D1 below); radiant fraction 0.3; PROCI SI flame-reach ellipse behind a verification gate; ember delay τ = 42 s; Hamada c₃ on V²; IJWF a = downwind; Hamada T in minutes; neighbour cutoff 30 m (heuristic, ~~owner to confirm~~ confirmed, D2 below) | `docs/structure-spread-spec.md` §4.4, §7 |
| 2026-10-09 | **D1** House design fire: 150 kW/m² default (5 min growth, 1 min full, 60 min decay; PROCI24 after NIST TN 1600), 400 kW/m² as a named "heavier fuel load" scenario; spec C1 stands. No current output changes (used only by the unbuilt WU-E stage) | Owner delegated the six structure-spread questions on 2026-10-09 ("trust your judgement… documented with reasoning"); decisions report [R6]. 150 is the only value used in a run compared with building-loss data (PROCI24, FSJ104651); a training tool should not default to the more severe value without evidence |
| 2026-10-09 | **D2** Keep `neighbour_cutoff_m` 30 m and `wildland_contact_m` 10 m; measure the sensitivity (cutoff 20 / 30 / 45 m, contact 5 / 10 / 20 m) and change a default only if a result is unstable or unphysical | [R6]. Evidence anchors: IJWF24 Fig. 7a and NRC (2021) p.27 for 30 m; Cohen (2000) flame-contact band for 10 m; no Canadian loss data to choose on. Sensitivity [R7]: cutoff −48 % to +220 %, contact −71 % to +557 % (involved buildings at 6 h); defaults kept; contact found grid-dependent (open item, §6) |
| 2026-10-09 | **D3** (superseded 2026-10-10, below) Structure-spread output stays aggregate (per-frame counts; later by neighbourhood or distance band). Per-building involvement times are not returned in UI, API or exports; revisit only after a Canadian validation | [R6]. 2026-10-07 avoid-list [R1] rules out per-building loss outputs; building-level precision in FSJ104651 was 9-77 %; a per-house map would read as a loss forecast |
| 2026-10-09 | **D4** Keep all 346,238 Microsoft footprints (garages and sheds) by default; test a < 40 m² size filter [H] instead of a City parcel cross-check (parcel join deferred) | [R6]. Outbuildings burn; removing them widens gaps and slows spread (less cautious). Result [R7]: the filter changes involved buildings by −8 % to 0 %, and the city-wide median nearest separation stays 2.6 m without footprints < 40 m² (short gaps are mostly not small sheds) |
| 2026-10-09 | **D5** Jasper 2024 building-level data: search the public record first (Parks Canada, Municipality of Jasper, provincial/federal after-action reviews, published damage inspections); only if nothing usable is public does the owner ask through the EM network. Validation (spec §8) stays unexecuted until then | [R6]. Jasper is the only recent, well-documented Canadian WUI loss event close to FireSim's setting; public data are reproducible and citable |
| 2026-10-09 | **D6** Hamada rate: keep the published equations (0.197 m/s at a₀ = d = 10 m, f_b = 1, 17.8 m/s) against Qin (2025)'s 0.34 m/s; keep the strict xfail and the open item; **no contact with the authors** (owner instruction) | [R6]; spec §4.5, C11. Wind height, units and the Hazus blend above 10 m/s were checked and do not explain 0.34; matching it would add an unsourced factor of ~1.7 |
| 2026-10-09 | **D2 follow-up: front contact measured from the building's own grid cells.** A unit is reached when a burned cell lies within `wildland_contact_m` (10 m, kept) of a grid cell its footprint touches (the all-touched mask cells), not of the footprint itself; below one cell this is the 8-neighbourhood of the building cells, so the outcome no longer depends on where a footprint sits inside its cell [H] | Owner decision under delegation ([R6] addendum) after [R7] showed the first rule was a grid artefact (contact 5 / 20 m: −71 % to +557 %). Smallest fix; formulated as a cell-to-cell distance so the parameter keeps a meaning on finer grids; no contact across non-fuel one cell wide or more that the footprint does not touch. `11c1230`; spec §3. Effect [R8]: contact 5 / 20 m now 0 %; involved buildings at 6 h +38 % to +751 % vs the first rule (contact at cell resolution, median 22-42 m from the footprint) |
| 2026-10-10 | **Jasper 2024 structure check, pre-registered.** All choices were fixed in `scripts/jasper_structure_validation.py` (`PREREG`, commit `ae33a21`) before any damage status was read:<br>• **Run:** structure-only (no Jasper fuel grid). Units are the 1,119 MOJ townsite footprints; Destroyed = positive (Destroyed + Visible Damage as a sensitivity).<br>• **Ember entry:** 15 seed buildings (the 5 nearest each of the three after-action-review first-impact areas) ignited at 18:00 on 24 July 2024.<br>• **Hamada:** engine defaults, 30 m cutoff and f_b = 1; 10 m wind 15 km/h from 225° (NOR-X-433 pp.33-36); window 18:00-24:00.<br>• **Sensitivity:** cutoff 20 / 45 m; wind × 0.75 / × 1.25; wind from 202.5°; window 4 h / 12 h; four alternative seed sets.<br>• **Baselines:** count-matched distance band; FPI < 5 m one-step and percolation rules; random at the observed rate; seeds only.<br>• **Decision rule:** structure spread is worth showing only if its κ beats the distance band and seeds only (spec §8 item 4) | Spec §8 item 5: no tuning on the test fire. The wildland front cannot be run for Jasper, and FPInnovations reports ember entry followed by structure-to-structure spread, so a structure-only run with imposed ember ignitions tests exactly the Hamada stage. Everything seen beforehand is disclosed in [R10] §1 |
| 2026-10-10 | **Structure units are built only where the spread can reach**, with a memory guard: after the grid run, units for the footprints intersecting the burned-cell box grown by the contact reach and a margin, doubled until no involved unit is within the cutoff of the box edge (exact: identical counts on all six sensitivity runs); frame fixed at the grid box centre; over 60,000 footprints in that box → frames say `computed: false`, "not computed: too many buildings in run area" | Production OOM 2026-10-10 05:39Z: an Edmonton run with `structure_spread` built all 334,213 footprints in the grid box (peak 1.76 GB locally vs 1.09 GB without; 2 GB Fly machine). After: 1.09 GB, +24 MB traced in the structure step [R11]. The guard limit leaves ~8x headroom over the largest sensitivity run (7,219 units) |
| 2026-10-10 | **Owner reversed D3: per-building display for exercises.** The final frame returns the involved units (simplified footprint, involvement time, mechanism, source unit; no address, owner or parcel data) and the app maps them by mechanism, appearing with the timeline; counts and a stacked chart stay. Map display only: not in any export, ICS 209 or report | Owner, 2026-10-10: aggregated blocks can look more catastrophic than the modelled result, and firefighters work a fire house by house, then block by block. D3's concern (building-level precision 9-77 % in FSJ104651 [35]; a per-house map can read as a loss forecast) is handled by labelling, not by hiding. An aggregated-bin layer was briefly specified the same day and dropped for this |
| 2026-10-10 | House-to-house spread UI kept minimal: checkbox "House-to-house spread" (off by default, only with the Edmonton grid and buildings, one-line hover explanation), "Illustrative" badge on the card and map legend, full caveat (not validated in Canada; modelled involvement, not a prediction of which buildings will burn; 30 m cutoff; ~50 m grid contact) in an info tooltip on hover or keyboard focus; colours magenta (front contact) / aqua (building to building), outside the fire, fuel, evac and isochrone families | Owner style direction 2026-10-10 (the app is cluttered with qualifiers; literature goes to a later "About & sources" tab) |
| 2026-10-10 | **M1** Grass curing default: date-aware. 95 % in the pre-green-up window (day of year 60-149, about 1 March to 29 May) [H for the dates and the value]; outside it no silent default: the API returns 422 when O-1 grass can burn and no `grass_cure` is given, the UI shows the field as required and suggests the last value entered. Explicit values are always used (backward compatible for clients that send one) | Owner delegated the mechanics choices on 2026-10-10 ("go with your views on the mechanics"); decisions report [R12], evidence [R13] rec 1. The fixed 60 % gives curing factor 0.20 (GLC-X-10 eq 35b, p.9); GLC-X-26 uses 90 % when curing is not observed (p.24); grass before green-up is cured (Pickell et al. 2017; Beverly & Schroeder 2025 p.14; NRC 2021 p.25). Implemented and validated on CFSDS in PR `feat/curing-default-and-citations` [R14] (§4.4) |
| 2026-10-10 | **M2** LiDAR crown base height stays out of FBP runs; a separate Perrakis et al. (2023) crown-fire likelihood layer later | [R12], [R13] rec 2: FBP CBH values were tuned, not measured (ST-X-3 p.35); GLC-X-10 p.30 advises against changing CBH outside C-6. Planned (§6) |
| 2026-10-10 | **M3** Area over-prediction is fixed through run set-up (active edges, burning period, spin-up); any spread-rate correction only by fire type and fuel, never a blanket ROS cut | [R12], [R13] rec 3: FBP under-predicts spread in most published comparisons (Cruz & Alexander 2013 p.16; Stocks et al. 2004 p.1557; Beverly & Schroeder 2025 p.8), FireSim's largest runs too; the over-prediction is set-up (Bennett et al. 2026 p.9). Open item reworded (§6) |
| 2026-10-10 | **M4** Embers from torching/crowning cells (Van Wagner criteria) instead of HFI ≥ 4,000 kW/m; add Albini, Alexander & Cruz (2012) active crown-fire spotting; LiDAR tree heights as Albini inputs | [R12], [R13] rec 4: Albini (1979, pp.3-5) is a torching-tree model guided by Van Wagner's criteria. Planned (§6) |
| 2026-10-10 | **M5** Native 20 m fuel grid in a window around buildings, 50 m elsewhere; where several fuel maps are used, average predictions, never fuels; memory budget checked against the 2 GB server first | [R12], [R13] rec 5: Forbes & Beverly (2024); Bennett, Da Silva & Boisvert (2024, p.11). Planned (§6) |
| 2026-10-10 | **M6** City tree inventory as an ember-source layer (364 managed conifer clusters outside the uPLVI boundary, treated as torching trees, not stand crown fire) only if its licence allows | [R12], [R13] rec 9. Planned, licence check first (§6) |
| standing | Ask the owner before each merge (auto-merge is enabled but still ask) | `CLAUDE.md` (CI/CD); owner notes |
| standing | Run the full verification stack, including browser E2E, before calling work done | Owner feedback note (2026-06-16) |
| standing | No Claude/Anthropic references in code committed to City of Edmonton systems (`git.edmonton.ca`); this GitHub repo is separate | `CLAUDE.md` |

## 4. Methods and references

### 4.1 Method → source

| Component | Implementation | Source |
|---|---|---|
| FBP fuel types, ROS, BUI effect, SFC, CFB, TFC, HFI, LB, back/flank ROS, net effective wind + slope, FMC from date, point-ignition acceleration | `fbp/calculator.py`, `fbp/constants.py`, `fbp/crown_fire.py` | ST-X-3 [1] eqs 1-8, 26-58, 70-73, 81; GLC-X-10 [2] (grass curing eqs 35a/b, M-4 `c`, revised forms); verified against `cffdrs` [6] |
| D-2 no spread below BUI 80 | `fbp/calculator.py` | Alexander (2010) [7] **as cited by cffdrs**; full reference from the reference lists of de Groot et al. (2022) and CFS NOR-X-433 (2026-10-10); the paper itself not read |
| Crown fire initiation, CFB | `fbp/crown_fire.py` | Van Wagner (1977) crown fire [3]; ST-X-3 eqs 56-58 |
| FWI System (FFMC, DMC, DC, ISI, BUI, FWI) | `fwi/calculator.py` | Van Wagner & Pickett (1985) [5]; Van Wagner (1987) [5b] |
| Hourly FFMC | `fwi/` hourly path | Van Wagner (1977) PS-X-69 [4]; matches cffdrs `hffmc` |
| Diurnal burning: FFMC spin-up from 17:00 local the previous day; burning period (spread only between set hours) | `spread/diurnal.py` | Lawson et al. (1996) [17] (daily FFMC ≈ 16:00 LST), Beck et al. (2002) [16], burning conditions as in Prometheus, Tymstra et al. (2010) [10]. 10-20 h chosen on calibration fires (§4.4) |
| Fire growth, spatial fuel | `spread/cellular.py`: level set advected with the Huygens velocity of each cell's FBP ellipse (second-order ENO upwind) | Richards (1990) [8]; Lautenberger (2013) [9] (ELMFIRE approach) |
| Fire growth, uniform fuel | `spread/huygens.py`: Huygens wavelets, convex hull, 5 min step, 1 m start | Richards (1990) [8] |
| Active / inactive perimeter edges | `spread/cellular.py` (`active_edges`) | FireSim method; harness proxy for an RPAS thermal flight (`docs/validation.md`) |
| Flame length | Byram (1959) for CFB < 0.1; Thomas (1963) for CFB ≥ 0.1 (approximate for crown fires) | Byram's form: Alexander & Cruz (2012) [15] Table 1, p.98. Thomas for crown fires was suggested by Rothermel (1991) [45], as reported by Alexander & Cruz (2012) p.99, who found no method "seem[s] to work consistently well" on experimental crown fires (corrected 2026-10-10; was "recommended by Alexander & Cruz"). Byram 1959 and Thomas 1963 originals not checked |
| Grass curing default (O-1a/O-1b) | `fbp/curing.py`; API `FuelModifiers.resolve_grass_cure` (422 when required); UI `utils/curing.ts` | Curing factor GLC-X-10 [2] eqs 35a/b, p.9. 95 % in day of year 60-149: GLC-X-26 [41] p.24 (90 % when not observed), Pickell et al. (2017) [42] (snow-melt to green-up window; Alberta green-up DOY 97-164), Beverly & Schroeder (2025) [43] p.14, NRC (2021) [19] p.25. Window dates and 95 % are FireSim choices [H] (M1, 2026-10-10) |
| Spotting maximum distance | `spread/albini.py`: torching-tree and wind-driven surface-fire models | Albini (1979, 1981, 1983) [11-13], Chase (1981, 1984) [14a,b], Morris (1987) [14c] |
| Spotting emission, probability, landing | `spread/spotting.py` | **Heuristic** (no source gives these; illustrative only) |
| Ensemble | `spread/ensemble.py`: perturbed wind direction (systematic per member), wind speed, FFMC, DMC/DC, curing, FMC, ROS multiplier → P10/P50/P90 arrival, burn probability | Wind direction as dominant error: Fox-Hughes et al. (2024) [23], Bennett et al. (2026) [20]; ROS error band: Cruz & Alexander (2013) [22]. Sigmas: Alberta input-error climatology (GEM day-1 and ERA5 vs 14 ECCC stations, 2024-25; `scripts/validation/input_errors.py`) x 1.5 chosen on calibration fires; curing Anderson et al. (2011) [28]; FMC 10 % x 1.5 is a **judgement** (no published error statistic) |
| Burn probability (simple) | `spread/montecarlo.py`: ignition ±100 m, wind ±10 %, RH ±5 % | Method only; not a validated or Burn-P3-style product |
| Building exposure | `exposure.py`: distance bands (≤10, 10-30, 30-100, 100-500 m); solid-flame radiant flux (1200 K, 117.6 kW/m² and 200 kW/m² scenarios); flux-time criterion FTP = ∫(q − 13.1)^1.828 dt ≥ 11,501 | Cohen (2004) [18] (SIAM, eqs 2-4 after Tran et al. 1992, not checked); Cohen (2000) [18b]; NRC (2021) [19] p.27 bands and flux ranges. Exposure, **not** ignition probability |
| HFI classes 1-6 | `frontend/src/utils/fireClasses.ts` | Cole & Alexander (1995) [21] meanings (C-2, level ground; class 6 = their "explosive" upper class 5); limits from CWFIS GeoServer `public:hfi` legend (read 2026-10-06) |
| FWI classes | `fwi/classes.py`, `frontend/src/utils/fwiClass.ts` | CWFIS national FWI map intervals (GeoServer `public:fwi` legend) |
| Structure-to-structure spread (**partly built**: building units PR #38, Hamada option PR #39; WU-E and embers specified only; opt-in, illustrative) | `docs/structure-spread-spec.md`: per-building units; Hamada option; WU-E flame contact + radiation + heat-dose FTP; ember generation, transport and ignition | Hamada via Himoto & Tanaka (2008) [36] eqs 42-43, Purnomo et al. (2026) FSJ 104651 [35] eq 2 + SI, Qin (2025) [38] eqs 2.66-2.80; WU-E: Purnomo et al. (2024) PROCI [33] eqs 1-8 + SI S1-S21, Purnomo et al. (2024) IJWF [34]; embers: Qin et al. (2026) FSJ 104686 [37], Qin (2025) [38]. FireSim adaptations marked [H], tuned values [T] in the spec |
| Building units for structure spread | `structures/units.py`: one node per Microsoft footprint clipped to the run area (`BuildingIndex.building_geoms_in_bbox`); envelope candidates + exact shapely distances; not rasterised onto the fuel grid | Single-unit treatment: Qin et al. (2026) FSJ 104686 §3.4, §4, §6, pp.4, 7-9 (grid convergence needs Δx ≤ half the building size and spacing); square-equivalent size after Hamada's square plans (Himoto & Tanaka 2008, p.25) — applying it to irregular footprints is a FireSim heuristic |
| Structure-to-structure spread, Hamada option (opt-in; **illustrative — not validated in Canada**) | `structures/hamada.py`: T_i, C(V), U_i = (a0 + d)/T_i, Hazus low-wind blend (long-time limit, FireSim derivation), direction on the Hamada ellipse (FBP-ellipse construction); `structures/spread.py`: unit involved when a burned cell is within 10 m of one of its building grid cells, i.e. the front reaches a cell next to a cell the footprint touches (heuristic, from 2026-10-09; previously 10 m from the footprint), then pair crossing time (a0 + d)/rate(θ) under the run's weather periods, Dijkstra; no burnout; per-frame counts, and the involved units (footprint, time, mechanism) on the final frame for the map (from 2026-10-10); units built only for the reachable area (exact, spec §2) | Purnomo et al. (2026) FSJ 104651 eq 2 (p.3) + SI; Qin (2025) eqs 2.66-2.80, Table 2.2 (pp.38-40; eq 2.70 typo c4 → c3); Himoto & Tanaka (2008) eqs 42-43 (m/min). Heuristics: 10 m contact from the building cells, 30 m cutoff, pair a0 and d, 10 m wind, long-time Hazus limit, direct ellipse (spec §4) |
| Validation metrics | `validation/metrics.py`: precision, recall, F1 (Dice), IoU, normalised area difference, Hausdorff, forward spread distance and bearing; ±35 % band | Bennett et al. (2026) [20] protocol; Fox-Hughes et al. (2024) [23]; Cruz & Alexander (2013) [22] |
| Observed fire data | CFSDS day-of-burning rasters + daily summaries | Barber et al. (2024) [24] |
| Ensemble scores | `validation/ensemble_scores.py`: Brier score and reliability of burn probability, P10/P50/P90 footprint scores, rank histogram and P10-P90 coverage of burned area, CRPS (ensemble estimator), spread-skill | Brier (1950) [29]; Gneiting & Raftery (2007) [30]; Hersbach (2000) [31]; Fortin et al. (2014) [32] |

**Deliberate deviations and heuristics**
- FFMC moisture coefficient 147.2 (ST-X-3 eq 46); cffdrs uses 147.27723 (ISI changes ≤ 1e-3). The cffdrs fixture is generated with 147.2.
- Spotting: emission, probability, landing below the maximum, and per-fuel stand sizes (`STAND_DEFAULTS`) are assumptions; terrain correction not implemented; active crown fires use the torching model with several trees (underestimate). The active crown fire model of Albini, Alexander & Cruz (2012) is **not** implemented.
- Ensemble perturbations are symmetric (median 1): the area bias is not corrected, so burn probability is over-confident; curing and FMC sizes are not validated. Per M3 (2026-10-10) the area bias is to be fixed through set-up, and any ROS factor only by fire type and fuel.
- Burning period is a fixed clock window (no overnight runs, ROS × 0 outside it).
- DMC and DC are held fixed within a run.
- Building exposure: blackbody 1200 K, emissivity 1 even for thin surface flames; no embers, no convective heating, no burning buildings or yard fuels.
- Validation harness: CFSDS DOB days are interpolated from satellite passes (hours of timing uncertainty); one ERA5 weather point per fire; 2014b national fuel grid without burn-scar succession; D-1/M-1 → D-2/M-2 between day-of-year 150 and 258 (assumption for Alberta boreal); M-1/M-2 at 50 % conifer; O-1 curing 90 % outside the green season / 60 % inside (the M1 check [R14] also ran 95 % in the spring window and 60 % everywhere).
- WUI zone modifiers in `data/wui_zones.geojson.gz` are unsourced and off by default.

### 4.2 References

Only what the code or docs cite. "Unverified" = cited second-hand and not checked against the original.

1. Forestry Canada Fire Danger Group (1992). *Development and Structure of the Canadian Forest Fire Behavior Prediction System.* Information Report ST-X-3.
2. Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). *Updates and revisions to the 1992 Canadian Forest Fire Behavior Prediction System.* Information Report GLC-X-10.
3. Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire. *Canadian Journal of Forest Research* 7: 23-34.
4. Van Wagner, C.E. (1977). *A method of computing fine fuel moisture content throughout the diurnal cycle.* Information Report PS-X-69.
5. Van Wagner, C.E., Pickett, T.L. (1985). *Equations and FORTRAN program for the Canadian Forest Fire Weather Index System.* Forestry Technical Report 33.
   5b. Van Wagner, C.E. (1987). *Development and structure of the Canadian Forest Fire Weather Index System.* Forestry Technical Report 35.
6. Wang, X. et al. (2017). cffdrs: an R package for the Canadian Forest Fire Danger Rating System. *Ecological Processes* 6: 5. Reference implementation: CFS `cffdrs` Python port (`cffdrs_py`).
7. Alexander, M.E. (2010). Surface fire spread potential in trembling aspen during summer in the Boreal Forest Region of Canada. *The Forestry Chronicle* 86(2): 200-212. doi:10.5558/tfc86200-2. (Cited by cffdrs for the D-2 BUI < 80 rule; reference checked against the reference lists of de Groot et al. 2022 and CFS NOR-X-433; the paper itself not read.)
8. Richards, G.D. (1990). An elliptical growth model of forest fire fronts and its numerical solution. *International Journal for Numerical Methods in Engineering* 30: 1163-1179.
9. Lautenberger, C. (2013). Wildland fire modeling with an Eulerian level set method and automated calibration. *Fire Safety Journal* 62: 289-298.
10. Tymstra, C., Bryce, R.W., Wotton, B.M., Taylor, S.W., Armitage, O.B. (2010). *Development and structure of Prometheus: the Canadian Wildland Fire Growth Simulation Model.* Information Report NOR-X-417.
11. Albini, F.A. (1979). *Spot fire distance from burning trees: a predictive model.* General Technical Report INT-56. USDA Forest Service, Intermountain Research Station.
12. Albini, F.A. (1981). *Spot fire distance from isolated sources: extensions of a predictive model.* Research Note INT-309. USDA Forest Service.
13. Albini, F.A. (1983). *Potential spotting distance from wind-driven surface fires.* Research Paper INT-309. USDA Forest Service.
14. (a) Chase, C.H. (1981). *Spot fire distance equations for pocket calculators.* Research Note INT-310. (b) Chase, C.H. (1984). *Spotting distance from wind-driven surface fires: extensions of equations for pocket calculators.* Research Note INT-346 (corrects Albini 1983 Table 4). (c) Morris, G.A. (1987). *A simple method for computing spotting distances from wind-driven surface fires.* Research Note INT-374. All USDA Forest Service, Intermountain Research Station.
15. Alexander, M.E., Cruz, M.G. (2012). Interdependencies between flame length and fireline intensity in predicting crown fire initiation and crown scorch height. *International Journal of Wildland Fire* 21: 95-113.
16. Beck, J.A., Alexander, M.E., Harvey, S.D., Beaver, A.K. (2002). Forecasting diurnal variations in fire intensity to enhance wildland firefighter safety. *International Journal of Wildland Fire* 11: 173-182.
17. Lawson, B.D., Armitage, O.B., Hoskins, W.D. (1996). *Diurnal variation in the Fine Fuel Moisture Code: tables and computer source code.* FRDA Report 245.
18. Cohen, J.D. (2004). Relating flame radiation to home ignition using modeling and experimental crown fires. *Canadian Journal of Forest Research* 34: 1616-1626.
   18b. Cohen, J.D. (2000). Preventing disaster: home ignitability in the wildland-urban interface. *Journal of Forestry* 98(3): 15-21.
   18c. Tran, H.C., Cohen, J.D., Chase, R.A. (1992). Modeling ignition of structures in wildland/urban interface fires. In *Proceedings: 1st International Fire and Materials Conference*, Arlington, Virginia, 24-25 September 1992. Inter Science Communications, London, pp. 253-262. (As cited in Cohen 2004, checked against Cohen's reference list 2026-10-10; corrects an earlier wrong title; the paper itself not read.)
   18d. Westhaver, A. (2017). Fort McMurray home ignition study, ICLR (cited in `docs/building-exposure.md` for 60-90 s flame residence; title not re-checked).
19. National Research Council Canada (2021). *National Guide for Wildland-Urban Interface Fires.*
20. Bennett, L., Jain, P., Moore, B., Boisvert, J. (2026). Assessment of fire spread predictions from the Wildfire Intelligence and Simulation Engine (W.I.S.E.) using a large set of satellite-derived wildfire perimeters. *International Journal of Wildland Fire* 35(8): WF26072. doi:10.1071/WF26072.
21. Cole, F.V., Alexander, M.E. (1995). *Head fire intensity class graph for FBP System Fuel Type C-2 (Boreal Spruce).* Alaska DNR Division of Forestry, Fairbanks, and Natural Resources Canada, Canadian Forest Service, Edmonton. Poster (based on Alexander & Cole 1995, SAF Publication 95-02, pp. 185-192). Local copy: `~/dev/wildfire/references/hfi-classes/`.
22. Cruz, M.G., Alexander, M.E. (2013). Uncertainty associated with model predictions of surface and crown fire rates of spread. *Environmental Modelling & Software* 47: 16-28.
23. Fox-Hughes, P., Bridge, C., Faggian, N., Jolly, C., Matthews, S., Ebert, E., Jacobs, H., Brown, B., Bally, J. (2024). An evaluation of wildland fire simulators used operationally in Australia. *International Journal of Wildland Fire* 33(4). doi:10.1071/WF23028. (Title checked against the reference list of Bennett et al. 2026 [20].)
24. Barber, Q.E., et al. (2024). The Canadian Fire Spread Dataset. *Scientific Data* 11: 764. doi:10.1038/s41597-024-03436-4.
25. Hersbach, H., et al. (2020). The ERA5 global reanalysis. *Quarterly Journal of the Royal Meteorological Society* 146: 1999-2049.
26. Beaudoin, A., et al. (2014). Mapping attributes of Canada's forests at moderate resolution through kNN and MODIS imagery. *Canadian Journal of Forest Research* 44: 521-532.
27. Byram, G.M. (1959) and Thomas, P.H. (1963): flame length relations, cited in code via Alexander & Cruz (2012); originals not checked (unverified). Thomas for crown fires: suggested by Rothermel (1991) [45].
28. Anderson, S.A.J., Anderson, W.R., Hollis, J.J., Botha, E.J. (2011). A simple method for field-based grassland curing assessment. *International Journal of Wildland Fire* 20: 804-814.
29. Brier, G.W. (1950). Verification of forecasts expressed in terms of probability. *Monthly Weather Review* 78: 1-3.
30. Gneiting, T., Raftery, A.E. (2007). Strictly proper scoring rules, prediction, and estimation. *Journal of the American Statistical Association* 102: 359-378.
31. Hersbach, H. (2000). Decomposition of the continuous ranked probability score for ensemble prediction systems. *Weather and Forecasting* 15: 559-570.
32. Fortin, V., Abaza, M., Anctil, F., Turcotte, R. (2014). Why should ensemble spread match the RMSE of the ensemble mean? *Journal of Hydrometeorology* 15: 1708-1713.
33. Purnomo, D.M.J., Qin, Y., Theodori, M., Zamanialaei, M., Lautenberger, C., Trouvé, A., Gollner, M.J. (2024). Reconstructing modes of destruction in wildland-urban interface fires using a semi-physical level-set model. *Proceedings of the Combustion Institute* 40: 105755. doi:10.1016/j.proci.2024.105755. (Spec key PROCI24.)
34. Purnomo, D.M.J., Qin, Y., Theodori, M., Zamanialaei, M., Lautenberger, C.W., Trouvé, A., Gollner, M.J. (2024). Integrating an urban fire model into an operational wildland fire model to simulate one dimensional wildland-urban interface fires: a parametric study. *International Journal of Wildland Fire* 33(10): WF24102. doi:10.1071/WF24102. (Read as publisher HTML; PDF and supplement not read.)
35. Purnomo, D.M.J., Zamanialaei, M., Earle, M., Theodori, M., Qin, Y., Lautenberger, C., Trouvé, A., Gollner, M.J. (2026). Sensitivity of ELMFIRE to real-world input datasets for WUI fire modeling. *Fire Safety Journal* 161: 104651. doi:10.1016/j.firesaf.2026.104651. (The supplementary material is titled "Coupling urban and wildland fire spread models to investigate sensitivity of inputs in wildland urban interface fires simulations".)
36. Himoto, K., Tanaka, T. (2008). Development and validation of a physics-based urban fire spread model. *Fire Safety Journal* 43(7): 477-494. (Read as the Kyoto University author manuscript.)
37. Qin, Y., Purnomo, D.M.J., Theodori, M., Zamanialaei, M., Lautenberger, C., Gollner, M., Trouvé, A. (2026). Simulations of firebrand-driven fire spread in landscape-scale Wildland-Urban-Interface (WUI) and urban conflagration models. *Fire Safety Journal* 162: 104686. doi:10.1016/j.firesaf.2026.104686.
38. Qin, Y. (2025). *A physics-based Eulerian framework for modeling firebrand showering in regional-scale wildland and WUI fire simulations.* PhD dissertation, University of Maryland (DRUM).
39. Hamada, M. (1951). On the rate of fire spread. (Cited via [35], [36], [38]; **not read**.) Scawthorn / FEMA Hazus low-wind correction: cited via [35] SI and [38] p.39; **not read**.
40. Maranghides, A., Johnsson, E.L. (2008). *Residential Structure Separation Fire Experiments.* NIST Technical Note 1600. (Design-fire source cited by [33]; not read here.)
41. Canadian Forest Service Fire Danger Group (2021). *An overview of the next generation of the Canadian Forest Fire Danger Rating System.* Information Report GLC-X-26. Great Lakes Forestry Centre. (p.24: grassland indices use an observed curing, "a default value (i.e., 90% cured)", or an estimate; p.18: phenology inputs; p.29: Cruz et al. 2015 curing curve.)
42. Pickell, P.D., Coops, N.C., Ferster, C.J., Bater, C.W., Blouin, K.D., Flannigan, M.D., Zhang, J. (2017). An early warning system to forecast the close of the spring burning window from satellite-observed greenness. *Scientific Reports* 7: 14190. doi:10.1038/s41598-017-14730-0. (Read 2026-10-10 as the Europe PMC full text, PMC5660258; not in the local corpus.)
43. Beverly, J.L., Schroeder, D. (2025). Alberta's 2023 wildfires: context, factors, and futures. *Canadian Journal of Forest Research* 55: 1-19. (p.14: fire-prone weather "following snow-melt but before green-up"; p.8: 2023 C-2 spread under-predicted.)
44. Beverly, J.L., Bothwell, P., Conner, J.C.R., Herd, E.P.K. (2010). Assessing the exposure of the built environment to potential ignition sources generated from vegetative fuel. *International Journal of Wildland Fire* 19(3): 299-313. doi:10.1071/WF09071. (Cited in [R3]; DOI checked against the reference lists of Forbes & Beverly 2024 and Johnston et al. 2017.)
45. Rothermel, R.C. (1991). *Predicting behavior and size of crown fires in the Northern Rocky Mountains.* Research Paper INT-438. USDA Forest Service. (Cited via Alexander & Cruz 2012, p.99; not read.)

**Internal reports** (owner's research, not peer reviewed; local, not in the repo):
- [R1] *FireSim EOC best practice review* (2026-10-07), `~/dev/wildfire/reports/FireSim EOC best practice review.md`: post-incident reviews (Slave Lake, Fort McMurray, Jasper, Lytton, Marshall, Camp, Lahaina), ranked programme, "avoid" list, name clash with Technosylva's FireSim.
- [R2] *Fuel type grids: Canada and global* (2026-10-07), `~/dev/wildfire/reports/Fuel type grids Canada global.md`: Edmonton grid provenance, loader misplacement, OSM water mask, CFS Current-Year national layer, code 13, global options.
- [R5] *Grass diagonal spread fix validation* (2026-10-09), `~/dev/wildfire/reports/Grass diagonal spread fix validation 2026-10-09.md`: CFSDS held-out comparison before/after the grid-model diagonal-spread fix (PR #35), set-up, fire-days, F1 tables, commands; raw runs in `$FIRESIM_VALIDATION_DATA/diagfix/`. (R3/R4 are reserved for the structure-ignition reports on another branch.)
- [R3] *Structure ignition in the WUI: a literature review for FireSim* (2026-10-06), `~/dev/wildfire/references/structure-ignition/structure_ignition_review.md`: repo audit of building outputs, structure-ignition literature, the Berkeley/UMD model and its code-only items.
- [R4] *Structure ignition paper check: can FireSim rebuild the Berkeley/UMD WUI model from the literature alone?* (2026-10-09, revised the same day), `~/dev/wildfire/reports/Structure ignition paper check.md`: component-by-component check, conflicting published values, recall-only validation (precision 9-77 %), grid-convergence finding, build order. Basis of `docs/structure-spread-spec.md`.
- [R6] *Structure spread: owner decisions and reasoning* (2026-10-09), `~/dev/wildfire/reports/Structure spread owner decisions 2026-10-09.md`: decisions D1-D6 made under the owner's delegation (design fire, distance limits, aggregate-only output, footprints, Jasper data, Qin 0.34 m/s), each with its reasoning.
- [R7] *Structure spread: sensitivity to the cutoff, contact distance and footprint filter* (2026-10-09), `~/dev/wildfire/reports/Structure spread sensitivity 2026-10-09.md`: decisions D2/D4 follow-up on Edmonton footprints, 3 sites × 2 weather days, Hamada option, aggregate counts only; script `scripts/structure_sensitivity.py`, outputs `~/dev/wildfire/reports/data/structure-sensitivity-2026-10-09/`.
- [R8] *Structure spread: front contact measured from the building's own cells* (2026-10-09), `~/dev/wildfire/reports/Structure front contact fix 2026-10-09.md`: the building-cell contact rule and why, rerun of the [R7] sensitivity before/after (same settings, seed 20261009), aggregate counts only; outputs `~/dev/wildfire/reports/data/structure-front-contact-2026-10-09/`.
- [R9] *Jasper 2024 building data search* (2026-10-09), `~/dev/wildfire/reports/Jasper 2024 building data search 2026-10-09.md`: public sources for building-level damage, timing and weather of the Jasper Wildfire Complex (decision D5). The MOJ "Damage Assessment Structures" layer is the observed set (townsite 370 / 16 / 733), with CFS NOR-X-433 and the municipal after-action review for timing. It records the gaps: progression GIS, the FPI per-structure table, station data for 16:00-22:00, and suppression locations. No one was contacted.
- [R10] *Jasper 2024: building-by-building check of FireSim structure spread* (2026-10-10), `~/dev/wildfire/reports/Jasper 2024 structure validation 2026-10-10.md`: the pre-registered set-up, data provenance and licence status, results against the baselines, the pre-specified sensitivity, interpretation, limits and the data to request. Script `scripts/jasper_structure_validation.py`; outputs (local only) `~/dev/wildfire/validation-data/jasper2024/results-2026-10-10/`.
- [R11] *Structure spread memory fix* (2026-10-10), `~/dev/wildfire/reports/Structure spread memory fix 2026-10-10.md`: the API out-of-memory crash with `structure_spread`, cause (units for every footprint in the grid box), before/after peak RSS, the reachable-box build and its exactness argument, count equivalence on the six sensitivity runs, per-building payload sizes.
- [R12] *FireSim mechanics decisions* (2026-10-10), `~/dev/wildfire/reports/Mechanics decisions 2026-10-10.md`: decisions M1-M6 under the owner's delegation (curing default, LiDAR CBH, area bias, ember sources, 20 m grid near buildings, tree inventory), with reasons and order of work.
- [R13] *FireSim mechanics: what the NRES 799 literature says to change* (2026-10-10), `~/dev/wildfire/reports/FireSim mechanics literature review 2026-10-10.md`: read-only review of FireSim's mechanics against the NRES 799 corpus, page-level evidence, ranked recommendations, keep / don't-do lists, corpus gaps.
- [R14] *Grass curing default: validation before and after* (2026-10-10), `~/dev/wildfire/reports/Curing default validation 2026-10-10.md` (written first to the session scratchpad because the agent session could not write to the reports folder; to be copied there): M1 rule and sources, CFSDS held-out F1 and area bias for the chosen set-up under the old and new curing rules, grass fire-days broken out; runs in `$FIRESIM_VALIDATION_DATA/curing/`.
- Reference PDFs: `~/dev/wildfire/references/spotting/` (Albini, Chase, Morris originals + `spotting_equations.md`), `structure-ignition/` (Cohen 2000/2004, NRC 2021, Westhaver 2017, others + `structure_ignition_review.md`), `hfi-classes/` (Cole & Alexander 1995 poster, CWFIS legend notes).

### 4.3 Data sources

| Source | Use | Licence |
|---|---|---|
| Edmonton FBP fuel grid `data/Edmonton_FBP_FuelLayer_20251105_10m.tif` | Spatial fuel (codes 2 C-2, 12 D-2, 14 M-2, 31 O-1a, 32 O-1b, 99 non-fuel; `canopy_lidar` scheme) | City of Edmonton internal product. Lineage [R2]: **20 m** cells, EPSG:3776, built 2026-03-20 by `build_fuel_raster.py` (Urban-Forestry LiDAR repo, commit `223dd29`) from the City's 2025 leaf-on vegetation LiDAR, a conifer/deciduous crown classifier and uPLVI / Naturalized Areas polygons; committed to FireSim 2026-04-05 (`c1f3804`). Filename (date, "10m") is wrong. Never accuracy-assessed |
| `data/edmonton_dem.tif` | Slope and aspect | 30 m, EPSG:26912, TIFF date 2011; recorded as Open Government Canada, exact product not recorded (likely NRCan CDEM; **unverified**) |
| `data/edmonton_buildings.geojson.gz` | Building mask, exposure, building index | Microsoft Canadian Building Footprints, ODbL (file metadata: 346,238 footprints, generated 2025-11-16; corrected 2026-10-08, `45e68d2`; was listed as City Open Data). Its `type`/`height`/`material`/`roof_type` attributes have no documented source and are not used |
| `data/edmonton_neighbourhoods.geojson` | Neighbourhoods, building index | City of Edmonton Open Data `65fr-66s6`, OGL – City of Edmonton |
| `data/edmonton_water_bodies.geojson.gz` | Optional non-fuel mask (**off by default**) | OpenStreetMap (ODbL; features carry `osm_id`); `CLAUDE.md` corrected 2026-10-08 (`45e68d2`) |
| `data/wui_zones.geojson.gz` | WUI modifiers (**off by default**) | **No documented source** |
| `frontend/public/edmonton/assets.geojson` (623 features: rebuilt 2026-10-08 in PR #31, EOC point dropped in PR #33) | Critical assets: fire/police stations, recreation and seniors centres, schools, LRT (City Open Data `b4y7-zhnz`, `e7aq-scxv`, `nz3t-vyg3`, `zmac-3mxq`, `gfxq-u8uu`, `996c-239n`, `fhxi-cnhe`); 127 hospitals/care facilities from the Government of Alberta continuing care list (extract as of June 2026, geocoded with City Parcel Addresses `ut27-nrpn`, 102/104), StatCan ODHF v1.1 (2020-04-20; 26/28 coordinate-less records geocoded) and OSM (flagged "verify"); water/wastewater, power plants, substations ≥ 69 kV (OSM via Overpass). No manual points: the EOC is not in the layer (PR #33) | OGL – City of Edmonton; OGL – Alberta; OGL – Canada; ODbL 1.0 (derived database, share-alike) |
| `frontend/public/edmonton/roads.geojson` | Major roads, motorway to secondary + ramps, 816 features, rebuilt 2026-10-08 by `scripts/build_edmonton_roads.py` (Overpass, joined per road, Douglas-Peucker 5 m) | ODbL 1.0, © OpenStreetMap contributors |
| `frontend/public/edmonton/neighbourhoods.geojson` | Neighbourhood arrival + Planning-set status (`65fr-66s6`) | OGL – City of Edmonton |
| Open-Meteo forecast API | Hourly forecast weather aligned to the scenario start | Open-Meteo terms (CC BY 4.0 data) |
| CWFIS GeoServer WFS (`cwfis.cfs.nrcan.gc.ca/geoserver/public/ows`) | Current station weather / FWI (`/api/v1/weather/current`); HFI and FWI legend limits | NRCan / CWFIS (OGL – Canada, assumed; not checked) |
| OSM tiles, OpenTopoMap, optional Mapbox satellite; Nominatim; Overpass | Basemaps, geocoding, community asset lookup | ODbL / provider terms |
| CFSDS v1.1 (OSF f48ry) | Observed daily progression + daily FWI for validation | CC BY 4.0 |
| ERA5 via Open-Meteo archive API | Hourly validation weather | CC BY 4.0 (Open-Meteo); Copernicus licence (ERA5) |
| CFS National FBP fuel types 2014b (250 m) | Validation fuel (`cfs_national_2014` scheme) | NRCan end-user agreement: internal use, not redistributed |
| CFS national FBP grids, current (2024/2026, 30/100 m; `cfs_national` scheme) | Code scheme supported; not yet integrated as a national fuel path | Open Government Licence – Canada [R2] |
| Canadian MRDEM 30 m DTM | Validation terrain | Open Government Licence – Canada |
| Municipality of Jasper "Damage Assessment Structures" (ArcGIS Online, owner MOJ_GIS, layer 0) | Observed per-building losses for the Jasper 2024 check: townsite footprints with `Status`, retrieved 2026-10-10 with `OBJECTID` and `Status` only (no street or address field) [R9], [R10] | **No stated licence: local use only, not redistributed.** Cached outside the repo (`~/dev/wildfire/validation-data/jasper2024/`), never committed or attached. Written permission and metadata to be requested from MOJ (§6) |
| CFS NOR-X-433, *Jasper Wildfire Complex 2024* (Northern Forestry Centre) | Townsite timing (18:00 first structure ignitions), wind for the Jasper check | Open Government Licence – Canada 2.0 |
| Municipality of Jasper, *Jasper Wildfire Complex Municipal After-Action Review* | First-impact areas and townsite timeline (pp.21-22, 56); demolitions at 21:30 | Public PDF, no licence stated; cited only |
| FPInnovations WF TR 2025 n.04, *Jasper wildfire: community impact research* | Spread mechanism, first rooftop ignitions (p.14), the < 5 m spacing finding (pp.2, 26) | No open licence stated; cited only (OCR'd locally for reading) |
| OpenStreetMap via the Overpass API (read-only) | Locating the named first-impact areas; only centres rounded to 0.001° are kept in the script | ODbL 1.0 |
| ECCC MSC GeoMet `climate-hourly` (14 Alberta stations, May-Sep 2024-25) | Ensemble input-error climatology (station truth) | Open Government Licence – Canada |
| GEM forecasts via Open-Meteo previous-runs API (day-1 values); ERA5 via Open-Meteo archive | Ensemble input-error climatology (forecast / reanalysis) | CC BY 4.0 (Open-Meteo); ECCC / Copernicus source licences |

Validation data live outside the repo (`$FIRESIM_VALIDATION_DATA`, default `~/dev/wildfire/validation-data`, ~0.6 GB).

### 4.4 Validation results (summary of `docs/validation.md`, 2026-10-07 to 2026-10-09)

- **Data:** 143 fire-days, 32 Alberta fires 2014-2024 (CFSDS), one burn day from the observed perimeter, Bennett et al. (2026) protocol, grid model, deterministic, no suppression.
- **Baseline (first round):** one-day F1 at the default 06-23 h window **0.15** (whole perimeter start, operational) and **0.24** with Bennett's ignition (W.I.S.E. defaults 0.26 nationally; tuned W.I.S.E. 0.54). Growth over-predicted on 93 % of days (perimeter start), normalised area difference +0.67; head bearing error median ~50°; forward spread within ±35 % on ~20 % of days. On nine shared fire-days with identical inputs: W.I.S.E. 0.19 vs FireSim 0.24.
- **Held-out (second round):** fires split by seeded hash (16 calibration / 16 test fires, 79 test fire-days), settings chosen on calibration only. Active edges (previous 2 days) + FFMC spin-up + burning period 10-20 h: test F1 **0.118 → 0.208** (ΔF1 +0.091, 95 % CI +0.056 to +0.125, bootstrap by fire); area difference +0.72 → +0.27; spread within ±35 % on 24 % of days. Oracle start 0.245. Largest wind-driven runs (Horse River 4-5 May 2016) under-predicted further.
- **Grid diagonal-spread fix (2026-10-09, PR #35):** re-run of the chosen set-up and the Bennett start, all 143 fire-days, before (`a8f8f35`) and after (`4bcfb1d`); "before" reproduced the numbers above exactly. Held-out 17 h F1: active edges + spin-up + 10-20 h **0.208 → 0.212** (ΔF1 +0.004, 95 % CI −0.001 to +0.009); Bennett start + spin-up 0.245 → 0.250 (+0.005, +0.000 to +0.011). Earlier windows dip slightly (8 h −0.003 / −0.008; best hour −0.004 / −0.007, CIs exclude zero but tiny). Skill effectively unchanged; the fix is a correctness fix (grid-orientation independence), not a skill change [R5].
- **Ensemble (third round, held-out; measured before the grid fix):** 20 members on the chosen set-up, 65 test fire-days (14 largest excluded by run time only). Calibrated sizes vs former placeholders: observed area inside members' P10-P90 **20 % → 49 %** (ideal ~80 %); CRPS of log10 area 0.546 → **0.471** (single run 0.616); Brier skill vs single run +0.18 → +0.25; spread/skill 4.7 → 1.6 (still too narrow); cells at ~95 % burn probability burned 33 % of the time; P50 F1 0.201 vs single run 0.195; **P10 footprint held ≥ 90 % of observed growth on 29 % of days**, so P10 is not a worst case.
- **Structure-spread sensitivity (2026-10-09; model sensitivity, not a validation)** [R7]: Hamada option, Edmonton footprints (334,192 units), 3 WUI sites (mature ravine edge with garages; lower-density ravine edge; newer suburb beside grass) × 2 days (FWI 15 and 72), 6 h, deterministic FBP run shared by all variants. Involved buildings at 6 h vs the defaults (30 m cutoff, 10 m contact, all footprints), over the 5 runs with a non-zero default: cutoff 20 m **−48 % to 0 %**, 45 m **+5 % to +220 %** (between 30 and 45 m the unit graph joins blocks across streets: largest component 2.5 % → 7.5 % of units); contact 5 m **−71 % to 0 %**, 20 m **+1 % to +557 %** (and 0 → 148 on one moderate run); dropping footprints < 40 m² **−8 % to 0 %**. The contact result is grid-dependent: on the 50 m engine grid with the all-touched building mask the nearest burnable cell lies 0-50 m from a footprint depending on where it sits in its cell. Defaults unchanged; a change to how contact is measured is recommended (§6). City-wide graph at 30 m: median nearest separation 2.56 m (2.58 m without < 40 m²), median degree 8, 1.6 % with no neighbour. Run time: whole city units 3-6 s, spread < 0.3 s per variant; the script's default reproduces the engine's own counts exactly and repeats identically.
- **Structure front contact from the building cells (2026-10-09; model sensitivity, not a validation)** [R8]: same sites, days, seed and FBP runs as [R7] (a rerun of the unchanged script reproduced [R7] exactly). With contact measured from each building's grid cells (`11c1230`): contact 5 / 20 m **0 %** in all six runs (the contact distance is below the 50 m grid's resolution); involved buildings at 6 h vs the first rule **+38 % to +751 %** (site A moderate 0 → 199; front contacts 2-6×; front-contacted footprints a median 22-42 m, at most ~70 m, from the nearest burned cell edge); cutoff 20 m **−58 % to −27 %**, 45 m **+4 % to +170 %**; dropping footprints < 40 m² **−12 % to 0 %**. The cutoff is now the main structural sensitivity; defaults unchanged. The script's default again matches the engine in all runs; the contact rule costs ~2 s per variant over the whole city.
- **Jasper 2024 structure check (2026-10-10; first test against observed Canadian losses)** [R10]: end-state, structure-only, ember entry imposed, pre-registered (§3).
  - **Primary run:** 15 seeds at 18:00, cutoff 30 m, wind 15 km/h SW, 18:00-24:00. 396 involved against 370 destroyed (358 official). TP 250 / FP 146 / FN 120 / TN 603; precision **63.1 %**, recall **67.6 %**, F1 65.3 %, **κ 0.472**.
  - **Baselines (κ):**

    | Baseline | κ |
    |---|---|
    | Count-matched distance band from the seeds | **0.516** |
    | Distance band with N = observed | 0.499 |
    | FPI < 5 m, one step | 0.025 |
    | FPI ≤ 5 m percolation | 0.095 |
    | Random at the observed rate | ≈ 0 |
    | Seeds only (no spread) | 0.011 |

  - **κ differences (250 m block bootstrap):** FireSim − band −0.044 (95 % −0.157 to +0.045); FireSim − seeds only +0.461 (+0.238 to +0.649).
  - **Pre-registered rule not met:** given the ember-entry locations, Hamada does no better than distance from them.
  - **Sensitivity (12 pre-specified runs):**
    - κ 0.19-0.51, precision 55-84 %, recall 22-92 %, involved count 124-593;
    - the cutoff dominates: 20 m gives 124 (κ 0.19), 45 m gives 593 (κ 0.51);
    - wind ±25 % and wind from SSW change little, because below 10 m/s Hamada is nearly isotropic (Hazus blend);
    - FireSim − band runs from −0.044 to +0.068, and every interval includes zero.
  - **Seeds and outcome:** scoring without the seeds, or with Destroyed + Visible Damage, changes κ by ≤ 0.03.
  - **Errors** sit at 250-500 m from the seeds:
    - FN: separate ember-ignited groups (not modelled);
    - FP: at the north-east front towards the commercial core, where the after-action review records demolitions at 21:30 (not recorded, so not testable).
  - **Graph:** at 5 / 20 / 30 m the largest component holds 1.6 / 8.6 / 49.7 % of units.
  - **Context only:** precision is in the range of FSJ104651 (59 / 9 / 77 %).
- **Grass curing default (2026-10-10; M1)** [R14]: chosen set-up, all 143 fire-days, only O-1 curing changed.
  - **Held-out 17 h F1:** 60 % everywhere (old default) **0.2126** → M1 (95 % in day of year 60-149; 60 % summer and 90 % fall as stand-ins for the user's entry) **0.2120**; ΔF1 −0.0007 (95 % CI −0.0017 to −0.0000). Area difference +0.246 → +0.248. Calibration 0.2251 → 0.2285. The former harness rule (90 / 60) gives 0.2120 and reproduces the recorded number.
  - **Spring-window fire-days** (the only ones M1 changes, with 2 fall days): 5 held-out from 3 fires −0.009 [−0.016, +0.000]; 9 calibration (6 Horse River) +0.024 [−0.010, +0.135], mostly one day where 60 % stopped the fire.
  - **Grass involvement:** O-1 is 3.4 % of all observed growth; present in the growth of 76 of 143 fire-days, ≥ 5 % of the growth on 23. Boreal fires: the harness cannot test the 95 % value.
  - **Sensitivity:** a silent 90 % in summer lowers held-out F1 (−0.008 [−0.019, −0.000]) and raises area over-prediction (+0.25 → +0.30), which supports no summer default.
- **Not yet measured:** RPAS-corrected mid-day restarts, real thermal-flight active edges, ensemble skill on the largest runs; the curing default against Edmonton grass-fire records.

## 5. Testing

| Suite | Command | What it checks |
|---|---|---|
| Engine (pytest) | `PYTHONPATH=engine/src:api/src python -m pytest engine/tests -q` | FBP vs cffdrs fixture, FWI + hourly FFMC, FBP ellipse agreement, WISE reference, level set, deployment data (head/flank/back), active edges, diurnal, ensemble, Albini worked examples, Cohen exposure, loaders/reprojection, national code schemes, validation metrics |
| API (pytest) | `PYTHONPATH=engine/src:api/src python -m pytest api/tests -q` | Endpoints, WebSocket frames, grid runs, FWI, weather, skill options |
| Frontend types | `cd frontend && npx tsc --noEmit -p tsconfig.app.json` | TypeScript (the root `tsconfig.json` has `files: []` + references, so plain `npx tsc --noEmit` checks nothing; CI's `npm run build` runs `tsc -b`) |
| Frontend unit (Vitest) | `cd frontend && npm test` | Classes, contrast, time zone, evac arrival, assets, ICS outputs, skill options, curing default |
| Frontend E2E (Playwright + axe) | `cd frontend && npx playwright test` | Production build via `vite preview`; API mocked by `tests/e2e/mockApi.ts` replaying `tests/fixtures/terwillegar_grass_4h.json` (house-to-house spread: `structure_spread_4h.json`, `record_fixture.py --structure`); external requests blocked; axe baseline, 12 px / 24 px layout rules, idle-redraw performance budget |
| Validation (not CI) | `scripts/validate.py prepare / run / report / compare` | Skill on observed fires (§4.4) |

**Oracle / reference provenance**
- **cffdrs fixture** `engine/tests/fbp/data/` generated by `engine/tests/fbp/data/generate_cffdrs_reference.py` from a `cffdrs_py` checkout (`CFFDRS_PY_PATH`), FFMC coefficient 147.2; 18 fuels × 6 weather/slope cases in CI, 12,960 cases in a development run; hourly FFMC 5,760 cases (max diff 3e-11).
- **WISE reference** `engine/tests/spread/data/wise_reference.json`: WISE 1.0.6-beta.6 (official Ubuntu build, headless) on uniform C-2 / O-1a, wind 0/20/30 km/h, flat and sloped planes (built at 30.5/40.5 % because WISE truncates slope to integer percent).
- **Cohen worked values:** SIAM 50 m × 20 m flame at 1200 K → 79 / 45 / 27 kW/m² at 10 / 20 / 30 m and ~60 s at 31 kW/m² (Cohen 2004); FireSim 80.6 / 45.9 / 27.8 and 59 s.
- **Albini/Chase worked examples:** Chase 1981 torching 0.34 mi; Albini 1983 surface fire 0.45 km.
- **CFSDS validation:** Barber et al. (2024) DOB rasters; metrics unit-tested on shapes with known answers; W.I.S.E. comparison numbers from Bennett et al. (2026) Table 1 plus nine local W.I.S.E. runs.
- **Frontend fixture:** recorded from the real engine by `npm run fixture:record` (`frontend/tests/fixtures/record_fixture.py`).

**Structure-spread branches (2026-10-09, local workstation, Python 3.13 venv; frontend Node 22, Playwright Chromium/SwiftShader):** `docs/structure-spread-spec` (docs only): engine 873 passed / 1 xfailed, API 103; `feat/structure-units`: engine **886 passed / 1 xfailed**, API **103**, `tsc` 0, build OK, Vitest **272**, Playwright **28**; `feat/structure-hamada` (stacked on units): engine **912 passed / 2 xfailed** (new strict xfail: Qin 2025's 0.34 m/s Hamada rate not reproduced, spec §4.5), API **107**, `tsc` 0, build OK, Vitest **272**, Playwright **28**.

**Latest results (2026-10-10, branch `feat/curing-default-and-citations` on `master` `67ac021`, local workstation, Python 3.13, Node 22; run while the validation batch shared the machine):**
- Engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`): **943 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 163 s. +12: `engine/tests/fbp/test_curing.py` (window edges, no default outside the window, curing factors 0.20 / 0.80 / 0.90 / 1.00 at 60 / 90 / 95 / 100 %, the 422 message, the harness curing rule).
- API pytest (`--asyncio-mode=auto`): **120 passed**, 99 s. +12: `api/tests/test_grass_curing.py` (spring default, `day_of_year` before `start_time`, 422 for uniform O-1 / grid / synthetic CA in summer and with no date, explicit values in any season, no value needed without grass, multi-day and burn-probability, endpoint 422 text and the echoed 95 %). Eight grid-run payloads in existing API tests now send `grass_cure: 60` explicitly (they relied on the old default).
- `npx tsc --noEmit -p tsconfig.app.json`: exit 0. `npm run build`: built.
- Vitest: **275 passed** in 17 files (+3, `utils/curing.test.ts`).
- Playwright + axe (`E2E_PORT=4431`, `E2E_SKIP_BUILD=1` after the build): **30 passed**, first attempt, 7.7 min. +2 in `curing.spec.ts` (summer: the field is required and Run waits, the entry is sent; spring: 95 % pre-filled and sent). `openApp` now enters 60 % curing by default, so the other specs do not depend on today's date.
- Validation: §4.4 and [R14].

**Previous results (2026-10-10, branch `fix/structure-memory-ui` merged with `master` `67ac021`, local workstation, Python 3.13, Node 22):** engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`) **940 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 60 s; API pytest (`--asyncio-mode=auto`) **112 passed**, 21 s; `npx tsc --noEmit -p tsconfig.app.json` exit 0; `npm run build` OK; Vitest **282 passed** in 17 files; Playwright + axe (`E2E_PORT=4391`, `E2E_SKIP_BUILD=1` after the build) **30 passed**, first attempt, 3.5 min. New: engine `test_reachable_build.py` (9: an independent front-contact check against a rasterio all_touched mask on 400 random footprints, added after the fixture check below; reachable build = whole-area build at three wind/duration settings, a short run builds < 15 % of the area's units, margin growth, the guard's labelled not-computed result, no burned cells builds nothing, involved-unit detail); API +4 (detail only on the final frame and only for involved units, totals = `units_involved`; guard over the API; no detail when off; `BuildingIndex` footprint source); Vitest +10 (series, GeoJSON, tooltip text, caveat, token colours, card counts/badge/tooltip/chart cursor/toggle, not-computed note); Playwright +2 (`structure.spec.ts`: opt-in and request flag, card counts, tooltip on hover/focus, legend badge, hover popup on a footprint, keyboard layer toggle, chart cursor with the timeline, axe on the new card and legend; off by default). Fixture check (2026-10-10, after the first screenshot): in `structure_spread_4h.json` every front-contact unit's time equals the earliest arrival over the 3x3 neighbourhood of the cells its footprint touches (21/21, independent check), unburned cells are inf (one arrival-0 cell: the ignition cell; the two units at t = 0 touch its neighbours), detail polygons are (lng, lat) and within 0.08 m of the units; building-to-building units reach up to ~380 m (centroid to nearest burned cell centre) beyond the 18 burned cells. The screenshot's "No spread" and midnight clock times were mock artefacts (the app used its own start and default burning period on a fixture recorded at 13:00 without one); the fixture is re-recorded as the app sends it (burning period 10-20, start 13:00; identical counts) and the e2e test sets that start. Count equivalence: `scripts/structure_sensitivity.py` rerun (twice, before and after this check), all 6 site × weather runs and all 8 variants identical to [R8], engine default matches in all 6 [R11].

**Previous results (2026-10-10, branch `analysis/jasper-structure-validation` on `master` `022032d`, local workstation, Python 3.13, Node 22):**
- Engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`): **931 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 63 s.
- API pytest (`--asyncio-mode=auto`): **108 passed**, 25 s.
- Vitest: **272 passed** in 16 files.
- The frontend is not touched, so `tsc`, the build and Playwright were not rerun.
- The change adds `scripts/jasper_structure_validation.py` and docs only; no engine code changed and no tests were added.
- **Reproducibility:** a second full run of the script reproduced every scored number exactly. The script asserts that no footprint is dropped and that only `OBJECTID` and `Status` are in the cache, and it refuses to write inside the repo.
- Validation results: §4.4 and [R10].

**Previous results (2026-10-09, branch `fix/structure-front-contact` on `master` `9ef70c9`, local workstation, Python 3.13, Node 22):** engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`) **931 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 57 s; API pytest (`--asyncio-mode=auto`) **108 passed**, 21 s; `npx tsc --noEmit -p tsconfig.app.json` exit 0; Vitest **272 passed** in 16 files; Playwright + axe (`E2E_PORT=4411`, with production build) **28 passed**, first attempt, 3.2 min. +8 engine tests for the building-cell front contact: grid-offset invariance (one house at five sub-cell offsets, same time; the first rule differed), contact 0-20 m one outcome, no contact two cells away or across a 1-cell road / 2-cell river, multi-cell footprints use only the cells their outline touches, an unmasked building cell that burns, a contact of one cell reaches one more ring. Sensitivity rerun: §4.4 and [R8].

**Earlier results (2026-10-09, branch `analysis/structure-sensitivity` on `master` `8fa6500`, local workstation, Python 3.13, Node 22):** engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`) **923 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 56 s; API pytest (`--asyncio-mode=auto`) **108 passed**, 20 s; `npx tsc --noEmit -p tsconfig.app.json` exit 0; Vitest **272 passed** in 16 files; Playwright + axe (`E2E_PORT=4397`, with production build) **28 passed**, first attempt, 3.1 min. The change adds `scripts/structure_sensitivity.py` and docs only (no tests added; the script checks its default against the engine's own counts). Sensitivity results: §4.4 and [R7].

**Earlier results (2026-10-09, branch `feat/structure-hamada` merged with `master` `c264e27`, local workstation):** engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`) **923 passed, 1 xfailed** (strict xfail: Hamada rate 0.197 vs Qin's 0.34 m/s), 57 s; API pytest **108 passed**, 21 s; `tsc` exit 0; Vitest **272 passed**; Playwright + axe in CI `frontend-e2e`.

**Earlier results (2026-10-09, branch `feat/ensemble-calibration` merged with `master` `615d132` (PR #35), local workstation):**

| Suite | Result | Wall time |
|---|---|---|
| Engine pytest (`PYTHONPATH=engine/src`, `--import-mode=importlib`) | **884 passed**, 0 xfailed, 0 skipped | 52 s |
| API pytest | **104 passed** (1 warning) | 21 s |
| `npx tsc --noEmit -p tsconfig.app.json` | exit 0 | |
| Vitest (`npm test`) | **272 passed** in 16 files | |
| Playwright + axe | CI `frontend-e2e` on the merged branch | |

Results on the ensemble branch before this merge (2026-10-09, rebased on `a8f8f35`): engine 879 passed / 1 xfailed, API 104, Vitest 272, Playwright 28.

Results on PR #35 before this merge (2026-10-09, branch `fix/grass-diagonal-spread` at `4bcfb1d` on `a8f8f35`, local workstation, machine loaded by validation runs):

| Suite | Result | Wall time |
|---|---|---|
| Engine pytest | **878 passed**, 0 xfailed, 0 skipped (the former strict xfail `test_point_ignition_grass_diagonal_wind` passes; +4 tests: `TestRotationalInvariance` ×3, starting-ellipse corner connectivity) | 163 s |
| API pytest | **103 passed** (2 warnings) | 70 s |
| `npx tsc --noEmit -p tsconfig.app.json` | exit 0 | |
| `npm run build` | built | 15 s |
| Vitest (`npm test`) | **272 passed** in 16 files | 7 s |
| Playwright + axe (`E2E_PORT=4392`, `E2E_SKIP_BUILD=1`) | **28 passed**, first attempt | 6.9 min |
| CI (PR #35) | engine-tests 3.11 / 3.12, frontend, frontend-e2e: pass | |

Previous results (2026-10-08, branch `fix/record-open-items` rebased on `03997fd`, local workstation, machine loaded):

| Suite | Result | Wall time |
|---|---|---|
| Engine pytest | **873 passed, 1 xfailed** (strict xfail: `test_point_ignition_grass_diagonal_wind`, §6), 0 skipped | 291 s |
| API pytest | **103 passed** (1 Starlette deprecation warning) | 112 s |
| `npx tsc --noEmit -p tsconfig.app.json` | exit 0 | |
| `npm run build` | built | 29 s |
| Vitest (`npm test`) | **272 passed** in 16 files | |
| Playwright + axe (`E2E_PORT=4391`, `E2E_SKIP_BUILD=1`) | **28 passed**, first attempt | 10.4 min |

The three real-raster integration tests now run (no skips). Earlier results, worktree at
`b1a0136` (machine heavily loaded by validation runs):

| Suite | Result | Wall time | Environment |
|---|---|---|---|
| Engine pytest | **864 passed, 3 skipped**, 0 failed | 222 s (3 min 42 s) | Python 3.13.12 venv |
| API pytest | **102 passed**, 0 failed (2 warnings: Starlette `httpx` deprecation; rasterio `NotGeoreferencedWarning` in a synthetic-grid test) | 91 s | Python 3.13.12 venv |
| `npx tsc --noEmit -p tsconfig.app.json` | exit 0, no errors | 31 s | Node 22.22.0, TypeScript 5.9 |
| `npx tsc --noEmit -p tsconfig.node.json` | exit 0 | 10 s | |
| `npx tsc --noEmit` (root) | exit 0, but checks no files (`files: []`) | 1 s | |
| Vitest (`npm test`) | **255 passed** in 15 files, 0 failed | 7.8 s (10 s with start-up) | Vitest 5.0.3 |
| Playwright + axe (`npx playwright test`, `E2E_PORT=4391`) | **26 passed**, 0 failed, first attempt (no rerun needed) | 9.6 min (incl. production build) | Playwright 1.63.0, Chromium/SwiftShader, 1 worker |

The 3 engine skips at `b1a0136` were the real-raster integration tests (ignition on a non-fuel
cell), fixed in `af052e4`. The live API reported `git_sha` `b1a0136` at `GET /api/v1/version` and the live
frontend returned HTTP 200 the same day; no browser check of the live site was made for this
docs-only change. The E2E suite runs against a local production build with a mocked API, not
against the live site.

## 6. Open items

- [ ] **Ensemble still under-dispersed and over-confident** (held-out spread/skill 1.6; ~95 % burn probability → 33 % burned). **Fix area bias through set-up (active edges, burning period, spin-up); any ROS correction only by fire type and fuel** (M3, 2026-10-10 [R12]; reworded from "remove the shared over-prediction bias"). Evidence [R13] rec 3: FBP under-predicts spread in 75 % of 49 datasets (Cruz & Alexander 2013, p.16), on the ICFME crown fires (Stocks et al. 2004, p.1557) and on Alberta 2023 C-2 runs (Beverly & Schroeder 2025, p.8: predicted 18.7 m/min); FireSim's own Horse River head runs reach 0.1-0.4 of observed; the area over-prediction comes from default burn conditions and the whole perimeter being active (Bennett et al. 2026, p.9). A ROS factor, if any, is estimated per FBP fire type (surface / crown) and fuel group on calibration fires, reported per stratum with the > 1,000 ha days separately, and documented as a model-discrepancy term. Validate on the largest runs; curing and FMC perturbation sizes unvalidated.
- [ ] **Real RPAS thermal perimeters untested:** active edges were validated only with a CFSDS previous-day proxy; validate with real thermal flights and measure mid-day restarts.
- [ ] **Edmonton fuel grid never accuracy-assessed:** needs a stratified check (≥ 50 cells per class, confusion matrix, Wilson intervals), embedded metadata, correct filename, seasonal (green-up) switching; `percent_conifer.tif` is not read [R2].
- [ ] **National fuel grid integration:** CFS Current-Year FBP layer (100 m, OGL – Canada) as the base outside Edmonton; decide the code-13 stand-in (≈ 18 % of Canada); only uniform or synthetic fuel outside Edmonton today.
- [ ] **Water layer rebuild** from authoritative hydrography (City, CanVec/NRCan); repair invalid polygons; mask only where the LiDAR grid is non-fuel.
- [ ] **Name clash:** "FireSim" is a module of Technosylva's Wildfire Analyst; rename before any release outside the City.
- [ ] Per-fuel calibration (D-2 and small days remain poor); per-cell percent conifer; current fuel grid with burn scars.
- [ ] `load_fuel_grid` treats a geographic raster's degrees as metres (noted in `docs/validation.md`; check whether the 2026-10-07 reprojection fix resolved it).
- [ ] ICS 209-WF follow-ups: the ICS Canada 209 instructions cover the all-hazards 209; the block 9 code expansions (OC/BH/UC/O) and whether Alberta agencies file 209-WF are unverified; 30B uses a 100 m exposure band as "threatened" (a FireSim choice, not from the form); runs shorter than 12 h leave the 12-72 h horizons "not projected".
- [ ] Heterogeneous fuel, real terrain, barriers and spotting not compared with WISE, Burn-P3 or Cell2Fire.
- [ ] Unverified citations: Byram (1959), Thomas (1963), Rothermel (1991) and Tran et al. (1992) originals not read (references now checked against citing papers); Class 1/6 meanings flagged for the owner's check in the redesign notes; `spread/ellipse.py` listed an "Anderson, K. et al. (2009) Prometheus" reference that could not be identified (now marked unverified in the code; Prometheus is documented by Tymstra et al. 2010, NOR-X-417, not on disk). Resolved 2026-10-10: Alexander (2010) full reference; Fox-Hughes et al. (2024) title; Tran et al. (1992) title and proceedings; Van Wagner & Pickett (1985) mislabelled "PS-X-58" in `routers/fwi.py` and `test_fwi.py` (it is Forestry Technical Report 33); the "Alexander & de Groot (1988)" HFI reference in `icsForms.ts` (the forms take their classes from `fireClasses.ts`, Cole & Alexander 1995); Beverly et al. (2010) DOI.
- [ ] **ICS forms: complex-incident check never fires.** `utils/icsForms.ts` tests `["III", "IV", "V"].includes(intensityClass)`, but the class is now the number 1-6 from `fireClasses.ts` (as a string, "1"-"6"), so the "complex" ICS 207 branch is never taken. Found 2026-10-10 while checking the citation; not fixed here (a UI cleanup PR follows).
- [ ] **M1 follow-ups (grass curing):** a local check of the 95 % spring default against Edmonton grass-fire records (EFRS response times and areas, where they exist); curing from satellite greenness (Pickell et al. 2017; CFFDRS-2025, GLC-X-26 p.18) instead of a fixed window; the Cruz et al. (2015) curing curve when the next FBP adopts it (GLC-X-26 p.29). The CFSDS harness hardly tests curing (grass ≈ 3 % of observed growth) [R14].
- [ ] **M2 (planned):** keep FBP's fixed CBH in product runs; mark `FuelGrid.cbh` as research-only; add a Perrakis et al. (2023) crown-fire likelihood layer (Model 10/11) from LiDAR live crown base height, masked to stand-forming conifer cells; needs a city-wide CBH / fuel strata gap raster and a field check of about 20 cloud3 trees [R12], [R13] rec 2.
- [ ] **M4 (planned):** ember sources = torching or crowning cells (Van Wagner criteria, CFB > 0) instead of HFI ≥ 4,000 kW/m; add Albini, Alexander & Cruz (2012) active crown-fire spotting; LiDAR tree heights for the Albini torching inputs; validate on worked examples and documented spotting distances, not F1 [R12], [R13] rec 4.
- [ ] **M5 (planned):** native 20 m fuel grid in a window around buildings / the WUI, 50 m elsewhere; average predictions over fuel-map realizations, never the fuels; measure memory and run time against the 2 GB live server first; 20 vs 50 m sensitivity on the [R7] sites [R12], [R13] rec 5.
- [ ] **M6 (planned, licence first):** check the City tree inventory's open-data licence; if allowed, add the 364 managed conifer clusters outside the uPLVI boundary as torching ember sources (M4), not as stand crown fire [R12], [R13] rec 9.
- [ ] Roadmap after ensemble: time-available vs time-needed per zone, trigger buffers / ember reach (river is not a barrier), exercise/replay mode.
- [ ] **Structure-to-structure spread (spec 2026-10-09):** design fire (D1) and 30 m cutoff (D2) **confirmed** under the owner's delegation 2026-10-09 [R6]; Qin (2025) reports a Hamada rate of 0.34 m/s for a = d = 10 m at 17.8 m/s where the published equations give 0.197 m/s (unexplained; stays open, strict xfail kept, **no author contact**, D6); PROCI SI flame-reach intercepts (≈ 120 m reach at 10 m/s) to be checked against IJWF/Hamada before the WU-E stage; no Canadian validation (`docs/structure-spread-spec.md` §8).
- [ ] **Jasper 2024 (D5): public data found [R9]; first pre-registered check done [R10]; it did not beat the distance baseline.** Still open (owner action, through the EM network; de-identified, no addresses):
  - **MOJ:** written permission and metadata for the damage layer (no licence stated; assessment method and date, 370 vs 358, post-2024 edits); locations of the 21:30 demolitions and heavy-equipment breaks.
  - **CFS / Parks Canada:** NOR-X-433 progression polygons and ETA points as GIS; Ranger Creek, Dorothy and Paradise hourly records for 22-24 July.
  - **FPInnovations / CFS:** the per-structure table (roof class, spacing, exposure or ignition mode) and the block layer.
  - **Then:** a coupled run on a Jasper fuel grid (CFS national FBP 30 m), with its own front contact.
  - **Calibration:** choose any cutoff or f_b change on a different fire (e.g. Fort McMurray 2016) before re-testing on Jasper.
- [ ] **Structure-spread display vs the Jasper result.** The owner reinstated per-building display on 2026-10-10 (D3 reversed; [R6] addendum). The Jasper check found building-level precision of 63 % (55-84 %) and no gain over a distance band [R10], so keep the "illustrative — not validated" legend caveat and state the 30 m cutoff dependence (count 124-593 over cutoffs of 20-45 m on Jasper).
- [ ] **Structure front contact is made at grid resolution** (after `11c1230`, [R8]): contact no longer depends on a footprint's position in its cell, but on the 50 m grid a building is reached when the front reaches a cell next to its cells (median 22-42 m, up to ~70 m, from the footprint), and `wildland_contact_m` has no effect below one cell. Finer options for later, each with its own re-run: coverage-fraction masking, or a finer fuel grid near buildings.
- [ ] **Structure spread in the app (from 2026-10-10):** opt-in option, counts, chart and per-building map layer (D3 superseded by the owner, decisions log 2026-10-10); still to check with users that the per-building map is read as modelled involvement and not as a loss forecast; not yet in any export or ICS 209 (by design). Building mask on the fuel grid still covers only the 4 nearest neighbourhoods, while structure units may come from the whole run area (the building-cell contact rule needs no special case for it: outside the mask a building's own cells can burn; a mask over every building would change the wildland run itself, so it is not done here). 12,025 of the 346,238 footprints have their centroid outside the Edmonton fuel grid's box and never become units.
- [ ] **API memory baseline:** without structure spread a grid run with buildings already peaks at ~1.09 GB locally, mostly the `BuildingIndex` holding all 346,238 footprints as raw GeoJSON dicts (plus the JSON load); compact coordinate arrays would cut it. The structure-spread guard (60,000 units) is sized from local measurements, not on the Fly machine; the live API was not exercised with `structure_spread` after the fix (no deploy in this PR) [R11].
- [ ] Minor UI: "Click map to set ignition point" hint lingers after a typed ignition (owner note 2026-10-08; status not re-checked).
- [ ] Critical assets (after PR #31): 2 Alberta and 2 ODHF care sites could not be positioned; group homes and sites under 10 units excluded by design; OSM-only care sites flagged "verify".

Closed 2026-10-08: ICS Canada 209 (`d34b349`, `8f349e6`); skipped real-raster tests (`af052e4`); spotting
repeatability (`f681189`); `CLAUDE.md` water and building sources (`45e68d2`); roads
extraction script and `secondary` roads, ODHF care records without coordinates (`2357a02`,
PR #31); EOC manual point, left out of the layer (PR #33, `8da78f5`, merged `dd523f6`). See the decisions log (§3).

Closed 2026-10-09: grid-model point ignitions with off-axis wind (found 2026-10-08; strict xfail
`test_point_ignition_grass_diagonal_wind` now passes): root cause 4-connected labelling of the
starting ellipse plus cross-wind numerical diffusion of axis-by-axis upwinding; fixed in `4bcfb1d`
(PR #35). See the decisions log (§3) and `docs/verification.md` §2.

Closed or adjusted 2026-10-09 (owner delegation, decisions D1-D6 [R6]): structure design-fire
default (150 kW/m², 400 kW/m² scenario) and 30 m neighbour cutoff confirmed; per-building
structure output stays off (aggregate counts only); the footprint check against City
building/parcel data is replaced by the < 40 m² size-filter sensitivity [R7] (−8 % to 0 %
involved buildings; city-wide median nearest separation 2.6 m with or without footprints
< 40 m², so the short gaps are mostly not small sheds; a parcel join stays a possible later
refinement, needed only if per-building output is ever introduced; the earlier measurement on
all 346,238 footprints stands: median nearest separation 2.6 m, 79 % with a neighbour within
5 m, 94 % within 10 m, 5,916 with none within 30 m, median square-equivalent size 11.6 m). Still open: Jasper public-data
search (D5), Qin 0.34 m/s (D6, no author contact), and the grid-dependent front contact found by
[R7] (closed below).

Closed 2026-10-09: **structure front contact depended on the 50 m grid** (found by [R7]: the
all-touched building mask put the nearest burnable cell 0-50 m from a footprint depending on its
position in the cell, so the 10 m test flipped with it; contact 5 / 20 m changed involved
buildings by −71 % to +557 %). Contact is now measured from the building's own grid cells
(`11c1230`, spec §3, decisions log D2 follow-up); contact 5 / 20 m now change nothing, and the
remaining grid dependence (contact at cell resolution) stays open above [R8].

## 7. Work log

Generated from git: `git log --since=2026-10-01 --format="| %ad | \`%h\` | %s |" --date=short`.
No bot commits in this range. Branch-sync merges ("Merge branch 'master' into …", "Merge
(origin/)master into …") are omitted; PR merges are kept. 119 earlier commits (2026-02-12 to
2026-09-30, incl. `TRA-XXX` task history) are not listed. Regenerated 2026-10-10 on branch
`feat/curing-default-and-citations` (this PR's own documentation commit is not listed).

| Date | Commit | Summary |
|---|---|---|
| 2026-10-10 | `cd92175` | fix: citation corrections (flame length, FWI report number, Fox-Hughes, Prometheus, HFI classes) |
| 2026-10-10 | `2ab2102` | feat(engine+api+frontend): date-aware grass curing default (M1) |
| 2026-10-10 | `206e9bd` | test: independent front-contact check; structure fixture on the app's clock |
| 2026-10-10 | `67ac021` | Merge pull request #42 from Tphambolio/analysis/jasper-structure-validation |
| 2026-10-10 | `143ec13` | docs: record the structure-spread memory fix and the house-to-house UI |
| 2026-10-10 | `48776b5` | feat(frontend): house-to-house spread option, counts, chart and map layer |
| 2026-10-10 | `f5666a6` | fix(engine+api): build structure units only where the spread can reach |
| 2026-10-10 | `1b81a95` | docs: record the pre-registered Jasper 2024 structure check and its result |
| 2026-10-10 | `4e37d41` | fix(scripts): correct after-action review and FPI page citations in the Jasper pre-registration |
| 2026-10-09 | `ae33a21` | feat(scripts): pre-registered Jasper 2024 structure validation (Hamada, structure-only) |
| 2026-10-10 | `022032d` | Merge pull request #41 from Tphambolio/fix/structure-front-contact |
| 2026-10-09 | `97ebb69` | docs: record the building-cell front contact rule and its sensitivity rerun |
| 2026-10-09 | `11c1230` | fix(engine): measure structure front contact from the building's own grid cells |
| 2026-10-10 | `9ef70c9` | Merge pull request #40 from Tphambolio/analysis/structure-sensitivity |
| 2026-10-09 | `ff2c224` | docs: record structure-spread decisions D1-D6 and the sensitivity results |
| 2026-10-09 | `cba9868` | feat(scripts): structure-spread sensitivity to cutoff, contact distance and footprint size |
| 2026-10-09 | `8fa6500` | Merge pull request #39 from Tphambolio/feat/structure-hamada |
| 2026-10-09 | `c264e27` | Merge pull request #38 from Tphambolio/feat/structure-units |
| 2026-10-09 | `0f68244` | Merge pull request #37 from Tphambolio/docs/structure-spread-spec |
| 2026-10-09 | `5a91105` | Merge pull request #36 from Tphambolio/feat/ensemble-calibration |
| 2026-10-09 | `615d132` | Merge pull request #35 from Tphambolio/fix/grass-diagonal-spread |
| 2026-10-09 | `0850a65` | docs: record the grid diagonal-spread fix, its root cause and validation effect |
| 2026-10-09 | `78d6a6c` | docs: record structure-spread test results |
| 2026-10-09 | `b26c730` | feat(engine+api): opt-in Hamada structure-to-structure spread (illustrative) |
| 2026-10-09 | `968c747` | docs: ensemble calibration on observed Alberta fires |
| 2026-10-09 | `9ef2655` | feat(frontend): P10 is the early end of the ensemble range, not worst-credible |
| 2026-10-09 | `3dc62a7` | feat(engine+api): calibrated ensemble perturbation defaults |
| 2026-10-09 | `57313eb` | feat(engine): probabilistic ensemble validation on observed fires |
| 2026-10-09 | `1fe964b` | feat(engine): building units for structure-to-structure spread |
| 2026-10-09 | `4bcfb1d` | fix(engine): point ignitions spread the same with the wind on a grid diagonal |
| 2026-10-09 | `b3b58a9` | docs: structure-to-structure spread specification from the published literature |
| 2026-10-08 | `a8f8f35` | Merge pull request #34 from Tphambolio/docs/record-after-33 |
| 2026-10-08 | `33736fc` | docs: record PRs #32 and #33 as merged; refresh work log |
| 2026-10-08 | `dd523f6` | Merge pull request #33 from Tphambolio/fix/drop-eoc-point |
| 2026-10-08 | `27a3a02` | Merge pull request #32 from Tphambolio/fix/record-open-items |
| 2026-10-08 | `d97f587` | docs: record the owner's decision to leave the EOC point out (PR #33) |
| 2026-10-08 | `8da78f5` | fix(data): leave the EOC point out of the critical-assets layer |
| 2026-10-08 | `89aa36f` | docs: record closes four open items and PR #31; Claude for Science project id |
| 2026-10-08 | `8f349e6` | fix(frontend): keep PR #31's labels in the 209-WF after rebase |
| 2026-10-08 | `d34b349` | feat(frontend): ICS Canada Form 209-WF situation report replaces NIMS ICS-209 |
| 2026-10-08 | `45e68d2` | docs: correct CLAUDE.md data-file sources |
| 2026-10-08 | `af052e4` | test(engine): run the real-raster simulation tests from known fuel cells |
| 2026-10-08 | `f681189` | fix(engine+api): repeatable ember spotting with a seeded local RNG |
| 2026-10-08 | `03997fd` | Merge pull request #31 from Tphambolio/fix/asset-data-gaps |
| 2026-10-08 | `2357a02` | fix(frontend): resolve critical-asset data gaps (care facilities, EOC, roads, duplicates) |
| 2026-10-08 | `a80c265` | Merge pull request #30 from Tphambolio/docs/project-record |
| 2026-10-08 | `7c71205` | docs: project record (decisions, methods and sources, data licences, tests, open items, work log) |
| 2026-10-08 | `b1a0136` | Merge pull request #29 from Tphambolio/feat/critical-assets |
| 2026-10-08 | `0751583` | feat(frontend): automatic critical assets and roads with modelled arrival times |
| 2026-10-08 | `eda7239` | Merge pull request #28 from Tphambolio/feat/skill-options-ui |
| 2026-10-08 | `c53fafb` | feat(frontend): burning period, FFMC spin-up and RPAS active edges in the UI |
| 2026-10-08 | `c35e308` | feat(engine+api): burning period and FFMC spin-up options on the API |
| 2026-10-08 | `c080cb3` | Merge pull request #26 from Tphambolio/feat/ensemble-ui |
| 2026-10-08 | `665d65c` | Merge pull request #27 from Tphambolio/feat/spread-skill |
| 2026-10-07 | `6934822` | docs: second-round validation with held-out fires, active edges and diurnal burning |
| 2026-10-07 | `7d83ab7` | feat(frontend): ensemble-first view (P10 arrival lines, burn probability, area range) |
| 2026-10-07 | `37311ef` | feat(engine+api): active perimeter edges, hourly-FFMC spin-up, burning period |
| 2026-10-07 | `582e22e` | perf(engine): vectorise the grid model's per-cell FBP table |
| 2026-10-07 | `c832156` | Merge pull request #25 from Tphambolio/fix/limits-badge |
| 2026-10-07 | `c58a5e0` | fix(frontend): limits badge and ICS-209 disclaimer reflect the first validation |
| 2026-10-07 | `3afe4c2` | Merge pull request #23 from Tphambolio/fix/water-mask-default |
| 2026-10-07 | `4ef5e3d` | Merge pull request #24 from Tphambolio/fix/initial-front-zero-ros |
| 2026-10-07 | `584083d` | Merge pull request #22 from Tphambolio/feat/validation-harness |
| 2026-10-07 | `ce3a662` | docs: validation results against observed Alberta fires; terrain on the simulation grid |
| 2026-10-07 | `0cab14c` | fix(engine): seed spotting per fire-day in the validation harness |
| 2026-10-07 | `498fec7` | feat(engine): validation harness against observed fires (CFSDS, Bennett 2026 protocol) |
| 2026-10-07 | `5baad07` | feat(engine): national FBP fuel grid code schemes; fix uint8 no-data crash |
| 2026-10-07 | `3095bd8` | fix(engine): starting ellipse no longer burns fuel that cannot spread |
| 2026-10-07 | `d5f3bc1` | fix: OSM water mask off by default; D-2 BUI note in Setup |
| 2026-10-07 | `c598d52` | Merge pull request #20 from Tphambolio/feat/evac-outlines |
| 2026-10-07 | `755f706` | Merge pull request #17 from Tphambolio/feat/ignition-start |
| 2026-10-07 | `f4e139c` | Merge pull request #18 from Tphambolio/feat/ensemble-arrival |
| 2026-10-07 | `a484675` | Merge pull request #21 from Tphambolio/fix/raster-georeferencing |
| 2026-10-07 | `8ed5eb1` | fix(engine): reproject fuel and DEM rasters onto the lat/lng grid |
| 2026-10-07 | `c815fcf` | feat(frontend): evacuation status set by Planning as blue outlines; no suggested tiers |
| 2026-10-07 | `2103fe2` | Merge pull request #19 from Tphambolio/docs/audit-2026-10 |
| 2026-10-07 | `0593320` | docs: bring README, model card and references up to date |
| 2026-10-07 | `9a96835` | feat(engine+api): ensemble arrival percentiles (P10/P50/P90) and burn probability |
| 2026-10-07 | `c3bdb30` | feat(frontend): ignition fields, scenario start time and keyboard ignition |
| 2026-10-07 | `f0bf1d4` | Merge pull request #16 from Tphambolio/feat/layout |
| 2026-10-07 | `a645528` | Merge pull request #15 from Tphambolio/feat/tokens-shell |
| 2026-10-06 | `89a0a4b` | fix(frontend): time model follows Alberta's permanent UTC-6 from 2026-11-01 (tzdata 2026c) |
| 2026-10-06 | `d8affaa` | feat(frontend): map-first layout with Setup sections, Situation panel and clock-time timeline (EOC redesign PR 3) |
| 2026-10-06 | `f0a3c28` | feat(frontend): design tokens, dark theme and new top bar (EOC redesign PR 2) |
| 2026-10-06 | `3c53f7e` | Merge pull request #14 from Tphambolio/fix/remove-rpas-standoff |
| 2026-10-06 | `df8275d` | fix(frontend): neutral crown-fire safety wording in ICS 201 |
| 2026-10-06 | `5771c73` | fix(frontend): remove the unsourced 2 km crown-fire personnel rule from ICS 201 |
| 2026-10-06 | `5f3be56` | fix(frontend): remove the unsourced RPAS stand-off distance |
| 2026-10-06 | `571b7bd` | Merge pull request #13 from Tphambolio/feat/start-time |
| 2026-10-06 | `b92d411` | feat(api+frontend): scenario start_time; forecast aligned to the start |
| 2026-10-06 | `c8c3993` | Merge pull request #12 from Tphambolio/feat/fire-classes |
| 2026-10-06 | `10ce32b` | feat(frontend): one sourced head fire intensity class table |
| 2026-10-06 | `1a7f046` | Merge pull request #11 from Tphambolio/test/frontend-harness |
| 2026-10-06 | `3dc09e0` | test(frontend): re-record fixture in incremental mode after PR #10 |
| 2026-10-06 | `b4e6347` | test(frontend): add Vitest, Playwright and axe test harness (PR 0) |
| 2026-10-06 | `779d342` | Merge pull request #10 from Tphambolio/feat/deployment-data |
| 2026-10-06 | `487f8b0` | feat(engine+api+frontend): deployment data - head/flank/back, head summary, incremental frames |
| 2026-10-06 | `6df2df9` | Merge pull request #9 from Tphambolio/fix/ui-quick-wins |
| 2026-10-06 | `43f0800` | fix(frontend): UI quick wins from the graphics/UX audit |
| 2026-10-06 | `bad62f0` | Merge pull request #8 from Tphambolio/feat/building-exposure |
| 2026-10-06 | `87e4f2f` | feat: building exposure (distance bands, Cohen radiant flux, flux-time index) |
| 2026-10-06 | `ef1d2cd` | Merge pull request #7 from Tphambolio/fix/wui-off-crown-flame |
| 2026-10-06 | `aae819b` | fix: WUI multipliers off by default (unsourced); crown-fire flame length |
| 2026-10-06 | `5b29687` | Merge pull request #6 from Tphambolio/feat/hourly-weather |
| 2026-10-06 | `a16f182` | feat: hourly weather streams with hourly FFMC; forecast option in the UI |
| 2026-10-06 | `453124c` | Merge pull request #5 from Tphambolio/feat/slope-validation-buildings |
| 2026-10-06 | `294cb0b` | fix(api): frames carried no buildings_at_risk or ignition_snapped_m; test: WISE slope reference |
| 2026-10-06 | `8b5bae6` | Merge pull request #4 from Tphambolio/feat/albini-spotting |
| 2026-10-05 | `ad1d2fa` | feat(engine): Albini/Chase/Morris maximum spotting distance |
| 2026-10-06 | `631f4e3` | Merge pull request #3 from Tphambolio/fix/levelset-band |
| 2026-10-05 | `21f92ac` | fix(engine): level-set front lagged; add WISE reference comparison |
| 2026-10-06 | `f6a39c1` | Merge pull request #2 from Tphambolio/feat/acceleration-docs |
| 2026-10-05 | `d398fac` | docs: grid-model continuation, outlines and model selection |
| 2026-10-05 | `3605414` | fix(engine+api): grid model continues existing fires; real outlines; all grids use level set |
| 2026-10-05 | `ddd4551` | feat(engine): FBP point-ignition acceleration; 1 m Huygens start; docs rewrite |
| 2026-10-06 | `801634c` | Merge pull request #1 from Tphambolio/feat/levelset-grid-spread |
| 2026-10-05 | `876cf94` | feat(frontend): label FireSim as a planning and training tool |
| 2026-10-05 | `5061025` | feat(engine): level-set grid spread driven by the FBP fire ellipse |
| 2026-10-05 | `273e11f` | fix(engine+api): fuel raster code-scheme detection; add drone-pipeline scheme |
| 2026-10-05 | `e5a6ad6` | feat(engine): optional per-cell crown base height and crown fuel load layers |
| 2026-10-05 | `010625f` | fix(frontend): keep app usable when the map cannot start (WebGL unavailable) |
| 2026-10-05 | `289a1d0` | fix(engine+api+frontend): conform FBP layer to ST-X-3 / Wotton 2009 (verified against cffdrs) |
