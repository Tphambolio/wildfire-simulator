#!/usr/bin/env python3
"""Build the Alberta building file with municipal footprints (StatCan ODB v3) where licensed.

    python scripts/build_ab_buildings_odb.py \
        --ms-fgb ~/dev/wildfire/buildings-ab/ab_buildings_ms2019.fgb \
        --odb ~/dev/wildfire/buildings-ab/raw/ODB_v3_AB.gpkg \
        --csd ~/dev/wildfire/buildings-ab/raw/ab_csd_2021_lcsd000b21.geojson \
        --out ~/dev/wildfire/release-ab-buildings-odb/ab_buildings_2026_10.fgb

Inputs: the Microsoft 2019 Alberta file built by ``scripts/build_ab_buildings.py`` (ids and
areas kept), Statistics Canada's Open Database of Buildings v3 for Alberta (GeoPackage,
EPSG:3347) and the 2021 census subdivisions (CSD, EPSG:4326). Rules
(``docs/buildings-alberta.md`` §5):

* **Zones.** For each included provider (``PROVIDERS``: licence checked, OGL-type), the zone is
  its census subdivision minus the former Edmonton extract box (53.39-53.71 N,
  113.75-113.25 W), so every footprint the Edmonton runs read stays the Microsoft one.
* **Membership by ring-average centroid** (the mean of the first outer ring's vertices, as
  ``firesim.data.building_index`` computes it): an ODB footprint of the provider is kept when
  its centroid is in the zone; a Microsoft footprint is dropped when its centroid is in any
  zone. Each centroid is in at most one zone, so no footprint is counted twice.
* **Seams.** An ODB footprint that overlaps a kept Microsoft footprint (centroid outside the
  zone) by >= ``SEAM_OVERLAP`` of the smaller footprint is the same building seen twice
  across the zone edge: the ODB one is dropped (the Microsoft one keeps its id).
* **Attributes:** geometry (2D, EPSG:4326, 7 decimals for ODB), ``id`` (int64), ``area_m2``
  (geodesic, WGS84) and ``source`` (``ms2019`` or ``odb:<provider>``). Nothing else is read
  from the ODB (no address, name, type or any other field).
* **Ids:** Microsoft ids unchanged (Edmonton 1-346,238; others 1,000,000 + source position);
  ODB footprints ``ODB_ID_BASE`` (10,000,000) + their feature id (fid) in ``ODB_v3_AB.gpkg``
  (range 10,000,001-11,334,404).

The ODB is read one provider at a time (where + bbox); the Microsoft file in chunks. Writes
``<out>.json`` (counts, hashes, per-municipality statistics) next to the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import sys
import time

import numpy as np

EDMONTON_EXTRACT_BOX = (53.39, 53.71, -113.75, -113.25)  # lat_min, lat_max, lng_min, lng_max
ODB_ID_BASE = 10_000_000
SEAM_OVERLAP = 0.5
SEAM_BAND_DEG = 0.005  # zone-edge band searched for seam duplicates (> any footprint's half size)
MS_CHUNK = 200_000

#: Included ODB providers: ODB ``source`` -> (code, CSD uid, licence, licence URL, attribution).
#: Licences read 2026-10-12 (docs/data-sources.md): all grant copy, modify, publish, adapt and
#: distribute for any lawful purpose (commercial included), attribution only.
PROVIDERS = {
    "City of Calgary": ("calgary", "4806016", "Open Government Licence – City of Calgary 2.1",
                        "https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa",
                        "Contains information licensed under the Open Government Licence – City of Calgary."),
    "City of Lethbridge": ("lethbridge", "4802012", "City of Lethbridge – Open Data License (v1 in ODB; v2.0 on the portal)",
                           "https://opendata.lethbridge.ca/pages/open-data-license",
                           "Contains information licensed under the City of Lethbridge – Open Data License."),
    "Parkland County": ("parkland-county", "4811034", "Open Government Licence – Parkland County 1",
                        "https://www.parklandcounty.com/en/home-property-and-utilities/open-data-licence.aspx",
                        "Contains information licensed under the Open Government License – Parkland County."),
    "City of Grande Prairie": ("grande-prairie", "4819012", "Open Data License – City of Grande Prairie 1",
                               "https://opendata-cityofgp.hub.arcgis.com/",
                               "Contains information licensed under the Open Data License – City of Grande Prairie."),
    "City of Airdrie": ("airdrie", "4806021", "Open Data Licence – City of Airdrie 1",
                        "https://data-airdrie.opendata.arcgis.com/pages/our-open-licence",
                        "Contains information licensed under the Open Data Licence – City of Airdrie."),
    "Town of Okotoks": ("okotoks", "4806012", "Open Government Licence – Town of Okotoks 1",
                        "https://okotoksmaps-okotoks.hub.arcgis.com/pages/open-data-licence",
                        "Contains information licensed under the Open Government Licence – Town of Okotoks."),
    "Town of Cochrane": ("cochrane", "4806019", "Open Data Licence – Town of Cochrane 1",
                         "https://geohub.cochrane.ca/pages/open-data-license-agreement",
                         "Contains information licensed under the Open Data Licence – Town of Cochrane."),
    "Town of Canmore": ("canmore", "4815023", "Town of Canmore Open Data Licence 1",
                        "https://sttocgispublic.blob.core.windows.net/gis/Town%20of%20Canmore%20Open%20Data%20Licence.pdf",
                        "Contains information licensed under the Town of Canmore Open Data Licence."),
    "Town of Banff": ("banff", "4815035", "Open Government Licence – Banff 2",
                      "https://maps.banff.ca/opendata/",
                      "Contains information licensed under the Open Government Licence – Banff."),
}
#: Not used, with the reason (docs/buildings-alberta.md §5).
EXCLUDED = {
    "City of Edmonton": "Edmonton stays on the Microsoft footprints (results identical to before); licence (OGL – City of Edmonton) would allow it",
    "Strathcona County": "59 % of its ODB footprints (Sherwood Park) lie in the Edmonton extract box, which stays Microsoft; switching the rest would put a seam through Sherwood Park's east side; licence (OGL – Strathcona County) would allow it",
    "Government of Canada": "not municipal: NRCan Automatically Extracted Buildings (machine-extracted, like Microsoft's) covering parts of some CSDs only, so no CSD-wide switch; licence (OGL – Canada) would allow it",
}


def ring_centroids(geoms) -> tuple[np.ndarray, np.ndarray]:
    """(lat, lng) ring-average centroid of each polygon's first outer ring (vectorised)."""
    import shapely

    first = shapely.get_geometry(geoms, 0)
    poly = np.where(shapely.get_type_id(geoms) == 6, first, geoms)
    rings = shapely.get_exterior_ring(poly)
    xy, idx = shapely.get_coordinates(rings, return_index=True)
    n = np.bincount(idx, minlength=len(geoms)).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        lng = np.bincount(idx, weights=xy[:, 0], minlength=len(geoms)) / n
        lat = np.bincount(idx, weights=xy[:, 1], minlength=len(geoms)) / n
    return lat, lng


