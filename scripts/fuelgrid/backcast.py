"""Pre-fire (back-cast) fuel for historical replays: offline build.

FireSim's default fuel outside Edmonton is the Alberta Wildfire FBP grid of 2025. A replay of an
older fire on it meets the fire's own scar, and every later scar, as the post-fire fuel the grid
records there (grass O-1, vegetated non-fuel 105 or young deciduous). The back-cast keeps the
2025 grid and, for a scenario dated before it, restores the cells NBAC records as burned after
the scenario date with the fuel typed from an older source (``docs/fuel-grid-backcast.md``).

This script builds the two kinds of layers the API reads at run time, both on the Alberta grid's
own 100 m lattice (EPSG:3400; same transform as the 2025 COG, so a cell lines up exactly):

1. ``ab_burn_key_100m.tif`` (int32): per cell, the latest "restore key" of the NBAC burns that
   cover its centre, 1985-2025. A burn's key is its end date as ``YYYYDDD`` (latest of the agency
   and hotspot end dates); with no recorded end date it is ``YYYY366`` (after any day of its
   year). A scenario dated ``S`` (``YYYYDDD``) restores the cells whose key is ``>= S``: every
   burn of a later year, and burns of the scenario's own year not recorded as finished before
   it. 0 = no burn since 1985.
2. ``ab_prefire_scanfi_<E>_100m.tif`` (int16, Alberta Wildfire codes, 0 = no value): SCANFI v2
   epoch ``E`` typed to FBP with the CFS national decision rules (Swystun, Taylor & Simpson
   2018, ``FBP_Fuel_Layer_Decision_Rules.pdf``), only for cells burned after ``E`` (key
   ``> (E+1)*1000``; the epoch is used for scenarios in years ``E+1 .. E+5``). SCANFI's 30 m
   layers are averaged (closure, height, species crown closure) or taken by mode (land cover) to
   the 100 m cell before typing.

Sources (open data; licences in ``docs/fuel-grid-backcast.md`` and docs/PROJECT_RECORD.md §4.3):
NBAC 20260513 (Hall et al. 2020), SCANFI v2 (Guindon et al. 2026). Downloads go to
``$PREFIRE_DATA/raw`` and products to ``$PREFIRE_DATA/out`` (outside the repository).

    python -m scripts.fuelgrid.backcast burns                 # NBAC -> burn key (~5 min)
    python -m scripts.fuelgrid.backcast mirror 2010            # local sparse SCANFI copy
    python -m scripts.fuelgrid.backcast epoch 2010 [--workers 8]   # type (mirror or server)
    python -m scripts.fuelgrid.backcast manifest              # backcast.json beside the rasters
    python -m scripts.fuelgrid.backcast package               # tar for the data release
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

log = logging.getLogger("fuelgrid.backcast")

DATA = Path(os.environ.get("PREFIRE_DATA", Path.home() / "dev/wildfire/prefire-data"))
RAW_NBAC = DATA / "raw" / "nbac"
OUT = DATA / "out"
MIRROR = DATA / "raw" / "scanfi"
AB_COG = Path(os.environ.get(
    "AB_WILDFIRE_FUEL", Path.home() / "dev/wildfire/reference-layers/ab_wildfire_fbp_fueltype/"
                                      "ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif"))
NBAC_RELEASE = "20260513"
NBAC_URL = "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_{y}_" + NBAC_RELEASE + ".zip"
FIRST_YEAR, LAST_YEAR = 1985, 2025
SCANFI = ("https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/"
          "SCANFI_{layer}_{epoch}_v2_20260119.tif")
SCANFI_EPOCHS = (1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020)
SPECIES = ("blackSpruce", "broadleaf", "balsamFir", "jackPine", "lodgepolePine",
           "otherConiferous", "tamarack", "whiteRedPine", "douglasFir", "ponderosaPine")
LAYERS = ("att_nfiLandcover", "att_closure", "att_height") + tuple(f"spsCC_{s}" for s in SPECIES)
TILE = 128  # Alberta cells (12.8 km, about 1.7 SCANFI blocks) per tile; tiles without burns are skipped
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "8",
            "GDAL_HTTP_RETRY_DELAY": "3", "GDAL_HTTP_MULTIRANGE": "YES",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",
            "GDAL_CACHEMAX": "256", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif"}

# SCANFI v2 NFI land cover (att_nfiLandcover)
LC_BRYOID, LC_HERBS, LC_ROCK, LC_SHRUB, LC_BROADLEAF, LC_CONIFER, LC_MIXED, LC_WATER = range(1, 9)

# Alberta Wildfire codes written by the typing (firesim.data.fuel_loader.AB_WILDFIRE_CODES)
C1, C2, C3, C4, C5, C6, C7 = 1, 2, 3, 4, 5, 6, 7
D12 = 13           # D-1/D-2 (season swap at run time)
O1 = 31            # O-1 (O-1b in FireSim)
NONFUEL, WATER = 101, 102


def m12(pc: int) -> int:
    """Alberta M-1/M-2 code with ``pc`` percent conifer (610-690)."""
    return 600 + int(pc)


# Rule numbers follow the CFS 2018 decision-rule document (open stands 1-6, mixedwood 1-4,
# conifer 1-53); 0 = not typed. Kept per cell for the build report.
RULE_NAMES = {
    100: "water (land cover 8) -> water",
    101: "rock (land cover 3) -> non-fuel",
    110: "open (crown closure < 10 %), default -> D-1/2 (open rule 1)",
    111: "open, no black spruce / tamarack among the 3 leading species -> O-1 (open rule 3)",
    112: "open, shrub land cover -> M-1/2 25 % conifer (open rule 6)",
    120: "deciduous (> 75 % broadleaf incl. tamarack) -> D-1/2",
    130: "mixedwood, closure <= 30 % -> D-1/2 (mixedwood rule 1)",
    131: "mixedwood 25-42 % needleleaf -> M-1/2 35 % (rule 2)",
    132: "mixedwood 42-58 % needleleaf -> M-1/2 50 % (rule 3)",
    133: "mixedwood 58-75 % needleleaf -> M-1/2 65 % (rule 4)",
    141: "black spruce, closure < 30 % -> C-1 (conifer rule 1)",
    142: "black spruce, closure 30-70 % -> C-2 (rule 2)",
    143: "black spruce, closure >= 70 %, height < 7 m -> C-4 (rule 3)",
    144: "black spruce, closure >= 70 %, height >= 7 m -> C-2 (rule 4)",
    145: "black spruce + jack pine >= 15 %, closure >= 50 %, height < 10 m -> C-4 (rule 5)",
    146: "black spruce + jack pine >= 15 %, closure >= 50 %, height >= 10 m -> C-2 (rule 6)",
    148: "black spruce + jack pine >= 15 %, closure < 50 %, height < 10 m -> C-2 (rule 8, "
         "non-taiga) [H: no ecozone layer, so rule 7 (taiga -> C-1) is not applied]",
    149: "black spruce + jack pine >= 15 %, closure < 50 %, height >= 10 m -> C-3 (rule 9)",
    150: "white spruce / other conifer, closure < 30 % -> D-1/2 (rule 10)",
    151: "white spruce / other conifer, closure >= 30 % -> C-2 (rule 11)",
    152: "balsam fir, closure < 30 % -> M-1/2 25 % (rule 12)",
    153: "balsam fir, closure 30-70 % -> C-2 (rule 13)",
    154: "balsam fir, closure > 70 % -> C-4 (rule 14)",
    155: "jack / lodgepole pine, closure < 40 % -> C-7 (rule 15)",
    156: "jack / lodgepole pine, closure < 40 %, black spruce / tamarack > 15 % -> M-1/2 50 % "
         "(rule 16)",
    157: "jack / lodgepole pine, closure >= 40 %, height >= 7 m -> C-3 (rule 17)",
    158: "jack / lodgepole pine, closure >= 40 %, height < 7 m -> C-4 (rule 18)",
    160: "white / red pine, closure < 30 % -> C-7 (rule 25)",
    161: "white / red pine, closure >= 30 % -> C-5 (rule 26)",
    162: "ponderosa pine -> C-7 (rule 32)",
    163: "Douglas-fir, closure <= 40 % -> C-7 (rule 36)",
    164: "Douglas-fir, closure 40-60 % -> C-3 (rule 37)",
    165: "Douglas-fir, closure > 60 % -> C-4 (rule 38)",
    170: "conifer stand led by broadleaf / tamarack, closure >= 50 %, height < 10 m -> C-4 "
         "(rule 44)",
    171: "conifer stand led by broadleaf / tamarack, closure >= 50 %, height >= 10 m -> C-2 "
         "(rule 45)",
    172: "conifer stand led by broadleaf / tamarack, closure < 50 %, height < 10 m -> C-2 "
         "(rule 47, non-taiga) [H]",
    173: "conifer stand led by broadleaf / tamarack, closure < 50 %, height >= 10 m -> C-3 "
         "(rule 48)",
}


def type_scanfi(lc: np.ndarray, closure: np.ndarray, height: np.ndarray,
                cc: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """(Alberta Wildfire codes int16, rule numbers uint8-ish int16) from SCANFI v2 attributes.

    ``lc``: NFI land cover (1-8; anything else = no value); ``closure``: total crown closure %;
    ``height``: m; ``cc``: crown closure % per species (keys of ``SPECIES``). Follows the CFS
    national decision rules (Swystun, Taylor & Simpson 2018) for the classes that occur in
    Alberta: forest (closure >= 10 %) is deciduous / mixedwood / coniferous by needleleaf share
    (tamarack counted as broadleaf, document p.1), then typed by leading species, closure and
    height. Deviations, marked [H] in ``RULE_NAMES``: no ecozone layer (the taiga rules 7, 46,
    51 that give C-1 are not applied; the non-taiga branch C-2 is), and SCANFI land cover stands
    in for the kNN "vegetated" and NALCMS layers (water, rock, shrub, herbs, bryoid).
    """
    lc = np.asarray(lc)
    shape = lc.shape
    g = {k: np.nan_to_num(np.asarray(cc[k], np.float32)) for k in SPECIES}
    cl = np.nan_to_num(np.asarray(closure, np.float32))
    ht = np.nan_to_num(np.asarray(height, np.float32))
    code = np.zeros(shape, np.int16)
    rule = np.zeros(shape, np.int16)

    def put(mask, c, r):
        m = mask & (rule == 0)
        code[m] = c
        rule[m] = r

    valid = (lc >= 1) & (lc <= 8)
    put(valid & (lc == LC_WATER), WATER, 100)
    put(valid & (lc == LC_ROCK), NONFUEL, 101)

    bleaf = g["broadleaf"] + g["tamarack"]
    nleaf = sum(g[k] for k in SPECIES if k not in ("broadleaf", "tamarack"))
    total = bleaf + nleaf
    with np.errstate(invalid="ignore", divide="ignore"):
        needle = np.where(total > 0, 100.0 * nleaf / np.where(total > 0, total, 1), 0.0)
        share = {k: np.where(total > 0, g[k] / np.where(total > 0, total, 1), 0.0)
                 for k in SPECIES}
    # leading species (rank 0) and the 2nd / 3rd (ties: SPECIES order)
    stack = np.stack([g[k] for k in SPECIES])
    order = np.argsort(-stack, axis=0, kind="stable")
    sp1 = order[0]
    idx = {k: i for i, k in enumerate(SPECIES)}

    def in_2nd_3rd(name: str, min_share: float) -> np.ndarray:
        i = idx[name]
        return (((order[1] == i) | (order[2] == i)) & (share[name] >= min_share)
                & (g[name] > 0))

    forest = valid & (rule == 0) & np.isin(lc, (LC_BROADLEAF, LC_CONIFER, LC_MIXED)) & (cl >= 10)
    open_ = valid & (rule == 0) & ~forest

    # open stands (closure < 10 %, or non-treed vegetated land cover)
    bs_or_tam = np.zeros(shape, bool)
    for name in ("blackSpruce", "tamarack"):
        i = idx[name]
        bs_or_tam |= ((order[0] == i) | (order[1] == i) | (order[2] == i)) & (g[name] > 0)
    put(open_ & (lc == LC_SHRUB), m12(25), 112)
    put(open_ & ~bs_or_tam, O1, 111)
    put(open_, D12, 110)

    # forest: deciduous / mixedwood / coniferous
    decid = forest & (100.0 - needle > 75.0)
    conif = forest & (needle > 75.0)
    mixed = forest & ~decid & ~conif
    put(decid, D12, 120)
    put(mixed & (cl <= 30), D12, 130)
    put(mixed & (needle < 42), m12(35), 131)
    put(mixed & (needle <= 58), m12(50), 132)
    put(mixed, m12(65), 133)

    lead = {k: conif & (sp1 == idx[k]) for k in SPECIES}
    bs = lead["blackSpruce"]
    bs_jp = bs & in_2nd_3rd("jackPine", 0.15)
    put(bs_jp & (cl >= 50) & (ht < 10), C4, 145)
    put(bs_jp & (cl >= 50), C2, 146)
    put(bs_jp & (ht < 10), C2, 148)
    put(bs_jp, C3, 149)
    put(bs & (cl < 30), C1, 141)
    put(bs & (cl < 70), C2, 142)
    put(bs & (ht < 7), C4, 143)
    put(bs, C2, 144)
    oc = lead["otherConiferous"]
    put(oc & (cl < 30), D12, 150)
    put(oc, C2, 151)
    bf = lead["balsamFir"]
    put(bf & (cl < 30), m12(25), 152)
    put(bf & (cl <= 70), C2, 153)
    put(bf, C4, 154)
    pine = lead["jackPine"] | lead["lodgepolePine"]
    pine_wet = pine & (in_2nd_3rd("blackSpruce", 0.15001) | in_2nd_3rd("tamarack", 0.15001))
    put(pine & (cl < 40) & pine_wet, m12(50), 156)
    put(pine & (cl < 40), C7, 155)
    put(pine & (ht >= 7), C3, 157)
    put(pine, C4, 158)
    wp = lead["whiteRedPine"]
    put(wp & (cl < 30), C7, 160)
    put(wp, C5, 161)
    put(lead["ponderosaPine"], C7, 162)
    df = lead["douglasFir"]
    put(df & (cl <= 40), C7, 163)
    put(df & (cl <= 60), C3, 164)
    put(df, C4, 165)
    other = conif & (lead["broadleaf"] | lead["tamarack"])
    put(other & (cl >= 50) & (ht < 10), C4, 170)
    put(other & (cl >= 50), C2, 171)
    put(other & (ht < 10), C2, 172)
    put(other, C3, 173)
    return code, rule


# ── Alberta grid geometry ────────────────────────────────────────────────────────────────────

def ab_profile() -> dict:
    """Profile (crs, transform, width, height) of the Alberta Wildfire 2025 COG."""
    import rasterio

    with rasterio.open(AB_COG) as src:
        return {"crs": src.crs, "transform": src.transform, "width": src.width,
                "height": src.height}


def write_cog(path: Path, arr: np.ndarray, prof: dict, nodata: int, tags: dict) -> None:
    """Write a deflate-compressed, tiled GeoTIFF with overviews off (windowed reads only)."""
    import rasterio
    from rasterio.shutil import copy as rcopy

    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=prof["width"], height=prof["height"],
                       count=1, dtype=arr.dtype, crs=prof["crs"], transform=prof["transform"],
                       nodata=nodata, tiled=True, blockxsize=512, blockysize=512,
                       compress="deflate", predictor=2, zlevel=9) as dst:
        dst.write(arr, 1)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
    rcopy(tmp, path, driver="COG", compress="DEFLATE", predictor="2", level="9",
          blocksize="512", overviews="NONE")
    tmp.unlink()


# ── 1. NBAC burn key ─────────────────────────────────────────────────────────────────────────

def _as_date(v):
    """A date from a datetime / date / pandas Timestamp / ISO string; None for missing (None,
    NaN, NaT). No pandas needed (the tests run without it)."""
    from datetime import date, datetime

    if v is None:
        return None
    try:
        if v != v:  # NaN / NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _end_key(row) -> int:
    """Restore key of one NBAC polygon: latest recorded end date as YYYYDDD, else YYYY366."""
    year = int(row["YEAR"])
    ends = [_as_date(row.get(c)) for c in ("AG_EDATE", "HS_EDATE")]
    ends = [e for e in ends if e is not None and e.year == year]  # other years: typos here
    if not ends:
        return year * 1000 + 366
    e = max(ends)
    return e.year * 1000 + e.timetuple().tm_yday


def build_burns() -> Path:
    import pyogrio
    from rasterio.features import rasterize
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds

    prof = ab_profile()
    shape = (prof["height"], prof["width"])
    key = np.zeros(shape, np.int32)
    left_b, bottom_b, right_b, top_b = array_bounds(*shape, prof["transform"])
    stats = {}
    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        src = f"/vsizip/{RAW_NBAC}/NBAC_{y}_{NBAC_RELEASE}.zip/NBAC_{y}_{NBAC_RELEASE}.shp"
        crs = pyogrio.read_info(src)["crs"]
        bb = transform_bounds(prof["crs"], crs, left_b, bottom_b, right_b, top_b, densify_pts=41)
        df = pyogrio.read_dataframe(src, bbox=bb, columns=["YEAR", "AG_EDATE", "HS_EDATE",
                                                           "ADMIN_NAME", "PRESCRIBED"])
        if not len(df):
            stats[y] = {"polygons": 0, "cells": 0}
            continue
        df = df.to_crs(prof["crs"])
        df["key"] = [_end_key(r) for _, r in df.iterrows()]
        df = df.sort_values("key")
        r = rasterize(((g, int(k)) for g, k in zip(df.geometry, df["key"]) if g is not None),
                      out_shape=shape, transform=prof["transform"], fill=0, dtype="int32")
        np.maximum(key, r, out=key)
        n_end = int((df["key"] % 1000 != 366).sum())
        stats[y] = {"polygons": int(len(df)), "with_end_date": n_end, "cells": int((r > 0).sum())}
        log.info("NBAC %d: %s", y, stats[y])
        del r
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "ab_burn_key_100m.tif"
    write_cog(dest, key, prof, 0, {
        "content": "latest NBAC restore key per cell (YYYYDDD end date, or YYYY366 when no end "
                   "date is recorded); 0 = no burn 1985-2025",
        "source": f"NBAC {NBAC_RELEASE} (Hall et al. 2020), years {FIRST_YEAR}-{LAST_YEAR}",
        "grid": "Alberta Wildfire FBP 2025 grid (100 m, EPSG:3400)",
        "builder": "scripts/fuelgrid/backcast.py burns"})
    (OUT / "burns_stats.json").write_text(json.dumps(stats, indent=1))
    log.info("wrote %s", dest)
    return dest


# ── 2. SCANFI epochs ─────────────────────────────────────────────────────────────────────────

_local = threading.local()


def _mirror_path(layer: str, epoch: int) -> Path:
    return MIRROR / str(epoch) / f"{layer}.tif"


def _scanfi(layer: str, epoch: int):
    """SCANFI layer: the local partial mirror when built (``mirror``), else the NRCan server."""
    import rasterio

    cache = getattr(_local, "ds", None)
    if cache is None:
        cache = _local.ds = {}
    k = (layer, epoch)
    if k not in cache:
        local = _mirror_path(layer, epoch)
        cache[k] = rasterio.open(local if (MIRROR / str(epoch) / "complete").exists()
                                 else "/vsicurl/" + SCANFI.format(layer=layer, epoch=epoch))
    return cache[k]


def needed_blocks(epoch: int) -> tuple[dict, list[tuple[int, int, int]]]:
    """SCANFI 256 x 256 blocks under the Alberta cells burned after ``epoch`` (each 100 m cell's
    four corners), as runs of consecutive blocks per block row: (block row, first col, last col).
    Returns (SCANFI profile, runs)."""
    import rasterio
    from pyproj import Transformer

    prof = ab_profile()
    with rasterio.open(OUT / "ab_burn_key_100m.tif") as k:
        key = k.read(1)
    need = key > (epoch + 1) * 1000
    del key
    with rasterio.open(AB_COG) as src:
        for r0 in range(0, prof["height"], 1024):  # strip by strip: drop cells outside Alberta
            h = min(1024, prof["height"] - r0)
            ab = src.read(1, window=rasterio.windows.Window(0, r0, prof["width"], h))
            need[r0:r0 + h] &= ~np.isin(ab, (0, -1))
    r, c = np.nonzero(need)
    del need
    s = _scanfi("att_closure", epoch)
    sp = {"crs": s.crs, "transform": s.transform, "width": s.width, "height": s.height,
          "nodata": s.nodata}
    tr = Transformer.from_crs(prof["crs"], s.crs, always_xy=True)
    inv = ~s.transform
    T = prof["transform"]
    blocks = set()
    for dx in (-50.0, 50.0):
        for dy in (-50.0, 50.0):
            X, Y = tr.transform(T.c + (c + 0.5) * T.a + dx, T.f + (r + 0.5) * T.e + dy)
            col, row = inv * (np.asarray(X), np.asarray(Y))
            blocks |= set(np.unique(np.floor(row / 256).astype(np.int64) * 100000
                                    + np.floor(col / 256).astype(np.int64)).tolist())
    rows: dict[int, list[int]] = {}
    for b in blocks:
        rows.setdefault(b // 100000, []).append(b % 100000)
    runs = []
    for br, cols in sorted(rows.items()):
        cols = sorted(cols)
        start = prev = cols[0]
        for x in cols[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            runs.append((br, start, prev))
            if x is not None:
                start = prev = x
    return sp, runs


def build_mirror(epoch: int, workers: int = 4) -> None:
    """Local sparse copy of the 13 SCANFI layers of ``epoch``, only the blocks under Alberta
    cells burned after it (one range read per layer and run of consecutive blocks). Resumable."""
    import rasterio
    from rasterio.windows import Window

    os.environ.update(GDAL_ENV)
    sp, runs = needed_blocks(epoch)
    d = MIRROR / str(epoch)
    d.mkdir(parents=True, exist_ok=True)
    (d / "complete").unlink(missing_ok=True)
    br0 = min(r[0] for r in runs)
    br1 = max(r[0] for r in runs)
    bc0 = min(r[1] for r in runs)
    bc1 = max(r[2] for r in runs)
    ox, oy = bc0 * 256, br0 * 256
    W = min(sp["width"], (bc1 + 1) * 256) - ox
    H = min(sp["height"], (br1 + 1) * 256) - oy
    from rasterio.windows import transform as wtr
    tr = wtr(Window(ox, oy, W, H), sp["transform"])
    for layer in LAYERS:
        p = _mirror_path(layer, epoch)
        if not p.exists():
            with rasterio.open(p, "w", driver="GTiff", width=W, height=H, count=1,
                               dtype="uint8", crs=sp["crs"], transform=tr, nodata=255,
                               tiled=True, blockxsize=256, blockysize=256, compress="deflate",
                               sparse_ok=True):
                pass
    done_p = d / "runs_done.json"
    done = set(map(tuple, json.loads(done_p.read_text()))) if done_p.exists() else set()
    todo = [r for r in runs if tuple(r) not in done]
    log.info("mirror %d: %d blocks in %d runs (%d to do), local %d x %d px", epoch,
             sum(c1 - c0 + 1 for _, c0, c1 in runs), len(runs), len(todo), W, H)
    t0 = time.time()

    def fetch(run):
        br, c0, c1 = run
        win = Window(c0 * 256, br * 256, min(sp["width"], (c1 + 1) * 256) - c0 * 256,
                     min(sp["height"], (br + 1) * 256) - br * 256)
        data = {}
        for layer in LAYERS:
            for attempt in range(5):
                try:
                    data[layer] = _scanfi(layer, epoch).read(1, window=win)
                    break
                except Exception as exc:
                    log.warning("mirror %s %s: %r", layer, run, exc)
                    _local.ds = {}
                    time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"mirror {layer} {run} failed")
        return run, win, data

    outs = {layer: rasterio.open(_mirror_path(layer, epoch), "r+") for layer in LAYERS}
    try:
        with ThreadPoolExecutor(workers) as ex:
            for n, f in enumerate(as_completed([ex.submit(fetch, r) for r in todo]), 1):
                run, win, data = f.result()
                lw = Window(win.col_off - ox, win.row_off - oy, win.width, win.height)
                for layer, a in data.items():
                    outs[layer].write(a, 1, window=lw)
                done.add(tuple(run))
                if n % 50 == 0 or n == len(todo):
                    done_p.write_text(json.dumps(sorted(done)))
                    log.info("mirror %d: %d/%d runs, %.1f min", epoch, n, len(todo),
                             (time.time() - t0) / 60)
    finally:
        for o in outs.values():
            o.close()
        done_p.write_text(json.dumps(sorted(done)))
    if len(done) >= len(runs):
        (d / "complete").write_text("ok")


def _tile_attrs(epoch: int, transform, shape, crs) -> dict[str, np.ndarray]:
    """SCANFI layers of ``epoch`` on one Alberta tile (100 m): mean or mode of the 30 m cells."""
    from rasterio.enums import Resampling
    from rasterio.transform import array_bounds
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    out = {}
    b = array_bounds(*shape, transform)  # (west, south, east, north) in EPSG:3400
    for layer in LAYERS:
        for attempt in range(4):
            try:
                src = _scanfi(layer, epoch)
                lb = transform_bounds(crs, src.crs, *b, densify_pts=21)
                win = from_bounds(*lb, src.transform)
                c0 = max(0, int(math.floor(win.col_off)) - 4)
                r0 = max(0, int(math.floor(win.row_off)) - 4)
                c1 = min(src.width, int(math.ceil(win.col_off + win.width)) + 4)
                r1 = min(src.height, int(math.ceil(win.row_off + win.height)) + 4)
                window = Window(c0, r0, c1 - c0, r1 - r0)
                data = src.read(1, window=window)
                is_lc = layer == "att_nfiLandcover"
                dst = np.zeros(shape, np.float32) if not is_lc else np.zeros(shape, np.uint8)
                reproject(source=data.astype(np.float32) if not is_lc else data,
                          destination=dst, src_transform=src.window_transform(window),
                          src_crs=src.crs, src_nodata=src.nodata, dst_transform=transform,
                          dst_crs=crs, dst_nodata=np.nan if not is_lc else 0,
                          resampling=Resampling.mode if is_lc else Resampling.average)
                out[layer] = dst
                break
            except Exception as exc:  # network: retry with fresh handles
                log.warning("SCANFI %s %d: %r (attempt %d)", layer, epoch, exc, attempt + 1)
                _local.ds = {}
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"SCANFI {layer} {epoch}: read failed")
    return out


def build_epoch(epoch: int, workers: int = 8) -> Path:
    import rasterio
    from rasterio.windows import Window
    from rasterio.windows import transform as win_transform

    os.environ.update(GDAL_ENV)
    prof = ab_profile()
    H, W = prof["height"], prof["width"]
    with rasterio.open(OUT / "ab_burn_key_100m.tif") as src:
        key = src.read(1)

    def need_of(r, c, h, w):
        """Cells burned after the epoch (key > (E+1)*1000) inside the Alberta grid's extent."""
        with rasterio.open(AB_COG) as src:
            ab = src.read(1, window=Window(c, r, w, h))
        return (key[r:r + h, c:c + w] > (epoch + 1) * 1000) & ~np.isin(ab, (0, -1))

    codes = np.zeros((H, W), np.int16)
    rules = np.zeros((H, W), np.uint8)
    tiles = [(r, c) for r in range(0, H, TILE) for c in range(0, W, TILE)
             if (key[r:r + TILE, c:c + TILE] > (epoch + 1) * 1000).any()]
    log.info("epoch %d: %d candidate tiles", epoch, len(tiles))
    t0 = time.time()

    def work(rc):
        r, c = rc
        h, w = min(TILE, H - r), min(TILE, W - c)
        need = need_of(r, c, h, w)
        if not need.any():
            return r, c, need, None, None
        tr = win_transform(Window(c, r, w, h), prof["transform"])
        a = _tile_attrs(epoch, tr, (h, w), prof["crs"])
        code, rule = type_scanfi(a["att_nfiLandcover"], a["att_closure"], a["att_height"],
                                 {s: a[f"spsCC_{s}"] for s in SPECIES})
        return r, c, need, code, rule

    done = 0
    n_need = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(work, t) for t in tiles]
        for f in as_completed(futs):
            r, c, m, code, rule = f.result()
            done += 1
            n_need += int(m.sum())
            if code is not None:
                h, w = code.shape
                codes[r:r + h, c:c + w][m] = code[m]
                rules[r:r + h, c:c + w][m] = (rule[m] - 99).clip(0, 255)
            if done % 200 == 0 or done == len(tiles):
                log.info("epoch %d: %d/%d tiles, %.1f min", epoch, done, len(tiles),
                         (time.time() - t0) / 60)
    del key
    dest = OUT / f"ab_prefire_scanfi_{epoch}_100m.tif"
    write_cog(dest, codes, prof, 0, {
        "content": f"pre-fire FBP fuel (Alberta Wildfire codes) typed from SCANFI v2 epoch "
                   f"{epoch}, only for cells NBAC records as burned after {epoch} "
                   f"(restore key > {(epoch + 1) * 1000}); 0 = no value",
        "source": f"SCANFI v2 epoch {epoch} (Guindon et al. 2026, OGL-Canada); typing: CFS "
                  "national FBP decision rules (Swystun, Taylor & Simpson 2018)",
        "grid": "Alberta Wildfire FBP 2025 grid (100 m, EPSG:3400)",
        "builder": "scripts/fuelgrid/backcast.py epoch"})
    typed = codes != 0
    u, n = np.unique(rules[typed], return_counts=True)
    cu, cn = np.unique(codes[typed], return_counts=True)
    st = {"epoch": epoch, "cells_needed": n_need, "cells_typed": int(typed.sum()),
          "tiles": len(tiles), "minutes": round((time.time() - t0) / 60, 1),
          "rules": {int(k) + 99: int(v) for k, v in zip(u, n)},
          "codes": {int(k): int(v) for k, v in zip(cu, cn)}}
    (OUT / f"epoch_{epoch}_stats.json").write_text(json.dumps(st, indent=1))
    log.info("wrote %s: %s", dest, {k: st[k] for k in ("cells_needed", "cells_typed", "minutes")})
    return dest


