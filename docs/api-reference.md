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
- `fuel_modifiers.grass_cure` (degree of curing, %, O-1a/O-1b; changed 2026-10-10, decision M1):
  an explicit value is always used as given. If omitted:
  - **95 %** when the run's date is in the pre-green-up window, day of year 60-149 (about 1 March
    to 29 May). The date is `fuel_modifiers.day_of_year`, else `start_time`'s local date.
  - **No default** outside that window or with no date: the request is rejected with **422**
    ("fuel_modifiers.grass_cure is required ...") when O-1 grass can burn, i.e. uniform
    `O1a`/`O1b` fuel or any fuel-grid run (`fuel_grid_path`, `use_ca_mode`; burn-probability
    requests always). Runs where no grass can burn need no value (`grass_cure` stays null).
  - Before 2026-10-10 the default was a fixed 60 %, which gives an FBP curing factor of 0.20
    (GLC-X-10 eq 35b, p.9), so grass spread at a fifth of its fully cured rate. Clients that
    relied on it should send `"grass_cure": 60` to keep their results.
  - The value used is echoed in the response `config.fuel_modifiers.grass_cure`. Same rule on
    `/simulations/multiday` and `/simulations/burn-probability` (the latter has no
    `start_time`: use `fuel_modifiers.day_of_year`). Sources: `engine/src/firesim/fbp/curing.py`,
    `docs/model-card.md`.
- `start_time` (optional): scenario start (ignition) time, ISO 8601 with a UTC offset, e.g.
  `"2026-04-28T13:40:00-06:00"` (a time without an offset is rejected with 422). Frame
  `time_hours` and `hourly_weather[].hours_from_start` count from it, and when
  `fuel_modifiers.day_of_year` is not set its local date sets the foliar moisture day of year.
- `burning_period` (optional, default off): `{"start_hour": 10, "end_hour": 20}` or `[10, 20]`,
  local clock hours 0-24 with start before end (else 422). Fire spreads only between these
  hours each day and not at all outside them (Prometheus / W.I.S.E. "burning conditions"); a
  point ignition outside the period holds until it opens. Hours are on the clock of
  `start_time`'s own UTC offset, so `start_time` is required (422 without it) and should
  carry the scenario's local offset (the UI sends America/Edmonton, UTC-6 in Alberta from
  2026). Both models (grid and uniform-fuel Huygens). Frames outside the period report the
  fire as it was: no growth, and the Huygens head ROS/HFI as 0.
