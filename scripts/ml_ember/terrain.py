"""Terrain features for the ML ember-cast tables (PREREG_ML_EMBER_DEV section 4; addendum A2).

``inputs.npz`` ``elev`` is zero in every v8 fire, so ``elev_m``, ``slope_pct``, ``aspect_deg`` and ``tpi_1km``
are filled here from the Copernicus DEM GLO-90 (3 arc-second, public S3 bucket ``copernicus-dem-90m``, read
with rasterio over ``/vsicurl`` without credentials). Static, pre-fire, open data.

Method. For every included training fire the full 375 m block grid (the 125 m run grid in blocks of 3, with a
3-block margin) is projected to WGS84; elevation is sampled bilinearly at each block centre from the tile that
contains it; ``slope_pct`` = 100 * |grad| and ``aspect_deg`` = downslope azimuth from north (NaN where slope
< 0.5 %) by central differences at 375 m; ``tpi_1km`` = block elevation minus the mean of the 5 x 5 block
window (1.875 km wide, about a 1 km radius). Missing tiles or nodata are filled from the nearest valid block
and flagged ``terrain_fill`` = 1. The cell positions come from ``coords_control.parquet`` (row_id, bi, bj) and
never leave this module: the output ``table/terrain.parquet`` holds row_id and the four features plus the flag.

Usage: python -I scripts/ml_ember/terrain.py [--limit N] [--workers 1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ml_ember import paths as P  # noqa: E402
from scripts.ml_ember.loader import default_guard  # noqa: E402
from scripts.validation.v8 import common as C  # noqa: E402

BLOCK = C.CAND_F * 125.0  # 375 m
BUCKET = "https://copernicus-dem-90m.s3.amazonaws.com"
MARGIN = 3
_TILES: dict[str, tuple] = {}


def tile_name(lat: float, lon: float) -> str:
    la, lo = math.floor(lat), math.floor(lon)
    ns = "N" if la >= 0 else "S"
    ew = "E" if lo >= 0 else "W"
    return f"Copernicus_DSM_COG_30_{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00_DEM"


def load_tile(name: str):
    """(array float32 with NaN nodata, left, top, resx, resy) or None when the tile does not exist (ocean)."""
    if name in _TILES:
        return _TILES[name]
    import rasterio
    url = f"/vsicurl/{BUCKET}/{name}/{name}.tif"
    out = None
    for attempt in range(4):
        try:
            with rasterio.open(url) as src:
                a = src.read(1).astype(np.float32)
                if src.nodata is not None:
                    a[a == src.nodata] = np.nan
                t = src.transform
                out = (a, t.c, t.f, t.a, -t.e)
            break
        except rasterio.errors.RasterioIOError as e:  # missing tile (404) or transient
            if "404" in str(e) or "not recognized" in str(e).lower() or "No such file" in str(e):
                out = None
                break
            time.sleep(2.0 * (attempt + 1))
    _TILES[name] = out
    if len(_TILES) > 60:  # bounded cache
        _TILES.pop(next(iter(_TILES)))
    return out


def sample(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Bilinear elevation at (lat, lon) arrays; NaN where no tile / nodata."""
    from scipy.ndimage import map_coordinates
    z = np.full(lat.shape, np.nan, np.float32)
    names = np.array([tile_name(a, b) for a, b in zip(lat.ravel(), lon.ravel())]).reshape(lat.shape)
    for nm in np.unique(names):
        t = load_tile(nm)
        sel = names == nm
        if t is None:
            continue
        a, left, top, resx, resy = t
        col = (lon[sel] - left) / resx - 0.5
        row = (top - lat[sel]) / resy - 0.5
        v = map_coordinates(np.nan_to_num(a, nan=-9999.0), [row, col], order=1, mode="nearest")
        v[v < -1000] = np.nan
        z[sel] = v
    return z


