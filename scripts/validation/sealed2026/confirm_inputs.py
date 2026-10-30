"""Build FireSim harness inputs for the sealed 2026 US `confirm` subset (docs/PREREG_SEALED_CONFIRM_H1H2.md).

Purpose: turn each sealed 2026 US fire (SEALED_2026 `confirm`, orders 1-16) into the inputs the FireSim
validation harness (``scripts/validate.py run``) already reads for Alberta CFSDS fires, with every choice fixed
by the pre-registration and applied blind (no model output is looked at while building):

- **Day of burning (DOB) grid** in place of CFSDS: NASA FEDS (Fire Event Data Suite; Chen et al. 2022,
  Sci. Data 9:249, doi:10.1038/s41597-022-01343-0) near-real-time "large fire" new-fire-pixel layer
  (VIIRS 375 m active fires, S-NPP / NOAA-20 / NOAA-21, grouped into fire objects every 12 h), read from the
  open NASA VEDA OGC API (``public.eis_fire_lf_newfirepix_nrt``; no key). Each FEDS time step ``t`` is a
  local-solar date plus 00:00 (night overpass, ~01:30) or 12:00 (day overpass, ~13:30) [checked against
  FIRMS acquisition times on the 2021 Dixie fire, a spent fire, before registration]. A pixel's burn day is
  the date of ``t - 12 h``: the day overpass of day D and the following night overpass (D+1 00:00) both
  belong to burn day D (06:00 D to 06:00 D+1, the harness's scored day). Pixels inside the sealed WFIGS
  final perimeter (buffered by 375 m, EPSG:5070) with burn day from discovery - 1 d to end + 1 d (local
  dates) are kept, whatever their FEDS fire ID. Every grid cell whose centre lies inside the final perimeter
  takes the burn day of the nearest kept pixel (nearest neighbour; Parks 2014, IJWF 23:215); other cells 0.
- **Grid:** regular lat/lng (FireSim ``FuelGrid`` convention), the perimeter bounds + 15 km, 90 m cells, or
  the first of 180 / 270 / 360 m keeping <= 1,200,000 cells, else 450 m (``validate.choose_res`` rule).
- **Fuel:** LANDFIRE LF2025 FBFM40 CONUS (30 m, public domain; disturbances through 2025, i.e. pre-fire for
  2026), read from the LANDFIRE LFPS image service resampled (nearest) onto the grid, crosswalked to FBP as
  in the embers v7/v8 US inputs (``scripts/validation/v8/inputs.py`` on ``feat/long-range-embers-v8``):
  grass GR/GS (101-109, 121-124) -> O-1b, shrub SH (141-149) -> C-4, timber TU/TL/SB (161-165, 181-189,
  201-204) -> C-7, urban NB1 (91) -> O-1b, water NB8 (98) -> water, other non-burnable / no data ->
  non-fuel. Codes are the CIFFC national scheme ``cfs_national`` (32, 4, 7, 102, 101).
- **Elevation:** LANDFIRE LF2020 Elev CONUS (30 m, metres), bilinear onto the grid (slope/aspect by the
  harness's own ``terrain_grid_for``).
- **Hourly weather:** ERA5 at the grid centre via the Open-Meteo archive (as the Alberta harness,
  ``firesim.validation.weather.fetch_era5_hourly``), in the fire's local clock time (Open-Meteo
  ``timezone=auto``, the IANA zone of the point); cached under ``weather/`` with the exact key
  ``validate.py run`` asks for, so the runs read local-time hours.
- **Daily FWI codes** (the CFSDS-row stand-in, ``cfsds/groups/Firegrowth_groups_v1_1_2026.csv``): ERA5 hourly
  at the grid centre (UTC), noon local standard time (UTC 12:00 minus the zone's standard offset), 24 h rain
  to noon, ``firesim.fwi.calculator.FWICalculator`` from 1 April 2026 (or 30 days before discovery if
  earlier) with start values FFMC 85, DMC 6, DC 15 (standard start-up; v8 addendum A2).
- **Fire-days:** ``firesim.validation.cfsds.fire_day_pairs`` (harness defaults: burning on day - 1, growth
  touching the earlier area, >= 1 growth cell); at most 8 per fire, a seeded random sample
  (``random.Random(f"2026102501:{fire_id}")``) when a fire has more.
- Fire IDs are anonymous: ``2026_C<order>`` (e.g. ``2026_C07``); names stay in the sealed list.

Outputs (outside the repository), root ``$FIRESIM_VALIDATION_DATA/sealed2026-confirm`` (dev mode:
``sealed2026-dev``): ``domains/<fire_id>.npz``, ``manifest.json``, ``cfsds/groups/...2026.csv``,
``weather/`` (Open-Meteo cache), ``inputs/<fire_id>.json`` (per-fire build record: sources, counts, hashes),
``build_summary.json``.

Usage (sealed list opened only after the pre-registration is pushed; the list sha256 is checked first):
    PYTHONPATH=engine/src python3 scripts/validation/sealed2026/confirm_inputs.py confirm
    PYTHONPATH=engine/src python3 scripts/validation/sealed2026/confirm_inputs.py dev WFIGS:<key> ...
``dev`` builds non-sealed 2026 fires (rows of ``eligibility_log.csv`` removed by rules 2, 5-not-complete or
6; never ``rule5_no_end_time``) from the live WFIGS layer, for pipeline checks before registration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

HOME = Path.home() / "dev/wildfire"
VD = Path(os.environ.get("FIRESIM_VALIDATION_DATA", HOME / "validation-data"))
SEALED = Path(os.environ.get("FIRESIM_SEALED2026_DIR", HOME / "validation-data/sealed2026"))
LIST_SHA = "b9c7a303c71e63ebc63e9fdf25cda18e8ebcaf61a5af36a3ac69cdd297748e80"
PERIM_SHA = "7f0c62a2c482a116f130fb30b3104ad455e41f736e30d736c5fd24c300f7ab74"
CONFIRM_ORDERS = range(1, 17)
YEAR = 2026
FEDS = "https://openveda.cloud/api/features/collections/public.eis_fire_lf_newfirepix_nrt/items"
LFPS = "https://lfps.usgs.gov/arcgis/rest/services/{svc}/ImageServer/exportImage"
FBFM_SVC = "Landfire_LF2025/LF2025_FBFM40_CONUS"
ELEV_SVC = "Landfire_Topo/LF2020_Elev_CONUS"
OPENMETEO = "https://archive-api.open-meteo.com/v1/archive"
WFIGS_YTD = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "WFIGS_Interagency_Perimeters_YearToDate/FeatureServer/0/query")
UA = {"User-Agent": "FireSim-sealed2026-confirm (open data research)"}
BUFFER_M = 15000.0
PIX_BUFFER_M = 375.0
MAX_CELLS = 1_200_000
MAX_DAYS = 8
DAY_SEED = 2026102501
DEV_RULES = {"rule2_outside_11_western_states", "rule5_not_complete", "rule6_initial_point_over_2km"}

# LANDFIRE FBFM40 -> CIFFC national FBP codes (embers v7/v8 crosswalk)
GROUP_OF_FBFM: dict[int, str] = {}
for _c in list(range(101, 110)) + list(range(121, 125)):
    GROUP_OF_FBFM[_c] = "G"
for _c in range(141, 150):
    GROUP_OF_FBFM[_c] = "S"
for _c in list(range(161, 166)) + list(range(181, 190)) + list(range(201, 205)):
    GROUP_OF_FBFM[_c] = "T"
GROUP_OF_FBFM[91] = "U"
NAT_OF_GROUP = {"G": 32, "S": 4, "T": 7, "U": 32}
WATER_FBFM, WATER_NAT, NONFUEL_NAT = 98, 102, 101


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def http(url: str, data: bytes | None = None, tries: int = 8, timeout: int = 300) -> bytes:
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA),
                                          timeout=timeout).read()
        except Exception as e:  # network: retry with back-off
            if k == tries - 1:
                raise
            time.sleep((60 if getattr(e, "code", None) == 429 else 5) * (k + 1))
    return b""


# ── fire records ─────────────────────────────────────────────────────────────────────────────

def _parse_ms(v):
    return None if v in (None, "") else datetime.utcfromtimestamp(float(v) / 1000.0)


def confirm_records() -> list[dict]:
    """The `confirm` subset (orders 1-16) from the sealed list and snapshot, sha256-checked first."""
    import geopandas as gpd

    lst = SEALED / "us/sealed_us_2026.csv"
    per = SEALED / "us/sealed_us_2026_perimeters.geojson"
    if sha256(lst) != LIST_SHA:
        raise SystemExit(f"sealed list sha256 mismatch: {lst}")
    if sha256(per) != PERIM_SHA:
        raise SystemExit(f"sealed perimeter snapshot sha256 mismatch: {per}")
    rows = [r for r in csv.DictReader(open(lst)) if int(r["order"]) in CONFIRM_ORDERS]
    assert all(r["subset"] == "confirm" for r in rows) and len(rows) == len(CONFIRM_ORDERS)
    g = gpd.read_file(per)
    g["fire_key"] = "WFIGS:" + g["attr_UniqueFireIdentifier"].astype(str)
    out = []
    for r in sorted(rows, key=lambda r: int(r["order"])):
        p = g[g.fire_key == r["fire_key"]].sort_values("poly_GISAcres", ascending=False)
        out.append(dict(fire_id=f"{YEAR}_C{int(r['order']):02d}", order=int(r["order"]),
                        fire_key=r["fire_key"], state=r["state"],
                        discovery_utc=datetime.strptime(r["discovery_utc"], "%Y-%m-%dT%H:%MZ"),
                        end_utc=datetime.strptime(r["end_utc"], "%Y-%m-%dT%H:%MZ"),
                        ha=float(r["ha"]), geometry=p.geometry.iloc[0], perimeter_source="sealed snapshot"))
    return out


def dev_record(key: str, k: int) -> dict:
    """A non-sealed 2026 fire (removed from the pool by rule 2, 5-not-complete or 6) from live WFIGS."""
    import geopandas as gpd

    log = {r["fire_key"]: r["rule"] for r in csv.DictReader(open(SEALED / "us/eligibility_log.csv"))}
    if log.get(key) not in DEV_RULES:
        raise SystemExit(f"{key}: not a readable removed record (dev mode allows only {sorted(DEV_RULES)})")
    q = {"where": f"attr_UniqueFireIdentifier = '{key.split(':', 1)[1]}'", "outFields": "*",
         "returnGeometry": "true", "outSR": 4326, "f": "geojson"}
    g = gpd.read_file(http(WFIGS_YTD, data=urllib.parse.urlencode(q).encode()).decode())
    g = g.sort_values("poly_GISAcres", ascending=False)
    r = g.iloc[0]
    ends = [_parse_ms(r.get(c)) for c in ("attr_ContainmentDateTime", "attr_ControlDateTime",
                                          "attr_FireOutDateTime")]
    ends = [e for e in ends if e is not None]
    disc = _parse_ms(r["attr_FireDiscoveryDateTime"])
    return dict(fire_id=f"{YEAR}_D{k:02d}", order=None, fire_key=key, state=r.get("attr_POOState"),
                discovery_utc=disc, end_utc=min(ends) if ends else disc + timedelta(days=60),
                ha=float(r["poly_GISAcres"]) * 0.40468564224, geometry=r.geometry,
                perimeter_source="live WFIGS (dev)")


# ── grid ─────────────────────────────────────────────────────────────────────────────────────

def make_grid(geom) -> dict:
    """Lat/lng grid: perimeter bounds + BUFFER_M, resolution by the validate.choose_res rule."""
    import geopandas as gpd
    from firesim.validation.cfsds import M_PER_DEG, domain_grid

    b = gpd.GeoSeries([geom], crs=4326).to_crs(5070).total_bounds
    h, w = (b[3] - b[1]) + 2 * BUFFER_M, (b[2] - b[0]) + 2 * BUFFER_M
    res = next((r for r in (90.0, 180.0, 270.0, 360.0) if (h / r) * (w / r) <= MAX_CELLS), 450.0)
    lng_min, lat_min, lng_max, lat_max = geom.bounds
    mid = 0.5 * (lat_min + lat_max)
    blat = BUFFER_M / M_PER_DEG
    blng = BUFFER_M / (M_PER_DEG * math.cos(math.radians(mid)))
    lat_min, lat_max, lng_min, lng_max = lat_min - blat, lat_max + blat, lng_min - blng, lng_max + blng
    rows, cols, cell_lat, cell_lng = domain_grid(lat_min, lat_max, lng_min, lng_max, res)
    return dict(res_m=res, rows=rows, cols=cols, cell_lat=cell_lat, cell_lng=cell_lng,
                lat_max=lat_max, lng_min=lng_min)


def cell_centres(g: dict) -> tuple[np.ndarray, np.ndarray]:
    lat = g["lat_max"] - (np.arange(g["rows"]) + 0.5) * g["cell_lat"]
    lng = g["lng_min"] + (np.arange(g["cols"]) + 0.5) * g["cell_lng"]
    return lat, lng


def lfps_export(svc: str, g: dict, interp: str, step: int = 2000) -> np.ndarray:
    """A LANDFIRE image-service layer on the lat/lng grid (EPSG:4326 export, tiled)."""
    import rasterio

    out = np.zeros((g["rows"], g["cols"]), np.float32)
    for j0 in range(0, g["rows"], step):
        for i0 in range(0, g["cols"], step):
            h, w = min(step, g["rows"] - j0), min(step, g["cols"] - i0)
            x0 = g["lng_min"] + i0 * g["cell_lng"]
            y1 = g["lat_max"] - j0 * g["cell_lat"]
            bb = (x0, y1 - h * g["cell_lat"], x0 + w * g["cell_lng"], y1)
            q = {"bbox": ",".join(f"{v:.8f}" for v in bb), "bboxSR": 4326, "imageSR": 4326,
                 "size": f"{w},{h}", "format": "tiff", "pixelType": "F32", "interpolation": interp,
                 "f": "image"}
            data = http(LFPS.format(svc=svc) + "?" + urllib.parse.urlencode(q))
            with rasterio.MemoryFile(data) as mf, mf.open() as ds:
                a = ds.read(1)
                if a.shape != (h, w):
                    raise RuntimeError(f"{svc}: export shape {a.shape} != {(h, w)}")
                nod = ds.nodata
            if nod is not None:
                a = np.where(a == nod, np.nan, a)
            out[j0:j0 + h, i0:i0 + w] = a
    return out


def crosswalk(fb: np.ndarray) -> np.ndarray:
    fbi = np.where(np.isfinite(fb), fb, -1).astype(np.int32)
    nat = np.full(fb.shape, NONFUEL_NAT, np.int32)
    for c, grp in GROUP_OF_FBFM.items():
        nat[fbi == c] = NAT_OF_GROUP[grp]
    nat[fbi == WATER_FBFM] = WATER_NAT
    return nat


# ── DOB from FEDS ────────────────────────────────────────────────────────────────────────────

def feds_pixels(geom, d0: date, d1: date, cache: Path) -> list[tuple[float, float, str, str]]:
    """FEDS NRT new-fire pixels (lon, lat, t, fireid) in the perimeter's box for t dates d0-2 .. d1+2."""
    if cache.exists():
        return [tuple(x) for x in json.loads(cache.read_text())]
    lng_min, lat_min, lng_max, lat_max = geom.bounds
    pad = 0.05
    q = {"bbox": f"{lng_min - pad:.5f},{lat_min - pad:.5f},{lng_max + pad:.5f},{lat_max + pad:.5f}",
         "datetime": f"{d0 - timedelta(days=2)}T00:00:00Z/{d1 + timedelta(days=3)}T00:00:00Z",
         "limit": 500}
    url = FEDS + "?" + urllib.parse.urlencode(q)
    pts = []
    while url:
        d = json.loads(http(url))
        for f in d.get("features", []):
            p = f["properties"]
            geo = f["geometry"]
            coords = geo["coordinates"] if geo["type"] == "MultiPoint" else [geo["coordinates"]]
            pts += [(float(x), float(y), p["t"], str(p.get("fireid"))) for x, y in coords]
        url = next((ln["href"] for ln in d.get("links", []) if ln.get("rel") == "next"), None)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(pts))
    return pts


