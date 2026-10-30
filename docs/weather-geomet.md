# Model weather from ECCC GeoMet

FireSim's model weather (the hourly forecast/hindcast stream for a run, the GEM noon-LST
weather for the Pyra tier and the off-season cold-start estimate) comes from **Environment and
Climate Change Canada's GEM models, read from MSC GeoMet** by the API. Until 2026-10-30 it came
from the free Open-Meteo forecast API (`models=gem_seamless`), whose free tier is for
non-commercial use only. Pyra made the same switch on 2026-10-29 (Pyra PR #14, commit
`38c0d2b`). This is a **data-source swap, not a method change**: noon-LST sampling, carry-over,
the noon-to-noon 24 h rain window and the Alberta FWI LST offset (`FIRESIM_AB_FWI_LST_OFFSET_H`)
are unchanged (`docs/PROJECT_RECORD.md` §3).

It is a **product-only change**. The validation reports use ERA5 through the Open-Meteo
archive under `scripts/` (and `engine/src/firesim/validation/weather.py`), which this change
does not touch. None of them needs re-running.

## Source and licence

- Service: MSC GeoMet, `https://geo.weather.gc.ca/geomet`, OGC WCS 2.0.1 `GetCoverage` (data) and
  WMS 1.3.0 `GetCapabilities` (which model runs exist).
- Licence: **Open Government Licence – Canada**
  (<https://eccc-msc.github.io/open-data/licence/readme_en/>). Attribution, shown in the app's
  About tab and returned by the API (`attribution`): "Data Source: Environment and Climate
  Change Canada".
- No key, no quota published. FireSim sends at most 8 requests at a time per call.

## Design

FireSim needs weather at **any ignition point**. Pyra samples a fixed station list every 6 h
in a GitHub Action, which does not fit that need. So FireSim reads GeoMet server-side:

- `api/src/firesim_api/services/geomet.py`: one WCS request per (layer, model run, hour) for a
  0.02° box around the point, resampled by GeoMet to **one cell** (`SCALESIZE=x(1),y(1)`, i.e.
  the model cell containing the point, nearest neighbour), returned as ESRI ASCII grid text, so
  no raster library is needed. Every request pins the run (`DIM_REFERENCE_TIME`).
- `GET /api/v1/weather/hourly?lat=&lng=&start=&end=` serves the frontend. The browser never
  calls GeoMet or Open-Meteo.
- The **Pyra tier** reads Pyra's own GEM file, `data/gem/ab.json` (written every 6 h by Pyra's
  `gem-points.yml`, bilinear HRDPS → RDPS → GDPS at every Pyra station, 48 h of history), by
  station within 1 km. FireSim's codes therefore stay equal to Pyra's page. GeoMet at the
  station is the fallback.
- The **cold-start estimate** reads GeoMet at the point. When GeoMet no longer holds the
  oldest hours of the 24 h rain window it falls back to Pyra's file (below).

### Caching

- Model-run lists (WMS capabilities per model): 10 min, and the last list is reused if a
  refresh fails.
- Every sampled value, keyed by (layer, run, hour, point rounded to 0.001°): in-process LRU of
  200,000 values. A model run never changes, so there is no expiry.
- Pyra's GEM file: 15 min (as Pyra's carry-over file), last good copy on failure.
- Cost on a cold cache: about 5 requests per hour of weather (HRDPS rain needs one more per
  run), ~0.3-0.7 s each. A 24 h run with its spin-up (~37 h) is about 190 requests, ~10 s;
  161 h was 704 requests, 36 s (2026-10-30). A repeat request is served from the cache.

## Models, layers, units, conversions

| Model | Grid / horizon | Layers (temp, RH, wind, direction, rain) | Units as served |
|---|---|---|---|
| HRDPS | 2.5 km, hourly to +48 h, runs 00/06/12/18Z | `HRDPS.CONTINENTAL_TT` `_HR` `_WSPD` `_WD` `_PR` | °C, %, **m/s**, ° (from), **mm since the run started** |
| RDPS | 10 km, hourly to +84 h, runs 00/06/12/18Z | `RDPS_10km_AirTemp_2m` `_RelativeHumidity_2m` `_WindSpeed_10m` `_WindDir_10m` `_Precip-Accum1h` | °C, %, m/s, °, mm in the hour |
| GDPS | 15 km, hourly to +84 h, 3-hourly to +240 h, runs 00/12Z | as RDPS with `GDPS_15km_`; `_Precip-Accum3h` in the 3-hourly tail | as RDPS; mm in 3 h |

