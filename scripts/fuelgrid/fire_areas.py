"""Open-data FBP fuel grids for the CFSDS validation fires (northern Alberta and edges).

Applies the merged pipeline (``scripts/fuelgrid``, PR #51) as-is to arbitrary areas: the same
decision key (``rules.decision_key``), the same 88 features (``model.features``) and the same
LightGBM conifer model refitted exactly as ``build.build_all`` fits it for St. Albert (all
Edmonton LiDAR labels, seed ``PARAMS['seed']``). **No retraining on the fire areas.** Inputs are
fetched for each area on its own 20 m grid in EPSG:3400 (Alberta 10-TM Forest; the pipeline's
EPSG:3776 is a 3TM zone centred on 114 W and the fires span 109-121 W).

Two input vintages (``variant``):

- ``current``: the inputs as merged (Meta CHM v1, WorldCover 2021 v200, SCANFI v2/v3 2025, AAFC
  ACI 2025, Sentinel-2 2023-2026 windows, Microsoft footprints 2026, OSM 2026). These post-date
  most fires, so the fire itself (and later fires) are in the fuel map; the validation masks
  those burn scars (``scar_mask``) before use.
- ``prefire``: only inputs older than the fire year Y (fires 2021-2024): Meta CHM (imagery
  <= 2020, checked per area), WorldCover 2020 v100, SCANFI v2 epoch 2020, SCANFI v3 year Y-1,
  ACI Y-1, Sentinel-2 windows of years Y-4..Y-1 (not before 2018). Footprints and OSM stay
  current (wildland areas: negligible).

Deviations from the Edmonton build, fixed before any fire was scored (PREREG in
``scripts/validation/opendata_fire_test.py``): at most ``S2_MAX_DATES_PER_TILE`` clearest
acquisition dates per Sentinel-2 tile and season (data volume: ~100,000 km² instead of
1,400 km²); WorldCover class fractions and footprint fractions from nearest-neighbour
sub-sampling at 5 m / 4 m (the CHM method) instead of area-weighted averaging; OSM from the
Alberta extract only (fires reaching into BC / NWT have no OSM polygons there).

Everything goes to ``$FUELGRID_DATA/fires/`` (outside the repository).

    python -m scripts.fuelgrid.fire_areas areas        # area list from the validation domains
    python -m scripts.fuelgrid.fire_areas fetch [--variant current|prefire] [--only FIRE ...]
    python -m scripts.fuelgrid.fire_areas chm          # Meta CHM for every area (tile streaming)
    python -m scripts.fuelgrid.fire_areas build [--variant ...]
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject, transform_bounds, transform_geom
from rasterio.windows import Window

try:
    from . import config as C
    from .grid import Grid, write_tif
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import Grid, write_tif  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.fire_areas")

CRS = "EPSG:3400"  # NAD83 / Alberta 10-TM (Forest)
CELL = 20.0
FIRES_DIR = C.DATA / "fires"
VALDATA = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
S2_MAX_DATES_PER_TILE = 4
PREFIRE_MIN_YEAR = 2021
GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "8",
            "GDAL_HTTP_RETRY_DELAY": "3", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff,.vrt",
            "VSI_CACHE": "TRUE", "VSI_CACHE_SIZE": str(200 * 2 ** 20), "AWS_NO_SIGN_REQUEST": "YES",
            "GDAL_HTTP_USERAGENT": C.USER_AGENT, "GDAL_CACHEMAX": 512}
NBAC_DIR = C.RAW / "nbac"
NBAC_URL = "https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_{y}_20260513.zip"
WC_URL = {"current": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                     "ESA_WorldCover_10m_2021_v200_{t}_Map.tif",
          "prefire": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v100/2020/map/"
                     "ESA_WorldCover_10m_2020_v100_{t}_Map.tif"}
SCANFI = "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/"
ACI_URL = "https://agriculture.canada.ca/imagery-images/rest/services/annual_crop_inventory/{y}/ImageServer"
CFS2026 = C.RAW / "FBP_FuelTypes_Canada_2026_30m.tif"


# ── Areas ────────────────────────────────────────────────────────────────────
def area_bounds_lonlat(fire_id: str, days: list[int], margin_m: float = 20000.0) -> tuple:
    """Lon/lat box of the union of the harness's per-day working areas (``crop_for_day``)."""
    from firesim.validation.cfsds import FireDomain
    from firesim.validation.harness import crop_for_day

    dom = FireDomain.load(VALDATA / f"domains/{fire_id}.npz")
    boxes = []
    for d in days:
        c = crop_for_day(dom, d, margin_m)
        boxes.append((c.lng_min, c.lat_min, c.lng_max, c.lat_max))
    b = np.array(boxes)
    return (float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max()))


def areas_path() -> Path:
    return FIRES_DIR / "areas.json"


