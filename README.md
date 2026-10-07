# FireSim: Canadian FBP wildfire spread simulator

Fire growth, fire behaviour and building exposure from the Canadian Forest Fire Behavior
Prediction (FBP) System, built for municipal Emergency Operations Centre (EOC) planning,
preparedness and training in the wildland-urban interface.

- Live app: https://wildfire-simulator.vercel.app (API: https://firesim-api.fly.dev/api/v1/health)
- **Intended use (now): preparedness, training, exercises and what-if planning.** FireSim's FBP
  equations are verified against the CFS reference implementation and its spread against WISE
  on uniform fuel, but its spread **has not yet been validated against observed fires**. Do not
  use its output as an operational prediction or as the basis for evacuation decisions. See the
  [model card](docs/model-card.md) and [verification and limits](docs/verification.md).

## Evidence status

| Component | Status | Where |
|---|---|---|
| FBP System (18 fuel types, ST-X-3 + GLC-X-10) | Verified against CFS `cffdrs` to floating-point precision | [docs/fbp-reference.md](docs/fbp-reference.md) |
| FWI System, hourly FFMC | Verified against `cffdrs` | [docs/verification.md](docs/verification.md) |
| Spread models (Huygens, level set) | Burned area 0.90-1.07 x the FBP ellipse; vs WISE on uniform fuel and slope, area ratio 0.955-1.09 and head 0.98-1.05 | [docs/verification.md](docs/verification.md) |
| Head / flank / back, head summary | Fastest head cell spreads at FBP ROS and rearmost back cell at BROS on uniform fuel; head direction within 2° of RAZ | `engine/tests/spread/test_deployment_data.py` |
| Spotting | Maximum distance (Albini/Chase/Morris) reproduces published examples; emission and landing are heuristic (illustrative only) | [docs/verification.md](docs/verification.md) |
| Building exposure | Reproduces Cohen's (2004) SIAM worked values; exposure, not ignition probability | [docs/building-exposure.md](docs/building-exposure.md) |
| Observed fires | **Not yet validated** (validation harness in progress) | [docs/model-card.md](docs/model-card.md) |

## What it does

- **FBP fire behaviour** for all 18 fuel types: rate of spread (head, flank, back), fuel
  consumption, crown fraction burned, head fire intensity, fire type, length-to-breadth,
  slope through net effective wind, point-ignition acceleration, foliar moisture from date.
- **Fire growth** on spatial fuel grids with a deterministic level-set model (ELMFIRE-style
  Huygens velocity from the FBP ellipse; wraps around water, roads and buildings), or Huygens
  wavelets on uniform fuel. Hourly weather streams with hourly FFMC; Open-Meteo forecast
  aligned to a scenario start time.
- **Deployment data**: per-cell front speed and head / flank / back, the head's speed,
  direction, intensity and Albini maximum spotting distance per frame, and an arrival-time
  grid (`GET /simulations/{id}/arrival`).
- **Starting from an observed fire**: an RPAS (drone) perimeter or the previous day of a
  multi-day run.
- **Building exposure**: distance bands, worst-case radiant flux and a flux-time dose index
  per building (exposure, not ignition).
- **Burn probability** (Monte Carlo over ignition point, wind speed and RH).
- **EOC console**: incidents and operational periods, ICS forms and an ICS-209 situation
  report built from the run.
- **Neighbourhoods**: the modelled time the fire is first within 500 m of each
  neighbourhood. The evacuation status (Order / Alert / Watch) is set by Planning and drawn as
  blue outlines with line styles and labels. The model never suggests a tier.
- **Classes from published sources**: head fire intensity classes 1-6 (Cole & Alexander 1995;
  CWFIS map limits) and FWI classes (CWFIS national FWI map intervals).

## Stack

| Layer | Technology |
|-------|-----------|
| Fire engine | Python 3.10+ (NumPy, SciPy, Shapely), no web dependencies |
| API | FastAPI + WebSocket (Fly.io) |
| Frontend | React 19 + Vite + TypeScript + MapLibre GL (Vercel) |
| Tests | pytest (engine, API), Vitest and Playwright + axe (frontend), GitHub Actions |

## Quick start