Conversions:
- Wind × 3.6 → km/h (the CFFDRS 10 m open wind).
- RH clamped to 0-100.
- HRDPS rain = PR(t) − PR(t − 1 h) within one run (PR at the run start is 0), floored at 0.
- GDPS 3-hourly tail: rain = Accum3h(t1) / 3 for each hour of (t0, t1]. Temperature, RH and wind
  are interpolated linearly between t0 and t1, and direction through its unit vector.

These are the layers and conversions Pyra verified (Pyra `docs/data-sources.md`, 2026-10-29),
re-checked live on 2026-10-30.

### Time handling

- Hours are UTC hour stamps (`YYYY-MM-DDTHH:00`). Values are instantaneous at the stamp.
  Precipitation is the total of the **hour ending at the stamp**. This is Open-Meteo's
  convention, so `forecastStream`, the noon-LST sampling and the 24 h rain sum (`noon − 23 h …
  noon`, i.e. (noon − 24 h, noon]) are unchanged.
- **Which model supplies each hour** (`src`, one character per hour, returned by the API):

| Code | Source | When |
|---|---|---|
| `H` | HRDPS, newest run | forecast hours to +48 h |
| `h` | HRDPS, newest earlier run with a 1-6 h lead | past hours ("history") |
| `r` | RDPS, earlier run, lead 1-6 h | history when HRDPS has none |
| `b` | GDPS, earlier run, lead 1-12 h | history older than every HRDPS/RDPS run GeoMet keeps |
| `R` | RDPS, newest run | +48 to +84 h (or when HRDPS is down) |
| `G` | GDPS, newest run, hourly | to +84 h when RDPS is down |
| `g` | GDPS, newest run, 3-hourly interpolated | beyond the RDPS horizon, to +240 h |
| `-` | no data (null) | before the oldest run kept, beyond +240 h, or a failed request |

- RDPS is preferred over GDPS wherever both have the hour. GDPS fills an hour only where no
  HRDPS or RDPS run covers it.

### Which tier feeds a run (live check 2026-10-30 ~11:10 UTC, Edmonton)

The evening FFMC spin-up starts at the most recent 17:00 local before the start
(`skillOptions.spinupFromMs`, engine `diurnal.hourly_for_run`), so it reaches at most 24 h back:

| Run | Spin-up hours | Run hours |
|---|---|---|
| Start now (05:15 MDT), 24 h | `h8H4` | `H25` |
| Start 14:00 MDT today, 72 h | `h8H13` | `H35 R36 g2` |
| Start 14:00 MDT tomorrow, 72 h | `H21` | `H11 R36 g26` |
| Hindcast 14:00 MDT yesterday, 12 h | `h21` | `h11 H2` |
| Hindcast 08:00 MDT yesterday, 12 h | `h15` | `h13` |

For runs started today or later, the spin-up and the first run hours are **always HRDPS**.
GDPS reaches only:
- the tail of long runs, beyond RDPS's +84 h (3-hourly `g`), and
- hindcast hours older than every HRDPS/RDPS run (`b`), about 30-36 h back.

In both cases there is no finer run to prefer. The app says so in the forecast message, e.g.
"· 26 h from the 15 km global model GDPS: coarser, wind can differ", and counts the spin-up
hours separately.

## What GeoMet keeps, and what it cannot match

- GeoMet keeps about **6 HRDPS/RDPS runs (~30 h) and 4 GDPS runs (~36 h)**. Older runs return
  an empty coverage. Past hours therefore reach back about **39-51 h**, depending on when the
  newest GDPS run was published. Open-Meteo's `past_days=1` reached 00 UTC of the previous day
  (24-48 h), and `past_days=2` reached 48-72 h.
