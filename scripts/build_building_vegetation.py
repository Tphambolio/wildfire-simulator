#!/usr/bin/env python3
"""Per-building vegetation attributes and a gap-cover canopy raster from open data.

Structure step 3 (``docs/structure-spread-spec.md`` §2.1, §4.6). **Open data only**: the Meta/WRI
1 m global canopy height map (Tolan et al. 2024, *Remote Sens. Environ.* 300: 113888; CC BY 4.0)
and, optionally, ESA WorldCover 2021 v200 (Zanaga et al. 2022; CC BY 4.0). **Never run this on
City of Edmonton LiDAR products**: they are City-owned, not open data (contract 934295 §13.1),
and no per-building value derived from them may be shipped.

Three subcommands, all offline (the live API only reads their outputs):

``canopy-raster``
    Gap-cover raster for the vegetation-bridged cutoff (spec §4.6). On the CHM's own EPSG:3857
    grid, blocks of ``--block`` × ``--block`` pixels (7 × 0.71 m ≈ 5 m on the ground at 53.5° N)
    hold the share of their **non-building** pixels with CHM ≥ 2 m (woody canopy [H], the
    lowest class of the City canopy analysis, structure step 3 report §2.1), as a percentage
    0-100; 255 = no non-building pixel or no CHM. Building pixels are those inside a footprint
    (rasterised from ``--footprints``), so roofs are never canopy and corridor cover is the
    cover of the open ground between buildings.

``ring-metrics``
    For any area: canopy cover in rings 0-5, 5-10, 10-30 and 0-10 m around each footprint
    (canopy pixels / all ring pixels, roads, lawns and other buildings in the denominator as in
    FPInnovations WF TR 2025 n.04 p.9), the share of the footprint under canopy
    (``overhang_frac``; Syphard et al. 2014 pp.A, I) and the distance to a ≥ 1 ha stand
    (blocks of ~10 m with ≥ 50 % canopy, 8-connected, ≥ 1 ha: FPI p.9 patch size) [H]. This is
    the method of ``~/dev/wildfire/structure-veg-data/scripts/ring_metrics.py`` that produced
    the Edmonton table, without the auxiliary layers. Memory: one CHM tile window at a time.

``edmonton-table``
    Packs the Edmonton per-footprint metrics (keyed by the FireSim footprint ``id`` of
    ``data/edmonton_buildings.geojson.gz``) into the compact table the API loads,
    ``data/edmonton_building_vegetation.csv.gz``.

Usage (from the repo root):

    python3 scripts/build_building_vegetation.py canopy-raster \\
        --chm ~/dev/wildfire/fuelgrid-data/raw/meta_chm_021211312.tif \\
        --footprints data/edmonton_buildings.geojson.gz --out data/edmonton_canopy_5m.tif
    python3 scripts/build_building_vegetation.py edmonton-table \\
        --metrics ~/dev/wildfire/structure-veg-data/edmonton/edmonton_footprint_veg_metrics.csv.gz \\
        --out data/edmonton_building_vegetation.csv.gz
    python3 scripts/build_building_vegetation.py ring-metrics --chm <tile.tif> \\
        --footprints <footprints.geojson> --id-field id --out <metrics.csv>

Computing either product on the live server for an arbitrary run area is a follow-up: a Meta
CHM tile is ~0.5 GB compressed (65,536² px) and the ring metrics take minutes per city.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np

R_MERC = 6378137.0
CANOPY_M = 2.0  # [H] woody canopy: CHM >= 2 m (structure step 3 report §2.1)
NODATA = 255
RINGS = {"0_5": (0.0, 5.0), "5_10": (5.0, 10.0), "10_30": (10.0, 30.0), "0_10": (0.0, 10.0)}
TABLE_FIELDS = ("cc_0_5", "cc_5_10", "cc_10_30", "cc_0_10", "overhang_frac", "dist_stand_1ha_m")


def lnglat_to_3857(lng, lat):
    lng = np.asarray(lng, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -85.0, 85.0)
    return (R_MERC * np.radians(lng),
            R_MERC * np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0)))


def load_footprints(path: Path, id_field: str | None = None):
    """Shapely footprints (lng, lat) and ids from a GeoJSON (.gz) or GeoJSON-lines file."""
    import shapely
    from shapely.geometry import shape

    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        if str(path).endswith((".geojsonl", ".jsonl")):
            feats = [json.loads(line) for line in f if line.strip()]
        else:
            feats = json.load(f)["features"]
    geoms, ids = [], []
    for k, ft in enumerate(feats):
        g = shape(ft["geometry"])
        if not g.is_valid:
            g = shapely.make_valid(g)
        if g.is_empty or g.area <= 0:
            continue
        geoms.append(g)
        ids.append((ft.get("properties") or {}).get(id_field, k) if id_field else k)
    return np.asarray(geoms, dtype=object), np.asarray(ids)


def to_3857(geoms):
    import shapely

    def tf(c):
        x, y = lnglat_to_3857(c[:, 0], c[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geoms, tf)


# ------------------------------------------------------------------------------ canopy raster


def canopy_raster(chm_path: Path, footprints_path: Path, out: Path, block: int = 7,
                  bbox: tuple[float, float, float, float] | None = None,
                  strip_blocks: int = 256, pad_m: float = 500.0) -> dict:
    """Block canopy share of non-building pixels (percent, uint8; 255 = none). See module doc."""
    import rasterio
    import shapely
    from rasterio.features import rasterize
    from rasterio.windows import Window

    geoms, _ = load_footprints(footprints_path)
    g3857 = to_3857(geoms)
    if bbox is None:
        b = shapely.total_bounds(g3857)
        bbox = (b[0] - pad_m, b[1] - pad_m, b[2] + pad_m, b[3] + pad_m)
    tree = shapely.STRtree(g3857)
    with rasterio.open(chm_path) as s:
        if s.crs.to_epsg() != 3857:
            raise SystemExit("expects the Meta CHM on its native EPSG:3857 grid")
        inv = ~s.transform
        c0, r0 = inv * (bbox[0], bbox[3])
        c1, r1 = inv * (bbox[2], bbox[1])
        c0 = max(int(math.floor(c0)) // block * block, 0)
        r0 = max(int(math.floor(r0)) // block * block, 0)
        c1 = min(int(math.ceil(c1)), s.width)
        r1 = min(int(math.ceil(r1)), s.height)
        nbc, nbr = (c1 - c0) // block, (r1 - r0) // block
        out_tr = s.transform * rasterio.Affine.translation(c0, r0) * rasterio.Affine.scale(block)
        prof = {"driver": "GTiff", "dtype": "uint8", "count": 1, "width": nbc, "height": nbr,
                "crs": s.crs, "transform": out_tr, "nodata": NODATA, "compress": "deflate",
                "predictor": 2, "tiled": True, "blockxsize": 512, "blockysize": 512}
        out.parent.mkdir(parents=True, exist_ok=True)
        n_valid, sum_pct = 0, 0.0
        with rasterio.open(out, "w", **prof) as d:
            for br in range(0, nbr, strip_blocks):
                h = min(strip_blocks, nbr - br)
                win = Window(c0, r0 + br * block, nbc * block, h * block)
                chm = s.read(1, window=win)
                wtr = s.window_transform(win)
                x0, y1 = wtr * (0, 0)
                x1, y0 = wtr * (chm.shape[1], chm.shape[0])
                idx = tree.query(shapely.box(x0, y0, x1, y1))
                if len(idx):
                    bmask = rasterize(((g, 1) for g in g3857[idx]), out_shape=chm.shape,
                                      transform=wtr, fill=0, dtype="uint8").astype(bool)
                else:
                    bmask = np.zeros(chm.shape, dtype=bool)
                open_ = ~bmask
                can = (chm >= CANOPY_M) & open_
                shp = (h, block, nbc, block)
                n_open = open_.reshape(shp).sum(axis=(1, 3))
                n_can = can.reshape(shp).sum(axis=(1, 3))
                with np.errstate(invalid="ignore", divide="ignore"):
                    pct = np.where(n_open > 0, np.rint(100.0 * n_can / np.maximum(n_open, 1)),
                                   NODATA).astype(np.uint8)
                d.write(pct, 1, window=Window(0, br, nbc, h))
                ok = pct != NODATA
                n_valid += int(ok.sum())
                sum_pct += float(pct[ok].sum())
                print(f"  strip {br // strip_blocks + 1}/{math.ceil(nbr / strip_blocks)}",
                      file=sys.stderr, flush=True)
    info = {"out": str(out), "block_px": block, "shape": [nbr, nbc],
            "cell_m_native": abs(out_tr.a), "mean_cover_pct": sum_pct / max(n_valid, 1),
            "canopy_threshold_m": CANOPY_M,
            "source": "Meta/WRI 1 m global canopy height map (Tolan et al. 2024), CC BY 4.0",
            "footprints": str(footprints_path)}
    out.with_suffix(".json").write_text(json.dumps(info, indent=1))
    return info


# ------------------------------------------------------------------------------ ring metrics


def ring_metrics(chm_path: Path, footprints_path: Path, out: Path, id_field: str | None,
                 tile: int = 2048, halo: int = 60) -> None:
    """Ring cover, overhang and stand distance per footprint (see module doc)."""
    import rasterio
    import shapely
    from rasterio.features import rasterize
    from rasterio.windows import Window
    from scipy import ndimage

    geoms, ids = load_footprints(footprints_path, id_field)
    g3857 = to_3857(geoms)
    lat = shapely.get_y(shapely.centroid(geoms))
    rows = []
    with rasterio.open(chm_path) as s:
        inv = ~s.transform
        px = abs(s.transform.a)
        rep = shapely.point_on_surface(g3857)
        cc = np.array([inv * (p.x, p.y) for p in rep])
        tr_, tc_ = np.floor(cc[:, 1] / tile).astype(int), np.floor(cc[:, 0] / tile).astype(int)
        tree = shapely.STRtree(g3857)
        for (ti, tj) in sorted(set(zip(tr_.tolist(), tc_.tolist()))):
            sel = np.nonzero((tr_ == ti) & (tc_ == tj))[0]
            rr0, cc0 = max(ti * tile - halo, 0), max(tj * tile - halo, 0)
            rr1, cc1 = min((ti + 1) * tile + halo, s.height), min((tj + 1) * tile + halo, s.width)
            if rr1 <= rr0 or cc1 <= cc0:
                continue
            win = Window(cc0, rr0, cc1 - cc0, rr1 - rr0)
            chm = s.read(1, window=win).astype(np.float32)
            wtr = s.window_transform(win)
            x0, y1 = wtr * (0, 0)
            x1, y0 = wtr * (chm.shape[1], chm.shape[0])
            near = tree.query(shapely.box(x0, y0, x1, y1))
            label = rasterize(((g3857[k], int(k) + 1) for k in near), out_shape=chm.shape,
                              transform=wtr, fill=0, dtype="int32")
            canopy = (chm >= CANOPY_M) & (label == 0)
            winv = ~wtr
            for k in sel:
                ps = px * math.cos(math.radians(lat[k]))
                pad = int(math.ceil(30.5 / ps)) + 2
                bx0, by0, bx1, by1 = g3857[k].bounds
                ca, ra = winv * (bx0, by1)
                cb, rb = winv * (bx1, by0)
                cl, rl = max(int(math.floor(ca)) - pad, 0), max(int(math.floor(ra)) - pad, 0)
                ch, rh = min(int(math.ceil(cb)) + pad, chm.shape[1]), min(int(math.ceil(rb)) + pad, chm.shape[0])
                own = label[rl:rh, cl:ch] == int(k) + 1
                if not own.any():
                    st = wtr * rasterio.Affine.translation(cl, rl)
                    own = rasterize([(g3857[k], 1)], out_shape=own.shape, transform=st, fill=0,
                                    dtype="uint8", all_touched=True).astype(bool)
                rec = {"id": ids[k]}
                if own.any():
                    dist = ndimage.distance_transform_edt(~own) * ps
                    sub = chm[rl:rh, cl:ch]
                    rec["overhang_frac"] = float(np.mean(sub[own] >= CANOPY_M))
                    can = canopy[rl:rh, cl:ch]
                    for name, (a, b) in RINGS.items():
                        m = (dist > a) & (dist <= b)
                        if m.any():
                            rec[f"cc_{name}"] = float(can[m].mean())
                rows.append(rec)
            print(f"  tile ({ti},{tj}): {len(sel)} footprints", file=sys.stderr, flush=True)
        sd = stand_distance(s, g3857, float(np.median(lat)))
    by_id = {r["id"]: r for r in rows}
    for i, d in zip(ids, sd):
        if i in by_id:
            by_id[i]["dist_stand_1ha_m"] = float(d)
    write_table(out, list(by_id.values()))


def stand_distance(src, g3857, lat0: float, block: int = 14, pad_m: float = 2000.0) -> np.ndarray:
    """Distance (m, ~10 m resolution) from each footprint's bounding box to a >= 1 ha stand."""
    import rasterio
    import shapely
    from rasterio.windows import Window
    from scipy import ndimage

    b = shapely.total_bounds(g3857)
    inv = ~src.transform
    c0, r0 = inv * (b[0] - pad_m, b[3] + pad_m)
    c1, r1 = inv * (b[2] + pad_m, b[1] - pad_m)
    c0, r0 = max(int(c0) // block * block, 0), max(int(r0) // block * block, 0)
    c1, r1 = min(int(c1), src.width), min(int(r1), src.height)
    nbr, nbc = (r1 - r0) // block, (c1 - c0) // block
    frac = np.zeros((nbr, nbc), dtype=np.float32)
    step = block * 64
    for rs in range(0, nbr * block, step):
        h = min(step, nbr * block - rs)
        a = src.read(1, window=Window(c0, r0 + rs, nbc * block, h)) >= CANOPY_M
        frac[rs // block:(rs + h) // block] = a.reshape(h // block, block, nbc, block).mean(axis=(1, 3))
    cell = block * abs(src.transform.a) * math.cos(math.radians(lat0))
    lab, _ = ndimage.label(frac >= 0.5, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    keep = sizes * cell * cell >= 10000.0
    keep[0] = False
    dist = ndimage.distance_transform_edt(~keep[lab]) * cell
    binv = ~(src.transform * rasterio.Affine.translation(c0, r0) * rasterio.Affine.scale(block))
    out = np.full(len(g3857), np.nan)
    for k, g in enumerate(g3857):
        x0, y0, x1, y1 = g.bounds
        ca, ra = binv * (x0, y1)
        cb, rb = binv * (x1, y0)
        sub = dist[max(int(ra), 0):int(rb) + 1, max(int(ca), 0):int(cb) + 1]
        out[k] = float(sub.min()) if sub.size else np.nan
    return out


# ------------------------------------------------------------------------------ table


def write_table(out: Path, rows: list[dict]) -> None:
    """id + TABLE_FIELDS; covers to 0.01, distance to 1 m; blank = not available."""
    out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(out).endswith(".gz") else open
    with opener(out, "wt", encoding="utf-8", newline="") as f:
        f.write("id," + ",".join(TABLE_FIELDS) + "\n")
        for r in rows:
            vals = []
            for k in TABLE_FIELDS:
                v = r.get(k)
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    vals.append("")
                elif k == "dist_stand_1ha_m":
                    vals.append(str(int(round(v))))
                else:
                    vals.append(f"{v:.2f}".rstrip("0").rstrip(".") if v else "0")
            f.write(f"{r['id']}," + ",".join(vals) + "\n")


def edmonton_table(metrics: Path, out: Path) -> None:
    import csv

    with gzip.open(metrics, "rt", encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            rec = {"id": int(r["fid"])}
            for k in TABLE_FIELDS:
                v = r.get(k, "")
                rec[k] = float(v) if v not in ("", "nan") else None
            rows.append(rec)
    write_table(out, rows)
    print(f"wrote {out}: {len(rows)} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("canopy-raster")
    a.add_argument("--chm", type=Path, required=True)
    a.add_argument("--footprints", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--block", type=int, default=7)
    b = sub.add_parser("ring-metrics")
    b.add_argument("--chm", type=Path, required=True)
    b.add_argument("--footprints", type=Path, required=True)
    b.add_argument("--id-field", default=None)
    b.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("edmonton-table")
    c.add_argument("--metrics", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == "canopy-raster":
        print(json.dumps(canopy_raster(args.chm, args.footprints, args.out, args.block), indent=1))
    elif args.cmd == "ring-metrics":
        ring_metrics(args.chm, args.footprints, args.out, args.id_field)
    else:
        edmonton_table(args.metrics, args.out)


if __name__ == "__main__":
    main()
