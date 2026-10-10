# FireSim V3 — Claude Code Context

## What This Is
Canadian FBP wildfire simulation system — fire behaviour prediction engine, FastAPI backend, React frontend.
For municipal EOC planning, preparedness and training in Edmonton's WUI. **Not yet validated
against observed fires** — see `docs/model-card.md` (intended use, evidence, limits) and keep it
current with any engine change.

**Standards:** CFFDRS/FBP System (ST-X-3, Forestry Canada 1992), Van Wagner & Pickett (1985) FWI, Van Wagner (1977) crown fire.

---

## Architecture

```
engine/          Pure Python FBP engine (no web deps)
api/             FastAPI service — wraps engine, WebSocket streaming
frontend/        React + TypeScript + MapLibre GL — Vite build
docker-compose.yml  api:8000 + frontend:3000 (dev)
Makefile         All key commands
```

**Data flow:** `frontend` → HTTP/WebSocket → `api` → `engine` → frames streamed back

---

## Key Commands

```bash
make install        # Install all deps (engine + api + frontend)
make test           # Run all tests
make test-engine    # Engine only
make test-api       # API only
make test-cov       # Coverage report
make lint           # TypeScript type-check
make dev-api        # FastAPI at :8000 with auto-reload
make dev-frontend   # Vite at :3000
make build          # Production frontend bundle
make clean          # Remove build artifacts

# Docker
docker compose up --build
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs
```

**PYTHONPATH for manual runs:** `PYTHONPATH=engine/src:api/src`

---

## Engine (`engine/src/firesim/`)

### Modules
| Module | Purpose |
|--------|---------|
| `types.py` | Core dataclasses: `SimulationConfig`, `SimulationFrame`, `FBPResult`, `FWIResult`, `WeatherInput`, `FireType` enum |
| `fbp/constants.py` | All 18 FBP fuel types from ST-X-3 / Wotton 2009 (`FuelTypeSpec`: a/b/c ROS params, q, bui0, default cbh, cfl) |
| `fbp/calculator.py` | FBP equations matching cffdrs: `calculate_fbp()` (head/flank/back ROS, SFC, CFB, HFI, WSV/RAZ slope adjustment), ISI with eq 53a wind cap, curing (Wotton 2009), FMC from date |
| `fbp/crown_fire.py` | CSI, RSO, CFB (ST-X-3 eqs 56-58), C-6 crown ROS, `FireType` classification |
| `fwi/calculator.py` | Van Wagner & Pickett (1985): `FWICalculator` with `calculate()` → `FWIResult` |
| `spread/huygens.py` | Huygens wavelet model (uniform fuel, convex front); `FuelGrid` holds fuel types plus optional per-cell `cbh`/`cfl` (e.g. LiDAR); `fbp_for_conditions` is the FBP layer both models share |
| `spread/cellular.py` | Level-set grid model — used whenever a fuel grid is present (FBP-ellipse Huygens velocity, ELMFIRE-style); per-cell arrival, front speed, head/flank/back (`spread_directions`, `fire_part`), head summary, flame panels for exposure |
| `spread/ellipse.py` | LB ratio (forest + grass), flank ROS, ROS toward theta on the FBP ellipse |
| `spread/slope.py` | ST-X-3 eq 39 slope factor (slope itself is applied via net effective wind in the FBP calculator) |
| `spread/spotting.py`, `spread/albini.py` | Ember spotting (opt-in): Albini/Chase/Morris maximum distance (surface-fire or torching-tree model); emission, probability and landing are heuristic (illustrative) |
| `exposure.py` | Building exposure: distance bands, Cohen (2004) radiant flux and flux-time index from the grid run's flame panels (exposure, not ignition; `docs/building-exposure.md`) |
| `structures/` | Structure spread (opt-in, API `structure_spread`, labelled "illustrative — not validated in Canada"): `units.py` one unit per building footprint (centroid, area, size, neighbour graph within a cutoff); `hamada.py` Hamada rates; `spread.py` front contact + building-to-building spread, units built only where the spread can reach (memory guard 60,000 units → "not computed"), involved-unit detail on the final frame; `embers.py` opt-in ember ignition (API `structure_embers`, design fire 150/400, mechanism `ember`; spec §6.1); `docs/structure-spread-spec.md` |
| `spread/diurnal.py` | Opt-in burning period (`SimulationConfig.burning_period` + `start_hour`; grid and Huygens; a point ignition outside it waits for it); hourly FFMC spin-up = hourly records with negative `hours_from_start`, from 17:00 local (`hourly_for_run`) |
| `spread/simulator.py` | `Simulator` class — main orchestrator, yields `SimulationFrame` per snapshot |
| `spread/montecarlo.py` | Burn probability (jitter ignition, wind speed, RH over N iterations) |
| `fwi/classes.py` | FWI display classes (CWFIS FWI map intervals) |
| `data/fuel_loader.py` | GeoTIFF → `FuelGrid`; integer codes mapped by code scheme (`CODE_SCHEMES`, explicit `code_scheme` or detection) |
| `data/dem_loader.py` | DEM GeoTIFF → slope % + aspect ° → `TerrainGrid` |
| `data/wui_loader.py` | WUI GeoJSON → `SpreadModifierGrid` |
| `data/synthetic_grid.py` | Demo landscape generator (no data files needed) |

