"""Download open inputs and put each one on the region grid (EPSG:3776, 20 m).

Every fetcher is idempotent: it skips work whose output already exists (delete the file, or pass
``force=True``, to refetch) and records source, version, licence, URL and retrieval date in
``DATA/provenance.json``. No accounts or keys are needed.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject, transform_bounds, transform_geom

try:
    from . import config as C
    from .grid import REGION, Grid, record_provenance, write_tif
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import REGION, Grid, record_provenance, write_tif  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.sources")

GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "5",
            "GDAL_HTTP_RETRY_DELAY": "2", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
            "VSI_CACHE": "TRUE", "AWS_NO_SIGN_REQUEST": "YES", "GDAL_HTTP_USERAGENT": C.USER_AGENT}


def _get(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 300) -> bytes:
    h = {"User-Agent": C.USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _download(url: str, dest, force: bool = False) -> dict:
    """Stream a URL to dest; returns HTTP Last-Modified and size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return {"file": str(dest), "bytes": dest.stat().st_size, "cached": True}
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": C.USER_AGENT})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        lm = r.headers.get("Last-Modified")
        shutil.copyfileobj(r, f, length=1 << 22)
    tmp.rename(dest)
    return {"file": str(dest), "bytes": dest.stat().st_size, "last_modified": lm}


def region_lonlat_bounds(margin_deg: float = 0.02) -> tuple[float, float, float, float]:
    b = transform_bounds(C.CRS, "EPSG:4326", *REGION.bounds)
    return (b[0] - margin_deg, b[1] - margin_deg, b[2] + margin_deg, b[3] + margin_deg)


# ── Boundaries (OSM via Nominatim) ───────────────────────────────────────────
def boundaries(force: bool = False) -> None:
    for aoi in C.AOIS.values():
        dest = C.RAW / f"boundary_{aoi.key}.geojson"
        if dest.exists() and not force:
            continue
        q = urllib.parse.urlencode({"osm_ids": f"R{aoi.osm_relation}", "polygon_geojson": 1, "format": "json"})
        d = json.loads(_get(f"https://nominatim.openstreetmap.org/lookup?{q}"))
        if not d:
            raise RuntimeError(f"Nominatim returned nothing for relation {aoi.osm_relation}")
        feat = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": d[0]["geojson"],
                "properties": {"name": d[0]["display_name"], "osm_relation": aoi.osm_relation}}]}
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(feat))
        record_provenance("osm_boundary", {"files": [str(C.RAW / f"boundary_{k}.geojson") for k in C.AOIS],
                                           "retrieved": datetime.now(timezone.utc).isoformat()})


def boundary_geom(key: str, crs: str = C.CRS):
    from shapely.geometry import shape
    g = json.loads((C.RAW / f"boundary_{key}.geojson").read_text())["features"][0]["geometry"]
    return shape(transform_geom("EPSG:4326", crs, g))


def aoi_grid(key: str) -> Grid:
    aoi = C.AOIS[key]
    if aoi.fixed_bounds:
        return Grid.from_bounds(aoi.fixed_bounds)
    b = boundary_geom(key).buffer(aoi.buffer_m).bounds
    return Grid.from_bounds(b)


def aoi_mask(key: str, grid: Grid) -> np.ndarray:
    aoi = C.AOIS[key]
    geom = boundary_geom(key)
    if aoi.buffer_m:
        geom = geom.buffer(aoi.buffer_m)
    return rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0,
                     dtype="uint8").astype(bool)


# ── ESA WorldCover 2021 (class fractions) ────────────────────────────────────
def worldcover(force: bool = False) -> None:
    dest = C.REGION_DIR / "worldcover_frac.tif"
    if dest.exists() and not force:
        return
    url = C.SOURCES["worldcover"]["url"]
    with rasterio.Env(**GDAL_ENV), rasterio.open("/vsicurl/" + url) as src:
        win = rasterio.windows.from_bounds(*region_lonlat_bounds(), transform=src.transform)
        win = win.round_offsets().round_lengths()
        a = src.read(1, window=win)
        st = src.window_transform(win)
        scrs = src.crs
    out = np.zeros((len(C.WC_BANDS),) + REGION.shape, np.float32)
    for i, k in enumerate(C.WC_BANDS):
        reproject((a == k).astype(np.float32), out[i], src_transform=st, src_crs=scrs,
                  dst_transform=REGION.transform, dst_crs=C.CRS, resampling=Resampling.average)
    write_tif(dest, np.round(out * 100).astype(np.uint8), REGION,
              band_names=[f"wc{k}_{C.WC_CLASSES[k]}_pct" for k in C.WC_BANDS],
              tags={"source": "worldcover", "units": "percent of cell"})
    record_provenance("worldcover", {"window_px": [int(win.width), int(win.height)]})


