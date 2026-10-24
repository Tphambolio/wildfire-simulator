# Active-segment restart (backlog A1)

How FireSim restarts a forecast from an observed perimeter (an RPAS trace or an agency
perimeter) when only parts of its boundary are still burning. Report: [R62]
(`~/dev/wildfire/reports/Active-segment restart 2026-10-24.md`). Status: built and
unit-tested; **not validated on observed fires**. The pre-registered harness test of the barrier
failed (no detectable difference; see "Validation" below).

## 1. What a restart does

The observed perimeter's interior starts burned. Its boundary is split into segments, and each
segment is **active** or **held**:

- **Active** segments spread normally (FBP rates, as an established fire, with no acceleration).
- **Held** segments are burned out, and for the first **H hours** after the observation time T
  the ring of unburned fuel next to them (one grid cell, `hold_band_m`) cannot burn either.
  The ring is not applied next to an active cell. This stops fire from an active edge creeping
  along the outside of a held line during the hold.
- At T + H the barrier is lifted and the front is rebuilt at the edge of everything burned so
  far, so held segments may spread normally from then on.

| `hold_hours` | Behaviour |
|---|---|
| omitted (default in the API) | As before 2026-10-24: held parts are burned out for the whole run, with no barrier ring and no release. Every existing request runs exactly as before. |
| `0` | The explicit **all active** option: the classification is not applied and the whole perimeter spreads. |
| `1`–`72` | Held line plus barrier ring for H hours, then released. **The app sends 24 h by default** (owner decision, 2026-10-24; see §5). |

The hold acts only in the grid model (a fuel grid, or the synthetic demo grid). A request with
active segments or a hold and no fuel grid gets a 422.

Engine: `firesim.spread.cellular.run_cellular_simulation(hold_hours=, hold_band_m=)`, passed
through `Simulator`. Tests: `engine/tests/spread/test_held_line_hold.py`.

## 2. Classifying segments: the rule

Sources, in priority order. The first one given is used on its own.

1. **Manual:** edges drawn on the map or whole perimeter sides picked in the panel
   (`active_edges`, as before).
2. **RPAS thermal hotspots:** GeoJSON Point / MultiPoint features with a time property
   (`time`, ISO 8601; also `datetime`, `observed_at`, `timestamp`; naive times are read as
   UTC). Points without a time are dropped.
3. **VIIRS 375 m (NASA FIRMS):** an uploaded FIRMS CSV (`latitude`, `longitude`, `acq_date`,
   `acq_time` HHMM UTC, `confidence`). Rows with low confidence (`l`) are dropped; nominal (`n`)
   and high (`h`) rows are kept. MODIS rows (numeric confidence) are kept at confidence ≥ 30.
   There is no live FIRMS fetch: the user uploads the file.

**Rule (objective, in `firesim/active_segments.py`):**

- Each outer ring of the perimeter is cut into equal pieces of about 250 m along the boundary
  (`classification_segment_m`).
- A piece is **active** if at least one qualifying detection lies within **1 km**
  (`classification_radius_m`) of its nearest point, and was observed in the **12 h up to and
  including T** (`T − 12 h ≤ t ≤ T`; `classification_window_h`).
- Every other piece is **held**. If no piece is active, the whole perimeter is held: nothing
  spreads until T + H.
- Distances use a local equirectangular projection about the perimeter centroid.

The 1 km and 12 h come from the [R53] §6 item 1 proposal. They are fixed in code, not fitted.

References:
- Schroeder, W., Oliva, P., Giglio, L., Csiszar, I.A. (2014). The New VIIRS 375 m active fire
  detection data product. *Remote Sensing of Environment* 143:85–96 (pixel size, confidence
  classes).
- NASA FIRMS, *VIIRS I-Band 375 m Active Fire Data* attribute descriptions
  (firms.modaps.eosdis.nasa.gov).
- [R53] Dixie early-arrival diagnostic §4 point 1, §5 and §6 items 1–3.

**Not validated.** The rule has not been checked against real RPAS thermal flights, or against
VIIRS on fires with mapped held lines.

## 3. API (additive; existing payloads unchanged)

- `POST /api/v1/simulations/perimeter-override` gains optional fields: `hold_hours`,
  `hold_band_m`, `hotspots_geojson`, `firms_csv`, `classification_radius_m` (1000),
  `classification_window_h` (12) and `classification_segment_m` (250). Hotspot and VIIRS
  classification uses the restart's `start_time` as T (or the source run's).
- **`POST /api/v1/simulations/observed-perimeter`** (owner items R13/R14) is the standalone
  forecast from an observed perimeter, with no source run. The body is a normal run request
  (weather, FWI codes, fuel, buildings, options) plus:
  - `perimeter_geojson`;
  - `observation_time` T, **required**, ISO 8601 with a UTC offset; the run starts at T, and a
    `start_time` that differs from T is refused;
  - the same classification and hold fields.

  The ignition defaults to the perimeter's centroid. Ensembles are not offered.
- **`POST /api/v1/simulations/classify-perimeter`** classifies without running. It takes the
  perimeter, `observation_time` and the hotspots or FIRMS CSV, and returns `summary`,
  `active_edges` (a MultiLineString, or null) and `segments` (a FeatureCollection with a
  `status` of `active` or `held`).
- Responses of restarts that use any A1 field carry `active_segments`:
  - the source, `n_segments`, `n_active`, `n_held`, the lengths and the hold;
  - after the run, `static_share_model`: the share of the start perimeter (500 m samples) that
    advanced ≤ 1 km, the [R58] method on the run's arrival raster; and `growth_ha`.

  Other runs return `active_segments: null`. The archived request stores a FIRMS CSV as its row
  count only.

## 4. App

Setup → **Observed perimeter (RPAS)**:

- **Observed at (T):** an editable field that defaults to the timeline's selected time.
- **Classify from RPAS hotspots** and **Classify from VIIRS file** buttons.
- A **Classified from detections** mode. The map draws active segments as orange-red dashes and
  held segments in blue, and the panel reads "Segments held N / active M".
- **Hold held segments (h)**, 0–72 h, default 24.
- After a run, a readout: "This run: modelled static share X % · Segments held N / active M ·
  hold H h". The observed static share cannot be known during an incident.

Before any run, the same panel offers **Forecast from observed at T**, using Setup's weather,
fuel and options. No evacuation suggestions, no per-building output.

## 5. Validation

CFSDS harness: `scripts/validate.py run --ignition active --active-days N --hold-hours H`,
`--hold-band-m` and `--start-types mature`. The harness proxy for "active" is CFSDS day of
burning in the last N burn days within one cell. Results are in [R62].

Pre-registered test `docs/PREREG_ACTIVE_SEGMENT.md` (2026-10-24; active rule 2 burn days,
H = 24 h chosen on calibration fire-days): **fail**. ΔF1 at 17 h was −0.002 [−0.005, +0.001]
on 70 held-out mature fire-days. The barrier stays opt-in and not validated.

**Owner decision (Travis Kennedy, 2026-10-24): the app keeps H = 24 h as its default hold** when
edges are marked or classified, although the barrier failed its pre-registered test. On the
held-out evidence it is harmless (ΔF1 −0.002, about 6 % less growth) and it matches the intent of a
held line in an EOC restart. The API default is unchanged (an unset `hold_hours` means no barrier),
and the barrier stays labelled "not validated".