- `ffmc_spin_up` (optional, default `false`): start the hourly FFMC at 17:00 local
  (`start_time`'s clock) on or before the start, from `fwi_overrides.ffmc` taken as the daily
  value then (about 16:00 LST; Lawson et al. 1996), and run it through the night on
  `hourly_weather`. Needs `start_time` and `hourly_weather` records back to that 17:00
  (`hours_from_start` down to -24; e.g. -13 for a 06:00 start) and for the run (422 if
  missing). Without it, records before the start are dropped (the one in force at the start
  applies from 0).
- The UI turns both on by default (10-20 h; spin-up when hourly forecast weather is used): with
  RPAS active edges they raised one-day F1 on held-out Alberta fires from 0.12 to 0.21
  (docs/validation.md). The API keeps them off so existing clients see no change.
- `ensemble` members inherit `burning_period` and the spin-up.
- `seed` (optional integer): seed for the stochastic parts of the run (ember spotting, with
  `enable_spotting`). Omitted, it is derived from the other inputs, so identical requests give
  identical results; set it to rerun the same inputs with different draws
  (docs/verification.md, "Spotting is repeatable").
- `structure_spread` (optional, default `false`): **illustrative — not validated in Canada.**
  Grid runs with building footprints (`buildings_path` or `FIRESIM_BUILDINGS_PATH`, and
  `FIRESIM_NEIGHBOURHOODS_PATH`): every footprint in the run area can become a building unit
  (units are built only for the area the spread can reach, which gives the same result as
  building them all; spec §2), a unit is involved when the front reaches a grid cell its footprint touches or one of their
  8 neighbours (10 m contact measured from the building's grid cells), and fire passes
  between units up to 30 m apart in the Hamada crossing time under the run's 10 m wind
  (`docs/structure-spread-spec.md` §3-4). Frames then carry `structure_spread` (counts) and
  the final frame `structure_spread_detail` (the involved units, for the map). Modelled
  involvement, not a prediction of which buildings burn; building exposure is unchanged. If
  the reachable area holds more than 60,000 footprints the run still completes and frames say
  `computed: false` with `note: "not computed: too many buildings in run area"` (memory guard
  for the 2 GB API machine). Ignored by uniform-fuel (Huygens) runs, multi-day, perimeter-override and
  ensemble members.
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
| `structure_spread` | only with the request flag `structure_spread`, else `null`: counts by this frame `{model: "hamada", label: "illustrative — not validated in Canada", computed: true, units_in_run, units_built, units_front_contact, units_structure_to_structure, units_involved, combustible_fraction, neighbour_cutoff_m, wildland_contact_m, front_contact_rule}`. `units_in_run`: buildings whose centroid is in the run area (fuel grid box); `units_built`: units built for the area the spread can reach (spec §2); `units_front_contact`: units reached by the wildland front (a burned cell within `wildland_contact_m` of a grid cell the footprint touches; `front_contact_rule: "building_cells"`); `units_structure_to_structure`: units reached from another unit (Hamada). When the memory guard stops the build: `{model, label, computed: false, note: "not computed: too many buildings in run area", units_in_run, units_needed, max_units, ...}` with the three counts `null`. Any display must carry `label` (`docs/structure-spread-spec.md`) |
| `structure_spread_detail` | final frame only, with `structure_spread` (else `null`; `[]` when not computed): one entry per **involved** unit, in involvement order, `{id, t_h, mechanism, source_id, polygon}`. `t_h`: hours from the start; `mechanism`: `"front"` (wildland front contact) or `"b2b"` (building to building); `source_id`: the `id` of the unit that passed the fire on (`b2b` only, else `null`); `polygon`: the footprint ring `[[[lng, lat], ...]]`, simplified (0.5 m) and rounded to 6 decimals, largest part of a multi-part footprint. No other attributes (no address, owner or parcel data). About 200-250 bytes per unit (20-160 kB on the 2026-10-09 sensitivity runs). **Map display only** (owner decision 2026-10-10): the app does not put it in any export, ICS 209 or report. Illustrative — not validated in Canada |

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
int16 minutes, -1 = fewer than that share of members reached the cell; P10 = the time by
which 1 in 10 members reached the cell, the early end of the modelled range),
`burn_probability` (base64 uint8 percent), member area range and each member's
perturbations (`members`, each with its index `member` and `area_ha`).

Failed members: a member whose run raises is left out and listed in `failed`
(`[{"member": 3, "error": "IndexError: ..."}]`); `members_ok` and `members_failed` count them,
and the percentiles, burn probability and area range are over the `members_ok` members that
finished. Members that burned nothing (their ignition cell cannot carry fire under their
perturbed weather) are finished members with `area_ha` 0 and count in every statistic. The
ensemble is `"status": "failed"` (with `error`) only when fewer than half the members finish.

Default perturbations (`EnsembleParams`; calibrated on observed Alberta fires, see
`docs/validation.md` "Ensemble calibration" for sources and held-out scores): `wind_dir_sd_deg`
24 (degrees, one offset per member applied to every hourly record), `wind_speed_log_sd`
0.405 (log-normal multiplier), `ffmc_sd` 7.2, `dmc_dc_log_sd` 0.6 (log-normal, DMC and DC
independently), `curing_sd` 13.5 (percentage points), `fmc_sd` 15 (%), `ros_log_sd` 0.825
(log-normal rate-of-spread multiplier, median 1), `ignition_jitter_m` 0. On held-out fires
the observed one-day area fell inside the members' P10-P90 range on about half the days, so
the range is narrower than the real uncertainty, and **P10 is not a worst case**: its
footprint held at least 90 % of the observed growth on only 29 % of fire-days. Grass runs
near 58.8 % curing are very sensitive to the curing perturbation (the FBP curing factor
changes slope there); the curing and foliar-moisture sizes are not validated.

### POST /api/v1/simulations/multiday

Multi-day scenario: `days` is a list of 1-7 daily noon weather records
(`wind_speed`, `wind_direction`, `temperature`, `relative_humidity`, `precipitation_24h`); the FWI
codes are advanced day to day from `fwi_overrides` (Van Wagner & Pickett 1985; `month` sets
the day-length factors) and each day's
grid run continues from the previous day's burned area. Frames carry `day`. Cells are always
cumulative. Optional `start_time` (each day runs 24 h from its clock time) and
`burning_period` (as above, applied every day; needs `start_time`). No FFMC spin-up: multi-day
runs have daily weather only.

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

Optional `start_time` (time of the observed perimeter, ISO 8601 with offset; default the source
run's `start_time`), `burning_period` and `ffmc_spin_up` (as for POST /simulations; not
inherited from the source run). The source run's `hourly_weather` is re-based to `start_time`
(before 2026-10 the restart ignored it and used the constant `weather`); with `ffmc_spin_up`
it must reach back to 17:00 before the restart (422 otherwise).

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

Current fire weather and starting FWI codes for a point, for use as `fwi_overrides`. Tiers, in
order (`source_tier`):

1. **`pyra`** (Alberta, from 2026-10-10): the codes Pyra's station page shows today for the
   nearest station in Pyra's Alberta list ([Pyra](https://tphambolio.github.io/FWI/), the team's
   fire-weather app). See "Pyra tier" below.
