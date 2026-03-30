# FireSim V3 — Claude Code Context

## What This Is
Canadian FBP wildfire simulation system — fire behaviour prediction engine, FastAPI backend, React frontend.
Used for municipal wildfire risk assessment and EOC planning in Edmonton's WUI.

**Standards:** CFFDRS/FBP System (ST-X-3, Forestry Canada 1992), Van Wagner & Pickett (1985) FWI, Van Wagner (1977) crown fire.

---

## Architecture

```
engine/          Pure Python FBP engine (no web deps) — 434 tests
api/             FastAPI service — wraps engine, WebSocket streaming — 67 tests
frontend/        React + TypeScript + MapLibre GL — Vite build
docker-compose.yml  api:8000 + frontend:3000 (dev)
Makefile         All key commands
```

**Data flow:** `frontend` → HTTP/WebSocket → `api` → `engine` → frames streamed back

---

## Key Commands

```bash
make install        # Install all deps (engine + api + frontend)
make test           # Run all 501 tests
make test-engine    # Engine only (434 tests)
make test-api       # API only (67 tests)
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
| `fbp/constants.py` | All 18 FBP fuel types from ST-X-3 (`FuelTypeSpec`: a/b/c ROS params, cbh, cfl, sfc, cbd, q, bui0) |
| `fbp/calculator.py` | FBP equations: `calculate_fbp()`, `calculate_isi()`, `calculate_bui()`, `calculate_surface_ros()` |
| `fbp/crown_fire.py` | Van Wagner (1977) CFB, `FireType` classification (SURFACE → ACTIVE_CROWN) |
| `fwi/calculator.py` | Van Wagner & Pickett (1985): `FWICalculator` with `calculate()` → `FWIResult` |
| `spread/huygens.py` | **Primary spread algorithm** — elliptical Huygens wavelet, smooth perimeters |
| `spread/cellular.py` | CA mode for WUI/urban (discrete cells, use `use_ca_mode=True`) |
| `spread/ellipse.py` | LB ratio, back/flank ROS from head ROS |
| `spread/slope.py` | Butler (2007) cap, Anderson (1983) directional slope multiplier |
| `spread/spotting.py` | Albini (1979) ember spotting — crown fire → spot distance + probability |
| `spread/simulator.py` | `Simulator` class — main orchestrator, yields `SimulationFrame` per snapshot |
| `spread/montecarlo.py` | Stochastic burn probability (jitter wind/RH over N iterations) |
| `data/fuel_loader.py` | GeoTIFF → `FuelGrid` (FBP type codes) |
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

### FWI Danger Ratings
`Low <10 | Moderate 10–19 | High 19–30 | Very High 30+`

### Fire Types (`FireType` enum)
`SURFACE` → `SURFACE_WITH_TORCHING` → `PASSIVE_CROWN` → `ACTIVE_CROWN`

---

## API (`api/src/firesim_api/`)

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Status + uptime + engine version |
| POST | `/api/v1/simulations` | Start simulation → `SimulationResponse` |
| GET | `/api/v1/simulations/{id}` | Fetch stored results |
| WS | `/api/v1/simulations/ws/{id}` | Stream frames real-time (JSON) |
| POST | `/api/v1/simulations/multiday` | Multi-day FWI carry-over |
| POST | `/api/v1/simulations/perimeter-override` | Mid-incident RPAS perimeter correction |
| POST | `/api/v1/simulations/burn-probability` | Monte Carlo burn grid (2D float [0,1]) |
| POST | `/api/v1/fwi/calculate` | Single observation → FWI codes + danger rating |
| POST | `/api/v1/fwi/multi-day` | Chain FWI across daily observations |
| GET | `/api/v1/weather/current?lat=&lng=` | Live CWFIS WFS → `CurrentWeather` |

### WebSocket Frame Format
```json
{"type": "simulation.frame", "data": {
  "time_hours": 2.0,
  "perimeter": [[lat, lng], ...],
  "area_ha": 45.2,
  "head_ros": 12.5,
  "max_hfi": 3400,
  "fire_type": "PASSIVE_CROWN",
  "flame_length": 8.3,
  "fuel_breakdown": {"C2": 0.6, "D1": 0.4},
  "spot_fires": [[lat, lng], ...],
  "burned_cells": 1847,
  "num_fronts": 3
}}
```

### Environment Variables
```
FIRESIM_FUEL_GRID_PATH    GeoTIFF with FBP fuel type codes
FIRESIM_DEM_PATH          DEM GeoTIFF for slope/aspect
FIRESIM_WATER_PATH        Water bodies GeoJSON (non-fuel mask)
FIRESIM_BUILDINGS_PATH    Building footprints GeoJSON
```

---

## Frontend (`frontend/src/`)

### Stack
`React 19 + TypeScript 5.9 + Vite 7 + MapLibre GL 5`

### Key Components
| Component | Purpose |
|-----------|---------|
| `MapView.tsx` | MapLibre GL — click-to-ignite, perimeter rendering, layer toggles |
| `WeatherPanel.tsx` | Wind/temp/RH/precip/FWI inputs |
| `FireMetrics.tsx` | Area, ROS, HFI, fire type, flame length KPIs |
| `TimeSlider.tsx` | Frame scrubbing |
| `MultiDayPanel.tsx` | Multi-day weather progression |
| `PerimeterOverridePanel.tsx` | RPAS mid-incident correction |
| `EvacZonesPanel.tsx` | Evacuation trigger visualization |
| `EOCSummary.tsx` | ICS-209 printable situation report |
| `IsochronePanel.tsx` | Fire arrival time contours |
| `ScenarioPanel.tsx` | LocalStorage scenario save/load |

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

- **Triggers:** Push to `master`, all PRs to `master`
- **engine-tests:** Python 3.11 + 3.12 → `pytest engine/tests/ api/tests/`
- **frontend:** Node 20 → `tsc --noEmit` + `npm run build`
- **Rule:** Every test file must contain at least one `def test_` function

---

## Task Naming Convention

Commits use `TRA-XXX` format:
```
feat(scope): description (TRA-213)
fix(scope): description (TRA-XXX)
chore: description
```
Scopes: `engine`, `api`, `frontend`, `engine+api`, `api+frontend`, `engine+api+frontend`

Latest task: **TRA-213** (CWFIS live fire weather via WFS endpoint)

---

## Data Files

The engine works without real data using `synthetic_grid.py` (generates a mixed-fuel demo landscape). For real Edmonton scenarios:

| File | Source | Used by |
|------|--------|---------|
| `Edmonton_FBP_FuelLayer_*_10m.tif` | City of Edmonton / ABMI | `fuel_loader.py` |
| `edmonton_DEM_*.tif` | Open Government Canada | `dem_loader.py` |
| `edmonton_water_bodies.geojson` | City Open Data | `wui_loader.py` |
| `edmonton_buildings.geojson` | City Open Data | settings |
| `Edmonton_WUI_zones.geojson` | Custom (from `edmonton-burnp3` project) | `wui_loader.py` |

---

## City of Edmonton Notes

- **No Claude/Anthropic references** in code committed to CoE systems
- Related CoE project: `~/dev/wildfire/edmonton-burnp3/` (dual remote: GitHub + `git.edmonton.ca`)
- WUI data lives in `~/Documents/City-of-Edmonton/Wildfire-WUI/`

---

## GitHub

- **Repo:** `Tphambolio/wildfire-simulator` (renamed from `wildfire-simulator-v3`)
- **Branch:** `master`
- **CI:** GitHub Actions required to pass before merge
- **Vercel:** `frontend/` connected for preview deployments
