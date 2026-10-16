#!/usr/bin/env python3
"""Long-range embers v7, stage 1 input: pre-fire LANDFIRE fuels crosswalked to FBP fuel types.

Purpose
    v6 (PREREG_LR6) ran every CAL FIRE fire on one proxy FBP fuel (C-4 or C-7) and found that
    FireSim's front, not the ember chain, was the dominant error (about 10 h at community blocks).
    v7 replaces the single proxy with the LANDFIRE 40 Scott & Burgan fire behaviour fuel models
    (FBFM40) of the vintage before each fire, crosswalked to FireSim's FBP fuel types by the
    documented table ``CROSSWALK`` below, and keeps the fuel group of every cell so that stage 1 can
    calibrate a per-group rate-of-spread multiplier (``long_range_v7_front.py``).

Inputs
    - The v6 run grids (``validation-data/embers-v6/grids/<fire>.npz``: lat/lng box, 90 m cells,
      WorldCover water/bare/built shares, 3DEP terrain); fresh v7 fires get the same grids from
      ``long_range_v6_data.py`` written to ``embers-v7/grids``.
    - LANDFIRE FBFM40, CONUS, via the LANDFIRE Product Service image services
      (https://lfps.usgs.gov/arcgis/rest/services/Landfire_<V>/<V>_FBFM40_CONUS/ImageServer,
      ``exportImage`` at the native 30 m, EPSG:5070, nearest neighbour). LANDFIRE data are public
      domain (U.S. Government work; LANDFIRE Program, USDA Forest Service / U.S. Department of the
      Interior). Vintage rule (``VINTAGE``): the most recent LANDFIRE release whose disturbance
      year precedes the fire and that the product service still serves. LF 2019 (LF 2.1.0) and
      LF 2020 (LF 2.2.0) are no longer served (checked 2026-10-15: ``/api/products`` lists FBFM40 for
      LF2016, LF2022-LF2025 only), so 2017-2021 fires use the LF 2016 Remap (LF 2.0.0) and the 2025
      fires use LF2024. Disturbances between 2016 and a 2018-2021 fire are therefore missing (e.g.
      the 2017 Tubbs scar inside the Glass box); disclosed in the v7 report.

Outputs (outside the repo)
    ``validation-data/embers-v7/grids/<fire>.npz``: the v6 grid arrays plus ``fbfm40`` (mode of the
    30 m pixels in each 90 m cell, int16; 0 = no data), shares ``f_G``, ``f_S``, ``f_T``, ``f_U``
    (urban NB1), ``f_NB`` (other non-burnable or no data), ``lf_layer``; and
    ``embers-v7/landfire/<fire>_<layer>.tif`` (the 30 m extract) + ``provenance.json``.

Crosswalk (``CROSSWALK``; heuristic [H], no published FBFM40 -> FBP crosswalk for California fuels
was found; the groups, not the exact FBP surrogate, carry the calibration)
    G  grass and grass-shrub (GR1-GR9, GS1-GS4) -> O-1b standing grass (FCFDG 1992 ST-X-3 p.22,
       O-1 matted/standing grass). Scott & Burgan (2005) RMRS-GTR-153 pp.9-15 describe GR/GS as
       grass-dominated with a minor shrub component.
    S  shrub (SH1-SH9) -> C-4 immature lodgepole/jack pine (ST-X-3 p.20): the FBP type whose head-fire
       rate (110 (1 - e^{-0.0293 ISI})^1.5 m/min) is closest to the "10 % of the 10 m wind" rule of
       thumb for shrublands (Cruz & Alexander 2019 Ann. For. Sci. 76:44, p.1) and that can carry
       crown fraction burned (ember source). SH1-SH3 (low load) are also C-4: one surrogate per
       group keeps the calibration to one multiplier per group.
    T  timber understory, timber litter, slash-blowdown (TU1-TU5, TL1-TL9, SB1-SB4) -> C-7
       ponderosa pine - Douglas-fir (ST-X-3 p.21): the FBP type of western dry mixed conifer.
    U  NB1 urban/developed -> a "town" cell (FireSim's town rule of v6: ROS x 0.5 [H], spec C40),
       fuel O-1b (lawns, landscaping) [H]; also any cell with WorldCover built-up share >= 0.25.
    NB NB2 snow/ice, NB3 agriculture, NB8 water, NB9 barren, no data -> non-fuel (Scott & Burgan
       2005 p.7: NB3 "maintained in a non-burnable condition").

Run (repo root; ~0.3 GB per fire)
    python3 scripts/long_range_v7_fuels.py --fires eaton palisades camp tubbs woolsey glass czu northcomplex

Status: data preparation for the v7 pre-registered test (PREREG_LR7); illustrative.
References: Scott JH, Burgan RE (2005) USDA FS RMRS-GTR-153; Forestry Canada Fire Danger Group (1992)
    ST-X-3; Cruz MG, Alexander ME (2019) Ann. For. Sci. 76:44 doi:10.1007/s13595-019-0829-8;
    Rollins MG (2009) Int. J. Wildland Fire 18:235-249 (LANDFIRE) doi:10.1071/WF08088.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
V6GRIDS = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v6" / "grids"
V7 = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v7"
SERVICE = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_{v}/{v}_FBFM40_CONUS/ImageServer/exportImage"

# fire -> LANDFIRE release (fire date in the comment)
VINTAGE = {
    "tubbs": "LF2016",         # 2017-10-08
    "camp": "LF2016",          # 2018-11-08
    "woolsey": "LF2016",       # 2018-11-08
    "glass": "LF2016",         # 2020-09-27 (LF2019/LF2020 not served)
    "czu": "LF2016",           # 2020-08-16
    "northcomplex": "LF2016",  # 2020-09-08
    "eaton": "LF2024",         # 2025-01-07 (LF2024 = disturbance through 2024)
    "palisades": "LF2024",     # 2025-01-07
    # fresh v7 held-out fires (2018-2021) -> LF2016
    "dixie": "LF2016", "caldor": "LF2016", "carr": "LF2016", "zogg": "LF2016", "kincade": "LF2016",
    "thomas": "LF2016",
}

GROUPS = ("G", "S", "T", "U", "NB")
FBP_OF_GROUP = {"G": "O1b", "S": "C4", "T": "C7", "U": "O1b"}


def group_of(code: int) -> str:
    """FBFM40 code -> v7 fuel group (see module docstring)."""
    c = int(code)
    if 101 <= c <= 109 or 121 <= c <= 124:
        return "G"
    if 141 <= c <= 149:
        return "S"
    if 161 <= c <= 165 or 181 <= c <= 189 or 201 <= c <= 204:
        return "T"
    if c == 91:
        return "U"
    return "NB"  # 92, 93, 98, 99, no data


CROSSWALK = {}
for _c in list(range(101, 110)) + list(range(121, 125)) + list(range(141, 150)) + list(range(161, 166)) + \
        list(range(181, 190)) + list(range(201, 205)) + [91, 92, 93, 98, 99]:
    _g = group_of(_c)
    CROSSWALK[_c] = {"group": _g, "fbp": FBP_OF_GROUP.get(_g)}


def grid_path(fire: str) -> Path:
    p = V7 / "grids" / f"{fire}_base.npz"
    return p if p.exists() else V6GRIDS / f"{fire}.npz"


def fetch(fire: str, layer: str, g: dict) -> Path:
    """30 m FBFM40 extract covering the grid box (+300 m), EPSG:5070."""
    import pyproj

    out = V7 / "landfire" / f"{fire}_{layer}_FBFM40.tif"
    if out.exists():
        return out
    lat_min = g["lat_max"] - g["rows"] * g["cell_lat"]
    lng_max = g["lng_min"] + g["cols"] * g["cell_lng"]
    tr = pyproj.Transformer.from_crs(4326, 5070, always_xy=True)
    xs, ys = tr.transform([g["lng_min"], lng_max, g["lng_min"], lng_max], [lat_min, lat_min, g["lat_max"], g["lat_max"]])
    x0 = math.floor((min(xs) - 300) / 30) * 30
    y0 = math.floor((min(ys) - 300) / 30) * 30
    x1 = math.ceil((max(xs) + 300) / 30) * 30
    y1 = math.ceil((max(ys) + 300) / 30) * 30
    w, h = int((x1 - x0) / 30), int((y1 - y0) / 30)
    q = urllib.parse.urlencode({"bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 5070, "imageSR": 5070, "size": f"{w},{h}",
                                "format": "tiff", "pixelType": "S16", "interpolation": "RSP_NearestNeighbor",
                                "f": "image"})
    url = SERVICE.format(v=layer) + "?" + q
    data = urllib.request.urlopen(url, timeout=300).read()
    if data[:2] not in (b"II", b"MM"):
        raise RuntimeError(f"{fire}: not a TIFF: {data[:200]!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    prov_p = V7 / "landfire" / "provenance.json"
    prov = json.loads(prov_p.read_text()) if prov_p.exists() else {}
    prov[f"{fire}_{layer}"] = {"url": url, "retrieved_utc": datetime.now(timezone.utc).isoformat(), "bytes": len(data),
                               "bbox_5070": [x0, y0, x1, y1], "size": [w, h],
                               "licence": "LANDFIRE, public domain (U.S. Government work)"}
    prov_p.write_text(json.dumps(prov, indent=1))
    return out


def to_grid(tif: Path, g: dict) -> dict:
    """Mode FBFM40 and group shares per grid cell (30 m pixels assigned by their centre's lat/lng)."""
    import pyproj
    import rasterio

    with rasterio.open(tif) as s:
        a = s.read(1).astype(np.int32)
        T = s.transform
    hh, ww = a.shape
    cols, rows = np.meshgrid(np.arange(ww) + 0.5, np.arange(hh) + 0.5)
    x = T.c + cols * T.a
    y = T.f + rows * T.e
    tr = pyproj.Transformer.from_crs(5070, 4326, always_xy=True)
    lng, lat = tr.transform(x.ravel(), y.ravel())
    r = np.floor((g["lat_max"] - lat) / g["cell_lat"]).astype(int)
    c = np.floor((lng - g["lng_min"]) / g["cell_lng"]).astype(int)
    ok = (r >= 0) & (r < g["rows"]) & (c >= 0) & (c < g["cols"])
    code = a.ravel()[ok]
    code = np.where(code < 0, 0, code)
    cell = r[ok] * g["cols"] + c[ok]
    n = g["rows"] * g["cols"]
    # mode per cell over the codes present
    codes = np.unique(code)
    cnt = np.zeros((len(codes), n), np.int32)
    for k, cc in enumerate(codes):
        m = code == cc
        cnt[k] = np.bincount(cell[m], minlength=n)
    tot = cnt.sum(axis=0)
    mode = codes[np.argmax(cnt, axis=0)]
    mode = np.where(tot > 0, mode, 0).astype(np.int16).reshape(g["rows"], g["cols"])
    shares = {}
    grp = np.array([group_of(cc) for cc in codes])
    for G in GROUPS:
        shares[f"f_{G}"] = (cnt[grp == G].sum(axis=0) / np.maximum(tot, 1)).reshape(g["rows"], g["cols"]).astype(np.float32)
    shares["f_NB"] = np.where(tot.reshape(g["rows"], g["cols"]) > 0, shares["f_NB"], 1.0).astype(np.float32)
    return {"fbfm40": mode, **shares}


def build(fire: str) -> dict:
    src = grid_path(fire)
    z = dict(np.load(src))
    g = {k: float(z[k]) for k in ("lat_max", "lng_min", "cell_lat", "cell_lng", "h_m")}
    g["rows"], g["cols"] = int(z["rows"]), int(z["cols"])
    layer = VINTAGE[fire]
    tif = fetch(fire, layer, g)
    add = to_grid(tif, g)
    z.update(add)
    z["lf_layer"] = np.array(layer)
    (V7 / "grids").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(V7 / "grids" / f"{fire}.npz", **z)
    fb = add["fbfm40"]
    summary = {G: float(np.mean(add[f"f_{G}"])) for G in GROUPS}
    print(fire, layer, "cells", fb.size, "group shares", {k: round(v, 3) for k, v in summary.items()}, flush=True)
    return {"layer": layer, "grid_source": str(src), "group_share_mean": summary}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fires", nargs="+", required=True)
    a = ap.parse_args()
    info_p = V7 / "grids" / "fuels_info.json"
    info = json.loads(info_p.read_text()) if info_p.exists() else {"_about": {
        "purpose": "v7 pre-fire LANDFIRE FBFM40 fuels on the v6/v7 run grids (scripts/long_range_v7_fuels.py)",
        "crosswalk": {str(k): v for k, v in CROSSWALK.items()}, "vintage_rule": "latest LANDFIRE release before the fire that LFPS serves"}}
    for f in a.fires:
        t = time.time()
        info[f] = build(f)
        info[f]["seconds"] = round(time.time() - t, 1)
    info_p.write_text(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
