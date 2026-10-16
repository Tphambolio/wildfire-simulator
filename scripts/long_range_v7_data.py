#!/usr/bin/env python3
"""Long-range embers v7, data preparation: run grids for the fresh held-out fires (PREREG_LR7).

Purpose
    The v7 fresh held-out fires (never scored in any FireSim round) need the same run grid as the v6
    fires: a lat/lng box of 90 m cells with ESA WorldCover water/bare/built shares and USGS 3DEP terrain
    (``long_range_v6_data.py``), to which ``long_range_v7_fuels.py`` adds pre-fire LANDFIRE fuels.

Box rule (fixed before any fresh-fire score)
    v6 used the final FRAP perimeter + 4 km. The fresh fires are scored only over their run window, and
    Dixie's final perimeter (~390,000 ha) is far larger than the window's run, so the box is the bounds
    of the latest timed perimeter at or before the window end (``timed_perimeters`` of the fire's
    ``v7_config.json``) + ``PAD_M`` (8 km, twice v6's pad, so a too-fast model is not boxed in),
    intersected with the final FRAP perimeter's bounds + 8 km. Fires without timed perimeters use the
    FRAP perimeter + 4 km as v6. No damage data are read.

Inputs: ``calfire-dins/raw/frap_perimeter_<fire>.geojson`` (CC BY), the timed perimeters
    (``derived/v4/<fire>_gofer.geojson``; GOFER, Liu et al. 2024 ESSD 16:1395), ESA WorldCover 2021 v200
    (CC BY 4.0, Zanaga et al. 2022 doi:10.5281/zenodo.7254221), USGS 3DEP 1 arc-second (public domain).
Outputs: ``validation-data/embers-v7/grids/<fire>_base.npz`` (same keys as the v6 grids) and
    ``embers-v7/grids/provenance_v7.json``.

Run: python3 scripts/long_range_v7_data.py --fires dixie caldor
Status: data preparation for PREREG_LR7; illustrative.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import long_range_v6_data as D6  # noqa: E402

CALFIRE = D6.CALFIRE
_FRAP_BOUNDS = D6.fire_bounds
OUT = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v7" / "grids"
PAD_M = 8000.0


def box_for(fire: str):
    import shapely

    cfgp = CALFIRE / "refs" / fire / "v7_config.json"
    c = json.load(open(cfgp))
    frap = _FRAP_BOUNDS(fire)
    tp = c.get("timed_perimeters")
    if not tp:
        return frap, 4000.0, "frap+4km"
    p = Path(tp) if Path(tp).is_absolute() else CALFIRE / tp
    end = datetime.strptime(c["start_utc"], "%Y-%m-%d %H:%M") + timedelta(minutes=float(c["duration_min"]))
    best = None
    for f in json.load(open(p))["features"]:
        t = datetime.strptime(f["properties"]["tUTC"][:16].replace("T", " "), "%Y-%m-%d %H:%M")
        if t <= end and (best is None or t > best[0]):
            best = (t, f)
    if best is None:
        return frap, 4000.0, "frap+4km (no timed perimeter before the window end)"
    b = shapely.geometry.shape(best[1]["geometry"]).bounds
    lat_pad = PAD_M / 111_320.0
    lng_pad = PAD_M / (111_320.0 * math.cos(math.radians(0.5 * (b[1] + b[3]))))
    box = (max(b[0] - lng_pad, frap[0] - lng_pad), max(b[1] - lat_pad, frap[1] - lat_pad),
           min(b[2] + lng_pad, frap[2] + lng_pad), min(b[3] + lat_pad, frap[3] + lat_pad))
    return box, 0.0, f"timed perimeter {best[0]:%Y-%m-%d %H:%M} UTC + {PAD_M / 1000:.0f} km, within FRAP + 8 km"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fires", nargs="+", required=True)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    provp = OUT / "provenance_v7.json"
    prov = json.loads(provp.read_text()) if provp.exists() else {"_about": {
        "purpose": "v7 fresh held-out run grids (scripts/long_range_v7_data.py)", "box_rule": __doc__.split("Box rule")[1][:900]},
        "fires": {}}
    for fire in a.fires:
        t = time.time()
        (lng0, lat0, lng1, lat1), pad, rule = box_for(fire)
        D6.fire_bounds = (lambda _b: (lambda f: _b))((lng0, lat0, lng1, lat1))
        D6.PAD_M = pad
        g = D6.grid_for(fire)
        water, bare, built, wc_tiles = D6.worldcover_fractions(g)
        elev, dem_tiles = D6.dem(g)
        mid = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
        dx = g["cell_lng"] * 111_320.0 * math.cos(math.radians(mid))
        slope, aspect = D6.slope_aspect_m(elev, dx, g["h_m"])
        np.savez_compressed(OUT / f"{fire}_base.npz", f_water=water.astype(np.float32), f_bare=bare.astype(np.float32),
                            f_built=built.astype(np.float32), elev_m=elev.astype(np.float32), slope_pct=slope,
                            aspect_deg=aspect, **{k: np.float64(v) for k, v in g.items()})
        prov["fires"][fire] = {**g, "box_rule": rule, "worldcover_tiles": wc_tiles, "dem_tiles": dem_tiles,
                               "retrieved_utc": datetime.now(timezone.utc).isoformat()}
        provp.write_text(json.dumps(prov, indent=1, default=float))
        print(f"{fire}: {g['rows']}x{g['cols']} ({rule}) | {time.time() - t:.0f} s", flush=True)


if __name__ == "__main__":
    main()