# ── 3. manifest ──────────────────────────────────────────────────────────────────────────────

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest() -> Path:
    """``backcast.json``: what the API reads (``firesim.data.backcast.BackcastData``)."""
    epochs = {str(e): f"ab_prefire_scanfi_{e}_100m.tif" for e in SCANFI_EPOCHS
              if (OUT / f"ab_prefire_scanfi_{e}_100m.tif").exists()}
    files = {"burn_key": "ab_burn_key_100m.tif", **{f"epoch_{k}": v for k, v in epochs.items()}}
    man = {
        "version": 1,
        "grid_vintage_year": LAST_YEAR,
        "burns": {"file": "ab_burn_key_100m.tif", "source": f"NBAC {NBAC_RELEASE}",
                  "years": [FIRST_YEAR, LAST_YEAR]},
        "epochs": epochs,
        "epoch_source": "SCANFI v2 (5-year epochs), typed with the CFS national FBP decision "
                        "rules (2018)",
        "sha256": {v: _sha256(OUT / v) for v in files.values()},
        "bytes": {v: (OUT / v).stat().st_size for v in files.values()},
    }
    dest = OUT / "backcast.json"
    dest.write_text(json.dumps(man, indent=1))
    log.info("wrote %s", dest)
    return dest


def package() -> Path:
    """``ab_prefire_backcast_v1.tar`` (manifest + rasters, flat) for the data release that
    api/Dockerfile fetches; prints its SHA-256 for ``AB_BACKCAST_SHA256``."""
    import tarfile

    man = json.loads((OUT / "backcast.json").read_text())
    names = ["backcast.json", *man["sha256"]]
    dest = OUT / "ab_prefire_backcast_v1.tar"
    with tarfile.open(dest, "w") as tar:
        for n in names:
            tar.add(OUT / n, arcname=n)
    log.info("wrote %s (%d bytes) sha256 %s", dest, dest.stat().st_size, _sha256(dest))
    return dest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("burns")
    e = sub.add_parser("epoch")
    e.add_argument("epochs", type=int, nargs="+")
    e.add_argument("--workers", type=int, default=8)
    m = sub.add_parser("mirror")
    m.add_argument("epochs", type=int, nargs="+")
    m.add_argument("--workers", type=int, default=4)
    sub.add_parser("manifest")
    sub.add_parser("package")
    a = ap.parse_args()
    if a.cmd == "burns":
        build_burns()
    elif a.cmd == "epoch":
        for ep in a.epochs:
            build_epoch(ep, a.workers)
    elif a.cmd == "mirror":
        for ep in a.epochs:
            build_mirror(ep, a.workers)
    elif a.cmd == "manifest":
        write_manifest()
    else:
        package()


if __name__ == "__main__":
    main()