def exact_centroid(g) -> tuple[float, float]:
    """The engine's centroid (Python sum, as ``building_index._ring_centroid``)."""
    poly = g.geoms[0] if g.geom_type == "MultiPolygon" else g
    ring = poly.exterior.coords[:]
    n = len(ring)
    return sum(c[1] for c in ring) / n, sum(c[0] for c in ring) / n


def in_box(lat, lng, box=EDMONTON_EXTRACT_BOX):
    return (lat >= box[0]) & (lat <= box[1]) & (lng >= box[2]) & (lng <= box[3])


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_zones(csd_path: str):
    """{source: zone polygon (CSD minus the Edmonton box)} and {source: CSD polygon}."""
    import pyogrio
    import shapely
    from shapely.geometry import box

    df = pyogrio.read_dataframe(csd_path)
    ebox = box(EDMONTON_EXTRACT_BOX[2], EDMONTON_EXTRACT_BOX[0],
               EDMONTON_EXTRACT_BOX[3], EDMONTON_EXTRACT_BOX[1])
    zones, csds = {}, {}
    for src, (_, uid, *_rest) in PROVIDERS.items():
        g = df.loc[df["CSDUID"].astype(str) == uid, "geometry"]
        if len(g) != 1:
            raise SystemExit(f"CSD {uid} for {src} not found")
        csd = shapely.make_valid(g.iloc[0])
        csds[src] = csd
        zones[src] = csd.difference(ebox)
        shapely.prepare(zones[src])
    return zones, csds