def burn_date(t: str) -> date:
    """FEDS step (local-solar date + 00:00 night / 12:00 day overpass) -> burn day: date of t - 12 h."""
    return (datetime.fromisoformat(t) - timedelta(hours=12)).date()


def dob_grid(geom, g: dict, pts, d0: date, d1: date) -> tuple[np.ndarray, dict]:
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy.spatial import cKDTree
    from shapely import contains_xy

    from firesim.validation.cfsds import M_PER_DEG

    buf = gpd.GeoSeries([geom], crs=4326).to_crs(5070).buffer(PIX_BUFFER_M).to_crs(4326).iloc[0]
    lo, hi = d0 - timedelta(days=1), d1 + timedelta(days=1)
    keep = [(x, y, burn_date(t), f) for x, y, t, f in pts if lo <= burn_date(t) <= hi]
    stats = {"feds_pixels_in_box": len(pts), "feds_pixels_in_window": len(keep)}
    if keep:
        xy = np.array([(k[0], k[1]) for k in keep])
        inside = contains_xy(buf, xy[:, 0], xy[:, 1])
        keep = [k for k, ok in zip(keep, inside) if ok]
    stats["feds_pixels_kept"] = len(keep)
    stats["feds_fireids_kept"] = sorted({k[3] for k in keep})
    tr = from_origin(g["lng_min"], g["lat_max"], g["cell_lng"], g["cell_lat"])
    inside = rasterize([(geom, 1)], out_shape=(g["rows"], g["cols"]), transform=tr, fill=0,
                       all_touched=False, dtype="uint8").astype(bool)
    dob = np.zeros((g["rows"], g["cols"]), np.int32)
    stats["perimeter_cells"] = int(inside.sum())
    if not keep or not inside.any():
        return dob, stats
    coslat = math.cos(math.radians(g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]))
    kx = np.array([k[0] for k in keep]) * coslat * M_PER_DEG
    ky = np.array([k[1] for k in keep]) * M_PER_DEG
    kd = np.array([k[2].timetuple().tm_yday for k in keep], np.int32)
    lat, lng = cell_centres(g)
    rr, cc = np.nonzero(inside)
    q = np.column_stack([lng[cc] * coslat * M_PER_DEG, lat[rr] * M_PER_DEG])
    dist, idx = cKDTree(np.column_stack([kx, ky])).query(q)
    dob[rr, cc] = kd[idx]
    stats["nearest_pixel_m"] = {"median": float(np.median(dist)), "p95": float(np.percentile(dist, 95)),
                                "max": float(dist.max())}
    stats["burn_days"] = [int(v) for v in np.unique(kd)]
    return dob, stats


