# Wildfire Simulator V3

Planning, training and what-if tool for Canadian FBP fire spread simulation. The FBP
equations are verified against the CFS `cffdrs` reference implementation; spread has not been
validated against observed fires. See [docs/verification.md](docs/verification.md).

## What this does

Simulates wildfire spread using the Canadian Forest Fire Behavior Prediction (FBP) System:
- All 18 FBP fuel types (ST-X-3 with GLC-X-10 revisions), matching `cffdrs` to float precision
- Huygens wavelet spread for uniform fuel; level-set spread on spatial fuel grids
  (wraps around water, roads and buildings)
- Point-ignition acceleration, slope via net effective wind, crown fire (eqs 56-58, C-6 crown rate)
- Foliar moisture from location and date; grass curing, percent conifer / dead fir inputs
- Optional per-cell crown base height and crown fuel load (e.g. from drone LiDAR)
- FWI System (FFMC, DMC, DC, ISI, BUI, FWI), live CWFIS weather
- Hourly weather streams (or the Open-Meteo hourly forecast) with hourly FFMC
- Ember spotting (opt-in): Albini/Chase maximum distances; emission and landing heuristic
- Monte Carlo burn probability
- Spatial fuel/water/buildings/WUI-zone grids
- Interactive map with click-to-ignite, real-time streaming, pause/resume/cancel

## Stack

| Layer | Technology |
|-------|-----------|
| Fire engine | Python 3.10+ with Numba JIT |
| API | FastAPI + WebSocket |
| Frontend | React + Vite + TypeScript + MapLibre GL |
| Deploy | Docker Compose |

## Quick start

```bash
# Start the API backend
PYTHONPATH=engine/src:api/src uvicorn firesim_api.main:app --port 8000

# In a second terminal, start the frontend
cd frontend && npm install && npm run dev
```

Then open http://localhost:3000, click the map to set an ignition point, adjust weather, and run a simulation.

## Docker

```bash
docker compose up --build
# Frontend: http://localhost:3000
# API: http://localhost:8000/api/v1/health
```

## Testing

```bash
make test           # All tests (about 660)
make test-engine    # Engine tests, incl. cffdrs reference and FBP-ellipse agreement
make test-api       # API integration tests
```

Frontend unit tests (Vitest) and end-to-end tests (Playwright with a mocked API): see
[frontend/README.md](frontend/README.md#testing).

## Project structure

```
engine/     Pure Python fire science (zero web deps)
api/        FastAPI backend with WebSocket streaming
frontend/   React + Vite + TypeScript + MapLibre GL
```

## API

```
POST /api/v1/simulations          Start a simulation
GET  /api/v1/simulations/{id}     Get status and results
WS   /api/v1/simulations/ws/{id}  Stream frames in real-time
GET  /api/v1/health               Health check

POST /api/v1/fwi/calculate        Compute FWI from noon weather observation
POST /api/v1/fwi/multi-day        Chain FWI across daily observations

GET  /api/v1/weather              Live FWI indices for a location (CWFIS)
```

## Documentation

- [docs/fbp-reference.md](docs/fbp-reference.md): equations, inputs, cffdrs verification
- [docs/architecture.md](docs/architecture.md): components and the two spread models
- [docs/verification.md](docs/verification.md): what is verified, history of fixes, known limits
- [docs/api-reference.md](docs/api-reference.md), [docs/deployment.md](docs/deployment.md)

## References

- Forestry Canada Fire Danger Group (1992). *Development and Structure of the Canadian Forest
  Fire Behavior Prediction System.* Information Report ST-X-3.
- Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). *Updates and revisions to the 1992
  Canadian Forest Fire Behavior Prediction System.* Information Report GLC-X-10.
- Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire. *Can. J. For. Res.* 7: 23-34.
- Van Wagner, C.E., Pickett, T.L. (1985). *Equations and FORTRAN program for the Canadian Forest
  Fire Weather Index System.* Forestry Technical Report 33.
- Richards, G.D. (1990). An elliptical growth model of forest fire fronts and its numerical
  solution. *Int. J. Numer. Meth. Eng.* 30: 1163-1179.
- Tymstra, C., Bryce, R.W., Wotton, B.M., Taylor, S.W., Armitage, O.B. (2010). *Development and
  structure of Prometheus: the Canadian Wildland Fire Growth Simulation Model.* NOR-X-417.
- Lautenberger, C. (2013). Wildland fire modeling with an Eulerian level set method and
  automated calibration. *Fire Safety Journal* 62: 289-298. (ELMFIRE; level-set approach)
- Albini, F.A. (1979). *Spot fire distance from burning trees: a predictive model.* GTR INT-56;
  Albini (1981) Res. Note INT-309; Albini (1983) *Potential spotting distance from wind-driven
  surface fires.* Res. Pap. INT-309; Chase, C.H. (1981) Res. Note INT-310 and (1984) INT-346;
  Morris, G.A. (1987) Res. Note INT-374. USDA Forest Service.
- Wang, X. et al. (2017). cffdrs: an R package for the Canadian Forest Fire Danger Rating
  System. *Ecological Processes* 6: 5.