def fill_nearest(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from scipy.ndimage import distance_transform_edt
    bad = np.isnan(z)
    if not bad.any():
        return z, bad
    if bad.all():
        return z, bad
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    return z[tuple(idx)], bad


def terrain_grid(meta: dict) -> dict:
    from pyproj import Transformer
    g = meta["grid"]
    nbx, nby = math.ceil(g["nx"] / C.CAND_F), math.ceil(g["ny"] / C.CAND_F)
    bi = np.arange(-MARGIN, nbx + MARGIN); bj = np.arange(-MARGIN, nby + MARGIN)
    BI, BJ = np.meshgrid(bi, bj)
    x = g["x0"] + (BI + 0.5) * BLOCK; y = g["y1"] - (BJ + 0.5) * BLOCK
    tr = Transformer.from_crs(g["crs"], "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(x, y)
    z = sample(np.asarray(lat), np.asarray(lon))
    z, filled = fill_nearest(z)
    if np.isnan(z).all():
        raise RuntimeError("no DEM coverage")
    gy, gx = np.gradient(z.astype(np.float64), BLOCK)  # gy along rows (southward), gx along columns (eastward)
    dzdx, dzdy = gx, -gy  # northward
    slope = 100.0 * np.hypot(dzdx, dzdy)
    aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360.0) % 360.0
    aspect[slope < 0.5] = np.nan
    from scipy.ndimage import uniform_filter
    tpi = z - uniform_filter(z.astype(np.float64), size=5, mode="nearest")
    m = (slice(MARGIN, MARGIN + nby), slice(MARGIN, MARGIN + nbx))
    return dict(elev=z[m].astype(np.float32), slope=slope[m].astype(np.float32), aspect=aspect[m].astype(np.float32),
                tpi=tpi[m].astype(np.float32), fill=filled[m].astype(np.int8), nbx=nbx, nby=nby)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(P.TABLE_DIR / "terrain.parquet"))
    a = ap.parse_args(argv)
    import pyarrow as pa
    import pyarrow.parquet as pq
    guard = default_guard()
    fires = pq.read_table(P.TABLE_DIR / "fires.parquet", columns=["fire_key"]).to_pandas().fire_key.tolist()
    if a.limit:
        fires = fires[: a.limit]
    coords = pq.read_table(P.TABLE_DIR / "coords_control.parquet", columns=["row_id", "fire_key", "bi", "bj"]).to_pandas()
    parts, report = [], dict(fires_ok=[], fires_failed={}, tiles_missing=0, started_utc=pd.Timestamp.utcnow().isoformat())
    t0 = time.time()
    for n, k in enumerate(fires, 1):
        guard.assert_training(k)
        meta = guard.read_json(C.FIRES / k.replace(":", "_") / "meta.json")
        rows = coords[coords.fire_key == k]
        try:
            T = terrain_grid(meta)
        except Exception as e:  # noqa: BLE001
            report["fires_failed"][k] = str(e)
            print(k, "FAILED", e, flush=True)
            parts.append(pd.DataFrame(dict(row_id=rows.row_id.to_numpy(), elev_m=np.nan, slope_pct=np.nan, aspect_deg=np.nan,
                                           tpi_1km=np.nan, terrain_fill=np.int8(1))))
            continue
        bi = np.clip(rows.bi.to_numpy(), 0, T["nbx"] - 1); bj = np.clip(rows.bj.to_numpy(), 0, T["nby"] - 1)
        parts.append(pd.DataFrame(dict(row_id=rows.row_id.to_numpy(), elev_m=T["elev"][bj, bi], slope_pct=T["slope"][bj, bi],
                                       aspect_deg=T["aspect"][bj, bi], tpi_1km=T["tpi"][bj, bi], terrain_fill=T["fill"][bj, bi])))
        report["fires_ok"].append(k)
        print(f"{n}/{len(fires)} {k} ok blocks {T['nby']}x{T['nbx']} elev {np.nanmin(T['elev']):.0f}-{np.nanmax(T['elev']):.0f} m "
              f"fill {int(T['fill'].sum())} {time.time() - t0:.0f}s", flush=True)
    out = pd.concat(parts, ignore_index=True)
    for c in ("elev_m", "slope_pct", "aspect_deg", "tpi_1km"):
        out[c] = out[c].astype(np.float32)
    out["terrain_fill"] = out["terrain_fill"].astype(np.int8)
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False), outp, compression="zstd")
    sha = hashlib.sha256(outp.read_bytes()).hexdigest()
    report.update(rows=int(len(out)), sha256=sha, finished_utc=pd.Timestamp.utcnow().isoformat(), wall_seconds=time.time() - t0,
                  source="Copernicus DEM GLO-90 (ESA), public bucket copernicus-dem-90m, read 2026-10-29",
                  method="bilinear at 375 m block centres; slope/aspect central differences; TPI 5x5 blocks; nearest-valid fill flagged",
                  tiles_missing=sum(1 for v in _TILES.values() if v is None), n_fill=int(out.terrain_fill.sum()),
                  nan_elev=int(out.elev_m.isna().sum()))
    (outp.parent.parent / "terrain_build.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k not in ("fires_ok",)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