# ── weather and FWI ──────────────────────────────────────────────────────────────────────────

def openmeteo(lat: float, lng: float, start: date, end: date, tz: str, cache: Path) -> dict:
    if cache.exists():
        return json.loads(cache.read_text())
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}", "start_date": start.isoformat(),
         "end_date": end.isoformat(), "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,"
         "wind_direction_10m,precipitation", "timezone": tz, "models": "era5", "wind_speed_unit": "kmh"}
    d = json.loads(http(OPENMETEO + "?" + urllib.parse.urlencode(q)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d))
    return d


def daily_fwi(fire_id: str, lat: float, lng: float, tz: str, disc: date, last: date, root: Path):
    """CFSDS-style rows (ID, DOB, ffmc, dmc, dc, isi, bui, fwi, ...) from ERA5 at noon LST."""
    from firesim.fwi.calculator import FWICalculator

    start = min(date(YEAR, 4, 1), disc - timedelta(days=30))
    end = last + timedelta(days=1)
    d = openmeteo(lat, lng, start, end, "GMT", root / "inputs" / f"{fire_id}_era5_utc.json")
    h = d["hourly"]
    by = {datetime.fromisoformat(t): i for i, t in enumerate(h["time"])}
    z = ZoneInfo(tz)
    calc = FWICalculator()
    rows = []
    day = start
    while day <= end:
        loc = datetime(day.year, day.month, day.day, 12, tzinfo=z)
        std_off = loc.utcoffset() - (loc.dst() or timedelta(0))
        noon = datetime(day.year, day.month, day.day, 12) - std_off  # UTC of local standard noon
        i = by.get(noon)
        vals = None if i is None else [h[v][i] for v in ("temperature_2m", "relative_humidity_2m",
                                                         "wind_speed_10m")]
        rain = [h["precipitation"][by[noon - timedelta(hours=k)]] for k in range(24)
                if noon - timedelta(hours=k) in by] if i is not None else []
        if vals is None or any(v is None for v in vals):
            raise RuntimeError(f"{fire_id}: ERA5 missing at {noon} UTC")
        r = calc.calculate_daily(float(vals[0]), float(vals[1]), float(vals[2]),
                                 float(sum(x or 0.0 for x in rain)), day.month)
        rows.append({"ID": fire_id, "DOB": day.timetuple().tm_yday, "date": day.isoformat(),
                     "ffmc": r.ffmc, "dmc": r.dmc, "dc": r.dc, "isi": r.isi, "bui": r.bui, "fwi": r.fwi,
                     "temp_noon": vals[0], "rh_noon": vals[1], "ws_noon": vals[2],
                     "rain24": float(sum(x or 0.0 for x in rain)), "lat": lat, "lon": lng})
        day += timedelta(days=1)
    return rows, {"fwi_start": start.isoformat(), "fwi_start_values": [85.0, 6.0, 15.0],
                  "noon_utc_offset_h": -std_off.total_seconds() / 3600.0}


