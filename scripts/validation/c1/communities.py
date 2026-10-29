"""Community arrival scoring for C1 (PREREG_COMPARATOR_C1 §6.2).

Community units are FireSim's own (the Neighbourhoods card outside Edmonton):
``frontend/public/alberta/places.geojson`` (hamlets, urban service areas, designated places;
CGNDB points as 250 m discs) plus the StatCan 2021 census subdivisions that are settlements
(city, town, village, summer village, Indian reserve, Metis settlement: types CY, T, VL, SV,
IRI, S-E). Rural municipalities (MD, ID, SM, SA) are not communities. Each unit's reach zone is
the unit buffered by 500 m (FireSim's "fire within 500 m" rule), rasterised with all_touched.

Per fire-day, a community is *eligible* if its reach zone lies (at least partly) in the working
area and does not touch the start area (it is not already reached at the start). Arrival:

- model: the earliest arrival minute over the zone's cells, censored at the 17 h window;
- observed: *reached* if any zone cell has CFSDS DOB = the burn day; its time is the first
  VIIRS 375 m detection (FIRMS archive, any confidence) inside the zone between the start and
  the end of the window (local time UTC-6); reached without a timed detection = "untimed"
  (excluded from the error, counted); not reached = censored at the window end.

Error = |min(a_model, T) - min(a_obs, T)| in hours, T = 17 h. A model arriving at a community
the fire never reached is charged the hours by which it was early; a missed arrival is charged
likewise. Fire-day value = mean over its eligible, timed communities.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
PLACES = REPO / "frontend/public/alberta/places.geojson"
CSDS = REPO / "frontend/public/alberta/census_subdivisions.geojson"
SETTLEMENT_CSD = ("CY", "T", "VL", "SV", "IRI", "S-É")
REACH_M = 500.0
POINT_RADIUS_M = 250.0
VIIRS_DIR = Path("/home/rpas/dev/wildfire/validation-data/viirs/raw")
UTC_OFFSET_H = -6


@lru_cache(maxsize=1)
def community_zones():
    """GeoDataFrame (EPSG:4326) of community reach zones with ``name``."""
    import geopandas as gpd
    import pandas as pd

    pl = gpd.read_file(PLACES).to_crs(3400)
    pl["geometry"] = [g.buffer(POINT_RADIUS_M) if g.geom_type == "Point" else g
                      for g in pl.geometry]
    cs = gpd.read_file(CSDS)
    cs = cs[cs["csdtype"].isin(SETTLEMENT_CSD)].to_crs(3400)
    z = pd.concat([pl[["name", "geometry"]], cs[["name", "geometry"]]]).reset_index(drop=True)
    z["geometry"] = z.buffer(REACH_M)
    return z.to_crs(4326)


def zone_masks(dom) -> list[tuple[str, np.ndarray]]:
    """(name, mask) of the reach zones that intersect the domain grid."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    z = community_zones()
    lat_min = dom.lat_max - dom.rows * dom.cell_lat
    sub = z.cx[dom.lng_min:dom.lng_min + dom.cols * dom.cell_lng, lat_min:dom.lat_max]
    tr = from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat)
    out = []
    for name, g in zip(sub["name"], sub.geometry):
        m = rasterize([(g, 1)], out_shape=(dom.rows, dom.cols), transform=tr, dtype=np.uint8,
                      all_touched=True).astype(bool)
        if m.any():
            out.append((name, m))
    return out


@lru_cache(maxsize=4)
def _viirs_year(year: int):
    import pandas as pd

    frames = []
    for p in sorted(VIIRS_DIR.glob(f"viirs-*_{year}_Canada.csv")):
        f = pd.read_csv(p, usecols=["latitude", "longitude", "acq_date", "acq_time"])
        f = f[(f.longitude > -120.5) & (f.longitude < -109.5) & (f.latitude > 48.5)]
        frames.append(f)
    if not frames:
        return None
    f = pd.concat(frames)
    t = pd.to_datetime(f["acq_date"]) + pd.to_timedelta(f["acq_time"] // 100, unit="h") + \
        pd.to_timedelta(f["acq_time"] % 100, unit="m")
    f = f.assign(local=t + pd.Timedelta(hours=UTC_OFFSET_H))
    return f[["latitude", "longitude", "local"]].reset_index(drop=True)


def first_detection_min(dom, mask: np.ndarray, start, window_h: float) -> float:
    """Minutes from ``start`` to the first VIIRS detection inside ``mask`` within the window
    (nan if none or no VIIRS file)."""
    import pandas as pd

    v = _viirs_year(dom.year)
    if v is None:
        return math.nan
    t0 = pd.Timestamp(start)
    t1 = t0 + pd.Timedelta(hours=window_h)
    sel = v[(v["local"] >= t0) & (v["local"] <= t1)]
    if sel.empty:
        return math.nan
    r = np.floor((dom.lat_max - sel["latitude"].to_numpy()) / dom.cell_lat).astype(int)
    c = np.floor((sel["longitude"].to_numpy() - dom.lng_min) / dom.cell_lng).astype(int)
    ok = (r >= 0) & (r < dom.rows) & (c >= 0) & (c < dom.cols)
    r, c = r[ok], c[ok]
    hit = mask[r, c]
    if not hit.any():
        return math.nan
    return float((sel["local"].to_numpy()[ok][hit].min() - np.datetime64(t0)) /
                 np.timedelta64(1, "m"))


def observed_arrivals(case, window_h: float = 17.0) -> list[dict]:
    """Eligible communities of a fire-day with their observed arrival (minutes or None)."""
    dom = case.domain
    prior = (dom.dob > 0) & (dom.dob < case.day)
    obs = dom.dob == case.day
    out = []
    for name, m in zone_masks(dom):
        if (m & prior).any():
            continue
        reached = bool((m & obs).any())
        t_obs = first_detection_min(dom, m, case.start, window_h) if reached else math.inf
        out.append({"name": name, "mask": m, "reached": reached,
                    "obs_min": t_obs})
    return out


def arrival_errors(comms: list[dict], arrival: np.ndarray, start_mask: np.ndarray,
                   window_h: float = 17.0) -> dict:
    """Community arrival errors (hours) of one model's arrival grid on one fire-day."""
    T = window_h * 60.0
    rows = []
    for c in comms:
        m = c["mask"]
        if (m & start_mask).any():
            continue  # reached at the start by this run's start area (not scored)
        a = float(np.min(arrival[m])) if m.any() else math.inf
        a = min(a, T)
        o = c["obs_min"]
        if c["reached"] and not math.isfinite(o):
            rows.append({"name": c["name"], "reached": True, "untimed": True, "model_h": a / 60})
            continue
        o = min(o, T)
        rows.append({"name": c["name"], "reached": c["reached"], "untimed": False,
                     "model_h": a / 60.0, "obs_h": o / 60.0, "err_h": abs(a - o) / 60.0,
                     "model_reached": a < T})
    timed = [r for r in rows if not r["untimed"]]
    return {"n_eligible": len(rows), "n_timed": len(timed),
            "n_obs_reached": sum(r["reached"] for r in rows),
            "n_untimed": sum(r["untimed"] for r in rows),
            "n_model_reached": sum(r.get("model_reached", False) for r in timed),
            "n_false_arrival": sum(r.get("model_reached", False) and not r["reached"]
                                   for r in timed),
            "mae_h": float(np.mean([r["err_h"] for r in timed])) if timed else math.nan,
            "rows": rows}
