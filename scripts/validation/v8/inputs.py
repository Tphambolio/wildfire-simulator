"""v8 per-fire inputs (PREREG_LR8 sections 2, 5; addendum A2): grid, pre-fire fuels, curing, stations, weather.

For one fire (training, or a decision fire once drawn) this builds, outside the repository,
``$FIRESIM_V8_DATA/fires/<key>/``:

- ``inputs.npz``: the 125 m UTM run grid (final-perimeter bounds + 16 km), per cell
  ``code`` (CIFFC / CFS national FBP code, both countries), ``fbfm40`` (US only, else 0),
  ``hazard`` (B1 hazardous fuel: Canada C and M types; US FBFM40 timber and shrub groups),
  ``curing`` (0-1, grass receptivity factor from pre-fire summer NDVI), ``zone`` (wind zone index),
  ``elev`` (m, for foliar moisture; 0 where unknown).
- ``meta.json``: grid, sources and vintages, stations, zones, eligibility (station within 50 km with
  >= 50 % hourly coverage over the fire's detection window), retrieval URLs and times.
- ``wx_<station>.csv``: hourly weather (UTC) with hourly FFMC and daily DMC / DC / BUI.

Pre-fire fuels
    Canada: CFS FBP fuel types 2024 (100 m, EPSG:3978, OGL-C; forest state 2020, burns to 2023) with the
    cells NBAC records as burned on or after the fire's start (the fire itself, later fires, and same-year
    fires ending on or after its start) restored from SCANFI v2 typed by the CFS national decision rules
    (``scripts.fuelgrid.backcast.type_scanfi``), epoch 2015 for 2016-2020 fires and 2020 for 2021-2025 fires
    (the FireSim back-cast method, PR #79, generalised from the Alberta grid to the national grid).
    US: LANDFIRE FBFM40 of the latest release before the fire that LFPS serves (2018-2022: LF2016;
    2023: LF2022; 2024: LF2023; 2025: LF2024), crosswalked to FBP as in v7 (grass -> O-1b, shrub -> C-4,
    timber -> C-7, urban -> O-1b, other non-burnable -> non-fuel).
Curing (grass only)
    MODIS MOD13Q1 v6.1 250 m NDVI (Microsoft Planetary Computer, open), median of the 16-day composites of
    1 July - 31 August of the year before the fire; curing = clip((0.75 - NDVI) / 0.40, 0, 1) [H].
Stations and weather
    IEM ASOS/METAR archive (Iowa Environmental Mesonet; keyless open archive of NWS/ECCC observations):
    temperature, RH, 10 m wind speed and direction, hourly; hourly precipitation from ERA5 at the station
    (Open-Meteo archive API; Canadian METARs carry no precipitation), both countries alike. Hourly FFMC by
    Van Wagner (1977) (``firesim.fwi.calculator.hourly_ffmc``) and daily DMC/DC/BUI at local noon from
    1 April (start values FFMC 85, DMC 6, DC 15) [standard start-up].
Wind zones
    Fires whose final perimeter spans > 30 km: HydroBASINS level 6 basins (Lehner & Grill 2013); each zone
    takes the nearest eligible station inside the same basin, else the nearest eligible station; others: one
    zone, the nearest eligible station.
"""
from __future__ import annotations

import io
import os
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
import rasterio
import rasterio.errors
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.features import rasterize
from shapely import contains_xy

from . import common as C

W = Path.home() / "dev/wildfire"
RAW = C.DATA / "raw"
NBAC_ZIP = W / "prefire-data/raw/nbac/NBAC_{y}_20260513.zip"
CFS2024 = W / "validation-data/fuels/FBP_fueltypes_Canada_100m_EPSG3978_20240527.tif"
LF_SERVICE = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_{v}/{v}_{layer}_CONUS/ImageServer/exportImage"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OPENMETEO = "https://archive-api.open-meteo.com/v1/archive"
UA = {"User-Agent": "FireSim-v8-research (open data)"}


def lf_vintage(year: int) -> str:
    return {2023: "LF2022", 2024: "LF2023", 2025: "LF2024"}.get(year, "LF2016")


def scanfi_epoch(year: int) -> int:
    return 2015 if year <= 2020 else 2020


def http(url: str, data: bytes | None = None, tries: int = 8, timeout: int = 300) -> bytes:
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            if k == tries - 1:
                raise
            slow = getattr(e, "code", None) == 429
            time.sleep((60 if slow else 5) * (k + 1))
    return b""


# ── fire records ─────────────────────────────────────────────────────────────────────────────