def zone_of(lat, lng, zones, names) -> np.ndarray:
    """Index into ``names`` of the zone holding each point, -1 for none."""
    import shapely

    out = np.full(len(lat), -1, dtype=np.int16)
    for k, name in enumerate(names):
        z = zones[name]
        x0, y0, x1, y1 = z.bounds
        cand = np.nonzero((lng >= x0) & (lng <= x1) & (lat >= y0) & (lat <= y1) & (out < 0))[0]
        if len(cand):
            hit = shapely.contains_xy(z, lng[cand], lat[cand])
            out[cand[hit]] = k
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ms-fgb", required=True)
    ap.add_argument("--odb", required=True)
    ap.add_argument("--csd", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    import pyogrio
    import shapely
    from osgeo import gdal, ogr, osr
    from pyproj import Geod, Transformer

    gdal.UseExceptions()
    geod = Geod(ellps="WGS84")
    to_wgs = Transformer.from_crs(3347, 4326, always_xy=True)
    t0 = time.time()
    zones, csds = load_zones(args.csd)
    names = list(PROVIDERS)
    seam_bands = {n: zones[n].boundary.buffer(SEAM_BAND_DEG) for n in names}
    for b in seam_bands.values():
        shapely.prepare(b)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    if os.path.exists(args.out):
        os.remove(args.out)
    ds = ogr.GetDriverByName("FlatGeobuf").CreateDataSource(args.out)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    lyr = ds.CreateLayer("buildings", srs, ogr.wkbUnknown, options=["SPATIAL_INDEX=YES"])
    lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger64))
    lyr.CreateField(ogr.FieldDefn("area_m2", ogr.OFTReal))
    fd = ogr.FieldDefn("source", ogr.OFTString)
    fd.SetWidth(24)
    lyr.CreateField(fd)
    defn = lyr.GetLayerDefn()

    def write(wkb: bytes, bid: int, area: float, source: str) -> None:
        f = ogr.Feature(defn)
        f.SetField("id", int(bid))
        f.SetField("area_m2", float(area))
        f.SetField("source", source)
        f.SetGeometry(ogr.CreateGeometryFromWkb(wkb))
        lyr.CreateFeature(f)

    stats = {n: {"code": PROVIDERS[n][0], "csduid": PROVIDERS[n][1]} for n in names}
    ms_area = {n: [] for n in names}           # dropped Microsoft areas per zone
    ms_csd_in_box = {n: 0 for n in names}      # Microsoft footprints of the CSD inside the box
    band_ms = {n: [] for n in names}           # kept Microsoft footprints near each zone edge
    max_dx = max_dy = 0.0

    # ── Microsoft: keep every footprint whose centroid is in no zone ──────────────────────
    total = pyogrio.read_info(args.ms_fgb)["features"]
    n_ms = n_ms_kept = n_edm = 0
    for start in range(0, total, MS_CHUNK):
        _, _, wkb, fields = pyogrio.raw.read(args.ms_fgb, skip_features=start,
                                             max_features=MS_CHUNK, columns=["id", "area_m2"])
        g = shapely.from_wkb(wkb)
        lat, lng = ring_centroids(g)
        # centroids within 1e-6 deg of the Edmonton box edge: recompute as the engine does
        near_edge = np.nonzero(
            (np.abs(lat - EDMONTON_EXTRACT_BOX[0]) < 1e-6) | (np.abs(lat - EDMONTON_EXTRACT_BOX[1]) < 1e-6)
            | (np.abs(lng - EDMONTON_EXTRACT_BOX[2]) < 1e-6) | (np.abs(lng - EDMONTON_EXTRACT_BOX[3]) < 1e-6))[0]
        for k in near_edge:
            lat[k], lng[k] = exact_centroid(g[k])
        z = zone_of(lat, lng, zones, names)
        ids, area = fields[0], fields[1]
        inbox = in_box(lat, lng)
        n_edm += int((ids <= 346_238).sum())
        if (z[ids <= 346_238] >= 0).any():
            raise SystemExit("an Edmonton-extract footprint fell in an ODB zone")
        for k, name in enumerate(names):
            sel = z == k
            ms_area[name].extend(area[sel].tolist())
            # Microsoft footprints of the CSD that stay because they are in the Edmonton box
            cand = np.nonzero(inbox)[0]
            if len(cand):
                x0, y0, x1, y1 = csds[name].bounds
                cand = cand[(lng[cand] >= x0) & (lng[cand] <= x1) & (lat[cand] >= y0) & (lat[cand] <= y1)]
                if len(cand):
                    ms_csd_in_box[name] += int(shapely.contains_xy(csds[name], lng[cand], lat[cand]).sum())
        b = shapely.bounds(g)
        for j in np.nonzero(z < 0)[0]:
            write(wkb[j], ids[j], area[j], "ms2019")
        keep = np.nonzero(z < 0)[0]
        for name in names:
            x0, y0, x1, y1 = seam_bands[name].bounds
            c = keep[(b[keep, 2] >= x0) & (b[keep, 0] <= x1) & (b[keep, 3] >= y0) & (b[keep, 1] <= y1)]
            if len(c):
                hit = shapely.intersects(seam_bands[name], g[c])
                band_ms[name].extend(g[c[hit]].tolist())
        max_dx = max(max_dx, float(np.nanmax(b[:, 2] - b[:, 0])))
        max_dy = max(max_dy, float(np.nanmax(b[:, 3] - b[:, 1])))
        n_ms += len(g)
        n_ms_kept += len(keep)
        print(f"  Microsoft {n_ms:,}/{total:,} read, {n_ms_kept:,} kept, {time.time() - t0:.0f} s",
              flush=True)
    # every Edmonton-extract id is kept (none can be in a zone, checked per chunk above);
    # the real file has all 346,238

    # ── ODB: per provider, footprints whose centroid is in its zone ───────────────────────
    n_odb = 0
    odb_fid_max = 0
    for k, name in enumerate(names):
        code = PROVIDERS[name][0]
        _, fids, wkb, _ = pyogrio.raw.read(args.odb, where=f"source = '{name}'", columns=[],
                                           return_fids=True)
        n_src = len(fids)
        g = shapely.force_2d(shapely.from_wkb(wkb))
        del wkb
        g = shapely.transform(g, lambda xy: np.column_stack(to_wgs.transform(xy[:, 0], xy[:, 1])))
        # 7 decimals (~1 cm; the Microsoft file has 6)
        g = shapely.transform(g, lambda xy: np.round(xy, 7))
        # single-part multipolygons become polygons (the Microsoft layout); others stay multi
        single = (shapely.get_type_id(g) == 6) & (shapely.get_num_geometries(g) == 1)
        g[single] = shapely.get_geometry(g[single], 0)
        # invalid after reprojection/rounding: make valid, keep the polygonal parts
        invalid = np.nonzero(~shapely.is_valid(g))[0]
        n_invalid = len(invalid)
        for j in invalid:
            f = shapely.make_valid(g[j])
            if f.geom_type not in ("Polygon", "MultiPolygon"):
                polys = [p for p in shapely.get_parts(f) if p.geom_type == "Polygon"]
                f = shapely.union_all(polys) if polys else g[j]
            g[j] = f
        lat, lng = ring_centroids(g)
        z = zone_of(lat, lng, {name: zones[name]}, [name])
        kept = np.nonzero(z == 0)[0]
        n_outside_csd = int(n_src - shapely.contains_xy(csds[name], lng, lat).sum())
        in_csd_box = int((shapely.contains_xy(csds[name], lng, lat) & in_box(lat, lng)).sum())
        # seams: ODB footprints overlapping a kept Microsoft footprint across the zone edge
        seam_drop = np.zeros(len(g), dtype=bool)
        small_overlaps = 0
        if band_ms[name]:
            tree = shapely.STRtree(band_ms[name])
            cand = kept[shapely.intersects(seam_bands[name], g[kept])]
            if len(cand):
                pi, ti = tree.query(g[cand], predicate="intersects")
                for a, t in zip(pi, ti):
                    og, mg = g[cand[a]], band_ms[name][t]
                    inter = og.intersection(mg).area
                    if inter >= SEAM_OVERLAP * min(og.area, mg.area):
                        seam_drop[cand[a]] = True
                    elif inter > 0:
                        small_overlaps += 1
        kept = kept[~seam_drop[kept]]
        areas = np.array([abs(geod.geometry_area_perimeter(x)[0]) for x in g[kept]])
        b = shapely.bounds(g[kept])
        if len(kept):
            max_dx = max(max_dx, float((b[:, 2] - b[:, 0]).max()))
            max_dy = max(max_dy, float((b[:, 3] - b[:, 1]).max()))
        out_wkb = shapely.to_wkb(g[kept])
        for j, w in enumerate(out_wkb):
            write(w, ODB_ID_BASE + int(fids[kept[j]]), round(float(areas[j]), 2), f"odb:{code}")
        odb_fid_max = max(odb_fid_max, int(fids.max()))
        n_odb += len(kept)
        ma = np.asarray(ms_area[name])
        s = stats[name]
        s.update({
            "odb_source_features": n_src,
            "odb_outside_csd": n_outside_csd,
            "odb_in_csd_inside_edmonton_box": in_csd_box,
            "odb_invalid_fixed": n_invalid,
            "odb_seam_duplicates_dropped": int(seam_drop.sum()),
            "odb_seam_small_overlaps_kept": small_overlaps,
            "odb_kept": int(len(kept)),
            "ms_replaced": int(len(ma)),
            "ms_kept_in_csd_inside_edmonton_box": ms_csd_in_box[name],
            "odb_area_m2_quantiles": np.percentile(areas, [5, 25, 50, 75, 95]).round(1).tolist() if len(areas) else [],
            "ms_area_m2_quantiles": np.percentile(ma, [5, 25, 50, 75, 95]).round(1).tolist() if len(ma) else [],
            "odb_lt_20m2": int((areas < 20).sum()), "ms_lt_20m2": int((ma < 20).sum()),
            "odb_lt_40m2": int((areas < 40).sum()), "ms_lt_40m2": int((ma < 40).sum()),
            "odb_area_hist": np.histogram(np.log10(np.clip(areas, 1, 1e6)), bins=np.linspace(0, 5, 51))[0].tolist(),
            "ms_area_hist": np.histogram(np.log10(np.clip(ma, 1, 1e6)), bins=np.linspace(0, 5, 51))[0].tolist(),
        })
        print(f"  {name}: {len(kept):,} ODB kept (of {n_src:,}), {len(ma):,} Microsoft replaced, "
              f"{int(seam_drop.sum())} seam duplicates dropped, {time.time() - t0:.0f} s", flush=True)
        del g

    lyr = None
    ds = None  # writes the packed R-tree
    meta = {
        "file": os.path.basename(args.out),
        "sources": {
            "ms2019": "Microsoft Canadian Building Footprints, Alberta (2019), ODbL 1.0",
            **{f"odb:{v[0]}": f"{k} via Statistics Canada Open Database of Buildings v3 (2025-04-15); {v[2]}"
               for k, v in PROVIDERS.items()},
        },
        "attribution": {f"odb:{v[0]}": v[4] for v in PROVIDERS.values()},
        "licence_urls": {f"odb:{v[0]}": v[3] for v in PROVIDERS.values()},
        "excluded": EXCLUDED,
        "ms_input": os.path.basename(args.ms_fgb), "ms_input_sha256": sha256(args.ms_fgb),
        "odb_input": os.path.basename(args.odb), "odb_input_sha256": sha256(args.odb),
        "csd_input": os.path.basename(args.csd), "csd_input_sha256": sha256(args.csd),
        "features": n_ms_kept + n_odb, "ms_kept": n_ms_kept, "ms_replaced": n_ms - n_ms_kept,
        "odb_kept": n_odb, "edmonton_extract_ids_kept": n_edm,
        "edmonton_extract_box": EDMONTON_EXTRACT_BOX, "odb_id_base": ODB_ID_BASE,
        "odb_id_range": [ODB_ID_BASE + 1, ODB_ID_BASE + odb_fid_max],
        "seam_overlap": SEAM_OVERLAP,
        "max_footprint_extent_deg": [max_dx, max_dy],
        "per_municipality": stats,
        "bytes": os.path.getsize(args.out), "sha256": sha256(args.out),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "seconds": round(time.time() - t0),
    }
    with open(args.out + ".json", "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in meta.items() if k != "per_municipality"}, indent=1,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