- **Hindcasts:**
  - A start whose hour GeoMet no longer holds fails with a plain message ("no model weather
    for … UTC: ECCC GeoMet keeps only about 1.5-2 days of past model runs"). The run then uses
    the constant weather in the panel.
  - Spin-up hours older than that are dropped, and the spin-up is reported as skipped.
  - Before this change, a start older than Open-Meteo's data silently began at the first hour
    Open-Meteo had.
- **The oldest past hours (`b`) are the 15 km GDPS.** They agree less with Open-Meteo, which had
  kept HRDPS-based values for those hours. At Edmonton on 2026-10-28 13-18 UTC: wind 18-19 km/h
  (GDPS) vs 7.9 km/h (Open-Meteo). The HRDPS run for a later hour gave 5.6 km/h and GDPS
  12 km/h, so this is the coarse grid, not a unit error. These hours feed only hindcasts more
  than ~1.5 days old and, in the gap below, the oldest rain hours of the cold start.
- **Cold-start gap:**
  - The cold start uses the latest noon LST at or before now. Its 24 h rain window reaches up
    to 47 h back just before noon LST.
  - About **16-19 UTC in Alberta (10:00-13:00 MDT)**, after the 12Z GDPS run appears and
    before noon LST, the window's oldest hours are older than every run GeoMet keeps.
  - Fallback order for the codes in that window:
    1. A CWFIS station with codes (tier 2, unchanged; in season these exist and the gap does
       not arise).
    2. The nearest station's series in **Pyra's `data/gem/ab.json`** (48 h of history),
       Alberta box, within 50 km. The label says "from Pyra's GEM series for <station> (<d>
       km; …), GeoMet no longer holds these hours at the point".
    3. **No codes**, with a plain message: "… has no precipitation for N of the 24 h to noon
       LST <date> (GeoMet keeps about 1.5-2 days of model runs); no Pyra GEM series within 50
       km covers it either — FWI codes are unavailable until noon LST has passed (or enter
       station codes)". CWFIS station weather is still shown when there is any.
  - The Pyra tier (Alberta, pre-noon) is not affected: it uses today's noon, which is recent.
  - Tests: `api/tests/test_weather.py::TestWeatherPath::test_gap_window_*`,
    `test_after_noon_lst_geomet_covers_the_window`.
- **Grid sampling:** FireSim takes the model cell containing the point (nearest neighbour).
  Pyra samples bilinearly. Open-Meteo also adjusts temperature for the difference between the
  cell's and the point's elevation. In flat country the differences are a few tenths. In
  mountains they are larger (see the BC rows below).
- **Time zone without a CWFIS station:** Open-Meteo returned the point's zone (`timezone=auto`),
  which was used for noon LST when CWFIS named no province. GeoMet has no zone. A point in
  Pyra's Alberta box now gets Alberta's rule, and elsewhere the province table / CWFIS station
  table / longitude as before.

## Spot check: GeoMet vs Open-Meteo `gem_seamless` (2026-10-30)

Run 2026-10-30 05:25 and 11:10 UTC with throwaway scripts outside the repo (`scripts/` is out
of scope for this change). Each script called `services/geomet.hourly` and the Open-Meteo
forecast API (`models=gem_seamless`, `past_days=2`, `timezone=UTC`, `wind_speed_unit=kmh`) for
the same UTC hours, and `FWICalculator` / `hourly_ffmc` on both. Open-Meteo was called only for
this comparison, never by the product. Noon LST = 19 UTC. Each cell is GeoMet / Open-Meteo.
Open-Meteo rounds to 0.1 °C, 1 % RH, 0.1 km/h, 1° and 0.1 mm. Source codes are those of the
noon hour and the 24 h window.