def fire_record(fire_key: str) -> dict:
    """Final perimeter (EPSG:4326), record start / end, region, year: from the open sources."""
    src, fid = fire_key.split(":", 1)
    if src == "NBAC":
        y = int(fid[:4])
        d = pyogrio.read_dataframe(f"/vsizip/{str(NBAC_ZIP).format(y=y)}/NBAC_{y}_20260513.shp", where=f"GID = '{fid}'")
        d = d.to_crs(4326)
        t0 = pd.concat([d.HS_SDATE, d.AG_SDATE]).min()
        t1 = pd.concat([d.HS_EDATE, d.AG_EDATE]).max()
        return dict(fire_key=fire_key, region="CAN", year=y, geometry=d.geometry.union_all(), t0=t0, t1=t1)
    if src == "MTBS":
        d = pyogrio.read_dataframe(RAW / "mtbs/mtbs_perims_DD.shp", where=f"event_id = '{fid}'").to_crs(4326)
        r = d.iloc[0]
        return dict(fire_key=fire_key, region="US", year=int(str(r.ig_date)[:4]), geometry=d.geometry.union_all(),
                    t0=pd.Timestamp(r.ig_date), t1=pd.NaT)
    if src == "WFIGS":
        p = RAW / "wfigs_perims" / f"{fid}.geojson"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            q = {"where": f"attr_UniqueFireIdentifier = '{fid}'",
                 "outFields": "attr_UniqueFireIdentifier,attr_FireDiscoveryDateTime,attr_FireOutDateTime,poly_GISAcres",
                 "returnGeometry": "true", "outSR": 4326, "f": "geojson"}
            url = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
                   "WFIGS_Interagency_Perimeters/FeatureServer/0/query")
            p.write_bytes(http(url, data=urllib.parse.urlencode(q).encode()))
        d = gpd.read_file(p).sort_values("poly_GISAcres", ascending=False)
        r = d.iloc[0]
        t1 = pd.to_datetime(r.attr_FireOutDateTime, unit="ms") if r.attr_FireOutDateTime else pd.NaT
        return dict(fire_key=fire_key, region="US", year=int(pd.to_datetime(r.attr_FireDiscoveryDateTime, unit="ms").year),
                    geometry=d.geometry.iloc[:1].union_all(), t0=pd.to_datetime(r.attr_FireDiscoveryDateTime, unit="ms"), t1=t1)
    raise ValueError(fire_key)


def make_grid(geom4326) -> dict:
    gs = gpd.GeoSeries([geom4326], crs=4326)
    utm = gs.estimate_utm_crs()
    per = gs.to_crs(utm).iloc[0]
    minx, miny, maxx, maxy = per.bounds
    f = C.CELL_M * C.CAND_F
    x0 = math.floor((minx - C.PAD_M) / f) * f
    y1 = math.ceil((maxy + C.PAD_M) / f) * f
    nx = int(math.ceil((maxx + C.PAD_M - x0) / f)) * C.CAND_F
    ny = int(math.ceil((y1 - (miny - C.PAD_M)) / f)) * C.CAND_F
    return dict(crs=utm.to_string(), x0=x0, y1=y1, nx=nx, ny=ny, cell=C.CELL_M,
                span_km=max(maxx - minx, maxy - miny) / 1000.0)


def grid_transform(g):
    return from_origin(g["x0"], g["y1"], g["cell"], g["cell"])


def warp_to_grid(src_path: str, g: dict, resampling=Resampling.nearest, band: int = 1, dtype="float32", tries: int = 12):
    """Warp a raster (local or /vsicurl/) to the run grid; remote read failures are retried with back-off."""
    for k in range(tries):
        try:
            return _warp_once(src_path, g, resampling, band, dtype)
        except rasterio.errors.RasterioIOError:
            if k == tries - 1 or not str(src_path).startswith("/vsicurl/"):
                raise
            time.sleep(min(600, 60 * (k + 1)))


def _warp_once(src_path, g, resampling, band, dtype):
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="8", GDAL_HTTP_RETRY_DELAY="3", GDAL_HTTP_TIMEOUT="180", GDAL_HTTP_CONNECTTIMEOUT="30",
                      GDAL_HTTP_LOW_SPEED_LIMIT="1000", GDAL_HTTP_LOW_SPEED_TIME="120",
                      CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_CACHEMAX=256):
        with rasterio.open(src_path) as src:
            with WarpedVRT(src, crs=g["crs"], transform=grid_transform(g), width=g["nx"], height=g["ny"],
                           resampling=resampling, src_nodata=src.nodata) as v:
                return v.read(band).astype(dtype)


