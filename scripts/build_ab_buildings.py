#!/usr/bin/env python3
"""Build the Alberta building-footprint file (FlatGeobuf, packed Hilbert R-tree).

    python scripts/build_ab_buildings.py \
        --ms-zip ~/dev/wildfire/buildings-ab/raw/Alberta.zip \
        --edmonton data/edmonton_buildings.geojson.gz \
        --out ~/dev/wildfire/buildings-ab/ab_buildings_ms2019.fgb

Source: Microsoft Canadian Building Footprints, Alberta (ODbL; 1,777,439 footprints,
https://github.com/microsoft/CanadianBuildingFootprints, ``Alberta.zip``). One output feature
per source footprint, geometry unchanged (EPSG:4326, the source's 6-decimal coordinates), and
two attributes only (no address or other fields):

* ``id`` (int64), stable: footprints of the former Edmonton extract
  (``data/edmonton_buildings.geojson.gz``: the source footprints whose ring-average centroid is
  in 53.39-53.71 N, 113.75-113.25 W) keep that file's ``id`` (1-346,238; the key of
  ``edmonton_building_vegetation.csv`` and of earlier results); every other footprint gets
  ``1,000,000 + its 1-based position in the source file``.
* ``area_m2`` (float): geodesic area on the WGS84 ellipsoid (pyproj ``Geod``), holes removed.

Memory: the source is read one feature per line (the Microsoft file has one feature per
line), so the process holds only the Edmonton id lookup (~0.4 GB) and GDAL's per-feature index
entries; peak RSS is printed at the end. Needs GDAL's Python bindings (``osgeo``) for the
writer and pyogrio to read the Edmonton file; the API itself reads the result with pyogrio.

Writes ``<out>.json`` next to the file: counts, the largest footprint extent (degrees; the
reader's search margin must be at least this), SHA-256 and the source checksum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time
import zipfile

# Box of the former Edmonton extract (ring-average centroid, inclusive); verified: exactly
# the 346,238 footprints of data/edmonton_buildings.geojson.gz, same coordinates.
EDMONTON_EXTRACT_BOX = (53.39, 53.71, -113.75, -113.25)  # lat_min, lat_max, lng_min, lng_max
OTHER_ID_BASE = 1_000_000


def ring_key(ring) -> tuple:
    return tuple((round(x, 6), round(y, 6)) for x, y in ring[:])


def raw_centroid(ring) -> tuple[float, float]:
    """(lat, lng): mean of the outer ring's vertices, as ``BuildingIndex`` computes it."""
    n = len(ring)
    return sum(c[1] for c in ring) / n, sum(c[0] for c in ring) / n


def edmonton_ids(path: str) -> dict:
    import pyogrio
    import shapely

    _, _, wkb, fields = pyogrio.raw.read("/vsigzip/" + os.path.abspath(path), columns=["id"])
    geoms = shapely.from_wkb(wkb)
    del wkb
    out = {}
    for g, i in zip(geoms, fields[0]):
        poly = g if g.geom_type == "Polygon" else g.geoms[0]
        out[ring_key(list(poly.exterior.coords))] = int(i)
    return out


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ms-zip", required=True)
    ap.add_argument("--edmonton", required=True, help="data/edmonton_buildings.geojson.gz")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from osgeo import gdal, ogr, osr
    from pyproj import Geod

    gdal.UseExceptions()
    geod = Geod(ellps="WGS84")
    t0 = time.time()
    edm = edmonton_ids(args.edmonton)
    print(f"Edmonton extract: {len(edm):,} footprints", flush=True)

    if os.path.exists(args.out):
        os.remove(args.out)
    drv = ogr.GetDriverByName("FlatGeobuf")
    ds = drv.CreateDataSource(args.out)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    lyr = ds.CreateLayer("buildings", srs, ogr.wkbPolygon, options=["SPATIAL_INDEX=YES"])
    lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger64))
    lyr.CreateField(ogr.FieldDefn("area_m2", ogr.OFTReal))
    defn = lyr.GetLayerDefn()

    lat0, lat1, lng0, lng1 = EDMONTON_EXTRACT_BOX
    n = n_edm = n_multi = 0
    max_dx = max_dy = 0.0
    last_edm_id = 0
    edm_in_order = True
    bounds = [180.0, 90.0, -180.0, -90.0]
    with zipfile.ZipFile(args.ms_zip) as z:
        name = [m for m in z.namelist() if m.endswith(".geojson")][0]
        with z.open(name) as f:
            for line in f:
                line = line.strip().rstrip(b",")
                if not line.startswith(b'{"type":"Feature"'):
                    continue
                n += 1
                geom = json.loads(line)["geometry"]
                if geom["type"] == "Polygon":
                    polys = [geom["coordinates"]]
                else:
                    polys = geom["coordinates"]
                    n_multi += 1
                outer = polys[0][0]
                clat, clng = raw_centroid(outer)
                bid = None
                if lat0 <= clat <= lat1 and lng0 <= clng <= lng1:
                    bid = edm.get(ring_key(outer))
                    if bid is None:
                        raise SystemExit(f"source feature {n} is in the Edmonton box but not "
                                         "in the Edmonton extract")
                    n_edm += 1
                    edm_in_order &= bid > last_edm_id
                    last_edm_id = bid
                else:
                    bid = OTHER_ID_BASE + n
                area = 0.0
                xs, ys = [], []
                for poly in polys:
                    for k, ring in enumerate(poly):
                        a, _ = geod.polygon_area_perimeter([c[0] for c in ring],
                                                           [c[1] for c in ring])
                        area += abs(a) if k == 0 else -abs(a)
                    xs.extend(c[0] for c in poly[0])
                    ys.extend(c[1] for c in poly[0])
                max_dx = max(max_dx, max(xs) - min(xs))
                max_dy = max(max_dy, max(ys) - min(ys))
                bounds = [min(bounds[0], min(xs)), min(bounds[1], min(ys)),
                          max(bounds[2], max(xs)), max(bounds[3], max(ys))]
                feat = ogr.Feature(defn)
                feat.SetField("id", bid)
                feat.SetField("area_m2", round(area, 2))
                feat.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(geom)))
                lyr.CreateFeature(feat)
                feat = None
                if n % 200_000 == 0:
                    print(f"  {n:,} features, {time.time() - t0:.0f} s", flush=True)
    if n_edm != len(edm):
        raise SystemExit(f"{n_edm} Edmonton-box footprints != {len(edm)} in the extract")
    lyr = None
    ds = None  # writes the packed R-tree
    size = os.path.getsize(args.out)
    digest = sha256(args.out)
    meta = {
        "file": os.path.basename(args.out),
        "source": "Microsoft Canadian Building Footprints, Alberta (ODbL)",
        "source_url": "https://minedbuildings.z5.web.core.windows.net/legacy/canadian-buildings-v2/Alberta.zip",
        "source_sha256": sha256(args.ms_zip),
        "features": n,
        "multipolygons": n_multi,
        "edmonton_extract_features": n_edm,
        "edmonton_ids_in_source_order": edm_in_order,
        "edmonton_extract_box": EDMONTON_EXTRACT_BOX,
        "other_id_base": OTHER_ID_BASE,
        "bounds_lng_lat": bounds,
        "max_footprint_extent_deg": [max_dx, max_dy],
        "bytes": size,
        "sha256": digest,
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "seconds": round(time.time() - t0),
    }
    with open(args.out + ".json", "w") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