| Point | Noon LST | Src (noon; window) | T °C | RH % | Wind km/h | Dir ° | Rain 24 h mm | Cold-start FFMC | DMC | DC | ISI | FWI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Edmonton 53.55 N 113.50 W | 2026-10-29 | h; h24 | −1.8 / −1.8 | 76.7 / 77 | 2.7 / 2.5 | 328 / 328 | 1.8 / 1.9 | 63.64 / 62.56 | 4.92 / 4.79 | 15.39 / 15.38 | 0.57 / 0.54 | 0.25 / 0.24 |
| Edmonton | 2026-10-30 | H; h5 H19 | −1.6 / −1.7 | 76.0 / 76 | 5.0 / 5.0 | 170 / 170 | 0.0 / 0.0 | 82.98 / 82.97 | 6.00 / 6.00 | 15.42 / 15.40 | 2.07 / 2.07 | 0.97 / 0.97 |
| Edmonton | 2026-10-31 | H; H24 | −2.1 / −2.2 | 77.9 / 78 | 2.0 / 2.2 | 209 / 209 | 0.0 / 0.0 | 82.91 / 82.88 | 6.00 / 6.00 | 15.32 / 15.31 | 1.77 / 1.78 | 0.83 / 0.83 |
| Fort McMurray 56.65 N 111.22 W | 2026-10-29 | h; h24 | −2.2 / −2.4 | 70.8 / 71 | 4.0 / 4.0 | 270 / 270 | 1.8 / 1.7 | 65.50 / 66.34 | 5.01 / 5.16 | 15.31 / 15.27 | 0.66 / 0.68 | 0.29 / 0.31 |
| Fort McMurray | 2026-10-30 | H; h5 H19 | 0.3 / 0.1 | 69.4 / 69 | 4.6 / 4.7 | 213 / 213 | 0.0 / 0.0 | 83.75 / 83.77 | 6.07 / 6.06 | 15.77 / 15.72 | 2.25 / 2.26 | 1.30 / 1.32 |
| Fort McMurray | 2026-10-31 | H; H24 | 1.6 / 1.3 | 73.5 / 73 | 4.1 / 4.0 | 203 / 203 | 0.0 / 0.0 | 83.39 / 83.44 | 6.11 / 6.10 | 15.98 / 15.94 | 2.09 / 2.09 | 0.99 / 0.99 |
| Grande Prairie 55.18 N 118.88 W | 2026-10-29 | h; h24 | −2.9 / −2.9 | 73.5 / 74 | 4.1 / 4.0 | 161 / 161 | 1.0 / 1.0 | 75.59 / 75.46 | 6.00 / 6.00 | 15.20 / 15.20 | 0.97 / 0.96 | 0.46 / 0.45 |
| Grande Prairie | 2026-10-30 | H; h5 H19 | −3.2 / −3.1 | 67.3 / 67 | 3.5 / 3.6 | 147 / 147 | 0.0 / 0.0 | 83.83 / 83.85 | 6.00 / 6.00 | 15.20 / 15.20 | 2.14 / 2.16 | 1.03 / 1.10 |
| Grande Prairie | 2026-10-31 | H; H24 | −0.6 / −0.5 | 64.1 / 64 | 4.2 / 4.3 | 168 / 168 | 0.0 / 0.0 | 84.19 / 84.20 | 6.03 / 6.03 | 15.60 / 15.61 | 2.33 / 2.35 | 1.42 / 1.44 |
| Waterton/Cardston AB 49.2 N 113.5 W (wettest AB noon in the window) | 2026-11-02 | g; R23 g1 | 6.5 / 7.1 | 69.2 / 68 | 6.4 / 6.1 | 228 / 229 | 9.41 / 9.3 | 40.10 / 40.89 | 2.89 / 2.94 | 2.72 / 3.01 | 0.05 / 0.06 | 0.02 / 0.02 |
| Wild Horse AB 49.2 N 110.5 W (windiest AB noon in the window) | 2026-11-02 | g; R23 g1 | 5.3 / 5.4 | 71.3 / 70 | 20.3 / 20.9 | 304 / 304 | 6.16 / 6.2 | 49.47 / 50.11 | 3.10 / 3.11 | 7.89 / 7.83 | 0.43 / 0.48 | 0.15 / 0.17 |
| High Level area AB 59.5 N 116.5 W (windy) | 2026-10-31 | H; H24 | 0.4 / 0.4 | 57.8 / 58 | 20.4 / 20.5 | 208 / 208 | 0.0 / 0.0 | 84.70 / 84.68 | 6.10 / 6.10 | 15.78 / 15.78 | 5.65 / 5.67 | 4.80 / 4.82 |
| Creston area **BC** 49.2 N 116.5 W (wet, mountains) | 2026-10-29 | h; h24 | 2.0 / 0.0 | 84.8 / 82 | 2.2 / 2.5 | 188 / 143 | 10.1 / 13.5 | 27.56 / 25.44 | 2.62 / 2.46 | 1.78 / 0.70 | 0.00 / 0.00 | 0.00 / 0.00 |
| Selkirks **BC** 51.5 N 118.0 W (wet, mountains) | 2026-10-29 | h; h24 | −2.9 / −6.6 | 71.9 / 76 | 1.4 / 1.8 | 263 / 194 | 9.5 / 10.8 | 30.22 / 26.80 | 2.58 / 2.52 | 1.84 / 0.20 | 0.00 / 0.00 | 0.00 / 0.00 |

The cold-start codes are one day from 85/6/15 with each source's noon weather and 24 h rain.