# ── fuels ────────────────────────────────────────────────────────────────────────────────────

def ab_to_national(code: np.ndarray) -> np.ndarray:
    """SCANFI-typed Alberta Wildfire codes -> CIFFC national codes (same values for C, D-1/2 13, 6xx M-1/2,
    O-1 31 -> 31, non-fuel 101, water 102)."""
    return code.astype(np.int16)


def canada_fuel(rec: dict, g: dict, meta: dict) -> np.ndarray:
    code = warp_to_grid(str(CFS2024), g, Resampling.nearest).astype(np.int16)
    # restore cells burned on/after the fire's start (NBAC), from SCANFI typed by the CFS rules
    nb_crs = pyogrio.read_info(f"/vsizip/{str(NBAC_ZIP).format(y=rec['year'])}/NBAC_{rec['year']}_20260513.shp")["crs"]
    bx = gpd.GeoSeries([box_poly(g)], crs=g["crs"]).to_crs(nb_crs).total_bounds
    t0 = rec["t0"] if pd.notna(rec["t0"]) else pd.Timestamp(f"{rec['year']}-01-01")
    polys = []
    for y in range(rec["year"], 2026):
        d = pyogrio.read_dataframe(f"/vsizip/{str(NBAC_ZIP).format(y=y)}/NBAC_{y}_20260513.shp", bbox=tuple(bx))
        if not len(d):
            continue
        if y == rec["year"]:
            end = pd.concat([d.HS_EDATE, d.AG_EDATE], axis=1).max(axis=1)
            keep = (d.GID == rec["fire_key"][5:]) | end.isna() | (end >= t0)
            d = d[keep.values]
        polys.append(d.to_crs(g["crs"]).geometry)
    restored = np.zeros((g["ny"], g["nx"]), bool)
    if polys:
        geoms = [p for s in polys for p in s if p is not None and not p.is_empty]
        if geoms:
            restored = rasterize(((p, 1) for p in geoms), out_shape=(g["ny"], g["nx"]),
                                 transform=grid_transform(g), fill=0, dtype="uint8").astype(bool)
    meta["fuel_restored_share"] = float(restored.mean())
    if restored.any():
        from scripts.fuelgrid import backcast as B
        e = scanfi_epoch(rec["year"])
        # read SCANFI only over the window of restored cells (the typing is per cell, so the result is identical
        # to reading the whole run grid; it cuts the remote reads several-fold)
        rr, cc_ = np.where(restored.any(axis=1))[0], np.where(restored.any(axis=0))[0]
        r0, r1, c0, c1 = int(rr[0]), int(rr[-1]) + 1, int(cc_[0]), int(cc_[-1]) + 1
        gw = dict(g, x0=g["x0"] + c0 * g["cell"], y1=g["y1"] - r0 * g["cell"], nx=c1 - c0, ny=r1 - r0)
        att = {}
        for layer in B.LAYERS:
            src = scanfi_local(layer, e, gw, rec["fire_key"], meta, restored[r0:r1, c0:c1])
            rs = Resampling.mode if layer == "att_nfiLandcover" else Resampling.average
            att[layer] = warp_to_grid(src, gw, rs)
        typed_w, _ = B.type_scanfi(att["att_nfiLandcover"].astype(np.uint8), att["att_closure"], att["att_height"],
                                   {s: att[f"spsCC_{s}"] for s in B.SPECIES})
        typed = np.zeros(code.shape, np.int16)
        typed[r0:r1, c0:c1] = ab_to_national(typed_w)
        ok = restored & (typed > 0)
        code[ok] = typed[ok]
        meta["scanfi_window"] = [r0, r1, c0, c1]
        meta["scanfi_epoch"] = e
        meta["scanfi_url"] = B.SCANFI.format(layer="<layer>", epoch=e)
    meta["fuel_source"] = "CFS FBP fuel types 2024 100 m (OGL-C) + SCANFI v2 back-cast of NBAC burns on/after the fire start"
    return code


# ── SCANFI local cache (addendum A2, data access only; values identical) ─────────────────────

SCANFI_MIRROR = W / "prefire-data/raw/scanfi"       # sparse Alberta mirror of the same release (backcast.py)
SCANFI_CACHE = C.DATA / "raw" / "scanfi_v8"          # per-fire clips of the national files
SCANFI_MARGIN_PX = 8                                 # SCANFI pixels (30 m) beyond the run-grid window