# ── build ────────────────────────────────────────────────────────────────────────────────────

def build_fire(rec: dict, root: Path) -> dict:
    from firesim.validation.cfsds import FireDomain, fire_day_pairs
    from firesim.validation.start_strata import start_type
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    fid = rec["fire_id"]
    t0 = time.time()
    info = {"fire_id": fid, "order": rec["order"], "perimeter_source": rec["perimeter_source"],
            "state": rec["state"], "final_ha_record": rec["ha"]}
    g = make_grid(rec["geometry"])
    info["grid"] = {k: v for k, v in g.items()}
    clat = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
    clng = g["lng_min"] + 0.5 * g["cols"] * g["cell_lng"]
    # local zone of the grid centre (Open-Meteo timezone=auto on a one-day request)
    tzd = openmeteo(clat, clng, date(YEAR, 7, 1), date(YEAR, 7, 1), "auto",
                    root / "inputs" / f"{fid}_tz.json")
    tz = tzd["timezone"]
    z = ZoneInfo(tz)
    from datetime import timezone as _tz
    d0 = rec["discovery_utc"].replace(tzinfo=_tz.utc).astimezone(z).date()
    d1 = rec["end_utc"].replace(tzinfo=_tz.utc).astimezone(z).date()
    info.update(timezone=tz, discovery_local=d0.isoformat(), end_local=d1.isoformat())
    pts = feds_pixels(rec["geometry"], d0, d1, root / "inputs" / f"{fid}_feds.json")
    dob, st = dob_grid(rec["geometry"], g, pts, d0, d1)
    info["dob"] = st
    if not (dob > 0).any():
        info["status"] = "no FEDS pixels inside the perimeter: contributes nothing"
        return info
    fb = lfps_export(FBFM_SVC, g, "RSP_NearestNeighbor")
    fuel = crosswalk(fb)
    elev = lfps_export(ELEV_SVC, g, "RSP_BilinearInterpolation")
    if np.isnan(elev).all():
        elev = None
    elif np.isnan(elev).any():
        elev = np.where(np.isnan(elev), np.nanmean(elev), elev).astype(np.float32)
    burned = dob > 0
    u, n = np.unique(fuel[burned], return_counts=True)
    info["fuel_share_in_perimeter"] = {str(int(a)): float(b) / burned.sum() for a, b in zip(u, n)}
    dom = FireDomain(fire_id=fid, year=YEAR, dob=dob, fuel=fuel, elevation=elev,
                     lat_max=g["lat_max"], lng_min=g["lng_min"], cell_lat=g["cell_lat"],
                     cell_lng=g["cell_lng"], fuel_scheme="cfs_national",
                     meta={"res_m": g["res_m"], "buffer_m": BUFFER_M,
                           "dob_source": "NASA FEDS NRT lf_newfirepix (VIIRS 375 m), nearest pixel "
                                         "inside the sealed WFIGS final perimeter",
                           "fuel_source": "LANDFIRE LF2025 FBFM40 -> FBP (v7/v8 crosswalk)",
                           "dem_source": "LANDFIRE LF2020 Elev CONUS", "timezone": tz})
    (root / "domains").mkdir(parents=True, exist_ok=True)
    dpath = root / "domains" / f"{fid}.npz"
    dom.save(dpath)
    pairs = fire_day_pairs(dom)
    days = [p.day for p in pairs]
    if len(days) > MAX_DAYS:
        days = sorted(random.Random(f"{DAY_SEED}:{fid}").sample(days, MAX_DAYS))
    burn_days = dom.burn_days()
    # hourly local-time weather, cached under the key validate.py run will ask for
    fetch_era5_hourly(clat, clng, doy_to_date(YEAR, burn_days[0]) - timedelta(days=1),
                      doy_to_date(YEAR, burn_days[-1]) + timedelta(days=2), root / "weather",
                      timezone=tz)
    rows, fwi_meta = daily_fwi(fid, clat, clng, tz, min(d0, doy_to_date(YEAR, burn_days[0])),
                               doy_to_date(YEAR, burn_days[-1]), root)
    tags = {d: start_type(dom.dob, d, dom.cell_area_m2)["start_type"] for d in days}
    info.update(status="ok", domain_sha256=sha256(dpath), eligible_pairs=len(pairs), days=days,
                start_types=tags, burn_days=[burn_days[0], burn_days[-1]], fwi=fwi_meta,
                seconds=round(time.time() - t0, 1))
    man = {"fire_id": fid, "year": YEAR, "area_ha": float(burned.sum()) * dom.cell_area_m2 / 1e4,
           "lat": clat, "lng": clng, "ndays": len(burn_days), "res_m": g["res_m"],
           "grid": [dom.rows, dom.cols], "eligible_days": len(pairs), "days": days,
           "weather_point": [clat, clng], "weather_range": [burn_days[0], burn_days[-1]],
           "timezone": tz}
    return {"info": info, "manifest": man, "groups": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("confirm")
    dv = sub.add_parser("dev")
    dv.add_argument("keys", nargs="+")
    a = ap.parse_args()
    if a.cmd == "confirm":
        root = VD / "sealed2026-confirm"
        recs = confirm_records()
    else:
        root = VD / "sealed2026-dev"
        recs = [dev_record(k, i) for i, k in enumerate(a.keys, 1)]
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs").mkdir(exist_ok=True)
    manifest, groups, summary = [], [], []
    for rec in recs:
        try:
            out = build_fire(rec, root)
        except Exception as e:  # recorded; a fire whose inputs cannot be built contributes nothing
            out = {"fire_id": rec["fire_id"], "order": rec["order"], "status": f"error {e!r}"[:300]}
        info = out.get("info", out)
        (root / "inputs" / f"{rec['fire_id']}.json").write_text(json.dumps(info, indent=1, default=str))
        if a.cmd == "dev":
            info["fire_key"] = rec["fire_key"]
        summary.append({k: info.get(k) for k in ("fire_id", "order", "status", "eligible_pairs", "days",
                                                  "start_types", "timezone", "seconds")})
        if "manifest" in out and out["manifest"]["days"]:
            manifest.append(out["manifest"])
            groups += out["groups"]
        print(rec["fire_id"], info.get("status"), "days", info.get("days"), flush=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1))
    gp = root / "cfsds/groups" / f"Firegrowth_groups_v1_1_{YEAR}.csv"
    gp.parent.mkdir(parents=True, exist_ok=True)
    with open(gp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(groups[0]) if groups else ["ID", "DOB"])
        w.writeheader()
        w.writerows(groups)
    (root / "build_summary.json").write_text(json.dumps(
        {"built": datetime.utcnow().isoformat() + "Z", "mode": a.cmd, "fires": summary,
         "manifest_sha256": sha256(root / "manifest.json"), "groups_sha256": sha256(gp),
         "n_fire_days": sum(len(m["days"]) for m in manifest)}, indent=1, default=str))
    print("fire-days:", sum(len(m["days"]) for m in manifest), "fires with days:", len(manifest))


if __name__ == "__main__":
    sys.exit(main())