def make_areas() -> list[dict]:
    manifest = json.loads((VALDATA / "manifest.json").read_text())
    out = []
    for m in manifest:
        ll = area_bounds_lonlat(m["fire_id"], m["days"])
        b = transform_bounds("EPSG:4326", CRS, *ll, densify_pts=41)
        g = Grid.from_bounds(b, CELL)
        out.append({"fire_id": m["fire_id"], "year": m["year"], "days": m["days"], "lonlat": ll,
                    "grid": [g.x0, g.y0, g.width, g.height],
                    "prefire": m["year"] >= PREFIRE_MIN_YEAR})
    FIRES_DIR.mkdir(parents=True, exist_ok=True)
    areas_path().write_text(json.dumps(out, indent=1))
    log.info("%d areas, %.0f km² total", len(out), sum(a["grid"][2] * a["grid"][3] for a in out) * 4e-4)
    return out


def load_areas(only: list[str] | None = None) -> list[dict]:
    a = json.loads(areas_path().read_text())
    return [x for x in a if not only or x["fire_id"] in only]


def area_grid(a: dict) -> Grid:
    x0, y0, w, h = a["grid"]
    return Grid(x0, y0, w, h, CELL, CRS)


def area_dir(a: dict, variant: str) -> Path:
    return FIRES_DIR / a["fire_id"] / variant


def shared_dir(a: dict) -> Path:
    """Inputs identical for both variants (CHM, footprints, OSM, CFS 2026, scar mask)."""
    return FIRES_DIR / a["fire_id"] / "shared"


def _wt(path: Path, data, grid: Grid, **kw) -> None:
    write_tif(path, data, grid, **kw)


def _get(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 600) -> bytes:
    h = {"User-Agent": C.USER_AGENT, **(headers or {})}
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # network hiccups on a shared line
            if attempt == 5:
                raise
            log.warning("retry %s (%s)", url[:120], exc)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": C.USER_AGENT})
            with urllib.request.urlopen(req, timeout=900) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(1 << 22)
                    if not b:
                        break
                    f.write(b)
            tmp.rename(dest)
            return dest
        except Exception as exc:
            if attempt == 5:
                raise
            log.warning("retry download %s (%s)", url, exc)
            time.sleep(10 * (attempt + 1))
    return dest


def _fine_counts(src_open, grid: Grid, sub: int, classes: list[int] | None, nodata: int,
                 chunk_rows: int = 50) -> dict:
    """Nearest-neighbour sub-sampling of a categorical / height raster onto ``sub`` x ``sub``
    sub-cells of ``grid``; returns per-cell counts (one array per class) and the valid count."""
    fine = Grid(grid.x0, grid.y0, grid.width * sub, grid.height * sub, grid.cell / sub, grid.crs)
    out = {"valid": np.zeros(grid.shape, np.float32)}
    for k in classes or []:
        out[k] = np.zeros(grid.shape, np.float32)
    with src_open() as src, WarpedVRT(src, crs=grid.crs, transform=fine.transform, width=fine.width,
                                      height=fine.height, resampling=Resampling.nearest,
                                      nodata=nodata) as vrt:
        for r0 in range(0, grid.height, chunk_rows):
            h = min(chunk_rows, grid.height - r0)
            a = vrt.read(1, window=Window(0, r0 * sub, fine.width, h * sub))
            blk = lambda m: m.reshape(h, sub, grid.width, sub).sum((1, 3))  # noqa: E731
            v = a != nodata
            out["valid"][r0:r0 + h] += blk(v)
            for k in classes or []:
                out[k][r0:r0 + h] += blk(a == k)
    return out


# ── WorldCover (class fractions) ─────────────────────────────────────────────
def _wc_tiles(ll: tuple) -> list[str]:
    tiles = []
    for lat in range(int(math.floor(ll[1] / 3) * 3), int(math.floor(ll[3] / 3) * 3) + 1, 3):
        for lon in range(int(math.floor(ll[0] / 3) * 3), int(math.floor(ll[2] / 3) * 3) + 1, 3):
            tiles.append(f"N{lat:02d}W{-lon:03d}")
    return tiles


def worldcover(a: dict, variant: str) -> None:
    dest = area_dir(a, variant) / "worldcover_frac.tif"
    if dest.exists():
        return
    grid = area_grid(a)
    sub = 4  # 5 m sub-cells (WorldCover is ~5 x 9 m here)
    acc = {k: np.zeros(grid.shape, np.float32) for k in C.WC_BANDS}
    valid = np.zeros(grid.shape, np.float32)
    for t in _wc_tiles(a["lonlat"]):
        url = "/vsicurl/" + WC_URL[variant].format(t=t)
        with rasterio.Env(**GDAL_ENV):
            try:
                with rasterio.open(url):
                    pass
            except rasterio.errors.RasterioIOError:
                log.warning("WorldCover tile %s missing", t)
                continue
            c = _fine_counts(lambda: rasterio.open(url), grid, sub, C.WC_BANDS, 0)
        valid += c["valid"]
        for k in C.WC_BANDS:
            acc[k] += c[k]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.stack([np.where(valid > 0, acc[k] / valid, 0) for k in C.WC_BANDS])
    _wt(dest, np.round(out * 100).astype(np.uint8), grid,
        band_names=[f"wc{k}_{C.WC_CLASSES[k]}_pct" for k in C.WC_BANDS],
        tags={"source": "worldcover", "variant": variant, "url": WC_URL[variant], "units": "percent of cell"})


