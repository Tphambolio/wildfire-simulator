# Architecture

## Overview

FireSim V3 simulates wildfire spread with the Canadian FBP System. It is a planning, training
and what-if tool: its FBP equations are verified against the cffdrs reference implementation,
but its spread has not been validated against observed fires or against Prometheus/WISE.

## Components

```
engine/          Pure Python fire science (no web dependencies)
  fbp/           FBP calculator (cffdrs-verified), crown fire, fuel constants (18 types)
  fwi/           FWI calculator (FFMC, DMC, DC, ISI, BUI, FWI)
  spread/        Huygens wavelet simulator, level-set grid spread, ellipse geometry,
                 spotting, Monte Carlo burn probability
  data/          Fuel raster loader (code schemes), DEM loader, WUI/water/building masks,
                 synthetic demo landscape

api/             FastAPI backend
  routers/       HTTP + WebSocket endpoints (simulations, fwi, weather)
  services/      SimulationRunner (background threads, grid cache)
  schemas/       Pydantic request/response models (incl. fuel_modifiers)

frontend/        React + Vite + TypeScript + MapLibre GL
  components/    MapView (map error boundary), WeatherPanel, FireMetrics, TimeSlider, EOC tools
  hooks/         useSimulation (WebSocket + polling fallback)
  services/      API client
```

## Data flow

1. The user clicks the map to set an ignition point and sets weather, FWI codes and fuel inputs.
2. The frontend POSTs to `/api/v1/simulations`.
3. `SimulationRunner` loads (and caches) any fuel, water, WUI and DEM grids and runs the
   engine's `Simulator` in a background thread.
4. `Simulator.run()` yields `SimulationFrame` objects; each is streamed over WebSocket.
5. The map draws a perimeter polygon (Huygens) or a burned-cell heat map (grid mode).

## Fire spread models

`Simulator.run()` picks the model: a fuel grid of 50 x 50 cells or more uses the grid
(level-set) model; otherwise the Huygens model with the default fuel type.

**Both models share the FBP layer** (`spread/huygens.py: fbp_for_conditions`): for each
location the head, flank and back rates and the spread direction RAZ come from FBP with the
local fuel, slope (net effective wind) and any per-cell canopy (CBH, CFL), and fire
acceleration from a point ignition follows ST-X-3 eqs 70-72 and 81.

### Huygens wavelets (uniform fuel)

The front is a ring of vertices. Each step, every vertex emits the FBP fire ellipse for the
local conditions (semi-major `(ROS + BROS)/2`, semi-minor `FROS`, centre offset
`(ROS - BROS)/2` along RAZ); the outer hull of the wavelet points is the new front.
Time step 5 min; the fire starts as a 1 m circle. Each front (main ignition or spot fire)
accelerates from its own ignition time. Limitation: the hull is convex, so on heterogeneous
fuel a perimeter cannot be concave; grids large enough for that use the level-set model.

### Level set on the fuel grid (spatial fuel)

`spread/cellular.py`. The front is the zero contour of a function phi on the fuel grid,
advected along the Huygens front velocity `U = dH/dp`, where `H` is the support function of
each cell's FBP wavelet (the approach of ELMFIRE). Second-order ENO upwind differences, first
order next to non-fuel; non-fuel cells act as walls (zero-gradient boundary). The first few
cells of growth use the exact FBP point-ignition ellipse of the ignition cell, restricted to
cells connected to it through fuel. Deterministic: the same inputs give the same fire.

Each burned cell records its arrival time (minutes) and the front's normal speed when it
crossed; intensity and fire type use that speed, so flanks and backs are not given head-fire
intensity. Ember spotting (opt-in) is evaluated on newly burned cells every minute.

This replaced a stochastic cellular automaton in 2026-10; see `docs/verification.md`.

### Spotting and burn probability

- Spotting (`spread/spotting.py`) is an opt-in heuristic ported from v2: ember distance scales
  with wind and intensity and is not an implementation of Albini's (1979) equations.
- Burn probability (`spread/montecarlo.py`) runs the grid model N times with jittered ignition
  point (±100 m), wind speed (±10 %) and RH (±5 %); with the deterministic engine the map
  reflects only that input uncertainty.

## Fuel rasters

`data/fuel_loader.py` maps integer raster codes to FBP fuel types. Several code tables exist
(Edmonton FBP layer, uPLVI, Edmonton canopy LiDAR, RPAS drone pipeline); a table is detected
only if every code in the raster belongs to it, or set explicitly with `code_scheme`. The file
`data/Edmonton_FBP_FuelLayer_20251105_10m.tif` is the 20 m canopy-LiDAR grid (codes
2, 12, 14, 31, 32, 99) despite its name.

## Key design decisions

1. **Engine is standalone**: no web imports; usable as `from firesim.spread.simulator import Simulator`.
2. **One FBP layer** for both spread models, verified against cffdrs.
3. **Generator-based simulation**: `Simulator.run()` yields frames for streaming.
4. **WebSocket + polling fallback** in the frontend.
5. **MapLibre GL** with OSM tiles; the map sits in an error boundary so a WebGL failure does not
   blank the app.

## What this is not

- Not validated against observed fires or other fire growth models (see `docs/verification.md`).
- No hourly weather stream: one weather state per simulation (multi-day mode advances the FWI
  codes daily).
- No authentication or multi-tenancy.