def _scanfi_window(ds, gw: dict):
    """Pixel window of a SCANFI dataset covering the run-grid window ``gw`` plus a margin."""
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    b = (gw["x0"], gw["y1"] - gw["ny"] * gw["cell"], gw["x0"] + gw["nx"] * gw["cell"], gw["y1"])
    l, bt, r, t = transform_bounds(gw["crs"], ds.crs, *b, densify_pts=51)
    w = from_bounds(l, bt, r, t, ds.transform).round_offsets(op="floor").round_lengths(op="ceil")
    m = SCANFI_MARGIN_PX
    from rasterio.windows import Window
    return Window(w.col_off - m, w.row_off - m, w.width + 2 * m + 1, w.height + 2 * m + 1)


def _mirror_covers(mp: Path, gw: dict, need: np.ndarray) -> bool:
    """True when every source pixel the warp reads for the needed run-grid cells (dilated by one cell) is filled
    in the sparse mirror: the mirror's nodata mask is warped to the run grid with the max kernel."""
    from scipy import ndimage as _nd
    from rasterio.windows import transform as wtr
    from rasterio.warp import reproject
    with rasterio.open(mp) as ds:
        win = _scanfi_window(ds, gw)
        if win.col_off < 0 or win.row_off < 0 or win.col_off + win.width > ds.width or win.row_off + win.height > ds.height:
            return False
        miss = (ds.read(1, window=win) == ds.nodata).astype(np.uint8)
        dst = np.zeros((gw["ny"], gw["nx"]), np.uint8)
        reproject(miss, dst, src_transform=wtr(win, ds.transform), src_crs=ds.crs, dst_transform=grid_transform(gw),
                  dst_crs=gw["crs"], resampling=Resampling.max)
    nd = _nd.binary_dilation(need, iterations=1)
    return not bool((dst[nd] > 0).any())


def scanfi_local(layer: str, epoch: int, gw: dict, fire_key: str, meta: dict, need: np.ndarray) -> str:
    """Local GeoTIFF holding the SCANFI v2 (release 20260119) pixels that the warp to ``gw`` reads at the needed
    (restored) cells.

    Order: an existing per-fire clip; else the sparse Alberta mirror (``prefire-data/raw/scanfi``, built by
    ``scripts/fuelgrid/backcast.py mirror`` from the same files) when every source pixel under the needed cells is
    filled there; else a clip of the national file read once from the NRCan server. Pixels are copied, never
    resampled, so the values at the needed cells are those of the national file."""
    from rasterio.windows import transform as wtr
    out = SCANFI_CACHE / str(epoch) / f"{fire_key.replace(':', '_')}__{layer}.tif"
    src_log = meta.setdefault("scanfi_cache", {})
    if out.exists():
        src_log.setdefault(layer, "cache")
        return str(out)
    mp = SCANFI_MIRROR / str(epoch) / f"{layer}.tif"
    if (SCANFI_MIRROR / str(epoch) / "complete").exists() and mp.exists() and _mirror_covers(mp, gw, need):
        src_log[layer] = "alberta_mirror"
        return str(mp)
    out.parent.mkdir(parents=True, exist_ok=True)
    from scripts.fuelgrid.backcast import SCANFI
    url = "/vsicurl/" + SCANFI.format(layer=layer, epoch=epoch)
    for k in range(12):
        try:
            with rasterio.Env(**GDAL_REMOTE):
                with rasterio.open(url) as ds:
                    win = _scanfi_window(ds, gw)
                    data = ds.read(1, window=win, boundless=True, fill_value=ds.nodata if ds.nodata is not None else 0)
                    tr, crs, nod = wtr(win, ds.transform), ds.crs, ds.nodata
            break
        except rasterio.errors.RasterioIOError:
            if k == 11:
                raise
            time.sleep(min(600, 60 * (k + 1)))
    tmp = out.with_suffix(".part.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1, dtype=data.dtype,
                       crs=crs, transform=tr, nodata=nod, tiled=True, compress="deflate") as d:
        d.write(data, 1)
        d.update_tags(source="NRCan " + url[9:], release="SCANFI v2 20260119", layer=layer, epoch=str(epoch))
    tmp.rename(out)
    src_log[layer] = "nrcan_server_clip"
    return str(out)


GDAL_REMOTE = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="8", GDAL_HTTP_RETRY_DELAY="3",
                   GDAL_HTTP_TIMEOUT="180", GDAL_HTTP_CONNECTTIMEOUT="30", GDAL_HTTP_LOW_SPEED_LIMIT="1000",
                   GDAL_HTTP_LOW_SPEED_TIME="120", GDAL_HTTP_MULTIRANGE="YES",
                   GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", CPL_VSIL_CURL_CHUNK_SIZE="1048576",
                   CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_CACHEMAX=256)