# ── SCANFI v2 / v3 ───────────────────────────────────────────────────────────
SCANFI_V2 = ["att_closure", "att_height", "spsCC_broadleaf", "spsCC_blackSpruce", "spsCC_otherConiferous",
             "spsCC_balsamFir", "spsCC_jackPine", "spsCC_lodgepolePine", "spsCC_tamarack",
             "spsCC_whiteRedPine", "spsCC_douglasFir", "spsCC_ponderosaPine"]
SCANFI_V3 = ["treed_coniferous", "treed_broadleaf", "nonTreed_herbaceous", "nonTreed_lowShrubs",
             "nonTreed_tallShrubs"]


def scanfi_years(a: dict, variant: str) -> tuple[int, int]:
    if variant == "current":
        return 2025, 2025
    y = a["year"]
    return max(e for e in range(1985, 2030, 5) if e <= y - 1), y - 1


def _warp_remote(url: str, grid: Grid, resampling: Resampling, dtype=np.float32) -> np.ndarray:
    out = np.full(grid.shape, np.nan if np.dtype(dtype).kind == "f" else 0, dtype)
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        b = transform_bounds(grid.crs, src.crs, *grid.bounds, densify_pts=41)
        pad = 4 * src.res[0]
        win = rasterio.windows.from_bounds(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, src.transform)
        win = win.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
        arr = src.read(1, window=win)
        reproject(arr, out, src_transform=src.window_transform(win), src_crs=src.crs,
                  dst_transform=grid.transform, dst_crs=grid.crs, resampling=resampling,
                  src_nodata=src.nodata, dst_nodata=out.flat[0])
    return out


def scanfi(a: dict, variant: str) -> None:
    dest = area_dir(a, variant) / "scanfi.tif"
    if dest.exists():
        return
    grid = area_grid(a)
    y2, y3 = scanfi_years(a, variant)
    urls = ([f"/vsicurl/{SCANFI}v2/SCANFI_{n}_{y2}_v2_20260119.tif" for n in SCANFI_V2]
            + [f"/vsicurl/{SCANFI}v3/cog_SCANFI_{n}_{y3}_v3_20260528.tif" for n in SCANFI_V3])
    with ThreadPoolExecutor(4) as ex:
        bands = list(ex.map(lambda u: _warp_remote(u, grid, Resampling.bilinear), urls))
    _wt(dest, np.stack(bands).astype(np.float32), grid, nodata=np.nan,
        band_names=[f"v2_{n}" for n in SCANFI_V2] + [f"v3_{n}" for n in SCANFI_V3],
        tags={"source": "scanfi_v2+scanfi_v3", "v2_year": str(y2), "v3_year": str(y3)})


# ── AAFC ACI ─────────────────────────────────────────────────────────────────
def aci(a: dict, variant: str) -> None:
    dest = area_dir(a, variant) / "aci.tif"
    if dest.exists():
        return
    grid = area_grid(a)
    year = 2025 if variant == "current" else a["year"] - 1
    out = np.zeros(grid.shape, np.uint8)
    step = 4000
    for r0 in range(0, grid.height, step):
        for c0 in range(0, grid.width, step):
            h, w = min(step, grid.height - r0), min(step, grid.width - c0)
            x0, y1 = grid.x0 + c0 * CELL, grid.y0 - r0 * CELL
            bb = (x0, y1 - h * CELL, x0 + w * CELL, y1)
            q = urllib.parse.urlencode({
                "bbox": ",".join(f"{v}" for v in bb), "bboxSR": 3400, "imageSR": 3400,
                "size": f"{w},{h}", "format": "tiff", "pixelType": "U8",
                "interpolation": "RSP_NearestNeighbor", "f": "image"})
            raw = _get(ACI_URL.format(y=year) + "/exportImage?" + q)
            with rasterio.MemoryFile(raw) as mf, mf.open() as src:
                arr = src.read(1)
                if arr.shape != (h, w):
                    raise RuntimeError(f"ACI export returned {arr.shape}, expected {(h, w)}")
            out[r0:r0 + h, c0:c0 + w] = arr
    _wt(dest, out, grid, nodata=0, band_names=["aci_class"], tags={"source": "aci", "year": str(year)})


