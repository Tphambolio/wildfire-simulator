# Deployment

## Production

| Part | Where | How it deploys |
|---|---|---|
| API | Fly.io app `firesim-api` (region yyz): https://firesim-api.fly.dev | GitHub Actions `deploy` job on every push to `master`: `flyctl deploy --remote-only --build-arg GIT_SHA=<commit>` (needs the `FLY_API_TOKEN` secret). Image: `api/Dockerfile`, which bundles `data/` at `/app/data` and downloads the Alberta Wildfire fuel grid COG (31.8 MB, SHA-256 checked) from the GitHub release `data-ab-fbp-2025` (`docs/fuel-grid-alberta.md` §6; the build continues without it). |
| Frontend | Vercel: https://wildfire-simulator.vercel.app | Vercel builds `frontend/` from `master`; pull requests get preview deployments (behind Vercel login). |

`GET /api/v1/version` returns the deployed commit (from the `GIT_SHA` build argument), so any
output can be traced to the exact model version.

Master is protected: the checks `engine-tests (3.11)`, `engine-tests (3.12)` and `frontend` must
pass; `frontend-e2e` (Playwright) runs but is not required. Auto-merge is enabled.

## Development

```bash
# Terminal 1: API (port 8000; use another port if 8000 is taken and set VITE_API_URL)
PYTHONPATH=engine/src:api/src uvicorn firesim_api.main:app --port 8000 --reload

# Terminal 2: frontend
cd frontend && npm install && npm run dev      # http://localhost:3000, proxies /api to :8000
```

The frontend sends the Edmonton data paths as they exist in the production image
(`/app/data/...`). For local runs with the Edmonton grid either run the API in Docker, or
point the requests at the local `data/` directory (the Playwright e2e tests and the
`frontend/tests/fixtures/record_fixture.py` script show how).

## Docker Compose

```bash
docker compose up --build
# Frontend http://localhost:3000 (nginx serving the Vite build, proxying /api incl. WebSocket)
# API      http://localhost:8000/api/v1/health, OpenAPI at /docs
```

## Environment variables

### API

| Variable | Purpose |
|---|---|
| `FIRESIM_FUEL_GRID_PATH` | Default FBP fuel GeoTIFF (used by `use_ca_mode` runs without a path) |
| `FIRESIM_WATER_PATH` | Default water bodies GeoJSON (non-fuel mask) |
| `FIRESIM_BUILDINGS_PATH` | Building footprints GeoJSON(.gz): non-fuel mask, inside-perimeter counts, exposure |
| `FIRESIM_NEIGHBOURHOODS_PATH` | Neighbourhood polygons for the building index (needed with buildings) |
| `FIRESIM_DEM_PATH` | Default DEM GeoTIFF (slope and aspect) |
| `FIRESIM_AB_FUEL_GRID_PATH` | Alberta Wildfire FBP fuel grid COG: the default fuel outside the City grid (set by `api/Dockerfile`; unset or missing file = City grid only) |
| `FIRESIM_CITY_FUEL_GRID_PATH` | The City grid path that triggers the provincial default (default `/app/data/Edmonton_FBP_FuelLayer_20251105_10m.tif`) |
| `FIRESIM_NATIONAL_FUEL_GRID_PATH`, `FIRESIM_NATIONAL_FUEL_SCHEME` | Optional national FBP grid outside Alberta (`cfs_national` or `cfs_national_2014`); none bundled |
| `FIRESIM_PROVINCIAL_WINDOW_KM`, `FIRESIM_CITY_MARGIN_KM` | Window side for runs outside the City grid (40) and the margin inside the City grid's box (5) |
| `FIRESIM_GIT_SHA` | Set by the Docker build from `GIT_SHA`; served by `/api/v1/version` |

All are optional; without data files the engine runs on a uniform fuel type or a synthetic demo
landscape.

### Frontend

| Variable | Purpose |
|---|---|
| `VITE_API_URL` | API base URL (empty: Vite proxy in development, relative paths in production) |
| `VITE_MAPBOX_TOKEN` | Optional satellite basemap; OSM and topo work without it |

## Requirements

- Python 3.10+ (CI tests 3.11 and 3.12): `pip install -e engine/ -e api/`, or use `PYTHONPATH`.
- Node.js 22 (Vitest requires Node 22.12 or later).

## Tests

```bash
make test                          # engine and API (pytest)
cd frontend && npx tsc --noEmit && npm test && npx playwright test
```

Frontend test details, the recorded fixture and how to regenerate it: `frontend/README.md`.
