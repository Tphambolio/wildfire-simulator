# API Reference

Base URL: `http://localhost:8000` (production: `https://firesim-api.fly.dev`). Interactive schema:
`/docs` (OpenAPI). Every endpoint below is under `/api/v1`.

## Endpoints

### POST /api/v1/simulations

Start a new fire spread simulation.

**Request body:**

```json
{
  "ignition_lat": 51.0,
  "ignition_lng": -114.0,
  "weather": {
    "wind_speed": 20.0,
    "wind_direction": 270.0,
    "temperature": 25.0,
    "relative_humidity": 30.0,
    "precipitation_24h": 0.0
  },
  "fwi_overrides": {
    "ffmc": 90.0,
    "dmc": 45.0,
    "dc": 300.0
  },
  "duration_hours": 4.0,
  "snapshot_interval_minutes": 30.0,
  "fuel_type": "C2"
}
```

- `fwi_overrides` is optional. If omitted, FWI components are computed from weather.
- `fuel_type` must be one of the 18 FBP fuel type codes (C1-C7, D1-D2, M1-M4, O1a, O1b, S1-S3).
- `start_time` (optional): scenario start (ignition) time, ISO 8601 with a UTC offset, e.g.
  `"2026-04-28T13:40:00-06:00"` (a time without an offset is rejected with 422). Frame
  `time_hours` and `hourly_weather[].hours_from_start` count from it, and when
  `fuel_modifiers.day_of_year` is not set its local date sets the foliar moisture day of year.
- `cells_mode` (grid runs): `"cumulative"` (default) repeats every burned cell in each frame;
  `"incremental"` sends only the cells burned since the previous frame (a 24 h run: about
  1.6 MB instead of 35 MB). Multi-day runs are always cumulative.

**Response (201):**

```json
{
  "simulation_id": "abc123",
  "status": "running",
  "config": { ... },
  "frames": [],
  "error": null
}
```

### GET /api/v1/simulations/{id}

Get simulation status and results.

**Response (200):**

```json
{
  "simulation_id": "abc123",
  "status": "completed",
  "config": { ... },
  "frames": [
    {
      "time_hours": 0.5,
      "perimeter": [[51.001, -114.001], [51.001, -113.999], ...],
      "area_ha": 2.29,
      "head_ros_m_min": 7.82,
      "max_hfi_kw_m": 2298.6,
      "fire_type": "passive_crown",
      "flame_length_m": 2.73,
      "fuel_breakdown": {"C2": 1.0}
    }
  ],
  "error": null
}
```

Status values: `running`, `completed`, `failed`

**Grid-run frame fields** (spatial fuel grid):

| Field | Meaning |
|---|---|
| `burned_cells` | `{lat, lng, intensity (kW/m), fuel, fire_type, t (arrival, min), ros (m/min, front speed when the cell burned), part}`; `part` is `head`, `flank` or `back`: the spread direction within 45° of the cell's FBP head direction (RAZ, includes slope), within 45° of the opposite, or neither |
| `cells_offset` | incremental mode: the first `cells_offset` cells are the previous frame's and are not repeated; rebuild with `previous.burned_cells[:cells_offset] + burned_cells` |
| `head_ros_m_min` | the head fire's front speed: the fastest head cell reached since the previous frame |
| `head` | that cell: `{lat, lng, ros, raz (deg, direction of spread), hfi, cfb, fuel, t, max_spot_distance_m}`; the spotting distance is Albini's maximum (surface-fire or torching-tree model) with the 10 m wind at that time; `null` when no head cell was reached |
| `building_exposure`, `building_exposure_detail` | see `docs/building-exposure.md` |

### GET /api/v1/simulations/{id}/arrival

Arrival time at each fuel-grid cell, for a finished grid run (404 otherwise):

```json
{"rows": 400, "cols": 600, "lat_min": 53.4, "lat_max": 53.7, "lng_min": -113.7, "lng_max": -113.3,
 "encoding": "int16-le-base64", "minutes": "..."}
```

`minutes` decodes to `rows x cols` little-endian int16, row-major from the north-west corner:
whole minutes after ignition, `-1` where the cell did not burn. Used for isochrones in any
interval and for arrival at roads or control lines.

### WebSocket /api/v1/simulations/ws/{id}

Stream simulation frames in real-time.

**Events received:**

```json
{"type": "simulation.frame", "frame": { ... }}
{"type": "simulation.completed"}
{"type": "simulation.error", "error": "message"}
```

### GET /api/v1/health

Health check endpoint.