### FBP Fuel Types (18 canonical)
```
C1 Spruce-Lichen Woodland    C5 Red and White Pine       M3 Dead BF Mixedwood-Leafless
C2 Boreal Spruce (default)   C6 Conifer Plantation       M4 Dead BF Mixedwood-Green
C3 Mature JP/LP Pine         C7 Ponderosa Pine/DF        O1a Matted Grass
C4 Immature JP/LP Pine       D1 Leafless Aspen           O1b Standing Grass
                             D2 Green Aspen              S1 Jack Pine Slash
M1 Boreal Mixedwood-Leafless M2 Boreal Mixedwood-Green   S2 Spruce-Pine Slash
                                                         S3 Spruce Slash
```

### FBP ROS equation
`ros = a × (1 - e^(-b × ISI))^c` with BUI effect `BE = exp(50 × ln(q) × (1/BUI - 1/BUI₀))`

The FBP layer is verified against cffdrs (Python) to floating-point precision:
`engine/tests/fbp/test_cffdrs_reference.py` (fixture regenerated by
`engine/tests/fbp/data/generate_cffdrs_reference.py`). FFMC coefficient is 147.2 (ST-X-3 eq 46).

### FWI Classes
`Low 0–5 | Moderate 6–15 | High 16–22 | Very High 23–29 | Extreme 30+` — CWFIS national FWI map intervals, one table for engine/API/UI (`engine/src/firesim/fwi/classes.py`, `frontend/src/utils/fwiClass.ts`). An FWI map class, not an official danger rating.

### Fire Types (`FireType` enum)
`SURFACE` → `SURFACE_WITH_TORCHING` → `PASSIVE_CROWN` → `ACTIVE_CROWN`

---

## API (`api/src/firesim_api/`)

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Status + uptime |
| GET | `/api/v1/version` | Version + deployed git SHA (stamp outputs with it) |
| POST | `/api/v1/simulations` | Start simulation → `SimulationResponse` |
| GET | `/api/v1/simulations/{id}` | Fetch stored results |
| WS | `/api/v1/simulations/ws/{id}` | Stream frames real-time (JSON) |
| GET | `/api/v1/simulations/{id}/arrival` | Arrival-minute raster of a finished grid run |
| GET | `/api/v1/simulations/fuel-grid-image` | Fuel overlay PNG + legend |
| POST | `/api/v1/simulations/multiday` | Multi-day FWI carry-over |
| POST | `/api/v1/simulations/perimeter-override` | Mid-incident RPAS perimeter correction |
| POST | `/api/v1/simulations/burn-probability` | Monte Carlo burn grid (2D float [0,1]) |
| POST | `/api/v1/fwi/calculate` | Single observation → FWI codes + danger rating |
| POST | `/api/v1/fwi/multi-day` | Chain FWI across daily observations |
| GET | `/api/v1/weather/current?lat=&lng=` | Live CWFIS WFS → `CurrentWeather` |