# ── Meta / WRI 1 m canopy height → canopy cover and height on 20 m cells ─────
def _meta_tiles() -> list[str]:
    from shapely.geometry import box, shape
    idx = C.RAW / "meta_chm_tiles.geojson"
    if not idx.exists():
        _download("https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/tiles.geojson", idx)
    reg = box(*region_lonlat_bounds(0.0))
    feats = json.loads(idx.read_text())["features"]
    return [f["properties"]["tile"] for f in feats if shape(f["geometry"]).intersects(reg)]


def meta_chm(force: bool = False, chunk_rows: int = 1000) -> None:
    dest = C.REGION_DIR / "meta_chm.tif"
    if dest.exists() and not force:
        return
    tiles = _meta_tiles()
    base = C.SOURCES["meta_chm"]["url"]
    sub = 20  # 1 m sub-cells per 20 m cell
    fine = Grid(REGION.x0, REGION.y0, REGION.width * sub, REGION.height * sub, 1.0)
    n_valid = np.zeros(REGION.shape, np.float64)
    n_ge5 = np.zeros(REGION.shape, np.float64)
    n_ge2 = np.zeros(REGION.shape, np.float64)
    sum_h = np.zeros(REGION.shape, np.float64)
    sum_h5 = np.zeros(REGION.shape, np.float64)
    meta_dates = []
    for t in tiles:
        path = C.RAW / f"meta_chm_{t}.tif"
        _download(base + f"{t}.tif", path)
        mpath = C.RAW / f"meta_chm_meta_{t}.geojson"
        try:
            _download(base.replace("/chm/", "/metadata/") + f"{t}.geojson", mpath)
            meta_dates += _meta_dates(mpath)
        except Exception as exc:  # metadata is informative only
            log.warning("CHM metadata %s: %s", t, exc)
        with rasterio.open(path) as src, WarpedVRT(src, crs=C.CRS, transform=fine.transform,
                                                    width=fine.width, height=fine.height,
                                                    resampling=Resampling.nearest, nodata=255) as vrt:
            step = chunk_rows - chunk_rows % sub
            for r0 in range(0, fine.height, step):
                h = min(step, fine.height - r0)
                a = vrt.read(1, window=rasterio.windows.Window(0, r0, fine.width, h))
                rr = slice(r0 // sub, (r0 + h) // sub)
                v = a != 255
                blk = lambda m: m.reshape(h // sub, sub, fine.width // sub, sub).sum((1, 3))  # noqa: E731
                n_valid[rr] += blk(v)
                n_ge5[rr] += blk(v & (a >= C.PARAMS["chm_tree_height_m"]))
                n_ge2[rr] += blk(v & (a >= 2))
                af = np.where(v, a, 0).astype(np.float32)
                sum_h[rr] += blk(af)
                sum_h5[rr] += blk(np.where(a >= C.PARAMS["chm_tree_height_m"], af, 0))
                log.info("CHM %s rows %d/%d", t, r0 + h, fine.height)
    with np.errstate(invalid="ignore", divide="ignore"):
        cover5 = n_ge5 / n_valid
        cover2 = n_ge2 / n_valid
        hmean = sum_h / n_valid
        h5mean = np.where(n_ge5 > 0, sum_h5 / n_ge5, 0)
    stack = np.stack([cover5, cover2, hmean, h5mean, n_valid / (sub * sub)]).astype(np.float32)
    write_tif(dest, np.nan_to_num(stack, nan=-1), REGION, nodata=-1,
              band_names=["cover_ge5m", "cover_ge2m", "height_mean_m", "height_mean_ge5m_m", "valid_frac"],
              tags={"source": "meta_chm", "tiles": tiles})
    record_provenance("meta_chm", {"tiles": tiles, "imagery_dates": _date_summary(meta_dates)})


def _meta_dates(path) -> list[str]:
    d = json.loads(path.read_text())
    out = []
    for f in d.get("features", []):
        for k, v in f.get("properties", {}).items():
            if "date" in k.lower() and isinstance(v, str):
                out.append(v[:10])
    return out


def _date_summary(dates: list[str]) -> dict:
    if not dates:
        return {}
    ds = sorted(dates)
    years = defaultdict(int)
    for d in ds:
        years[d[:4]] += 1
    return {"min": ds[0], "max": ds[-1], "polygons_by_year": dict(years)}


# ── SCANFI v2 / v3 (CFS) ─────────────────────────────────────────────────────
SCANFI_V2 = ["att_closure", "att_height", "spsCC_broadleaf", "spsCC_blackSpruce", "spsCC_otherConiferous",
             "spsCC_balsamFir", "spsCC_jackPine", "spsCC_lodgepolePine", "spsCC_tamarack",
             "spsCC_whiteRedPine", "spsCC_douglasFir", "spsCC_ponderosaPine"]
SCANFI_V3 = ["treed_coniferous", "treed_broadleaf", "nonTreed_herbaceous", "nonTreed_lowShrubs",
             "nonTreed_tallShrubs"]


def _warp_remote(url: str, resampling: Resampling, dtype=np.float32, src_nodata=None) -> np.ndarray:
    out = np.full(REGION.shape, np.nan if np.dtype(dtype).kind == "f" else 0, dtype)
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
        b = transform_bounds(C.CRS, src.crs, *REGION.bounds)
        pad = 4 * src.res[0]
        win = rasterio.windows.from_bounds(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, src.transform)
        win = win.round_offsets().round_lengths()
        a = src.read(1, window=win)
        nd = src.nodata if src_nodata is None else src_nodata
        reproject(a, out, src_transform=src.window_transform(win), src_crs=src.crs,
                  dst_transform=REGION.transform, dst_crs=C.CRS, resampling=resampling,
                  src_nodata=nd, dst_nodata=out.flat[0])
    return out


def scanfi(force: bool = False) -> None:
    dest = C.REGION_DIR / "scanfi.tif"
    if dest.exists() and not force:
        return
    base = "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/"
    urls = ([f"/vsicurl/{base}v2/SCANFI_{n}_2025_v2_20260119.tif" for n in SCANFI_V2]
            + [f"/vsicurl/{base}v3/cog_SCANFI_{n}_2025_v3_20260528.tif" for n in SCANFI_V3])
    with ThreadPoolExecutor(6) as ex:
        bands = list(ex.map(lambda u: _warp_remote(u, Resampling.bilinear), urls))
    write_tif(dest, np.stack(bands).astype(np.float32), REGION, nodata=np.nan,
              band_names=[f"v2_{n}" for n in SCANFI_V2] + [f"v3_{n}" for n in SCANFI_V3],
              tags={"source": "scanfi_v2+scanfi_v3", "year": "2025"})
    record_provenance("scanfi_v2", {"layers": SCANFI_V2, "year": 2025})
    record_provenance("scanfi_v3", {"layers": SCANFI_V3, "year": 2025})


# ── AAFC Annual Crop Inventory 2025 ──────────────────────────────────────────
def aci(force: bool = False) -> None:
    dest = C.REGION_DIR / "aci2025.tif"
    if dest.exists() and not force:
        return
    b = REGION.bounds
    q = urllib.parse.urlencode({
        "bbox": f"{b[0]},{b[1]},{b[2]},{b[3]}", "bboxSR": 3776, "imageSR": 3776,
        "size": f"{REGION.width},{REGION.height}", "format": "tiff", "pixelType": "U8",
        "interpolation": "RSP_NearestNeighbor", "f": "image"})
    raw = C.RAW / "aci2025_region.tif"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(_get(C.SOURCES["aci"]["url"] + "/exportImage?" + q))
    with rasterio.open(raw) as src:
        a = src.read(1)
        if a.shape != REGION.shape:
            raise RuntimeError(f"ACI export returned {a.shape}, expected {REGION.shape}")
        if abs(src.transform.a - C.CELL) > 1e-6 or abs(src.transform.c - REGION.x0) > 0.5:
            raise RuntimeError(f"ACI export grid mismatch: {src.transform}")
    write_tif(dest, a.astype(np.uint8), REGION, nodata=0, band_names=["aci_class"],
              tags={"source": "aci", "year": "2025"})
    record_provenance("aci", {"request": q})


# ── Microsoft building footprints → footprint fraction per cell ──────────────
def buildings(force: bool = False, sub: int = 5) -> None:
    dest = C.REGION_DIR / "buildings_frac.tif"
    if dest.exists() and not force:
        return
    from shapely.geometry import box, shape
    links = C.RAW / "msbuildings_dataset-links.csv"
    _download(C.SOURCES["ms_buildings"]["url"], links)
    reg = box(*region_lonlat_bounds(0.0))
    tiles = json.loads((C.RAW / "meta_chm_tiles.geojson").read_text())["features"] \
        if (C.RAW / "meta_chm_tiles.geojson").exists() else []
    qks = [f["properties"]["tile"] for f in tiles if shape(f["geometry"]).intersects(reg)] or ["021211312"]
    rows = [ln.split(",") for ln in links.read_text().splitlines()[1:]]
    urls = [r[2] for r in rows if r[0] == "Canada" and r[1] in qks]
    geoms = []
    for u in urls:
        path = C.RAW / ("msbuildings_" + u.split("quadkey=")[1].split("/")[0] + ".csv.gz")
        _download(u, path)
        with gzip.open(path, "rt") as f:
            for line in f:
                g = json.loads(line)["geometry"]
                s = shape(g)
                if s.intersects(reg):
                    geoms.append(transform_geom("EPSG:4326", C.CRS, g))
    fine = Grid(REGION.x0, REGION.y0, REGION.width * sub, REGION.height * sub, C.CELL / sub)
    r = rasterize(((g, 1) for g in geoms), out_shape=fine.shape, transform=fine.transform, fill=0, dtype="uint8")
    frac = r.reshape(REGION.height, sub, REGION.width, sub).mean((1, 3)).astype(np.float32)
    write_tif(dest, frac, REGION, band_names=["building_frac"], tags={"source": "ms_buildings", "n": len(geoms)})
    record_provenance("ms_buildings", {"quadkeys": qks, "footprints_in_region": len(geoms)})


# ── OpenStreetMap landuse / natural / leisure → category raster ──────────────
def osm(force: bool = False) -> None:
    dest = C.REGION_DIR / "osm_landuse.tif"
    if dest.exists() and not force:
        return
    import pyogrio
    pbf = C.RAW / "alberta-latest.osm.pbf"
    info = _download(C.SOURCES["osm_landuse"]["url"], pbf)
    cols = ["landuse", "natural", "leisure", "amenity"]
    df = pyogrio.read_dataframe(pbf, layer="multipolygons", bbox=region_lonlat_bounds(0.0), columns=cols)
    df = df[df.geometry.notna()].to_crs(C.CRS)
    out = np.zeros(REGION.shape, np.uint8)
    counts = {}
    for cat in sorted(C.OSM_RULES):  # ascending priority: later categories overwrite
        m = np.zeros(len(df), bool)
        for key, vals in C.OSM_RULES[cat].items():
            m |= df[key].isin(vals).to_numpy()
        sel = df.geometry[m]
        counts[cat] = int(m.sum())
        if len(sel):
            r = rasterize(((g, 1) for g in sel), out_shape=REGION.shape, transform=REGION.transform,
                          fill=0, dtype="uint8")
            out[r > 0] = cat
    write_tif(dest, out, REGION, band_names=["osm_category"],
              tags={"source": "osm_landuse", "categories": {str(k): v for k, v in counts.items()}})
    record_provenance("osm_landuse", {"pbf": info, "polygons_by_category": counts})


# ── CFS national FBP layer 2026 (comparison only) ────────────────────────────
def cfs_fbp(force: bool = False) -> None:
    dest = C.REGION_DIR / "cfs_fbp_2026.tif"
    if dest.exists() and not force:
        return
    path = C.RAW / "FBP_FuelTypes_Canada_2026_30m.tif"
    info = _download(C.SOURCES["cfs_fbp_2026"]["url"], path)
    out = _warp_remote(str(path), Resampling.nearest, dtype=np.int16)
    write_tif(dest, out, REGION, nodata=0, band_names=["cfs_fbp_code"], tags={"source": "cfs_fbp_2026"})
    record_provenance("cfs_fbp_2026", {"file": info})


# ── Sentinel-2 L2A seasonal composites (Earth Search STAC, anonymous) ────────
def _stac_items(start: str, end: str) -> list[dict]:
    lon0, lat0, lon1, lat1 = region_lonlat_bounds(0.0)
    body = {"collections": ["sentinel-2-l2a"], "bbox": [lon0, lat0, lon1, lat1],
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": C.S2_MAX_CLOUD}}, "limit": 200}
    d = json.loads(_get(C.SOURCES["sentinel2"]["url"] + "/search", json.dumps(body).encode(),
                        {"Content-Type": "application/json"}))
    return d["features"]


def _read_s2(href: str, resampling: Resampling, overview: int | None) -> np.ndarray:
    kw = {"overview_level": overview} if overview is not None else {}
    with rasterio.Env(**GDAL_ENV), rasterio.open("/vsicurl/" + href, **kw) as src, \
            WarpedVRT(src, crs=C.CRS, transform=REGION.transform, width=REGION.width,
                      height=REGION.height, resampling=resampling, nodata=0) as vrt:
        return vrt.read(1)


def _scale(item: dict, band: str) -> tuple[float, float]:
    """Reflectance = DN * scale + offset. Earth Search v1 items with
    ``earthsearch:boa_offset_applied`` already have the processing-baseline >= 04.00 offset
    (-1000 DN) removed from the pixels, although raster:bands still lists offset -0.1; applying it
    again makes most red reflectances negative, so it is skipped for those items."""
    rb = item["assets"][band].get("raster:bands", [{}])[0]
    offset = 0.0 if item["properties"].get("earthsearch:boa_offset_applied") else float(rb.get("offset", 0.0))
    return float(rb.get("scale", 1e-4)), offset


def sentinel2(force: bool = False, workers: int = 8) -> None:
    for season, windows in C.S2_SEASONS.items():
        dest = C.REGION_DIR / f"s2_{season}.tif"
        if dest.exists() and not force:
            continue
        items = []
        for a, b in windows:
            items += _stac_items(a, b)
        winter = season == "winter"
        # snow-free seasons: skip scenes with much snow (snow pixels are masked anyway via SCL)
        if not winter:
            items = [it for it in items if it["properties"].get("s2:snow_ice_percentage", 0) < 20]
        log.info("S2 %s: %d items", season, len(items))

        def scl_of(it):
            return _read_s2(it["assets"]["scl"]["href"], Resampling.nearest, None)

        with ThreadPoolExecutor(workers) as ex:
            scls = list(ex.map(scl_of, items))
        valid = [np.isin(s, C.S2_VALID_SCL_WINTER if winter else C.S2_VALID_SCL) for s in scls]
        by_date = defaultdict(list)
        for i, it in enumerate(items):
            by_date[it["properties"]["datetime"][:10]].append(i)
        comp = np.zeros((len(C.S2_BANDS),) + REGION.shape, np.float32)
        nobs = np.zeros(REGION.shape, np.int16)
        for bi, band in enumerate(C.S2_BANDS):
            ten_m = band in ("blue", "green", "red", "nir")

            def read_band(it, band=band, ten_m=ten_m):
                return _read_s2(it["assets"][band]["href"], Resampling.average, 0 if ten_m else None)

            with ThreadPoolExecutor(workers) as ex:
                raw = list(ex.map(read_band, items))
            per_date = []
            for d, idx in sorted(by_date.items()):
                acc = np.zeros(REGION.shape, np.float64)
                n = np.zeros(REGION.shape, np.int16)
                for i in idx:
                    s, o = _scale(items[i], band)
                    ok = valid[i] & (raw[i] > 0)
                    acc[ok] += raw[i][ok] * s + o
                    n[ok] += 1
                with np.errstate(invalid="ignore", divide="ignore"):
                    per_date.append(np.where(n > 0, acc / n, np.nan).astype(np.float32))
            stack = np.stack(per_date)
            comp[bi] = np.nanmedian(stack, axis=0)
            if bi == 0:
                nobs = np.isfinite(stack).sum(0).astype(np.int16)
            log.info("S2 %s band %s done (%d dates)", season, band, len(per_date))
        write_tif(dest, np.concatenate([comp, nobs[None].astype(np.float32)]), REGION, nodata=np.nan,
                  band_names=C.S2_BANDS + ["n_dates"],
                  tags={"source": "sentinel2", "season": season, "windows": windows,
                        "scenes": sorted(it["id"] for it in items)})
        record_provenance(f"sentinel2_{season}", {**C.SOURCES["sentinel2"], "windows": windows,
                                                  "scenes": sorted(it["id"] for it in items)})


ALL = [boundaries, worldcover, meta_chm, scanfi, aci, buildings, osm, cfs_fbp, sentinel2]


def fetch_all(force: bool = False) -> None:
    C.REGION_DIR.mkdir(parents=True, exist_ok=True)
    for fn in ALL:
        log.info("fetch %s", fn.__name__)
        fn(force=force)
