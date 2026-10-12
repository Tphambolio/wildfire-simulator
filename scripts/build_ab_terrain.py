#!/usr/bin/env python3
"""Build the Alberta terrain file: NRCan MRDEM-30 elevation on a ~100 m lat/lng grid (COG).

    python3 scripts/build_ab_terrain.py OUT_DIR [--src URL_OR_PATH] [--overview-level 0]

Source: Natural Resources Canada, Medium Resolution Digital Elevation Model (MRDEM-30), digital
terrain model (bare earth), 30 m, EPSG:3979, cloud-optimised GeoTIFF on the CanElevation
bucket; Open Government Licence - Canada. Read by window over HTTP (``/vsicurl/``), block by
block, from the 60 m overview (``--overview-level 0``; -1 = the full 30 m grid), never whole.

Output (``ab_terrain_mrdem30_100m.tif``): one band, elevation in **decimetres** as uint16
(0 = no data; scale 0.1 recorded in the tags), EPSG:4326, cells 0.0009 deg (N-S, ~100 m) x
0.0015 deg (E-W, ~97 m at 54.5 N), box 48.95-60.05 N, 120.10-109.90 W (Alberta plus a margin:
parts of British Columbia, Saskatchewan and the Northwest Territories; nothing south of the
border, which MRDEM does not cover). Elevation is averaged from the source cells
(``Resampling.average``). 512 px tiles, DEFLATE + predictor 2, no overviews.

Why elevation, not slope and aspect: aspect is noise on flat ground and does not compress, and
storing elevation lets the engine derive slope and aspect on the run's own lat/lng lattice with
the same finite differences as the Edmonton DEM (``firesim.data.dem_loader.slope_aspect``), so
aspect is relative to true north. Decimetres keep the slope quantisation below 0.1 % at 100 m.

Memory: one block (default 1024 x 1024 output cells plus its source window) at a time, well
under 1 GB. Writes ``<out>.json`` with the extent, cell counts, SHA-256 and provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MRDEM_URL = "https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"
NAME = "ab_terrain_mrdem30_100m.tif"
LAT_MIN, LAT_MAX = 48.95, 60.05
LNG_MIN, LNG_MAX = -120.10, -109.90
DLAT, DLNG = 0.0009, 0.0015
SCALE = 0.1  # metres per stored unit
LICENCE = "Open Government Licence - Canada (https://open.canada.ca/en/open-government-licence-canada)"
ATTRIBUTION = ("Contains information licensed under the Open Government Licence - Canada "
               "(NRCan MRDEM-30).")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def build(out_dir: Path, src_path: str, overview_level: int, block: int) -> dict:
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    rows = int(round((LAT_MAX - LAT_MIN) / DLAT))
    cols = int(round((LNG_MAX - LNG_MIN) / DLNG))
    transform = from_origin(LNG_MIN, LAT_MAX, DLNG, DLAT)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (NAME + ".tmp.tif")
    out = out_dir / NAME
    profile = dict(driver="GTiff", width=cols, height=rows, count=1, dtype="uint16",
                   crs="EPSG:4326", transform=transform, nodata=0, tiled=True, blockxsize=512,
                   blockysize=512, compress="DEFLATE", predictor=2, BIGTIFF="IF_SAFER")
    src_url = src_path if not src_path.startswith("http") else "/vsicurl/" + src_path
    opts = {} if overview_level < 0 else {"overview_level": overview_level}
    stats = {"blocks": 0, "valid_cells": 0, "min_m": None, "max_m": None}
    t0 = time.time()
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_CACHEMAX=256,
                      GDAL_HTTP_MAX_RETRY=5, GDAL_HTTP_RETRY_DELAY=2), \
            rasterio.open(src_url, **opts) as src, rasterio.open(tmp, "w", **profile) as dst:
        src_res = src.res[0]
        nb = math.ceil(rows / block) * math.ceil(cols / block)
        for r0 in range(0, rows, block):
            for c0 in range(0, cols, block):
                h, w = min(block, rows - r0), min(block, cols - c0)
                win = Window(c0, r0, w, h)
                wt = dst.window_transform(win)
                west, north = wt * (0, 0)
                east, south = wt * (w, h)
                left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, west, south,
                                                            east, north, densify_pts=21)
                sw = from_bounds(left, bottom, right, top, src.transform)
                sc0 = max(0, int(math.floor(sw.col_off)) - 3)
                sr0 = max(0, int(math.floor(sw.row_off)) - 3)
                sc1 = min(src.width, int(math.ceil(sw.col_off + sw.width)) + 3)
                sr1 = min(src.height, int(math.ceil(sw.row_off + sw.height)) + 3)
                dem = np.full((h, w), np.nan, dtype=np.float32)
                if sc1 > sc0 and sr1 > sr0:
                    swin = Window(sc0, sr0, sc1 - sc0, sr1 - sr0)
                    data = src.read(1, window=swin)
                    reproject(source=data, destination=dem,
                              src_transform=src.window_transform(swin), src_crs=src.crs,
                              src_nodata=src.nodata, dst_transform=wt, dst_crs="EPSG:4326",
                              dst_nodata=np.nan, resampling=Resampling.average)
                    del data
                ok = np.isfinite(dem)
                q = np.zeros((h, w), dtype=np.uint16)
                q[ok] = np.clip(np.rint(dem[ok] / SCALE), 1, 65535).astype(np.uint16)
                dst.write(q, 1, window=win)
                if ok.any():
                    stats["valid_cells"] += int(ok.sum())
                    lo, hi = float(dem[ok].min()), float(dem[ok].max())
                    stats["min_m"] = lo if stats["min_m"] is None else min(stats["min_m"], lo)
                    stats["max_m"] = hi if stats["max_m"] is None else max(stats["max_m"], hi)
                stats["blocks"] += 1
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                print(f"block {stats['blocks']}/{nb} rows {r0} cols {c0}: "
                      f"{ok.mean() * 100:.0f}% valid, {time.time() - t0:.0f} s, peak RSS {rss:.0f} MB",
                      flush=True)
        dst.update_tags(
            SOURCE="NRCan Medium Resolution Digital Elevation Model MRDEM-30, DTM, "
                   f"{src_path} (overview level {overview_level}, {src_res:.0f} m cells)",
            LICENCE=LICENCE, ATTRIBUTION=ATTRIBUTION, UNITS="decimetres", SCALE=str(SCALE),
            BUILDER="scripts/build_ab_terrain.py")
    subprocess.run(["gdal_translate", "-q", "-of", "COG", "-co", "COMPRESS=DEFLATE",
                    "-co", "PREDICTOR=2", "-co", "LEVEL=9", "-co", "BLOCKSIZE=512",
                    "-co", "OVERVIEWS=NONE", str(tmp), str(out)], check=True)
    tmp.unlink()
    digest = sha256(out)
    meta = {
        "file": NAME, "bytes": out.stat().st_size, "sha256": digest,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "NRCan MRDEM-30 DTM (Medium Resolution Digital Elevation Model, 30 m)",
        "source_url": src_path, "overview_level": overview_level,
        "licence": LICENCE, "attribution": ATTRIBUTION,
        "crs": "EPSG:4326", "rows": rows, "cols": cols, "dlat": DLAT, "dlng": DLNG,
        "box": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lng_min": LNG_MIN, "lng_max": LNG_MAX},
        "dtype": "uint16", "units": "decimetres", "scale": SCALE, "nodata": 0,
        "resampling": "average", **stats, "seconds": round(time.time() - t0),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
    }
    (out_dir / (NAME + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--src", default=MRDEM_URL)
    ap.add_argument("--overview-level", type=int, default=0,
                    help="source overview (0 = 60 m, the default; -1 = full 30 m)")
    ap.add_argument("--block", type=int, default=1024)
    a = ap.parse_args()
    meta = build(a.out_dir, a.src, a.overview_level, a.block)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    sys.exit(main())