# ── Sentinel-2 seasonal composites ───────────────────────────────────────────
def s2_windows(a: dict, variant: str) -> dict[str, list[tuple[str, str]]]:
    if variant == "current":
        return C.S2_SEASONS
    y = a["year"]
    md = {s: (w[0][0][5:], w[0][1][5:]) for s, w in C.S2_SEASONS.items()}
    years = {"spring": range(max(2018, y - 4), y), "autumn": range(max(2017, y - 3), y),
             "summer": range(max(2017, y - 2), y), "winter": range(max(2018, y - 4), y)}
    return {s: [(f"{yy}-{md[s][0]}", f"{yy}-{md[s][1]}") for yy in years[s]] for s in C.S2_SEASONS}


def _stac_items(ll: tuple, start: str, end: str) -> list[dict]:
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(ll),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": C.S2_MAX_CLOUD}}, "limit": 200}
    url = C.SOURCES["sentinel2"]["url"] + "/search"
    feats = []
    while True:
        d = json.loads(_get(url, json.dumps(body).encode(), {"Content-Type": "application/json"}))
        feats += d["features"]
        nxt = [lk for lk in d.get("links", []) if lk.get("rel") == "next"]
        if not nxt or not d["features"]:
            break
        body = nxt[0].get("body") or body
        url = nxt[0].get("href", url)
    return feats


def _tile_of(it: dict) -> str:
    p = it["properties"]
    return p.get("grid:code") or p.get("s2:mgrs_tile") or it["id"].split("_")[1]


def select_items(items: list[dict], season: str, k: int = S2_MAX_DATES_PER_TILE) -> list[dict]:
    """Per MGRS tile: the ``k`` acquisition dates with the lowest scene cloud cover (one item per
    tile and date: the latest processing). Snow-free seasons drop scenes with >= 20 % snow, as
    the pipeline does."""
    if season != "winter":
        items = [it for it in items if it["properties"].get("s2:snow_ice_percentage", 0) < 20]
    by_tile_date: dict[tuple, dict] = {}
    for it in items:
        key = (_tile_of(it), it["properties"]["datetime"][:10])
        old = by_tile_date.get(key)
        if old is None or it["properties"].get("s2:processing_baseline", "") > \
                old["properties"].get("s2:processing_baseline", ""):
            by_tile_date[key] = it
    by_tile = defaultdict(list)
    for (t, _), it in by_tile_date.items():
        by_tile[t].append(it)
    out = []
    for t, its in by_tile.items():
        its.sort(key=lambda i: (i["properties"]["eo:cloud_cover"], i["properties"]["datetime"]))
        out += its[:k]
    return out


def _scale(item: dict, band: str) -> tuple[float, float]:
    rb = item["assets"][band].get("raster:bands", [{}])[0]
    offset = 0.0 if item["properties"].get("earthsearch:boa_offset_applied") else float(rb.get("offset", 0.0))
    return float(rb.get("scale", 1e-4)), offset


def _read_s2(href: str, grid: Grid, resampling: Resampling, overview: int | None) -> np.ndarray:
    kw = {"overview_level": overview} if overview is not None else {}
    for attempt in range(5):
        try:
            with rasterio.Env(**GDAL_ENV), rasterio.open("/vsicurl/" + href, **kw) as src, \
                    WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                              height=grid.height, resampling=resampling, nodata=0) as vrt:
                return vrt.read(1)
        except rasterio.errors.RasterioIOError as exc:
            if attempt == 4:
                raise
            log.warning("retry %s (%s)", href, exc)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def sentinel2(a: dict, variant: str, workers: int = 6) -> None:
    grid = area_grid(a)
    windows = s2_windows(a, variant)
    for season, wins in windows.items():
        dest = area_dir(a, variant) / f"s2_{season}.tif"
        if dest.exists():
            continue
        t0 = time.time()
        items = []
        for s, e in wins:
            items += _stac_items(a["lonlat"], s, e)
        items = select_items(items, season)
        winter = season == "winter"
        log.info("%s %s S2 %s: %d items (%d tiles)", a["fire_id"], variant, season, len(items),
                 len({_tile_of(i) for i in items}))
        if not items:
            comp = np.full((len(C.S2_BANDS) + 1,) + grid.shape, np.nan, np.float32)
            comp[-1] = 0
            _wt(dest, comp, grid, nodata=np.nan, band_names=C.S2_BANDS + ["n_dates"],
                tags={"source": "sentinel2", "season": season, "windows": wins, "scenes": []})
            continue
        with ThreadPoolExecutor(workers) as ex:
            scls = list(ex.map(lambda it: _read_s2(it["assets"]["scl"]["href"], grid, Resampling.nearest, None), items))
        valid = [np.isin(s, C.S2_VALID_SCL_WINTER if winter else C.S2_VALID_SCL) for s in scls]
        del scls
        by_date = defaultdict(list)
        for i, it in enumerate(items):
            by_date[it["properties"]["datetime"][:10]].append(i)
        dates = sorted(by_date)
        comp = np.full((len(C.S2_BANDS) + 1,) + grid.shape, np.nan, np.float32)
        for bi, band in enumerate(C.S2_BANDS):
            ten_m = band in ("blue", "green", "red", "nir")
            with ThreadPoolExecutor(workers) as ex:
                raw = list(ex.map(lambda it: _read_s2(it["assets"][band]["href"], grid, Resampling.average,
                                                      0 if ten_m else None), items))
            scales = [_scale(it, band) for it in items]
            step = 256
            for r0 in range(0, grid.height, step):
                rs = slice(r0, min(r0 + step, grid.height))
                per_date = []
                for d in dates:
                    acc = np.zeros((rs.stop - rs.start, grid.width), np.float64)
                    n = np.zeros(acc.shape, np.int16)
                    for i in by_date[d]:
                        s, o = scales[i]
                        r = raw[i][rs]
                        ok = valid[i][rs] & (r > 0)
                        acc[ok] += r[ok] * s + o
                        n[ok] += 1
                    with np.errstate(invalid="ignore", divide="ignore"):
                        per_date.append(np.where(n > 0, acc / n, np.nan).astype(np.float32))
                stack = np.stack(per_date)
                with np.errstate(all="ignore"):
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        comp[bi, rs] = np.nanmedian(stack, axis=0)
                if bi == 0:
                    comp[-1, rs] = np.isfinite(stack).sum(0)
            del raw
        _wt(dest, comp, grid, nodata=np.nan, band_names=C.S2_BANDS + ["n_dates"],
            tags={"source": "sentinel2", "season": season, "windows": wins,
                  "max_dates_per_tile": S2_MAX_DATES_PER_TILE,
                  "scenes": sorted(it["id"] for it in items)})
        log.info("%s %s S2 %s done in %.0f s; median dates per cell %.0f", a["fire_id"], variant, season,
                 time.time() - t0, float(np.median(comp[-1])))