2. **`cwfis`** / **`cwfis_archive`**: the nearest CWFIS station's published codes (below).
3. **`gem_estimate`**: a one-day cold-start estimate (below).

User-entered codes in the app always win over any tier.

#### Pyra tier

FireSim reproduces Pyra's Alberta station page (`initFWI`, Pyra commit in `pyra.pyra_commit`)
with its own FWI calculator (same equations, FFMC coefficient 147.2):

- **Station:** nearest in Pyra's `ALBERTA_STATIONS` (198 stations, exported to
  `services/pyra_stations.py`; first of equally near ones). Only points in Pyra's Alberta box
  (48.8-60.5° N, 120.5-109.5° W) use this tier.
- **Carry-over:** CWFIS `firewx_stns_current` within ±2° of the Pyra station, nearest station
  with FFMC/DC unless a weather-only station is > 200 km nearer (Pyra's holding cache), and the
  station's entry in Pyra's published `data/cwfis_prev.json` (by name, else nearest within
  10 km); the newer one, CWFIS first on a tie; at most 2 days old. Spring (March-June) DC ≤ 60 is
  raised to Pyra's regional floor.
- **Before noon LST (MST, 19 UTC):** the carry-over is stepped one day with the GEM noon-LST
  forecast for today (Open-Meteo `models=gem_seamless`, the URL Pyra requests: noon temperature,
  RH, 10 m wind, 24 h rain to noon). `codes_status` = `forecast`.
- **After noon LST:** a CWFIS station with codes → its codes as published (`today`, or
  `yesterday` until CWFIS publishes today's, as Pyra shows them); CWFIS with weather-only
  stations → carry-over stepped with that station's observation; CWFIS empty → carry-over
  stepped with GEM's noon-LST value (Pyra tries MSC SWOB first; FireSim does not). `codes_status`
  = `stepped` when stepped.
- ISI/BUI/FWI are the daily values (noon wind), as in Pyra's components strip. Pyra's
  "peak burn" ISI/FWI (same codes, 16:00 MDT wind) are in `pyra.peak_*`.
- `source` reads "Pyra · <station> · as of noon LST [forecast] <date> (chain <CWFIS station>
  <date>)"; `station_name` / `distance_km` are the Pyra station and its distance from the point.
- The carry-over file is cached for 15 min; on a failed refresh the last copy is used while it
  is ≤ 48 h old (Pyra's limit); the GitHub Pages copy is tried when raw.githubusercontent.com
  fails.
- If the tier has no value (outside Alberta, no carry-over ≤ 2 days, GEM unavailable) the next
  tier answers and its `message` ends with "Pyra chain not used: <reason>".
- `FIRESIM_PYRA_SOURCE=0` turns the tier off.

`pyra` object (only when `source_tier` is `pyra`):

| Field | Meaning |
|---|---|
| `station_name`, `station_lat`, `station_lng`, `station_distance_km` | Pyra station and its distance from the point |
| `page_url` | Pyra's station page for it (shows the same numbers) |
| `chain_source` | `cwfis_live` (CWFIS current layer) or `pyra_cwfis_prev` (Pyra's `cwfis_prev.json`) |
| `chain_station_name`, `chain_station_id`, `chain_distance_km`, `chain_date` | Station whose codes were carried, CWFIS feature id (live only), distance from the Pyra station, noon-LST date of the carried codes |
| `step` | `none` (codes used as published), `gem_noon_forecast`, `gem_noon` or `cwfis_obs` |
| `rain_24h` | 24 h rain to noon LST used for the step (mm) |
| `peak_wind_speed`, `peak_isi`, `peak_fwi` | Pyra's peak-burn values (16:00 MDT wind); `null` when the weather has no 16:00 hour |
| `carry_over_generated`, `carry_over_url` | `generated` time and URL of the carry-over file read |
| `pyra_commit` | Pyra commit the port was checked against |
| `notes` | Known differences for this answer (e.g. SWOB not queried; 2-day-old carry-over stepped once, as Pyra does) |

#### CWFIS tiers

Current fire weather and FWI codes from the nearest CWFIS station **that reports FFMC, DMC and
DC** (GeoServer WFS `public:firewx_stns_current`, within 2°).
Stations without codes are skipped (the message says how many nearer ones were); `distance_km`
is the distance of the station actually used.

- If the current layer is empty (it is refreshed around 19 UTC), the newest day of the archive
  layer `public:firewx_stns` at most two days old is used (`source` ends in `[archive layer]`).
- If no station has codes (off-season) or CWFIS has nothing, the codes are a **cold-start
  estimate**: one day from 85 / 6 / 15 with the Open-Meteo GEM forecast (`models=gem_seamless`)
  at the latest noon LST and the noon-to-noon 24 h rain. Station weather is kept when present.
  If GEM has no value for a needed variable, the message says which.
- `temperature` may be negative (before 2026-10-10 sub-zero values were dropped).

Fields added 2026-10-10 (all optional, `null` when not applicable):

| Field | Meaning |
|---|---|
| `codes_date` | Date (YYYY-MM-DD) whose noon-LST codes are returned (CWFIS `rep_date`, or the estimate's noon) |
| `codes_status` | `today`, `yesterday`, `older` (station codes), `estimate` (cold-start estimate); Pyra tier also `forecast` (before noon LST, stepped with the noon forecast) and `stepped` (after noon LST, carried and stepped) |
| `codes_label` | The same in words, e.g. "Yesterday's codes (as of noon LST 2026-10-09; today's are computed after noon LST)"; also appended to `message` |
| `weather_model` | Open-Meteo model used for any value (`gem_seamless`), `null` for station data only |
| `source_tier` | `pyra`, `cwfis`, `cwfis_archive` or `gem_estimate` (`null` when unavailable); added with the Pyra tier |
| `pyra` | Pyra tier details (above) |

Noon LST uses the province's standard-time offset (AB UTC-7, BC UTC-8, …), else the zone's
standard offset, else longitude / 15.

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