**Response (200):**

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "uptime_seconds": 123.4,
  "engine": "firesim"
}
```

### GET /api/v1/simulations/{id}/ensemble

Runs only when the POST included `"ensemble": {"n_members": 30, ...}` (grid runs). The
ensemble starts after the deterministic frames complete (about 1 s per member for a 4 h run
on the Edmonton grid). While running: `{"status": "running", "done": 12, "total": 30}`.
When complete: grid bounds, `arrival` with `p10` / `p50` / `p90` rasters (base64 little-endian
int16 minutes, -1 = fewer than that share of members reached the cell; P10 is the
worst-credible early arrival), `burn_probability` (base64 uint8 percent), member area range
and each member's perturbations.

Perturbations (defaults, **not yet calibrated** on observed fires): wind direction sd 20°
applied to every hourly record, wind speed log-sd 0.2, FFMC sd 1.5, DMC/DC log-sd 0.1, grass
curing sd 10 points, foliar moisture sd 5 %, rate-of-spread multiplier log-sd 0.3. Grass
runs near 58.8 % curing are very sensitive to the curing perturbation (the FBP curing factor
changes slope there).

### POST /api/v1/simulations/multiday

Multi-day scenario: `days` is a list of 1-7 daily noon weather records
(`wind_speed`, `wind_direction`, `temperature`, `relative_humidity`, `precipitation_24h`); the FWI
codes are advanced day to day from `fwi_overrides` (Van Wagner & Pickett 1985; `month` sets
the day-length factors) and each day's
grid run continues from the previous day's burned area. Frames carry `day`. Cells are always
cumulative.

### POST /api/v1/simulations/perimeter-override

Restart from an observed perimeter (e.g. RPAS thermal mapping): `simulation_id` of a run whose
configuration is reused, `perimeter_geojson` (a GeoJSON Polygon or MultiPolygon *geometry*, not
a Feature, in [lng, lat]), `duration_hours` and `snapshot_interval_minutes`. The observed area
starts burned and spreads as an established fire (no point-ignition acceleration), with the
grid model when the run had a fuel grid.

Optional `active_edges`: a GeoJSON geometry in [lng, lat] marking where the observed fire is
still active, e.g. hot edges or heat seen on an RPAS thermal flight (LineString /
MultiLineString along the active edges, Polygon / MultiPolygon of active zones, Point /
MultiPoint hotspots). Burned cells within `active_edge_buffer_m` of it (default one fuel-grid
cell, max 5,000 m) spread; the rest of the observed area is burned out and does not spread,
though fire from an active edge can later reach the fuel beyond an inactive edge. Omit it to
treat the whole perimeter as active (the previous behaviour). Grid model only: a source run
without a fuel grid returns 422. On observed Alberta fires this raised one-day skill
(docs/validation.md).

### POST /api/v1/simulations/burn-probability

Monte Carlo burn probability (synchronous). Varies the ignition point (`jitter_m`), wind speed
(`wind_speed_pct`) and RH (`rh_abs`, applied as an FFMC change) over `n_iterations` grid runs
of `duration_hours`; returns `burn_probability[rows][cols]` (fraction of iterations that
burned each cell) with the grid bounds. Not a Burn-P3-style analysis over historical weather.

### GET /api/v1/simulations/fuel-grid-image?fuel_grid_path=...

The fuel grid as a base64 PNG for the map overlay, its WGS84 `bounds`, and a `legend` of the
FBP fuel types drawn with their colours.

### POST /api/v1/fwi/calculate, POST /api/v1/fwi/multi-day

FWI System codes (FFMC, DMC, DC, ISI, BUI, FWI) from a noon observation, or chained across
daily observations, with `danger_rating` = the CWFIS national FWI map class (0-5 Low, 6-15
Moderate, 16-22 High, 23-29 Very High, 30+ Extreme). That is an FWI map class, not an official
fire danger rating.

### GET /api/v1/weather/current?lat=&lng=

Current fire weather and FWI codes from the nearest CWFIS station (GeoServer WFS, within 2°),
for use as `fwi_overrides`.

### GET /api/v1/version

```json
{"version": "3.0.0", "git_sha": "6df2df9..."}
```

`git_sha` is the commit the deployed build came from (set by the CI deploy), `unknown` locally.

## Perimeter format

Perimeters are arrays of `[latitude, longitude]` pairs forming a closed polygon. The first and last points are the same.

## Error responses

```json
{
  "detail": "Error description"
}
```

Common status codes:
- 404: Simulation not found
- 422: Validation error (invalid fuel type, missing fields)
- 500: Internal simulation error