### Frames
WebSocket events are `{"type": "simulation.frame", "simulation_id": ..., "frame": {...}}`, then
`simulation.completed` or `simulation.error`. Frame fields (`time_hours`, `perimeter`, `area_ha`,
`head_ros_m_min`, `max_hfi_kw_m`, `fire_type`, `flame_length_m`, `burned_cells`, `cells_offset`,
`head`, `building_exposure`, ...) are documented in `docs/api-reference.md` — keep that file, the
Pydantic schema and `frontend/src/types/simulation.ts` in step. Requests can set `start_time`,
`hourly_weather` and `cells_mode: "incremental"` (the frontend uses incremental), and the
spread-skill options `burning_period` / `ffmc_spin_up` (API default off, UI default on; hours
on `start_time`'s own clock), on perimeter-override, `active_edges`, and the opt-in
`structure_spread` (Hamada building-to-building counts, frame `structure_spread`; involved units on the final
frame `structure_spread_detail`, map display only, never exported; illustrative).

### Environment Variables
```
FIRESIM_FUEL_GRID_PATH    GeoTIFF with FBP fuel type codes
FIRESIM_DEM_PATH          DEM GeoTIFF for slope/aspect
FIRESIM_WATER_PATH        Water bodies GeoJSON (non-fuel mask)
FIRESIM_BUILDINGS_PATH    Building footprints GeoJSON
FIRESIM_NEIGHBOURHOODS_PATH  Neighbourhood polygons (building index; needed for building masks/exposure)
FIRESIM_GIT_SHA           Set by the Docker build (CI passes the commit); served by /api/v1/version
```

---

## Frontend (`frontend/src/`)

### Stack
`React 19 + TypeScript 5.9 + Vite 7 + MapLibre GL 5`

### Layout
Top bar (incident, status, limits badge, America/Edmonton clock) · Setup column (sections with
summaries, sticky Run bar) · map (`MapView.tsx`) · Situation panel (`FireMetrics.tsx`, exposure,
Neighbourhoods card `EvacStatusPanel.tsx`, `EOCSummary.tsx`) · clock-time timeline
(`TimeSlider.tsx`). Neighbourhoods: modelled fire arrival within 500 m and evacuation status set by
Planning (`utils/evacZones.ts`); FireSim never suggests Order/Alert/Watch. Critical assets card
`CriticalAssetsPanel.tsx` (`utils/assets.ts`): arrival within 500 m / inside for each asset and
first reach per major road, from the bundled `public/edmonton/assets.geojson`
(`scripts/build_edmonton_assets.py`; roads `scripts/build_edmonton_roads.py`; sources in `docs/data-sources.md`) or a user layer. EOC console tab: `EOCConsole.tsx`
(ICS forms `utils/icsForms.ts`, ICS Canada 209-WF `utils/ics209.ts`, `docs/ics-canada-209.md`). Shared tables: `utils/fireClasses.ts`
(HFI classes, Cole & Alexander 1995), `utils/fwiClass.ts`, `utils/time.ts`,
`utils/suppressionAdvisory.ts`. Design tokens: `src/styles/tokens.css` (dark default).
Spread-skill options: Setup → Run options "Diurnal burning" (burning period, evening FFMC
spin-up; `utils/skillOptions.ts`), timeline shading outside the burning period; RPAS restart
(`PerimeterOverridePanel.tsx`, state in `hooks/useRecon.ts`, `utils/activeEdges.ts`): observed
perimeter, active edges drawn on the map or picked by side, drawn by MapView (`recon` prop).
House-to-house spread (opt-in, Setup → Fuel & landscape): Situation card `StructureSpreadPanel.tsx`
(counts, chart, map toggle), MapView `structureUnits` layer, caveat in `InfoTip.tsx`
(`utils/structureSpread.ts`); minimal visible text, "Illustrative" badge.

### Services / Hooks
- `src/services/api.ts` — All API calls + WebSocket URL builder
- `src/hooks/useSimulation.ts` — WebSocket state machine (pending→running→completed)
- `src/hooks/useScenarios.ts` — LocalStorage scenario persistence

### Environment Variables
```
VITE_API_URL=             Base URL (default "" = relative paths)
VITE_MAPBOX_TOKEN=        Satellite tiles (optional, defaults to OSM)
```

---

## CI/CD (`.github/workflows/ci.yml`)

- **Triggers:** push to `master`; PRs to `master` and `feat/**`
- **Required checks on master:** `engine-tests (3.11)`, `engine-tests (3.12)`, `frontend`; auto-merge
  is enabled (`gh pr merge N --merge --auto`) — still ask the user before merging
- **deploy** (master only): `flyctl deploy --remote-only --build-arg GIT_SHA=…`; Vercel rebuilds the frontend
- **engine-tests:** Python 3.11 + 3.12 → `pytest engine/tests/ api/tests/`
- **frontend:** Node 22 → `tsc --noEmit` + `npm run build` + `npm test` (Vitest)
- **frontend-e2e** (not required): Playwright + axe against `vite preview` with a mocked API replaying `frontend/tests/fixtures/terwillegar_grass_4h.json` (see frontend/README.md)
- **Rule:** Every test file must contain at least one `def test_` function

---

## Commits

Conventional commits: `feat(scope): …`, `fix(scope): …`, `docs: …`, `test: …`, `chore: …`.
Scopes: `engine`, `api`, `frontend`, or combinations (`engine+api`). (Older history uses `TRA-XXX` task IDs.)

## Data Files

The engine works without real data using `synthetic_grid.py` (generates a mixed-fuel demo landscape). For real Edmonton scenarios:

| File | Source | Used by |
|------|--------|---------|
| `Edmonton_FBP_FuelLayer_*_10m.tif` | City of Edmonton canopy-LiDAR fuel product (20 m grid in EPSG:3776 despite the name; codes 2, 12, 14, 31, 32, 99, with 0 = no data) | `fuel_loader.py` |
| `edmonton_dem.tif` | 30 m DEM, UTM 12N (EPSG:26912), file dated 2011; recorded as Open Government Canada, exact product not recorded (likely NRCan CDEM; unverified) | `dem_loader.py` |
| `edmonton_water_bodies.geojson.gz` | OpenStreetMap (ODbL; features carry `osm_id`), 2,275 polygons. **Off by default** (2026-10-07): it covers about 3,700 ha the LiDAR grid maps as vegetation, and the LiDAR grid already maps water as non-fuel (`docs/verification.md`) | `fuel_loader.py` (`water_path` mask) |
| `edmonton_buildings.geojson.gz` | Microsoft Canadian Building Footprints (ODbL), 346,238 footprints, generated 2025-11-16 per the file metadata. The `type`/`height`/`material`/`roof_type` attributes have no documented source; exposure uses only the footprint geometry | `building_index.py`: mask, inside-perimeter counts, exposure |
| `edmonton_neighbourhoods.geojson` | City of Edmonton Open Data, Neighbourhoods `65fr-66s6` (OGL – City of Edmonton), 407 polygons | building index, neighbourhood card |
| `wui_zones.geojson.gz` | Custom, **no documented source** — off by default; don't present results using it as measured | `wui_loader.py` |

---

## City of Edmonton Notes

- **No Claude/Anthropic references** in code committed to CoE systems
- Related CoE project: `~/dev/wildfire/edmonton-burnp3/` (dual remote: GitHub + `git.edmonton.ca`)
- WUI data lives in `~/Documents/City-of-Edmonton/Wildfire-WUI/`
- Never put SFOC numbers or SFOC condition IDs in this public repo

---

## GitHub

- **Repo:** `Tphambolio/wildfire-simulator` (renamed from `wildfire-simulator-v3`)
- **Branch:** `master`
- **CI:** GitHub Actions required to pass before merge
- **Vercel:** `frontend/` connected for preview deployments