# ── Meta CHM (shared by both variants; imagery <= 2020) ──────────────────────
def _meta_tiles_for(ll: tuple) -> list[str]:
    from shapely.geometry import box, shape
    idx = C.RAW / "meta_chm_tiles.geojson"
    reg = box(*ll)
    feats = json.loads(idx.read_text())["features"]
    return [f["properties"]["tile"] for f in feats if shape(f["geometry"]).intersects(reg)]


def chm_all(areas: list[dict], keep_tiles: bool = False) -> None:
    """Canopy cover (>= 5 m) and height per 20 m cell for every area, streaming tiles."""
    todo = [a for a in areas if not (shared_dir(a) / "meta_chm.tif").exists()]
    if not todo:
        return
    tiles = defaultdict(list)
    for a in todo:
        for t in _meta_tiles_for(a["lonlat"]):
            tiles[t].append(a["fire_id"])
    log.info("CHM: %d tiles for %d areas", len(tiles), len(todo))
    acc = {}
    sub = 20
    base = C.SOURCES["meta_chm"]["url"]
    order = sorted(tiles)

    def fetch(t):
        _download(base.replace("/chm/", "/metadata/") + f"{t}.geojson", C.RAW / f"meta_chm_meta_{t}.geojson")
        return _download(base + f"{t}.tif", C.RAW / f"meta_chm_{t}.tif")

    byid = {a["fire_id"]: a for a in todo}
    with ThreadPoolExecutor(2) as ex:
        futs = {t: ex.submit(fetch, t) for t in order[:2]}
        for i, t in enumerate(order):
            if i + 2 < len(order):
                futs[order[i + 2]] = ex.submit(fetch, order[i + 2])
            path = futs.pop(t).result()
            t0 = time.time()
            with rasterio.open(path) as src:
                tb = transform_bounds(src.crs, CRS, *src.bounds, densify_pts=41)
            for fid in tiles[t]:
                a = byid[fid]
                g = area_grid(a)
                # window of the area grid covered by the tile
                c0 = max(0, int((tb[0] - g.x0) // CELL))
                c1 = min(g.width, int(math.ceil((tb[2] - g.x0) / CELL)))
                r0 = max(0, int((g.y0 - tb[3]) // CELL))
                r1 = min(g.height, int(math.ceil((g.y0 - tb[1]) / CELL)))
                if c1 <= c0 or r1 <= r0:
                    continue
                sg = Grid(g.x0 + c0 * CELL, g.y0 - r0 * CELL, c1 - c0, r1 - r0, CELL, CRS)
                if fid not in acc:
                    acc[fid] = {k: np.zeros(g.shape, np.float32) for k in ("valid", "ge5", "ge2", "h", "h5")}
                A = acc[fid]
                fine = Grid(sg.x0, sg.y0, sg.width * sub, sg.height * sub, 1.0, CRS)
                with rasterio.open(path) as src, WarpedVRT(src, crs=CRS, transform=fine.transform,
                                                           width=fine.width, height=fine.height,
                                                           resampling=Resampling.nearest, nodata=255) as vrt:
                    step = 50
                    for rr in range(0, sg.height, step):
                        h = min(step, sg.height - rr)
                        arr = vrt.read(1, window=Window(0, rr * sub, fine.width, h * sub))
                        blk = lambda m: m.reshape(h, sub, sg.width, sub).sum((1, 3))  # noqa: E731
                        v = arr != 255
                        rs, cs = slice(r0 + rr, r0 + rr + h), slice(c0, c1)
                        A["valid"][rs, cs] += blk(v)
                        A["ge5"][rs, cs] += blk(v & (arr >= C.PARAMS["chm_tree_height_m"]))
                        A["ge2"][rs, cs] += blk(v & (arr >= 2))
                        af = np.where(v, arr, 0).astype(np.float32)
                        A["h"][rs, cs] += blk(af)
                        A["h5"][rs, cs] += blk(np.where(v & (arr >= C.PARAMS["chm_tree_height_m"]), af, 0))
            log.info("CHM tile %s (%d/%d) for %s in %.0f s", t, i + 1, len(order), tiles[t], time.time() - t0)
            # areas whose last tile this was: write
            for fid in tiles[t]:
                if all(fid not in tiles[u] for u in order[i + 1:]) and fid in acc:
                    _write_chm(byid[fid], acc.pop(fid))
            if not keep_tiles and t != "021211312":
                path.unlink(missing_ok=True)


def _write_chm(a: dict, A: dict) -> None:
    g = area_grid(a)
    with np.errstate(invalid="ignore", divide="ignore"):
        cover5 = A["ge5"] / A["valid"]
        cover2 = A["ge2"] / A["valid"]
        hmean = A["h"] / A["valid"]
        h5 = np.where(A["ge5"] > 0, A["h5"] / A["ge5"], 0)
    stack = np.stack([cover5, cover2, hmean, h5, A["valid"] / 400.0]).astype(np.float32)
    _wt(shared_dir(a) / "meta_chm.tif", np.nan_to_num(stack, nan=-1), g, nodata=-1,
        band_names=["cover_ge5m", "cover_ge2m", "height_mean_m", "height_mean_ge5m_m", "valid_frac"],
        tags={"source": "meta_chm", "tiles": _meta_tiles_for(a["lonlat"])})
    log.info("CHM written for %s", a["fire_id"])


def chm_dates(a: dict) -> dict:
    """Area-weighted acquisition dates of the Meta CHM imagery over the area."""
    from shapely.geometry import box, shape
    reg = box(*a["lonlat"])
    areas: dict[str, float] = defaultdict(float)
    for t in _meta_tiles_for(a["lonlat"]):
        p = C.RAW / f"meta_chm_meta_{t}.geojson"
        if not p.exists():
            _download(C.SOURCES["meta_chm"]["url"].replace("/chm/", "/metadata/") + f"{t}.geojson", p)
        for f in json.loads(p.read_text())["features"]:
            g = shape(f["geometry"])
            if g.intersects(reg):
                areas[f["properties"].get("acq_date", "unknown")[:10]] += g.intersection(reg).area
    tot = sum(areas.values()) or 1.0
    ds = sorted(areas)
    return {"min": ds[0] if ds else None, "max": ds[-1] if ds else None,
            "share_by_year": {y: round(sum(v for d, v in areas.items() if d[:4] == y) / tot, 3)
                              for y in sorted({d[:4] for d in ds})}}


# ── Footprints, OSM, CFS 2026 (shared) ───────────────────────────────────────
def buildings(a: dict) -> None:
    dest = shared_dir(a) / "buildings_frac.tif"
    if dest.exists():
        return
    from shapely.geometry import box, shape
    grid = area_grid(a)
    links = C.RAW / "msbuildings_dataset-links.csv"
    _download(C.SOURCES["ms_buildings"]["url"], links)
    reg = box(*a["lonlat"])
    qks = set(_meta_tiles_for(a["lonlat"]))
    rows = [ln.split(",") for ln in links.read_text().splitlines()[1:]]
    urls = [r[2] for r in rows if r[0] == "Canada" and r[1] in qks]
    geoms = []
    for u in urls:
        path = C.RAW / ("msbuildings_" + u.split("quadkey=")[1].split("/")[0] + ".csv.gz")
        _download(u, path)
        with gzip.open(path, "rt") as f:
            for line in f:
                g = json.loads(line)["geometry"]
                if shape(g).intersects(reg):
                    geoms.append(transform_geom("EPSG:4326", CRS, g))
    sub = 5
    frac = np.zeros(grid.shape, np.float32)
    if geoms:
        step = 500
        for r0 in range(0, grid.height, step):
            h = min(step, grid.height - r0)
            sgrid = Grid(grid.x0, grid.y0 - r0 * CELL, grid.width * sub, h * sub, CELL / sub, CRS)
            r = rasterize(((g, 1) for g in geoms), out_shape=sgrid.shape, transform=sgrid.transform,
                          fill=0, dtype="uint8")
            frac[r0:r0 + h] = r.reshape(h, sub, grid.width, sub).mean((1, 3))
    _wt(dest, frac, grid, band_names=["building_frac"], tags={"source": "ms_buildings", "n": len(geoms)})


def osm(a: dict) -> None:
    dest = shared_dir(a) / "osm_landuse.tif"
    if dest.exists():
        return
    import pyogrio
    grid = area_grid(a)
    pbf = C.RAW / "alberta-latest.osm.pbf"
    df = pyogrio.read_dataframe(pbf, layer="multipolygons", bbox=tuple(a["lonlat"]),
                                columns=["landuse", "natural", "leisure", "amenity"])
    df = df[df.geometry.notna()].to_crs(CRS)
    out = np.zeros(grid.shape, np.uint8)
    counts = {}
    for cat in sorted(C.OSM_RULES):
        m = np.zeros(len(df), bool)
        for key, vals in C.OSM_RULES[cat].items():
            m |= df[key].isin(vals).to_numpy()
        sel = df.geometry[m]
        counts[cat] = int(m.sum())
        if len(sel):
            r = rasterize(((g, 1) for g in sel), out_shape=grid.shape, transform=grid.transform,
                          fill=0, dtype="uint8")
            out[r > 0] = cat
    _wt(dest, out, grid, band_names=["osm_category"], tags={"source": "osm_landuse", "categories": counts})


def cfs2026(a: dict) -> None:
    dest = shared_dir(a) / "cfs_fbp_2026.tif"
    if dest.exists():
        return
    grid = area_grid(a)
    out = _warp_remote(str(CFS2026), grid, Resampling.nearest, dtype=np.int16)
    _wt(dest, out, grid, nodata=0, band_names=["cfs_fbp_code"], tags={"source": "cfs_fbp_2026"})


# ── Burn-scar mask (NBAC year >= fire year, plus the fire's own CFSDS cells) ──
def scar_mask(a: dict) -> None:
    dest = shared_dir(a) / "scar_mask.tif"
    if dest.exists():
        return
    import pyogrio
    from firesim.validation.cfsds import DOB_NODATA
    from scipy import ndimage as ndi
    grid = area_grid(a)
    m = np.zeros(grid.shape, np.uint8)
    years_hit = {}
    for y in range(a["year"], 2026):
        z = _download(NBAC_URL.format(y=y), NBAC_DIR / f"NBAC_{y}_20260513.zip")
        df = pyogrio.read_dataframe(f"/vsizip/{z}/NBAC_{y}_20260513.shp", bbox=None, columns=[])
        # NBAC is in Canada Lambert; filter by the area box in that CRS
        bb = transform_bounds("EPSG:4326", df.crs, *a["lonlat"], densify_pts=41)
        df = df.cx[bb[0]:bb[2], bb[1]:bb[3]]
        years_hit[y] = len(df)
        if len(df):
            df = df.to_crs(CRS)
            r = rasterize(((g, 1) for g in df.geometry if g is not None), out_shape=grid.shape,
                          transform=grid.transform, fill=0, dtype="uint8")
            m[r > 0] = 1
    # the fire's own CFSDS day-of-burning cells
    dob_path = VALDATA / f"cfsds/doy/{a['fire_id']}_krig.tif"
    with rasterio.open(dob_path) as src:
        d = src.read(1)
        v = ((d != DOB_NODATA) & (d > 0) & (d < 400)).astype(np.uint8)
        own = np.zeros(grid.shape, np.uint8)
        reproject(v, own, src_transform=src.transform, src_crs=src.crs, dst_transform=grid.transform,
                  dst_crs=CRS, resampling=Resampling.nearest, src_nodata=None)
    m |= own
    m = ndi.binary_dilation(m.astype(bool), structure=np.ones((3, 3), bool), iterations=3).astype(np.uint8)
    _wt(dest, m, grid, band_names=["scar"], tags={"source": "NBAC + CFSDS", "years": years_hit,
                                                  "dilation_m": 60})


# ── Build ────────────────────────────────────────────────────────────────────
_FULL_MODEL = None


def full_model():
    """The St. Albert model of ``build.build_all``: LightGBM + quantile map fitted on all
    Edmonton labels (same training cells, features and seed)."""
    global _FULL_MODEL
    if _FULL_MODEL is not None:
        return _FULL_MODEL
    cache = FIRES_DIR / "edmonton_full_model.pkl"
    import pickle
    if cache.exists():
        _FULL_MODEL = pickle.loads(cache.read_bytes())
        return _FULL_MODEL
    from . import build as B
    from . import labels as L
    from . import model as M
    from . import rules as R
    from .sources import aoi_grid
    eg = aoi_grid("edmonton")
    X, _ = M.features(eg)
    lab = L.load_labels()
    probe = B.key_inputs("edmonton", eg, np.zeros(eg.shape, np.float32))
    built = R.built_mask(probe.bld_frac, probe.wc[50])
    domain = B.edmonton_domain(eg, lab, probe.cover, built)
    train = M.train_mask(lab, domain)
    y = np.nan_to_num(lab["conifer_frac"]).astype(np.float32)
    log.info("refit on %d Edmonton cells", int(train.sum()))
    _FULL_MODEL = M.fit_full(X, y, train)
    FIRES_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(_FULL_MODEL))
    return _FULL_MODEL


def _features_strip(a: dict, variant: str, r0: int, r1: int):
    """model.features on rows r0:r1 of the area grid (1-row halo for the 3 x 3 means)."""
    from . import model as M
    g = area_grid(a)
    h0, h1 = max(r0 - 1, 0), min(r1 + 1, g.height)
    sg = Grid(g.x0, g.y0 - h0 * CELL, g.width, h1 - h0, CELL, CRS)
    vdir, sdir = area_dir(a, variant), shared_dir(a)

    def read(path, grid):
        p = Path(path)
        if not p.exists():
            p = sdir / p.name
        with rasterio.open(p) as src:
            arr = src.read(window=Window(0, h0, g.width, h1 - h0))
        return arr[0] if arr.shape[0] == 1 else arr

    def names(path):
        p = Path(path)
        if not p.exists():
            p = sdir / p.name
        with rasterio.open(p) as src:
            return list(src.descriptions)

    old = (M.read_tif, M.band_names, C.REGION_DIR)
    M.read_tif, M.band_names, C.REGION_DIR = read, names, vdir
    try:
        X, fnames = M.features(sg)
    finally:
        M.read_tif, M.band_names, C.REGION_DIR = old
    return X[:, r0 - h0: r0 - h0 + (r1 - r0)], fnames


def _read(p: Path) -> np.ndarray:
    with rasterio.open(p) as src:
        a = src.read()
    return a[0] if a.shape[0] == 1 else a


def build(a: dict, variant: str) -> Path:
    from . import rules as R
    out = area_dir(a, variant) / "opendata_grid.npz"
    if out.exists():
        return out
    g = area_grid(a)
    vdir, sdir = area_dir(a, variant), shared_dir(a)
    wc = _read(vdir / "worldcover_frac.tif").astype(np.float32) / 100.0
    chm = _read(sdir / "meta_chm.tif")
    cover = np.clip(np.where(chm[0] < 0, 0, chm[0]), 0, 1)
    del chm
    bld = _read(sdir / "buildings_frac.tif")
    valid = np.ones(g.shape, bool)
    built = R.built_mask(bld, wc[C.WC_BANDS.index(50)])
    stands = R.stand_mask(cover, built, valid)
    need = stands & (cover >= C.PARAMS["treed_cell_cover"])
    cf = np.zeros(g.shape, np.float32)
    model = full_model()
    step = 256
    t0 = time.time()
    for r0 in range(0, g.height, step):
        r1 = min(r0 + step, g.height)
        if not need[r0:r1].any():
            continue
        X, _ = _features_strip(a, variant, r0, r1)
        sel = need[r0:r1]
        Xs = X[:, sel].T
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cf[r0:r1][sel] = model.predict(Xs).astype(np.float32)
    log.info("%s %s conifer fraction on %d cells in %.0f s", a["fire_id"], variant, int(need.sum()),
             time.time() - t0)
    x = R.KeyInputs(valid=valid, cover=cover, conifer_frac=cf, bld_frac=bld,
                    wc={k: wc[i] for i, k in enumerate(C.WC_BANDS)},
                    aci=_read(vdir / "aci.tif"), osm=_read(sdir / "osm_landuse.tif"))
    cls, rule, stands = R.decision_key(x)
    np.savez_compressed(out, cls=cls, rule=rule, conifer_frac=cf.astype(np.float16), stands=stands,
                        built=built)
    return out


def fetch(a: dict, variant: str) -> None:
    area_dir(a, variant).mkdir(parents=True, exist_ok=True)
    shared_dir(a).mkdir(parents=True, exist_ok=True)
    for fn in (worldcover, scanfi, aci, sentinel2):
        t = time.time()
        fn(a, variant)
        log.info("%s %s %s %.0f s", a["fire_id"], variant, fn.__name__, time.time() - t)
    for fn in (buildings, osm, cfs2026, scar_mask):
        t = time.time()
        fn(a)
        log.info("%s shared %s %.0f s", a["fire_id"], fn.__name__, time.time() - t)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["areas", "fetch", "chm", "build", "chm-dates", "model"])
    ap.add_argument("--variant", choices=["current", "prefire"], default="current")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    if args.stage == "areas":
        make_areas()
        return 0
    areas = load_areas(args.only)
    if args.variant == "prefire":
        areas = [x for x in areas if x["prefire"]]
    if args.stage == "model":
        full_model()
    elif args.stage == "chm":
        chm_all(areas)
    elif args.stage == "chm-dates":
        out = {x["fire_id"]: chm_dates(x) for x in areas}
        (FIRES_DIR / "chm_dates.json").write_text(json.dumps(out, indent=1))
        print(json.dumps(out, indent=1))
    elif args.stage == "fetch":
        for x in areas:
            fetch(x, args.variant)
    elif args.stage == "build":
        for x in areas:
            build(x, args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
