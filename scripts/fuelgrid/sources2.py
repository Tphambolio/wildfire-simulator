"""v2 inputs: region grids outside Edmonton and the time-dependent layers of the full FBP key.

A *region* is a 20 m grid that inputs are fetched onto once; AOIs are windows of it. The v1
region (Edmonton + St. Albert, EPSG:3776) is ``capital``; the northern Alberta test regions are
in EPSG:3400 (Alberta 10-TM Forest) around validation fires. Each fetcher is idempotent, writes
to ``region.dir`` (never to the repo) and records provenance in ``DATA/provenance.json``.
v1 files are only read, never rewritten.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject, transform_bounds, transform_geom

try:
    from . import config as C
    from .grid import REGION, Grid, write_tif
    from .sources import GDAL_ENV, _download, _get
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import REGION, Grid, write_tif  # type: ignore[no-redef]
    from sources import GDAL_ENV, _download, _get  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.sources2")
PROV_V2 = C.DATA / "provenance_v2.json"  # separate from v1's provenance.json (never rewritten)


def record_v2(key: str, info: dict) -> None:
    """Merge one retrieval record into DATA/provenance_v2.json."""
    from datetime import date
    prov = json.loads(PROV_V2.read_text()) if PROV_V2.exists() else {}
    prov[key] = {**info, "recorded": date.today().isoformat()}
    PROV_V2.parent.mkdir(parents=True, exist_ok=True)
    PROV_V2.write_text(json.dumps(prov, indent=2, sort_keys=True, default=str))
SCANFI = "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/"
SCANFI_V2_SPECIES = ["broadleaf", "blackSpruce", "otherConiferous", "balsamFir", "jackPine", "lodgepolePine",
                     "tamarack", "whiteRedPine", "douglasFir", "ponderosaPine"]
SCANFI_V3_FRAC = ["treed_coniferous", "treed_broadleaf", "nonTreed_lichen", "nonTreed_herbaceous",
                  "nonTreed_lowShrubs", "nonTreed_tallShrubs", "nonTreed_water", "nonTreed_burnScars"]


@dataclass(frozen=True)
class Region:
    key: str
    grid: Grid
    dir: Path

    @property
    def crs(self) -> str:
        return self.grid.crs


def _test_area_bounds(t: C.TestArea, buffer_m: float = C.TEST_BUFFER_M) -> tuple[float, float, float, float]:
    b = transform_bounds("EPSG:4326", C.TEST_CRS, *t.fire_lonlat, densify_pts=21)
    return (b[0] - buffer_m, b[1] - buffer_m, b[2] + buffer_m, b[3] + buffer_m)


def test_area_grid(key: str) -> Grid:
    return dataclasses.replace(Grid.from_bounds(_test_area_bounds(C.TEST_AREAS[key])), crs=C.TEST_CRS)


def regions() -> dict[str, Region]:
    out = {"capital": Region("capital", REGION, C.REGION_DIR)}
    keys = sorted({t.region for t in C.TEST_AREAS.values()})
    for k in keys:
        bs = [_test_area_bounds(t, C.TEST_BUFFER_M + 1000.0) for t in C.TEST_AREAS.values() if t.region == k]
        b = (min(x[0] for x in bs), min(x[1] for x in bs), max(x[2] for x in bs), max(x[3] for x in bs))
        g = dataclasses.replace(Grid.from_bounds(b), crs=C.TEST_CRS)
        out[k] = Region(k, g, C.DATA / "regions" / k)
    return out


def lonlat_bounds(r: Region, margin: float = 0.0) -> tuple[float, float, float, float]:
    b = transform_bounds(r.crs, "EPSG:4326", *r.grid.bounds, densify_pts=21)
    return (b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin)


def warp_to(r: Region, src_path: str, resampling: Resampling, dtype=np.float32, src_nodata=None,
            band: int = 1) -> np.ndarray:
    """Window-read a (possibly remote) raster and warp it onto the region grid."""
    fill = np.nan if np.dtype(dtype).kind == "f" else 0
    out = np.full(r.grid.shape, fill, dtype)
    with rasterio.Env(**GDAL_ENV), rasterio.open(src_path) as src:
        b = transform_bounds(r.crs, src.crs, *r.grid.bounds, densify_pts=21)
        pad = 4 * abs(src.res[0])
        win = rasterio.windows.from_bounds(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad, src.transform)
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        win = win.round_offsets().round_lengths()
        a = src.read(band, window=win)
        nd = src.nodata if src_nodata is None else src_nodata
        reproject(a, out, src_transform=src.window_transform(win), src_crs=src.crs,
                  dst_transform=r.grid.transform, dst_crs=r.crs, resampling=resampling,
                  src_nodata=nd, dst_nodata=fill)
    return out


# ── SCANFI v2 (5-year epochs) and v3 (annual) ────────────────────────────────
def scanfi_v2_epoch(r: Region, epoch: int, force: bool = False) -> Path:
    dest = r.dir / f"scanfi_v2_{epoch}.tif"
    if dest.exists() and not force:
        return dest
    names = ["att_closure", "att_height"] + [f"spsCC_{s}" for s in SCANFI_V2_SPECIES]
    urls = [f"/vsicurl/{SCANFI}v2/SCANFI_{n}_{epoch}_v2_20260119.tif" for n in names]
    with ThreadPoolExecutor(6) as ex:
        bands = list(ex.map(lambda u: warp_to(r, u, Resampling.bilinear), urls))
    write_tif(dest, np.stack(bands).astype(np.float32), r.grid, nodata=np.nan, band_names=names,
              tags={"source": "scanfi_v2_epoch", "epoch": str(epoch), "units": "percent / m"})
    record_v2(f"scanfi_v2_{epoch}_{r.key}", {**C.SOURCES_V2["scanfi_v2_epoch"], "epoch": epoch,
                                                    "layers": names, "region": r.key})
    return dest


def scanfi_v3_year(r: Region, year: int, force: bool = False) -> Path:
    dest = r.dir / f"scanfi_v3_{year}.tif"
    if dest.exists() and not force:
        return dest
    urls = [f"/vsicurl/{SCANFI}v3/cog_SCANFI_{n}_{year}_v3_20260528.tif" for n in SCANFI_V3_FRAC]
    with ThreadPoolExecutor(6) as ex:
        bands = list(ex.map(lambda u: warp_to(r, u, Resampling.bilinear), urls))
    write_tif(dest, np.stack(bands).astype(np.float32), r.grid, nodata=np.nan, band_names=SCANFI_V3_FRAC,
              tags={"source": "scanfi_v3_annual", "year": str(year), "units": "percent of cell"})
    record_v2(f"scanfi_v3_{year}_{r.key}", {**C.SOURCES_V2["scanfi_v3_annual"], "year": year,
                                                   "layers": SCANFI_V3_FRAC, "region": r.key})
    return dest


# ── Meta 1 m canopy height: cover / height on the region grid + image year ──
def _meta_tiles(r: Region) -> list[str]:
    from shapely.geometry import box, shape
    idx = C.RAW / "meta_chm_tiles.geojson"
    if not idx.exists():
        _download("https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/tiles.geojson", idx)
    reg = box(*lonlat_bounds(r))
    return [f["properties"]["tile"] for f in json.loads(idx.read_text())["features"]
            if shape(f["geometry"]).intersects(reg)]


def meta_chm(r: Region, force: bool = False, chunk_rows: int = 1000) -> Path:
    """Same aggregation as v1 ``sources.meta_chm`` (cover >= 5 m, >= 2 m, mean heights) on any region."""
    dest = r.dir / "meta_chm.tif"
    if dest.exists() and not force:
        return dest
    tiles = _meta_tiles(r)
    base = C.SOURCES["meta_chm"]["url"]
    sub = 20
    g = r.grid
    fine = Grid(g.x0, g.y0, g.width * sub, g.height * sub, 1.0, g.crs)
    acc = {k: np.zeros(g.shape, np.float64) for k in ("n", "n5", "n2", "h", "h5")}
    for t in tiles:
        path = C.RAW / f"meta_chm_{t}.tif"
        _download(base + f"{t}.tif", path)
        with rasterio.open(path) as src, WarpedVRT(src, crs=g.crs, transform=fine.transform, width=fine.width,
                                                    height=fine.height, resampling=Resampling.nearest,
                                                    nodata=255) as vrt:
            # only the region cells this tile covers
            tb = transform_bounds(src.crs, g.crs, *src.bounds, densify_pts=21)
            c0 = max(0, int((tb[0] - g.x0) // g.cell))
            c1 = min(g.width, int(math.ceil((tb[2] - g.x0) / g.cell)))
            q0 = max(0, int((g.y0 - tb[3]) // g.cell))
            q1 = min(g.height, int(math.ceil((g.y0 - tb[1]) / g.cell)))
            if c1 <= c0 or q1 <= q0:
                continue
            ncol = c1 - c0
            step = max(1, chunk_rows // sub)
            for q in range(q0, q1, step):
                hq = min(step, q1 - q)
                a = vrt.read(1, window=rasterio.windows.Window(c0 * sub, q * sub, ncol * sub, hq * sub))
                if not (a != 255).any():
                    continue
                rr = (slice(q, q + hq), slice(c0, c1))
                v = a != 255

                def blk(m, hq=hq):
                    return m.reshape(hq, sub, ncol, sub).sum((1, 3))

                af = np.where(v, a, 0).astype(np.float32)
                acc["n"][rr] += blk(v)
                acc["n5"][rr] += blk(v & (a >= C.PARAMS["chm_tree_height_m"]))
                acc["n2"][rr] += blk(v & (a >= 2))
                acc["h"][rr] += blk(af)
                acc["h5"][rr] += blk(np.where(v & (a >= C.PARAMS["chm_tree_height_m"]), af, 0))
        log.info("CHM tile %s done", t)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = acc["n"]
        stack = np.stack([acc["n5"] / n, acc["n2"] / n, acc["h"] / n,
                          np.where(acc["n5"] > 0, acc["h5"] / np.maximum(acc["n5"], 1), 0), n / (sub * sub)])
    write_tif(dest, np.nan_to_num(stack.astype(np.float32), nan=-1), g, nodata=-1,
              band_names=["cover_ge5m", "cover_ge2m", "height_mean_m", "height_mean_ge5m_m", "valid_frac"],
              tags={"source": "meta_chm", "tiles": tiles})
    record_v2(f"meta_chm_{r.key}", {**C.SOURCES["meta_chm"], "tiles": tiles, "region": r.key})
    return dest


def meta_chm_year(r: Region, force: bool = False) -> Path:
    """Acquisition year of the canopy-model imagery per cell (from the per-tile metadata polygons)."""
    dest = r.dir / "meta_chm_year.tif"
    if dest.exists() and not force:
        return dest
    from shapely.geometry import box, shape
    reg = box(*lonlat_bounds(r, 0.01))
    shapes = []
    base = C.SOURCES["meta_chm"]["url"].replace("/chm/", "/metadata/")
    for t in _meta_tiles(r):
        mpath = C.RAW / f"meta_chm_meta_{t}.geojson"
        _download(base + f"{t}.geojson", mpath)
        for f in json.loads(mpath.read_text())["features"]:
            d = f["properties"].get("acq_date")
            if not d or not shape(f["geometry"]).intersects(reg):
                continue
            shapes.append((transform_geom("EPSG:4326", r.crs, f["geometry"]), int(str(d)[:4])))
    shapes.sort(key=lambda s: s[1])  # later imagery drawn last
    yr = rasterize(shapes, out_shape=r.grid.shape, transform=r.grid.transform, fill=0, dtype="int16") \
        if shapes else np.zeros(r.grid.shape, np.int16)
    write_tif(dest, yr, r.grid, nodata=0, band_names=["acq_year"], tags={"source": "meta_chm metadata"})
    return dest


# ── ESA WorldCover 2021 (3 x 3 degree tiles) ─────────────────────────────────
def worldcover(r: Region, force: bool = False) -> Path:
    dest = r.dir / "worldcover_frac.tif"
    if dest.exists() and not force:
        return dest
    lon0, lat0, lon1, lat1 = lonlat_bounds(r, 0.01)
    out = np.zeros((len(C.WC_BANDS),) + r.grid.shape, np.float32)
    tiles = []
    for la in range(int(math.floor(lat0 / 3) * 3), int(math.floor(lat1 / 3) * 3) + 1, 3):
        for lo in range(int(math.floor(lon0 / 3) * 3), int(math.floor(lon1 / 3) * 3) + 1, 3):
            name = f"N{la:02d}W{-lo:03d}"
            tiles.append(name)
            url = (f"/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                   f"ESA_WorldCover_10m_2021_v200_{name}_Map.tif")
            with rasterio.Env(**GDAL_ENV), rasterio.open(url) as src:
                win = rasterio.windows.from_bounds(lon0, lat0, lon1, lat1, transform=src.transform)
                win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
                win = win.round_offsets().round_lengths()
                a = src.read(1, window=win)
                st, scrs = src.window_transform(win), src.crs
            for i, k in enumerate(C.WC_BANDS):
                tmp = np.full(r.grid.shape, np.nan, np.float32)
                reproject((a == k).astype(np.float32), tmp, src_transform=st, src_crs=scrs, src_nodata=None,
                          dst_transform=r.grid.transform, dst_crs=r.crs, resampling=Resampling.average,
                          dst_nodata=np.nan)
                ok = np.isfinite(tmp)
                out[i][ok] = tmp[ok]
    write_tif(dest, np.round(out * 100).astype(np.uint8), r.grid,
              band_names=[f"wc{k}_{C.WC_CLASSES[k]}_pct" for k in C.WC_BANDS],
              tags={"source": "worldcover", "units": "percent of cell", "tiles": tiles})
    record_v2(f"worldcover_{r.key}", {**C.SOURCES["worldcover"], "tiles": tiles, "region": r.key})
    return dest


# ── AAFC ACI (agricultural extent only; zero where not mapped) ───────────────
def aci(r: Region, year: int, force: bool = False) -> Path:
    import urllib.parse
    dest = r.dir / f"aci{year}.tif"
    if dest.exists() and not force:
        return dest
    b = r.grid.bounds
    epsg = int(r.crs.split(":")[1])
    q = urllib.parse.urlencode({
        "bbox": f"{b[0]},{b[1]},{b[2]},{b[3]}", "bboxSR": epsg, "imageSR": epsg,
        "size": f"{r.grid.width},{r.grid.height}", "format": "tiff", "pixelType": "U8",
        "interpolation": "RSP_NearestNeighbor", "f": "image"})
    url = f"https://agriculture.canada.ca/imagery-images/rest/services/annual_crop_inventory/{year}/ImageServer"
    raw = r.dir / f"aci{year}_raw.tif"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(_get(url + "/exportImage?" + q))
    with rasterio.open(raw) as src:
        a = src.read(1)
    if a.shape != r.grid.shape:
        raise RuntimeError(f"ACI export returned {a.shape}, expected {r.grid.shape}")
    write_tif(dest, a.astype(np.uint8), r.grid, nodata=0, band_names=["aci_class"],
              tags={"source": "aci", "year": str(year)})
    raw.unlink()
    record_v2(f"aci{year}_{r.key}", {**C.SOURCES["aci"], "url": url, "year": year, "region": r.key,
                                            "mapped_cells": int((a > 0).sum())})
    return dest


# ── Microsoft building footprints and OSM land use on any region ─────────────
def buildings(r: Region, force: bool = False, sub: int = 5) -> Path:
    import gzip

    from shapely.geometry import box, shape
    dest = r.dir / "buildings_frac.tif"
    if dest.exists() and not force:
        return dest
    links = C.RAW / "msbuildings_dataset-links.csv"
    _download(C.SOURCES["ms_buildings"]["url"], links)
    reg = box(*lonlat_bounds(r))
    qks = _meta_tiles(r)  # same zoom-9 quadkeys as the footprint partitions
    rows = [ln.split(",") for ln in links.read_text().splitlines()[1:]]
    urls = [x[2] for x in rows if x[0] == "Canada" and x[1] in qks]
    geoms = []
    for u in urls:
        path = C.RAW / ("msbuildings_" + u.split("quadkey=")[1].split("/")[0] + ".csv.gz")
        _download(u, path)
        with gzip.open(path, "rt") as f:
            for line in f:
                gj = json.loads(line)["geometry"]
                if shape(gj).intersects(reg):
                    geoms.append(transform_geom("EPSG:4326", r.crs, gj))
    g = r.grid
    fine = Grid(g.x0, g.y0, g.width * sub, g.height * sub, g.cell / sub, g.crs)
    a = rasterize(((x, 1) for x in geoms), out_shape=fine.shape, transform=fine.transform, fill=0,
                  dtype="uint8") if geoms else np.zeros(fine.shape, np.uint8)
    frac = a.reshape(g.height, sub, g.width, sub).mean((1, 3)).astype(np.float32)
    write_tif(dest, frac, g, band_names=["building_frac"], tags={"source": "ms_buildings", "n": len(geoms)})
    record_v2(f"ms_buildings_{r.key}", {**C.SOURCES["ms_buildings"], "quadkeys": qks,
                                               "footprints_in_region": len(geoms), "region": r.key})
    return dest


def osm(r: Region, force: bool = False) -> Path:
    import pyogrio
    dest = r.dir / "osm_landuse.tif"
    if dest.exists() and not force:
        return dest
    pbf = C.RAW / "alberta-latest.osm.pbf"
    info = _download(C.SOURCES["osm_landuse"]["url"], pbf)
    df = pyogrio.read_dataframe(pbf, layer="multipolygons", bbox=lonlat_bounds(r),
                                columns=["landuse", "natural", "leisure", "amenity"])
    df = df[df.geometry.notna()].to_crs(r.crs)
    out = np.zeros(r.grid.shape, np.uint8)
    counts = {}
    for cat in sorted(C.OSM_RULES):
        m = np.zeros(len(df), bool)
        for key, vals in C.OSM_RULES[cat].items():
            m |= df[key].isin(vals).to_numpy()
        counts[cat] = int(m.sum())
        if m.any():
            a = rasterize(((x, 1) for x in df.geometry[m]), out_shape=r.grid.shape, transform=r.grid.transform,
                          fill=0, dtype="uint8")
            out[a > 0] = cat
    write_tif(dest, out, r.grid, band_names=["osm_category"],
              tags={"source": "osm_landuse", "categories": {str(k): v for k, v in counts.items()}})
    record_v2(f"osm_landuse_{r.key}", {**C.SOURCES["osm_landuse"], "pbf": info, "region": r.key,
                                              "polygons_by_category": counts})
    return dest


# ── Disturbance: Hansen forest loss year, Alberta wildfire perimeters ────────
def hansen_lossyear(r: Region, force: bool = False) -> Path:
    """Forest-loss calendar year (2001-2024; 0 = none) on the region grid (nearest)."""
    dest = r.dir / "hansen_lossyear.tif"
    if dest.exists() and not force:
        return dest
    lon0, lat0, lon1, lat1 = lonlat_bounds(r, 0.01)
    out = np.zeros(r.grid.shape, np.int16)
    tiles = []
    base = C.SOURCES_V2["hansen_gfc"]["url"].rsplit("/", 1)[0] + "/"
    for la in range(int(math.ceil(lat0 / 10) * 10), int(math.ceil(lat1 / 10) * 10) + 1, 10):
        for lo in range(int(math.floor(lon0 / 10) * 10), int(math.floor(lon1 / 10) * 10) + 1, 10):
            name = f"{la:02d}N_{-lo:03d}W"
            tiles.append(name)
            path = C.RAW / f"Hansen_GFC-2024-v1.12_lossyear_{name}.tif"
            _download(base + f"Hansen_GFC-2024-v1.12_lossyear_{name}.tif", path)
            a = warp_to(r, str(path), Resampling.nearest, np.int16, src_nodata=0)
            out = np.where(a > 0, a, out)
    yr = np.where(out > 0, 2000 + out, 0).astype(np.int16)
    write_tif(dest, yr, r.grid, nodata=0, band_names=["loss_year"], tags={"source": "hansen_gfc", "tiles": tiles})
    record_v2(f"hansen_gfc_{r.key}", {**C.SOURCES_V2["hansen_gfc"], "tiles": tiles, "region": r.key})
    return dest


def fire_perimeters(r: Region, force: bool = False) -> Path:
    """Alberta wildfire perimeters (BURNCODE B*) intersecting the region, as a GeoPackage subset."""
    import pyogrio
    dest = r.dir / "ab_fire_perimeters.gpkg"
    if dest.exists() and not force:
        return dest
    src = f"/vsizip/{C.AB_PERIMETERS}/HistoricalWildfirePerimeters.shp"
    b = transform_bounds(r.crs, "EPSG:3400", *r.grid.bounds, densify_pts=21)
    df = pyogrio.read_dataframe(src, bbox=b, columns=["FIRENUMBER", "YEAR", "BURNCODE", "HECTARES_U"])
    df = df[df.BURNCODE.astype(str).str.startswith("B")].to_crs(r.crs)
    r.dir.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(df, dest, driver="GPKG")
    record_v2(f"ab_fire_perimeters_{r.key}", {**C.SOURCES_V2["ab_fire_perimeters"],
                                                     "file": str(C.AB_PERIMETERS), "n": len(df), "region": r.key})
    return dest


def burn_years(r: Region, grid: Grid) -> list[tuple[int, np.ndarray]]:
    """[(year, mask)] of every burn perimeter year on an AOI grid (ascending)."""
    import pyogrio
    df = pyogrio.read_dataframe(r.dir / "ab_fire_perimeters.gpkg")
    out = []
    for y, sub in sorted(df.groupby("YEAR"), key=lambda t: t[0]):
        m = rasterize(((geom, 1) for geom in sub.geometry if geom is not None), out_shape=grid.shape,
                      transform=grid.transform, fill=0, dtype="uint8").astype(bool)
        if m.any():
            out.append((int(y), m))
    return out


# ── Comparison layers (CFS 2026 30 m, CFS 2024 100 m) ────────────────────────
def cfs_layers(r: Region, force: bool = False) -> dict[str, Path]:
    out = {}
    for name, src in (("cfs_fbp_2026", C.RAW / "FBP_FuelTypes_Canada_2026_30m.tif"),
                      ("cfs_fbp_2024", C.CFS_FBP_2024)):
        dest = r.dir / f"{name}.tif"
        if (not dest.exists() or force) and src.exists():
            a = warp_to(r, str(src), Resampling.nearest, np.int16)
            write_tif(dest, a, r.grid, nodata=0, band_names=["cfs_fbp_code"], tags={"source": name})
        out[name] = dest
    return out


def fetch_region(r: Region, years: list[int], epochs: list[int], force: bool = False) -> None:
    r.dir.mkdir(parents=True, exist_ok=True)
    capital = r.key == "capital"
    if not capital:  # the capital region already has these from v1 (read, never rewritten)
        for fn in (meta_chm, worldcover, buildings, osm):
            log.info("fetch %s %s", r.key, fn.__name__)
            fn(r, force=force)
        for y in years:
            aci(r, y - 1, force=force)
    meta_chm_year(r, force=force)
    for e in epochs:
        scanfi_v2_epoch(r, e, force=force)
    for y in years:
        scanfi_v3_year(r, min(y - 1, 2025), force=force)
    hansen_lossyear(r, force=force)
    fire_perimeters(r, force=force)
    cfs_layers(r, force=force)


def epochs_for(year: int) -> list[int]:
    """SCANFI v2 epochs a map of `year` needs: the latest one before it, and the one before that
    (the pre-harvest composition of blocks cut 1-5 years earlier)."""
    e = max(x for x in C.SCANFI_V2_EPOCHS if x < year)
    return sorted({e, e - 5})


def fetch_all_v2(force: bool = False) -> None:
    regs = regions()
    plan: dict[str, tuple[set, set]] = {"capital": ({C.MAP_YEAR_CAPITAL}, set(epochs_for(C.MAP_YEAR_CAPITAL)))}
    for t in C.TEST_AREAS.values():
        ys, es = plan.setdefault(t.region, (set(), set()))
        ys.add(t.year)
        es.update(epochs_for(t.year))
    for k, (ys, es) in plan.items():
        log.info("region %s: years %s, SCANFI epochs %s", k, sorted(ys), sorted(es))
        fetch_region(regs[k], sorted(ys), sorted(es), force=force)