All hours both sources cover (2026-10-28 13 UTC to 10-31 23 UTC, 83 h per point), mean absolute
difference by source code:

| Point | Code (hours) | T °C | RH % | Wind km/h | Dir ° | Rain mm/h |
|---|---|---|---|---|---|---|
| Edmonton | H (47) · h (30) · b (6) | 0.13 · 0.11 · 0.17 | 0.42 · 0.40 · 2.3 | 0.11 · 0.16 · **10.5** | 0.8 · 5.4 · 2.8 | 0.00 · 0.02 · 0.15 |
| Fort McMurray | H · h · b | 0.22 · 0.23 · 0.76 | 0.46 · 0.33 · 1.6 | 0.09 · 0.16 · 1.1 | 0.3 · 1.3 · 14.5 | 0.00 · 0.02 · 0.18 |
| Grande Prairie | H · h · b | 0.03 · 0.06 · 0.38 | 0.36 · 0.38 · 4.9 | 0.13 · 0.17 · 1.6 | 0.9 · 1.2 · 13.3 | 0.00 · 0.02 · 0.11 |

### Does anything change numerically?

Yes, slightly. The model is the same GEM, and FireSim now reads ECCC's values directly instead
of Open-Meteo's processed copy.

- **Noon-LST cold-start codes**, Alberta points:
  - |ΔFFMC| ≤ 1.08: the largest at Edmonton on 10-29, where rain was 1.8 vs 1.9 mm on a wet
    day. Wet days change most because FFMC wetting is steep around 1-10 mm.
  - |ΔDMC| ≤ 0.15, |ΔDC| ≤ 0.29, |ΔISI| ≤ 0.05, |ΔFWI| ≤ 0.07.
  - BC mountain points (different cell elevation): ΔFFMC up to 3.4 and ΔDC up to 1.6, at FWI 0
    in both.
- **Hourly FFMC / ISI path** (engine `hourly_ffmc` over 48 h from FFMC 85, 2026-10-29 20 UTC on,
  each source's hourly stream): |ΔFFMC| ≤ 0.05 (mean ≤ 0.02), |ΔISI| ≤ 0.15 (mean ≤ 0.011) at
  the three Alberta points.
- **Pyra tier:** equal to Pyra's page by construction (same file). Pyra's own switch changed its
  numbers by the amounts in Pyra's record.

### Limits of the spot check

- The window (2026-10-28 to 11-02) is late October in Alberta. It holds **no Alberta noon with ≥
  10 mm in 24 h** (wettest 9.4 mm, Waterton/Cardston, Nov 2) and **no noon with ≥ 30 km/h
  anywhere in Alberta** (windiest 20.4-20.9 km/h). Grid of 77 points, 49.2-59.5 N, 119.5-110.5
  W, Open-Meteo screen then GeoMet.
- The two ≥ 10 mm rows are in BC mountains, where the cell-elevation difference dominates.
- Summer convective rain and strong-wind days are not covered. Repeat the check in fire season
  (§6 open item). It can only be done for dates inside GeoMet's ~1.5-2-day window, so it has to
  be run live on such a day.

## Known differences from Open-Meteo `gem_seamless`

1. Nearest model cell, no elevation adjustment (Open-Meteo adjusts temperature for elevation).
   Larger differences in mountains.
2. Oldest past hours come from GDPS (15 km), not HRDPS. History reach is about 39-51 h.
3. The response's `weather_model` is `eccc_gem_geomet` (was `gem_seamless`), and the label is
   "ECCC GEM (GeoMet)".
4. A failed GeoMet request leaves that hour null (`src` `-`). It is never filled from another
   hour. A null at or after a run's start fails the hourly stream with its reason (constant
   panel weather is used).
5. The ensemble's input-error climatology (`scripts/validation/input_errors.py`) was measured on
   Open-Meteo's GEM previous-runs archive. It is the same model family, and it was not
   re-measured. GeoMet keeps no history to re-measure on, so the ensemble keeps its "calibrated on
   Open-Meteo GEM errors" provenance. The proposal for the owner is a small daily GeoMet archive of
   noon-LST inputs at the Alberta points, re-measured once a season has accumulated.
6. No elevation (lapse-rate) adjustment: negligible in Alberta's product scope, but it matters for any
   BC or mountain expansion (BC mountain ΔFFMC up to 3.4 above).