GROUP_OF_FBFM = {}
for _c in list(range(101, 110)) + list(range(121, 125)):
    GROUP_OF_FBFM[_c] = "G"
for _c in range(141, 150):
    GROUP_OF_FBFM[_c] = "S"
for _c in list(range(161, 166)) + list(range(181, 190)) + list(range(201, 205)):
    GROUP_OF_FBFM[_c] = "T"
GROUP_OF_FBFM[91] = "U"
NAT_OF_GROUP = {"G": 32, "S": 4, "T": 7, "U": 32}


def us_fuel(rec: dict, g: dict, meta: dict) -> tuple[np.ndarray, np.ndarray]:
    v = lf_vintage(rec["year"])
    fb = lf_export(v, "FBFM40", g, g["cell"])
    nat = np.full(fb.shape, 101, np.int16)
    for c, grp in GROUP_OF_FBFM.items():
        nat[fb == c] = NAT_OF_GROUP[grp]
    nat[(fb == 98)] = 102
    meta["fuel_source"] = f"LANDFIRE {v} FBFM40 (public domain) crosswalked to FBP as v7"
    meta["landfire_vintage"] = v
    return nat, fb.astype(np.int16)


def lf_export(v: str, layer: str, g: dict, cell: float) -> np.ndarray:
    """LANDFIRE layer on a grid with origin (x0, y1) and ``cell`` size in the grid CRS (nearest)."""
    epsg = int(g["crs"].split(":")[-1])
    nx = int(round(g["nx"] * g["cell"] / cell))
    ny = int(round(g["ny"] * g["cell"] / cell))
    x1, y0 = g["x0"] + nx * cell, g["y1"] - ny * cell
    out = np.zeros((ny, nx), np.int16)
    step = 3000  # LFPS export tile (cells)
    for j0 in range(0, ny, step):
        for i0 in range(0, nx, step):
            w, h = min(step, nx - i0), min(step, ny - j0)
            bb = (g["x0"] + i0 * cell, g["y1"] - (j0 + h) * cell, g["x0"] + (i0 + w) * cell, g["y1"] - j0 * cell)
            q = {"bbox": ",".join(f"{b:.1f}" for b in bb), "bboxSR": epsg, "imageSR": epsg, "size": f"{w},{h}",
                 "format": "tiff", "pixelType": "S16", "interpolation": "RSP_NearestNeighbor", "f": "image"}
            base = (LF_SERVICE.format(v=v, layer=layer) if layer != "Elev" else
                    "https://lfps.usgs.gov/arcgis/rest/services/Landfire_Topo/LF2020_Elev_CONUS/ImageServer/exportImage")
            data = http(base + "?" + urllib.parse.urlencode(q))
            with rasterio.MemoryFile(data) as mf, mf.open() as ds:
                a = ds.read(1)
            out[j0:j0 + h, i0:i0 + w] = a[:h, :w]
    return out


def box_poly(g):
    from shapely.geometry import box
    return box(g["x0"], g["y1"] - g["ny"] * g["cell"], g["x0"] + g["nx"] * g["cell"], g["y1"])


# ── curing ───────────────────────────────────────────────────────────────────────────────────

def curing(rec: dict, g: dict, meta: dict) -> np.ndarray:
    y = rec["year"] - 1
    bb = gpd.GeoSeries([box_poly(g)], crs=g["crs"]).to_crs(4326).total_bounds
    body = json.dumps({"collections": ["modis-13Q1-061"], "bbox": list(map(float, bb)),
                       "datetime": f"{y}-07-01/{y}-08-31", "limit": 100}).encode()
    req = urllib.request.Request(PC_STAC, data=body, headers={**UA, "Content-Type": "application/json"})
    items = json.loads(urllib.request.urlopen(req, timeout=120).read())["features"]
    items = [it for it in items if it["id"].startswith("MOD13Q1")] or items
    stack = []
    for it in items:
        raw = it["assets"]["250m_16_days_NDVI"]["href"]
        href = raw + "?" + pc_token(raw)
        a = warp_to_grid("/vsicurl/" + href, g, Resampling.bilinear)
        a[(a <= -3000) | (a == 0)] = np.nan
        stack.append(a)
    meta["curing_source"] = f"MODIS MOD13Q1 v061 NDVI {y}-07-01..{y}-08-31, {len(items)} items (Planetary Computer)"
    if not stack:
        meta["curing_missing"] = True
        return np.ones((g["ny"], g["nx"]), np.float32)
    # composites of different tiles cover different cells: median over the available values per cell
    with np.errstate(all="ignore"):
        ndvi = np.nanmedian(np.stack(stack), axis=0) * 1e-4
    cur = np.clip((0.75 - ndvi) / 0.40, 0, 1)
    return np.where(np.isfinite(cur), cur, 1.0).astype(np.float32)


