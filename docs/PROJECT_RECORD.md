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
- **Status at this revision:** `master` `dd523f6` (PR #33, 2026-10-08); tests 2026-10-08 on PR #32's branch before merge: engine 873 passed / 1 xfailed (known defect, §6) / 0 skipped, API 103, Vitest 272, Playwright 28, all green (§5).

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
| `engine/src/firesim/structures/` | Structure-to-structure spread (opt-in, illustrative): `units.py` builds one unit per building footprint (centroid, footprint in local metres, area, square-equivalent size, neighbour graph with edge-to-edge separations within a cutoff, default 30 m); specification `docs/structure-spread-spec.md` |
| `engine/src/firesim/data/` | Fuel loader (code schemes incl. Edmonton canopy LiDAR, drone pipeline, CFS national), DEM loader, reprojection (`raster_grid.py`), WUI/water/building masks, synthetic demo grid |
| `engine/src/firesim/validation/` | CFSDS validation harness (`cfsds.py`, `harness.py`, `metrics.py`, `report.py`, `weather.py`) |
| `api/src/firesim_api/` | FastAPI: `routers/` (simulations, fwi, weather, health), `services/runner.py` (background runs, grid cache), `schemas/`, `ws/` |
| `frontend/src/` | React 19 + TypeScript + Vite + MapLibre GL: `MapView`, Setup sections, Situation panel (`FireMetrics`, `EvacStatusPanel`, `CriticalAssetsPanel`, `EOCSummary`), `TimeSlider`, `EOCConsole` (ICS forms, ICS Canada 209-WF); shared tables `utils/fireClasses.ts`, `utils/fwiClass.ts`, `utils/time.ts` |
| `scripts/build_edmonton_assets.py` | Builds `frontend/public/edmonton/assets.geojson` (critical assets) from open sources |
| `scripts/build_edmonton_roads.py` | Builds `frontend/public/edmonton/roads.geojson` (motorway to secondary + ramps) from Overpass |
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
| standing | Ask the owner before each merge (auto-merge is enabled but still ask) | `CLAUDE.md` (CI/CD); owner notes |
| standing | Run the full verification stack, including browser E2E, before calling work done | Owner feedback note (2026-06-16) |
| standing | No Claude/Anthropic references in code committed to City of Edmonton systems (`git.edmonton.ca`); this GitHub repo is separate | `CLAUDE.md` |

## 4. Methods and references

### 4.1 Method → source

| Component | Implementation | Source |
|---|---|---|
| FBP fuel types, ROS, BUI effect, SFC, CFB, TFC, HFI, LB, back/flank ROS, net effective wind + slope, FMC from date, point-ignition acceleration | `fbp/calculator.py`, `fbp/constants.py`, `fbp/crown_fire.py` | ST-X-3 [1] eqs 1-8, 26-58, 70-73, 81; GLC-X-10 [2] (grass curing eqs 35a/b, M-4 `c`, revised forms); verified against `cffdrs` [6] |
| D-2 no spread below BUI 80 | `fbp/calculator.py` | Alexander (2010) **as cited by cffdrs** [7]; full citation not checked (unverified) |
| Crown fire initiation, CFB | `fbp/crown_fire.py` | Van Wagner (1977) crown fire [3]; ST-X-3 eqs 56-58 |
| FWI System (FFMC, DMC, DC, ISI, BUI, FWI) | `fwi/calculator.py` | Van Wagner & Pickett (1985) [5]; Van Wagner (1987) [5b] |
| Hourly FFMC | `fwi/` hourly path | Van Wagner (1977) PS-X-69 [4]; matches cffdrs `hffmc` |
| Diurnal burning: FFMC spin-up from 17:00 local the previous day; burning period (spread only between set hours) | `spread/diurnal.py` | Lawson et al. (1996) [17] (daily FFMC ≈ 16:00 LST), Beck et al. (2002) [16], burning conditions as in Prometheus, Tymstra et al. (2010) [10]. 10-20 h chosen on calibration fires (§4.4) |
| Fire growth, spatial fuel | `spread/cellular.py`: level set advected with the Huygens velocity of each cell's FBP ellipse (second-order ENO upwind) | Richards (1990) [8]; Lautenberger (2013) [9] (ELMFIRE approach) |
| Fire growth, uniform fuel | `spread/huygens.py`: Huygens wavelets, convex hull, 5 min step, 1 m start | Richards (1990) [8] |
| Active / inactive perimeter edges | `spread/cellular.py` (`active_edges`) | FireSim method; harness proxy for an RPAS thermal flight (`docs/validation.md`) |
| Flame length | Byram (1959) for CFB < 0.1; Thomas (1963) for CFB ≥ 0.1 | As recommended by Alexander & Cruz (2012) [15]; Byram 1959 and Thomas 1963 cited via code, not checked here |
| Spotting maximum distance | `spread/albini.py`: torching-tree and wind-driven surface-fire models | Albini (1979, 1981, 1983) [11-13], Chase (1981, 1984) [14a,b], Morris (1987) [14c] |
| Spotting emission, probability, landing | `spread/spotting.py` | **Heuristic** (no source gives these; illustrative only) |
| Ensemble | `spread/ensemble.py`: perturbed wind direction (systematic per member), wind speed, FFMC, DMC/DC, curing, FMC, ROS multiplier → P10/P50/P90 arrival, burn probability | Wind direction as dominant error: Fox-Hughes et al. (2024) [23], Bennett et al. (2026) [20]; ROS error band: Cruz & Alexander (2013) [22]. **Sigmas are placeholders until calibrated** |
| Burn probability (simple) | `spread/montecarlo.py`: ignition ±100 m, wind ±10 %, RH ±5 % | Method only; not a validated or Burn-P3-style product |
| Building exposure | `exposure.py`: distance bands (≤10, 10-30, 30-100, 100-500 m); solid-flame radiant flux (1200 K, 117.6 kW/m² and 200 kW/m² scenarios); flux-time criterion FTP = ∫(q − 13.1)^1.828 dt ≥ 11,501 | Cohen (2004) [18] (SIAM, eqs 2-4 after Tran et al. 1992, not checked); Cohen (2000) [18b]; NRC (2021) [19] p.27 bands and flux ranges. Exposure, **not** ignition probability |
| HFI classes 1-6 | `frontend/src/utils/fireClasses.ts` | Cole & Alexander (1995) [21] meanings (C-2, level ground; class 6 = their "explosive" upper class 5); limits from CWFIS GeoServer `public:hfi` legend (read 2026-10-06) |
| FWI classes | `fwi/classes.py`, `frontend/src/utils/fwiClass.ts` | CWFIS national FWI map intervals (GeoServer `public:fwi` legend) |
| Building units for structure spread | `structures/units.py`: one node per Microsoft footprint clipped to the run area (`BuildingIndex.building_geoms_in_bbox`); envelope candidates + exact shapely distances; not rasterised onto the fuel grid | Single-unit treatment: Qin et al. (2026) FSJ 104686 §3.4, §4, §6, pp.4, 7-9 (grid convergence needs Δx ≤ half the building size and spacing); square-equivalent size after Hamada's square plans (Himoto & Tanaka 2008, p.25) — applying it to irregular footprints is a FireSim heuristic |
| Validation metrics | `validation/metrics.py`: precision, recall, F1 (Dice), IoU, normalised area difference, Hausdorff, forward spread distance and bearing; ±35 % band | Bennett et al. (2026) [20] protocol; Fox-Hughes et al. (2024) [23]; Cruz & Alexander (2013) [22] |
| Observed fire data | CFSDS day-of-burning rasters + daily summaries | Barber et al. (2024) [24] |

**Deliberate deviations and heuristics**
- FFMC moisture coefficient 147.2 (ST-X-3 eq 46); cffdrs uses 147.27723 (ISI changes ≤ 1e-3). The cffdrs fixture is generated with 147.2.
- Spotting: emission, probability, landing below the maximum, and per-fuel stand sizes (`STAND_DEFAULTS`) are assumptions; terrain correction not implemented; active crown fires use the torching model with several trees (underestimate). The active crown fire model of Albini, Alexander & Cruz (2012) is **not** implemented.
- Ensemble perturbation sizes are placeholders until calibrated on observed fires and ECCC forecast-error statistics.
- Burning period is a fixed clock window (no overnight runs, ROS × 0 outside it).
- DMC and DC are held fixed within a run.
- Building exposure: blackbody 1200 K, emissivity 1 even for thin surface flames; no embers, no convective heating, no burning buildings or yard fuels.
- Validation harness: CFSDS DOB days are interpolated from satellite passes (hours of timing uncertainty); one ERA5 weather point per fire; 2014b national fuel grid without burn-scar succession; D-1/M-1 → D-2/M-2 between day-of-year 150 and 258 (assumption for Alberta boreal); M-1/M-2 at 50 % conifer; O-1 curing 90 % / 60 %.
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
7. Alexander, M.E. (2010). Cited by cffdrs for the D-2 BUI < 80 rule. **Full citation unverified** (not given in the repo).
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
   18c. Tran, H.C. et al. (1992). Wood ignition with radiant heat. In *Fire and Flammability of Furnishings and Contents of Buildings*, ASTM STP 1233. (Via Cohen 2004; unverified.)
   18d. Westhaver, A. (2017). Fort McMurray home ignition study, ICLR (cited in `docs/building-exposure.md` for 60-90 s flame residence; title not re-checked).
19. National Research Council Canada (2021). *National Guide for Wildland-Urban Interface Fires.*
20. Bennett, L., Jain, P., Moore, B., Boisvert, J. (2026). Assessment of fire spread predictions from the Wildfire Intelligence and Simulation Engine (W.I.S.E.) using a large set of satellite-derived wildfire perimeters. *International Journal of Wildland Fire* 35(8): WF26072. doi:10.1071/WF26072.
21. Cole, F.V., Alexander, M.E. (1995). *Head fire intensity class graph for FBP System Fuel Type C-2 (Boreal Spruce).* Alaska DNR Division of Forestry, Fairbanks, and Natural Resources Canada, Canadian Forest Service, Edmonton. Poster (based on Alexander & Cole 1995, SAF Publication 95-02, pp. 185-192). Local copy: `~/dev/wildfire/references/hfi-classes/`.
22. Cruz, M.G., Alexander, M.E. (2013). Uncertainty associated with model predictions of surface and crown fire rates of spread. *Environmental Modelling & Software* 47: 16-28.
23. Fox-Hughes, P., et al. (2024). *International Journal of Wildland Fire*, doi:10.1071/WF23028 (four simulators on ten Australian fires). Full title not given in the repo (unverified).
24. Barber, Q.E., et al. (2024). The Canadian Fire Spread Dataset. *Scientific Data* 11: 764. doi:10.1038/s41597-024-03436-4.
25. Hersbach, H., et al. (2020). The ERA5 global reanalysis. *Quarterly Journal of the Royal Meteorological Society* 146: 1999-2049.
26. Beaudoin, A., et al. (2014). Mapping attributes of Canada's forests at moderate resolution through kNN and MODIS imagery. *Canadian Journal of Forest Research* 44: 521-532.
27. Byram, G.M. (1959) and Thomas, P.H. (1963): flame length relations, cited in code via Alexander & Cruz (2012); originals not checked (unverified).

**Internal reports** (owner's research, not peer reviewed; local, not in the repo):
- [R1] *FireSim EOC best practice review* (2026-10-07), `~/dev/wildfire/reports/FireSim EOC best practice review.md`: post-incident reviews (Slave Lake, Fort McMurray, Jasper, Lytton, Marshall, Camp, Lahaina), ranked programme, "avoid" list, name clash with Technosylva's FireSim.
- [R2] *Fuel type grids: Canada and global* (2026-10-07), `~/dev/wildfire/reports/Fuel type grids Canada global.md`: Edmonton grid provenance, loader misplacement, OSM water mask, CFS Current-Year national layer, code 13, global options.
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

Validation data live outside the repo (`$FIRESIM_VALIDATION_DATA`, default `~/dev/wildfire/validation-data`, ~0.6 GB).

### 4.4 Validation results (summary of `docs/validation.md`, 2026-10-07)

- **Data:** 143 fire-days, 32 Alberta fires 2014-2024 (CFSDS), one burn day from the observed perimeter, Bennett et al. (2026) protocol, grid model, deterministic, no suppression.
- **Baseline (first round):** one-day F1 at the default 06-23 h window **0.15** (whole perimeter start, operational) and **0.24** with Bennett's ignition (W.I.S.E. defaults 0.26 nationally; tuned W.I.S.E. 0.54). Growth over-predicted on 93 % of days (perimeter start), normalised area difference +0.67; head bearing error median ~50°; forward spread within ±35 % on ~20 % of days. On nine shared fire-days with identical inputs: W.I.S.E. 0.19 vs FireSim 0.24.
- **Held-out (second round):** fires split by seeded hash (16 calibration / 16 test fires, 79 test fire-days), settings chosen on calibration only. Active edges (previous 2 days) + FFMC spin-up + burning period 10-20 h: test F1 **0.118 → 0.208** (ΔF1 +0.091, 95 % CI +0.056 to +0.125, bootstrap by fire); area difference +0.72 → +0.27; spread within ±35 % on 24 % of days. Oracle start 0.245. Largest wind-driven runs (Horse River 4-5 May 2016) under-predicted further.
- **Not yet measured:** ensemble skill, RPAS-corrected mid-day restarts, real thermal-flight active edges.

## 5. Testing

| Suite | Command | What it checks |
|---|---|---|
| Engine (pytest) | `PYTHONPATH=engine/src:api/src python -m pytest engine/tests -q` | FBP vs cffdrs fixture, FWI + hourly FFMC, FBP ellipse agreement, WISE reference, level set, deployment data (head/flank/back), active edges, diurnal, ensemble, Albini worked examples, Cohen exposure, loaders/reprojection, national code schemes, validation metrics |
| API (pytest) | `PYTHONPATH=engine/src:api/src python -m pytest api/tests -q` | Endpoints, WebSocket frames, grid runs, FWI, weather, skill options |
| Frontend types | `cd frontend && npx tsc --noEmit -p tsconfig.app.json` | TypeScript (the root `tsconfig.json` has `files: []` + references, so plain `npx tsc --noEmit` checks nothing; CI's `npm run build` runs `tsc -b`) |
| Frontend unit (Vitest) | `cd frontend && npm test` | Classes, contrast, time zone, evac arrival, assets, ICS outputs, skill options |
| Frontend E2E (Playwright + axe) | `cd frontend && npx playwright test` | Production build via `vite preview`; API mocked by `tests/e2e/mockApi.ts` replaying `tests/fixtures/terwillegar_grass_4h.json`; external requests blocked; axe baseline, 12 px / 24 px layout rules, idle-redraw performance budget |
| Validation (not CI) | `scripts/validate.py prepare / run / report / compare` | Skill on observed fires (§4.4) |

**Oracle / reference provenance**
- **cffdrs fixture** `engine/tests/fbp/data/` generated by `engine/tests/fbp/data/generate_cffdrs_reference.py` from a `cffdrs_py` checkout (`CFFDRS_PY_PATH`), FFMC coefficient 147.2; 18 fuels × 6 weather/slope cases in CI, 12,960 cases in a development run; hourly FFMC 5,760 cases (max diff 3e-11).
- **WISE reference** `engine/tests/spread/data/wise_reference.json`: WISE 1.0.6-beta.6 (official Ubuntu build, headless) on uniform C-2 / O-1a, wind 0/20/30 km/h, flat and sloped planes (built at 30.5/40.5 % because WISE truncates slope to integer percent).
- **Cohen worked values:** SIAM 50 m × 20 m flame at 1200 K → 79 / 45 / 27 kW/m² at 10 / 20 / 30 m and ~60 s at 31 kW/m² (Cohen 2004); FireSim 80.6 / 45.9 / 27.8 and 59 s.
- **Albini/Chase worked examples:** Chase 1981 torching 0.34 mi; Albini 1983 surface fire 0.45 km.
- **CFSDS validation:** Barber et al. (2024) DOB rasters; metrics unit-tested on shapes with known answers; W.I.S.E. comparison numbers from Bennett et al. (2026) Table 1 plus nine local W.I.S.E. runs.
- **Frontend fixture:** recorded from the real engine by `npm run fixture:record` (`frontend/tests/fixtures/record_fixture.py`).

**Latest results (2026-10-08, branch `fix/record-open-items` rebased on `03997fd`, local workstation, machine loaded):**

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

- [ ] **Ensemble calibration in progress:** perturbation sigmas are placeholders; calibrate on the validation harness (and ECCC forecast-error statistics); measure ensemble skill (P10/P50 reliability).
- [ ] **Real RPAS thermal perimeters untested:** active edges were validated only with a CFSDS previous-day proxy; validate with real thermal flights and measure mid-day restarts.
- [ ] **Edmonton fuel grid never accuracy-assessed:** needs a stratified check (≥ 50 cells per class, confusion matrix, Wilson intervals), embedded metadata, correct filename, seasonal (green-up) switching; `percent_conifer.tif` is not read [R2].
- [ ] **National fuel grid integration:** CFS Current-Year FBP layer (100 m, OGL – Canada) as the base outside Edmonton; decide the code-13 stand-in (≈ 18 % of Canada); only uniform or synthetic fuel outside Edmonton today.
- [ ] **Water layer rebuild** from authoritative hydrography (City, CanVec/NRCan); repair invalid polygons; mask only where the LiDAR grid is non-fuel.
- [ ] **Name clash:** "FireSim" is a module of Technosylva's Wildfire Analyst; rename before any release outside the City.
- [ ] Per-fuel calibration (D-2 and small days remain poor); per-cell percent conifer; current fuel grid with burn scars.
- [ ] `load_fuel_grid` treats a geographic raster's degrees as metres (noted in `docs/validation.md`; check whether the 2026-10-07 reprojection fix resolved it).
- [ ] **Grid-model point ignitions with off-axis wind (found 2026-10-08):** a point ignition in a narrow ellipse (O-1a, LB about 5) barely spreads when the wind is off the grid axes: 50 m cells, 2 h, 25 km/h, no acceleration: 43 cells burned with wind from 180°, 3 from 225° (cells on the downwind diagonal are skipped and burn late). Starting from a perimeter, 225° and 270° agree (601 vs 575 m downwind). Recorded as a strict xfail, `engine/tests/spread/test_cellular.py::test_point_ignition_grass_diagonal_wind` (`f681189`); the real-raster tests use a west wind until it is fixed. Affects grass-fire point ignitions in the UI and possibly the validation's Bennett-ignition runs.
- [ ] ICS 209-WF follow-ups: the ICS Canada 209 instructions cover the all-hazards 209; the block 9 code expansions (OC/BH/UC/O) and whether Alberta agencies file 209-WF are unverified; 30B uses a 100 m exposure band as "threatened" (a FireSim choice, not from the form); runs shorter than 12 h leave the 12-72 h horizons "not projected".
- [ ] Heterogeneous fuel, real terrain, barriers and spotting not compared with WISE, Burn-P3 or Cell2Fire.
- [ ] Unverified citations: Alexander (2010) full reference; Fox-Hughes et al. (2024) title; Byram (1959), Thomas (1963), Tran et al. (1992) originals; Class 1/6 meanings and an Alexander & De Groot (1988) citation flagged for the owner's check in the redesign notes.
- [ ] Roadmap after ensemble: time-available vs time-needed per zone, trigger buffers / ember reach (river is not a barrier), exercise/replay mode.
- [ ] **Edmonton footprints for structure spread (measured 2026-10-09, `build_units` on all 346,238):** median nearest edge-to-edge separation 2.6 m; 79 % of footprints have a neighbour within 5 m, 94 % within 10 m; 5,916 have none within 30 m; median square-equivalent size 11.6 m. The very short separations suggest garages and sheds mapped as separate footprints: check a sample against the City's building or parcel data before structure-spread results are shown. Full-city build 17 s on the workstation (pair search + distances).
- [ ] Minor UI: "Click map to set ignition point" hint lingers after a typed ignition (owner note 2026-10-08; status not re-checked).
- [ ] Critical assets (after PR #31): 2 Alberta and 2 ODHF care sites could not be positioned; group homes and sites under 10 units excluded by design; OSM-only care sites flagged "verify".

Closed 2026-10-08: ICS Canada 209 (`d34b349`, `8f349e6`); skipped real-raster tests (`af052e4`); spotting
repeatability (`f681189`); `CLAUDE.md` water and building sources (`45e68d2`); roads
extraction script and `secondary` roads, ODHF care records without coordinates (`2357a02`,
PR #31); EOC manual point, left out of the layer (PR #33, `8da78f5`, merged `dd523f6`). See the decisions log (§3).

## 7. Work log

Generated from git: `git log --since=2026-10-01 --format="| %ad | \`%h\` | %s |" --date=short`.
No bot commits in this range. Branch-sync merges ("Merge branch 'master' into …", "Merge
(origin/)master into …") are omitted; PR merges are kept. 119 earlier commits (2026-02-12 to
2026-09-30, incl. `TRA-XXX` task history) are not listed.

| Date | Commit | Summary |
|---|---|---|
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