```bash
# API
PYTHONPATH=engine/src:api/src uvicorn firesim_api.main:app --port 8000
# Frontend (second terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:3000, click the map to set an ignition point, set
the weather and run. Edmonton data paths and other settings: [docs/deployment.md](docs/deployment.md).

## Testing

```bash
make test           # engine and API (pytest): cffdrs reference, FBP ellipse, WISE comparison, exposure, ...
cd frontend && npm test            # Vitest unit tests
cd frontend && npx playwright test # end-to-end with a mocked API, accessibility (axe), performance budget
```

See [frontend/README.md](frontend/README.md#testing) for the frontend fixture and test setup.

## Project structure

```
engine/     Fire science: FBP, FWI, spread models, spotting, exposure, data loaders (no web deps)
api/        FastAPI service: runs, streaming, arrival grid, FWI and weather endpoints
frontend/   React app: map, setup, situation panel, timeline, EOC console
docs/       Model card, verification, FBP reference, architecture, API, deployment
```

## Documentation

- [docs/model-card.md](docs/model-card.md): intended use, users, evidence, limits, out-of-scope uses
- [docs/verification.md](docs/verification.md): what is verified, how, history of fixes, known limits
- [docs/fbp-reference.md](docs/fbp-reference.md): FBP equations and inputs
- [docs/building-exposure.md](docs/building-exposure.md): exposure metrics and their sources
- [docs/architecture.md](docs/architecture.md): components and spread models
- [docs/api-reference.md](docs/api-reference.md), [docs/deployment.md](docs/deployment.md)

## Name

"FireSim" is also the name of a module in Technosylva's Wildfire Analyst. The name of this
project is under review before any release outside the City of Edmonton.

## References

Fire behaviour
- Forestry Canada Fire Danger Group (1992). *Development and Structure of the Canadian Forest
  Fire Behavior Prediction System.* Information Report ST-X-3.
- Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). *Updates and revisions to the 1992
  Canadian Forest Fire Behavior Prediction System.* Information Report GLC-X-10.
- Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire. *Can. J. For. Res.* 7: 23-34.
- Van Wagner, C.E. (1977). *A method of computing fine fuel moisture content throughout the
  diurnal cycle.* Information Report PS-X-69 (hourly FFMC).
- Van Wagner, C.E., Pickett, T.L. (1985). *Equations and FORTRAN program for the Canadian Forest
  Fire Weather Index System.* Forestry Technical Report 33.
- Wang, X. et al. (2017). cffdrs: an R package for the Canadian Forest Fire Danger Rating
  System. *Ecological Processes* 6: 5.
- Alexander, M.E., Cruz, M.G. (2012). Interdependencies between flame length and fireline
  intensity. *Int. J. Wildland Fire* 21: 95-113.
- Cole, F.V., Alexander, M.E. (1995). *Head fire intensity class graph for FBP System Fuel Type
  C-2 (Boreal Spruce).* Alaska DNR Division of Forestry and Canadian Forest Service.

Fire growth
- Richards, G.D. (1990). An elliptical growth model of forest fire fronts and its numerical
  solution. *Int. J. Numer. Meth. Eng.* 30: 1163-1179.
- Tymstra, C., Bryce, R.W., Wotton, B.M., Taylor, S.W., Armitage, O.B. (2010). *Development and
  structure of Prometheus: the Canadian Wildland Fire Growth Simulation Model.* NOR-X-417.
- Lautenberger, C. (2013). Wildland fire modeling with an Eulerian level set method and
  automated calibration. *Fire Safety Journal* 62: 289-298.

Spotting
- Albini, F.A. (1979). *Spot fire distance from burning trees: a predictive model.* GTR INT-56;
  Albini (1981) Res. Note INT-309; Albini (1983) Res. Pap. INT-309; Chase, C.H. (1981) Res. Note
  INT-310 and (1984) INT-346; Morris, G.A. (1987) Res. Note INT-374. USDA Forest Service.

Structure exposure
- Cohen, J.D. (2004). Relating flame radiation to home ignition using modeling and experimental
  crown fires. *Can. J. For. Res.* 34: 1616-1626.
- National Research Council Canada (2021). *National Guide for Wildland-Urban Interface Fires.*
