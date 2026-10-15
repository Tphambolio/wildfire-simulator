#!/usr/bin/env python3
"""Long-range embers v6, data preparation for target A (ember transport and community arrival).

Purpose
    Build, for each CAL FIRE DINS fire used by the long-range ember work, a lat/lng run grid
    (~90 m) with the open inputs FireSim needs to run its own wildland front over the fire area:
    land-cover fractions (water, bare, built-up) and terrain (elevation, slope, aspect). No outcome
    (DINS damage, perimeters, VIIRS) is read here.

Inputs (open data, fetched by windowed HTTP range reads; nothing else is downloaded)
    - CAL FIRE FRAP final perimeters already on disk (``calfire-dins/raw/frap_perimeter_<fire>.geojson``,
      CC BY) -- used only to place the grid box (perimeter bounds + 4 km).
    - ESA WorldCover 10 m 2021 v200 (Zanaga et al. 2022, doi:10.5281/zenodo.7254221), CC BY 4.0,
      https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ . Only classes that do not
      change with a fire are used: 80 permanent water, 60 bare/sparse, 50 built-up (the 2021 map is
      post-fire for 2017-2020 fires; vegetation classes are not used).
    - USGS 3D Elevation Program 1 arc-second DEM (public domain, U.S. Geological Survey),
      https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/ .

Outputs (outside the repo)
    ``~/dev/wildfire/validation-data/embers-v6/grids/<fire>.npz``: lat_max, lng_min, cell_lat, cell_lng,
    rows, cols, h_m, f_water, f_bare, f_built (0-1 shares of 10 m pixels per cell), elev_m, slope_pct,
    aspect_deg (upslope azimuth, as ``firesim.data.dem_loader``); ``grids/provenance.json``.

Run (repo root; memory ~1-2 GB per fire, one fire at a time)
    python3 scripts/long_range_v6_data.py --fires camp eaton ...

Methods
    Slope/aspect: ``firesim.data.dem_loader.slope_aspect`` (central differences, as the engine).
    Status: data preparation only (spec §6.8, PROJECT_RECORD decision 2026-10-13).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))

CALFIRE = Path.home() / "dev" / "wildfire" / "validation-data" / "calfire-dins"
OUT = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v6" / "grids"
WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
DEM_URL = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/{tile}/USGS_1_{tile}.tif"
FIRES = ("eaton", "palisades", "camp", "tubbs", "woolsey", "glass", "czu", "northcomplex")
H_M = 90.0
PAD_M = 4000.0


def fire_bounds(fire: str):
    """Bounds of the main FRAP perimeter (parts within 30 km of the largest part)."""
    import shapely

    d = json.load(open(CALFIRE / "raw" / f"frap_perimeter_{fire}.geojson"))
    parts = []
    for f in d["features"]:
        g = shapely.geometry.shape(f["geometry"])
        parts += list(getattr(g, "geoms", [g]))
    big = max(parts, key=lambda p: p.area)
    c = big.centroid
    keep = [p for p in parts if p.centroid.distance(c) < 30_000 / 111_320]
    return shapely.union_all(keep).bounds


def grid_for(fire: str):
    lng0, lat0, lng1, lat1 = fire_bounds(fire)
    mid = 0.5 * (lat0 + lat1)
    mlng = 111_320.0 * math.cos(math.radians(mid))
    lat0 -= PAD_M / 111_320.0
    lat1 += PAD_M / 111_320.0
    lng0 -= PAD_M / mlng
    lng1 += PAD_M / mlng
    cell_lat, cell_lng = H_M / 111_320.0, H_M / mlng
    rows = int(math.ceil((lat1 - lat0) / cell_lat))
    cols = int(math.ceil((lng1 - lng0) / cell_lng))
    return {"lat_max": lat0 + rows * cell_lat, "lng_min": lng0, "cell_lat": cell_lat, "cell_lng": cell_lng,
            "rows": rows, "cols": cols, "h_m": H_M}


def _tiles(lat0, lat1, lng0, lng1, step, fmt):
    out = []
    for la in range(int(math.floor(lat0 / step) * step), int(math.ceil(lat1)), step):
        for lo in range(int(math.floor(lng0 / step) * step), int(math.ceil(lng1)), step):
            out.append(fmt(la, lo))
    return out


def worldcover_fractions(g):
    """Shares of 10 m WorldCover pixels per grid cell for classes 80 (water), 60 (bare), 50 (built)."""
    import rasterio
    from rasterio.windows import from_bounds

    rows, cols = g["rows"], g["cols"]
    lat_min = g["lat_max"] - rows * g["cell_lat"]
    lng_max = g["lng_min"] + cols * g["cell_lng"]
    cnt = {k: np.zeros(rows * cols) for k in ("n", 80, 60, 50)}
    tiles = _tiles(lat_min, g["lat_max"], g["lng_min"], lng_max, 3,
                   lambda la, lo: f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}")
    used = []
    for t in tiles:
        url = "/vsicurl/" + WC_URL.format(tile=t)
        try:
            src = rasterio.open(url)
        except Exception:  # tile outside coverage (ocean)
            continue
        with src:
            b = src.bounds
            w0, s0 = max(b.left, g["lng_min"]), max(b.bottom, lat_min)
            e0, n0 = min(b.right, lng_max), min(b.top, g["lat_max"])
            if w0 >= e0 or s0 >= n0:
                continue
            win = from_bounds(w0, s0, e0, n0, src.transform).round_offsets().round_lengths()
            a = src.read(1, window=win)
            tr = src.window_transform(win)
            used.append(t)
        r_lat = tr.f + (np.arange(a.shape[0]) + 0.5) * tr.e
        c_lng = tr.c + (np.arange(a.shape[1]) + 0.5) * tr.a
        rr = np.floor((g["lat_max"] - r_lat) / g["cell_lat"]).astype(np.int64)
        cc = np.floor((c_lng - g["lng_min"]) / g["cell_lng"]).astype(np.int64)
        okr, okc = (rr >= 0) & (rr < rows), (cc >= 0) & (cc < cols)
        a = a[okr][:, okc]
        idx = (rr[okr][:, None] * cols + cc[okc][None, :]).ravel()
        flat = a.ravel()
        valid = flat > 0
        cnt["n"] += np.bincount(idx[valid], minlength=rows * cols)
        for k in (80, 60, 50):
            cnt[k] += np.bincount(idx[flat == k], minlength=rows * cols)
        del a, flat, idx
    n = np.maximum(cnt["n"], 1)
    # cells with no WorldCover pixel (open ocean outside the tiles) count as water
    water = np.where(cnt["n"] > 0, cnt[80] / n, 1.0)
    return (water.reshape(rows, cols), (cnt[60] / n).reshape(rows, cols),
            (cnt[50] / n).reshape(rows, cols), used)


def dem(g):
    """Mean 3DEP 1" elevation per grid cell (bilinear onto cell centres of a 2x finer grid, averaged)."""
    import rasterio
    from rasterio.warp import Resampling, reproject
    from rasterio.transform import from_origin

    rows, cols = g["rows"], g["cols"]
    lat_min = g["lat_max"] - rows * g["cell_lat"]
    lng_max = g["lng_min"] + cols * g["cell_lng"]
    out = np.full((rows, cols), np.nan, dtype=np.float32)
    dst_tr = from_origin(g["lng_min"], g["lat_max"], g["cell_lng"], g["cell_lat"])
    used = []
    for la in range(int(math.floor(lat_min)), int(math.ceil(g["lat_max"]))):
        for lo in range(int(math.floor(g["lng_min"])), int(math.ceil(lng_max))):
            tile = f"n{la + 1:02d}w{abs(lo):03d}"
            try:
                src = rasterio.open("/vsicurl/" + DEM_URL.format(tile=tile))
            except Exception:
                continue
            with src:
                tmp = np.full((rows, cols), np.nan, dtype=np.float32)
                from rasterio.windows import from_bounds

                b = src.bounds
                w0, s0 = max(b.left, g["lng_min"]), max(b.bottom, lat_min)
                e0, n0 = min(b.right, lng_max), min(b.top, g["lat_max"])
                if w0 >= e0 or s0 >= n0:
                    continue
                win = from_bounds(w0, s0, e0, n0, src.transform).round_offsets().round_lengths()
                a = src.read(1, window=win).astype(np.float32)
                a[a < -1000] = np.nan
                reproject(a, tmp, src_transform=src.window_transform(win), src_crs=src.crs, dst_transform=dst_tr,
                          dst_crs="EPSG:4326", resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
                used.append(tile)
            out = np.where(np.isfinite(tmp), tmp, out)
    out = np.where(np.isfinite(out), out, 0.0)  # ocean
    return out, used


def slope_aspect_m(elev, dx, dy):
    """Percent slope and upslope aspect (deg from north), central differences (the engine's
    ``firesim.data.dem_loader.slope_aspect``)."""
    from firesim.data.dem_loader import slope_aspect

    s, a = slope_aspect(np.asarray(elev, float), dy, dx)
    return np.asarray(s, np.float32), np.asarray(a, np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fires", nargs="+", default=list(FIRES))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    provp = OUT / "provenance.json"
    prov = json.loads(provp.read_text()) if provp.exists() else {"sources": {
        "worldcover": {"title": "ESA WorldCover 10 m 2021 v200", "citation": "Zanaga D. et al. (2022) ESA WorldCover 10 m 2021 v200, doi:10.5281/zenodo.7254221",
                       "licence": "CC BY 4.0", "url": WC_URL, "use": "classes 80 water, 60 bare, 50 built-up only"},
        "dem": {"title": "USGS 3DEP 1 arc-second DEM", "licence": "public domain (U.S. Government work)", "url": DEM_URL},
        "perimeter": {"title": "CAL FIRE FRAP fire perimeters (on disk)", "licence": "CC BY", "use": "grid box only"}},
        "fires": {}}
    for fire in args.fires:
        t = time.time()
        g = grid_for(fire)
        water, bare, built, wc_tiles = worldcover_fractions(g)
        elev, dem_tiles = dem(g)
        dy = g["h_m"]
        mid = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
        dx = g["cell_lng"] * 111_320.0 * math.cos(math.radians(mid))
        slope, aspect = slope_aspect_m(elev, dx, dy)
        np.savez_compressed(OUT / f"{fire}.npz", f_water=water.astype(np.float32), f_bare=bare.astype(np.float32),
                            f_built=built.astype(np.float32), elev_m=elev.astype(np.float32), slope_pct=slope,
                            aspect_deg=aspect, **{k: np.float64(v) for k, v in g.items()})
        prov["fires"][fire] = {**g, "worldcover_tiles": wc_tiles, "dem_tiles": dem_tiles,
                               "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                               "water_share": float(water.mean()), "built_share": float(built.mean())}
        provp.write_text(json.dumps(prov, indent=1))
        print(f"{fire}: {g['rows']}x{g['cols']} water {water.mean():.3f} built {built.mean():.3f} "
              f"elev {np.nanmin(elev):.0f}-{np.nanmax(elev):.0f} m | {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()