_TOKENS: dict = {}


def pc_token(href: str) -> str:
    """SAS token for the blob container of ``href`` (Planetary Computer token endpoint), cached ~45 min."""
    u = urllib.parse.urlparse(href)
    account, container = u.netloc.split(".")[0], u.path.lstrip("/").split("/")[0]
    k = (account, container)
    if k not in _TOKENS or time.time() - _TOKENS[k][1] > 2700:
        j = json.loads(http(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"))
        _TOKENS[k] = (j["token"], time.time())
    return _TOKENS[k][0]


# ── stations and weather ─────────────────────────────────────────────────────────────────────

def stations_table() -> pd.DataFrame:
    rows = []
    for f in sorted((RAW / "iem").glob("*.geojson")):
        for ft in json.load(open(f)).get("features", []):
            p = ft["properties"]
            lon, lat = ft["geometry"]["coordinates"][:2]
            rows.append(dict(sid=ft["id"], network=p.get("network"), lon=lon, lat=lat, elev=p.get("elevation"),
                             begin=p.get("archive_begin"), end=p.get("archive_end")))
    return pd.DataFrame(rows).drop_duplicates("sid")


def iem_hourly(sid: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    p = RAW / "iem_data" / f"{sid}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        q = [("station", sid), ("data", "tmpf"), ("data", "relh"), ("data", "sknt"), ("data", "drct"),
             ("year1", start.year), ("month1", start.month), ("day1", start.day), ("year2", end.year),
             ("month2", end.month), ("day2", end.day),
             ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"), ("direct", "no"),
             ("report_type", 3), ("report_type", 4)]
        p.write_bytes(http(IEM + "?" + urllib.parse.urlencode(q)))
    d = pd.read_csv(p, na_values=["M"])
    if not len(d):
        return d
    d["valid"] = pd.to_datetime(d.valid)
    d["hour"] = d.valid.dt.floor("h")
    return d.groupby("hour").agg(tmpf=("tmpf", "mean"), relh=("relh", "mean"), sknt=("sknt", "mean"),
                                 drct=("drct", lambda s: circmean_deg(s.dropna().values))).reset_index()


def weather_period(year: int, t0) -> tuple[pd.Timestamp, pd.Timestamp]:
    """FWI start-up from 1 April, or 30 days before the record start for fires starting before 1 May
    (start values FFMC 85, DMC 6, DC 15) [standard start-up; addendum A2]; to 10 November."""
    start = pd.Timestamp(f"{year}-04-01")
    if pd.notna(t0) and pd.Timestamp(t0) - pd.Timedelta(days=30) < start:
        start = (pd.Timestamp(t0) - pd.Timedelta(days=30)).normalize()
    return start, pd.Timestamp(f"{year}-11-10 23:00")


def circmean_deg(a):
    if len(a) == 0:
        return np.nan
    r = np.radians(a)
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def era5_precip(lat: float, lon: float, start: pd.Timestamp, end: pd.Timestamp, sid: str) -> pd.Series:
    p = RAW / "era5_precip" / f"{sid}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": f"{start:%Y-%m-%d}",
             "end_date": f"{end:%Y-%m-%d}", "hourly": "precipitation", "timezone": "GMT", "models": "era5"}
        p.write_bytes(http(OPENMETEO + "?" + urllib.parse.urlencode(q)))
    j = json.loads(p.read_text())
    return pd.Series(j["hourly"]["precipitation"], index=pd.to_datetime(j["hourly"]["time"]), dtype=float)


def station_weather(st: pd.Series, year: int, w0=None, w1=None, t0=None) -> pd.DataFrame | None:
    """Hourly UTC series from 1 April: temp C, rh %, ws km/h, wd deg, rain mm, hourly FFMC, daily DMC/DC/BUI."""
    from firesim.fwi.calculator import hourly_ffmc, FWICalculator
    start, end = weather_period(year, t0)
    h = iem_hourly(st.sid, start, end + pd.Timedelta(days=1))
    if h is None or not len(h):
        return None
    idx = pd.date_range(start, end, freq="h")
    h = h.set_index("hour").reindex(idx)
    for c in ("tmpf", "relh", "sknt"):
        h[c] = h[c].interpolate(limit=3)
    h["drct"] = h["drct"].ffill(limit=3)
    out = pd.DataFrame(index=idx)
    out["temp"] = (h.tmpf - 32) * 5 / 9
    out["rh"] = h.relh.clip(1, 100)
    out["ws"] = h.sknt * 1.852
    out["wd"] = h.drct
    out["obs"] = out[["temp", "rh", "ws"]].notna().all(axis=1)
    if w0 is not None and float(out.loc[w0:w1, "obs"].mean() if len(out.loc[w0:w1]) else 0.0) < 0.5:
        return None  # coverage below 50 % over the detection window: not eligible (no ERA5 call)
    out["rain"] = era5_precip(st.lat, st.lon, start, end, st.sid).reindex(idx).fillna(0.0).values
    ff = np.empty(len(out))
    f = 85.0
    for k, (t, rh, ws, r, ok) in enumerate(zip(out.temp.values, out.rh.values, out.ws.values, out.rain.values, out.obs.values)):
        if ok:
            f = hourly_ffmc(float(t), float(rh), float(ws), float(r), f)
        ff[k] = f
    out["ffmc"] = ff
    # daily DMC/DC at local standard noon (UTC offset from longitude), 24 h rain to noon
    off = int(round(-st.lon / 15.0))
    calc = FWICalculator()
    dmc, dc = 6.0, 15.0
    daily = {}
    for day in pd.date_range(start, end.normalize(), freq="D"):
        noon = day + pd.Timedelta(hours=12 + off)
        if noon not in out.index:
            continue
        row = out.loc[noon]
        rain24 = float(out.rain.loc[noon - pd.Timedelta(hours=23):noon].sum())
        if bool(row.obs):
            dmc = calc.calculate_dmc(float(row.temp), float(row.rh), rain24, day.month, dmc)
            dc = calc.calculate_dc(float(row.temp), rain24, day.month, dc)
        daily[day.date()] = (dmc, dc)
    dd = pd.Series({k: v[0] for k, v in daily.items()})
    dcs = pd.Series({k: v[1] for k, v in daily.items()})
    from firesim.fbp.calculator import calculate_bui
    out["dmc"] = [dd.get(t.date(), np.nan) for t in out.index]
    out["dc"] = [dcs.get(t.date(), np.nan) for t in out.index]
    out["bui"] = [calculate_bui(a, b) if np.isfinite(a) else np.nan for a, b in zip(out.dmc, out.dc)]
    return out


# ── build ────────────────────────────────────────────────────────────────────────────────────

def detection_window(fire_key: str, year: int):
    """First and last detection of the fire in the cached FIRMS rows (inputs, not outcomes)."""
    p = C.DATA / "viirs_training" / f"viirs_training_{year}.pkl.gz"
    p2 = C.DATA / "viirs_decision" / f"{fire_key.replace(':', '_')}.pkl.gz"
    for q in (p2, p):
        if q.exists():
            d = pd.read_pickle(q)
            d = d[d.fire_key == fire_key] if "fire_key" in d else d
            if len(d):
                return d.t.min(), d.t.max()
    return None, None


def record_window(rec: dict):
    """Blind-to-hotspots burning window for decision-fire eligibility (addendum A2)."""
    t0 = rec["t0"]
    if rec["fire_key"].startswith("NBAC:") and pd.notna(rec["t1"]):
        return t0, rec["t1"]
    if rec["fire_key"].startswith("WFIGS:") and pd.notna(rec["t1"]):
        return t0, min(rec["t1"], t0 + pd.Timedelta(days=60))
    return t0, t0 + pd.Timedelta(days=30)


def build(fire_key: str, force: bool = False, blind: bool = False) -> dict:
    d = C.fire_dir(fire_key)
    if (d / "meta.json").exists() and not force:
        return json.loads((d / "meta.json").read_text())
    t_start = time.time()
    rec = fire_record(fire_key)
    g = make_grid(rec["geometry"])
    meta = dict(fire_key=fire_key, region=rec["region"], year=rec["year"], grid=g,
                t0=str(rec["t0"]), t1=str(rec["t1"]), built_utc=datetime.now(timezone.utc).isoformat())
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"])
    # stations within 50 km of the final perimeter, with data over the detection window
    st = stations_table()
    sp = gpd.GeoSeries(gpd.points_from_xy(st.lon, st.lat), crs=4326).to_crs(g["crs"])
    st["dist_km"] = sp.distance(per.iloc[0]).values / 1000.0
    st["x"], st["y"] = sp.x.values, sp.y.values
    near = st[st.dist_km <= 50.0].sort_values("dist_km")
    w0, w1 = record_window(rec) if blind else detection_window(fire_key, rec["year"])
    meta["eligibility_window"] = "record dates (blind)" if blind else "VIIRS detection window (training)"
    good = []
    for s in near.itertuples():
        try:
            wx = station_weather(pd.Series(s._asdict()), rec["year"], w0, w1, rec["t0"])
        except Exception as e:  # network or empty archive
            meta.setdefault("station_errors", {})[s.sid] = repr(e)[:200]
            continue
        if wx is None:
            continue
        cov = float(wx.loc[w0:w1, "obs"].mean()) if w0 is not None else 0.0
        if cov >= 0.5:
            wx.to_csv(d / f"wx_{s.sid}.csv", index_label="utc")
            good.append(dict(sid=s.sid, dist_km=float(s.dist_km), x=float(s.x), y=float(s.y), coverage=cov))
    meta["stations"] = good
    meta["eligible_station"] = bool(good)
    meta["detection_window"] = [str(w0), str(w1)]
    if not good:  # ineligible (no station within 50 km): no fuels or curing are built
        meta["seconds"] = time.time() - t_start
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
        C.log_run("inputs", fire_key, "no_station", meta["seconds"])
        return meta
    if rec["region"] == "CAN":
        code = canada_fuel(rec, g, meta)
        fbfm = np.zeros_like(code)
        hazard = ((code >= 1) & (code <= 7)) | ((code >= 400) & (code < 1000)) | np.isin(code, [40, 50, 60, 70, 80, 90])
    else:
        code, fbfm = us_fuel(rec, g, meta)
        hazard = np.isin(fbfm, [c for c, gg in GROUP_OF_FBFM.items() if gg in ("S", "T")])
    cur = curing(rec, g, meta)
    # zones
    zone = np.zeros((g["ny"], g["nx"]), np.int16)
    zst = [good[0]["sid"]] if good else []
    if good and g["span_km"] > 30.0:
        hb = pd.concat([gpd.read_file(f"zip://{RAW}/hydrobasins/hybas_{r}_lev06_v1c.zip",
                                      bbox=tuple(gpd.GeoSeries([box_poly(g)], crs=g["crs"]).to_crs(4326).total_bounds))
                        for r in ("na", "ar")]).to_crs(g["crs"])
        hb = hb[hb.intersects(box_poly(g))].reset_index(drop=True)
        if len(hb) > 1:
            zone = rasterize(((geom, i + 1) for i, geom in enumerate(hb.geometry)), out_shape=zone.shape,
                             transform=grid_transform(g), fill=0, dtype="int16")
            zst = []
            gx = np.array([s["x"] for s in good]); gy = np.array([s["y"] for s in good])
            for i, geom in enumerate([None] + list(hb.geometry)):
                if i == 0:
                    ins = np.zeros(len(good), bool)
                else:
                    ins = contains_xy(geom, gx, gy)
                cand = [k for k in range(len(good)) if ins[k]] or list(range(len(good)))
                if i == 0:
                    zst.append(good[0]["sid"])
                    continue
                c = geom.representative_point()
                k = min(cand, key=lambda k: math.hypot(gx[k] - c.x, gy[k] - c.y))
                zst.append(good[k]["sid"])
            meta["basins"] = [str(v) for v in hb.HYBAS_ID.values]
    meta["zone_station"] = zst
    elev = np.zeros((g["ny"], g["nx"]), np.float32)
    np.savez_compressed(d / "inputs.npz", code=code.astype(np.int16), fbfm40=fbfm.astype(np.int16),
                        hazard=hazard.astype(np.uint8), curing=cur.astype(np.float32), zone=zone, elev=elev)
    meta["seconds"] = time.time() - t_start
    (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    C.log_run("inputs", fire_key, "ok" if good else "no_station", meta["seconds"])
    return meta


if __name__ == "__main__":
    import sys
    C.guard()
    for k in sys.argv[1:]:
        try:
            m = build(k, force=os.environ.get("V8_FORCE") == "1")
            print(k, "eligible" if m["eligible_station"] else "NO STATION", f"{m.get('seconds', 0):.0f}s", flush=True)
        except Exception as e:
            C.log_run("inputs", k, f"error {e!r}"[:200], 0)
            import traceback; traceback.print_exc(); print(k, "ERROR", repr(e), flush=True)
